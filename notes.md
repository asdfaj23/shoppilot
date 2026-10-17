# 学习笔记 · 八股卡 + 每周检查 + 实验记录卡

> 面试前把"八股卡"手写一遍；每周自查；每次改动记一张实验记录卡。

## 一、八股卡（面试直接背，建议手抄一遍）

1. **Agent 四能力**：规划（拆任务）/ 工具（行动入口）/ 执行（跑完+分支）/ 反馈（看结果定下一步）。
2. **Agent vs Workflow**：谁决定控制流——人写死流程 = Workflow；模型自己决定下一步 = Agent。
3. **ReAct 循环**：思考→行动（tool_calls）→观察（tool 结果喂回）→再思考…直到不再调工具（finish_reason=stop）。
4. **messages 四角色**：system（人设规则）/ user / assistant（可含 tool_calls）/ tool（结果，须带 tool_call_id）。
5. **结构化输出**：JSON mode 在采样层约束词表 + 程序校验兜底 + Reflection 让模型看着报错自改。
6. **上下文窗口与压缩**：长对话 = 窗口限制 + 注意力稀释 + token 成本；解法 = 滑窗（近原文）+ 滚动摘要（远期）。
7. **混合检索**：向量懂语义（改写/同义）但怕专名数字；BM25 反之；RRF 融合 1/(60+rank) 两头吃。
8. **MCP**：Client-Server / JSON-RPC 2.0 / 工具注册（name+desc+schema）/ stdio 或 HTTP 传输；工具与 Agent 解耦。
9. **多 Agent 三件事**：怎么拆（DAG）、怎么路由（按类型 handoff）、怎么聚合（仲裁+引用）。
10. **Harness**：模型之上决定上限——上下文组装、工具预加载、有界重试/超时/降级、评测与观测、权限确认。
11. **评测三指标**：正确率（LLM-as-judge 分档）/ 引用准确率（引用是否指向真出处）/ 拒答率（库外诚实说不会）。
12. **badcase 归因**：检索偏（没召回）→ 改检索；拆解错 → 改 Planner；编造 → 改 prompt/加引用校验。

## 二、每周检查清单（照着自测，全绿才算过关）

### Week1
- [ ] 能口头讲 Agent 四能力 + ReAct 循环（不看书）
- [ ] `python week1/react_loop.py` 本地跑通，能解释每行
- [ ] DeerFlow 三模块流程图能徒手画出（对照 week1/deerflow_architecture.md 第 5 节）
- [ ] 完成 3 个"亲手改一处"并记录

### Week2
- [ ] `python week2/agent_v01.py "分析蓝牙耳机A的差评痛点"` 出带数字报告
- [ ] 能讲清 Planner 两种做法与取舍；DAG 怎么定依赖
- [ ] `--chat` 模式聊 8 轮，亲眼看到 [Memory] 压缩触发并能解释机制

### Week3
- [ ] `mcp_client_demo.py` 走通握手→工具列表→调用，能画 JSON-RPC 时序
- [ ] RAG 答案带 [n] 引用且能溯源到具体文档小节
- [ ] 能白板推 RRF 公式、说清混合检索 > 纯向量的原因

### Week4
- [ ] `multi_agent.py` 现场演示 + 讲清工具预加载省 token 的原理
- [ ] 评测三指标有真实数字；至少 1 个 badcase 修复前后对比
- [ ] Streamlit 演示 + Docker 一键跑通

## 三、实验记录卡（每周一张，手写/OneNote 留底）

| 日期 | 改了什么 | 为什么 | 指标变化 | 结论 |
|---|---|---|---|---|
| 2026-10-17 | 项目初始化，Week1-4 代码就位 | — | baseline 待测 | 先跑通再优化 |
|  |  |  |  |  |

> 记录纪律：一次只改一处；改动前后必须都有数字；说不清动机的改动不做。
