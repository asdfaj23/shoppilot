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
from shoppilot.agents.planner import make_plan, refuse_looks_wrong, replan, unknown_entities
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

    # ① Planner：意图路由 + 结构化抽取 + 拆 DAG（规划 → 中间 ReAct → 交付前 Reflection 的第一段）
    # 注入：用户画像（长期记忆）+ Session State（结构化任务状态，多轮追问消解指代）+ 商品池（防编造）
    query, plan, tokens = make_plan(user_text, profile_block=memory.prefs.render(),
                                    state_block=memory.render_state(), on_event=on_event)
    result.intent = query.intent if query else ""
    result.tokens += tokens
    tracer.log("plan", "Planner", user_text,
               (plan.model_dump_json() if plan else "out_of_scope"), tokens=tokens)

    # ①.5 防误拒复核：Planner 判 out_of_scope 但文本明显是导购请求 → 重问一次（确定性规则，零 token）
    if plan is None and refuse_looks_wrong(user_text):
        on_event("refuse_recheck", {"first": result.intent})
        query, plan, tokens2 = make_plan(user_text + "\n（系统提示：若与导购相关请勿判为 out_of_scope，"
                                                   "商品池内品类务必正常拆任务）",
                                         profile_block=memory.prefs.render(),
                                         state_block=memory.render_state(), on_event=on_event)
        result.intent = query.intent if query else result.intent
        result.tokens += tokens2
        tracer.log("plan", "PlannerRecheck", user_text,
                   plan.model_dump_json() if plan else "out_of_scope(confirmed)", tokens=tokens2)

    # ①.6 库外实体确定性闸：用户点名的型号不在商品池（如"蓝牙耳机Z"）→ 重问一次，
    # 要求 Planner 按 out_of_scope 处理或明确说明库外（防"编造库外型号参数"）
    if plan is not None and query.entities and unknown_entities(query):
        unknown = unknown_entities(query)
        on_event("pool_recheck", {"unknown": unknown})
        query, plan, tokens2 = make_plan(
            user_text + f"\n（系统提示：{unknown} 不在商品池中，按规则1判 out_of_scope，"
                        "或在回答中明确说明该商品查询不到，禁止编造其价格与参数）",
            profile_block=memory.prefs.render(),
            state_block=memory.render_state(), on_event=on_event)
        result.intent = query.intent if query else result.intent
        result.tokens += tokens2
        tracer.log("plan", "PlannerPoolRecheck", user_text,
                   plan.model_dump_json() if plan else "out_of_scope", tokens=tokens2)

    if plan is None:  # ② 超范围拒答短路（不拆任务、不调工具）
        result.answer_md = OUT_OF_SCOPE_ANSWER
        result.duration_s = time.time() - t0
        tracer.log("refuse", "OutOfScope", user_text, result.answer_md)
        _persist(memory, user_text, result)
        return result

    # ③ Dispatcher：DAG 分层并发（同层并行，工具按 task_type 预加载）
    results, sources = execute_plan(plan, registry, tracer, preload=preload_tools,
                                    on_event=on_event)

    # ③.5 Replan（计划可更新）：子任务降级 → 带失败证据重拆一次补救任务，再执行一轮。
    # 呼应"规划执行不是机械执行到底，而是把计划当作可更新的任务清单"（kamacoder Agent 范式）。
    failed = [r for r in results.values() if r.degraded]
    if failed:
        on_event("replan_start", {"failed": [r.task_id for r in failed]})
        patch, tokens3 = replan(query, failed, results,
                                start_id=max(results) + 1, on_event=on_event)
        result.tokens += tokens3
        tracer.log("replan", "Replanner", f"failed={[r.task_id for r in failed]}",
                   patch.model_dump_json() if patch else "no-patch", tokens=tokens3)
        if patch:
            patch_results, patch_sources = execute_plan(patch, registry, tracer,
                                                        preload=preload_tools, on_event=on_event)
            results.update(patch_results)
            sources.extend(patch_sources)

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
    # Session State 回写：结构化任务状态（多轮追问的上下文，优于纯聊天记录）
    if result.structured:
        findings = result.structured.get("summary") or result.structured.get("conclusion") or ""
    else:
        findings = result.answer_md[:200]
    memory.update_state(user_text, result.intent, result.tasks, findings)


def _issues_digest(result: TeamResult) -> str:
    return "；".join(result.reflect_issues) or "无问题"
