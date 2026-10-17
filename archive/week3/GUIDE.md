# Week3 GUIDE · MCP Server 与 FAISS 混合检索 RAG 怎么写的

> 理论对应：hello-agents 第八章（RAG/chunk/存储）、第十章（MCP：Client-Server/JSON-RPC）。
> 本周产出 = 可公开检阅的 MCP Server（简历硬货）+ 带引用溯源的 RAG 0.1。

## A · MCP（mcp_server.py / mcp_client_demo.py）

### MCP 是什么（一句话）
Agent 宿主内的 MCP Client 与独立工具进程（Server）之间用 **JSON-RPC 2.0** 通信的标准协议。
价值：**工具与 Agent 解耦** —— 同一个 Server 能被 Claude Desktop / 任何 MCP 宿主直接挂载。

### 通信全流程（client demo 里逐步走了）
```
Agent 进程                          MCP Server 子进程（stdio 传输）
   │ ── initialize（版本/能力握手）──▶ │
   │ ── tools/list ────────────────▶ │  返回 [{name, description, JSON Schema}]
   │ ── tools/call{name,arguments} ▶ │  执行真实函数
   │ ◀──── JSON-RPC result ───────── │
```
- **stdio 传输**：子进程的 stdin/stdout 就是通信管道，天然跨机器可换 HTTP/SSE；
- **@mcp.tool()** 自动把「函数签名 + docstring」转成 JSON Schema 注册——但它底下干了什么要能讲清；
- 读写隔离：Server 只暴露白名单工具，Agent 拿不到 Server 内部任何其他能力（权限边界的起点）。

### 跑法
```bash
python week3/mcp_client_demo.py    # 拉起 server 子进程，走完整握手→列表→调用
```

## B · RAG（rag/ 目录）

### 链路：切分 → Embedding → 混合检索 → 强制引用

| 步骤 | 实现 | 面试考点 |
|---|---|---|
| 切分 | 按 Markdown 标题做**结构感知切分**，超长段落再按 300 字滑窗 + 60 字重叠 | 为什么不硬切 500 字？知识点被拦腰斩断两边都检索不到 |
| Embedding | 默认 TF-IDF 字符 n-gram（离线秒建）；接口化，可无缝换 bge-small-zh | 向量相似度=归一化后内积（余弦）；生产换 sentence-transformers 只改 encode() |
| 检索 | **FAISS 向量 Top-k + BM25 Top-k → RRF 融合** | 向量懂语义但怕专有名词/数字，BM25 反之 → 互补 |
| 生成 | 资料编号进 prompt，强制 `[1]` 标注，只许用资料回答 | 引用溯源的实现：编号 ↔ chunk 一一对应 |
| 兜底 | 资料不足必须答"知识库中没有相关资料" | 拒答能力 = 幻觉率的第一道闸（评测指标之一） |

### RRF 融合公式（要能手推）
`score(chunk) = Σ路 1/(60 + rank)`，两路都命中的 chunk 得分相加排最前——
这就是混合检索"1+1>2"的机制核心。

### 跑法
```bash
python week3/rag/build_index.py          # 建索引（一次）
python week3/rag/query.py "耳机保修多久"  # 带引用问答
```

## 必须弄懂的 5 个点

1. **JSON-RPC 在 MCP 里怎么传**：请求/响应都是 JSON 文本行，stdio 逐行读写；id 配对请求与响应。
2. **chunk 策略为何影响召回**：切太碎→单 chunk 信息量不足；切太粗→噪声稀释相似度。结构感知 > 固定长度。
3. **混合检索为什么强于纯向量**：专有名词/型号/数字 BM25 命中率高，语义改写靠向量，RRF 融合两头吃。
4. **Rerank 是干嘛的**（本版未做，路线）：粗召回 Top20 → 精排模型重打分取 Top4，精度换算力。
5. **引用溯源怎么实现**：检索结果编号化进 prompt + 生成后校验引用编号 → citation 准确率可评测（Week4 用）。

## 亲手改一处

1. 【改检索】把 top_k 从 4 改成 2 和 8 各跑一次，观察答案质量与 token 变化；
2. 【加知识库】在 data/knowledge/ 新增一篇你自己的文档，重建索引并提问验证；
3. 【加 MCP 工具】给 mcp_server.py 加第 4 个工具，client 里 list_tools 应能看到。

## 面试追问预案

- "MCP 和 Function Calling 什么关系？" → FC 是模型侧能力（决定调什么）；MCP 是工具侧协议（工具怎么注册与通信），一层智能一层管道。
- "为什么 FAISS 不是数据库？" → 内存向量索引库，教学/单机够用；生产换 Milvus/pgvector 要持久化与过滤。
