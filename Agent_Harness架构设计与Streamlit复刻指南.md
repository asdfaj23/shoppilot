# Agent Harness 通用架构设计指南

> 基于 Datawhale Hello-Agents 第四部分三个综合案例（第十三章 智能旅行助手 / 第十四章 自动化深度研究智能体 / 第十五章 构建赛博小镇）提炼而成。
> 目标：把三个案例的"骨架"抽离成一套可复用的 Agent 运行框架（Agent Harness），并用 Streamlit 作为前端快速复刻任意一个同类应用。

---

## 〇、什么是 Agent Harness

**Harness（马具 / 框架）** 指的是"把一匹野马（LLM）套进可控制系统里的那套架子"。在 AI 工程语境下，Agent Harness = 让一个大模型能够**可靠、可观测、可复用**地完成任务的整套运行时框架。

它不是某个具体应用，而是三个应用背后**共享的骨架**：

| 案例 | 业务域 | 核心 Agent 范式 |
|---|---|---|
| 智能旅行助手 | 行程规划 | Orchestrator-Worker（3 个并行 Worker + 1 个总控） |
| 自动化深度研究 | 知识检索 | TODO-Plan-Execute-Report（流水线） |
| 赛博小镇 | 实时交互 | 单 Agent 多实例 + 记忆系统 |

三者形态不同，但拆开看底层都是同一套东西：**模型 + 工具 + 记忆 + 状态 + 编排 + 前端**。

---

## 一、三案例架构横向对比（一眼看懂差异）

```
┌─────────────────────────────────────────────────────────────────┐
│                    通用四层架构（所有案例共用）                  │
├──────────────┬──────────────┬───────────────┬─────────────────┤
│   前端层     │   后端层     │   智能体层     │   外部服务层     │
├──────────────┼──────────────┼───────────────┼─────────────────┤
│  Vue3/Godot  │  FastAPI     │  N 个 Agent   │  LLM / API / DB  │
│  /Streamlit  │  /同进程     │  + Harness    │  /向量库/搜索    │
└──────────────┴──────────────┴───────────────┴─────────────────┘
```

| 维度 | 旅行助手 (Ch13) | 深度研究 (Ch14) | 赛博小镇 (Ch15) |
|---|---|---|---|
| 交互模式 | 一次性表单 → 等待结果 | SSE 流式推送 | 实时双向对话 |
| 编排模式 | 并行 Worker | 线性 Pipeline | 单 Agent 多实例 |
| 核心 Agent | 4 个（Planner/Attraction/Weather/Hotel） | 3 个（TODO Planner/Summarizer/Reporter） | 每 NPC 1 个 SimpleAgent |
| 工具 | 高德 MCP、Unsplash | SearchTool、NoteTool | 无外部工具，纯 LLM |
| 记忆 | 无（一次性） | 无（写入笔记） | **WorkingMemory + EpisodicMemory** |
| 数据流 | 请求-响应 | 流式进度推送 | 实时双向 |
| 状态存储 | 内存 Itinerary | workspace 文件 | Qdrant + SQLite |
| 可视化 | 地图 + 卡片 | Markdown 报告 | 2D 游戏引擎 |

**关键洞察：** 所有案例都遵循同一套"**输入 → 编排 → 工具/记忆 → 输出**"的内核，只是**编排拓扑**和**交互模式**不同。

---

## 二、Agent Harness 的完整分层设计

一套通用的 Agent Harness 应该包含以下 8 个模块，可按需裁剪：

```
┌─────────────────────────────────────────────────────────────┐
│  ⑧ Frontend Adapter   (Streamlit / Vue3 / Godot)          │
├─────────────────────────────────────────────────────────────┤
│  ⑦ Orchestrator       (总控：决定调用哪个 Agent)          │
├─────────────┬──────────────┬─────────────────────────────┤
│ ⑥ Agent     │ ⑤ Tool       │ ④ Memory                    │
│   Registry  │   Registry   │   Manager                   │
│   (角色注册) │   (工具注册)  │   (短期+长期)               │
├─────────────┴──────────────┴─────────────────────────────┤
│ ③ Model Layer (Pydantic 数据契约)                         │
├─────────────────────────────────────────────────────────────┤
│ ② LLM Provider (OpenAI 兼容接口，可切换 DeepSeek/Qwen)    │
├─────────────────────────────────────────────────────────────┤
│ ① Infra Layer (配置、日志、缓存、并发池)                   │
└─────────────────────────────────────────────────────────────┘
```

### ① Infra Layer（基础设施层）

最底层，提供所有上层模块共用的能力：

```python
# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")
    llm_provider: str = "deepseek"
    llm_api_key: str
    llm_base_url: str
    llm_model: str = "deepseek-chat"
    amap_key: str | None = None
    unsplash_key: str | None = None
    search_backend: str = "duckduckgo"
    qdrant_url: str | None = None
```

**关键能力清单：**
- 配置统一（pydantic-settings 读 `.env`）
- 日志系统（按模块分文件、按天滚动）
- LLM 调用缓存（相同 prompt 命中缓存省钱）
- 并发池（`ThreadPoolExecutor` / `asyncio`）
- 错误重试 + 超时控制

### ② LLM Provider 抽象层

**绝对不要直接写 `openai.ChatCompletion.create()`**，要包一层：

```python
# llm.py
from openai import OpenAI
from .config import settings

class LLM:
    def __init__(self):
        self.client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        self.model = settings.llm_model

    def chat(self, messages, response_format=None, temperature=0.7):
        kwargs = dict(model=self.model, messages=messages, temperature=temperature)
        if response_format == "json":
            kwargs["response_format"] = {"type": "json_object"}
        resp = self.client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content
```

**为什么要抽象？**
- 换模型（DeepSeek ↔ Qwen ↔ OpenAI）只改配置；
- 统一加缓存、加 retry、加日志；
- 给所有 Agent 共享同一个 client，避免重复建连。

### ③ Model Layer（Pydantic 数据契约）

**这是整个 Harness 最重要的基础**。所有跨模块流转的数据，都必须是 Pydantic 模型。

**设计原则：自底向上**

```
原子模型 (Location / Money / Text)
    ↓ 组合
实体模型 (Attraction / Hotel / Task / NPCProfile)
    ↓ 聚合
会话模型 (Itinerary / ResearchReport / DialogueContext)
    ↓ 顶层
请求/响应模型 (TripQuery / ResearchTopic / UserMessage)
```

**关键技巧：**
1. **用 `field_validator` 清洗脏数据**（如高德返回的 `"16°C"` → `16`）
2. **用 `Optional` + `default` 区分"没给"和"给了"**
3. **用 `Literal` 限制枚举值**（如 `type: Literal["breakfast","lunch","dinner"]`）
4. **所有跨模块流转的对象都要 `model_dump()` / `model_validate()`**

### ④ Memory Manager（记忆管理器）

**这是"能不能做成持续交互型应用"的关键**。赛博小镇案例证明了记忆系统的价值，而旅行助手/深度研究因为是一次性的所以没做——但你的 Harness 应该把它做成**可插拔**的。

```python
class MemoryManager:
    def __init__(self, working_memory, episodic_memory=None):
        self.working = working_memory      # 短期：最近 N 条消息，TTL 过期
        self.episodic = episodic_memory    # 长期：向量库语义检索

    def add(self, user_msg, assistant_msg):
        self.working.add(user_msg, assistant_msg)
        if self.episodic:
            self.episodic.add(user_msg, assistant_msg)

    def build_context(self, query: str, k: int = 3) -> list[dict]:
        """组装给 LLM 的上下文：短期最近 5 条 + 长期 top-k 相关"""
        recent = self.working.get_recent(5)
        relevant = self.episodic.search(query, top_k=k) if self.episodic else []
        return self._merge(recent, relevant)
```

**三种记忆组合（按需用）：**

| 记忆类型 | 实现 | 适用场景 |
|---|---|---|
| Working Memory | deque(maxlen=N) | 对话连贯性 |
| Episodic Memory | Qdrant / Chroma 向量库 | 长期个性化 |
| Note Memory | Markdown 文件 | 深度研究中间产物 |

### ⑤ Tool Registry（工具注册中心）

**统一工具签名**，让 Agent 只需知道"有哪些工具可用"，而不关心底层实现。

```python
# tool.py
from dataclasses import dataclass
from typing import Callable

@dataclass
class Tool:
    name: str
    description: str
    func: Callable
    args_schema: type  # Pydantic 模型

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        self._tools[tool.name] = tool

    def get_schema_for_llm(self) -> list[dict]:
        """生成 OpenAI function-calling 格式的工具描述"""
        return [
            {"type": "function",
             "function": {"name": t.name, "description": t.description,
                          "parameters": t.args_schema.model_json_schema()}}
            for t in self._tools.values()
        ]

    def call(self, name: str, args: dict):
        return self._tools[name].func(**args)
```

**为什么要注册中心？**
- 换实现（高德 → 百度地图）只改一处；
- 自动生成 LLM function-calling 描述；
- 多 Agent 共享同一套工具，避免重复建连。

### ⑥ Agent Registry（智能体注册中心）

每个 Agent 是一个独立类，有自己的 system prompt + 专属工具集 + 记忆策略。

```python
# agent.py
class BaseAgent:
    name: str
    system_prompt: str
    tools: list[str] = []   # 该 Agent 可调用的工具名

    def __init__(self, llm: LLM, registry: ToolRegistry, memory: MemoryManager | None = None):
        self.llm = llm
        self.registry = registry
        self.memory = memory

    def run(self, user_input: str, context: dict | None = None) -> str:
        messages = [{"role": "system", "content": self.system_prompt}]
        if self.memory:
            messages += self.memory.build_context(user_input)
        if context:
            messages.append({"role": "user", "content": f"{user_input}\n\n背景：{context}"})
        else:
            messages.append({"role": "user", "content": user_input})
        return self.llm.chat(messages)
```

**三种典型 Agent 范式（可继承 BaseAgent）：**

| 范式 | 适用案例 | 关键特征 |
|---|---|---|
| **SimpleAgent** | 赛博小镇 | 单轮调用 LLM，带记忆 |
| **ToolAgent** | 旅行助手 Worker | 能根据 LLM 决策调用工具 |
| **PipelineAgent** | 深度研究 | 规划-执行-总结 三步走 |

### ⑦ Orchestrator（编排器）

**这是 Harness 的大脑**。决定"用户来了，该走哪条路"。

三种经典编排模式（对应三个案例）：

#### 模式 A：并行 Worker（旅行助手）
```python
class TripOrchestrator:
    def run(self, query: TripQuery) -> Itinerary:
        with ThreadPoolExecutor() as pool:
            f_attr = pool.submit(self.attraction_agent.run, query)
            f_weather = pool.submit(self.weather_agent.run, query)
            f_hotel = pool.submit(self.hotel_agent.run, query)
            attractions = f_attr.result()
            weather = f_weather.result()
            hotels = f_hotel.result()
        # 总控 Agent 整合
        return self.planner_agent.run(query, attractions, weather, hotels)
```

#### 模式 B：线性 Pipeline（深度研究）
```python
class ResearchOrchestrator:
    def run(self, topic: str):
        todos = self.planner_agent.run(topic)        # 规划
        notes = []
        for task in todos:                            # 执行
            search_results = self.search_tool.call(task.query)
            summary = self.summarizer_agent.run(task, search_results)
            self.note_tool.call(summary)
            notes.append(summary)
        report = self.reporter_agent.run(topic, notes) # 报告
        return report
```

#### 模式 C：单 Agent 路由（赛博小镇）
```python
class DialogueOrchestrator:
    def route(self, npc_id: str) -> BaseAgent:
        return self.npc_registry[npc_id]   # 直接找到对应 NPC 的 Agent
```

**编排器设计要点：**
- 无依赖的步骤**并行**（缩短延迟）；
- 有依赖的步骤**串行**（保证正确性）；
- 每一步都要**发事件**给前端（进度、日志、状态变化）；
- 失败时**降级**（某个 Worker 挂了，总控用已有结果继续）。

### ⑧ Frontend Adapter（前端适配层）

把 Harness 的能力暴露给前端。三种前端方案：

| 前端 | 适用场景 | 通信方式 |
|---|---|---|
| **Streamlit** | 快速 MVP、内部工具 | 同进程直接调用 |
| **FastAPI + Vue3** | 生产级 Web 应用 | HTTP / SSE |
| **FastAPI + Godot** | 游戏/3D 应用 | HTTP |

---

## 三、Streamlit 复刻的完整设计

既然你要用 Streamlit，下面是**具体可落地的目录结构和关键代码**。

### 3.1 推荐项目结构

```
my-agent-harness/
├── app.py                    # Streamlit 入口
├── .env
├── requirements.txt
├── harness/                  # 通用 Harness（可复用）
│   ├── __init__.py
│   ├── config.py             # ① 配置
│   ├── llm.py               # ② LLM 抽象
│   ├── models.py            # ③ Pydantic 模型
│   ├── memory.py             # ④ 记忆管理器
│   ├── tools/                # ⑤ 工具注册
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── amap.py
│   │   ├── search.py
│   │   └── unsplash.py
│   ├── agents/              # ⑥ Agent 注册
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── simple.py
│   │   ├── tool_agent.py
│   │   └── pipeline.py
│   └── orchestrator.py      # ⑦ 编排器
├── apps/                     # 具体业务应用（复用 Harness）
│   ├── trip_planner.py      # 应用1：旅行助手
│   ├── deep_research.py     # 应用2：深度研究
│   └── cyber_town.py        # 应用3：赛博小镇
└── utils/
    ├── export.py            # 导出 PNG/PDF
    └── viz.py               # 可视化
```

**核心思路：Harness 是通用底座，apps/ 里每个业务应用都是"组装 Harness 组件"得到的**。这就是为什么叫"Harness"——同一副马具，套不同的马。

### 3.2 app.py 入口（多应用路由）

```python
# app.py
import streamlit as st
from apps import trip_planner, deep_research, cyber_town

st.set_page_config(page_title="My Agent Harness", layout="wide")

PAGES = {
    "🧳 旅行规划": trip_planner,
    "🔍 深度研究": deep_research,
    "🏠 赛博小镇": cyber_town,
}

choice = st.sidebar.radio("选择应用", list(PAGES.keys()))
PAGES[choice].main()   # 每个 app 模块暴露一个 main() 函数
```

### 3.3 旅行助手应用（apps/trip_planner.py）

```python
# apps/trip_planner.py
import streamlit as st
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from harness.llm import LLM
from harness.tools import ToolRegistry, AmapTool, UnsplashTool
from harness.models import TripQuery
from harness.orchestrator import TripOrchestrator

def main():
    st.header("🧳 智能旅行助手")

    with st.form("trip_form"):
        destination = st.text_input("目的地", "北京")
        days = st.slider("天数", 1, 7, 3)
        prefs = st.multiselect("偏好", ["历史","美食","自然风光","购物"], ["历史"])
        budget = st.select_slider("预算", ["经济","中等","豪华"], value="中等")
        submitted = st.form_submit_button("开始规划")

    if submitted:
        query = TripQuery(destination=destination, days=days,
                          prefs=prefs, budget_level=budget)
        with st.spinner("多智能体并行规划中..."):
            orch = TripOrchestrator()
            st.session_state.itinerary = orch.run(query)

    if "itinerary" in st.session_state:
        it = st.session_state.itinerary
        # 概览
        c1, c2, c3 = st.columns(3)
        c1.metric("总预算", f"¥{it.budget.total}")
        c2.metric("景点数", sum(len(d.attractions) for d in it.days))
        c3.metric("酒店", it.days[0].hotel.name if it.days[0].hotel else "-")
        # 地图
        rows = [{"lat": a.location.latitude, "lon": a.location.longitude, "name": a.name}
                for d in it.days for a in d.attractions]
        st.map(pd.DataFrame(rows))
        # 每日
        for d in it.days:
            with st.expander(f"第{d.day_index+1}天 · {d.date}"):
                for a in d.attractions:
                    st.write(f"📍 {a.name} · 游览{a.visit_duration}分钟 · ¥{a.ticket_price}")
```

### 3.4 深度研究应用（apps/deep_research.py）

关键差异：**用 `st.status` 模拟 SSE 流式进度**。

```python
def main():
    st.header("🔍 自动化深度研究")
    topic = st.text_input("研究主题", "Datawhale是一个什么样的组织？")
    if st.button("开始研究"):
        orch = ResearchOrchestrator()
        todos = orch.plan(topic)
        # 实时展示 TODO 列表
        progress = st.progress(0, "规划完成")
        notes = []
        for i, task in enumerate(todos):
            with st.status(f"搜索：{task.title}"):
                results = orch.search(task)
                summary = orch.summarize(task, results)
                notes.append(summary)
            progress.progress((i+1)/len(todos), f"已完成 {i+1}/{len(todos)}")
        with st.status("撰写最终报告"):
            report = orch.report(topic, notes)
        st.markdown(report)
```

### 3.5 赛博小镇应用（apps/cyber_town.py）

关键差异：**有记忆，是持续对话**。

```python
def main():
    st.header("🏠 赛博小镇")
    npc = st.selectbox("选择 NPC", ["张三(Python工程师)", "李四(产品经理)", "王五(UI设计师)"])
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    for msg in st.session_state.chat_history:
        st.chat_message(msg["role"]).write(msg["content"])

    user_input = st.chat_input(f"对{npc}说点什么...")
    if user_input:
        st.chat_message("user").write(user_input)
        agent = get_npc_agent(npc)   # 带记忆的 SimpleAgent
        reply = agent.run(user_input)
        st.session_state.chat_history.append({"role":"user","content":user_input})
        st.session_state.chat_history.append({"role":"assistant","content":reply})
        st.chat_message("assistant").write(reply)
```

---

## 四、Streamlit 特有的坑（必看）

| 坑 | 现象 | 解法 |
|---|---|---|
| **脚本重跑** | 点任何按钮整个 `app.py` 从头执行 | 状态全放 `st.session_state`；耗时计算放 `if "key" not in st.session_state:` |
| **并行 Agent 拖慢 UI** | Worker 串行执行，界面卡死 | 用 `concurrent.futures.ThreadPoolExecutor` 并发 |
| **LLM 调用无进度** | 用户盯着 spinner 不知道在干嘛 | 用 `st.status` 分阶段展示，每个 Agent 完成后更新 |
| **地图 marker 太丑** | `st.map` 只能画圆点 | 用 `streamlit-folium` 画带编号、有连线的路线 |
| **多应用共享配置** | 每个 app 都重复初始化 LLM | 用 `@st.cache_resource` 缓存 LLM client |
| **对话状态丢失** | 刷新 NPC 记忆就没了 | 用 `st.session_state` 存对话历史，记忆系统持久化到文件 |

---

## 五、MVP 开发顺序（6 个阶段）

| 阶段 | 目标 | 关键产出 |
|---|---|---|
| **P0 打通 Harness 骨架** | LLM + 配置 + 1 个最简 Agent 能跑 | `harness/` 空架子，Hello World |
| **P1 加工具 + ToolAgent** | 接入 1 个真实 API（如高德 POI） | `ToolRegistry` 可用 |
| **P2 做第一个应用（旅行助手）** | 表单 → 并行 3 Worker → 出结果 | `apps/trip_planner.py` 能看 |
| **P3 加记忆系统** | 给赛博小镇应用做对话 | `MemoryManager` + `EpisodicMemory` |
| **P4 做深度研究** | TODO-Execute-Report 流水线 | `apps/deep_research.py` |
| **P5 打磨** | 导出、进度条、错误处理、缓存 | 生产可用 |

---

## 六、设计思想总结（一句话）

> **"把 LLM 当一匹野马，用 Harness 的 8 层架子把它套住：配置管钥匙、模型管数据、工具管手脚、记忆管脑子、Agent 管角色、编排管调度、前端管交互。"**

**三条核心原则：**

1. **分层解耦** — 每一层只做一件事，上层不知道下层细节；
2. **数据契约优先** — Pydantic 模型是所有模块的通用语言；
3. **可插拔** — Agent / Tool / Memory 都是注册制，换一个实现不影响其他。

---

## 附录 A：requirements.txt 最小集

```
streamlit>=1.36
streamlit-folium
pydantic>=2.0
pydantic-settings
python-dotenv
requests
openai
pandas
folium
qdrant-client     # 长期记忆（赛博小镇用）
chromadb          # 备选向量库
reportlab         # 导出 PDF
matplotlib
```

## 附录 B：.env 模板

```bash
# LLM
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# 工具
AMAP_API_KEY=xxx
UNSPLASH_ACCESS_KEY=xxx
SEARCH_BACKEND=duckduckgo

# 记忆（可选）
QDRANT_URL=http://localhost:6333
```

## 附录 C：三个案例的"复刻难度评级"

| 案例 | Harness 复用度 | 开发难度 | 推荐先做 |
|---|---|---|---|
| 旅行助手 | 高（工具+编排最典型） | ★★★ | ✅ 第一个做 |
| 深度研究 | 高（流水线最清晰） | ★★★ | 第二个 |
| 赛博小镇 | 中（要加记忆系统） | ★★★★ | 最后做 |
