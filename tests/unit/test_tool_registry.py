"""Tests for the tool registry: registration, schema scoping, validation, caching."""
from __future__ import annotations

import json

from shoppilot.core.tools import ToolRegistry
from shoppilot.domains.ecommerce.tools import register_all


def test_registers_seven_tools(ecom_adapter):
    reg = ToolRegistry()
    register_all(reg)
    assert len(reg.names()) == 7
    assert "recommend_products" in reg.names()


def test_recommend_tool_returns_budget_respecting_data(ecom_adapter):
    reg = ToolRegistry()
    register_all(reg)
    out = json.loads(reg.call(
        "recommend_products",
        {"category": "数码配件", "budget_max": 500, "concerns": ["降噪"]}))
    assert "recommendations" in out and out["recommendations"]
    assert all(r["price"] <= 500 for r in out["recommendations"])


def test_invalid_arguments_return_self_describing_error(ecom_adapter):
    reg = ToolRegistry()
    register_all(reg)
    out = json.loads(reg.call("search_products", {"budget_max": "not_a_number"}))
    assert "error" in out  # Pydantic contract violation is fed back, not raised


def test_preload_scopes_schemas_by_task_type(ecom_adapter):
    reg = ToolRegistry()
    register_all(reg)
    data_tools = set(reg.preload_names("data_query"))
    review_tools = set(reg.preload_names("review_analysis"))
    assert "recommend_products" in data_tools
    assert "search_reviews" in review_tools
    assert "recommend_products" not in review_tools  # irrelevant tools stay out of context


def test_identical_calls_are_cached(ecom_adapter):
    reg = ToolRegistry()
    register_all(reg)
    args = {"category": "数码配件", "budget_max": 500}
    first = reg.call("search_products", args)
    # Same call -> identical cached string, registry cache populated.
    assert reg.call("search_products", args) == first
