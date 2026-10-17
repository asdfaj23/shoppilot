"""
ShopPilot agents/team.py · run_team 总装（⑦ Orchestrator 唯一对外入口）
================================================================================
全链路：记忆读取 → Planner（意图路由+拆DAG）→ out_of_scope 拒答短路 →
Dispatcher 分层并发 → Aggregator（仲裁+引用+结构化卡）→ Reflector（自检+修复）→
记忆回写（会话历史 + 用户画像，画像更新返回"学到了什么"供前端展示——可解释记忆）。
前端（app.py）、benchmark.py、MCP 之外的任何调用方都只 import 这一个函数。
"""
from __future__ import annotations

import time

from shoppilot.agents.aggregator import aggregate
from shoppilot.agents.dispatcher import execute_plan
from shoppilot.agents.planner import make_plan
from shoppilot.agents.reflector import reflect_and_repair
from shoppilot.domains.ecommerce.tools import registry
from shoppilot.harness.memory import MemoryManager
from shoppilot.harness.observability import Tracer
from shoppilot.models import TeamResult

OUT_OF_SCOPE_ANSWER = (
    "抱歉，这个问题超出了我的导购职责范围。\n\n"
    "我只能帮你：**选购推荐**（预算内选品）、**商品对比**、**口碑解读**（差评归因/避坑）、"
    "**售后问答**（保修/退换货）。商品池覆盖：蓝牙耳机A/F、充电宝B、手机壳C、智能手表D、"
    "数据线E、机械键盘G、显示器H、路由器I、智能音箱J。换一个问题试试？"
)


def run_team(user_text: str, session_id: str = "default", user_id: str = "default",
             on_event=None, preload_tools: bool = True) -> TeamResult:
    """
    user_text: 用户输入；session_id/user_id: 多会话与画像的持久化键。
    on_event: callable(etype, payload) 事件回调（前端 st.status 实时渲染）。
    preload_tools: False 时全量注入工具 schema（benchmark 对照组）。
    """
    on_event = on_event or (lambda *_: None)
    t0 = time.time()
    tracer = Tracer("shoppilot", on_event=on_event)
    memory = MemoryManager(session_id, user_id)
    degradations: list[str] = []
    result = TeamResult()

    # ① Planner：意图路由 + 结构化抽取 + 拆 DAG
    query, plan, tokens = make_plan(user_text, profile_block=memory.prefs.render(),
                                    on_event=on_event)
    result.intent = query.intent if query else ""
    result.tokens += tokens
    tracer.log("plan", "Planner", user_text,
               (plan.model_dump_json() if plan else "out_of_scope"), tokens=tokens)

    if plan is None:  # ② 超范围拒答短路（不拆任务、不调工具）
        result.answer_md = OUT_OF_SCOPE_ANSWER
        result.duration_s = time.time() - t0
        tracer.log("refuse", "OutOfScope", user_text, result.answer_md)
        _persist(memory, user_text, result)
        return result

    # ③ Dispatcher：DAG 分层并发（同层并行，工具按 task_type 预加载）
    results, sources = execute_plan(plan, registry, tracer, preload=preload_tools,
                                    on_event=on_event)
    result.tasks = list(results.values())
    result.tokens += sum(r.tokens for r in results.values())
    for r in results.values():
        if r.degraded:
            degradations.append(f"任务{r.task_id}（{r.task_type}）降级：{r.output[:60]}")

    # ④ Aggregator：冲突仲裁 + 强制引用 + 结构化卡
    answer_md, card, citations, tokens = aggregate(query, results, sources, on_event=on_event)
    result.answer_md, result.structured, result.citations = answer_md, card, citations
    result.tokens += tokens
    tracer.log("aggregate", "Aggregator", query.raw_text, answer_md[:300], tokens=tokens)

    # ⑤ Reflector：四项确定性自检 + 一次修复
    result = reflect_and_repair(query, result, sources, on_event=on_event)
    tracer.log("reflect", "Reflector", _issues_digest(result),
               "repaired" if result.reflect_issues and result.reflect_issues[0].startswith("已修复")
               else "checked")

    # ⑥ 记忆回写：会话历史 + 用户画像（可解释：返回学到了什么）
    result.learned = memory.prefs.observe(query)
    _persist(memory, user_text, result)

    result.duration_s = time.time() - t0
    result.degradations = degradations
    result.trace_file = tracer.summary()["trace_file"]
    on_event("done", {"tokens": result.tokens, "duration_s": round(result.duration_s, 1)})
    return result


def _persist(memory: MemoryManager, user_text: str, result: TeamResult) -> None:
    memory.add_message("user", user_text)
    memory.add_message("assistant", result.answer_md, tokens=result.tokens)


def _issues_digest(result: TeamResult) -> str:
    return "；".join(result.reflect_issues) or "无问题"
