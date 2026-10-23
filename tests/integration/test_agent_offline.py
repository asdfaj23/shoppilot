"""Fully offline end-to-end agent run.

A ScriptedLLM drives every LLM step (planner -> bounded ReAct tool call ->
synthesis -> guarded composer), while the real tool registry, CSV data,
recommender and deterministic guardrails run for real. No network, no tokens.
"""
from __future__ import annotations

import json

from shoppilot.core.llm import ScriptedLLM
from shoppilot.domains.ecommerce.factory import build_ecommerce_agent

TOOL_CALL = {
    "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "recommend_products",
                     "arguments": json.dumps({"category": "数码配件", "budget_max": 500})}}]
}
PLAN = {
    "query": {"intent": "recommend", "entities": ["蓝牙耳机A"], "category": "数码配件",
              "budget_max": 500, "budget_min": None, "concerns": ["降噪"]},
    "tasks": [
        {"id": 1, "goal": "用 recommend_products 推荐500元内降噪耳机",
         "task_type": "data_query", "depends_on": []},
        {"id": 2, "goal": "汇总推荐理由", "task_type": "synthesis", "depends_on": [1]},
    ],
}
COMPOSE = {
    "answer_md": "为你推荐 **蓝牙耳机A**（¥349），主动降噪表现好且在500元预算内。[1]",
    "card": {"kind": "recommendation", "summary": "500元内降噪首选蓝牙耳机A",
             "items": [{"name": "蓝牙耳机A", "price": 349, "rating": 4.3,
                        "reason": "降噪好、预算内", "review_summary": "好评多", "citation": 1}]},
    "citations": [{"no": 1, "source": "任务1", "heading": "推荐打分",
                   "text": "蓝牙耳机A 349元 降噪好"}],
}


def _shopping_router(messages):
    system = messages[0]["content"]
    if "规划器" in system:
        return PLAN
    if "任务执行者" in system:
        if any(m.get("role") == "tool" for m in messages):
            return {"content": "蓝牙耳机A 349元 降噪好，符合500元预算。"}
        return TOOL_CALL
    if "分析执行者" in system:
        return "综合判断：蓝牙耳机A 是500元内降噪首选。"
    if "成稿者" in system or "修复器" in system:
        return COMPOSE
    return {"answer_md": "（未知阶段）", "card": None}


def test_recommend_runs_end_to_end_offline(tmp_path):
    llm = ScriptedLLM(router=_shopping_router)
    agent = build_ecommerce_agent(
        llm=llm, guardrail_mode="deterministic",
        enable_memory=False, report_dir=tmp_path)
    result = agent.run("500元内降噪耳机推荐")

    assert llm.calls, "the scripted LLM must have driven the run"
    assert result.intent == "recommend"
    assert result.structured is not None
    assert result.structured["kind"] == "recommendation"
    assert "蓝牙耳机A" in result.answer_md
    assert result.tokens > 0
    assert result.guardrail.get("passed") is True
    assert result.guardrail_unresolved is False


def test_out_of_scope_short_circuits_to_refusal(tmp_path):
    def router(messages):
        return {"query": {"intent": "out_of_scope", "entities": [], "category": "",
                          "budget_max": None, "budget_min": None, "concerns": []},
                "tasks": []}

    llm = ScriptedLLM(router=router)
    agent = build_ecommerce_agent(
        llm=llm, guardrail_mode="deterministic",
        enable_memory=False, report_dir=tmp_path)
    result = agent.run("帮我写一段 Python 快速排序算法")
    assert result.intent == "out_of_scope"
    assert "超出" in result.answer_md or "导购" in result.answer_md
