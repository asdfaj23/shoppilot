# 电商导购与评论分析多智能体系统（DeerFlow 核心范式轻量复现）

> 求职项目：复现字节 DeerFlow 核心编排范式（Planner 任务分解 → Dispatcher 调度 → Aggregator 聚合），
> 套电商业务场景，逐步升级为「Agent = Model + Harness」框架。
> 模型：DeepSeek-V4.1-Flash（纯 API，无 GPU，笔记本可跑）。

## 快速开始

```bash
# 1) 环境（已建好可跳过）
conda create -n agent python=3.11 -y
conda activate agent
pip install -r requirements.txt

# 2) 配置密钥：编辑根目录 .env（参考 .env.example）
#    DEEPSEEK_API_KEY=sk-xxx

# 3) 按周跑
python week1/react_loop.py                    # Week1: 手写 ReAct Loop
python week1/sdk_agent_demo.py                # Week1: Agents SDK 5 行版
python week2/agent_v01.py "分析蓝牙耳机A的差评痛点并给出改进建议"   # Week2: Planner+工具+汇总
python week2/agent_v01.py --chat              # Week2: 多轮对话（记忆压缩演示）
python week3/mcp_client_demo.py               # Week3: MCP Server/Client 全流程
python week3/rag/build_index.py               # Week3: 建 RAG 索引
python week3/rag/query.py "耳机保修多久"       # Week3: 带引用 RAG 问答
python week4/multi_agent.py "蓝牙耳机A值得买吗？差评多不多？保修政策是什么？"  # Week4: 多 Agent
python week4/eval.py --gen && python week4/eval.py --run   # Week4: 评测闭环
python -m streamlit run week4/app.py          # Week4: 浏览器演示
```

## 目录结构

```
├── config.py            # 统一配置（.env → DeepSeek 兼容 OpenAI 客户端）
├── .env / .env.example  # 密钥（.env 已 gitignore，绝不提交）
├── data/                # 数据层（reviews/sales CSV + knowledge/ 知识库 + 评测集）
├── week1/               # 手写 ReAct Loop + Agents SDK + DeerFlow 架构笔记
├── week2/               # Planner(DAG) + 记忆压缩 + 单 Agent 0.1
├── week3/               # MCP Server/Client + FAISS+BM25 混合检索 RAG
├── week4/               # 多 Agent + Harness 护栏 + Trace + 评测 + Streamlit + Docker
├── notes.md             # 八股卡 + 每周检查清单 + 实验记录卡
└── 各周 GUIDE.md        # 每个文件怎么写的、必须弄懂的点、亲手改任务、面试预案
```

## 技术栈与卖点（简历口径，数字以评测实测为准）

- 复现 DeerFlow 核心编排范式：Planner 结构化 DAG / Dispatcher 按任务类型路由 /
  Aggregator 冲突仲裁+强制引用；
- **基于任务类型的工具预加载**：无关工具排除出上下文（Token 优化点，用 week4/traces 实测填数）；
- 混合检索 RAG：结构感知切分 + FAISS 向量 + BM25 + RRF 融合 + 答案强制引用溯源；
- 分层记忆：滑窗 + LLM 滚动摘要压缩；Harness 护栏：有界重试/超时降级/结果缓存；
- MCP Server（stdio/JSON-RPC）封装业务工具，可被任意 MCP 宿主挂载；
- LLM 生成评测集 + 三指标（正确率/引用准确率/拒答率）+ badcase 归因闭环；
- 全链路 Trace（JSONL，接口对齐 Langfuse）+ Streamlit 演示 + Docker 一键部署。

## 安全须知

- `.env` 含 API Key，已在 `.gitignore`；**开仓库前确认没被 track**：`git status` 不应出现 .env；
- key 已在别处泄露过的话去 DeepSeek 后台重置。

## 后续路线

- 用 Kaggle Olist 真实数据替换 data/（见 data/README.md）
- 接 Langfuse 云端 Trace；评测集扩到 80 case；简历数字全部实测填充
