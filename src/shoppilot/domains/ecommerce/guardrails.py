"""E-commerce domain guardrails.

Deterministic rules for the failure modes that actually hurt a shopping agent.
The reference-citation rule is reused from the generic built-ins; this module
adds the four domain-specific rules:

* :class:`BudgetGuardrail`        — never recommend a card item over the budget.
* :class:`SourceGroundingGuardrail` — every product named in the card/answer must
  be traceable to a retrieved source (and must exist in the product catalogue).
* :class:`FalseRefusalGuardrail`  — after-sales answers with evidence present must
  not wrongly refuse ("知识库中没有…").
* :class:`GoalCoverageGuardrail`  — comparisons cover every named product;
  recommendations address every stated concern.

Rules are pure functions over :class:`GuardrailContext`; the catalogue is injected
so they stay unit-testable without an LLM.
"""
from __future__ import annotations

from shoppilot.core.guardrails.base import (
    REFUSAL_WORDS,
    GuardrailContext,
    Verdict,
)


def _card_dump(card) -> dict:
    if card is None:
        return {}
    return card.model_dump() if hasattr(card, "model_dump") else (card if isinstance(card, dict) else {})


def _card_products(card) -> list[str]:
    """Product names carried by a recommendation / compare / research card."""
    c = _card_dump(card)
    names: list[str] = []
    for it in c.get("items", []) or []:
        if isinstance(it, dict) and it.get("name"):
            names.append(it["name"])
    for s in c.get("subjects", []) or []:
        if isinstance(s, str) and s:
            names.append(s)
    for cand in c.get("candidates", []) or []:
        if isinstance(cand, dict) and cand.get("name"):
            names.append(cand["name"])
    return names


def _card_prices(card) -> list[tuple[str, float]]:
    c = _card_dump(card)
    out = []
    for it in c.get("items", []) or []:
        if isinstance(it, dict) and it.get("name"):
            try:
                out.append((it["name"], float(it.get("price", 0) or 0)))
            except (TypeError, ValueError):
                continue
    return out


class BudgetGuardrail:
    """Blocker: any structured recommendation priced above the user's ceiling."""

    name = "budget_guard"
    severity = "blocker"

    def __init__(self, tolerance: float = 0.0):
        self.tolerance = tolerance  # allow rounding slack (fraction, e.g. 0.02)

    def check(self, ctx: GuardrailContext) -> Verdict:
        cap = ctx.budget_max
        if cap is None:
            return Verdict.ok(self.name, "预算约束检查")
        limit = cap * (1 + self.tolerance)
        over = [(n, p) for n, p in _card_prices(ctx.card) if p > limit]
        if over:
            desc = "、".join(f"{n}（¥{p:.0f}）" for n, p in over)
            return Verdict.fail(
                self.name, "预算约束检查",
                f"用户预算上限 ¥{cap:.0f}，但推荐了：{desc}，超出预算，必须换成预算内商品。",
                evidence={"budget_max": cap, "over_budget": over})
        return Verdict.ok(self.name, "预算约束检查", {"budget_max": cap})


class SourceGroundingGuardrail:
    """Blocker: named products must exist in the catalogue and be grounded in sources."""

    name = "grounding"
    severity = "blocker"

    def __init__(self, product_names: list[str] | None = None):
        self.product_names = product_names or []
        self._pool = set(self.product_names)

    def _source_blob(self, ctx: GuardrailContext) -> str:
        return "\n".join(str(s.get("text", "")) + str(s.get("source", "")) + str(s.get("heading", ""))
                         for s in ctx.sources)

    def check(self, ctx: GuardrailContext) -> Verdict:
        blob = self._source_blob(ctx)
        card_products = _card_products(ctx.card)
        out_of_catalogue = [n for n in card_products if self._pool and n not in self._pool]
        if out_of_catalogue:
            return Verdict.fail(
                self.name, "商品溯源检查",
                f"卡片中出现了商品池之外的商品：{sorted(set(out_of_catalogue))}，"
                "属于编造商品，必须删除或改用真实商品。",
                evidence={"out_of_catalogue": sorted(set(out_of_catalogue))})
        # In-catalogue but never retrieved -> the card was not backed by a tool call.
        ungrounded = [n for n in card_products if n not in blob]
        if ungrounded:
            return Verdict.fail(
                self.name, "商品溯源检查",
                f"卡片商品 {sorted(set(ungrounded))} 在检索资料中没有任何依据，"
                "必须先调用工具核实价格/参数，或删除该商品。",
                evidence={"ungrounded": sorted(set(ungrounded))},
                severity="warning")
        return Verdict.ok(self.name, "商品溯源检查", {"products": card_products})


class FalseRefusalGuardrail:
    """Blocker: don't refuse after-sales / policy questions when evidence exists."""

    name = "false_refusal"
    severity = "blocker"

    def __init__(self, intents: set[str] | None = None, min_evidence_chars: int = 20):
        self.intents = intents or {"after_sales", "research"}
        self.min_evidence_chars = min_evidence_chars

    def check(self, ctx: GuardrailContext) -> Verdict:
        if ctx.intent not in self.intents:
            return Verdict.ok(self.name, "误拒答检查")
        if not any(w in ctx.answer_md for w in REFUSAL_WORDS):
            return Verdict.ok(self.name, "误拒答检查")
        evidence_blob = "\n".join(str(s.get("text", "")) for s in ctx.sources)
        if len(evidence_blob.strip()) >= self.min_evidence_chars:
            return Verdict.fail(
                self.name, "误拒答检查",
                "回答以'没有资料/无法回答'拒绝了用户，但已检索到相关政策/售后资料，"
                "必须基于资料给出带引用的回答，而不是拒绝。",
                evidence={"evidence_chars": len(evidence_blob.strip())})
        return Verdict.ok(self.name, "误拒答检查")


class GoalCoverageGuardrail:
    """Warning: comparisons cover all named products; recommendations cover concerns."""

    name = "goal_coverage"
    severity = "warning"

    def check(self, ctx: GuardrailContext) -> Verdict:
        missing: list[str] = []
        c = _card_dump(ctx.card)
        card_blob = " ".join(_card_products(ctx.card)) + " " + " ".join(
            str(d) for d in c.get("dimensions", []) or [])
        haystack = ctx.answer_md + " " + card_blob

        if ctx.intent == "compare":
            for ent in ctx.entities:
                if ent and ent not in haystack:
                    missing.append(f"对比遗漏商品「{ent}」")

        concerns = []
        q = ctx.structured_query
        if q is not None:
            concerns = list(q.get("concerns", [])) if isinstance(q, dict) \
                else list(getattr(q, "concerns", []))
        for concern in concerns:
            if concern and concern not in ctx.answer_md:
                missing.append(f"未回应在意维度「{concern}」")

        if missing:
            return Verdict.fail(self.name, "目标覆盖检查", "；".join(missing),
                                severity="warning", evidence={"missing": missing})
        return Verdict.ok(self.name, "目标覆盖检查")
