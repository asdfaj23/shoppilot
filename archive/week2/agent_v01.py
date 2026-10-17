"""
Week2 · 单 Agent 电商分析 0.1（本项目的第一个"能跑的东西"）
================================================================================
链路（DeerFlow 范式的单机版）：
    用户目标 → Planner 拆成子任务 DAG → 按拓扑序逐个执行（每个子任务 = 一次带工具的 ReAct 循环）
             → Aggregator 把各子任务结论汇总成带依据的报告

用法：
    python week2/agent_v01.py "分析蓝牙耳机A的差评痛点并给出改进建议"   # 单次分析
    python week2/agent_v01.py --chat                                   # 多轮对话（演示记忆压缩）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import MODEL, get_client  # noqa: E402
from week2.memory import SessionMemory  # noqa: E402
from week2.planner import plan, show_plan  # noqa: E402
from week2.tools import TOOLS, TOOL_MAP  # noqa: E402

client = get_client()

SUBTASK_SYSTEM = (
    "你是电商运营分析子任务的执行者。优先调用工具拿真实数据，禁止编造数字。"
    "完成后用不超过150字给出结论（带数字）。"
)

AGGREGATE_SYSTEM = (
    "你是报告汇总器。把各子任务的结论整合成一份简短报告，结构：结论 / 关键发现（带数字）/ 建议。"
    "不同子任务结论冲突时必须明确指出，禁止隐瞒矛盾。"
)

CHAT_SYSTEM = "你是电商运营助手，基于工具数据回答，禁止编造。"


def react_once(task_goal: str, tools=TOOLS, max_steps: int = 5, verbose: bool = True) -> tuple[str, int]:
    """Week1 ReAct 循环的可复用版：接收外部 tools，供不同子任务按需注入（工具预加载的前身）。
    返回 (结论文本, 消耗 tokens)——tokens 供上层做成本归因。"""
    messages = [
        {"role": "system", "content": SUBTASK_SYSTEM},
        {"role": "user", "content": task_goal},
    ]
    total_tokens = 0
    for step in range(1, max_steps + 1):
        if verbose:
            print(f"\n  ---- [{task_goal[:12]}…] Step {step} ----")
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, tools=tools, temperature=0.3
        )
        msg = resp.choices[0].message
        total_tokens += resp.usage.total_tokens
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content or "", total_tokens
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            if verbose:
                print(f"  [行动] {tc.function.name}({json.dumps(args, ensure_ascii=False)})")
            result = TOOL_MAP[tc.function.name](**args)
            if verbose:
                print(f"  [观察] {result[:120]}…")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
    return "（子任务达到最大步数未完成）", total_tokens


def run_plan(goal: str) -> str:
    """Planner → 顺序执行子任务（depends_on 已保证 id 升序即拓扑序）→ 汇总。"""
    tasks = plan(goal)
    show_plan(tasks)

    results = {}
    for t in tasks:
        # 把依赖任务的结论作为上下文带给当前任务（显式状态传递，替代框架的 shared state）
        ctx = "\n".join(f"[任务{tid}结论] {results[tid]}" for tid in t["depends_on"] if tid in results)
        prompt = f"{t['goal']}\n\n已知信息：\n{ctx or '（无，请自行查工具）'}"
        print(f"\n>>> 执行任务[{t['id']}] ({t['task_type']})：{t['goal']}")
        results[t["id"]], tokens = react_once(prompt)
        print(f"  （本子任务消耗 {tokens} tokens）")

    print("\n>>> 汇总各子任务结论…")
    report_input = "\n\n".join(f"任务[{tid}] {next(x['goal'] for x in tasks if x['id'] == tid)}：\n{res}"
                               for tid, res in results.items())
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": AGGREGATE_SYSTEM},
            {"role": "user", "content": f"目标：{goal}\n\n各子任务结论：\n{report_input}"},
        ],
        temperature=0.3,
    )
    return resp.choices[0].message.content or ""


def chat() -> None:
    """多轮对话模式：演示会话记忆 + 超长自动压缩。"""
    memory = SessionMemory(keep_recent=6, max_old_chars=600)
    print("多轮对话模式（输入 q 退出）。试着聊 6 轮以上，观察 [Memory] 压缩日志。")
    while True:
        user = input("\n你> ").strip()
        if user.lower() in ("q", "quit", "exit"):
            break
        memory.add("user", user)
        messages = memory.build(CHAT_SYSTEM)
        resp = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS, temperature=0.3)
        msg = resp.choices[0].message

        # 简单处理：本轮若想调工具就执行一轮工具再答（完整循环见 react_once）
        if msg.tool_calls:
            messages.append(msg.model_dump(exclude_none=True))
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                print(f"  [行动] {tc.function.name}({json.dumps(args, ensure_ascii=False)})")
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": TOOL_MAP[tc.function.name](**args)})
            resp = client.chat.completions.create(model=MODEL, messages=messages, temperature=0.3)
            msg = resp.choices[0].message

        answer = msg.content or ""
        memory.add("assistant", answer)
        print(f"助手> {answer}")


if __name__ == "__main__":
    if "--chat" in sys.argv:
        chat()
    else:
        goal = sys.argv[1] if len(sys.argv) > 1 else "分析蓝牙耳机A的差评痛点并给出改进建议"
        print(f"\n目标：{goal}\n最终报告：\n{run_plan(goal)}")
