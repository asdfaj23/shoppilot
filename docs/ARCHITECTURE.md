# ShopPilot 架构设计文档

> 本文档不是"功能清单"，而是一份**系统设计说明书**：回答每个模块为什么存在、为什么这样设计、被否掉的方案是什么。
> 目标读者：想真正理解这套 Agent 系统的人（面试/评审/复用）。

---

## 1. 设计哲学：一句话

> **LLM 负责"理解和表达"，确定性代码负责"计算和校验"。Agent 系统的价值不是让 LLM 变得更强，而是用工程护栏把它的不稳定性关进笼子。**

这个哲学贯穿了所有设计决策。具体拆解为五条原则：

| 原则 | 含义 | 落地点 |
|---|---|---|
| **确定性优先** | 能写死的绝不交给 LLM（打分、排序、引用编号、意图校验） | 七维打分在工具内、Reflection 全是确定性规则 |
| **契约兜底** | LLM 输出永远是脏的，在入口用 Pydantic 清洗一次 | `models.py` 的 `field_validator` |
| **单一事实源** | schema/权重/品类清单只在一处定义，自动派生 | ToolRegistry 从 Pydantic 生成 OpenAI schema |
| **失败降级** | 任何一步失败都不让整体炸掉，而是降级为"部分答案" | 三重预算 / 超时降级 / 子任务独立失败 |
| **可观测可复盘** | 每次运行留下完整 trace，badcase 可定位到具体某步 | JSONL trace + 分步 token 归因 |

---

## 2. 为什么是"规划→ReAct→Reflection"三段式？

这是本系统最核心的架构决策，也是和"纯 prompt 工程"的本质区别。

### 2.1 三段各自解决什么问题

```
用户输入
   │
   ▼
┌─────────┐  把自由文本 → 结构化需求 + 可执行任务 DAG
│ Planner │  解决：LLM 不知道"该干什么"
└────┬────┘
     │  tasks[] (带依赖、带 task_type)
     ▼
┌─────────┐  对每个 task_type，有界循环调工具
│  ReAct  │  解决：LLM 不知道"怎么拿到数据"
│  Loop   │  护栏：max_steps / 超时 / 并行 / 重试
└────┬────┘
     │  工具返回的原始数据
     ▼
┌──────────┐  聚合 → 结构化产出卡 → 确定性自检
│Composer │  解决：LLM 拿到数据后"可能编造/漏引用"
│+Reflect │  五项检查： grounded / cited / 无超预算 / 无池外商品 / 有理由
└──────────┘
```

**关键代码**（`agents/planner.py`）：
```python
def make_plan(user_text: str, memory_block: str = "") -> tuple[ShoppingQuery, Plan, int]:
    # ① 让 LLM 一次性输出结构化 JSON：意图/实体/预算/在意维度/子任务列表
    #    json_object 模式强制合法 JSON，避免模型吐自由文本
    data, tk = llm.chat_json([
        # system：告诉模型要输出什么 schema（意图枚举、字段名、DAG 依赖规则）
        {"role": "system", "content": PLANNER_SYSTEM},
        # user：记忆块（最近对话摘要+长期偏好）+ 用户原话
        {"role": "user", "content": f"{memory_block}\n\n用户输入：{user_text}"},
    ])
    # ② Pydantic 契约校验：脏数据在入口清洗（缺字段给默认、类型错自动纠偏）
    query = ShoppingQuery(**(data.get("query") or {}), raw_text=user_text)
    # ③ 子任务列表包成 Plan 对象，带依赖关系（DAG）
    plan = Plan(query=query, tasks=[SubTask(**t) for t in data.get("tasks") or []])
    return query, plan, tk   # 返回：结构化需求 + 任务计划 + 本轮 token 数
```

**关键代码**（`harness/loop.py` ReAct 循环）：
```python
def react_loop(llm, system: str, user: str, registry,
               budget: LoopBudget = LoopBudget(), on_event=None) -> LoopResult:
    # 组装初始消息：system 提示词 + 用户问题
    messages = build_messages(system, user)
    for step in range(1, budget.max_steps + 1):   # 三重预算：最多 max_steps 步
        # 调 LLM，把可用工具的 schema 一起给它，让它决定"要不要调工具"
        resp = llm.chat(messages, tools=registry.schemas())
        msg = resp["message"]
        messages.append(msg)
        # ① 模型不再要工具 = 任务完成，content 就是最终答案
        if not msg.get("tool_calls"):
            return LoopResult(answer=msg.get("content") or "", ...)
        # ② 模型要调工具：并行执行每个 tool_call，结果按 tool_call_id 喂回
        #    这样顺序乱了也不怕——id 对应关系不乱
        for tc in msg["tool_calls"]:
            out = registry.call(                      # 按名字找到工具函数
                tc["function"]["name"],               #   工具名
                tc["function"].get("arguments") or "{}"  #   参数（JSON 字符串）
            )
            # 把工具结果以 role=tool 塞回 messages，模型下一步就能看到
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],             #   对应回哪个 tool_call
                "content": out,                        #   工具返回的 JSON 字符串
            })
        # 循环往复，直到模型说"不调工具了"或步数超限（降级返回）
```

### 2.2 被否掉的方案

| 方案 | 为什么否掉 |
|---|---|
| **纯 ReAct（无 Planner）** | LLM 直接对着开放工具集循环调，步数不可控、工具选错率高、token 爆炸。适合"聊天式 agent"，不适合"任务型导购" |
| **纯 Pipeline（无 ReAct）** | 把流程写死成"先查商品→再查评论→再总结"，灵活性为零。用户问"耳机翻车点在哪"时管道不知道该走口碑分支 |
| **多智能体（>3 个 agent）** | 调研了 DeerFlow / AgentScope 的多角色方案。电商导购是窄域任务，2-3 个 agent 足够，再多是调试地狱，错误累积放大 |
| **让 LLM 自己打分** | 价格贴合度/差评率归一化是数学题，LLM 算不对还会编分。打分必须确定性 |

### 2.3 为什么 Reflection 必须是确定性规则？

这是面试最容易被质疑的点。常规做法是"让 LLM 自查一遍"——但 LLM 自查时会：
- 倾向于认可自己刚输出的内容（确认偏误）
- 编造"我已经检查过了"的理由
- 不可回归（同样的输入两次检查结果可能不同）

**本系统的 Reflection 是五条纯函数规则**（`composer.py`）：
1. **grounding**：推荐的商品必须在工具返回的候选集里
2. **citation**：正文里的 `[n]` 编号必须落在工具返回的资料集里
3. **budget**：推荐价格必须 ≤ 用户预算
4. **pool**：不能提到商品池外的型号
5. **reason**：每个推荐至少有一条理由

零 token、可回归、可单测。LLM 只在规则报错时做一次修复。

**关键代码**（`agents/composer.py`）：
```python
def _self_check(answer: str, query: ShoppingQuery, result: TeamResult) -> list[str]:
    issues = []
    # ① 引用越界检查：答案里的 [n] 编号必须落在资料总数范围内
    #    （防止模型编造"[99] 某来源"这种不存在的引用）
    cited = {int(m) for m in re.findall(r"\[(\d{1,2})\]", answer)}
    n_sources = len(result.tasks) + len(result.citations)
    if cited and not cited <= set(range(1, n_sources + 1)):
        issues.append("引用编号越界")
    # ② 预算硬约束：推荐价格不能超过用户预算上限
    if query.budget_max and not budget_ok(answer, query.budget_max):
        issues.append("推荐价格超出预算")
    # ③ grounding：推荐的商品必须在工具返回的候选集里（防编造商品）
    # ④ pool：不能提到商品池外的型号（防编造 iPhone 这种）
    # ⑤ reason：每个推荐至少有一条理由（防空泛回答）
    return issues   # 返回所有问题列表，非空则触发 LLM 修复一次
```

---

## 3. 推荐排序：七维加权打分为什么放在工具内？

### 3.1 设计决策

这是"确定性优先"原则的最典型落地。`recommend_products` 工具返回的不是"候选商品列表"，而是**已经排好序、带得分构成、带优缺点原文、带选择理由草稿**的结构化结果。

### 3.2 七维权重及其业务含义

| 维度 | 权重 | 业务含义 | 为什么是这个权重 |
|---|---|---|---|
| 好评匹配 | 22% | 好评里是否命中用户在意的维度 | 用户买耳机就是要降噪，命中降噪好评比"评分4.5"更有说服力 |
| 差评风险 | 18% | 1-差评率，关键维度差评追加惩罚 | 电商退货率主要由差评驱动 |
| 平台评分 | 16% | 商品表 rating | 平台分是第一道筛选，但可被刷 |
| 价格贴合 | 13% | 买得起且不浪费预算 | 预算是硬约束，贴合度是软信号 |
| **物流时效** | 13% | 次日达>2-3天>慢递 | **电商转化关键信号**，急用场景直接主导排序 |
| 评论均分 | 10% | 真实评论星级均值 | 比平台分更抗刷，但样本量小 |
| 销量热度 | 8% | 月销量归一 | 大众选择的统计信号，但不是核心 |

### 3.3 LLM 在排序中的角色

LLM **不参与计算**，只负责"表达"——把 `decision_reason` 草稿组织成自然语言：
> "选它主要因为好评匹配占优，命中你在意的降噪（得分0.22/权重0.22）；需注意差评率70%集中在音质。"

这样做的好处：
- 排序可回归（改权重 benchmark 跑一遍就知道好不好）
- 不会出现"两个商品分数相同但 LLM 给出不同排序"的不一致
- 面试时能说清楚"为什么推荐这个而不是那个"

**关键代码**（`domains/ecommerce/recommend.py`）：
```python
# 七维权重：每个维度代表一种业务信号，加和=1.0
WEIGHTS = {"price":0.13, "rating":0.16, "review_avg":0.10, "popularity":0.08,
           "pros":0.22, "cons":0.18, "delivery":0.13}

def recommend_products(...) -> str:
    for row in df.itertuples():
        parts = {
            # ① 价格贴合度：越接近预算上限越好，不浪费预算
            "price": budget_fit(row.price, budget_max),
            # ② 平台评分归一：rating 3~5 分映射到 0~1
            "rating": (row.rating - 3.0) / 2.0,
            # ③ 物流时效：次日达=1.0，2-3天=0.5，4天以上=0.25
            "delivery": 1 if row.delivery_days <= 1 else 0.25,
            # ... 其余维度同理（评论均分/销量热度/好评匹配/差评风险）
        }
        # 加权求和：总分 = Σ(权重 × 维度得分)
        total = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS)
        # LLM 不参与这个计算，只负责把 decision_reason 草稿组织成自然语言
```

---

## 4. 多轮会话：为什么是"三层记忆"而不是"一个长 prompt"？

### 4.1 三层结构

```
┌─ L1: 短期滑窗（最近6轮原文）        解决"它/这个"指代
│     └─ 更早的压成≤150字滚动摘要
│
├─ L2: 结构化槽位（ShoppingQuery）    解决"用户到底要什么"
│     └─ 每轮 Planner 强制重抽，不是"记忆"而是"重新理解"
│
└─ L3: 长期记忆（Markdown 追加式）    解决"跨会话还记得"
      └─ 按关键词命中检索最相关的几条
```

### 4.2 为什么槽位是"每轮重抽"而不是"继承上一轮"？

这是关键反直觉决策。直觉上"用户上一轮说了预算500，这轮不用再问"——但真实场景是：
- 用户可能改主意了（"算了，800以内吧"）
- 用户可能换品类了（上一轮买耳机，这一轮问路由器）
- 用户的"顺便买充电线"是配套需求，预算/在意维度继承，但品类换了

**解法**：Planner 每轮都重新读用户原话 + 记忆块，LLM 自己决定"新需求是继承、覆盖还是配套"。Prompt 里写清楚配套识别规则（"顺带/顺便买个…"），但不把槽位写死。

### 4.3 为什么不做"用户画像推断"？

MemGPT 式做法：core_memory 里写"用户偏好降噪耳机、预算500、学生党"。
**企业落地为什么不做**：
- 推断错一次就翻车（把"给爸爸买"推断成"用户自己要"）
- 画像面板在前端展示，用户看到错误推断会不信任系统
- 用户每次输入都可能不同（帮人代买、送礼）

**本系统做法**：只记用户明确说过的话（"在意安全""出差用"），不做任何推断。

---

## 5. RAG：无 GPU 环境下的混合检索

### 5.1 架构

```
用户问题
   │
   ├─ 向量通道：TF-IDF char n-gram → 余弦相似 → top_k
   └─ BM25 通道：jieba 分词 → BM25Okapi → top_k
          │
          ▼
     RRF 融合（k=60）：score = Σ 1/(60+rank)
          │
          ▼
     top_k 条塞 prompt → LLM 强制引用回答
```

### 5.2 为什么是 TF-IDF 而不是 bge-small-zh？

教学/小规模场景（电商知识库 5 篇 md，chunks <50 条）：
- TF-IDF 零依赖、秒建索引、中文 char n-gram 对专有名词友好
- bge-small-zh 100MB 模型下载 + CPU 推理 5ms/query，收益不明显
- **换 bge 只需实现 `encode()` 接口**，索引层代码不变——这是可插拔设计

### 5.3 为什么必须混合检索？

- 纯向量：对"鼓包""发票""LED"这种专有名词不敏感（语义相似但词不匹配）
- 纯 BM25：对"耳机音质怎么样"这种语义 query 召回差
- RRF 融合不用调权重，工业界默认配置

### 5.4 强制引用 + 诚实拒答

电商售后场景（"我这耳机能不能退"）答错 = 客诉。所以 RAG 的 system prompt 写死两条规则：
1. 每个关键结论后必须标 `[n]`
2. 资料不足必须说"知识库中没有相关资料"，禁止硬答

**关键代码**（`rag/rag_lib.py` 混合检索）：
```python
def hybrid_search(query: str, top_k: int = 4) -> list[dict]:
    # ① 向量路：把 query 转向量，和所有 chunk 算余弦相似，取 top_k*3 候选
    qv = embedder.encode([query])                       # TF-IDF char n-gram
    vec_ranks = np.argsort(-(vectors @ qv[0]))[:top_k * 3]

    # ② BM25 路：jieba 分词后算 BM25 得分，取 top_k*3 候选
    bm25_ranks = bm25.get_scores(jieba.cut(query))      # 对专名/数字极准

    # ③ RRF 融合：两路排名各算 1/(60+rank)，加起来就是融合分
    #    k=60 是工业界默认值，压低单路排名的优势，让"两路都命中"的 chunk 排最前
    fused = {}
    for rank, i in enumerate(vec_ranks):
        fused[i] = fused.get(i, 0) + 1 / (60 + rank + 1)
    for rank, i in enumerate(np.argsort(-bm25_ranks)[:top_k * 3]):
        fused[i] = fused.get(i, 0) + 1 / (60 + rank + 1)

    # ④ 按融合分排序，取 top_k 条塞给 LLM
    return [chunks[i] for i in sorted(fused, key=fused.get, reverse=True)[:top_k]]
```

---

## 6. 被否掉的所有花架子

| 设计 | 为什么不上 |
|---|---|
| MemGPT 式 LLM 自管记忆 | 经常丢关键实体，不如显式槽位+滑窗 |
| Milvus/Qdrant 向量数据库 | chunks 万级内 numpy 暴力点积够快 |
| 多智能体 >3 角色 | 电商导购窄域，2-3 个 agent 足够 |
| 自动推断用户画像 | 推断错一次就翻车 |
| KG 知识图谱 | 电商知识库是政策文档，不是社交关系网 |
| 32K 长上下文替代 RAG | 政策更新就要重跑全量推理，不可控 |
| ReAct 循环 >5 步 | 步数越多错误累积越严重 |
| LangGraph 复杂条件边 | 直线 pipeline 够用时别上图 |

---

## 7. 对比实验怎么做的（如何证明 Agent 比纯 API 强）

### 7.1 实验设计：控制变量对照

**目标**：证明"差距来自系统设计，不是模型本身"。

| | Agent 组 | 基线组（纯 API） |
|---|---|---|
| 题库 | 同一套 27 题 | 同一套 27 题 |
| 模型 | 同一个 DeepSeek | 同一个 DeepSeek |
| 评分 | 同一套 `check_constraints()` 确定性规则 | 同一套规则 |
| **给 LLM 什么** | 意图分类 + 工具 + RAG 知识库 + 记忆 + 编排 | 只有一句 system prompt（"你是电商导购助手"），裸问 |
| temperature | 0.2 | 0.3 |

**控制变量**：题库、模型、评分规则完全相同。**唯一变量**：Agent 有"系统"（工具/知识库/编排/记忆），基线没有。

### 7.2 评分规则（零裁判 token，可复现）

不是人工打分，也不是 LLM-as-judge，而是**确定性规则判定**：

```python
def check_constraints(answer: str, constraints: list[dict]) -> tuple[bool, list[str]]:
    """must_contain=数组内 AND；must_contain_any=OR（兼容"12个月/一年"）；must_not_contain=出现即失败"""
    fails = []
    for c in constraints:
        ctype, value = c.get("type"), c.get("value")
        kws = [value] if isinstance(value, str) else (value or [])
        if ctype == "must_contain":
            for kw in kws:
                if kw not in answer:
                    fails.append(f"缺关键词：{kw}")
        elif ctype == "must_contain_any":
            if not any(kw in answer for kw in kws):
                fails.append(f"缺关键词（任一）：{'/'.join(kws)}")
        elif ctype == "must_not_contain":
            for kw in kws:
                if kw in answer:
                    fails.append(f"出现禁用词：{kw}")
    return (not fails), fails
```

**为什么不用 LLM 裁判**：裁判有偏差且烧钱；"推荐了蓝牙耳机A"这种事实用字符串包含判断 100% 可靠、可回归。

### 7.3 基线翻车实录（定性展示）

不是只报数字，还挑了基线翻车的真实例子：

> **基线回答（shop-rec-001：预算500以内推荐降噪耳机）**：
> "推荐华为 FreeBuds 6i，约 399-499 元，主动降噪…"
> **失败点**：编造池外商品（商品池只有蓝牙耳机A/F）、编造价格、该拒答时硬答。

这正是要证明的：**没有系统（工具/知识库/知识边界）= LLM 只能凭训练记忆瞎编**。

### 7.4 已知瑕疵（面试被问主动承认）

| 瑕疵 | 影响 | 改进方向 |
|---|---|---|
| temperature 不一致（Agent 0.2 vs 基线 0.3） | 基线温度更高更"放飞"，翻车率被放大 | 两边同温重跑 |
| 样本只有 27 题 | 小样本，96.3% vs 48% 悬殊但小差异（1.0 vs 0.84）不稳 | 扩到 100+ 题 |
| 单次跑，没报方差 | LLM 有随机性 | 跑 3 次取均值±标准差 |
| 没做显著性检验 | 48% vs 96.3% 悬殊但没 formal test | McNemar 检验 + bootstrap CI |

**一句话**：悬殊差异（48% vs 96.3%）不是噪声能解释的；但严格论文口径还差"同温+多次+显著性检验"。

---

## 8. 一句话总结

> **这套系统的价值不是"用了 LLM"，而是"用工程把 LLM 的不稳定性关进了笼子"。**
> 七维打分让推荐可回归，三层记忆让多轮不断片，混合检索让售后不答错，确定性 Reflection 让编造无处遁形。
> 每一个决策都能回答"为什么这样设计、被否掉的方案是什么、换真实业务时改哪里"。
