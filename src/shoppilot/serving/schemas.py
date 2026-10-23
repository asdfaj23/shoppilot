"""Pydantic request/response schemas for the HTTP API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    query: str = Field(..., description="用户的导购问题")
    guardrail_mode: str = Field("deterministic",
                                description="deterministic | none | llm_reflection")
    enable_memory: bool = Field(False, description="是否启用跨轮记忆")


class GuardrailSummary(BaseModel):
    passed: bool
    blockers: int
    warnings: int
    repaired: bool
    repair_rounds: int
    unresolved: bool
    extra_tokens: int
    violations: list[str] = Field(default_factory=list)


class AskResponse(BaseModel):
    intent: str
    answer_md: str
    structured: dict[str, Any] | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    guardrail: GuardrailSummary
    tokens: int
    duration_s: float
    degraded: bool = False


class HealthResponse(BaseModel):
    status: str
    version: str
