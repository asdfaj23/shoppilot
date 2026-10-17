"""
Week4 · 多 Agent 协作（DeerFlow 三模块范式落地）+ Harness 工程护栏
================================================================================
架构（对照规划 4.2 的三模块）：
    Planner（week2 复用）拆子任务 DAG
      → Dispatcher 按 task_type 路由到三个子 Agent，并【按类型预加载工具】
           data_query    → 数据 Agent（week2 pandas 工具）
           knowledge_qa  → 检索 Agent（week3 RAG）
           synthesis     → 综合 Agent（纯 LLM，不给工具）
      → Aggregator 汇总 + 冲突仲裁 + 强制引用

Harness 护栏（对应规划 7.1 的 Agentic Loop/Permissions/Observability）：
    ① 有界重试：子任务失败最多重试 2 次（指数退避）
    ② 超时降级：单子任务超 90s 判失败，带着部分结果走，不卡死整个任务
    ③ 工具预加载：每个 Agent 只拿到自己需要的工具 → 无关工具不进上下文 → 省 Token
    ④ 结果缓存：相同子任务描述直接命中缓存
    ⑤ 全链路 Trace（week4/trace.py）

用法：python week4/multi_agent.py "蓝牙耳机A值得买吗？差评多不多？保修政策是什么？"
"""
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import MODEL, get_client  # noqa: E402
from week2.agent_v01 import react_once  # noqa: E402
from week2.planner import plan, show_plan  # noqa: E402
from week2.tools import TOOLS  # noqa: E402
from week3.rag.query import answer as rag_answer  # noqa: E402
from week4.trace import Tracer  # noqa: E402

client = get_client()

MAX_RETRIES = 2          # 护栏①：最多重试次数
SUBTASK_TIMEOUT_S = 90   # 护栏②：单子任务超时
_result_cache: dict[str, str] = {}  # 护栏④：子任务级结果缓存

AGGREGATE_SYSTEM = (
    "你是最终报告聚合器。输入是各子 Agent 的结论，输出一份简短报告：结论 / 关键发现（带数字与来源）/ 建议。\n"
    "硬性要求：\n"
    "1. 每个关键发现必须标注来源：数据结论标 [任务n]，知识库结论标 [资料:文档名]；\n"
    "2. 子结论互相矛盾时（如数据说差评率高、手册说质量可靠），必须明确指出冲突并说明你采信哪方及理由；\n"
    "3. 禁止编造任何子结论里没有的数字。"
)

# 护栏③：任务类型 → 注入的工具集（无关工具绝不进上下文）
TOOL_PRELOAD = {
    "data_query": TOOLS,     # 数据 Agent：3 个 pandas 工具
    "knowledge_qa": [],      # 检索 Agent：走 RAG，不需要 function tools
    "synthesis": [],         # 综合 Agent：纯推理，不给工具（防止它乱调）
}


# ---------- Dispatcher：路由 + 护栏包裹 ----------
def dispatch(task: dict, context: str, tracer: Tracer) -> str:
    goal = task["goal"]
    if goal in _result_cache:  # 护栏④
        tracer.log("dispatch", f"cache-hit:{goal[:20]}", goal, _result_cache[goal])
        return _result_cache[goal]

    # 护栏①+②：重试 × 超时
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            t0 = time.time()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_run_subagent, task, context)
                result, tokens = future.result(timeout=SUBTASK_TIMEOUT_S)
            tracer.log("dispatch", f"{task['task_type']}#{task['id']}(尝试{attempt})",
                       goal, result, tokens=tokens, duration_s=round(time.time() - t0, 1))
            _result_cache[goal] = result
            return result
        except Exception as e:  # 超时或子 Agent 异常
            err = f"{type(e).__name__}: {e}"
            print(f"  [护栏] 任务{task['id']} 第{attempt}次失败（{err}）"
                  + ("，重试…" if attempt <= MAX_RETRIES else "，已达重试上限，降级为空结果"))
            tracer.log("dispatch", f"fail:{task['id']}(尝试{attempt})", goal, err)
    return "（该子任务执行失败，已降级，请基于其余结论回答）"


def _run_subagent(task: dict, context: str) -> tuple[str, int]:
    """按 task_type 分发到对应子 Agent（= DeerFlow 的 Research Team）。返回 (结论, tokens)。"""
    tt = task["task_type"]
    if tt == "data_query":
        return react_once(f"{task['goal']}\n\n已知信息：\n{context or '（无）'}", tools=TOOL_PRELOAD[tt])
    if tt == "knowledge_qa":
        r = rag_answer(task["goal"])
        cites = "；".join(f"[资料:{c['source']}|{c['heading']}]" for c in r["citations"])
        return f"{r['answer']}\n（来源：{cites or '知识库未命中，已如实回答无资料'}）", r.get("tokens", 0)
    # synthesis：纯 LLM 综合，不给工具
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": "你是电商分析师，基于给定信息推理，禁止编造数据。"},
                  {"role": "user", "content": f"{task['goal']}\n\n已知信息：\n{context or '（无）'}"}],
        temperature=0.3,
    )
    return resp.choices[0].message.content or "", resp.usage.total_tokens


# ---------- 主流程：Planner → Dispatcher → Aggregator ----------
def run_multi(goal: str) -> str:
    tracer = Tracer("multi_agent")
    print(f"目标：{goal}")

    tasks = plan(goal)          # ① Planner（week2 复用，已被 week4 评测验证过）
    show_plan(tasks)
    tracer.log("plan", "Planner", goal, json.dumps(tasks, ensure_ascii=False))

    results = {}
    for t in tasks:             # ② Dispatcher 按拓扑序调度
        ctx = "\n".join(f"[任务{tid}] {results[tid]}" for tid in t["depends_on"] if tid in results)
        print(f"\n>>> 调度任务[{t['id']}] ({t['task_type']})：{t['goal']}")
        results[t["id"]] = dispatch(t, ctx, tracer)

    print("\n>>> Aggregator 汇总 + 冲突仲裁…")   # ③ 聚合
    sub = "\n\n".join(f"[任务{tid}] {next(x['goal'] for x in tasks if x['id']==tid)}\n{res}"
                      for tid, res in results.items())
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": AGGREGATE_SYSTEM},
                  {"role": "user", "content": f"目标：{goal}\n\n各子 Agent 结论：\n{sub}"}],
        temperature=0.3,
    )
    final = resp.choices[0].message.content or ""
    tracer.log("aggregate", "Aggregator", sub[:300], final, tokens=resp.usage.total_tokens)

    s = tracer.summary()        # ④ 可观测汇总
    print(f"\n{'=' * 20} 最终报告 {'=' * 20}\n{final}")
    print(f"\n[Trace] 总 tokens: {s['total_tokens']} | 耗时 {s['total_seconds']}s | 分步: {s['by_step']}")
    print(f"[Trace] 明细: {s['trace_file']}")
    return final


if __name__ == "__main__":
    question = (sys.argv[1] if len(sys.argv) > 1
                else "蓝牙耳机A值得买吗？差评主要是什么？保修政策是什么？")
    run_multi(question)
