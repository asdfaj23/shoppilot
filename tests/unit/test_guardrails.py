"""Unit tests for the deterministic output guardrails (the project's core claim).

Every rule is tested with hand-built contexts — no LLM, no network, fully
deterministic. This is what makes the guardrails verifiable in the way an LLM
self-reflection baseline cannot be.
"""
from __future__ import annotations

from dataclasses import replace

from shoppilot.core.guardrails import (
    CitationBoundGuardrail,
    GuardrailContext,
    GuardrailRunner,
    StructuredCardGuardrail,
    Verdict,
)
from shoppilot.domains.ecommerce.guardrails import (
    BudgetGuardrail,
    FalseRefusalGuardrail,
    GoalCoverageGuardrail,
    SourceGroundingGuardrail,
)

SRC = [
    {"no": 1, "source": "任务1", "heading": "推荐打分", "text": "蓝牙耳机A 349元 降噪好"},
    {"no": 2, "source": "任务2", "heading": "口碑", "text": "蓝牙耳机F 899元 音质强"},
]


def make_ctx(**kw) -> GuardrailContext:
    base = dict(user_input="", intent="", structured_query={}, sources=[],
                answer_md="", card=None)
    base.update(kw)
    return GuardrailContext(**base)


# ----------------------------- Citation bound -----------------------------
def test_citation_bound_flags_dangling_number():
    v = CitationBoundGuardrail().check(make_ctx(answer_md="结论如下 [9]", sources=SRC))
    assert not v.passed and v.severity == "blocker"
    assert 9 in v.evidence["out_of_range"]


def test_citation_bound_accepts_known_numbers():
    assert CitationBoundGuardrail().check(
        make_ctx(answer_md="见 [1] 与 [2]", sources=SRC)).passed


def test_citation_bound_no_citation_is_not_a_violation():
    assert CitationBoundGuardrail().check(make_ctx(answer_md="无引用纯陈述", sources=SRC)).passed


# ------------------------------- Budget -----------------------------------
def test_budget_blocks_over_price_card():
    card = {"kind": "recommendation", "items": [{"name": "蓝牙耳机F", "price": 899}]}
    v = BudgetGuardrail().check(
        make_ctx(intent="recommend", structured_query={"budget_max": 500}, card=card))
    assert not v.passed and "蓝牙耳机F" in v.violation


def test_budget_passes_within_cap():
    card = {"kind": "recommendation", "items": [{"name": "蓝牙耳机A", "price": 349}]}
    assert BudgetGuardrail().check(
        make_ctx(structured_query={"budget_max": 500}, card=card)).passed


def test_budget_without_cap_skips():
    card = {"kind": "recommendation", "items": [{"name": "x", "price": 99999}]}
    assert BudgetGuardrail().check(make_ctx(card=card)).passed


# ----------------------------- Source grounding ----------------------------
def test_grounding_blocks_out_of_catalogue_product():
    g = SourceGroundingGuardrail(["蓝牙耳机A", "蓝牙耳机F"])
    card = {"kind": "recommendation", "items": [{"name": "幻夜手机Z", "price": 1}]}
    v = g.check(make_ctx(card=card, sources=SRC))
    assert not v.passed
    assert "幻夜手机Z" in v.evidence["out_of_catalogue"]


def test_grounding_passes_for_retrieved_in_catalogue_product():
    g = SourceGroundingGuardrail(["蓝牙耳机A", "蓝牙耳机F"])
    card = {"kind": "recommendation", "items": [{"name": "蓝牙耳机A"}]}
    assert g.check(make_ctx(card=card, sources=SRC)).passed


# ----------------------------- False refusal ------------------------------
def test_false_refusal_blocked_when_evidence_present():
    v = FalseRefusalGuardrail().check(
        make_ctx(intent="after_sales", answer_md="抱歉，知识库中没有相关资料，无法回答。",
                 sources=SRC))
    assert not v.passed


def test_false_refusal_ignored_for_non_service_intents():
    assert FalseRefusalGuardrail().check(
        make_ctx(intent="recommend", answer_md="无法回答", sources=SRC)).passed


# ----------------------------- Goal coverage ------------------------------
def test_goal_coverage_warns_on_missing_compare_subject():
    v = GoalCoverageGuardrail().check(make_ctx(
        intent="compare",
        structured_query={"entities": ["蓝牙耳机A", "蓝牙耳机F"], "concerns": []},
        answer_md="蓝牙耳机A 更好",
        card={"kind": "compare", "subjects": ["蓝牙耳机A"]}))
    assert not v.passed and v.severity == "warning"


def test_goal_coverage_passes_when_all_subjects_covered():
    assert GoalCoverageGuardrail().check(make_ctx(
        intent="compare",
        structured_query={"entities": ["蓝牙耳机A", "蓝牙耳机F"], "concerns": []},
        answer_md="蓝牙耳机A 和 蓝牙耳机F 各有优劣",
        card={"kind": "compare", "subjects": ["蓝牙耳机A", "蓝牙耳机F"]})).passed


# ----------------------------- Structured card -----------------------------
def test_missing_card_for_recommend_intent_is_blocker():
    assert not StructuredCardGuardrail().check(
        make_ctx(intent="recommend", card=None)).passed


def test_structured_card_not_required_for_after_sales():
    assert StructuredCardGuardrail().check(
        make_ctx(intent="after_sales", card=None)).passed


# ------------------------------- Runner ------------------------------------
class _ToggleGuardrail:
    """Fails on the first evaluation, passes on the second (after a repair)."""
    name = "toggle"
    severity = "blocker"

    def __init__(self):
        self.calls = 0

    def check(self, ctx):
        self.calls += 1
        if self.calls == 1:
            return Verdict.fail("toggle", "开关规则", "初稿有问题")
        return Verdict.ok("toggle", "开关规则")


class _AlwaysFail:
    name = "always"
    severity = "blocker"

    def check(self, ctx):
        return Verdict.fail("always", "永远失败规则", "修不好")


def test_runner_repairs_once_then_rechecks_and_passes():
    g = _ToggleGuardrail()
    runner = GuardrailRunner([g])

    def repair(c, report):
        return replace(c, answer_md="已修正的合规回答"), 120

    res = runner.run(make_ctx(answer_md="有问题的回答"), repair)
    assert res.passed and res.repaired
    assert res.repair_rounds == 1 and res.extra_tokens == 120
    assert res.final_answer_md == "已修正的合规回答"
    assert g.calls == 2  # evaluated once before + once after repair


def test_runner_bounds_repair_to_single_round_and_marks_unresolved():
    runner = GuardrailRunner([_AlwaysFail()])

    def repair(c, report):
        return replace(c, answer_md=c.answer_md + "（再试）"), 50

    res = runner.run(make_ctx(answer_md="x"), repair)
    assert res.unresolved and res.repaired
    assert res.repair_rounds == 1  # never loops forever


def test_runner_without_repair_fn_marks_unresolved_without_spending_tokens():
    res = GuardrailRunner([_AlwaysFail()]).run(make_ctx(answer_md="x"))
    assert res.unresolved and not res.repaired and res.extra_tokens == 0
