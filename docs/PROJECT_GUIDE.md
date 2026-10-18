# ShopPilot 项目全景手册

> 一份文档理解整个项目：是什么 → 数据长什么样 → 架构为什么这么设计 → 怎么玩 → 效果如何 → 面试怎么讲。
> 配套：`../README.md`（快速上手）、`design_review.html`（面试富文本版）、`benchmark_report.md`（评测明细，脚本自动生成）。

---

## 1. 项目是什么

**面向消费者的电商导购 Agent**：输入"预算 500 内推荐降噪耳机 / A 和 B 怎么选 / 这商品翻车点在哪 /
保修多久"，Agent 结构化抽取需求 → 并行查商品池/口碑/售后知识库 → 输出带依据的推荐清单、对比卡、
避坑提示，并跨会话记住用户偏好（预算档/在意维度/品类）。

与 DeerFlow/hello-agents 复现品的差异：①导购五场景各有专属工具链与结构化产出卡；②可解释记忆
（画像可查看可编辑可删除）；③全手写 8 层 Harness（不引 LangChain/LangGraph）；④25 题确定性评测闭环。

## 2. 快速上手

```bash
# 双击 start.bat 启动服务 → 双击 ShopPilot.html 控制台（状态灯变绿后进入）
# 或命令行：
D:\conda_envs\agent\python.exe -m streamlit run app.py
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --run --compare   # 评测
D:\conda_envs\agent\python.exe -X utf8 benchmark.py --baseline        # 纯API基线对照
```

## 3. 数据说明（`data/ecommerce/`）

| 文件 | 内容 | 口径/要点 |
| --- | --- | --- |
| `products.csv` | 10 商品：名称/品类/价格/评分/月销/保修月数/卖点 | 商品池即"知识边界"——池外商品一律拒答，不编造 |
| `reviews.csv` | 60 行评论：商品/星级/维度/原文/日期 | `rating<=3` 记差评；维度枚举：物流/质量/音质/客服/描述不符/续航/降噪/兼容性 |
| `sales.csv` | 品类销量排行 | 供 query_sales_rank 与"哪个卖得好"类问题 |
| `knowledge/*.md` | 5 篇 Markdown：售后政策/蓝牙耳机手册/物流FAQ/卖家指南/选购指南 | RAG 知识库，结构感知切分（按标题，chunk 300 字 overlap 60） |
| `bench_cases.json` | 25 题验收集 | 字段与 bytedance llmeval schema 同源：id/prompt/reference/constraints/meta.expected_tools/expected_behavior |

**换真实数据只改 `adapter.py`**：CSV → 电商 API，6 个工具签名与 schema 不变。

## 4. 架构设计

### 4.1 8 层单向依赖

```
①config(pydantic-settings) ②llm(LLM Provider)
        └─► ③models.py(Pydantic 契约：LLM 脏 JSON 在入口清洗)
                └─► ④harness/loop(有界ReAct) ⑤harness/tools(注册中心) ⑥memory/observability
                        └─► domains/ecommerce(adapter+6工具) + rag/rag_lib(FAISS+BM25+RRF)
                                └─► ⑦agents(planner→dispatcher→aggregator→reflector→team)
                                        └─► ⑧app.py / benchmark.py / mcp/server.py
```

### 4.2 三范式组合（kamacoder《ReAct、Reflection、规划执行》）

| 范式 | 落点 | 护栏 |
| --- | --- | --- |
| 规划执行 | Planner 一次调用产出 ShoppingQuery+DAG | **Replan**：子任务降级带失败证据重拆（换路径），最多一轮防循环 |
| ReAct | harness/loop 有界循环 | 三重预算(steps/tokens/cost)+并行 tool_calls 按id喂回+重试退避+超时降级；工具按 task_type 预加载（防跑散又省 token） |
| Reflection | Reflector 五项自检 | 全确定性规则零裁判 token：①引用编号可验证 ②预算约束 ③商品溯源 ④误拒答 ⑤目标覆盖（拦过早交付）；LLM 只做修复 |

### 4.3 记忆四分类（kamacoder《Agent 的记忆》）

| 类型 | 实现 | 设计 |
| --- | --- | --- |
| 短期 | WorkingMemory 滑窗+滚动摘要 | 摘要压缩而非全文保留，丢噪音留目标/约束/证据 |
| Session State | 结构化任务状态 JSON | 不是聊天记录：goal/任务清单(含降级)/关键结论，下轮注入 Planner 消解指代 |
| 长期 | PreferenceMemory 画像 | 写入像数据库：只收确定性抽取的用户明确表达；覆盖=冲突合并；条数上限=噪音清理；forget()=遗忘；带 updated_at |
| 知识库 | shoppilot/rag | RAG 是"可查的资料"不是"记忆"，语义分开 |

### 4.4 MCP 三原语

Tools（6 只读工具，与内部 Agent 共享 ToolRegistry 单一事实源）/ Resources（`shoppilot://products`
商品池、`shoppilot://knowledge/{doc}` 售后文档）/ Prompts（导购模板）。安全边界：工具最小暴露、
文件资源防目录穿越、参数 Pydantic 校验、危险工具确认闸（Host 侧权限决策挂载点）。

### 4.5 关键取舍速查（被否掉的方案）

| 决策 | 理由 | 代价与对策 |
| --- | --- | --- |
| Plan-and-Solve 拆 DAG 而非纯 ReAct | 省 token、可审、可并行 | 动态性靠 Replan 补 |
| 同层 ThreadPool 并发 | 两路查询时延近半 | 事件交错 → 前端按 task_id 归位 |
| Aggregator 输出结构化卡 | 前端可渲染卡片 | ~~自由 markdown~~ |
| 清洗收敛到 Pydantic 入口 | 修一次到处干净 | ~~使用点各写一套~~ |
| 拒答短路 | 省一轮工具往返 | 误拒 → 关键词复核+库外实体闸双保险 |
| TF-IDF 教学向量 | 离线可复现秒建 | 语义弱 → Embedder 接口化换 bge 只动一个类 |

## 5. 示例提问（五大场景）

与 `ShopPilot.html` 场景卡片一一对应；服务启动后链接可直接点。

- **🎁 选购推荐**：`预算500以内推荐一副降噪耳机`｜`我想给新家组网，预算300左右，有什么路由器推荐吗`｜`500预算买个充电宝，主要出差用，在意安全`｜`给我推荐个蓝牙耳机，要音质好的，贵一点没关系`
- **⚖️ 商品对比**：`蓝牙耳机A和蓝牙耳机F怎么选`｜`机械键盘G和显示器H哪个更值得买`｜`同价位下充电宝B和数据线E哪个口碑更好`｜`充电宝B和蓝牙耳机A哪个卖得更好`
- **⚠️ 口碑解读**：`蓝牙耳机A翻车点在哪`｜`最近智能手表D的差评多吗`｜`有没有人反馈充电宝鼓包的`｜`想买机械键盘G，办公室用怕吵`
- **🛡️ 售后问答**：`蓝牙耳机A保修多久`｜`七天无理由退货有什么条件`｜`蓝牙耳机A充不进电怎么办`｜`显示器H有亮点能退吗`
- **🚫 拒答**：`iPhone 18 卖多少钱`｜`蓝牙耳机Z多少钱`｜`周末去哪里爬山`

## 6. 评测与对照实验（详见 benchmark_report.md）

四项确定性指标（零裁判 token，规则判定可回归）：

| 对照 | 任务完成率 | 拒答正确率 | 结论 |
| --- | --- | --- | --- |
| **纯 API 直调（基线）** | 0.48 | 0.84 | 推荐类 5 题全灭（编造池外商品/价格）、4 道拒答题全硬答、售后数字靠猜 |
| **ShopPilot Agent** | **1.00** | **1.00** | 引用可验证 0.96；token 10706/题 |

辅助对照：工具按 task_type 预加载 vs 全量注入 → 工具密集场景省 **16-28%** token（整体 4%，被无工具场景稀释）。

## 7. 面试 Q&A（速查版，富文本见 design_review.html）

- **Q 不就是调 API 吗？** API 解决"会说"，Agent 解决"系统做对"。`llm.py` 不到 100 行；其余是重试退避/
  超时降级/Replan/确定性自检/记忆/评测。基线实测：纯 API 完成率 0.48 且编造商品——**API 便宜但不可信，
  Agent 贵得有道理**。
- **Q 和 DeerFlow 复现品区别？** 承认方法论同源；差异五点：业务深度/可解释记忆/确定性 Reflection/
  评测闭环/MCP 三原语，逐条有数字。
- **Q ReAct 怎么防跑散？** 步数限制+三重预算+同 goal 缓存+工具预加载+停止条件。
- **Q Reflection 为什么用规则？** LLM 自评有自我讨好偏差；引用越界/超预算/编造/误拒答用规则 100% 可靠零成本。
- **Q 长对话为什么变傻？** 上下文污染+窗口+计费；滑窗+摘要+Session State 三层解。
- **Q 记忆怎么防脏？** 只写确定性抽取的用户明确表达；覆盖更新；上限清理；forget 删除；updated_at 过期。
- **Q RAG 为什么混合检索？** 向量懂语义不懂数字，BM25 反之，RRF 融合。
- **Q 幻觉怎么治？** 生成侧强制 [n] 引用+不足拒答；校验侧 Reflector 规则查编号；评测侧引用可验证率。
- **Q MCP 和 Function Calling？** FC 模型侧调用机制，MCP 应用侧接入协议；我的 Server 与内部 Agent 共享 ToolRegistry。
- **Q 指标为什么不用 LLM 裁判？** 裁判有偏差且烧钱；四件事规则判 100% 可靠。
- **Q 最大的坑？** "拒答"vs"诚实说明信息不足"的口径——拒答=职责外/库外，信息不足=部分完成。
