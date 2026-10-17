"""
ShopPilot agents · BaseAgent / ToolAgent（吸收 week4 多 Agent 的子 Agent 抽象）
================================================================================
BaseAgent：纯推理子 Agent（synthesis 用，不给工具——防止它乱调工具，week4 的工具预加载思想）。
ToolAgent：带工具的 ReAct 子 Agent（data_query / review_analysis 用），
          工具集由 Dispatcher 按 task_type 预加载注入——省 token 的关键设计。
统一约定：run() 返回 (结论文本, tokens, tool_trace, degraded)，上层做成本归因与降级统计。
"""
from __future__ import annotations

from shoppilot.harness.loop import LoopResult, build_messages, react_loop
from shoppilot.harness.tools import ToolRegistry


class BaseAgent:
    name = "base"
    system = "你是任务执行者。基于给定信息完成任务，禁止编造数据与数字。"

    def __init__(self, llm=None, on_event=None, system: str | None = None):
        from shoppilot.llm import get_llm

        self.llm = llm or get_llm()
        self.on_event = on_event
        if system:
            self.system = system  # 实例级覆盖类级默认提示词

    def run(self, goal: str, context: str = "") -> tuple[str, int, list[dict], bool]:
        blocks = [f"[任务]\n{goal}"]
        if context:
            blocks.append(f"[已知信息]\n{context}")
        text, tokens = self.llm.chat_text(build_messages(self.system, *blocks))
        return text, tokens, [], False


class ToolAgent(BaseAgent):
    name = "tool_agent"
    system = (
        "你是导购任务的执行者。优先调用工具拿真实数据，禁止编造数字与商品信息。"
        "结论用中文，控制在 200 字内，关键数字保留原值。"
    )

    def __init__(self, registry: ToolRegistry, tool_names: list[str], **kw):
        super().__init__(**kw)
        self.registry = registry
        self.tool_names = tool_names  # None = 全量注入（benchmark 对照组用）

    def run(self, goal: str, context: str = "") -> tuple[str, int, list[dict], bool]:
        blocks = [f"[任务]\n{goal}"]
        if context:
            blocks.append(f"[已知信息]\n{context}")
        result: LoopResult = react_loop(self.llm, self.system, "\n\n".join(blocks),
                                        self.registry, tool_names=self.tool_names,
                                        on_event=self.on_event)
        return result.answer, result.tokens, result.trace_summary(), result.degraded
