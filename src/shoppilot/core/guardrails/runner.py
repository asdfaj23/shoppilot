"""Guardrail runner: evaluate, optionally repair once, re-evaluate.

Bound by design: at most one repair round (no infinite self-reflection loops).
Repair itself is injected as a callback so the runner never imports an LLM — it
stays deterministic and unit-testable. The rules themselves are pure-Python and
cost **zero** LLM tokens; only the optional repair round spends tokens, which are
reported separately (``extra_tokens``) so the experiment can compare cost.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from shoppilot.core.guardrails.base import (
    GuardrailContext,
    GuardrailReport,
    OutputGuardrail,
    Verdict,
)

# repair_fn(context, failing_report) -> (repaired_context, tokens_spent).
# The callback rebuilds the context (answer and, if it parsed one, a new card),
# keeping the runner itself domain-agnostic.
RepairFn = Callable[[GuardrailContext, GuardrailReport], tuple[GuardrailContext, int]]


@dataclass
class GuardrailResult:
    final_report: GuardrailReport
    initial_report: GuardrailReport
    final_answer_md: str
    final_context: GuardrailContext | None = None
    repaired: bool = False
    repair_rounds: int = 0
    unresolved: bool = False
    extra_tokens: int = 0
    history: list[GuardrailReport] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.final_report.passed


class GuardrailRunner:
    def __init__(self, guardrails: list[OutputGuardrail], tracer=None,
                 on_event=None, max_repair_rounds: int = 1):
        self.guardrails = guardrails
        self.tracer = tracer
        self.on_event = on_event or (lambda *_: None)
        self.max_repair_rounds = max_repair_rounds

    def evaluate(self, ctx: GuardrailContext) -> GuardrailReport:
        """Run every rule deterministically; a broken rule is isolated as a warning."""
        verdicts: list[Verdict] = []
        for g in self.guardrails:
            try:
                verdicts.append(g.check(ctx))
            except Exception as e:  # never let a buggy rule break the serving path
                verdicts.append(Verdict.fail(
                    rule=f"{g.name}::error", name=f"{g.name}（规则自身异常）",
                    violation=f"{type(e).__name__}: {e}", severity="warning"))
        return GuardrailReport(verdicts=verdicts)

    def run(self, ctx: GuardrailContext, repair_fn: RepairFn | None = None) -> GuardrailResult:
        initial = self.evaluate(ctx)
        history = [initial]
        cur_ctx = ctx
        extra_tokens = 0
        rounds = 0
        unresolved = False
        repaired = False

        if initial.passed or repair_fn is None:
            unresolved = not initial.passed
            self._emit(initial, repaired=False, rounds=0, tokens=0)
            return GuardrailResult(
                final_report=initial, initial_report=initial,
                final_answer_md=ctx.answer_md, final_context=ctx,
                repaired=False, repair_rounds=0,
                unresolved=unresolved, extra_tokens=0, history=history)

        for _ in range(self.max_repair_rounds):
            if not history[-1].blockers:
                break
            try:
                new_ctx, tk = repair_fn(cur_ctx, history[-1])
            except Exception as e:
                self.on_event("guardrail_repair_error", {"error": str(e)[:200]})
                unresolved = True
                break
            extra_tokens += tk
            rounds += 1
            repaired = True
            cur_ctx = new_ctx
            new_report = self.evaluate(cur_ctx)
            history.append(new_report)
            if new_report.passed:
                break
        else:
            unresolved = bool(history[-1].blockers)

        final = history[-1]
        unresolved = unresolved or bool(final.blockers)
        result = GuardrailResult(
            final_report=final, initial_report=initial,
            final_answer_md=cur_ctx.answer_md, final_context=cur_ctx,
            repaired=repaired, repair_rounds=rounds, unresolved=unresolved,
            extra_tokens=extra_tokens, history=history)
        self._emit(final, repaired=repaired, rounds=rounds, tokens=extra_tokens,
                   unresolved=unresolved)
        return result

    def _emit(self, report: GuardrailReport, repaired: bool, rounds: int,
              tokens: int, unresolved: bool = False) -> None:
        self.on_event("guardrail", {
            "passed": report.passed, "repaired": repaired, "rounds": rounds,
            "blockers": [v.rule for v in report.blockers],
            "warnings": [v.rule for v in report.warnings],
            "extra_tokens": tokens, "unresolved": unresolved})
        if self.tracer is not None:
            try:
                self.tracer.log(
                    "guardrail", "output_guardrails",
                    report.to_fix_prompt() or "all passed",
                    json.dumps(report.to_trace(), ensure_ascii=False),
                    tokens=tokens)
            except Exception:
                pass
