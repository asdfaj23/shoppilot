# Week4 GUIDE · 多 Agent + Harness 护栏 + 评测 + 演示

> 理论对应：hello-agents 第七章（多 Agent 调度）、第九章（上下文工程）、第十二章（评估）；
> 卡码网第六节（Agent 是一套系统）。本周产出 = 完整简历项目。

## 文件地图

| 文件 | 干什么 | 对应 Harness 六层 |
|---|---|---|
| `multi_agent.py` | Planner→Dispatcher→3 子 Agent→Aggregator | Agentic Loop ③ + Guides ① |
| `trace.py` | JSONL Trace + Token 成本归因 | Observability ⑥ |
| `eval.py` | LLM 造评测集 + 三指标 + badcase 归因 | Sensors ② |
| `app.py` | Streamlit 演示界面 | —（演示用） |
| `Dockerfile` | 一键部署 | Permissions ⑤ 的外沿（运行环境固化） |

## multi_agent.py 的五个设计点

1. **三子 Agent 分工**：数据 Agent（pandas 工具）/ 检索 Agent（RAG）/ 综合 Agent（纯 LLM）。
   分工依据 = 信息来源不同，而不是拍脑袋。
2. **工具预加载（简历 Token 降 XX% 的那个点）**：
   `TOOL_PRELOAD = {"data_query": TOOLS, "knowledge_qa": [], "synthesis": []}`
   —— 无关工具的 name+description+schema 不进上下文，每个工具几百 token，工具越多省得越多。
3. **护栏四件套**：有界重试（2 次指数退避）/ 超时降级（90s 判失败但整体不卡死）/
   结果缓存（相同子任务描述直接命中）/ 降级文案兜底（失败子任务不拖垮整个报告）。
4. **共享状态**：依赖任务结论以文本显式注入后续任务 prompt（dataclass/字典也行，不上重量级框架）。
5. **Aggregator 冲突仲裁**：子结论矛盾必须点名 + 说明采信理由 + 全部关键发现带来源引用。

## eval.py 的评测设计（面试最容易被追问）

- **造集**：每篇知识库文档 → LLM 生成 3 个"读了才答得出"的 QA（问题/标准答案/出处小节）；
  另混入**库外问题**测拒答率。
- **三指标**：
  - `accuracy`：LLM-as-judge 判 correct=1 / partial=0.5 / wrong=0；
  - `citation_accuracy`：答案引用的来源是否包含标准答案所在文档；
  - `refusal_rate`：拒答占比（对库外题，拒答才是对的）。
- **badcase 归因二分法**：引用里没有标准文档 → 检索偏；有但答错 → 生成/编造问题。
  归因决定改哪：前者改检索（top_k/切分/embedding），后者改 prompt/换模型。
- **Ratchet 闭环**：每个 badcase 沉淀成一条规则/校验，跑分对比——"问题→改动→指标变化"就是面试故事。

```bash
python week4/eval.py --gen             # 生成评测集（一次）
python week4/eval.py --run --limit 6   # 先小跑验证，再全量
```

## trace.py 怎么用

每次 `multi_agent.py` 运行在 `week4/traces/*.jsonl` 落一份全链路明细；
结束时打印总 tokens / 耗时 / 分步占比。**优化前先看 by_step：钱花在哪，刀就砍哪。**
接 Langfuse：把 Tracer.log 换成 langfuse SDK 的 span 上报即可，接口已对齐。

## 演示（现场面试三步）

```bash
python -m streamlit run week4/app.py        # 浏览器演示 问→报告
python week4/multi_agent.py "..."           # 终端看调度与护栏全过程
python week4/eval.py --run                  # 拿出评测分数证明靠谱
```

## 必须弄懂的 5 个点

1. **什么时候不该用多 Agent**：单工具单轮能答的别拆——多 Agent 换来模块化与可观测，代价是 token 与时延。
2. **工具预加载省 token 的原理**：工具 schema 占系统上下文，按任务类型只注入相关工具 = 上下文裁剪。
3. **为什么护栏必须有界**：LLM 会犯循环/幻觉/超时错误，无界循环=烧钱+假死；预算（步数/秒/钱）是底线。
4. **LLM-as-judge 的偏差与对策**：偏宽松、偏长答案；对策：判分规则写细 + 抽样人工复核 + 固定 judge 温度。
5. **"无需人工干预完成的任务数"** 才是真指标：完成率是分子，可观测日志是证据链。

## 亲手改一处

1. 【改调度】给 dispatch 加"子任务失败自动把 task_type 降级重试"逻辑；
2. 【改评测】badcase 里挑一个"检索偏"，把 top_k 4→8 重跑评测，记录 accuracy 变化；
3. 【改聚合】Aggregator 的输出加"置信度"字段（高/中/低 + 理由），观察演示效果。

## 面试追问预案

- "你的项目 Token 降了 XX% 怎么测的？" → 固定同一批评测问题，改动前后各跑一遍，trace.py 记录总 tokens 对比。
- "多 Agent 状态怎么共享？" → 依赖结论显式注入 prompt（显式 > 隐式黑盒），Aggregator 仲裁冲突。
- "怎么证明系统可靠？" → 评测集基线分 + badcase 归因修复曲线 + 全链路 trace 证据。
