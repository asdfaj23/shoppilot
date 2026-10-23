"""
ShopPilot ④ 有界 ReAct 循环（吸收 week1/react_loop.py + week4 护栏的合并升级版）
================================================================================
面试必背的三步循环：① messages(+tools) 发给模型 ② 有 tool_calls 就执行并把结果按
tool_call_id 喂回去 ③ 没 tool_calls 时 content 即最终答案。messages 就是 Agent 的记忆。

在 week1 教学版之上叠加 Harness 工程护栏（对应通用架构指南 7.1）：
  - 三重预算：max_steps / max_tokens / max_cost_cny，任一超限即降级返回，不无限烧钱；
  - 并行 tool_calls：模型一次返回多个工具调用 → 线程池全部并发执行 → 按 id 喂回
    （顺序会乱但 id 对应关系不乱，这正是 tool_call_id 存在的意义）；
  - 重试退避：LLM 调用失败最多重试 max_retries 次，指数退避（网络抖动不打断任务）；
  - 超时降级：单工具执行超 subtask_timeout_s 判失败，喂回错误信息让模型自行调整；
  - 输出校验钩子：最终答案可挂确定性校验函数（如引用编号检查），不过关触发一次修复。
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from shoppilot.core.settings import settings
from shoppilot.core.tools import ToolRegistry


@dataclass
class LoopBudget:
    max_steps: int = settings.max_steps
    max_tokens: int = settings.max_tokens_budget
    max_cost_cny: float = settings.max_cost_cny


@dataclass
class LoopResult:
    answer: str = ""
    tokens: int = 0
    steps: int = 0
    degraded: bool = False
    degrade_reason: str = ""
    tool_trace: list[dict] = field(default_factory=list)  # [{name,args,summary,ok}]

    def trace_summary(self, max_len: int = 60) -> list[dict]:
        """给前端/聚合器的工具调用摘要。"""
        return [{**t, "args": str(t.get("args"))[:120], "summary": str(t.get("summary"))[:max_len]}
                for t in self.tool_trace]


def build_messages(system: str, *blocks: str) -> list[dict]:
    """分层提示拼装：system → 若干带小标题的上下文块 → 已是 messages 形态的原样并入。"""
    messages = [{"role": "system", "content": system}]
    for b in blocks:
        if isinstance(b, str) and b.strip():
            messages.append({"role": "user", "content": b})
        elif isinstance(b, list):
            messages.extend(b)
    return messages


def _run_one_tool(registry: ToolRegistry, name: str, args_json: str, timeout: float) -> tuple[str, bool]:
    """带超时的单工具执行。超时不是异常炸掉，而是降级为错误消息喂回模型。"""
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(registry.call, name, args_json)
        try:
            return future.result(timeout=timeout), True
        except Exception as e:
            return json.dumps({"error": f"工具执行超时或失败（{type(e).__name__}），请调整参数或换工具"},
                              ensure_ascii=False), False


def react_loop(llm, system: str, user: str, registry: ToolRegistry,
               tool_names: list[str] | None = None,
               budget: LoopBudget | None = None,
               on_event=None,
               output_validator=None) -> LoopResult:
    """
    llm: shoppilot.core.llm.LLM；tool_names=None 表示注入全部 schema（全量对照模式）。
    on_event: callable(type, payload) 事件回调，type ∈ plan_step/tool_start/tool_end/loop_end。
    output_validator: callable(answer) -> str|None，返回错误描述表示不过关（触发一次自修复）。
    """
    budget = budget or LoopBudget()
    on_event = on_event or (lambda *_: None)
    tools = registry.schemas(tool_names)
    messages = build_messages(system, user)
    result = LoopResult()

    def cost(tk):
        return tk / 1e6 * settings.price_per_m_tokens

    for step in range(1, budget.max_steps + 1):
        result.steps = step
        # ---- LLM 调用 + 重试退避（护栏：指数退避，最多 max_retries 次）----
        resp = None
        for attempt in range(1, settings.max_retries + 2):
            try:
                resp = llm.chat(messages, tools=tools or None)
                break
            except Exception as e:
                if attempt > settings.max_retries:
                    result.degraded = True
                    result.degrade_reason = f"LLM 调用失败：{type(e).__name__}: {e}"
                    result.answer = "（该子任务执行失败，已降级，请基于其余结论回答）"
                    return result
                time.sleep(0.5 * 2 ** (attempt - 1))

        result.tokens += resp["tokens"]
        msg = resp["message"]
        messages.append(msg)

        if result.tokens > budget.max_tokens or cost(result.tokens) > budget.max_cost_cny:
            result.degraded = True
            result.degrade_reason = f"超出预算（tokens={result.tokens}）"
            break

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:  # ③ 模型认为信息够了 → 输出即最终答案
            answer = msg.get("content") or ""
            # 输出校验钩子：不过关给模型一次自修复机会（把错误喂回去重答一次）
            if output_validator:
                err = output_validator(answer)
                if err:
                    messages.append({"role": "user",
                                     "content": f"你的输出未通过校验：{err}。请修正后重新输出完整答案。"})
                    resp2 = llm.chat(messages, tools=tools or None)
                    result.tokens += resp2["tokens"]
                    answer = resp2["message"].get("content") or answer
            result.answer = answer
            on_event("loop_end", {"steps": step, "tokens": result.tokens})
            return result

        # ② 并行执行本轮全部 tool_calls，再按 tool_call_id 逐一喂回
        pending = []
        for tc in tool_calls:
            name = tc["function"]["name"]
            args_json = tc["function"].get("arguments") or "{}"
            on_event("tool_start", {"name": name, "args": args_json})
            pending.append((tc, registry, args_json))

        with ThreadPoolExecutor(max_workers=min(4, len(pending))) as pool:
            futures = {pool.submit(_run_one_tool, registry, tc["function"]["name"],
                                   tc["function"].get("arguments") or "{}",
                                   settings.subtask_timeout_s): tc
                       for tc in tool_calls}
            outputs = {}
            for future, tc in futures.items():
                name = tc["function"]["name"]
                args_json = tc["function"].get("arguments") or "{}"
                try:
                    out, ok = future.result()
                except Exception as e:
                    out, ok = json.dumps({"error": f"工具执行异常：{e}"}, ensure_ascii=False), False
                outputs[tc["id"]] = out
                result.tool_trace.append({"name": name, "args": args_json,
                                          "summary": out[:120], "ok": ok})
                on_event("tool_end", {"name": name, "args": args_json,
                                      "summary": out[:160], "ok": ok})
        for tc in tool_calls:  # 按 tool_call_id 对应喂回（顺序与模型要求一致）
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": outputs[tc["id"]]})

    result.degraded = True
    result.degrade_reason = f"达到 max_steps={budget.max_steps} 仍未收敛"
    result.answer = "（已达最大步数，任务未完成，请基于已有信息回答）"
    return result
