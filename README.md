# ShopPilot · 电商导购智能体

> ⚠️ **免责声明**：本项目为个人学习练习项目。代码、商品池/评论/销量等全部数据、
> 知识库文档与题库均为**自行生成（LLM 合成，结构模仿巴西 Olist 开源数据集）**，
> **仅供参考练习之用**，不代表任何真实企业业务，不可用于商业用途。

## 这是什么

面向消费者的电商导购 Agent。用户输入自然语言需求（"预算500推荐降噪耳机 / A和B怎么选 / 这商品翻车点在哪 / 保修多久"），Agent 结构化抽取约束 → 并行查询商品池、口碑与售后知识库 → 输出带依据编号的推荐清单卡、对比卡、避坑提示，并跨会话记住用户偏好。

**五大导购场景**：选购推荐 / 商品对比 / 口碑解读 / 售后问答 / 超范围拒答。

## 设计思路

一句话：**LLM 负责"理解和表达"，确定性代码负责"计算和校验"。** Agent 系统的价值不是让 LLM 更强，而是用工程护栏把它的不稳定性关进笼子。

### 三部件编排（刻意收敛，不为分层而分层）

```
用户输入
  │
  ├─ 规划 Planner：意图/约束抽取（json_object + Pydantic 校验 + 1次重写）+ 拆子任务 DAG
  │
  ├─ 执行 Executor：子任务按拓扑分层并发；data/review 走有界 ReAct 循环，knowledge 走 RAG
  │     ├─ 有界循环：max_steps / max_tokens / max_cost 三重预算，任一超限即降级
  │     ├─ 并行 tool_calls：模型一次返回多个工具调用 → 线程池并发执行 → 按 tool_call_id 喂回
  │     └─ 退避重试 + 超时降级：网络抖动不打断任务，单工具超时喂回错误让模型换路径
  │
  └─ 成稿 Composer：聚合子结论 → 结构化产出卡 + 强制引用 + 五项确定性自检（不过关修复一次）
```

### 两个记忆（长期/短期，语义不混）

- **短期**：会话滑窗（最近6轮原文）+ 更早的由 LLM 压成滚动摘要
- **长期**：跨会话 Markdown 追加式（关键词命中检索注入，可查看可删）

### 推荐排序：七维加权打分在工具内（非 LLM 算）

价格贴合 13% / 平台评分 16% / 评论均分 10% / 销量热度 8% / 好评匹配 22% / 差评风险 18% / **物流时效 13%**（次日达是电商转化关键信号）。输出带得分构成、好评/差评原文、decision_reason 草稿——LLM 只负责"讲"，不负责"算"。

### 售后 RAG：TF-IDF + BM25 + RRF 融合

向量路懂语义但怕专名数字，BM25 反之；RRF `score = Σ 1/(60+rank)` 两路共识 chunk 排最前。按 Markdown 标题结构感知切分，强制引用溯源——售后答错=客诉，资料不足诚实拒答。

## 创新点（与"调 API"的本质区别）

| 创新点 | 为什么重要 |
|---|---|
| **多轮槽位继承** | 用户说"买耳机→顺带买充电线"自动识别为配套场景，继承上一轮预算与在意维度，不重新推荐耳机 |
| **七维打分可回归** | 排序是数学题，放工具内确定性计算；改权重 benchmark 跑一遍就知道好不好，不让 LLM 每次给出不一致排序 |
| **五项确定性自检** | 引用越界/超预算/商品溯源/误拒答/目标覆盖——纯函数规则零 token，LLM 自评有自我讨好偏差不可靠 |
| **库外实体闸** | 池外型号（如 iPhone）直接拒答，不编造价格——防编造的第一道闸 |
| **换业务只改一层** | domains/ecommerce/ 是数据适配层，上面 6 层一行不改；海事场景接进来只加一个 adapter |

**被否掉的方案**（为什么不上）：MemGPT 式 LLM 自管记忆（丢实体）、Milvus 向量库（小规模没必要）、多智能体>3角色（窄域调试地狱）、自动画像推断（推断错翻车）、32K长上下文替代RAG（不可控）。

完整设计决策（含权衡）见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)；数据/架构/示例/评测详解见 [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md)。

## 快速开始

| 入口 | 操作 |
|---|---|
| 控制台 | 双击 `start.bat` 启动 → 双击 `ShopPilot.html` → 点场景卡片 |
| 直达链接 | 浏览器打开 `http://localhost:8601/?q=预算500以内推荐降噪耳机` |
| 命令行 | `D:\conda_envs\agent\python.exe -m streamlit run app.py` |

环境：Python 3.11（conda env `agent`），`.env` 参考 `.env.example` 配置 `DEEPSEEK_API_KEY / BASE_URL / MODEL`。

## 示例提问（五大场景）

- 🎁 **选购推荐**："预算500以内推荐一副降噪耳机"
- ⚖️ **商品对比**："蓝牙耳机A和蓝牙耳机F怎么选"
- ⚠️ **口碑解读**："蓝牙耳机A翻车点在哪"
- 🛡️ **售后问答**："蓝牙耳机A保修多久"
- 🚫 **超范围拒答**："iPhone 18 现在卖多少钱"

## 效果评测

```bash
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --run --compare
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --baseline
```

同题库纯 API 直调任务完成率 48%（编造价格、该拒未拒）vs Agent 96.3%；27 题拒答正确率 100%。评测明细见 [docs/benchmark_report.md](docs/benchmark_report.md)。

## 目录速览

```
shoppilot/
├── models.py            # Pydantic 数据契约（LLM 脏 JSON 在入口清洗）
├── harness/             # loop/tools/memory/observability
├── agents/               # planner/executor/composer/team
├── domains/ecommerce/    # adapter + 7 导购工具（schema 自动生成 + task_type 预加载）
├── rag/rag_lib.py        # 混合检索 + RAG 问答
└── mcp/server.py         # FastMCP 三原语
app.py                    # Streamlit 前端（多会话/流式/任务卡片）
benchmark.py              # 27 题验收集 + 四项确定性指标
data/ecommerce/           # 商品池/评论/销量/知识库/题库（教学数据，可换真实 API）
```

## MCP 三原语

Tools（7 只读工具）/ Resources（商品池+售后文档）/ Prompts（导购模板），与内部 Agent 共享同一 ToolRegistry。配置方式见 [docs/PROJECT_GUIDE.md §5](docs/PROJECT_GUIDE.md)。
