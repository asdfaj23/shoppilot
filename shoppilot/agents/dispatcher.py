"""
ShopPilot agents · Dispatcher：DAG 分层并发调度 + 失败降级（吸收 week4/multi_agent.py）
================================================================================
相对 week4 串行版的三点升级：
  1. 并发：Plan.layers() 把 DAG 按拓扑分层，同层子任务 ThreadPoolExecutor 并发执行
     （推荐场景"查商品 + 查口碑"两路并行，时延近乎减半）；
  2. 工具预加载：data_query/review_analysis 子任务只注入各自工具 schema
     （registry.preload_names(task_type)），无关工具不进上下文；
  3. 失败降级：子任务异常/超时不炸全链路——记 degraded、喂回兜底文案，
     Aggregator 拿到的是"缺失信息"而不是崩溃。
同 goal 结果缓存：DAG 内重复子任务直接命中。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from shoppilot.agents.base import BaseAgent, ToolAgent
from shoppilot.harness.observability import Tracer
from shoppilot.harness.tools import ToolRegistry
from shoppilot.models import Plan, SubTask, SubTaskResult

SYNTH_SYSTEM = (
    "你是导购分析子任务的执行者。基于已知信息完成分析推理，禁止编造数据与数字；"
    "已知信息不足时明确说'信息不足'。结论用中文，不超过 200 字。"
)


def _run_one(task: SubTask, context: str, registry: ToolRegistry, preload: bool,
             tracer: Tracer, on_event) -> tuple[SubTaskResult, list[dict]]:
    """执行单个子任务。返回 (结果, 该任务贡献的引用来源列表)。"""
    t0 = time.time()
    sources: list[dict] = []
    on_event("task_start", {"id": task.id, "task_type": task.task_type, "goal": task.goal})
    try:
        if task.task_type in ("data_query", "review_analysis"):
            names = registry.preload_names(task.task_type) if preload else registry.names()
            agent = ToolAgent(registry, names, on_event=on_event)
            answer, tokens, trace, degraded = agent.run(task.goal, context)
            sources.append({"source": f"任务{task.id}", "heading": task.goal[:40],
                            "text": answer[:120]})
        elif task.task_type == "knowledge_qa":
            from shoppilot.rag.rag_lib import rag_answer

            r = rag_answer(task.goal)  # rag 内部取共享 LLM 单例
            cites = "；".join(f"[{c['no']}] {c['source']}|{c['heading']}" for c in r["citations"])
            answer = r["answer"] + (f"\n（来源：{cites}）" if cites else "")
            tokens, trace, degraded = r["tokens"], [], False
            sources.extend(r["citations"])
            sources.append({"source": f"任务{task.id}", "heading": task.goal[:40],
                            "text": r["answer"][:120]})
        else:  # synthesis：纯 LLM，不给工具
            agent = BaseAgent(system=SYNTH_SYSTEM, on_event=on_event)
            answer, tokens, trace, degraded = agent.run(task.goal, context)
            sources.append({"source": f"任务{task.id}", "heading": task.goal[:40],
                            "text": answer[:120]})
    except Exception as e:  # 失败降级：喂回兜底文案，不炸全链路
        answer, tokens, trace, degraded = f"（子任务执行失败已降级：{type(e).__name__}: {e}）", 0, [], True
    res = SubTaskResult(task_id=task.id, goal=task.goal, task_type=task.task_type,
                        output=answer, tokens=tokens, degraded=degraded, tool_trace=trace)
    tracer.log("dispatch", f"{task.task_type}#{task.id}", task.goal, answer,
               tokens=tokens, duration_s=time.time() - t0)
    on_event("task_end", {"id": task.id, "goal": task.goal, "degraded": degraded,
                          "summary": answer[:120], "tokens": tokens,
                          "tool_trace": trace})
    return res, sources


def execute_plan(plan: Plan, registry: ToolRegistry, tracer: Tracer,
                 preload: bool = True, on_event=None,
                 max_workers: int = 3) -> tuple[dict[int, SubTaskResult], list[dict]]:
    """按拓扑分层并发执行 DAG。返回 (task_id→结果, 全链路引用来源列表)。"""
    on_event = on_event or (lambda *_: None)
    results: dict[int, SubTaskResult] = {}
    sources: list[dict] = []
    cache: dict[str, SubTaskResult] = {}  # 同 goal 去重

    for layer in plan.layers():
        if len(layer) == 1:
            todo = [(layer[0], _context_of(layer[0], results))]
        else:
            todo = [(t, _context_of(t, results)) for t in layer]
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = []
            for task, ctx in todo:
                if task.goal in cache:  # 结果缓存命中
                    hit = cache[task.goal]
                    results[task.id] = hit.model_copy(update={"task_id": task.id})
                    tracer.log("dispatch", f"cache-hit#{task.id}", task.goal, hit.output[:200])
                    on_event("task_end", {"id": task.id, "goal": task.goal,
                                          "summary": "（缓存命中）", "tokens": 0})
                    continue
                futures.append(pool.submit(_run_one, task, ctx, registry, preload, tracer, on_event))
            for future in futures:
                res, src = future.result()
                results[res.task_id] = res
                sources.extend(src)
                cache[res.goal] = res
    return results, sources


def _context_of(task: SubTask, results: dict[int, SubTaskResult]) -> str:
    """把依赖任务的结论作为上下文传递（显式状态传递，替代框架的 shared state）。"""
    return "\n".join(f"[任务{tid}] {results[tid].output}"
                     for tid in task.depends_on if tid in results)
