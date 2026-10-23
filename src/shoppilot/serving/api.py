"""FastAPI serving app.

Run with ``shoppilot serve`` or ``uvicorn shoppilot.serving.api:app``.
An LLM provider can be injected (``create_app(llm=...)``) so the API is
fully testable offline with a ScriptedLLM.
"""
from __future__ import annotations

from functools import cache

from fastapi import FastAPI

from shoppilot import __version__
from shoppilot.core.llm import build_default_llm
from shoppilot.domains.ecommerce.factory import build_ecommerce_agent
from shoppilot.serving.schemas import (
    AskRequest,
    AskResponse,
    GuardrailSummary,
    HealthResponse,
)


def create_app(llm=None) -> FastAPI:
    app = FastAPI(
        title="ShopPilot",
        version=__version__,
        description="电商导购智能体：确定性输出护栏 + 可插拔域适配 + 有界 ReAct。",
    )

    @cache
    def _agent(guardrail_mode: str):
        return build_ecommerce_agent(
            llm=llm or build_default_llm(),
            guardrail_mode=guardrail_mode, enable_memory=False)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health():
        return HealthResponse(status="ok", version=__version__)

    @app.post("/api/ask", response_model=AskResponse, tags=["agent"])
    def ask(req: AskRequest):
        result = _agent(req.guardrail_mode).run(req.query)
        g = result.guardrail
        summary = GuardrailSummary(
            passed=bool(g.get("passed")),
            blockers=len(g.get("blockers", [])),
            warnings=len(g.get("warnings", [])),
            repaired=result.guardrail_repaired,
            repair_rounds=result.guardrail_repair_rounds,
            unresolved=result.guardrail_unresolved,
            extra_tokens=result.guardrail_extra_tokens,
            violations=[b.get("violation", "") for b in g.get("blockers", [])],
        )
        return AskResponse(
            intent=result.intent,
            answer_md=result.answer_md,
            structured=result.structured,
            citations=[c.model_dump() for c in result.citations],
            guardrail=summary,
            tokens=result.tokens,
            duration_s=round(result.duration_s, 3),
            degraded=bool(result.degradations),
        )

    return app


app = create_app()
