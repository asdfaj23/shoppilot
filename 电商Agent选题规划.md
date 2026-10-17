# 电商 Agent 应用选题规划

> 你的诉求：① 能写简历 ② 自己用得上 ③ 能沉淀真实数据 ④ 电商方向
> 核心思路：电商是目前 Agent 落地最成熟的赛道（阿里/京东/美团都在做），做出来面试时故事性最强

---

## 一、为什么电商方向值得做

**简历角度：**
- 2025-2026 所有大厂（阿里国际站、京东、美团、拼多多）都在招"电商 Agent"方向的人
- 面试官一听"电商 Agent"就知道你懂真实业务，不是玩具 demo
- 能展示"多平台数据采集 + 多 Agent 协作 + 用户个性化"三大技术点

**自用角度：**
- 谁都要买东西，这个项目你自己每周都在用
- 沉淀的"商品库 + 比价数据 + 评论库"是真实数据资产

**差异化角度：**
- 论文研读类已经烂大街（你说得对）
- 电商 Agent 技术深度更高（涉及爬虫、NLP、推荐、对话）

---

## 二、电商 Agent 的 4 个细分选题

| 选题 | 核心价值 | 自用程度 | 数据沉淀 | 简历杀伤力 | 开发难度 |
|---|---|---|---|---|---|
| **A. 个人导购/比价 Agent** | 帮用户决策"买哪个" | ★★★ | ★★★ | ★★★ | ★★ |
| **B. 商品评论洞察 Agent** | 分析差评/好评，提炼痛点 | ★★ | ★★★ | ★★★ | ★★ |
| **C. 选品/市场分析 Agent** | 帮小商家分析"这个品类能不能做" | ★ | ★★★ | ★★★★ | ★★★ |
| **D. 电商文案生成 Agent** | 自动生成标题/详情页/短视频脚本 | ★ | ★★ | ★★ | ★ |

---

## 三、详细对比 + 推荐

### 选题 A：个人导购/比价 Agent（⭐ 最推荐）

**用户场景：**
> "我想买个 4000 元以内的笔记本电脑，要轻便、续航好，主要用来看论文和写代码"
>
> Agent 自动：
> 1. 解析需求（预算/场景/偏好）
> 2. 去京东/淘宝/什么值得买搜候选商品（10-20 个）
> 3. 对比参数（CPU/重量/续航/售后）
> 4. 分析评论（差评关键词："风扇响"、"屏幕色差"）
> 5. 给出最终推荐 Top 3 + 理由
> 6. 沉淀你的"购买偏好"，下次推荐更准

**技术架构（多 Agent 协作）：**

```
用户输入自然语言
   ↓
Intent Agent（理解需求：预算/品类/偏好）
   ↓
Search Agent（并行多平台搜索）
   ├── 京东 API / 爬虫
   ├── 淘宝 API / 爬虫
   └── 什么值得买 / 慢慢买（历史价格）
   ↓
Compare Agent（参数对比表）
   ↓
Review Agent（评论情感分析 + 差评关键词提取）
   ↓
Recommend Agent（综合排序，生成推荐报告）
   ↓
输出：Top 3 推荐 + 比价表 + 评论洞察
```

**数据沉淀（关键）：**
- `商品库`：每次搜索的商品参数、价格、评论，入库（SQLite/PostgreSQL）
- `用户偏好库`：记住"你喜欢轻便"、"你讨厌 RGB 风扇"，长期记忆
- `比价历史`：同一个商品不同时间的价格，判断"现在买是不是好价"

**简历故事：**
> "做了一个个人导购 Agent，把原本 1 小时的购物决策压缩到 5 分钟。多 Agent 协作（意图理解/搜索/对比/评论分析/推荐），沉淀了 5000+ 商品的比价数据库。"

**MVP 工作量：** 2-3 周

---

### 选题 B：商品评论洞察 Agent

**用户场景：**
> 输入一个京东/淘宝商品链接 →
> Agent 自动抓取 1000 条评论 →
> 输出：
> - 好评 Top 3 关键词（"续航好"、"屏幕清晰"）
> - 差评 Top 3 关键词（"风扇响"、"售后差"）
> - 情感倾向分布（90% 正面 / 8% 中性 / 2% 负面）
> - 典型差评原文（让你看到真实吐槽）

**技术要点：**
- 评论爬虫（京东/淘宝有反爬，要做代理池/延时）
- LLM 做评论聚类和关键词提取
- 情感分析（可以用现成模型，也可以 LLM-as-Judge）

**数据沉淀：**
- 评论库：每个商品的 1000 条评论，结构化存储
- 痛点标签库：积累"这个品类大家都在抱怨什么"

**适合场景：** 你自己想买东西时，先跑一遍这个 Agent，就知道值不值得买。

**MVP 工作量：** 1.5 周

---

### 选题 C：选品/市场分析 Agent（商家侧）

**用户场景：**
> "我想在抖音卖露营灯，这个品类能不能做？"
>
> Agent 自动：
> 1. 抓取当前 Top 20 热销品（价格/销量/评价数）
> 2. 分析价格带（大部分在 50-150 元）
> 3. 分析差评关键词（"亮度不够"、"续航虚标"）
> 4. 给出建议："这个品类红海，差评集中在续航，如果你做 200 元价位 + 强调真实续航，有机会"

**简历杀伤力最强**（因为商家付费意愿强，故事性最好），但**自用性最弱**（你不是商家）。

**MVP 工作量：** 3-4 周

---

### 选题 D：电商文案生成 Agent

**太简单，不推荐**（1 周就能做完，但简历上没深度）。

---

## 四、我的最终建议

**首选做选题 A（个人导购/比价 Agent）**，理由：

1. **自用性最强** — 你自己买电脑/手机/相机都能用，每周都跑
2. **多 Agent 协作最典型** — 5 个 Agent 各司其职，技术展示度高
3. **数据沉淀最自然** — 每次搜索都是数据，越用越聪明
4. **和 Hello-Agents 案例不雷同** — 旅行助手是"规划"，你做的是"决策"，本质不同
5. **面试故事最好讲** — "帮用户在 5 分钟内完成原本 1 小时的购物决策"

**可以叠加选题 B（评论洞察）** — 作为 A 的一个子模块（Review Agent），不额外增加多少工作量，但让推荐更可信。

---

## 五、技术架构详细设计（选题 A）

### 5.1 目录结构

```
shopping-agent/
├── app.py                    # Streamlit 入口
├── .env
├── harness/                  # 复用之前的 Harness
│   ├── config.py
│   ├── llm.py
│   ├── models.py            # Pydantic 数据模型
│   ├── memory.py            # 用户偏好记忆
│   ├── tools/
│   │   ├── jd_spider.py     # 京东爬虫
│   │   ├── taobao_spider.py # 淘宝爬虫
│   │   └── smzdm.py          # 什么值得买（历史价格）
│   └── agents/
│       ├── intent_agent.py    # 意图理解
│       ├── search_agent.py     # 多平台搜索
│       ├── compare_agent.py   # 参数对比
│       ├── review_agent.py    # 评论分析
│       └── recommend_agent.py # 最终推荐
├── data/
│   ├── products.db           # 商品库（SQLite）
│   ├── reviews.db            # 评论库
│   └── user_profile.json     # 用户偏好记忆
└── utils/
    ├── export.py             # 导出推荐报告
    └── viz.py                # 比价图/情感分布图
```

### 5.2 数据模型（核心）

```python
# models.py
from pydantic import BaseModel
from typing import List, Optional

class Product(BaseModel):
    """商品"""
    platform: str           # 京东/淘宝/拼多多
    name: str
    price: float
    original_price: Optional[float] = None   # 历史最高价
    params: dict            # {"CPU": "i5-13500H", "重量": "1.4kg"}
    rating: float           # 用户评分
    review_count: int
    url: str
    image_url: Optional[str] = None

class ReviewInsight(BaseModel):
    """评论洞察"""
    product_id: str
    positive_keywords: List[str]   # ["续航好", "屏幕清晰"]
    negative_keywords: List[str]   # ["风扇响", "售后差"]
    sentiment_dist: dict           # {"positive": 0.9, "neutral": 0.08, "negative": 0.02}
    typical_negative: List[str]    # 典型差评原文

class Recommendation(BaseModel):
    """最终推荐"""
    top_3: List[Product]
    reasons: List[str]            # 每个推荐的理由
    price_analysis: str           # "现在是好价/可以再等"
    fit_analysis: str              # "符合你轻便偏好，但续航略低于你的要求"

class UserProfile(BaseModel):
    """用户偏好记忆（长期）"""
    budget_range: tuple[float, float]
    preferred_features: List[str]     # ["轻便", "续航好"]
    hated_features: List[str]          # ["RGB", "风扇响"]
    purchase_history: List[str]         # 历史购买记录
```

### 5.3 关键技术点

| 技术点 | 实现方案 | 难度 |
|---|---|---|
| 京东/淘宝爬虫 | `requests` + 代理池 + 延时；或用 `playwright` 模拟浏览器 | ★★★ |
| 历史价格 | 接入"什么值得买"或"慢慢买"API | ★★ |
| 评论情感分析 | LLM-as-Judge（让 GPT/DeepSeek 给评论打标签） | ★ |
| 多 Agent 编排 | Orchestrator 并行调用 Search/Compare/Review | ★★ |
| 用户偏好记忆 | `EpisodicMemory`（Qdrant 存历史购买偏好） | ★★ |
| Streamlit 界面 | 表单 → 加载进度 → 推荐卡片 + 比价表 + 评论洞察 | ★ |

---

## 六、开发路线（4 周）

| 周 | 目标 | 产出 |
|---|---|---|
| **W1** | 打通京东爬虫 + 1 个最简 Agent | 能搜出 10 个笔记本商品 |
| **W2** | 加评论分析 + 比价表 | 能输出 Top 3 推荐 |
| **W3** | 加多平台（淘宝/什么值得买）+ 用户偏好记忆 | 越用越准 |
| **W4** | Streamlit 界面打磨 + 导出报告 | 自用 + 面试演示 |

---

## 七、简历表述模板

> **个人导购 Agent（Shopping Agent）**
>
> 基于自研 Agent Harness 框架，实现多平台（京东/淘宝/什么值得买）商品搜索与比价的智能导购系统。
>
> **技术栈：** Python / Streamlit / Pydantic / OpenAI 兼容接口 / Qdrant / Playwright 爬虫
>
> **核心工作：**
> 1. 设计并实现 5 个专职 Agent（意图理解/搜索/参数对比/评论分析/最终推荐），通过 Orchestrator 并行编排
> 2. 实现多平台商品数据采集与统一数据模型（商品/评论/价格历史）
> 3. 基于 Qdrant 构建用户偏好记忆系统，实现个性化推荐
> 4. 沉淀 5000+ 商品的比价数据库，支持历史价格走势分析
>
> **亮点：** 将原本 1 小时的购物决策压缩到 5 分钟，自用中。
