"""
ShopPilot orchestrator/agent.py · 总装：规划 → 执行 → 成稿（确定性护栏）→ 记忆回写
================================================================================
:class:`ShoppingAgent` is the assembled runtime. It is **domain-agnostic**: the
tool registry, the product catalogue, the knowledge-RAG function and the
guardrails are injected by the composition root (domains/<domain>/factory.py).
This keeps orchestrator free of any domain import.

    ShoppingAgent.run()
      ├─ planner.make_plan()      意图 + 约束抽取（catalog 注入；记忆支持"它/这个"）
      ├─ executor.execute_plan()  DAG 分层并发调工具（llm / knowledge_fn 注入）
      ├─ composer.compose()       结构化卡 + 强制引用 + 确定性护栏（必要时修复一次）
      ├─ 研究报告落盘            intent=research 时存 data/reports/*.md
      └─ memory.add_turn()        短期进滑窗，长期追加一条可读记录

Token accounting is fixed here: planner + executor + composer tokens are summed
once (the previous version overwrote the running total and self-added tokens).
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from shoppilot.core.guardrails.base import OutputGuardrail
from shoppilot.core.guardrails.runner import GuardrailRunner
from shoppilot.core.memory import MemoryManager
from shoppilot.core.settings import DATA_DIR
from shoppilot.core.tracing import Tracer
from shoppilot.core.types import TeamResult
from shoppilot.orchestrator.composer import compose
from shoppilot.orchestrator.executor import execute_plan
from shoppilot.orchestrator.planner import (
    make_plan,
    refuse_looks_wrong,
    unknown_entities,
)

OUT_OF_SCOPE_ANSWER = (
    "抱歉，这个问题超出了我的导购职责范围。\n\n"
    "我能帮你：**选购推荐**（按预算/在意维度打分推荐）、**商品对比**、**口碑解读**（差评归因/避坑）、"
    "**售后问答**（保修/退换货）。商品池覆盖手机、笔电、平板、耳机、手表、相机、显示器、"
    "路由器、智能家居等品类。换一个问题试试？"
)


def _dump_report(result: TeamResult) -> str:
    """Render a research-report card into an archivable Markdown document."""
    card = result.structured or {}
    lines = [f"# {card.get('topic', '选购研究报告')}", "",
             f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M} ｜ 数据窗口：{card.get('data_window', '—')}", "",
             "## 结论", card.get("conclusion", ""), "", "## 候选对比", ""]
    for c in card.get("candidates", []):
        lines.append(f"- **{c.get('name')}**（¥{c.get('price')}，评分 {c.get('score')}）→ {c.get('verdict', '')}")
    lines += ["", "## 证据", ""]
    for e in card.get("evidence", []):
        cite = f" [{e.get('citation')}]" if e.get("citation") else ""
        lines.append(f"- {e.get('claim')}{cite}（来源：{e.get('source', '—')}）")
    lines += ["", "## 风险", ""]
    lines += [f"- {r}" for r in card.get("risks", [])]
    lines += ["", "## 建议", ""]
    lines += [f"- {a}" for a in card.get("advice", [])]
    lines += ["", "---", "", "## 完整回答", result.answer_md]
    return "\n".join(lines)


class ShoppingAgent:
    """Assembled, domain-agnostic shopping agent (all domain pieces injected)."""

    def __init__(self, llm=None, registry=None, catalog=None, knowledge_fn=None,
                 guardrails: list[OutputGuardrail] | None = None,
                 guardrail_runner: GuardrailRunner | None = None,
                 on_event=None, report_dir: str | Path | None = None,
                 enable_memory: bool = True):
        self.llm = llm
        self.registry = registry
        self.catalog = catalog
        self.knowledge_fn = knowledge_fn
        self.on_event = on_event or (lambda *_: None)
        if guardrail_runner is not None:
            self.guardrail_runner = guardrail_runner
        else:
            self.guardrail_runner = GuardrailRunner(
                guardrails or [], on_event=self.on_event) if guardrails else None
        self.enable_memory = enable_memory
        self.report_dir = Path(report_dir) if report_dir else DATA_DIR / "reports"
        self.last_query = None

    # -- helpers -----------------------------------------------------------
    def _persist_report(self, result: TeamResult) -> str:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        path = self.report_dir / f"report_{datetime.now():%Y%m%d_%H%M%S}.md"
        path.write_text(_dump_report(result), encoding="utf-8")
        return str(path)

    # -- main loop ---------------------------------------------------------
    def run(self, user_text: str, session_id: str = "default", user_id: str = "default",
            preload_tools: bool = True) -> TeamResult:
        on_event = self.on_event
        t0 = time.time()
        tracer = Tracer("shoppilot", on_event=on_event)
        memory = MemoryManager(session_id, user_id) if self.enable_memory else None
        memory_block = memory.memory_block(user_text) if memory else ""

        # ① 规划：意图 + 约束 + 任务拆解（带记忆、带商品池）
        query, plan, plan_tokens = make_plan(
            user_text, memory_block=memory_block, llm=self.llm,
            on_event=on_event, catalog=self.catalog)
        intent = query.intent if query else ""
        tracer.log("plan", "Planner", user_text,
                   plan.model_dump_json() if plan else "out_of_scope", tokens=plan_tokens)

        # ② 防误拒：判超范围但文本明显是导购请求 → 重问一次（确定性、零 token）
        if plan is None and refuse_looks_wrong(user_text):
            on_event("refuse_recheck", {})
            query, plan, t2 = make_plan(
                user_text + "\n（系统提示：若与导购相关请勿判 out_of_scope，商品池内品类务必正常拆任务）",
                memory_block=memory_block, llm=self.llm, on_event=on_event,
                catalog=self.catalog)
            intent = query.intent if query else intent
            plan_tokens += t2

        # ③ 库外实体闸：点名了池外型号 → 要求按拒答处理，防编造参数
        pool = self.catalog.product_names() if self.catalog is not None else []
        if plan is not None and query is not None and query.entities:
            unknown = unknown_entities(query, pool)
            if unknown:
                on_event("pool_recheck", {"unknown": unknown})
                query, plan, t2 = make_plan(
                    user_text + f"\n（系统提示：{unknown} 不在商品池中，"
                                "请按 out_of_scope 处理或明确说明查不到，禁止编造价格参数）",
                    memory_block=memory_block, llm=self.llm, on_event=on_event,
                    catalog=self.catalog)
                intent = query.intent if query else intent
                plan_tokens += t2

        if plan is None or query is None:  # 拒答短路
            result = TeamResult(intent=intent, answer_md=OUT_OF_SCOPE_ANSWER,
                                tokens=plan_tokens, duration_s=round(time.time() - t0, 1))
            tracer.log("refuse", "OutOfScope", user_text, result.answer_md)
            if memory:
                memory.add_turn(user_text, result.answer_md, [], "触发拒答：超出范围或池外商品")
            result.trace_file = tracer.summary()["trace_file"]
            return result

        # ④ 执行：DAG 分层并发（llm / knowledge_fn 注入）
        self.last_query = query
        results, sources = execute_plan(
            plan, self.registry, tracer, preload=preload_tools,
            on_event=on_event, llm=self.llm, knowledge_fn=self.knowledge_fn)
        exec_tokens = sum(r.tokens for r in results.values())

        # ⑤ 成稿：聚合 + 结构化卡 + 强制引用 + 确定性护栏（必要时修复一次）
        result = compose(query, results, sources, memory_block, llm=self.llm,
                         runner=self.guardrail_runner, tracer=tracer, on_event=on_event)
        compose_tokens = result.tokens

        # 正确的三段 token 聚合（修复旧版覆盖/自加 bug）
        result.tokens = plan_tokens + exec_tokens + compose_tokens
        result.duration_s = round(time.time() - t0, 1)
        result.degradations = [f"任务{r.task_id}（{r.task_type}）降级：{r.output[:50]}"
                               for r in results.values() if r.degraded]
        summary = tracer.summary()
        result.trace_file = summary["trace_file"]
        result.token_breakdown = summary["by_step"]

        # ⑥ 记忆回写
        if memory:
            items = [it.get("name", "") for it in (result.structured or {}).get("items", [])
                     if it.get("name")]
            items += [s for s in (result.structured or {}).get("subjects", [])]
            memory.add_turn(
                user_text, result.answer_md, items,
                (result.structured or {}).get("summary")
                or (result.structured or {}).get("conclusion", ""),
                tokens=result.tokens, llm=None)

        # ⑦ 研究报告落盘
        if result.intent == "research" and result.structured:
            result.report_file = self._persist_report(result)
        on_event("done", {"tokens": result.tokens, "duration_s": result.duration_s,
                          "guardrail": result.guardrail})
        return result
