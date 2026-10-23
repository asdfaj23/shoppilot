"""Deterministic output guardrails — core data contracts.

The core idea (and the project's research claim): after an LLM produces an answer,
run a set of **deterministic, zero-LLM-token, unit-testable** checks on it. Each
check is a small pure function over the *structured* evidence the agent already
collected (tool results, citations, the parsed card, the user's constraints). A
failed ``blocker`` triggers exactly one bounded repair pass; the checks are
re-run once; an unresolved blocker is reported rather than silently shipped.

This module deliberately has no LLM import: guardrails are programs, not prompts.
The LLM-self-reflection baseline lives in a separate, optional module so the two
approaches can be compared head to head.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

CITE_PATTERN = re.compile(r"\[(\d{1,2})\]")

# Answers containing any of these are treated as a (potential) refusal.
REFUSAL_WORDS = ("没有相关资料", "无法回答", "知识库中没有", "查询不到该商品",
                 "无法找到", "没有找到相关", "抱歉")


@dataclass
class GuardrailContext:
    """Everything a guardrail may inspect — all produced *before* the final answer."""

    user_input: str = ""
    intent: str = ""
    structured_query: Any = None          # ShoppingQuery | dict | None
    sources: list[dict] = field(default_factory=list)   # retrieved evidence, each has "no"
    answer_md: str = ""
    card: Any = None                      # structured card / Recommendation / None
    task_outputs: dict = field(default_factory=dict)    # task_id -> SubTaskResult (optional)

    @property
    def known_source_nos(self) -> set[int]:
        nos: set[int] = set()
        for s in self.sources:
            no = s.get("no")
            if isinstance(no, int):
                nos.add(no)
        return nos

    @property
    def cited_numbers(self) -> list[int]:
        return [int(n) for n in CITE_PATTERN.findall(self.answer_md)]

    @property
    def budget_max(self) -> float | None:
        q = self.structured_query
        if q is None:
            return None
        b = q.get("budget_max") if isinstance(q, dict) else getattr(q, "budget_max", None)
        return float(b) if b is not None else None

    @property
    def entities(self) -> list[str]:
        q = self.structured_query
        if q is None:
            return []
        return list(q.get("entities", [])) if isinstance(q, dict) else list(getattr(q, "entities", []))


@dataclass
class Verdict:
    """Result of one guardrail rule."""

    rule: str                       # stable machine id, e.g. "citation_bound"
    name: str                       # human-readable rule name
    passed: bool
    severity: str = "blocker"       # "blocker" | "warning"
    violation: str = ""             # what is wrong (goes into the repair prompt)
    evidence: dict = field(default_factory=dict)

    @classmethod
    def ok(cls, rule: str, name: str, evidence: dict | None = None) -> Verdict:
        return cls(rule=rule, name=name, passed=True, evidence=evidence or {})

    @classmethod
    def fail(cls, rule: str, name: str, violation: str,
             severity: str = "blocker", evidence: dict | None = None) -> Verdict:
        return cls(rule=rule, name=name, passed=False, severity=severity,
                   violation=violation, evidence=evidence or {})


@runtime_checkable
class OutputGuardrail(Protocol):
    """A guardrail is anything with a name, severity and a pure ``check``."""

    name: str
    severity: str

    def check(self, ctx: GuardrailContext) -> Verdict: ...


@dataclass
class GuardrailReport:
    """Aggregated verdicts from a set of guardrails."""

    verdicts: list[Verdict] = field(default_factory=list)

    @property
    def blockers(self) -> list[Verdict]:
        return [v for v in self.verdicts if not v.passed and v.severity == "blocker"]

    @property
    def warnings(self) -> list[Verdict]:
        return [v for v in self.verdicts if not v.passed and v.severity == "warning"]

    @property
    def passed(self) -> bool:
        return not self.blockers

    def to_fix_prompt(self) -> str:
        """A bounded, itemised instruction fed back to the composer for one repair pass."""
        issues = [f"- {v.name}：{v.violation}" for v in self.blockers + self.warnings]
        if not issues:
            return ""
        return (
            "你的回答在以下确定性校验中未通过，请只修正这些问题、保持其余内容不变，"
            "并严格基于已给资料重写，禁止编造数据：\n" + "\n".join(issues)
        )

    def to_trace(self) -> dict:
        return {
            "passed": self.passed,
            "blockers": [v.rule for v in self.blockers],
            "warnings": [v.rule for v in self.warnings],
            "details": [
                {"rule": v.rule, "passed": v.passed, "severity": v.severity,
                 "violation": v.violation}
                for v in self.verdicts
            ],
        }
