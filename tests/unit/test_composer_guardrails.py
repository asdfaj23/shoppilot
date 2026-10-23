"""End-to-end test of the composer + deterministic guardrail repair loop.

The LLM is a ScriptedLLM, so this runs offline and deterministically. It proves
the central claim: a fabricated citation in the first draft is caught by a
zero-token rule, repaired in exactly one bounded pass, and the final answer is
released only after re-verification.
"""
from __future__ import annotations

from shoppilot.core.guardrails import (
    CitationBoundGuardrail,
    GuardrailRunner,
    StructuredCardGuardrail,
)
from shoppilot.core.llm import ScriptedLLM
from shoppilot.core.types import ShoppingQuery, SubTaskResult
from shoppilot.domains.ecommerce import guardrails as eg
from shoppilot.orchestrator.composer import compose

CARD = {"kind": "recommendation", "summary": "500元内降噪首选",
        "items": [{"name": "蓝牙耳机A", "price": 349, "rating": 4.3,
                   "reason": "降噪好、预算内", "review_summary": "好评多", "citation": 1}]}
CITES = [{"no": 1, "source": "任务1", "heading": "推荐打分", "text": "蓝牙耳机A 349元 降噪好"}]
SOURCES = [
    {"source": "任务1", "heading": "推荐打分", "text": "蓝牙耳机A 349元 降噪好，预算内"},
    {"source": "任务1", "heading": "卖点", "text": "蓝牙耳机A 支持主动降噪"},
]


def _query():
    return ShoppingQuery(intent="recommend", entities=["蓝牙耳机A"],
                         category="数码配件", budget_max=500, concerns=["降噪"],
                         raw_text="500元内降噪耳机推荐")


def _results():
    return {1: SubTaskResult(task_id=1, goal="推荐500元内降噪耳机",
                             task_type="data_query",
                             output="蓝牙耳机A 349元 降噪好，符合预算")}


def _runner(adapter):
    return GuardrailRunner([
        CitationBoundGuardrail(),
        StructuredCardGuardrail(),
        eg.BudgetGuardrail(),
        eg.SourceGroundingGuardrail(adapter.product_names()),
        eg.FalseRefusalGuardrail(),
        eg.GoalCoverageGuardrail(),
    ])


def test_fabricated_citation_triggers_one_bounded_repair(ecom_adapter):
    draft = {
        "answer_md": "推荐 **蓝牙耳机A**（¥349），降噪出色且在预算内。[9]",
        "card": CARD, "citations": CITES,
    }
    repaired = {
        "answer_md": "推荐 **蓝牙耳机A**（¥349），降噪出色且在预算内。[1]",
        "card": CARD, "citations": CITES,
    }
    llm = ScriptedLLM(responses=[draft, repaired])
    result = compose(_query(), _results(), SOURCES, llm=llm, runner=_runner(ecom_adapter))

    assert result.guardrail_repaired is True
    assert result.guardrail_unresolved is False
    assert "[9]" not in result.answer_md
    assert "[1]" in result.answer_md
    assert result.guardrail_extra_tokens == llm.token_per_call
    assert result.tokens == 2 * llm.token_per_call


def test_clean_first_draft_passes_without_repair(ecom_adapter):
    draft = {
        "answer_md": "推荐 **蓝牙耳机A**（¥349），降噪出色且在预算内。[1]",
        "card": CARD, "citations": CITES,
    }
    llm = ScriptedLLM(responses=[draft])
    result = compose(_query(), _results(), SOURCES, llm=llm, runner=_runner(ecom_adapter))

    assert result.guardrail_repaired is False
    assert result.guardrail_unresolved is False
    assert result.guardrail_extra_tokens == 0
    assert result.tokens == llm.token_per_call
