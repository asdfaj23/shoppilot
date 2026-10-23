"""Domain-agnostic built-in guardrails.

These two rules rely only on the generic :class:`GuardrailContext` (citations and
the optional structured card), so they work for any domain:

* :class:`CitationBoundGuardrail` — every ``[n]`` in the answer must resolve to a
  retrieved source. A dangling number is a fabricated citation.
* :class:`StructuredCardGuardrail` — intents that require a structured result must
  actually produce one; prevents the model writing prose when the UI needs a card.
"""
from __future__ import annotations

from shoppilot.core.guardrails.base import (
    GuardrailContext,
    Verdict,
)


class CitationBoundGuardrail:
    """Reject answers that cite source numbers that were never retrieved."""

    name = "citation_bound"
    severity = "blocker"

    def check(self, ctx: GuardrailContext) -> Verdict:
        cited = ctx.cited_numbers
        if not cited:
            # Whether an answer *must* cite is a domain decision; here we only
            # guard against fabricating a citation number that cannot exist.
            return Verdict.ok(self.name, "引用越界检查")
        known = ctx.known_source_nos
        out_of_range = [n for n in cited if n not in known]
        if out_of_range:
            return Verdict.fail(
                self.name, "引用越界检查",
                f"回答引用了来源 {sorted(set(out_of_range))}，但实际只检索到编号 "
                f"{sorted(known) or '（无）'}，属于凭空编号，必须删除或改成真实来源。",
                evidence={"cited": cited, "known": sorted(known),
                          "out_of_range": sorted(set(out_of_range))})
        return Verdict.ok(self.name, "引用越界检查",
                          {"cited": sorted(set(cited))})


class StructuredCardGuardrail:
    """Ensure intents that should emit a structured card actually do so."""

    name = "structured_card"
    severity = "blocker"

    def __init__(self, expected_card_intents: set[str] | None = None):
        self.expected_card_intents = expected_card_intents or {
            "recommend", "compare", "research"}

    def check(self, ctx: GuardrailContext) -> Verdict:
        if ctx.intent not in self.expected_card_intents:
            return Verdict.ok(self.name, "结构化结果检查")
        if ctx.card is None:
            return Verdict.fail(
                self.name, "结构化结果检查",
                f"意图为 {ctx.intent}，但没有产出可供前端渲染的结构化卡片，"
                "请按工具返回的真实数据生成推荐/对比/研究卡片。",
                evidence={"intent": ctx.intent})
        return Verdict.ok(self.name, "结构化结果检查", {"intent": ctx.intent})
