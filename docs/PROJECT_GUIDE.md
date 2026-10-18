# ShopPilot 项目全景手册（详细版）

> 一份文档理解整个项目：数据从哪来 → 数据长什么样 → 架构为什么这么设计 → MCP 怎么工作 → 效果如何 → 面试怎么讲。
> 配套：`../README.md`（快速上手）、`design_review.html`（面试富文本）、`benchmark_report.md`（评测明细，脚本自动生成）。
> 本文所有代码示例、JSON、命令均为**本仓库真实运行产物**，可直接复制验证。

---

## 目录

1. [项目速览](#1-项目速览)
2. [三分钟上手](#2-三分钟上手)
3. [数据层详解](#3-数据层详解)（数据来源 / 格式样例 / 如何换成真实电商数据）
4. [架构：8 层与单向依赖](#4-架构8-层与单向依赖)（含"单向依赖"到底是什么）
5. [MCP 协议详解](#5-mcp-协议详解)（三原语 / 真实报文 / 挂载方式 / 安全边界）
6. [前端交互链路](#6-前端交互链路)
7. [评测体系](#7-评测体系)
8. [示例提问全链路拆解](#8-示例提问全链路拆解)
9. [面试 Q&A](#9-面试-qa)
10. [附录：常见改动指引](#10-附录常见改动指引)

---

## 1. 项目速览

**ShopPilot = 面向消费者的电商导购 Agent**。用户输入自然语言需求，Agent 结构化抽取约束 → 并行查询商品池 / 商品口碑 / 售后知识库 → 输出带依据编号的推荐清单卡、对比卡、避坑提示，并跨会话记住用户偏好。

五大导购场景：

| 场景 | 示例输入 | 系统内部链路 | 产出 |
| --- | --- | --- | --- |
| 选购推荐 | 预算500以内推荐一副降噪耳机 | Planner(意图=recommend) → data_query → synthesis | 推荐清单卡（价格/理由/口碑摘要/依据编号[n]） |
| 商品对比 | 蓝牙耳机A和蓝牙耳机F怎么选 | Planner(compare) → data_query ×2 → synthesis | 10 维对比卡 + 分场景结论 |
| 口碑解读 | 蓝牙耳机A翻车点在哪 | Planner(review_analysis) → review_analysis 工具 | 差评维度归因 + 避坑清单 |
| 售后问答 | 蓝牙耳机A保修多久 | Planner(after_sales) → knowledge_qa(RAG) | 强制引用答案（来源可展开溯源） |
| 超范围拒答 | iPhone 18 卖多少钱 | Planner(out_of_scope) → 拒答短路 | 诚实说明商品池外，不编造 |

**不是什么**：不是通用聊天机器人（超范围必拒）、不是单次 LLM 调用（有工具/记忆/自检/评测）、不依赖 LangChain/LangGraph 等现成 Agent 框架（自主搭建 8 层 Harness，vibe coding 驱动高效迭代）。

---

## 2. 三分钟上手

```bash
# 环境：Python 3.11（conda env agent），依赖已装齐；.env 配置 DEEPSEEK_API_KEY/BASE_URL/MODEL
# ① 网页版
D:\conda_envs\agent\python.exe -m streamlit run app.py          # 或双击 start.bat（自动开浏览器）
# ② 控制台
双击 ShopPilot.html                                               # 状态灯变绿后点场景卡片即提问
# ③ 评测（25 题确定性指标）
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --run --compare   # 或双击 benchmark.bat
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --baseline        # 纯 API 基线对照
# ④ MCP Server（由 MCP Host 拉起，不要手动运行）
D:\conda_envs\agent\python.exe -X utf8 -m shoppilot.mcp.server
```

自测（不调模型也能跑的单元级自检）：

```bash
D:\conda_envs\agent\python.exe -X utf8 -m shoppilot.domains.ecommerce.tools   # 6 个工具
D:\conda_envs\agent\python.exe -X utf8 -m shoppilot.rag.rag_lib              # 索引构建 + 混合检索
```

---

## 3. 数据层详解

### 3.1 数据是自己生成的吗？——是"人工构造的教学仿真数据"

| 问题 | 答案 |
| --- | --- |
| 数据来源 | **人工设计**，不是爬取的真实电商数据，也没有用 LLM 批量生成 |
| 为什么不用真实数据 | ①教学数据可控：能精确构造"差评集中在续航""宣传降噪 vs 实际 35dB"这类**典型 badcase**；②可复现：同一份 CSV 保证评测指标稳定；③无合规风险：不含真实用户评论与个人信息；④离线可跑：不需要 API Key 也能跑工具层与检索层 |
| 数据规模 | 商品 10 个 / 评论 65 条（其中差评 36 条，rating≤3）/ 销量记录 11 条 / 知识库文档 5 篇 / 评测题 25 道 |
| 商品池即"知识边界" | 池外型号（如 iPhone、蓝牙耳机Z）系统一律拒答或明确说明查不到——这是**防编造的第一道闸**，不是数据缺失的副作用 |

### 3.2 商品池 `data/ecommerce/products.csv`

字段定义：

| 字段 | 类型 | 含义 | 用途 |
| --- | --- | --- | --- |
| name | str | 商品名（全称，回答中禁止简称） | 实体识别、对比、引用溯源 |
| category | str | 品类（5 类，见下） | 意图识别后的筛选维度 |
| price | float | 售价（元） | 预算约束硬校验 |
| rating | float | 商品评分（0-5） | 排序与推荐理由 |
| sales_month | int | 月销量 | 热度对比 |
| warranty_months | int | 保修月数 | 售后问答、结构化卡 |
| highlights | str | 卖点（分号分隔） | 关键词筛选、推荐卡展示 |

真实首行（文件原文）：

```csv
name,category,price,rating,sales_month,warranty_months,highlights
蓝牙耳机A,数码配件,349,4.3,15230,12,35dB主动降噪；单次6h总续航24h；IPX4防水；蓝牙5.3
```

全量清单（10 商品 × 5 品类）：

| 品类 | 商品 | 价格 | 评分 | 保修 |
| --- | --- | --- | --- | --- |
| 数码配件 | 蓝牙耳机A / 蓝牙耳机F | 349 / 899 | 4.3 / 4.6 | 12 / 24 月 |
| 数码配件 | 充电宝B / 手机壳C / 数据线E | 129 / 29 / 39 | 4.0 / 4.5 / 4.1 | 12 / 3 / 6 月 |
| 智能穿戴 | 智能手表D | 599 | 4.2 | 12 月 |
| 电脑外设 | 机械键盘G / 显示器H | 329 / 1099 | 4.7 / 4.5 | 12 / 36 月 |
| 网络设备 | 路由器I | 249 | 4.4 | 12 月 |
| 智能家居 | 智能音箱J | 199 | 4.3 | 12 月 |

**数据里刻意埋的"坑"**（用于验证系统是否真能发现问题）：

- 蓝牙耳机A：宣传 35dB，评论实测"实际单次只有4小时多没有宣传的6小时"（续航缩水）、差评率 70%（10 条评论 7 条差评）
- 充电宝B：标称 20000mAh 但"实测只能充手机1.5次"，且有安全类差评"充到80%就断电用了一个月还鼓包了"
- 智能手表D：标称 14 天续航，差评"实际4天就没电"；亮点政策与售后政策冲突（见 5.2 题）
- 显示器H：保修 36 月（其他多为 12 月），退货争议点（3 个以内亮点不算质量问题）

### 3.3 评论 `data/ecommerce/reviews.csv`

| 字段 | 含义 | 口径 |
| --- | --- | --- |
| product | 商品名 | 关联 products.name |
| rating | 星级 1-5 | **rating ≤ 3 记为差评**（`adapter.BAD_RATING = 3`） |
| aspect | 差评维度 | 实际用到 21 种：质量/音质/降噪/续航/物流/客服/描述不符/做工/噪音/佩戴/信号/兼容性/价格/外观/安装/散热/画质/稳定性/手感/售后/语音助手 |
| text | 评论原文 | Agent 引用与"搜索评论"工具的检索对象 |
| date | 日期 | 差评趋势工具按日聚合 |

真实两行：

```csv
product,rating,aspect,text,date
蓝牙耳机A,1,物流,快递走了12天而且外包装破损,2026-09-01
蓝牙耳机A,2,音质,低音杂音明显戴久了耳朵疼,2026-09-02
```

工具真实输出示例（`query_product_reviews("蓝牙耳机A")`，代码实际返回）：

```json
{"product": "蓝牙耳机A", "aspect": "全部", "total_reviews": 10, "bad_reviews": 7,
 "bad_rate": "70%", "aspect_distribution": {"音质": 2, "物流": 1, "质量": 1, "描述不符": 1, "客服": 1, "续航": 1},
 "samples": ["[1星/物流] 快递走了12天而且外包装破损", "[2星/音质] 低音杂音明显戴久了耳朵疼", "..."]}
```

### 3.4 销量 `data/ecommerce/sales.csv`（11 条）

```csv
category,product,sales,rank
数码配件,手机壳C,24560,1
数码配件,蓝牙耳机A,15230,2
```

注意：**同一商品可出现在多个品类**（蓝牙耳机A 同时在"数码配件"与"智能穿戴"），用于验证"哪个卖得更好"类问题是否正确使用销量数据而非凭感觉。

### 3.5 知识库 `data/ecommerce/knowledge/*.md`（5 篇）

| 文件 | 内容 | 服务的场景 |
| --- | --- | --- |
| `product_manual_bluetooth.md` | 蓝牙耳机A 参数/保养/故障排查/保修 | 保修问答、故障排查 |
| `return_policy.md` | 七天无理由/换新/维修/客服响应 | 售后问答、退换货 |
| `logistics_faq.md` | 物流与发货 | 售后问答 |
| `seller_guide.md` | 商家侧规则 | 边界问题 |
| `buying_guide.md` | 选购指南（降噪耳机/充电宝/键盘/显示器/路由器/音箱/售后怎么看） | 推荐理由、避坑 |

结构示例（`return_policy.md` 真实片段，切分依赖 `##` 标题结构）：

```markdown
# 售后与退换货政策
## 七天无理由退货
- 自签收之日起 7 天内，商品不影响二次销售（吊牌完整、配件齐全）可申请无理由退货
- 退货邮费：非质量问题由买家承担；质量问题由卖家承担
## 质量问题换新
- 签收后 15 天内出现非人为质量故障，可免费换新，来回运费卖家承担
```

### 3.6 评测题库 `data/ecommerce/bench_cases.json`（25 题）

字段与字节 llmeval 数据集 schema 同源（`id/dimension/task_type/prompt/reference/constraints/meta`），一条真实 case：

```json
{
  "id": "shop-rec-001", "dimension": "agent", "task_type": "agent_tool", "difficulty": "easy",
  "prompt": "预算500以内推荐一副降噪耳机",
  "reference": "预算500内可推荐：蓝牙耳机A（349元，35dB降噪，4.3分）为性价比首选；蓝牙耳机F（899元）超预算不应推荐。",
  "constraints": [
    {"type": "must_contain", "value": ["蓝牙耳机A"]},
    {"type": "must_not_contain", "value": ["推荐蓝牙耳机F"]},
    {"type": "language", "value": "zh"}
  ],
  "meta": {
    "expected_tools": ["search_products"],
    "expected_behavior": "answer",
    "rationale": "预算约束筛选 + 推荐理由，考察是否推荐超预算商品",
    "tags": ["选购推荐"]
  }
}
```

分布：推荐 7 题、对比 4 题、口碑 5 题、售后 5 题、拒答 4 题。

### 3.7 数据 → 系统映射（哪个工具读哪个文件）

| 文件 | 读取函数 | 消费者 |
| --- | --- | --- |
| products.csv | `adapter.load_products()` | search_products / get_product_info / Planner 的商品池注入 |
| reviews.csv | `adapter.load_reviews()` | query_product_reviews / search_reviews / daily_bad_review_trend |
| sales.csv | `adapter.load_sales()` | query_sales_rank |
| knowledge/*.md | `rag_lib.hybrid_search(kb_dir=...)` | knowledge_qa 子 Agent |
| bench_cases.json | `benchmark.load_cases()` | 评测脚本（不参与线上推理） |

### 3.8 换成真实电商数据怎么做（可照抄）

**第一步：只改 `adapter.py` 的加载函数，保持返回列名不变。** 6 个工具的签名、schema、前端、其他 7 层全部不动：

```python
# 现在（本地 CSV）
@lru_cache(maxsize=1)
def load_products() -> pd.DataFrame:
    return pd.read_csv(ECOM_DIR / "products.csv", encoding="utf-8")

# 换成真实 API（示意：某电商开放平台商品接口）
import requests, os
API_KEY = os.getenv("SHOP_API_KEY")

@lru_cache(maxsize=1)          # 仍保留缓存，避免每轮对话都打接口
def load_products() -> pd.DataFrame:
    rows, page = [], 1
    while True:
        resp = requests.get(
            "https://api.example-shop.com/v1/products",
            headers={"Authorization": f"Bearer {API_KEY}"},
            params={"page": page, "page_size": 50}, timeout=10).json()
        rows += resp["data"]["items"]
        if len(rows) >= resp["data"]["total"] or page >= 20:
            break
        page += 1
    df = pd.DataFrame(rows)
    # 列名映射：平台字段 → 系统字段（关键：下游只认这套列名）
    return df.rename(columns={
        "item_title": "name", "category_name": "category",
        "price_cent": "price",          # 分→元
        "score": "rating", "sold_30d": "sales_month",
        "warranty_month": "warranty_months", "selling_points": "highlights",
    }).assign(price=lambda d: d["price"] / 100)
```

需要遵守的四条工程纪律：

1. **口径统一**：分/元、天/月销、好评率/评分 必须显式换算；定义写进函数 docstring。
2. **缓存与限流**：`lru_cache` 保留；真实接口要加重试（可复用 `harness/loop.py` 的退避写法）与超时；分页上限保护。
3. **数据时间戳**：真实数据会变。在 `ProductCard`/回答中标注"数据截至 YYYY-MM-DD"，否则用户拿旧价下单会纠纷。
4. **隐私与合规**：真实评论需脱敏（手机号、订单号、昵称），只保留 text/rating/aspect/date。

**第二步：知识库换成真实政策文档。** 两种做法：

```python
# A. 预处理成 Markdown（推荐）：保留 ## 标题结构，切分器依赖标题
#    PDF → markdown（marker/pymupdf4llm）→ 存入 data/ecommerce/knowledge/
# B. 改读取器：rag_lib.build_index() 里把 md.read_text() 换成你的解析函数
for md in sorted(kb_dir.glob("*.md")):        # ← 改成遍历 pdf/html 即可
    chunks.extend(chunk_markdown(parse(md), md.name))
```

注意：切分是**结构感知**的（按 `#`~`###` 标题 + 超长段 300 字 overlap 60 字），所以真实文档最好先转成带标题的 Markdown，直接扔 PDF 进来会导致整篇一个 chunk。

**第三步：题库换成线上真实 query。** 从搜索词日志/客服工单采样 100~300 条，人工标注四个字段：`reference`（参考答案）、`constraints.must_contain`（关键事实）、`meta.expected_tools`、`meta.expected_behavior`。采样时保证：60% 池内商品、20% 池外型号、20% 边界/无关问题——这三类分别对应"该答/该拒/该短路"三种行为。

---

## 4. 架构：三个部件 + 两个记忆（刻意简单）

> 说明：项目早期文档写过"8 层 Harness"，那是把配置/契约/工具/循环/编排都拆开写的结果。
> 重构成熟版后**刻意收敛**：运行时只有 3 个部件 + 2 个记忆 + 数据层，不再为分层而分层。
> "单向依赖"的纪律保留：数据层不知道 Agent 存在，Agent 不依赖前端。

| 部件 | 文件 | 职责 |
| --- | --- | --- |
| 规划 | `agents/planner.py` | 意图/约束抽取 + 拆子任务（json_object + Pydantic 校验 + 1 次重写） |
| 执行 | `agents/executor.py` | 子任务按拓扑分层并发；data/review 类走有界 ReAct 循环，knowledge 类走 RAG；工具按 task_type 预加载 |
| 成稿 | `agents/composer.py` | 聚合子结论 → 结构化产出卡 + 强制引用 + 五项确定性自检（不过关修复一次） |
| 短期记忆 | `harness/memory.py::ShortTermMemory` | 会话滑窗 + 滚动摘要 |
| 长期记忆 | `harness/memory.py::LongTermMemory` | 跨会话 Markdown（data/memory/{user}.md），关键词检索注入，可查看可删 |
| 数据层 | `domains/ecommerce/` | 商品池/评论/销量/知识库 + 7 个业务工具（换数据只改这里） |

### 4.1 深度研究模式（借鉴 DeepResearch Agent 的做法）

**定义**：层与层之间只允许**上层 import 下层**，下层代码里永远不出现上层的模块名。

```
app.py / benchmark.py / mcp/server.py      ← 只能 import agents、domains、harness、models
        ↓
agents/{planner,dispatcher,aggregator,reflector,team}
        ↓
harness/{loop,tools,memory,observability}
        ↓
domains/ecommerce + rag
        ↓
models.py（契约，谁都能 import 它）
        ↓
config.py / llm.py（基础设施，不依赖任何人）
```

三条硬规则：

1. `models.py` 不 import 任何业务模块（它只依赖 pydantic）——所以任何层都能安全引用契约；
2. `domains/` 不 import `agents/`——业务数据层不知道有 Agent 存在；
3. `agents/` 不 import `app.py`——编排层不关心前端。

**违反了会怎样**（真实反面教材）：

```python
# 反例 A：domains 反向依赖 agents
# domains/ecommerce/tools.py 里写：from shoppilot.agents import planner
# → planner 又 import domains 的 registry → Python 循环 import 直接崩溃

# 反例 B：harness 反向依赖业务
# harness/tools.py 里写：from shoppilot.domains.ecommerce.adapter import load_products
# → 想把工具层复用到海事项目，就必须把电商数据一起拖上；换业务时 harness 全部重写
```

**收益（也是我们敢换业务的原因）**：海事场景接进来只需要在 `domains/` 加一个 adapter + 一组工具（替换 6 个工具实现），上面 6 层**一行不改**——这就是分层唯一值得衡量的指标：换业务要改几层。

### 4.2 逐层详解

| 层 | 文件 | 职责 | 关键 API / 输出 |
| --- | --- | --- | --- |
| ① 配置 | `config.py` | 唯一的环境变量出口（pydantic-settings，读 .env；无该包时有轻量兜底） | `settings.max_steps / subtask_timeout_s / ECOM_DIR` |
| ② LLM Provider | `shoppilot/llm.py` | 三种调用模式统一封装 + token 计数 + 共享实例 | `chat()`（带工具）/`chat_json()`（结构化）/`chat_text()` |
| ③ 数据契约 | `shoppilot/models.py` | LLM/工具脏 JSON 的入口清洗（`field_validator`）、DAG 合法性校验、分层降级 | `ShoppingQuery/Plan/SubTask/TeamResult/PreferenceProfile/BenchCase` |
| ④ 有界 ReAct | `harness/loop.py` | 推理-行动-观察循环 + 三重预算 + 并行工具 + 退避重试 + 超时降级 + 输出校验钩子 | `react_loop() → LoopResult(answer,tokens,tool_trace,degraded)` |
| ⑤ 工具注册中心 | `harness/tools.py` | Pydantic 自动生成 OpenAI schema、按 task_type 预加载、结果缓存、危险工具确认闸 | `registry.call(name,args)` / `registry.preload("data_query")` |
| ⑥ 记忆/可观测 | `harness/memory.py`、`observability.py` | 四类记忆统一门面；JSONL trace + 分步 token 归因 + 事件回调 | `MemoryManager / Tracer(emit/log/summary)` |
| ⑦ 编排 | `agents/*.py` | 规划→并行调度→聚合仲裁→反思修复 | `run_team() → TeamResult` |
| ⑧ 前端/评测/协议 | `app.py`、`benchmark.py`、`mcp/server.py` | 三个平级消费方，只调用 `run_team()` 与 `registry` | 互不依赖，可单独替换 |

### 4.3 三范式组合与 Replan（规划 → ReAct → 反思）

```
用户输入
  │
  ├─ Planner（规划）  意图分类 + ShoppingQuery 抽取 + 拆子任务 DAG（json_object + Pydantic 校验 + 1 次 Reflection 重写）
  │     ↓  防误拒复核（确定性规则，零 token）· 库外实体闸
  ├─ Dispatcher（DAG 分层，同层 ThreadPoolExecutor 并发）
  │     ├─ data_query / review_analysis → ToolAgent（有界 ReAct，工具按 task_type 预加载）
  │     ├─ knowledge_qa               → RAG 问答（强制 [n] 引用）
  │     └─ synthesis                   → 纯 LLM（不给工具，防乱调）
  │     ↓
  ├─ Replan（计划可更新）  任一子任务 degraded → 带失败证据重拆补救任务（换路径），最多一轮防循环
  │     ↓
  ├─ Aggregator（聚合）   冲突仲裁 + 强制引用 + 结构化产出卡（Recommendation/CompareCard/CaveatList）
  │     ↓
  └─ Reflector（反思）    五项确定性自检 → 不过关触发一次修复（LLM 只做修复）
```

**为什么要 Replan**：规划执行最大的坑是"计划跟不上变化"（以为问题在数据库，查完发现是缓存击穿）。我们的做法是把计划当**可更新的任务清单**：失败不是终点，而是重新规划的一手证据（失败信息里写着"参数校验失败：期望 product 字段"这类可操作线索）。

**为什么自检用规则不用 LLM 自评**：LLM 自评存在自我一致性偏差（自己给自己打高分）。引用编号越界、价格超预算、库外商品编造、误拒答这四类错误用规则判断 100% 可靠、零 token、可回归；LLM 只在规则判失败后负责"修"。

五项自检一览（`agents/reflector.py`）：

| # | 检查 | 判定逻辑 | 不过关的后果 |
| --- | --- | --- | --- |
| ① | 引用可验证 | 答案中所有 `[n]` 必须落在 citations/资料编号范围内 | 触发修复（防编造来源） |
| ② | 预算约束 | recommend 卡内价格 ≤ `query.budget_max` | 触发修复 |
| ③ | 商品溯源 | 卡内商品名必须出现在资料或用户原文中 | 触发修复（防编造商品） |
| ④ | 误拒答 | after_sales 场景检索到资料却回答"没有相关资料" | 触发修复 |
| ⑤ | 目标覆盖 | 用户点名的商品必须出现在回答；recommend/compare/review_analysis 必须出卡 | 触发修复（拦过早交付） |

### 4.4 工具系统

**schema 自动生成**（真实输出，`search_products`）：Pydantic 参数模型 → OpenAI function schema，函数与 schema 永远一致：

```json
{"type": "function", "function": {
  "name": "search_products",
  "description": "按品类/预算/关键词筛选商品池并返回候选商品（价格、评分、卖点）。任何推荐类问题先调它。",
  "parameters": {"type": "object", "properties": {
    "category": {"description": "品类：数码配件/智能穿戴/电脑外设/网络设备/智能家居，可空", "type": "string"},
    "budget_max": {"description": "预算上限（元）", "type": "number"},
    "budget_min": {"description": "预算下限（元）", "type": "number"},
    "keyword": {"description": "关键词，如 降噪/快充/游戏", "type": "string"},
    "limit": {"description": "最多返回条数，默认5", "type": "integer"}}, "required": []}}}
```

**task_type 预加载表**（`domains/ecommerce/tools.py` 的 TASK_TOOL_MAP）：

| task_type | 注入的工具 | 为什么 |
| --- | --- | --- |
| data_query | search_products / get_product_info / query_sales_rank / daily_bad_review_trend | 找商品、比价格 |
| review_analysis | query_product_reviews / search_reviews / daily_bad_review_trend | 口碑、差评 |
| knowledge_qa | 无（走 RAG） | Function tools 对 RAG 无意义 |
| synthesis | 无 | 纯推理，防止乱调工具 |

效果实测：工具密集场景（3 轮以上工具循环）比全量注入省 **16-28% token**（`benchmark.py --compare` 可复现）。

**结果缓存**：key = `工具名 + 规范化参数 JSON`，LRU（默认 256 条）。DAG 内多个任务查同一商品时直接命中。

**危险工具确认闸**：`registry.confirm_fn = lambda name, args: True/False`。CLI/benchmark 无头模式默认拒绝；Streamlit 前端可换成 `st.confirm`。当前 6 个工具全是只读查询，闸门默认不触发——但它是给"下单/退款"类动作预留的权限边界。

### 4.5 ReAct 循环的三重预算与降级路径

| 触发条件 | 行为 | 用户可见 |
| --- | --- | --- |
| 步数超 `max_steps`(8) | 返回兜底文案 + degraded | 前端 ⚠️ 标注降级 |
| token 超 `max_tokens_budget`(6万) | 中断 + degraded | 同上 |
| 成本超 `max_cost_cny`(0.5) | 中断 + degraded | 同上 |
| LLM 调用异常 | 指数退避重试 2 次，仍失败→降级 | trace 里可见重试记录 |
| 单工具执行超 90s | 把超时错误喂回模型让它换参数/换工具 | trace 里 ok=false |
| 模型传错参数 | Pydantic 校验失败，返回结构化错误 + 期望字段 | 模型自我纠正 |
| 子任务整体异常 | 记 degraded，喂兜底文案，其他任务继续 | Aggregator 如实告知"未能查询到" |

原则：**任何单点失败都不许炸全链路**，最差也要产出一份带降级标注的回答。

### 4.6 记忆四分类（落盘形态）

| 类型 | 落盘文件（真实结构） | 生命周期 |
| --- | --- | --- |
| 短期（会话滑窗+滚动摘要） | `data/sessions/{sid}.json` 的 `working.recent/old/summary` | 单会话，超限自动摘要压缩 |
| Session State（结构化任务状态） | 同上文件的 `state` 字段：`{"goal","intent","completed_tasks[{id,type,goal,degraded}],"key_findings"}` | 单会话，跨轮注入 Planner |
| 长期（用户画像） | `data/sessions/profile_{user}.json`：`{"budget_tier":"300-800","priorities":["降噪","预算"],"categories":["耳机"],"history":[...],"updated_at":"..."}` | 跨会话，可编辑可删除 |
| 外部知识库 | `data/ecommerce/knowledge/*.md` + `shoppilot/rag/index/`（向量+BM25） | 全局共享 |

**写入过筛四问**（对应 kamacoder《Agent 的记忆》）：

1. 是否长期稳定？——预算档/在意维度稳定；"我今天想问个登山推荐"不写；
2. 以后是否有用？——只写固定三类字段，不做自由文本堆砌；
3. 是否被确认？——全部来自 ShoppingQuery 的**确定性抽取**（用户原话），不信模型猜测；
4. 隐私合规——本地文件态、可一键 `forget()` 删除。

**检索按需**：每轮只把"画像块 + 任务状态块 + 最近原文"注入 Planner，不全量塞历史。画像块真实内容示例：

```
- 预算档：300-800
- 在意维度：降噪、预算
- 关注品类：耳机
- 最近询问：预算500以内推荐一副降噪耳机；...
- 更新时间：2026-10-18 10:22
```

### 4.4 RAG（混合检索）

- **切分**：结构感知（按 `#`~`###` 标题），超长段 300 字、overlap 60 字（避免答案被切断在两半导致两边都检索不到）；
- **向量路**：TF-IDF 字符 n-gram（离线秒建、中文友好）→ FAISS 内积检索（已 L2 归一化）；
- **BM25 路**：jieba 分词 + BM25Okapi（对型号/数字/专名极准）；
- **融合**：RRF `score = Σ 1/(60 + rank)`，两路都命中的 chunk 排最前。

真实检索（query="耳机保修多久"）：

| chunk | 来源 | 小节 | RRF | 向量命中 | BM25 命中 |
| --- | --- | --- | --- | --- | --- |
| 整机保修 12 个月，电池保修 6 个月…… | product_manual_bluetooth.md | 保修政策 | 0.0328 | ✓ | ✓ |
| 保修期以商品页和产品手册为准…… | buying_guide.md | 售后怎么看 | 0.0317 | ✓ | ✓ |
| 保修期内：凭订单号免费维修…… | return_policy.md | 维修政策 | 0.0161 | ✓ | ✗ |

**索引自愈**：`ensure_index()` 检测文档数变化或索引缺失会自动重建；Embedding 想换成 bge-small-zh 只需实现 `encode(texts)->np.ndarray`（实现类替换一处，其余检索/问答代码零改动）。

---

## 5. MCP 协议详解

### 5.1 为什么需要 MCP（要解决的问题）

没有 MCP 时，把工具接给不同 AI 应用要各写一套适配：Claude 接一遍、Cursor 接一遍、公司内部 Agent 再接一遍——"越做越像胶水工程"。MCP（Model Context Protocol）把这套接入标准化：**工具方按标准暴露能力，任何 Host 支持 MCP 即可挂载**。类比：前后端之间用 HTTP API，而不是各写一套私有协议。

### 5.2 三个角色与隔离边界

| 角色 | 是谁 | 职责 | 本项目对应 |
| --- | --- | --- | --- |
| Host | 用户用的 AI 应用（Claude Desktop / Cursor / 你的 Streamlit 应用） | 交互、模型调用、**权限决策**、上下文聚合 | `app.py` 扮演 Host，控确认闸与画像编辑 |
| Client | Host 内连接某个 Server 的组件（一 Host 多 Client，一 Client 一 Server） | 建连接、协商能力、发 tools/list、tools/call | `ToolRegistry` 的外部等价物 |
| Server | 提供能力的一方 | 只应答协议方法，**不负责与用户对话** | `shoppilot/mcp/server.py` |

隔离原则：Server 不应看到全部对话、不应访问其他 Server 的数据；权限与上下文由 Host 统一管。

### 5.3 与 Function Calling 的关系（不同层面）

| 维度 | Function Calling | MCP |
| --- | --- | --- |
| 层面 | 模型侧 | 应用侧 |
| 解决什么 | 模型本次要不要调工具、调哪个、传什么参数 | 工具从哪来、怎么发现（tools/list）、怎么连接、怎么通信、怎么暴露给不同应用 |
| 本项目 | `harness/loop.py` 循环里的 `tools=[...]` | `mcp/server.py` 让外部 Host 发现同一批工具 |

实际链路：Host 通过 MCP 发现工具 → 转成模型可用的 function schema → 模型决定调用 → Host 通过 MCP `tools/call` 执行 → 结果回喂模型 → 进入 ReAct 循环。**本项目内部与外部共用同一个 `ToolRegistry`**，不存在两套工具实现。

### 5.4 三原语：Tools / Resources / Prompts

口诀：**Tools 是能做什么，Resources 是能看什么，Prompts 是怎么做**。

本项目实际暴露（SDK 客户端实测输出）：

```
TOOLS: ['search_products', 'get_product_info', 'query_product_reviews',
        'search_reviews', 'query_sales_rank', 'daily_bad_review_trend']
RESOURCES: ['shoppilot://products', 'shoppilot://knowledge/{doc_name}']
PROMPTS: [('shopping_guide', ['question'])]
```

| 原语 | 声明方式 | 本项目 | 为什么要它 |
| --- | --- | --- | --- |
| Tools | `@mcp.tool()` | 6 个只读查询 | 模型主动调用 |
| Resources | `@mcp.resource("shoppilot://products")` | 商品池 JSON、`shoppilot://knowledge/{doc}` 文档原文 | 被动读取的上下文，避免把整个商品库塞进 Prompt |
| Prompts | `@mcp.prompt()` | `shopping_guide(question)`：先筛商品→再查口碑→后看售后的规范顺序 | 预置模板，让 Host 侧模型更稳 |

### 5.5 真实协议报文（本机抓包）

**① 初始化与能力协商**

```json
{"jsonrpc":"2.0","id":1,"result":{
  "protocolVersion":"2024-11-05",
  "capabilities":{"experimental":{},"prompts":{"listChanged":false},
                  "resources":{"subscribe":false,"listChanged":false},
                  "tools":{"listChanged":false}},
  "serverInfo":{"name":"shoppilot-tools","version":"1.30.0"}}}
```

**② 工具发现 `tools/list`（节选，inputSchema 由 Pydantic 自动生成）**

```json
{"jsonrpc":"2.0","id":2,"result":{"tools":[
  {"name":"query_product_reviews",
   "description":"查商品评论统计：差评数、差评率、差评维度分布、典型评论原文。分析口碑/痛点时用。",
   "inputSchema":{"properties":{
      "product":{"title":"Product","type":"string"},
      "aspect":{"default":"全部","title":"Aspect","type":"string"}},
     "required":["product"],"title":"query_product_reviewsArguments","type":"object"}},
  ...其余 5 个工具]}}
```

**③ 工具调用 `tools/call`（真实响应）**

```json
{"jsonrpc":"2.0","id":3,"result":{
  "content":[{"type":"text","text":"{\"product\": \"蓝牙耳机A\", \"aspect\": \"续航\", \"total_reviews\": 10, \"bad_reviews\": 1, \"bad_rate\": \"10%\", \"aspect_distribution\": {\"续航\": 1}, \"samples\": [\"[2星/续航] 实际单次只有4小时多没有宣传的6小时\"]}"}],
  "structuredContent":{"result":"{同上}"},
  "isError":false}}
```

**④ 资源读取 `resources/read`**

```
read_resource("shoppilot://products")
→ [{"name": "蓝牙耳机A", "category": "数码配件", "price": 349, "rating": 4.3, "sales_month": 1523...
read_resource("shoppilot://knowledge/return_policy")
→ "# 售后与退换货政策 ## 七天无理由退货 - 自签收之日起 7 天内..."
```

**⑤ Prompt 获取 `prompts/get`**（Host 侧渲染成提示词）

```json
{"name":"shopping_guide","arguments":[{"name":"question","required":true}]}
→ 渲染文本："你是电商导购助手。请按以下顺序处理用户问题：{question} 1. search_products 按预算/品类筛选候选商品…"
```

### 5.6 怎么挂载

**Claude Desktop / 任意 MCP Host 的配置**：

```json
{"mcpServers": {"shoppilot": {
  "command": "D:/conda_envs/agent/python.exe",
  "args": ["-X", "utf8", "-m", "shoppilot.mcp.server"],
  "cwd": "G:/agent_internship"}}}
```

**自己写 Host 调用（本项目用的验证方式，真实可跑）**：

```python
import asyncio, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command=sys.executable,
                                   args=["-X","utf8","-m","shoppilot.mcp.server"],
                                   cwd=r"g:\agent_internship")
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()                       # 发现
            res = await s.call_tool("search_products", {"budget_max": 500})  # 调用
            doc = await s.read_resource("shoppilot://knowledge/return_policy")  # 读资源
            print([t.name for t in tools.tools], res.content[0].text[:80])

asyncio.run(main())
```

### 5.7 安全边界（本项目的做法）

| 风险 | 做法 |
| --- | --- |
| 工具副作用 | 6 个工具**全部只读**（查询类），无下单/退款/删除类动作 |
| 越权读文件 | Resources 只允许读 `data/ecommerce/knowledge/*.md`：`Path(doc_name).name` 取文件名 + 父目录校验，防目录穿越（`../../.env` 会被拒绝） |
| 参数污染 | 每个工具参数是 Pydantic 模型，入口强校验，错误结构化返回让模型自我纠正 |
| 高危动作 | `ToolRegistry.confirm_fn` 确认闸预留（无头模式默认拒绝） |
| 上下文泄漏 | Server 只应答协议方法，看不到用户对话 |
| 传输选择 | 本地数据 → stdio（简单快）；若要远程部署需补 Streamable HTTP + 鉴权 + 多租户隔离（本项目明确不做） |

### 5.8 什么时候不该上 MCP

数据全在本地、只有自己的应用在用 → 直接 `registry.call()` 最省事。MCP 的价值在"工具要被多个 Host 复用"。所以本项目**内部走 ToolRegistry（快路径）、对外提供 MCP（可插拔路径）**，两者共享同一份实现。

---

## 6. 前端交互链路

```
浏览器 → Streamlit rerun → app.py
   ├─ 侧边栏：会话列表 / 画像面板（可编辑可删除）/ 示例问题 / token 统计
   ├─ 空状态：问候首屏 + 4 张场景卡（点击即提问，与控制台 ?q= 直达同一套逻辑）
   └─ 有输入时：
        st.status 容器 ← 事件流（observability emit 实时驱动）
            ① plan        → 任务拆解卡片逐条打勾（[ ] → [x]）
            ② task_start  → 状态栏显示当前任务
            ③ tool_end    → 工具调用折叠框（参数 + 结果摘要）
            ④ replan      → 显示补救任务
            ⑤ reflect_end → 通过/修复中
        最终回答：write_stream 分块流式 + 结构化卡 + 引用来源展开器 + 画像学习提示
```

关键坑与对策：会话状态用 `st.session_state`（rerun 不丢）、LLM 与索引用模块级单例/lru_cache（不重复加载）、事件回调包 try/except（前端异常不影响 Agent）、降级用 `st.warning` 局部呈现（不整页报错）、每次运行后 `st.rerun()` 让历史与产物卡片对齐。

---

## 7. 评测体系

### 7.1 四项确定性指标（零裁判 token）

| 指标 | 判定逻辑 | 防的是什么 |
| --- | --- | --- |
| task_completed | 无降级 且 constraints 全过 且拒答行为正确 | 半成品交付 |
| tool_selection_acc | 实际工具集合对 `meta.expected_tools` 的覆盖率 | 该查的不查、不该调的乱调 |
| refusal_correct | `expected_behavior` 与实际行为一致（拒答=职责外/库外/诚实披露查不到） | 该拒不拒、不该拒硬拒 |
| citation_verifiable | 答案中 `[n]` 全部落在资料编号范围内 | 编造来源 |

`constraints` 支持 `must_contain`（数组内 AND）、`must_contain_any`（数组内 OR，兼容"12个月/一年"表述差异）、`must_not_contain`、`language`。

### 7.2 基线对照（回应"你不就是调 API 吗"）

| 方案 | 任务完成率 | 拒答正确率 | tokens/题 |
| --- | --- | --- | --- |
| 纯 API 直调（同 25 题，无工具/无知识库/无编排） | **0.48** | **0.84** | 718 |
| ShopPilot Agent | **1.00** | **1.00** | 10706 |

基线翻车实录：推荐类 5 题全灭（编造"华为 FreeBuds 6i 约 399-499 元"）、拒答类 4 题全硬答、售后数字靠猜。结论：Agent 用约 15 倍 token 买的是**不编造、可溯源、该拒就拒**。

### 7.3 复现与波动说明

```bash
python -X utf8 benchmark.py --run            # 主轮
python -X utf8 benchmark.py --run --compare  # + 预加载 vs 全量 token 对照
python -X utf8 benchmark.py --baseline       # + 纯 API 基线（结果并入报告）
```

单轮完成率在 92%~100% 间波动（±1 题），波动源：引用编号偶发越界、"质量"类同义改写（做工/连不上）。这是评测存在的意义——把不确定性显性化，而不是靠调参掩盖。

---

## 8. 示例提问全链路拆解

**例：预算500以内推荐一副降噪耳机**

1. Planner 输出：`intent=recommend, category=耳机, budget_max=500, concerns=[降噪]`，拆 2 个子任务：`data_query#1 筛选预算500以内具备降噪的耳机` → `synthesis#2 出推荐清单`；
2. ToolAgent 调 `search_products(budget_max=500, keyword=降噪)` → `{"count":1,"products":[{"name":"蓝牙耳机A","price":349,...}]}`；
3. Synthesis 基于结果出结论（349 元唯一符合、899 元的耳机F 超预算并说明）；
4. Aggregator 强制引用输出：`蓝牙耳机A 售价349元，评分4.3，月销15230，保修12个月 [1]` + 推荐清单卡；
5. Reflector 检查：引用 [1] 合法 ✓、价格 349 ≤ 500 ✓、商品可溯源 ✓、未误拒 ✓、有结构化卡 ✓；
6. 记忆回写：画像学到 `预算档 300-800`、`在意维度 降噪`、`关注品类 耳机`（前端提示"本轮记住的偏好"）；
7. 前端展示：任务卡片打勾 → 工具折叠框 → 推荐卡 → 引用展开器 → 画像提示。

其余四类场景（对比/口碑/售后/拒答）链路见 §1 表格。

---

## 9. 面试 Q&A

1. **不就是调 API 吗？** API 解决"会说"，Agent 解决"系统做对"：`llm.py` 不到 100 行，其余是重试退避/超时降级/Replan/确定性自检/记忆/评测。基线实测 0.48 vs 1.00。
2. **和复现品区别？** 承认方法论同源；差异：业务深度、可解释记忆、确定性 Reflection、评测闭环、MCP 三原语，逐条有数字。
3. **ReAct 怎么防跑散？** 三重预算 + 同 goal 缓存 + 工具预加载 + 停止条件。
4. **Reflection 为什么用规则？** LLM 自评有自我讨好偏差；引用/预算/编造/拒答用规则 100% 可靠。
5. **长对话为什么变傻？** 上下文污染+窗口+计费；滑窗+摘要+Session State 三层解。
6. **记忆怎么防脏？** 只写确定性抽取的用户明确表达；覆盖更新；上限清理；forget 删除；updated_at。
7. **RAG 为什么混合检索？** 向量懂语义不懂数字，BM25 反之，RRF 融合。
8. **幻觉怎么治？** 生成侧强制引用+不足拒答；校验侧五项规则；评测侧引用可验证率。
9. **MCP 和 Function Calling？** FC 模型侧、MCP 应用侧；本项目 Server 与内部 Agent 共享 ToolRegistry。
10. **数据是自己造的吗？** 是教学仿真数据（人工设计、非爬取），理由与换真实数据的四步改法见 §3.8。
11. **为什么不用 LangChain？** 依赖越少每层越讲得清；换业务只改 1 层（adapter），且预加载/确认闸这类定制能力框架里反而要绕着写。
12. **指标为什么不用 LLM 裁判？** 裁判有偏差且烧钱；四件事规则判 100% 可靠、可回归。

---

## 10. 附录：常见改动指引

| 我想… | 改哪里 | 注意 |
| --- | --- | --- |
| 加一个新工具 | `domains/ecommerce/tools.py`：写实现 + Pydantic args 模型 + `register_all()` 注册 | 声明 `task_types` 决定预加载分组；返回 JSON 字符串且带 `error` 字段 |
| 换真实商品数据 | `adapter.load_products()`（见 §3.8） | 列名映射 + 分/元换算 + 缓存 + 时间戳 |
| 换 RAG 知识库 | 传 `kb_dir` 或改 `rag_lib.build_index()` 的文件遍历 | 保留标题结构，否则切分失效 |
| 换 Embedding | 实现 `encode(texts)->np.ndarray` 换掉 `TfidfEmbedder` | 检索与问答代码零改动 |
| 换模型 Provider | `shoppilot/llm.py`（OpenAI 兼容即可，换 base_url） | 计数 token 的口径同步改 |
| 调整护栏 | `config.py`：max_steps / max_tokens_budget / max_cost_cny / subtask_timeout_s | 改完跑 `benchmark.py --limit 3` 冒烟 |
| 新增自检项 | `reflector.deterministic_checks()` 加一条规则 | 必须是确定性规则；新增题库回归 |
| 加评测题 | `bench_cases.json` + README 示例 + 控制台卡片（三处同步） | 字段对齐 llmeval：expected_tools / expected_behavior |
