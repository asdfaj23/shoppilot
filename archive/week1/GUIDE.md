# Week1 GUIDE · 每个文件怎么写的 + 你必须弄懂的点

> 用法：先跑通，再对着本文件逐行读代码，最后做"亲手改一处"任务。
> 理论对应：卡码网《Agent 到底是什么》全文；hello-agents 第一章、第四章 ReAct 节。

## 文件地图

| 文件 | 干什么 | 为什么这么写 |
|---|---|---|
| `react_tools.py` | 工具的"合同"(schema) + 真实实现 | 工具四要素：name / description / parameters(JSON Schema) / 函数体。description 写得越准模型调用越准 |
| `react_loop.py` | 手写 ReAct 主循环（本周核心） | 只用 openai SDK 不用框架，看清 Agent 本质：循环 + 工具分发 + 历史累积 |
| `sdk_agent_demo.py` | Agents SDK 5 行版 | 对照体会：框架 = 把手写 Loop 包起来 + 加重试/tracing |
| `deerflow_architecture.md` | DeerFlow 三模块流程图 | Week1 产出②，要求能徒手画出 |

## react_loop.py 逐段拆解（说不清的行当场查）

### ① messages 四种角色（消息结构）
```python
messages = [
    {"role": "system", "content": SYSTEM_PROMPT},  # 人设与规则，模型最听 system 的话
    {"role": "user",   "content": goal},           # 用户目标
]
```
循环中动态追加：`assistant`（模型的回复，含 tool_calls）→ `tool`（工具结果）→ `assistant`…。
**messages 就是 Agent 的记忆**：每轮全量喂回，模型才知道自己做过什么。

### ② tool_calls / finish_reason（Agent 本质，面试必考）
- 模型想调工具时：返回 `message.tool_calls = [...]`，此时 `finish_reason="tool_calls"`，**content 可能为空**；
- 模型认为信息够了：`tool_calls=None`，`finish_reason="stop"`，content 即最终答案；
- 所以循环的退出条件就一句：`if not msg.tool_calls`。

### ③ 工具结果怎么喂回（最容易写错的地方）
```python
messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
```
- `tool_call_id` 必须等于对应 `tool_calls[i].id` —— 模型靠 id 把"我要的结果"和"回来的结果"对上，对不上就报错或乱套；
- 一次可能返回多个 tool_calls（并行工具调用），必须**全部**执行并逐条喂回；
- 模型的 assistant 消息（含 tool_calls）必须先原样入历史，再喂 tool 结果，顺序不能反。

### ④ 为什么要 max_steps
模型可能死循环调工具。有界循环 = 最基础的 Agent 护栏（后面 Week4 还有重试/超时/降级）。

### ⑤ temperature=0.3 为什么
分析类任务要稳定、可复现：温度低 → 采样分布更尖 → 输出更确定。写创意文案才调高。

## 「必须弄懂的 5 个点」（每点一句话，白板能讲）

1. **Agent 四能力**：规划（拆任务）、工具（行动入口）、执行（跑完分支处理）、反馈（看结果定下一步）。
2. **Agent vs Workflow**：Workflow 是人写死的流程图；Agent 是模型自己决定下一步——判据就是"谁能决定控制流"。
3. **Agent Loop 本质**：`发消息 → 看有无 tool_calls → 执行并喂回 → 重复`，直到模型不再要工具。
4. **结构化输出**：`response_format=json_object` 把采样约束在合法 JSON 词表内，加上我们自己的字段校验兜底。
5. **Token 成本**：每轮全量历史重发，成本随轮数平方级增长——这就是 Week2 上下文压缩的动机。

## 亲手改一处（改完记进 notes.md 实验记录卡）

1. 【改 prompt】把 SYSTEM_PROMPT 的输出结构改成"结论/依据/风险"重跑，观察答案结构变化；
2. 【加工具】在 react_tools.py 加第 3 个工具 `query_product_info`（查商品参数），schema+函数+TOOL_MAP 三处都要动；
3. 【降成本】把 SYSTEM_PROMPT 和工具 description 各删 20 个字重跑，对比 tokens 变化（体会 prompt 精简）。

## 面试追问预案

- **"模型怎么知道该调哪个工具？"** → 工具 name+description+参数 schema 进 system 层，模型按语义匹配；description 质量决定调用准确率。
- **"并行 tool_calls 怎么处理？"** → 遍历全部执行，逐条用各自 tool_call_id 喂回。
- **"为什么不用 LangChain？"** → 手写一遍才知道框架在做什么；框架是工程加速器不是认知替代品。
