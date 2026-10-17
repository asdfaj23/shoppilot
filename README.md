# ShopPilot · 电商导购智能体

> 把已验证的 week1-4 教学代码合并重构的正式项目：用户输入"预算 500 内推荐降噪耳机 /
> A 和 B 怎么选 / 这商品翻车点在哪 / 保修多久"，Agent 结构化抽取需求 → 并行查商品池/口碑/售后
> 知识库 → 输出带依据的推荐清单、对比卡、避坑提示，并跨会话记住用户偏好。

## 快速开始

```bash
# 环境：Python 3.11（conda env: agent），依赖见 requirements.txt（均已安装）
# 1. 配置 .env（参考 .env.example）：DEEPSEEK_API_KEY / BASE_URL / MODEL
# 2. 启动网页版前端
D:\conda_envs\agent\python.exe -m streamlit run app.py

# 3. 跑效果评测（25 题验收集 + 四项确定性指标；--compare 加 token 对照实验）
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --run --compare
```

## 五大导购场景

| 场景 | 示例输入 | 产出 |
| --- | --- | --- |
| 选购推荐 | "预算500以内推荐降噪耳机" | 推荐清单卡（价格/理由/口碑摘要/依据编号） |
| 商品对比 | "蓝牙耳机A和F怎么选" | 对比维度表格 + 分场景结论 |
| 口碑解读 | "蓝牙耳机A翻车点在哪" | 差评维度归因 + 避坑清单 |
| 售后问答 | "蓝牙耳机A保修多久" | RAG 强制引用回答（来源可展开） |
| 超范围拒答 | "iPhone 18 多少钱" | 诚实说明商品池外，不编造 |

## 架构（8 层，单向依赖）

```
①config ②llm ──► ③models(Pydantic 契约) ──► ④harness/loop ⑤tools ⑥memory/observability
                                        └──► domains/ecommerce(6工具) + rag(FAISS+BM25+RRF)
⑦agents(planner→dispatcher→aggregator→reflector→team) ──► ⑧app.py / benchmark.py / mcp
```

- **范式组合**（参考 kamacoder Agent 专栏）：先规划（Planner 拆 DAG）→ 中间 ReAct
  （有界循环：三重预算/并行 tool_calls/重试退避/超时降级）→ 交付前 Reflection
  （五项确定性自检 + 修复）；计划是**可更新的任务清单**（子任务降级触发 Replan）
- **记忆四分类**：短期（滑窗+滚动摘要）/ Session State（结构化任务状态）/ 长期
  （用户画像，确定性写入+遗忘机制+前端可编辑）/ 外部知识库（RAG，与记忆语义分开）
- **MCP 三原语**：Tools（6 只读工具）/ Resources（商品池+售后文档）/ Prompts（导购模板），
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
