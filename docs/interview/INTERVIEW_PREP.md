# ShopPilot 面试准备手册

> 本文档是面试前唯一需要看的文档。涵盖：项目是什么、每一层干什么、为什么这么设计、关键代码怎么写、常见拷问怎么答。
> 读完能白板画出完整流程，能回答任何"为什么这么设计"的问题。

---

## 一、30 秒电梯陈述

> "我做了一个电商导购 Agent。用户说'预算500推荐降噪耳机'，Agent 把这句话拆成结构化需求，并行去查商品池、差评、售后政策，最后输出带依据编号的推荐卡。核心设计是：LLM 只负责理解和表达，所有计算和校验都是确定性代码。跟纯 API 直调做了对照实验，同27题、同 temperature、同一模型，Agent 完成率 96.3%，纯 API 只有 40.7%，McNemar 检验 p=6.1e-05 统计显著。"

---

## 二、整体架构（三部件 + 两记忆）

```
用户输入
  │
  ├─ ① 规划 Planner：意图分类 + 约束抽取 + 拆子任务 DAG
  │     一次 LLM 调用输出 JSON，Pydantic 校验，拆错重写一次
  │
  ├─ ② 执行 Executor：子任务按拓扑分层并发
  │     data/review → 有界 ReAct 循环（工具 + 三重预算护栏）
  │     knowledge_qa → RAG 混合检索（不走工具）
  │     synthesis → 纯 LLM 汇总
  │
  └─ ③ 成稿 Composer：聚合子结论 → 结构化产出卡 + 强制引用 → 五项确定性自检
        不过关让 LLM 修复一次
```

**为什么是三部件不是八层？** 刻意收敛。层太多 = 调试地狱。三部件刚好覆盖"规划→执行→交付"全链路，不多不少。

**为什么不用 LangChain/LangGraph？** 教学项目，要理解每一层在干什么。真实业务会用，但手写一遍才能知道框架帮你挡了哪些坑。

---

## 三、每一层干什么 + 关键代码

### 3.1 数据契约层（models.py）——全项目的"普通话"

**作用**：LLM 输出和工具返回都是脏 JSON，在入口用 Pydantic `field_validator` 清洗一次。上层拿到的永远是干净对象。

**关键设计**：
- `Intent = Literal["recommend", "compare", "review_analysis", "after_sales", "research", "out_of_scope"]`：用枚举约束 LLM 的自由文本，让意图收敛到有限集合
- `Plan._check_dag()`：模型校验器，检查 DAG 无环、依赖存在、id 唯一。不合法直接抛错，Planner 重写
- `Plan.layers()`：Kahn 拓扑分层，同一层无互相依赖 → 执行器可以并发

```python
# models.py 核心：DAG 合法校验
@model_validator(mode="after")
def _check_dag(self):
    ids = [t.id for t in self.tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("任务 id 重复")
    for t in self.tasks:
        for d in t.depends_on:
            if d >= t.id:  # 依赖必须指向更小的 id → 保证无环
                raise ValueError(f"依赖必须指向更小的 id")
    return self
```

**面试点**：为什么清洗放入口不放使用点？因为使用点各写一套会漏；入口统一清洗，所有人拿到的都一样。

---

### 3.2 规划层（planner.py）——意图 + 约束 + DAG

**作用**：一次 LLM 调用同时产出：
1. `ShoppingQuery`：意图/实体/预算/在意维度
2. `tasks`：子任务列表（带依赖关系）

**关键设计**：
- `json_object` 模式强制合法 JSON
- 两次机会：第一次校验失败，把错误喂回去重写一次
- 商品池清单注入 prompt：池外型号直接判 out_of_scope（防编造）
- **多轮继承规则**：记忆块里有"最近提到的商品"时，用户说"顺带买个充电线"，entities 同时列上一轮商品+本轮新商品，预算/在意维度继承

```python
# planner.py 核心：一次调用产出 query + tasks
for attempt in (1, 2):
    try:
        data, tk = llm.chat_json(messages, temperature=0.2)
        query = ShoppingQuery(**(data.get("query") or {}), raw_text=user_text)
        if query.intent == "out_of_scope":
            return query, None, tokens_total
        plan = Plan(query=query, tasks=[SubTask(**t) for t in data.get("tasks") or []])
        return query, plan, tokens_total
    except Exception as e:
        messages.append({"role": "user",
                         "content": f"你的输出有误（{e}），严格按规则重写 JSON。"})
```

**面试点**：为什么 Planner 和 Executor 分开？因为"想"和"做"是两个独立问题。Planner 错了要重规划，Executor 错了可以降级不影响整体。

---

### 3.3 执行层（executor.py + loop.py）——ReAct 有界循环

**executor.py**：
- 按 `plan.layers()` 拓扑分层，同层用 `ThreadPoolExecutor` 并发
- 三类任务走不同路径：
  - `data_query` / `review_analysis` → ReAct 循环（带工具）
  - `knowledge_qa` → 直接 RAG 问答（Function tools 对 RAG 无意义）
  - `synthesis` → 纯 LLM 汇总（不给工具）

**loop.py 有界 ReAct 循环**（面试必背）：

```python
def react_loop(llm, system, user, registry, budget=LoopBudget()):
    messages = build_messages(system, user)
    for step in range(1, budget.max_steps + 1):
        resp = llm.chat(messages, tools=registry.schemas())  # 模型决定要不要调工具
        msg = resp["message"]
        messages.append(msg)

        # ① 模型不再要工具 = 任务完成
        if not msg.get("tool_calls"):
            return LoopResult(answer=msg.get("content"))

        # ② 并行执行本轮所有 tool_calls，结果按 tool_call_id 喂回
        for tc in msg["tool_calls"]:
            out = registry.call(tc["function"]["name"], tc["function"]["arguments"])
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
```

**三重预算护栏**（loop.py）：
- `max_steps=8`：最多走 8 步
- `max_tokens=60000`：token 超了就降级
- `max_cost_cny=0.5`：成本超了就降级
- 任一超限 → 不炸链路，返回"已降级，请基于已有信息回答"

**面试点**：为什么要并行 tool_calls？因为模型一次返回多个独立调用（如同时查商品和差评），串行等每个返回会浪费 3-4 倍时间。

---

### 3.4 成稿层（composer.py）——聚合 + 五项确定性自检

**作用**：把各子任务结论交给 LLM，产出"回答 markdown + 结构化卡 + 引用编号"。然后用**确定性规则**自检，不过关让 LLM 修复一次。

**五项自检**（全是纯函数，零 token）：

```python
def _self_check(answer_md, citations, sources, query, card):
    issues = []
    # ① 引用越界：答案里的 [n] 必须在资料列表里存在
    bad = {int(m) for m in CITE_PATTERN.findall(answer_md)} - known
    if bad: issues.append(f"引用了不存在的编号 {sorted(bad)}")

    # ② 超预算：推荐商品价格不能 > 用户预算
    over = [i["name"] for i in card["items"] if i["price"] > query.budget_max]
    if over: issues.append(f"推荐商品 {over} 超出预算")

    # ③ 商品溯源：推荐商品必须在资料或商品池里
    unknown = [i["name"] for i in card["items"] if i["name"] not in pool]
    if unknown: issues.append(f"推荐商品 {unknown} 不在商品池中")

    # ④ 误拒答：知识库有资料却回答"没有相关资料"
    if REFUSAL_WORDS in answer and sources and intent == "after_sales":
        issues.append("知识库已检索到资料却回答没有相关资料")

    # ⑤ 目标覆盖：用户点名的商品必须出现在回答里
    missing = [e for e in query.entities if e not in answer_md]
    if missing: issues.append(f"用户点名的商品 {missing} 未在回答中出现")
    return issues
```

**面试点**：为什么不用 LLM 做自评？因为 LLM 有"自我讨好偏差"——自己写的东西自己打分，倾向于说"挺好的"。纯函数规则没有偏差，可回归。

---

### 3.5 推荐打分（recommend.py）——七维加权

**核心设计**：推荐不是"按评分排序"（那是 SQL），而是工具内七维加权打分：

| 维度 | 权重 | 含义 |
|---|---|---|
| 价格（预算贴合） | 13% | 买得起且不浪费预算 |
| 平台评分 | 16% | 商品表 rating（4分制） |
| 评论均分 | 10% | 真实评论星级均值（抗刷） |
| 销量热度 | 8% | 月销量归一（大众选择信号） |
| 好评匹配 | 22% | 好评是否命中用户在意维度 |
| 差评风险 | 18% | 1-差评率，关键维度集中追加惩罚 |
| 物流时效 | 13% | 次日达是电商转化关键信号 |

```python
# recommend.py 核心：每维归一化后加权求和
parts = {
    "价格": WEIGHTS["price"] * row.budget_n,        # 越接近预算上限越好
    "平台评分": WEIGHTS["rating"] * row.rating_n,    # (rating-3)/2 归一到0~1
    "物流时效": WEIGHTS["delivery"] * row.deliver_n,  # 1天=1.0, 4天=0.25
}
total = sum(parts.values())  # LLM 不参与计算，只负责把 decision_reason 讲成自然语言
```

**面试点**：为什么打分放工具内不放 LLM？因为 LLM 每次算出来不一样（随机性），打分是数学题，确定性代码可回归。改权重 benchmark 跑一遍就知道好不好。

---

### 3.6 RAG 混合检索（rag_lib.py）——TF-IDF + BM25 + RRF

**为什么必须混合**：
- 向量路（TF-IDF char n-gram）：懂语义，"耳机音质怎么样"召回好；但怕专名数字，"鼓包""发票"召回差
- BM25 路：对型号/数字/专名极准；但不懂同义，"耳机音质"和"耳机音效"它觉得不一样

**RRF 融合** `score = Σ 1/(60+rank)`：两路排名各算倒数排名加起来，两路都命中的 chunk 排最前。

```python
# rag_lib.py 核心：双路召回 + RRF 融合
vec_rank = np.argsort(-(vectors @ qv[0]))[:top_k*3]      # 向量路
bm_rank = np.argsort(-bm25.get_scores(jieba.cut(query)))[:top_k*3]  # BM25路
fused = {}
for rank, i in enumerate(vec_rank):
    fused[i] = fused.get(i, 0) + 1/(60+rank+1)  # RRF
for rank, i in enumerate(bm_rank):
    fused[i] = fused.get(i, 0) + 1/(60+rank+1)
return [chunks[i] for i in sorted(fused, key=fused.get, reverse=True)[:top_k]]
```

**为什么用 TF-IDF 不用 bge-small-zh？** 没有 GPU。TF-IDF 字符 n-gram 离线秒建、中文友好，换 bge 只需实现 `encode()` 接口。

**强制引用溯源**：售后答错=客诉，所以 system prompt 写死"每个关键结论后必须标 [n]，资料不足必须说知识库中没有相关资料"。

---

### 3.7 记忆层（memory.py）——短期 + 长期

**短期记忆**（会话内）：
- 最近 6 轮原文进滑窗
- 更早的由 LLM 压成 ≤150 字滚动摘要
- 解决长对话变傻（窗口限制 + 注意力稀释 + token 成本）

**长期记忆**（跨会话）：
- Markdown 文件追加式（`data/memory/{user}.md`），人类可读可删
- 每次对话追加一条：问了什么、答了什么、涉及哪些商品、结论要点
- 注入时按 jieba 分词关键词命中检索最相关的几条

```python
# memory.py 核心：长期记忆检索
def recall(self, query, top_k=4):
    qwords = {w for w in jieba.cut(query) if len(w) > 1}
    def score(it):
        blob = " ".join(str(v) for v in it.values())
        return sum(1 for w in qwords if w in blob)
    ranked = sorted(self.entries(), key=lambda x: (-score(x), -x[0]))
    return ranked[:top_k]
```

**面试点**：为什么不做用户画像？因为画像推断错一次就翻车。用户每次输入都可能不同，需要个性化时把在意维度写进问题里即可（ShoppingQuery 已经在抽）。

---

## 四、数据长什么样

| 数据集 | 规模 | 字段 |
|---|---|---|
| 商品池 products.csv | 40 个 SKU / 12 品类 | name, category, price, rating, sales_month, warranty_months, highlights, delivery_days |
| 评论 reviews.csv | 138 条（差评63条） | product, rating, text, aspect（21种维度） |
| 销量 sales.csv | 39 条 | product, category_rank |
| 知识库 knowledge/ | 6 篇 Markdown | 蓝牙耳机手册 / 退换货政策 / 物流FAQ / 选购指南等 |
| 题库 bench_cases.json | 27 题 | id, prompt, reference, constraints（must_contain/must_not_contain）, meta.expected_behavior |

**数据是自己生成的**（LLM 合成，结构模仿巴西 Olist 开源数据集），教学用，不代表真实业务。

---

## 五、对比实验（怎么证明 Agent 比纯 API 强）

### 实验设计（控制变量）

| | Agent 组 | 基线组（纯 API） |
|---|---|---|
| 题库 | 同一套 27 题 | 同一套 27 题 |
| 模型 | 同一个 DeepSeek | 同一个 DeepSeek |
| temperature | **统一 0.2** | **统一 0.2** |
| 评分 | 同一套确定性规则 | 同一套规则 |
| **唯一变量** | 有工具/知识库/编排/记忆 | 只有一句 system prompt，裸问 |

### 结果

| 方案 | 完成率 |
|---|---|
| **ShopPilot Agent** | **96.3%（26/27）** |
| 纯 API 基线 | 40.7%（11/27） |

**McNemar 配对检验**：b=15（Agent对/基线错）, c=0（Agent错/基线对）, **p=6.1e-05 极显著**。

**分场景**：
- 推荐类：Agent 7/8 vs 基线 2/8（基线编造池外商品）
- 拒答类：Agent 4/4 vs 基线 0/4（基线该拒硬答）
- 口碑/售后：差距较小（简单基线也能蒙对）

### 已知瑕疵（主动承认）

| 瑕疵 | 改进方向 |
|---|---|
| 单次跑，没报方差 | 跑 3 次取均值±标准差 |
| 样本只有 27 题 | 扩到 100+ 题 |
| 基线没有商品池可见 | 这正是要证明的：没有系统=没有知识边界 |

---

## 六、被否掉的方案（为什么不上）

| 方案 | 为什么否掉 |
|---|---|
| MemGPT 式 LLM 自管记忆 | 经常丢关键实体，不如显式槽位+滑窗 |
| Milvus 向量库 | 小规模（万级 chunks）没必要，numpy 暴力点积够快 |
| 多智能体 >3 角色 | 电商导购是窄域任务，2-3 个 agent 足够，再多是调试地狱 |
| 自动推断用户画像 | 推断错一次就翻车，不如让用户自己说 |
| 32K 长上下文替代 RAG | 政策更新就要重跑全量推理，不可控 |
| 纯 ReAct（无 Planner） | 步数不可控、工具选错率高、token 爆炸 |
| 纯 Pipeline（无 ReAct） | 灵活性为零，用户问"耳机翻车点在哪"不知道走口碑分支 |
| LangChain/LangGraph | 教学项目，手写一遍才能理解每一层在干什么 |

---

## 七、常见拷问 Q&A

### Q1：你这不就是调 API 吗？跟纯调用有什么区别？
> 纯 API 直调完成率只有 40.7%。它不知道商品池边界在哪，编造"华为 FreeBuds 约399元"这种池外商品。我的系统有三道闸：Planner 注入商品池清单防编造、Composer 五项自检（引用越界/超预算/商品溯源/误拒答/目标覆盖）、七维打分在工具内确定性计算。这些是工程护栏，不是模型本身的能力。

### Q2：为什么打分放工具内不让 LLM 算？
> LLM 每次算出来不一样（随机性）。打分是数学题：价格贴合度、平台评分归一、物流时效——这些用确定性代码算，可回归。改权重 benchmark 跑一遍就知道好不好，不会出现"同一批商品两次打分 LLM 给出不同排序"。

### Q3：为什么 RAG 要混合检索？只用向量不行吗？
> 向量路懂语义但怕专名数字。"鼓包""发票"这种词 TF-IDF 召回差。BM25 对专名极准但不懂同义。RRF 融合两路共识 chunk 排最前，召回率显著更高。k=60 是工业界默认配置。

### Q4：为什么记忆分短期长期？一个长 prompt 不行吗？
> 不行。长 prompt 有三个问题：窗口限制（32K 也会满）、注意力稀释（早期内容被忽略）、token 成本（每次全量带）。短期滑窗 + 滚动摘要解决会话内，长期 Markdown 检索解决跨会话，各取所需。

### Q5：为什么不做用户画像？
> 推断错一次就翻车。用户每次输入都可能不同，需要个性化时把在意维度写进问题里即可（ShoppingQuery 已经在抽"降噪/续航/拍照"这些维度）。

### Q6：五项自检为什么不用 LLM 裁判？
> LLM 有自我讨好偏差——自己写的东西自己打分，倾向于说"挺好的"。纯函数规则没有偏差、零 token、可回归、可单测。LLM 只在规则报错时做一次修复。

### Q7：为什么 DAG 分层并发？不能串行吗？
> 串行太慢。"查商品"和"查差评"没有依赖关系，可以并发。拓扑分层（Kahn 法）保证同层无依赖才并发，跨层必须等。

### Q8：三重预算是什么？为什么需要？
> max_steps=8 / max_tokens=60000 / max_cost=0.5元。防止 LLM 陷入死循环无限烧钱。任一超限不炸链路，降级返回"请基于已有信息回答"。

### Q9：对比实验的 temperature 为什么统一？
> 之前 Agent 用 0.2、基线用 0.3，基线温度更高更"放飞"，翻车率被放大了。统一 0.2 后差距还在，说明确来自系统设计不是温度。

### Q10：换真实业务要改哪里？
> 只改 `domains/ecommerce/adapter.py` 的 6 个 load 函数（换成真实 API），上面 6 层一行不改。这就是"换业务只改一层"的设计。

---

## 八、准备路线

1. **第一遍**：本文档通读，建立全局观
2. **第二遍**：第三章每段代码对照源码读一遍（能白板画出流程）
3. **第三遍**：第七章 Q&A 逐条口头练习
4. **查漏**：读 loop.py 的重试退避、tools.py 的 ToolRegistry、observability.py 的 JSONL trace
