"""Scoring for the ablation — one deterministic ruler applied to every arm.

Key idea: regardless of which *checking* strategy produced a final answer, we
re-score that answer with the same deterministic rules. That way vanilla (no
guardrail), LLM-reflection and this project are compared on identical ground —
we measure what violations *survived*, not what each arm claims about itself.
"""
from __future__ import annotations

from shoppilot.core.guardrails import CitationBoundGuardrail, GuardrailContext
from shoppilot.domains.ecommerce import guardrails as eg


def check_constraints(answer: str, constraints: list[dict]) -> tuple[bool, list[str]]:
    """Keyword constraints (must_contain / must_contain_any / must_not_contain)."""
    fails: list[str] = []
    for c in constraints:
        ctype, value = c.get("type"), c.get("value")
        kws = [value] if isinstance(value, str) else (value or [])
        if ctype == "must_contain":
            for kw in kws:
                if kw not in answer:
                    fails.append(f"缺关键词：{kw}")
        elif ctype == "must_contain_any":
            if not any(kw in answer for kw in kws):
                fails.append(f"缺关键词（任一）：{'/'.join(kws)}")
        elif ctype == "must_not_contain":
            for kw in kws:
                if kw in answer:
                    fails.append(f"出现禁用词：{kw}")
    return (not fails), fails


def _index(sources: list[dict]) -> list[dict]:
    return [{**s, "no": i + 1} for i, s in enumerate(sources or [])]


def rescan_violations(query, answer: str, card: dict | None,
                      sources: list[dict], product_names: list[str]) -> dict:
    """Re-scan a final answer with the four deterministic rules -> violation flags."""
    ctx = GuardrailContext(
        user_input=getattr(query, "raw_text", "") or "",
        intent=getattr(query, "intent", "") or "",
        structured_query=query or {},
        sources=_index(sources), answer_md=answer, card=card)

    checks = {
        "over_budget": eg.BudgetGuardrail(),
        "fabrication": eg.SourceGroundingGuardrail(product_names),
        "citation": CitationBoundGuardrail(),
        "refusal": eg.FalseRefusalGuardrail(),
    }
    flags: dict[str, bool] = {}
    for kind, rule in checks.items():
        v = rule.check(ctx)
        flags[kind] = not v.passed  # a surviving blocker on this rule = a violation
    flags["total"] = sum(flags[k] for k in
                         ("over_budget", "fabrication", "citation", "refusal"))
    return flags


def score_case(adapter, case, result, query) -> dict:
    """Score one (case, strategy-run) -> flat row for the summary table."""
    answer = result.answer_md or ""
    product_names = adapter.product_names()
    viols = rescan_violations(query, answer, result.structured, result.sources, product_names)

    constraints_ok, fails = check_constraints(answer, case.get("constraints", []))
    expected = (case.get("meta") or {}).get("expected_behavior", "answer")
    behavior_ok = (result.intent == "out_of_scope") == (expected == "refuse")

    task_passed = (not result.degradations) and constraints_ok and behavior_ok

    return {
        "id": case["id"],
        "prompt": case["prompt"],
        "task_passed": task_passed,
        "over_budget": viols["over_budget"],
        "fabrication": viols["fabrication"],
        "citation": viols["citation"],
        "refusal": viols["refusal"],
        "violation_total": viols["total"],
        "extra_tokens": result.guardrail_extra_tokens,
        "tokens": result.tokens,
        "duration_s": round(result.duration_s, 2),
        "repaired": result.guardrail_repaired,
        "note": "；".join(fails[:3]),
    }
