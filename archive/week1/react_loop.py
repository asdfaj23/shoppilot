"""
Week1 Day3 · 手写 ReAct Agent Loop（不依赖任何 Agent 框架，只用 openai SDK）
================================================================================
ReAct = Reason(思考) + Act(行动)。循环三步（面试必背）：

  ① 把 messages（含 tools 定义）发给模型
  ② 若返回 tool_calls → 逐个执行真实函数 → 结果以 role="tool" 追加进 messages → 回到 ①
  ③ 若没有 tool_calls（finish_reason="stop"）→ 该条 content 就是最终答案

为什么能循环：messages 就是 Agent 的记忆，每一轮都把"模型说过什么 + 工具返回什么"
全量喂回去，模型据此决定下一步。
为什么必须设 max_steps：模型可能反复调工具，有界循环（Budget）是 Agent 护栏的第一道。
"""
import json
import sys
from pathlib import Path

# 让 Python 能找到仓库根目录的 config.py（week 子目录独立运行的小技巧）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import MODEL, get_client  # noqa: E402
from week1.react_tools import TOOL_MAP, TOOLS  # noqa: E402

client = get_client()

SYSTEM_PROMPT = (
    "你是电商运营分析助手。回答前必须先调用工具拿真实数据，禁止编造数字。"
    "最终答复用中文，结构固定为：结论 / 数据依据 / 改进建议。"
)


def run_agent(goal: str, max_steps: int = 8) -> str:
    """ReAct 主循环。max_steps 是有界护栏。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": goal},
    ]
    total_tokens = 0

    for step in range(1, max_steps + 1):
        print(f"\n========== Step {step} ==========")

        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            temperature=0.3,  # 分析类任务要稳定，温度调低
        )
        choice = resp.choices[0]
        msg = choice.message
        total_tokens += resp.usage.total_tokens
        print(f"[finish_reason] {choice.finish_reason} | 本步 tokens: {resp.usage.total_tokens}")

        # 模型这条回复必须原样入历史（tool_calls 里有 id，后面喂结果要靠它对应）
        messages.append(msg.model_dump(exclude_none=True))

        # 不再要工具 = 模型认为信息够了，输出即最终答案
        if not msg.tool_calls:
            print(f"\n[最终答案]\n{msg.content}")
            print(f"\n（全程共消耗 {total_tokens} tokens）")
            return msg.content or ""

        # 一次可能返回多个工具调用，全部执行
        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments or "{}")
            print(f"[行动] {name}({json.dumps(args, ensure_ascii=False)})")

            result = TOOL_MAP[name](**args)  # 本地执行真实函数
            print(f"[观察] {result}")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,  # 必须与上面 tool_calls 的 id 对应
                    "content": str(result),
                }
            )

    print("[警告] 达到 max_steps 仍未完成，返回兜底")
    return "已达最大步数，任务未完成"


if __name__ == "__main__":
    # 用法：python week1/react_loop.py "你的问题"；不传参则用默认 demo 问题
    question = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "分析蓝牙耳机A的差评痛点，并结合销量给出改进建议"
    )
    run_agent(question)
