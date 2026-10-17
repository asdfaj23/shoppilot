# ShopPilot · DESIGN.md（随阶段更新的设计决策记录）

> 状态：第 7 存档点。本文不是接口文档，是"为什么这么设计"的决策记录——每个取舍都写清
> 被否掉的方案是什么，供面试深挖与复盘。

---

## 0. 选题与差异化

**面向消费者的电商导购 Agent**：输入"预算 500 内推荐降噪耳机 / A 和 B 怎么选 / 这商品翻车点在哪 /
保修多久"，结构化抽取需求 → 并行查商品池/口碑/售后知识库 → 输出带依据的推荐清单、对比卡、
避坑提示，并跨会话记住用户偏好。

差异化三条（对照 DeerFlow / hello-agents 复现品）：
1. **业务深度**：不是"跑通框架的 demo"，而是导购五大场景（推荐/对比/口碑/售后/拒答）各有
   专属工具链与结构化产出卡；
2. **可解释记忆**：用户画像（预算档/在意维度/品类）文件态持久化、前端可查看可编辑、
   每轮回答告诉用户"本轮记住了什么"；
3. **全手写 8 层 Harness**：不引 LangChain/LangGraph——依赖越少，每层越讲得清楚。

---

## 1. 三种 Agent 范式的组合（参考 kamacoder《ReAct、Reflection、规划执行》）

文章结论：三种思路不互斥，真实项目组合使用——**先规划，中间 ReAct，交付前 Reflection**，
且"规划不是机械执行的死计划，而是可更新的任务清单"。ShopPilot 的落点：

| 文章观点 | ShopPilot 实现 | 代码位置 |
|---|---|---|
| 规划执行：复杂任务先拆解，避免跑偏 | Planner 一次调用同时产出 ShoppingQuery（意图/预算/在意维度）+ 子任务 DAG | `agents/planner.py` |
| 计划要可更新，不死守原计划 | **Replan**：子任务降级后带失败证据重拆补救任务（换路径：放宽筛选/换关键词/直接综合收尾），最多一轮防循环 | `planner.replan()` + `team.py` |
| ReAct 适合路径不确定的任务，但必须配步数限制/工具权限/状态记录/停止条件 | 三重预算（steps/tokens/cost）+ 危险工具确认闸 + tool_trace 全记录 + 有界循环 | `harness/loop.py` + `harness/tools.py` |
| ReAct 会"跑散"：忘了目标/重复调工具 | 子任务粒度拆小（单 goal 单任务）+ 同 goal 结果缓存 + 工具按 task_type 预加载（无关工具不进上下文，既防误调又省 token） | `dispatcher.py` |
| Reflection 不是"再想想"，是按**明确标准**检查 | 自检 ①-⑤ 全是确定性规则（零裁判 token），LLM 只负责修复 | `agents/reflector.py` |
| Reflection 拦"过早交付" | ⑤ 目标覆盖：用户点名的商品必须出现在回答、recommend/compare/review_analysis 必须产出结构化卡 | `reflector.deterministic_checks()` |

**为什么自检用规则不用 LLM 自评**：LLM 自评有自我一致性偏差（"自我讨好"）；引用编号越界、
价格超预算、库外商品编造、误拒答这类错误用规则判 100% 可靠且零成本。

## 2. 记忆架构（参考 kamacoder《Agent 的记忆》四分类）

文章核心论点：**记忆不是把历史全塞进 Prompt，而是分四类各司其职、写入要过筛、检索按需、
还要会遗忘**。ShopPilot 四层映射：

| 记忆类型 | ShopPilot 实现 | 关键设计 |
|---|---|---|
| ① 短期记忆 | `WorkingMemory`：滑窗 + 滚动摘要 | "摘要压缩而非全文保留"：最近 6 条保原文，更早的压成 running summary，丢噪音留目标/约束/证据 |
| ② Session State | `MemoryManager.state`：结构化任务状态 | **不是聊天记录**：goal/意图/任务清单(含降级标记)/关键结论 的 JSON，下一轮注入 Planner 消解指代（"那它保修多久"的"它"） |
| ③ 长期记忆 | `PreferenceMemory`：画像 JSON 文件 | 写入像数据库：只写 ShoppingQuery **确定性抽取**的用户明确表达（不靠模型猜）；冲突=覆盖更新；history≤20 条、priorities≤8（噪音清理）；`forget()` 删除入口；条目带 `updated_at`（过期可判） |
| ④ 外部知识库 | `shoppilot/rag`：FAISS+BM25+RRF | 与记忆语义分开：RAG 是"可查的资料"，不是"对用户的记忆" |

写入过筛四问的落法：是否长期稳定（预算档/在意维度 vs 临时闲聊——后者不写）→ 是否有用
（画像字段固定三类，拒绝自由文本堆砌）→ 是否被确认（全部来自用户原话抽取，来源=用户明确表达）
→ 隐私（本地文件态、前端可编辑可删）。**检索按需**：render() 只注入非空字段的画像块，
不全量塞历史。

## 3. MCP（参考 kamacoder《MCP 协议》三原语）

文章核心：MCP 是**应用侧接入协议**（不是 Function Calling 的替代品——FC 是模型侧调用机制）；
Server 按 **Tools/Resources/Prompts** 三原语暴露能力；安全边界在 Host（权限决策、确认、审计）。

| 三原语 | ShopPilot 实现 |
|---|---|
| Tools（能做什么） | 6 个只读导购工具，共享 `ToolRegistry`（单一事实源：内部 Agent 与外部 MCP 宿主调同一份实现+数据） |
| Resources（能看什么） | `shoppilot://products` 商品池清单、`shoppilot://knowledge/{doc}` 售后文档——宿主直接读，避免全塞进 Prompt |
| Prompts（怎么做） | `shopping_guide` 模板：先筛商品→再查口碑→后查售后的规范化顺序 |

安全落法：工具全只读（无下单/删除类高危动作）；文件资源限定知识库目录（防目录穿越）；
参数 Pydantic 校验；`ToolRegistry` 的危险工具确认闸是 Host 侧权限决策的挂载点。
**没有做**远程传输（Streamable HTTP）——数据全在本地，stdio 足够，远程鉴权是过度设计（边界声明）。

## 4. 8 层分层与单向依赖

```
models.py（③契约）← 一切模块可 import 它，它不 import 任何人
config.py（①配置）/ llm.py（②Provider）
harness/{loop,tools,memory,observability}（④⑤⑥）
domains/ecommerce（adapter→tools） + rag/rag_lib
agents/{base,planner,dispatcher,aggregator,reflector,team}（⑦编排）
app.py（⑧前端）/ benchmark.py / mcp/server.py（三个平级消费方）
```

被否掉的方案：
- *agents 直接 import pandas 查数据* ——数据换源（本地 CSV→真实 API）会牵连所有 Agent；
  现在只改 `adapter.py`，6 工具签名不变（week2 "业务换了工具接口不变"的落地）；
- *Aggregator 输出自由 markdown* ——前端无法渲染卡片；改为输出结构化 JSON 卡
  （Recommendation/CompareCard/CaveatList），Pydantic 校验清洗后交给渲染器；
- *LLM 输出后逐处手写清洗* ——清洗收敛到 Pydantic `field_validator`（数据入口修一次）。

## 5. 关键取舍速查

| 决策 | 理由 | 代价 |
|---|---|---|
| Plan-and-Solve 拆 DAG 而非纯 ReAct | token 省、计划可审、同层可并行 | 执行中信息不足需 Replan 补（已实现） |
| 同层 ThreadPoolExecutor 并发 | 推荐/口碑两路查询时延近半 | 事件流交错，前端需按 task_id 归位 |
| 工具按 task_type 预加载 | 无关 schema 不进上下文（省 token，benchmark --compare 量化） | task_type 划分要维护（Literal 枚举约束） |
| 拒答短路（out_of_scope 不拆任务） | 省一轮工具往返 | 误拒风险 → 防误拒复核 + 库外实体闸双重确定性护栏 |
| 教学数据本地 CSV/TF-IDF | 离线可复现、秒建索引 | 语义检索弱 → Embedder 接口化，换 bge 只动一个类 |

## 6. 与 week1-4 的继承关系

| week1-4 教学代码 | 吸收方式 | 升级点 |
|---|---|---|
| week1/react_loop.py | `harness/loop.py` | +三重预算/重试退避/超时降级/并行 tool_calls/输出校验钩子 |
| week2/tools.py | `harness/tools.py` + `domains/ecommerce/tools.py` | 手写 schema → Pydantic 自动生成；+task_type 预加载/缓存/确认闸 |
| week2/planner.py | `agents/planner.py` | 意图路由合并进一次调用；+Replan/防误拒/库外实体闸 |
| week2/memory.py | `harness/memory.py` | 单一会话记忆 → 四类记忆（+Session State/画像/遗忘） |
| week3/rag/* | `shoppilot/rag/rag_lib.py` | kb_dir 参数化、索引 lru_cache、ensure_index 自愈重建 |
| week3/mcp_server.py | `shoppilot/mcp/server.py` | 3 工具 → 6 工具 + Resources + Prompts，共享 ToolRegistry |
| week4/multi_agent.py | `agents/dispatcher.py` | 串行 → DAG 同层并行；+结果缓存/失败降级/Replan |
| week4/trace.py | `harness/observability.py` | +事件回调总线（驱动前端 st.status 实时渲染） |
| week4/eval.py | `benchmark.py` | LLM 生成题集+裁判 → 手工 25 题+四项确定性指标（零裁判 token）+token 对照实验 |
