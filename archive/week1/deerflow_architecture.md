# DeerFlow 三模块架构（Week1 Day4 产出 · 读源码画图的底稿）

> 目标：能**徒手**画出下面这张流程图，并说清每个模块"做什么、输入输出是什么"。
> DeerFlow = 字节开源 Deep Research Agent（github.com/bytedance/deer-flow），
> Python(LangGraph) 后端 + Next.js 前端。本笔记基于其公开架构整理，
> 具体文件名随版本变动，clone 后按"职责"对号入座，别死记文件路径。

## 1. 一图看懂（文字版流程图，先背这张）

```
用户问题
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│ Coordinator（协调者）                                     │
│ 入口/监督节点：判断模式（自动 or 人工确认），控制全局流转     │
└───────────────┬─────────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────────┐
│ Planner（规划器）= 任务分解                                │
│ 输入：研究主题    输出：结构化调研大纲（JSON：多个 section， │
│ 每个 section 有描述与要求）                                │
│ ★ human-in-the-loop：暂停等用户确认/修改大纲               │
└───────────────┬─────────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────────┐
│ Research Team（研究团队）= 调度执行（Dispatcher）           │
│ 按大纲 section 逐个分派：                                  │
│   Researcher Agent：ReAct + 工具（Tavily 搜索 / Jina 抓网页）│
│   Coder Agent：需要计算/数据时写代码跑 Python 沙箱          │
│   （工具结果与引用随状态一起回传）                          │
└───────────────┬─────────────────────────────────────────┘
                ▼
┌─────────────────────────────────────────────────────────┐
│ Reporter（报告器）= 结果聚合（Aggregator）                  │
│ 汇总各 section 研究素材 → 交叉验证 → 生成带引用的 Markdown 报告│
└───────────────┬─────────────────────────────────────────┘
                ▼
             最终报告（带引用溯源）
```

## 2. 三模块对照表（DeerFlow 原组件 ↔ 我们项目的复现）

| DeerFlow 组件 | 职责 | 关键机制 | 我们项目里的对应 |
|---|---|---|---|
| **Planner** | 任务分解 | LLM 输出结构化大纲 JSON + 人工确认（interrupt） | `week2/planner.py`：goal → 子任务 DAG JSON + 校验 + Reflection 重试 |
| **Coordinator + Research Team** | 调度执行 | LangGraph 条件边/并行分派；子 Agent 用 ReAct 循环 + 工具 | `week4/multi_agent.py` 的 `dispatch()`：按 task_type 路由三个子 Agent + 工具预加载 |
| **Reporter** | 结果聚合 | 汇总所有 section 素材，强制带引用输出报告 | `week4/multi_agent.py` 的 Aggregator + `week3/rag/query.py` 引用溯源 |

## 3. 它用了 LangGraph 的什么能力（为什么是 LangGraph 而不是 LangChain）

| LangGraph 能力 | DeerFlow 用它干什么 |
|---|---|
| StateGraph（节点+边） | 把 Coordinator/Planner/Researcher/Reporter 建成图节点，状态显式流转 |
| 自定义 State schema | research state：主题/大纲/各 section 结论/引用，随图流动 |
| 条件边 + Send | Planner 出大纲后动态分派多个研究任务（可并行） |
| interrupt / checkpointer | human-in-the-loop：大纲确认点暂停；断点可恢复 |

## 4. 复现这套范式，最小要抄走的 3 个设计（面试可直接答）

1. **先出计划再执行**：Planner 产出结构化 JSON 计划（可审阅、可校验、可缓存），
   而不是让模型自由发挥到底 —— 可控性、可评测性的来源。
2. **按任务类型路由 + 子 Agent 职责单一**：每个子 Agent 只配自己需要的工具
   （我们落地成"工具预加载"，这正是 Token 优化 35% 的那个点）。
3. **聚合层强制引用溯源**：最终报告的每个关键结论都能回溯到具体数据/文档，
   杜绝"编数据"，这是"研究型 Agent"和"聊天机器人"的分界线。

## 5. 徒手画图自测（本周检查用）

不看上文，白纸画出：Coordinator→Planner→Research Team→Reporter 四个框 +
每个框的输入/输出 + 标出 human-in-the-loop 暂停点 + 标出三处我们项目对应文件。
画得出来 = 本周 DeerFlow 部分过关。
