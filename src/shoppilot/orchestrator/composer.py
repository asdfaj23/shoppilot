"""
ShopPilot composer.py · 成稿：聚合子结果 → 结构化卡 + 强制引用 → 确定性输出护栏
================================================================================
职责：
  1. 把各子任务结论 + 资料列表交给 LLM，产出「回答 markdown + 结构化卡 + 引用编号」；
  2. 用一组**可插拔、零 token、可单测的确定性护栏**校验输出（引用越界 / 超预算 /
     商品溯源 / 误拒答 / 目标覆盖 / 结构化卡），不再把校验逻辑写死在本文件；
  3. 只有出现 blocker 才让 LLM 修复一次（LLM 只负责改，不负责当裁判），护栏再跑一次。

护栏集合由组合根（domains/.../factory.py）注入；换域或换策略无需改本文件。
产出卡按意图四选一：Recommendation / CompareCard / CaveatList / ResearchReport。
"""
from __future__ import annotations

import time
from dataclasses import replace

from shoppilot.core.guardrails.base import GuardrailContext
from shoppilot.core.guardrails.runner import GuardrailRunner
from shoppilot.core.types import (
    CaveatList,
    Citation,
    CompareCard,
    Recommendation,
    ResearchReport,
    ShoppingQuery,
    SubTaskResult,
    TeamResult,
)

COMPOSE_SYSTEM = """你是导购成稿者。基于各子任务结论输出 JSON：
{"answer_md": "给用户看的最终回答（Markdown，简洁分点，关键数字保留原值，关键结论后标 [n]）",
 "card": 结构化卡片（四选一）, "citations": [{"no":1,"source":"来源名","heading":"小节或任务名","text":"原文片段"}]}

card 按意图四选一：
- recommend → {"kind":"recommendation","summary":"一句话总评","items":[{"name":"商品全称","price":349,"rating":4.3,"reason":"推荐理由（引用推荐打分结论）","review_summary":"口碑摘要（含好评/差评要点）","citation":1}]}
- compare → {"kind":"compare","subjects":["商品A","商品B"],"dimensions":[{"维度":"价格","A":"349元","B":"899元"}],"conclusion":"分场景给结论","citations":[]}
- review_analysis → {"kind":"caveats","summary":"痛点总评","items":[{"product":"商品","issue":"维度","detail":"差评原文要点","citation":1}]}
- research → {"kind":"research","topic":"研究主题","conclusion":"一段式结论","candidates":[{"name":"商品","price":3299,"score":"0.60","verdict":"首选/备选/不推荐"}],"evidence":[{"claim":"证据陈述","source":"来源","citation":1}],"risks":["风险1"],"advice":["建议1"],"data_window":"2026-10","citations":[]}
- after_sales → card 传 null，回答里必须带 [n] 引用

硬性要求：
1. 只允许引用资料列表里出现的来源编号 [n]，禁止编造编号或来源；
2. 商品一律用全称（如"蓝牙耳机A"），用户点名的商品必须出现在回答里；
3. 子任务结论冲突时，明确指出冲突并说明采信哪方及理由；
4. 推荐/研究类必须写出「为什么」（价格/评分/好评/差评四类依据），禁止只报商品；
5. research 报告必须含：结论、候选对比、带引用的证据、风险、建议、数据窗口；
6. 子任务降级或信息缺失，如实说明"未能查询到"，不要脑补。"""

CARD_MODELS = {
    "recommendation": Recommendation,
    "compare": CompareCard,
    "caveats": CaveatList,
    "research": ResearchReport,
}


def _parse_card(data: dict):
    """Validate the model's card payload against the right Pydantic contract."""
    card = data.get("card")
    if not isinstance(card, dict):
        return None
    model = CARD_MODELS.get(card.get("kind"))
    if model is None:
        return None
    try:
        return model(**card)
    except Exception:
        return None


def compose(query: ShoppingQuery, results: dict[int, SubTaskResult], sources: list[dict],
            memory_block: str = "", llm=None, runner: GuardrailRunner | None = None,
            tracer=None, on_event=None) -> TeamResult:
    from shoppilot.core.llm import get_llm

    llm = llm or get_llm()
    on_event = on_event or (lambda *_: None)
    t0 = time.time()

    sub_block = "\n\n".join(
        f"[任务{r.task_id}]（{r.task_type}{'，已降级' if r.degraded else ''}）{r.goal}\n{r.output}"
        for r in results.values())
    # Sources are numbered exactly as the model sees them in the prompt ([1], [2], ...).
    indexed_sources = [{**s, "no": i + 1} for i, s in enumerate(sources)]
    src_block = "\n".join(f"[{s['no']}] {s['source']} | {s.get('heading', '')}\n{s.get('text', '')[:150]}"
                          for s in indexed_sources)
    messages = [{"role": "system", "content": COMPOSE_SYSTEM}, {"role": "user", "content":
                f"用户输入：{query.raw_text}\n意图：{query.intent}\n"
                f"预算：{'≤' + str(int(query.budget_max)) + '元' if query.budget_max else '无'}\n"
                f"在意维度：{'、'.join(query.concerns) or '未说明'}"
                + (f"\n\n[记忆]\n{memory_block}" if memory_block else "")
                + f"\n\n各子任务结论：\n{sub_block}\n\n资料列表（引用编号依据）：\n{src_block}"}]

    answer_md, card_obj, citations = "", None, []
    gen_tokens = 0
    for attempt in (1, 2):
        try:
            data, tk = llm.chat_json(messages, temperature=0.3)
            gen_tokens += tk
            answer_md = (data.get("answer_md") or "").strip()
            if not answer_md:
                raise ValueError("answer_md 为空")
            card_obj = _parse_card(data)
            citations = [Citation(**c) for c in (data.get("citations") or []) if c.get("source")]
            break
        except Exception as e:
            if tracer:
                tracer.log("compose", f"retry{attempt}", "json_output", str(e)[:200])
            messages.append({"role": "user",
                             "content": f"输出有误（{str(e)[:160]}），严格按规则重新输出 JSON。"})
    if not answer_md:  # both attempts failed -> graceful fallback, never crash the path
        answer_md = "\n\n".join(f"**[任务{r.task_id}]** {r.output}" for r in results.values())
    on_event("compose_end", {"tokens": gen_tokens, "duration_s": round(time.time() - t0, 1)})
    if tracer:
        tracer.log("compose", "Composer", query.raw_text, answer_md[:300], tokens=gen_tokens)

    # ---- Deterministic output guardrails + at most one bounded repair ----
    guardrail_trace: dict = {}
    extra_tokens = 0
    repaired = False
    unresolved = False
    remaining_issues: list[str] = []
    repair_rounds = 0

    if runner is not None:
        ctx = GuardrailContext(
            user_input=query.raw_text, intent=query.intent, structured_query=query,
            sources=indexed_sources, answer_md=answer_md, card=card_obj,
            task_outputs={r.task_id: r for r in results.values()})

        def repair_fn(cur_ctx: GuardrailContext, report) -> tuple[GuardrailContext, int]:
            nonlocal card_obj
            fix_messages = [
                {"role": "system", "content": "你是导购质检修复器，严格基于资料，只输出 JSON。"},
                {"role": "user", "content":
                    f"{report.to_fix_prompt()}\n\n原回答：\n{cur_ctx.answer_md}\n\n"
                    f"资料列表：\n{src_block}\n\n"
                    "请修复后重新输出 JSON（{\"answer_md\": ..., \"card\": ..., \"citations\": ...}），"
                    "只允许引用资料中存在的编号，修不了的信息缺口如实说明。"},
            ]
            d, tk = llm.chat_json(fix_messages, temperature=0.2)
            new_answer = (d.get("answer_md") or cur_ctx.answer_md).strip() or cur_ctx.answer_md
            new_card = _parse_card(d)
            new_cites = [Citation(**c) for c in (d.get("citations") or []) if c.get("source")]
            if new_cites:
                citations.clear()
                citations.extend(new_cites)
            if new_card is not None:
                card_obj = new_card
            on_event("guardrail_repair", {"issues": report.to_fix_prompt()[:200], "tokens": tk})
            return replace(cur_ctx, answer_md=new_answer, card=new_card or cur_ctx.card), tk

        gr = runner.run(ctx, repair_fn)
        answer_md = gr.final_answer_md
        if gr.final_context is not None and gr.final_context.card is not None:
            card_obj = gr.final_context.card
        guardrail_trace = gr.final_report.to_trace()
        extra_tokens = gr.extra_tokens
        repaired = gr.repaired
        repair_rounds = gr.repair_rounds
        unresolved = gr.unresolved
        remaining_issues = [f"{v.name}：{v.violation}" for v in gr.final_report.blockers]

    card_dict = card_obj.model_dump() if card_obj is not None else None
    result = TeamResult(
        intent=query.intent, answer_md=answer_md, structured=card_dict,
        sources=indexed_sources,
        citations=citations, tasks=list(results.values()),
        tokens=gen_tokens + extra_tokens,
        duration_s=round(time.time() - t0, 1),
        degradations=[r.goal for r in results.values() if r.degraded],
        reflect_issues=remaining_issues,
        guardrail=guardrail_trace, guardrail_extra_tokens=extra_tokens,
        guardrail_repaired=repaired, guardrail_repair_rounds=repair_rounds,
        guardrail_unresolved=unresolved)
    return result
