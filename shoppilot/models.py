"""
ShopPilot ③ Pydantic 数据契约（全项目的"普通话"，分层单向依赖的最底层）
================================================================================
设计原因：
  - LLM 输出与工具返回都是"脏 JSON"，契约层用 pydantic v2 的 field_validator 在
    模型边界清洗（strip/类型纠偏/默认值），上层模块拿到的永远是干净对象——
    "清洗放在数据入口而不是每个使用点"是防御式设计的关键取舍。
  - Literal 枚举约束 intent / task_type：让 LLM 的自由文本收敛到有限集合，
    Dispatcher 才能按 task_type 做工具预加载（省 token 的根基）。
  - 分层单向依赖：本文件只依赖 pydantic，谁都可以 import 它，它不 import 任何人。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------- 意图与查询 ----------------
Intent = Literal["recommend", "compare", "review_analysis", "after_sales", "out_of_scope"]
TaskType = Literal["data_query", "review_analysis", "knowledge_qa", "synthesis"]


class ShoppingQuery(BaseModel):
    """Planner 从用户原话中结构化抽取的需求。"""
    intent: Intent
    entities: list[str] = Field(default_factory=list, description="提到的商品名，如 蓝牙耳机A")
    category: str = ""
    budget_max: float | None = None
    budget_min: float | None = None
    concerns: list[str] = Field(default_factory=list, description="在意维度：降噪/续航/售后…")
    raw_text: str = ""

    @field_validator("entities", "concerns", mode="before")
    @classmethod
    def _clean_str_list(cls, v):
        """LLM 可能给 None/字符串/含空元素——统一成干净字符串列表。"""
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(x).strip() for x in v if str(x).strip()]

    @field_validator("budget_max", "budget_min", mode="before")
    @classmethod
    def _clean_budget(cls, v):
        """容忍 "500" / "500元" / 500.0 / None。"""
        if v in (None, "", "无", "null"):
            return None
        return float(str(v).replace("元", "").replace("￥", "").replace("¥", "").strip())


# ---------------- 计划 DAG ----------------
class SubTask(BaseModel):
    id: int
    goal: str
    task_type: TaskType
    depends_on: list[int] = Field(default_factory=list)

    @field_validator("depends_on", mode="before")
    @classmethod
    def _clean_deps(cls, v):
        if v is None:
            return []
        if isinstance(v, int):
            v = [v]
        return [int(x) for x in v]

    @field_validator("goal")
    @classmethod
    def _goal_not_empty(cls, v):
        v = (v or "").strip()
        if not v:
            raise ValueError("子任务 goal 不能为空")
        return v


class Plan(BaseModel):
    query: ShoppingQuery
    tasks: list[SubTask]

    @model_validator(mode="after")
    def _check_dag(self):
        """id 唯一、依赖存在且指向更小 id（按 id 升序即合法拓扑序，无环）。"""
        ids = [t.id for t in self.tasks]
        if not ids:
            raise ValueError("tasks 不能为空")
        if len(set(ids)) != len(ids):
            raise ValueError("任务 id 重复")
        idset = set(ids)
        for t in self.tasks:
            for d in t.depends_on:
                if d not in idset:
                    raise ValueError(f"任务{t.id} 依赖不存在的任务{d}")
                if d >= t.id:
                    raise ValueError(f"依赖必须指向更小的 id：任务{t.id}→{d}")
        return self

    def layers(self) -> list[list[SubTask]]:
        """DAG 分层（Kahn 法）：同一层无互相依赖 → Dispatcher 可并行。"""
        remaining = {t.id: t for t in self.tasks}
        done: set[int] = set()
        layers: list[list[SubTask]] = []
        while remaining:
            layer = [t for t in remaining.values() if all(d in done for d in t.depends_on)]
            if not layer:  # 理论上被 model_validator 拦住，防御性兜底
                raise ValueError("DAG 存在环，无法分层")
            layers.append(sorted(layer, key=lambda t: t.id))
            done |= {t.id for t in layer}
            for t in layer:
                remaining.pop(t.id)
        return layers


# ---------------- 结构化产出卡 ----------------
class Citation(BaseModel):
    """引用来源：编号 ↔ 资料/任务一一对应（Reflector 可验证性的根基）。"""
    no: int
    source: str = Field(description="文档名 或 任务n")
    heading: str = ""
    text: str = ""

    @field_validator("text", mode="before")
    @classmethod
    def _clip(cls, v):
        return str(v or "")[:120]


class ProductCard(BaseModel):
    name: str
    category: str = ""
    price: float = 0
    rating: float = 0
    sales: int = 0
    warranty: str = ""
    highlights: list[str] = Field(default_factory=list)
    reason: str = ""                       # 推荐理由
    review_summary: str = ""               # 口碑摘要
    citation: int | None = None            # 依据编号

    @field_validator("highlights", mode="before")
    @classmethod
    def _clean_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            v = [v]
        return [str(x).strip() for x in v if str(x).strip()]

    @field_validator("price", "rating", mode="before")
    @classmethod
    def _num(cls, v):
        if v in (None, "", "无"):
            return 0
        return float(str(v).replace("元", "").strip())


class Recommendation(BaseModel):
    kind: Literal["recommendation"] = "recommendation"
    summary: str = ""
    items: list[ProductCard] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class CompareCard(BaseModel):
    kind: Literal["compare"] = "compare"
    subjects: list[str] = Field(default_factory=list)
    dimensions: list[dict[str, str]] = Field(default_factory=list,
                                             description="维度行，如 {维度: 音质, A: xx, B: yy}")
    conclusion: str = ""
    citations: list[Citation] = Field(default_factory=list)

    @field_validator("dimensions", mode="before")
    @classmethod
    def _clean_rows(cls, v):
        rows = []
        for row in v or []:
            if isinstance(row, dict):
                rows.append({str(k).strip(): str(val).strip() for k, val in row.items()})
        return rows


class Caveat(BaseModel):
    product: str = ""
    issue: str = ""
    detail: str = ""
    citation: int | None = None


class CaveatList(BaseModel):
    kind: Literal["caveats"] = "caveats"
    summary: str = ""
    items: list[Caveat] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


StructuredCard = Recommendation | CompareCard | CaveatList


# ---------------- 任务级结果 ----------------
class SubTaskResult(BaseModel):
    task_id: int
    goal: str
    task_type: str
    output: str = ""
    tokens: int = 0
    degraded: bool = False
    tool_trace: list[dict] = Field(default_factory=list,
                                   description="[{name, args, summary, ok}]")


class TeamResult(BaseModel):
    """一次 run_team 的最终产出（前端渲染的唯一数据源）。"""
    intent: str = ""
    answer_md: str = ""
    structured: dict[str, Any] | None = None   # 结构化卡 model_dump（kind 区分渲染器）
    citations: list[Citation] = Field(default_factory=list)
    tasks: list[SubTaskResult] = Field(default_factory=list)
    tokens: int = 0
    duration_s: float = 0.0
    degradations: list[str] = Field(default_factory=list)
    reflect_issues: list[str] = Field(default_factory=list)
    learned: list[str] = Field(default_factory=list, description="本轮画像学到了什么（可解释记忆）")
    trace_file: str = ""

    @field_validator("citations", mode="before")
    @classmethod
    def _clean_citations(cls, v):
        out = []
        for c in v or []:
            if isinstance(c, dict):
                out.append(Citation(no=int(c.get("no", 0)), source=str(c.get("source", "")),
                                    heading=str(c.get("heading", "")), text=str(c.get("text", ""))))
        return [c for c in out if c.source]


# ---------------- 用户画像（文件态记忆） ----------------
class PreferenceProfile(BaseModel):
    user_id: str = "default"
    budget_tier: str = ""            # 预算档：<300 / 300-800 / 800+
    priorities: list[str] = Field(default_factory=list, description="在意维度，按出现频次")
    categories: list[str] = Field(default_factory=list)
    history: list[str] = Field(default_factory=list, description="最近询问，最多 20 条")
    updated_at: str = ""             # 元数据：最近更新时间（过期可判）

    def observe(self, query: ShoppingQuery) -> list[str]:
        """从一次结构化查询里学偏好，返回"学到了什么"（可解释记忆：前端展示给用户看）。"""
        learned: list[str] = []
        if query.budget_max:
            tier = "<300" if query.budget_max < 300 else ("300-800" if query.budget_max <= 800 else "800+")
            if tier != self.budget_tier:
                self.budget_tier = tier
                learned.append(f"预算档更新为 {tier}")
        for c in query.concerns:
            if c not in self.priorities:
                self.priorities.append(c)
                learned.append(f"新增在意维度：{c}")
        if query.category and query.category not in self.categories:
            self.categories.append(query.category)
            learned.append(f"新增关注品类：{query.category}")
        if query.raw_text and query.raw_text not in self.history:
            self.history.insert(0, query.raw_text[:60])
            self.history = self.history[:20]
        self.priorities = self.priorities[:8]
        self.categories = self.categories[:8]
        return learned


# ---------------- Benchmark ----------------
class BenchCase(BaseModel):
    """与 bytedance llmeval 数据集 schema 同源的字段设计（id/dimension/task_type/
    prompt/reference/constraints/meta），meta.expected_tools 与 meta.expected_behavior
    分别对齐 tool_use.jsonl 与 safety.jsonl 的用法。"""
    id: str
    dimension: str = "agent"
    task_type: str = "agent_tool"
    difficulty: str = "medium"
    prompt: str
    reference: str = ""
    constraints: list[dict] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
