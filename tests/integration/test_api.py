"""Offline API tests using FastAPI's TestClient with an injected ScriptedLLM."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from shoppilot.core.llm import ScriptedLLM
from shoppilot.serving.api import create_app

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


def _router(messages):
    system = messages[0]["content"]
    if "规划器" in system:
        return PLAN
    if "任务执行者" in system:
        if any(m.get("role") == "tool" for m in messages):
            return {"content": "蓝牙耳机A 349元 降噪好，符合500元预算。"}
        return TOOL_CALL
    if "分析执行者" in system:
        return "综合判断：蓝牙耳机A 是500元内降噪首选。"
    return COMPOSE


def _client():
    return TestClient(create_app(llm=ScriptedLLM(router=_router)))


def test_health_reports_version():
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and r.json()["version"]


def test_ask_returns_answer_and_guardrail_verdict():
    r = _client().post("/api/ask", json={
        "query": "500元内降噪耳机推荐", "guardrail_mode": "deterministic"})
    assert r.status_code == 200
    body = r.json()
    assert body["intent"] == "recommend"
    assert "蓝牙耳机A" in body["answer_md"]
    assert body["guardrail"]["passed"] is True
    assert body["guardrail"]["extra_tokens"] == 0
    assert body["tokens"] > 0
