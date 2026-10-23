# ShopPilot · 电商导购 Agent（Verifiable-by-Design）

> **一句话**：在可验证的子问题上，用**零 token 的确定性程序**替代 LLM 的主观判断与自我反思，把"输出是否可信"从模型运气变成可回归的工程断言。
>
> 免责声明：这是一个小型研究/学习项目。商品池、评论、销量、知识库与题库均为**合成数据**（结构参考巴西 Olist 开源数据集），仅用于演示与评测方法论，不代表任何真实企业业务，不可商用。

---

## 为什么有这个项目

LLM Agent 会犯错：引用编号凭空编造、推荐超预算商品、把不在商品池的型号编出参数、该拒答时硬答。常见的应对是"让 LLM 自己反思一遍"（Reflexion 范式），但它**要多花一轮 token、且不可复现**——同一个输出今天被放行、明天被打回。

本项目的主张是：**只要某个错误可以被写成确定性规则，就不该再花 token 让 LLM 去"感觉"它对不对。** 因此在成稿后挂了一组**可插拔、零 token 的输出护栏（Output Guardrails）**，违规时只修复一次、再复核一次，不无限循环。

配套的对照实验（见 [`eval/`](eval/)）在同一 ReAct 内核、同一题库、同一模型 temperature=0 下，只切换"最终输出怎么被检查"：

| 策略 | 检查方式 | 额外 token | 预期违规率 |
|---|---|---|---|
| `vanilla` | 不做任何输出检查 | 0 | 最高 |
| `llm_reflection` | LLM 按 checklist 自反思（Reflexion） | 高 | 中 |
| `multi_agent` | 独立 Critic Agent 打回重改（AutoGen/MetaGPT） | 最高 | 中 |
| **`deterministic`（本项目）** | 6 条确定性规则 + 一次有界修复 | **≈0** | **≈0** |

> 真实结论需配置 API key 后跑 `shoppilot eval --all`；`python examples/01_quickstart.py` 可以在**不打任何 LLM** 的情况下，用脚本化替身看到这套护栏如何拦截并修复一条"越界引用"。

## 核心创新点

| 机制 | 类型 | 说明 |
|---|---|---|
| **确定性输出护栏** | 推理时 | 引用越界 / 超预算 / 库外实体（编造）/ 误拒答 / 目标覆盖 / 缺结构化卡——6 条纯函数规则，零 token，违反即修复一次并复核 |
| **可插拔护栏集** | 架构 | `OutputGuardrail` Protocol：换域只换规则集合，编排层不感知；LLM 反思臂/独立 Critic 臂作为对照实验 baseline |
| **有界 ReAct + 三重预算** | 推理时 | 最多 N 步 / token 预算 / 成本预算，任一超限即降级返回，不无限烧钱 |
| **库外实体闸** | 推理时 | 点名商品池外的型号直接拒答，不编造价格参数 |
| **七维加权打分在工具内** | 工具 | 排序是数学题：价格贴合/评分/口碑/销量/好评匹配/差评风险/物流时效，确定性计算、可回归，不让 LLM 给出不一致排序 |
| **混合检索 RAG** | 检索 | TF-IDF（语义）+ BM25（专名/数字）+ RRF 融合，售后答错=客诉，资料不足诚实拒答 |
| **分层测试 + 离线复现** | 工程 | 护栏规则/工具/编排全有 pytest；`ScriptedLLM` 让整条链路可离线、零 token 复现 |

## 架构

刻意收敛为三个部件，不为分层而分层（不引入 LangGraph / AutoGen / CrewAI / 向量数据库）：

```
用户自由文本
  └─ Planner    意图/实体/预算/在意维度 → 结构化需求 → 子任务 DAG
  └─ Executor    同层无依赖并行；data/review 走有界 ReAct，knowledge 走 RAG
  └─ Composer    聚合成稿 → 结构化卡 → 强制引用 → 确定性护栏（违规则修复一次）
```

- **域可插拔**：`core / orchestrator` 不 import 任何具体业务；电商域只贡献 `CsvEcommerceAdapter + 工具 + 域护栏 + factory`。换真实数据源 = 换一个 Adapter 实现。
- **可观测**：每步耗时/token 落 JSONL trace，前端/评测同一份数据。

## 快速开始

```bash
# 1) 安装（editable，改代码即时生效）
pip install -e ".[dev,serve]"

# 2) 离线冒烟：不打任何 LLM，用脚本化替身完整跑一遍并展示护栏拦截/修复
python examples/01_quickstart.py

# 3) 接真实模型：复制 .env.example 为 .env，填 SHOPPILOT_API_KEY / BASE_URL / MODEL
cp .env.example .env
shoppilot ask "500元以内推荐一副降噪耳机"
shoppilot repl                     # 多轮
shoppilot serve --port 8000        # FastAPI：POST /api/ask

# 4) 跑对照实验（真实 token）/ 离线不跑全量
shoppilot eval --all --limit 5
python -m eval.run --dry-run       # 仅离线验证管线

# 5) 测试与静态检查
pytest -q
ruff check src tests eval
```

## 数据规模（合成，可整体替换）

| 数据集 | 规模 | 内容 |
|---|---|---|
| 商品池 | 40 SKU / 12 品类 | 价格、评分、月销、保修、卖点 |
| 评论 | 138 条（63 条差评） | 21 种差评维度，含典型 badcase |
| 销量 | 39 条 | 品类内排名 |
| 知识库 | 6 篇 Markdown | 手册/退换货/物流FAQ/选购指南 |
| 验收集 | 27 题 | 推荐/对比/口碑/售后/拒答 |

## 目录结构

```
src/shoppilot/
├── core/            # LLM Provider、ToolRegistry、ReAct 循环、类型、记忆、trace
│   └── guardrails/  # ★ OutputGuardrail / Runner / 内置规则 + LLM反思臂(对照)
├── orchestrator/    # planner / executor / composer / ShoppingAgent
├── domains/ecommerce/# CSV Adapter + 7 工具 + 域护栏 + factory（组合根）
├── serving/         # FastAPI + schemas
├── mcp/            # FastMCP 三原语（Tools/Resources/Prompts）
└── cli.py          # shoppilot ask | repl | serve | eval
eval/               # 四臂对照实验（strategies/metrics/run）
tests/              # 离线单测 + e2e（ScriptedLLM，零 token）
examples/01_quickstart.py
```

## 为什么不用这些常见方案

- **重型多智能体框架（AutoGen/CrewAI/LangGraph）**：窄域导购任务三部件足够；多一个角色就多一层错误累积。
- **Milvus/向量库**：记忆条目几十条量级，关键词命中够用；上向量库是过度工程且用户不可读。
- **MemGPT 式 LLM 自管记忆**：会漏掉关键实体；本项目用显式槽位 + 滑窗，只记用户明确表达过的。
- **让 LLM 自评代替确定性校验**：有"自我讨好偏差"，且每次结果不可复现——这正是本项目要消除的对象。

## 许可

MIT（见 [LICENSE](LICENSE)）。
