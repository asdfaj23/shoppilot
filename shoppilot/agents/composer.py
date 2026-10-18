"""
ShopPilot composer.py · 成稿：聚合子结果 → 结构化产出卡 + 强制引用 → 五项确定性自检
================================================================================
职责（合并原 aggregator + reflector，刻意不再拆两个 Agent）：
  1. 把各子任务结论 + 资料列表交给 LLM，产出「回答 markdown + 结构化卡 + 引用编号」；
  2. 用确定性规则自检（引用越界 / 超预算 / 商品溯源 / 误拒答 / 目标覆盖）——零 token、可回归；
  3. 不过关才让 LLM 修复一次（LLM 只做修复，不做裁判）。

产出卡按意图三选一：Recommendation（推荐清单）/ CompareCard（对比维度表）/ CaveatList（避坑清单）。
"""
from __future__ import annotations

import re
import time

from shoppilot.models import (CaveatList, Citation, CompareCard, Recommendation,
                              ResearchReport, ShoppingQuery, SubTaskResult, TeamResult)

COMPOSE_SYSTEM = """你是导购成稿者。基于各子任务结论输出 JSON：
{"answer_md": "给用户看的最终回答（Markdown，简洁分点，关键数字保留原值，关键结论后标 [n]）",
 "card": 结构化卡片（三选一）, "citations": [{"no":1,"source":"来源名","heading":"小节或任务名","text":"原文片段"}]}

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

CITE_PATTERN = re.compile(r"\[(\d{1,2})\]")
REFUSAL_WORDS = ("没有相关资料", "无法回答", "知识库中没有", "查询不到该商品")


def compose(query: ShoppingQuery, results: dict[int, SubTaskResult], sources: list[dict],
            memory_block: str = "", tracer=None, on_event=None) -> TeamResult:
    from shoppilot.llm import get_llm

    llm = get_llm()
    on_event = on_event or (lambda *_: None)
    t0 = time.time()

    sub_block = "\n\n".join(
        f"[任务{r.task_id}]（{r.task_type}{'，已降级' if r.degraded else ''}）{r.goal}\n{r.output}"
        for r in results.values())
    src_block = "\n".join(f"[{i + 1}] {s['source']} | {s.get('heading', '')}\n{s.get('text', '')[:150]}"
                          for i, s in enumerate(sources))
    messages = [{"role": "system", "content": COMPOSE_SYSTEM}, {"role": "user", "content":
                f"用户输入：{query.raw_text}\n意图：{query.intent}\n"
                f"预算：{'≤' + str(int(query.budget_max)) + '元' if query.budget_max else '无'}\n"
                f"在意维度：{'、'.join(query.concerns) or '未说明'}"
                + (f"\n\n[记忆]\n{memory_block}" if memory_block else "")
                + f"\n\n各子任务结论：\n{sub_block}\n\n资料列表（引用编号依据）：\n{src_block}"}]

    answer_md, card, citations, tokens = "", None, [], 0
    for attempt in (1, 2):
        try:
            data, tokens = llm.chat_json(messages, temperature=0.3)
            answer_md = (data.get("answer_md") or "").strip()
            if not answer_md:
                raise ValueError("answer_md 为空")
            card = data.get("card") or None
            if card:
                model = {"recommendation": Recommendation, "compare": CompareCard,
                         "caveats": CaveatList, "research": ResearchReport}.get(card.get("kind"))
                if model:
                    card = model(**card).model_dump()
            citations = [Citation(**c) for c in (data.get("citations") or []) if c.get("source")]
            break
        except Exception as e:
            if tracer:
                tracer.log("compose", f"retry{attempt}", "json_output", str(e)[:200])
            messages.append({"role": "user",
                             "content": f"输出有误（{str(e)[:160]}），严格按规则重新输出 JSON。"})
    if not answer_md:  # 两次都失败 → 兜底拼接（不炸链路）
        answer_md = "\n\n".join(f"**[任务{r.task_id}]** {r.output}" for r in results.values())
    on_event("compose_end", {"tokens": tokens, "duration_s": round(time.time() - t0, 1)})
    if tracer:
        tracer.log("compose", "Composer", query.raw_text, answer_md[:300], tokens=tokens)

    result = TeamResult(intent=query.intent, answer_md=answer_md, structured=card,
                        citations=citations, tasks=list(results.values()), tokens=tokens)
    result.reflect_issues = _self_check(answer_md, citations, sources, query, card)
    if result.reflect_issues:  # 修复一次
        issues = "\n- ".join(result.reflect_issues)
        fix_prompt = (f"你的导购回答存在以下问题：\n- {issues}\n\n原回答：\n{answer_md}\n"
                      f"结构化卡：{card}\n\n资料列表：\n{src_block}\n\n"
                      "请修复后重新输出 JSON（{\"answer_md\": ..., \"card\": ...}），"
                      "只允许引用资料中存在的编号，修不了的信息缺口如实说明。")
        try:
            data, tk = llm.chat_json([{"role": "system", "content": "你是导购质检修复器，只输出 JSON。"},
                                      {"role": "user", "content": fix_prompt}], temperature=0.2)
            result.tokens += tk
            if data.get("answer_md"):
                result.answer_md = data["answer_md"]
            if data.get("card"):
                model = {"recommendation": Recommendation, "compare": CompareCard,
                         "caveats": CaveatList, "research": ResearchReport}.get(
                    data["card"].get("kind"))
                if model:
                    result.structured = model(**data["card"]).model_dump()
            result.reflect_issues = [f"已修复：{i}" for i in result.reflect_issues]
        except Exception:
            pass
    on_event("reflect_end", {"ok": not result.reflect_issues, "issues": result.reflect_issues})
    return result


def _self_check(answer_md: str, citations: list[Citation], sources: list[dict],
                query: ShoppingQuery, card: dict | None) -> list[str]:
    """五项确定性自检（零 token、可回归）：引用越界 / 超预算 / 商品溯源 / 误拒答 / 目标覆盖。"""
    issues: list[str] = []
    known = {c.no for c in citations} | set(range(1, len(sources) + 1))
    bad = {int(m) for m in CITE_PATTERN.findall(answer_md or "")} - known
    if bad:
        issues.append(f"答案引用了不存在的编号 {sorted(bad)}（引用可验证性失败）")
    if card and card.get("kind") == "recommendation" and query.budget_max:
        over = [i.get("name") for i in card.get("items", [])
                if isinstance(i.get("price"), (int, float)) and i["price"] > query.budget_max]
        if over:
            issues.append(f"推荐商品 {over} 超出预算 {query.budget_max} 元（约束失败）")
    if card and card.get("kind") == "recommendation":
        pool = " ".join(str(s.get("text", "")) for s in sources) + query.raw_text
        unknown = [i.get("name") for i in card.get("items", [])
                   if i.get("name") and str(i["name"]) not in pool]
        if unknown:
            issues.append(f"推荐商品 {unknown} 不在资料或商品池中（商品溯源失败）")
    if any(w in (answer_md or "") for w in REFUSAL_WORDS) and sources \
            and any(not str(s.get("source", "")).startswith("任务") for s in sources) \
            and query.intent == "after_sales":
        issues.append("知识库已检索到资料却回答'没有相关资料'（误拒答）")
    missing = [e for e in query.entities if e and e not in (answer_md or "")]
    if missing:
        issues.append(f"用户点名的商品 {missing} 未在回答中出现（目标覆盖失败）")
    if query.intent in ("recommend", "compare", "review_analysis", "research") and not card:
        issues.append("该场景应产出结构化卡但缺失（过早交付）")
    if card and card.get("kind") == "research":
        for field in ("conclusion", "evidence", "risks", "advice"):
            if not card.get(field):
                issues.append(f"研究报告缺少「{field}」（完整性失败）")
    return issues
