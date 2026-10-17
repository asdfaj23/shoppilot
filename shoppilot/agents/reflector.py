"""
ShopPilot agents · Reflector：自检四项 + 触发一次修复
================================================================================
为什么自检要确定性规则而不是再问一次 LLM（面试必问）：
  LLM 自评会"自我讨好"（自我一致性偏差）；引用编号错、数字超预算、误拒答这类错误
  用规则判断 100% 可靠且零 token——LLM 只负责规则查不出来之后的"修复"这一步。
四项自检 + 一项目标检查（对应 kamacoder「Reflection 要有明确验收标准、拦过早交付」）：
  ① 引用可验证：answer_md 里的 [n] 必须在 citations/资料列表范围内（防编造来源）；
  ② 约束满足：recommend 意图下结构化卡内商品价格不得超出用户预算；
  ③ 数字溯源：卡内出现的商品必须能在资料/任务结论里找到名字（防编造商品）；
  ④ 误拒答：知识库有资料却回答"没有相关资料" → 误拒，触发修复；
  ⑤ 目标覆盖：用户点名的商品必须出现在回答里（防答非所问/简称丢名），且
     recommend/compare/review_analysis 场景必须产出结构化卡（防"半成品交付"）。
"""
from __future__ import annotations

import json
import re

from shoppilot.models import ShoppingQuery, SubTaskResult, TeamResult

CITE_PATTERN = re.compile(r"\[(\d{1,2})\]")
REFUSAL_WORDS = ("没有相关资料", "无法回答", "查询不到", "知识库中没有", "未能查询到", "没有找到相关")


def deterministic_checks(answer_md: str, citations: list[dict], sources: list[dict],
                         query: ShoppingQuery, card: dict | None) -> list[str]:
    """返回问题清单（空 = 通过）。citations 为编号列表，sources 为全链路资料列表。"""
    issues: list[str] = []
    known = {c.get("no") for c in citations} | set(range(1, len(sources) + 1))
    cited = {int(m) for m in CITE_PATTERN.findall(answer_md or "")}
    bad = cited - known
    if bad:
        issues.append(f"答案引用了不存在的编号 {sorted(bad)}（引用可验证性失败）")

    if card and card.get("kind") == "recommendation" and query.budget_max:
        over = [it.get("name") for it in card.get("items", [])
                if isinstance(it.get("price"), (int, float)) and it["price"] > query.budget_max]
        if over:
            issues.append(f"推荐商品 {over} 价格超出预算 {query.budget_max} 元（约束满足失败）")

    if card and card.get("kind") == "recommendation":
        pool_text = json.dumps(sources, ensure_ascii=False) + query.raw_text
        unknown = [it.get("name") for it in card.get("items", [])
                   if it.get("name") and str(it["name"]) not in pool_text]
        if unknown:
            issues.append(f"推荐商品 {unknown} 不在资料或商品池中（数字/商品溯源失败）")

    lowered = answer_md or ""
    if any(w in lowered for w in REFUSAL_WORDS) and sources:
        has_kb = any(not str(s.get("source", "")).startswith("任务") for s in sources)
        if has_kb and query.intent == "after_sales":
            issues.append("知识库已检索到资料却回答'没有相关资料'（误拒答）")

    # ⑤ 目标覆盖 / 过早交付拦截（Reflection 的"明确验收标准"）
    if lowered:
        missing = [e for e in query.entities if e and e not in lowered]
        if missing:
            issues.append(f"用户点名的商品 {missing} 未在回答中出现（目标覆盖失败/疑似答非所问）")
    if query.intent in ("recommend", "compare", "review_analysis") and not card:
        issues.append("该场景应产出结构化卡但缺失（过早交付）")
    return issues


def reflect_and_repair(query: ShoppingQuery, result: TeamResult, sources: list[dict],
                       llm=None, on_event=None) -> TeamResult:
    """四项自检；有问题 → 把问题清单喂回 LLM 修复一次（重出 answer_md 与 card）。"""
    from shoppilot.llm import get_llm

    on_event = on_event or (lambda *_: None)
    issues = deterministic_checks(result.answer_md,
                                  [c.model_dump() for c in result.citations],
                                  sources, query, result.structured)
    result.reflect_issues = issues
    if not issues:
        on_event("reflect_end", {"ok": True, "issues": []})
        return result

    on_event("reflect_end", {"ok": False, "issues": issues})
    llm = llm or get_llm()
    src_block = "\n".join(f"[{i + 1}] {s.get('source', '')} | {s.get('heading', '')}: {s.get('text', '')[:80]}"
                          for i, s in enumerate(sources))
    prompt = (f"你的导购回答存在以下问题：\n- " + "\n- ".join(issues) +
              f"\n\n原回答：\n{result.answer_md}\n"
              f"结构化卡：\n{json.dumps(result.structured, ensure_ascii=False) if result.structured else '（无）'}\n"
              f"可用资料（引用编号依据）：\n{src_block}\n\n"
              "请修复后重新输出 JSON：{\"answer_md\": \"...\", \"card\": 原卡修正或null}。"
              "只允许引用资料列表中存在的来源；修复不了的信息缺口要如实说明。")
    try:
        data, _ = llm.chat_json([{"role": "system", "content":
                                  "你是导购回答质检修复器，输出严格 JSON。"}, {"role": "user", "content": prompt}],
                                temperature=0.2)
        if data.get("answer_md"):
            result.answer_md = data["answer_md"]
        if data.get("card"):
            from shoppilot.models import CaveatList, CompareCard, Recommendation

            card = data["card"]
            model = {"recommendation": Recommendation, "compare": CompareCard,
                     "caveats": CaveatList}.get(card.get("kind"))
            if model:
                result.structured = model(**card).model_dump()
        result.reflect_issues = [f"已修复：{i}" for i in issues]
    except Exception:
        result.reflect_issues = issues + ["（修复失败，保留原回答并已降级标注）"]
    return result
