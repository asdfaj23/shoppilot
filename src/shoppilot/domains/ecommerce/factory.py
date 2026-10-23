"""E-commerce composition root.

This is the *single* place that wires the domain-agnostic :class:`ShoppingAgent`
to concrete e-commerce pieces: the CSV adapter, the seven tools, the RAG
knowledge function, and the guardrail set. Nothing in ``core`` /
``orchestrator`` imports this module — the dependency points inward.

``guardrail_mode`` selects the policy used in the ablation experiment:
  * ``"deterministic"``  (default): the six zero-token programmatic rules;
  * ``"none"`` / ``"vanilla"``: no output guardrails (raw baseline);
  * ``"llm_reflection"``: a single LLM self-reflection guardrail (token-spending
    control, used as the Reflexion-style comparison arm).
"""
from __future__ import annotations

from functools import partial
from pathlib import Path

from shoppilot.core.guardrails.builtin import (
    CitationBoundGuardrail,
    StructuredCardGuardrail,
)
from shoppilot.core.guardrails.llm_reflection import (
    CriticAgentGuardrail,
    LLMReflectionGuardrail,
)
from shoppilot.core.guardrails.runner import GuardrailRunner
from shoppilot.core.llm import get_llm
from shoppilot.core.settings import Settings
from shoppilot.core.settings import settings as default_settings
from shoppilot.core.tools import ToolRegistry
from shoppilot.domains.ecommerce import adapter as adapter_mod
from shoppilot.domains.ecommerce import guardrails as eg
from shoppilot.domains.ecommerce.adapter import CsvEcommerceAdapter
from shoppilot.domains.ecommerce.rag import rag_answer
from shoppilot.domains.ecommerce.tools import register_all
from shoppilot.orchestrator.agent import ShoppingAgent


def build_ecommerce_guardrails(adapter, llm=None, mode: str = "deterministic"):
    """Construct the guardrail list for the chosen policy."""
    if mode in ("none", "vanilla"):
        return []
    if mode == "llm_reflection":
        return [LLMReflectionGuardrail(llm or get_llm())]
    if mode == "multi_agent":
        return [CriticAgentGuardrail(llm or get_llm())]
    names = adapter.product_names()
    return [
        CitationBoundGuardrail(),
        StructuredCardGuardrail(),
        eg.BudgetGuardrail(),
        eg.SourceGroundingGuardrail(names),
        eg.FalseRefusalGuardrail(),
        eg.GoalCoverageGuardrail(),
    ]


def build_ecommerce_agent(llm=None, settings: Settings | None = None,
                          guardrail_mode: str = "deterministic",
                          on_event=None, enable_memory: bool = True,
                          report_dir: str | Path | None = None,
                          data_dir: str | Path | None = None) -> ShoppingAgent:
    """Assemble a ready-to-run e-commerce ShoppingAgent."""
    cfg = settings or default_settings
    ecom_dir = Path(data_dir) if data_dir is not None else Path(cfg.data_dir) / "ecommerce"
    adapter = CsvEcommerceAdapter(ecom_dir)
    adapter_mod.configure(adapter)

    registry = ToolRegistry()
    register_all(registry)

    # RAG inherits the same injected LLM (a ScriptedLLM in tests => fully offline).
    knowledge_fn = partial(rag_answer, llm=llm)

    if guardrail_mode in ("llm_reflection", "multi_agent") and llm is None:
        llm = get_llm()
    guardrails = build_ecommerce_guardrails(adapter, llm=llm, mode=guardrail_mode)
    runner = GuardrailRunner(guardrails, on_event=on_event) if guardrails else None

    return ShoppingAgent(
        llm=llm, registry=registry, catalog=adapter, knowledge_fn=knowledge_fn,
        guardrail_runner=runner, on_event=on_event,
        enable_memory=enable_memory, report_dir=report_dir)
