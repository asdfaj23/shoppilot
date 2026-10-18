"""
ShopPilot executor.py · 工具执行：DAG 分层并发 + ReAct 有界循环（合并原 base/dispatcher）
================================================================================
两类子执行器：
  - ToolAgent：带工具跑有界 ReAct（步数/token/成本三重预算、并行 tool_calls、重试退避、超时降级）
  - knowledge_qa：直接走 RAG 问答（Function tools 对 RAG 无意义）
调度：Plan.layers() 拓扑分层，同层 ThreadPoolExecutor 并发；同 goal 结果缓存；失败降级不炸链路。
工具按 task_type 预加载：data/review 类任务只拿到相关工具的 schema，无关工具不进上下文。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from shoppilot.harness.loop import react_loop
from shoppilot.harness.tools import ToolRegistry
from shoppilot.models import Plan, SubTask, SubTaskResult

SYNTH_SYSTEM = ("你是导购分析执行者。基于已知信息完成分析，禁止编造数据与数字；"
                "信息不足就明确说'信息不足'。结论用中文，不超过 200 字。")


class ToolAgent:
    def __init__(self, registry: ToolRegistry, tool_names: list[str], on_event=None):
        from shoppilot.llm import get_llm

        self.llm = get_llm()
        self.registry = registry
        self.tool_names = tool_names  # None = 全量注入
        self.on_event = on_event
        self.system = ("你是导购任务执行者。优先调用工具拿真实数据，禁止编造数字与商品信息。"
                       "结论用中文，200 字内，关键数字保留原值。")

    def run(self, goal: str, context: str = "") -> tuple[str, int, list[dict], bool]:
        blocks = f"[任务]\n{goal}" + (f"\n\n[已知信息]\n{context}" if context else "")
        r = react_loop(self.llm, self.system, blocks, self.registry,
                       tool_names=self.tool_names, on_event=self.on_event)
        return r.answer, r.tokens, r.trace_summary(), r.degraded


def _run_one(task: SubTask, context: str, registry: ToolRegistry, preload: bool,
             tracer, on_event) -> tuple[SubTaskResult, list[dict]]:
    t0 = time.time()
    sources: list[dict] = []
    on_event("task_start", {"id": task.id, "task_type": task.task_type, "goal": task.goal})
    try:
        if task.task_type in ("data_query", "review_analysis"):
            names = registry.preload_names(task.task_type) if preload else registry.names()
            answer, tokens, trace, degraded = ToolAgent(registry, names, on_event).run(task.goal, context)
            sources.append({"source": f"任务{task.id}", "heading": task.goal[:40],
                            "text": answer[:120]})
        elif task.task_type == "knowledge_qa":
            from shoppilot.rag.rag_lib import rag_answer

            r = rag_answer(task.goal)
            cites = "；".join(f"[{c['no']}] {c['source']}|{c['heading']}" for c in r["citations"])
            answer = r["answer"] + (f"\n（来源：{cites}）" if cites else "")
            tokens, trace, degraded = r["tokens"], [], False
            sources.extend(r["citations"])
            sources.append({"source": f"任务{task.id}", "heading": task.goal[:40],
                            "text": r["answer"][:120]})
        else:  # synthesis：纯 LLM，不给工具
            from shoppilot.llm import get_llm

            text, tokens = get_llm().chat_text([
                {"role": "system", "content": SYNTH_SYSTEM},
                {"role": "user", "content": f"[任务]\n{task.goal}\n\n[已知信息]\n{context or '（无）'}"}])
            answer, trace, degraded = text, [], False
            sources.append({"source": f"任务{task.id}", "heading": task.goal[:40], "text": text[:120]})
    except Exception as e:  # 失败降级
        answer, tokens, trace, degraded = f"（子任务失败已降级：{type(e).__name__}）", 0, [], True
    res = SubTaskResult(task_id=task.id, goal=task.goal, task_type=task.task_type,
                        output=answer, tokens=tokens, degraded=degraded, tool_trace=trace)
    tracer.log("execute", f"{task.task_type}#{task.id}", task.goal, answer,
               tokens=tokens, duration_s=time.time() - t0)
    on_event("task_end", {"id": task.id, "goal": task.goal, "degraded": degraded,
                          "summary": answer[:120], "tokens": tokens, "tool_trace": trace})
    return res, sources


def execute_plan(plan: Plan, registry: ToolRegistry, tracer, preload: bool = True,
                 on_event=None) -> tuple[dict[int, SubTaskResult], list[dict]]:
    """按拓扑分层并发执行。返回 (task_id→结果, 全链路引用来源)。"""
    on_event = on_event or (lambda *_: None)
    results: dict[int, SubTaskResult] = {}
    sources: list[dict] = []
    cache: dict[str, SubTaskResult] = {}

    for layer in plan.layers():
        pending = []
        for task in layer:
            if task.goal in cache:  # 同 goal 去重
                hit = cache[task.goal]
                results[task.id] = hit.model_copy(update={"task_id": task.id})
                on_event("task_end", {"id": task.id, "goal": task.goal, "summary": "（缓存命中）"})
                continue
            ctx = "\n".join(f"[任务{tid}] {results[tid].output}"
                            for tid in task.depends_on if tid in results)
            pending.append((task, ctx))
        if not pending:
            continue
        with ThreadPoolExecutor(max_workers=min(3, len(pending))) as pool:
            futures = [pool.submit(_run_one, t, c, registry, preload, tracer, on_event)
                       for t, c in pending]
            for f in futures:
                res, src = f.result()
                results[res.task_id] = res
                sources.extend(src)
                cache[res.goal] = res
    return results, sources
