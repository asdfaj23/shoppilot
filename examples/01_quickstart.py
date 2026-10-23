"""Offline quickstart — the full ShopPilot pipeline with **zero LLM tokens**.

A ScriptedLLM (deterministic test double) replaces the real model, so the whole
planner -> bounded ReAct -> real tool -> composer -> guardrail pipeline runs
without an API key or network. The tools run against the real bundled CSV
catalog. This demonstrates the project's central claim: a zero-token
deterministic guardrail catches a fabricated citation and repairs it in one
bounded pass, while a clean draft passes untouched.

For a real model, configure .env (SHOPPILOT_API_KEY / SHOPPILOT_BASE_URL /
SHOPPILOT_MODEL) and run ``shoppilot ask "你的问题"``.
"""
from __future__ import annotations

import json

from shoppilot.core.llm import ScriptedLLM
from shoppilot.domains.ecommerce.factory import build_ecommerce_agent

PLAN = {
    "query": {"intent": "recommend", "entities": ["蓝牙耳机A"], "category": "数码配件",
              "budget_max": 500, "budget_min": None, "concerns": ["降噪"]},
    "tasks": [
        {"id": 1, "goal": "用 recommend_products 推荐500元内降噪耳机",
         "task_type": "data_query", "depends_on": []},
        {"id": 2, "goal": "汇总推荐理由", "task_type": "synthesis", "depends_on": [1]},
    ],
}
TOOL_CALL = {
    "tool_calls": [{
        "id": "call_1", "type": "function",
        "function": {"name": "recommend_products",
                     "arguments": json.dumps({"category": "数码配件", "budget_max": 500})}}]
}
CARD = {"kind": "recommendation", "summary": "500元内降噪首选蓝牙耳机A",
        "items": [{"name": "蓝牙耳机A", "price": 349, "rating": 4.3,
                   "reason": "降噪好、预算内", "review_summary": "好评多", "citation": 1}]}
CITES = [{"no": 1, "source": "任务1", "heading": "推荐打分", "text": "蓝牙耳机A 349元 降噪好"}]


def make_router(compose_draft, compose_repair=None):
    """Route scripted responses by pipeline stage; optionally repair a bad draft."""
    state = {"draft": compose_draft}

    def router(messages):
        system = messages[0]["content"]
        if "规划器" in system:
            return PLAN
        if "任务执行者" in system:
            if any(m.get("role") == "tool" for m in messages):
                return {"content": "蓝牙耳机A 349元 降噪好，符合500元预算。"}
            return TOOL_CALL
        if "分析执行者" in system:
            return "综合判断：蓝牙耳机A 是500元内降噪首选。"
        if "修复器" in system:
            return compose_repair or compose_draft
        if "成稿者" in system:
            return state["draft"]
        return {"answer_md": "（未知阶段）", "card": None}

    return router


def run_case(title, draft, repair=None):
    llm = ScriptedLLM(router=make_router(draft, repair))
    agent = build_ecommerce_agent(llm=llm, guardrail_mode="deterministic",
                                  enable_memory=False)
    r = agent.run("500元内降噪耳机推荐")
    print(f"\n{'='*70}\n{title}\n{'='*70}")
    print(r.answer_md)
    print(f"\n护栏通过      : {r.guardrail.get('passed')}")
    print(f"是否触发修复  : {r.guardrail_repaired}（修复轮次 {r.guardrail_repair_rounds}）")
    print(f"护栏额外 tokens: {r.guardrail_extra_tokens}（确定性规则本身 0 token）")
    print(f"违反项        : {[b['violation'] for b in r.guardrail.get('blockers', [])] or '无'}")
    print(f"总 LLM tokens : {r.tokens}（来自 ScriptedLLM，未调用真实模型）")


def main():
    clean = {
        "answer_md": "为你推荐 **蓝牙耳机A**（¥349），主动降噪表现好且在500元预算内。[1]",
        "card": CARD, "citations": CITES,
    }
    # Case 1: a clean draft passes untouched.
    run_case("Case 1 · 干净初稿（无违规）", clean)

    # Case 2: the first draft cites a non-existent source [9]; the guardrail
    # catches it, triggers one bounded repair, and the second draft is released.
    bad = {
        "answer_md": "为你推荐 **蓝牙耳机A**（¥349），主动降噪表现好。[9]",
        "card": CARD, "citations": CITES,
    }
    fixed = {
        "answer_md": "为你推荐 **蓝牙耳机A**（¥349），主动降噪表现好且在500元预算内。[1]",
        "card": CARD, "citations": CITES,
    }
    run_case("Case 2 · 初稿越界引用 [9]（被拦截并修复）", bad, fixed)


if __name__ == "__main__":
    main()
