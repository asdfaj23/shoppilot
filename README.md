# ShopPilot · 电商导购智能体

> ⚠️ **免责声明**：本项目为个人学习练习项目。代码、商品池/评论/销量等全部数据、
> 知识库文档与题库均为**自行生成（LLM 合成，结构模仿巴西 Olist 开源数据集）**，
> **仅供参考练习之用**，不代表任何真实企业业务，不可用于商业用途。

> 把已验证的 week1-4 教学代码合并重构的正式项目：用户输入"预算 500 内推荐降噪耳机 /
> A 和 B 怎么选 / 这商品翻车点在哪 / 保修多久"，Agent 结构化抽取需求 → 并行查商品池/口碑/售后
> 知识库 → 输出带依据的推荐清单、对比卡、避坑提示，并跨会话记住用户偏好。

## 文档导读（按需取用）

| 你想… | 看这个 |
| --- | --- |
| **一份文档理解整个项目**（数据/架构/示例/评测/面试） | [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md) ⭐ |
| 快速跑起来 / 知道怎么用 | 本页下面的「快速开始」「示例提问」 |
| 看评测明细（脚本自动生成） | [docs/benchmark_report.md](docs/benchmark_report.md) |
| 准备面试 / 深挖质疑（富文本版） | [docs/design_review.html](docs/design_review.html) |
| 知道改代码的规矩 | [AGENTS.md](AGENTS.md) |

## 快速开始（三种入口）

| 入口 | 操作 | 适合 |
| --- | --- | --- |
| ① 控制台 | 双击 `start.bat` 启动服务 → 双击 `ShopPilot.html` → 点场景卡片或"开始对话" | 日常使用（卡片点击即自动提问） |
| ② 直达链接 | 浏览器打开 `http://localhost:8601/?q=预算500以内推荐降噪耳机`（`?q=` 可带任意问题） | 分享/书签 |
| ③ 命令行 | `D:\conda_envs\agent\python.exe -m streamlit run app.py` | 开发调试 |

环境：Python 3.11（conda env `agent`），依赖见 `requirements.txt`（均已安装）；
`.env` 参考 `.env.example` 配置 `DEEPSEEK_API_KEY / BASE_URL / MODEL`。

## 示例提问（对应五大导购场景）

> 与控制台（`ShopPilot.html`）四张场景卡片一一对应；链接在服务启动后可直接点击。

### 🎁 选购推荐
约束筛选 → 推荐清单卡（价格/推荐理由/口碑摘要/依据编号）
- "预算500以内推荐一副降噪耳机" → [直达](http://localhost:8601/?q=预算500以内推荐一副降噪耳机)
- "我想给新家组网，预算300左右，有什么路由器推荐吗" → [直达](http://localhost:8601/?q=我想给新家组网，预算300左右，有什么路由器推荐吗)
- "500预算买个充电宝，主要出差用，在意安全"
- "给我推荐个蓝牙耳机，要音质好的，贵一点没关系"

### ⚖️ 商品对比
维度对比表 + 分场景结论
- "蓝牙耳机A和蓝牙耳机F怎么选" → [直达](http://localhost:8601/?q=蓝牙耳机A和蓝牙耳机F怎么选)
- "机械键盘G和显示器H哪个更值得买"
- "同价位下充电宝B和数据线E哪个口碑更好"
- "充电宝B和蓝牙耳机A哪个卖得更好"

### ⚠️ 口碑解读
差评维度归因 + 避坑清单
- "蓝牙耳机A翻车点在哪" → [直达](http://localhost:8601/?q=蓝牙耳机A翻车点在哪)
- "最近智能手表D的差评多吗，主要是哪些问题"
- "有没有人反馈充电宝鼓包的？具体说了什么"
- "想买机械键盘G，但在办公室用怕吵，有什么要注意的"

### 🛡️ 售后问答
RAG 强制引用回答，来源可展开溯源
- "蓝牙耳机A保修多久" → [直达](http://localhost:8601/?q=蓝牙耳机A保修多久)
- "七天无理由退货有什么条件？运费谁出"
- "蓝牙耳机A充不进电怎么办"
- "显示器H买了半个月左下角有个亮点，能退吗"

### 🧭 深度研究（多源取证调研报告，可下载）
- "深度研究一下：3000元以内拍照好的手机怎么选" → 候选对比 + 证据链 + 风险 + 建议
- "研究一下机械键盘G值不值得买，说详细点" → 打分 + 口碑归因 + 选购指南 + 结论

### 🚫 超范围拒答（诚实说明，不编造）
- "iPhone 18 现在卖多少钱，值得入手吗"
- "蓝牙耳机Z多少钱，比蓝牙耳机A好吗"（库外型号）
- "周末去哪里爬山比较好"（无关请求）

## 效果评测

```bash
# 27 题验收集 + 四项确定性指标；--compare 加工具预加载 token 对照；--baseline 加纯 API 基线对照
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --run --compare
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --baseline
# 或双击 benchmark.bat（含全部对照）
```

## 设计原则（论文级表达）

> 一句话：**LLM 负责"理解和表达"，确定性代码负责"计算和校验"。** Agent 系统的价值不是让 LLM 更强，而是用工程护栏把它的不稳定性关进笼子。

| 原则 | 落地 |
|---|---|
| **确定性优先** | 能写死的绝不交给 LLM：七维打分在工具内、Reflection 全是纯函数规则（零 token、可回归） |
| **契约兜底** | LLM 输出永远是脏的，在 `models.py` 入口用 Pydantic `field_validator` 清洗一次 |
| **单一事实源** | 工具 schema 由 Pydantic args 自动派生 OpenAI schema，不手写、不漂移 |
| **失败降级** | 三重预算（步数/token/成本）+ 超时降级 + 子任务独立失败，不让整体炸掉 |
| **可观测可复盘** | JSONL trace + 分步 token 归因，badcase 可定位到具体某一步 |

**被否掉的方案**（为什么不上）：MemGPT 式 LLM 自管记忆（丢实体）、Milvus 向量库（小规模没必要）、多智能体>3角色（窄域调试地狱）、自动画像推断（推断错翻车）、32K长上下文替代RAG（不可控）。

完整设计决策（含权衡）见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) ⭐

## 架构（8 层，单向依赖）

```
①config ②llm ──► ③models(Pydantic 契约) ──► ④harness/loop ⑤tools ⑥memory/observability
                                        └──► domains/ecommerce(6工具) + rag(FAISS+BM25+RRF)
⑦agents(planner→dispatcher→aggregator→reflector→team) ──► ⑧app.py / benchmark.py / mcp
```

- **范式组合**（参考 kamacoder Agent 专栏）：先规划（Planner 拆 DAG）→ 中间 ReAct
  （有界循环：三重预算/并行 tool_calls/重试退避/超时降级）→ 交付前 Reflection
  （五项确定性自检 + 修复）；计划是**可更新的任务清单**（子任务降级触发 Replan）
- **推荐排序（企业落地核心）**：七维加权打分放在工具内（非 LLM 算）——
  价格贴合 13% / 平台评分 16% / 评论均分 10% / 销量热度 8% / 好评匹配 22% /
  差评风险 18% / **物流时效 13%**（次日达是电商转化关键信号）；输出带得分构成、
  好评/差评原文、decision_reason 草稿，LLM 只负责"讲"不负责"算"
- **多轮会话状态**：三层记忆（短期滑窗+滚动摘要 / Planner 每轮重抽槽位 / 长期 Markdown
  追加式）；Planner prompt 显式规定"顺带买配件/配套关系"时继承上一轮预算与在意维度
  （如"买了蓝牙耳机A → 顺便买充电线"自动识别为配套场景）
- **知识库 RAG（无 GPU）**：TF-IDF char n-gram 向量 + BM25 双路召回 + RRF 融合，
  按 Markdown 标题切 chunk；换 bge-small-zh 只需实现 encode() 接口；强制引用溯源，
  资料不足诚实拒答
- **MCP 三原语**：Tools（7 只读工具）/ Resources（商品池+售后文档）/ Prompts（导购模板），
  与内部 Agent 共享同一 ToolRegistry

详细设计决策（含被否掉的方案）见 [docs/DESIGN.md](docs/DESIGN.md)；
面试应对与简历文案见 [docs/design_review.html](docs/design_review.html)；
效果数据见 [docs/benchmark_report.md](docs/benchmark_report.md)。

## 目录速览

```
shoppilot/
├── models.py            # ③ Pydantic 数据契约（LLM 脏 JSON 在入口清洗）
├── harness/             # ④⑤⑥ loop/tools/memory/observability
├── agents/              # ⑦ planner/dispatcher/aggregator/reflector/team
├── domains/ecommerce/   # adapter + 6 导购工具（schema 自动生成 + task_type 预加载）
├── rag/rag_lib.py       # 混合检索 + RAG 问答（kb_dir 参数化）
└── mcp/server.py        # FastMCP 三原语
app.py                   # ⑧ Streamlit 暗色前端（多会话/流式/任务卡片/画像面板）
benchmark.py             # 25 题验收集 + 四项确定性指标 + token 对照
data/ecommerce/          # 商品池/评论/销量/知识库/题库（教学数据，可换真实 API）
```

## 面试一句话

"ReAct 适合路径不确定的任务但必须配预算与停止条件；Reflection 不是再想想而是按明确标准检查；
规划是可更新的任务清单。我的项目三段组合落地，效果由 25 题确定性指标回归保障。"
