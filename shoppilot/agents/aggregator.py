"""
ShopPilot agents · Aggregator：冲突仲裁 + 强制引用 + 结构化产出卡（吸收 week4 的汇总器）
================================================================================
week4 的 Aggregator 只会拼 markdown；本版让它直接产出**结构化 JSON 卡**
（Recommendation / CompareCard / CaveatList，按意图路由一种），前端据此渲染推荐清单卡、
对比维度表格、避坑清单——"数据进、卡出"而不是自由文本。
强制引用：资料列表（知识库 chunk + 数据任务结论）预先编号，要求每个关键结论标 [n]，
Reflector 据此做确定性校验。子结论冲突（数据说差评率高、手册说质量可靠）必须点名采信方与理由。
"""
from __future__ import annotations

import json

from shoppilot.models import (CaveatList, Citation, CompareCard, Recommendation,
                              ShoppingQuery, SubTaskResult)

AGGREGATOR_SYSTEM = """你是导购报告聚合器。基于各子任务结论输出 JSON：
{"answer_md": "给用户看的最终回答（Markdown，简洁分点，关键数字保留原值，每个关键结论后标 [n]）",
 "card": 结构化卡片（三选一）,
 "citations": [{"no": 1, "source": "来源名", "heading": "小节或任务名", "text": "原文片段"}]}

card 按意图三选一（不要混用）：
- recommend → {"kind":"recommendation","summary":"一句话总评","items":[{"name":"蓝牙耳机A","price":349,"rating":4.3,"category":"数码配件","reason":"推荐理由","review_summary":"口碑摘要","citation":1}]}
- compare → {"kind":"compare","subjects":["蓝牙耳机A","蓝牙耳机F"],"dimensions":[{"维度":"价格","A":"349元","B":"899元"},{"维度":"降噪","A":"35dB","B":"45dB"}],"conclusion":"分场景给结论","citations":[]}
- review_analysis → {"kind":"caveats","summary":"痛点总评","items":[{"product":"蓝牙耳机A","issue":"音质","detail":"低音杂音、人声发闷","citation":1}]}
- after_sales → 不出 card（card 传 null），answer_md 中必须带 [n] 引用

硬性要求：
1. 只允许输出资料列表里给出的来源编号 [n]，禁止编造编号或来源；
2. 子任务结论冲突时，在 answer_md 中明确指出冲突并说明你采信哪方及理由；
3. 禁止编造任何子任务结论里没有的数字；推荐商品价格不得超过用户预算约束；
4. 商品一律使用全称（如"蓝牙耳机F"，禁止简写成"耳机F"），用户点名的商品必须出现在回答中；
5. 子任务降级失败的信息缺失，如实说明"未能查询到"，不要脑补。"""


def aggregate(query: ShoppingQuery, results: dict[int, SubTaskResult],
              sources: list[dict], llm=None, on_event=None
              ) -> tuple[str, dict | None, list[Citation], int]:
    """返回 (answer_md, card_dict|None, citations, tokens)。"""
    import time

    from shoppilot.llm import get_llm

    llm = llm or get_llm()
    on_event = on_event or (lambda *_: None)

    numbered = []  # 给 LLM 的资料列表（编号 ↔ 来源一一对应）
    for i, s in enumerate(sources):
        numbered.append({"no": i + 1, **s})
    sub_block = "\n\n".join(f"[任务{r.task_id}]（{r.task_type}{'，已降级' if r.degraded else ''}）{r.goal}\n{r.output}"
                            for r in results.values())
    src_block = "\n".join(f"[{s['no']}] {s['source']} | {s.get('heading', '')}\n{s.get('text', '')[:150]}"
                          for s in numbered)

    messages = [
        {"role": "system", "content": AGGREGATOR_SYSTEM},
        {"role": "user", "content":
            f"用户输入：{query.raw_text}\n意图：{query.intent}\n"
            f"预算约束：{'≤' + str(query.budget_max) + '元' if query.budget_max else '无'}\n"
            f"在意维度：{'、'.join(query.concerns) or '未说明'}\n\n"
            f"各子任务结论：\n{sub_block}\n\n资料列表（引用编号依据）：\n{src_block}"},
    ]
    t0 = time.time()
    last_err = ""
    for attempt in (1, 2):
        try:
            data, tokens = llm.chat_json(messages, temperature=0.3)
            answer_md = (data.get("answer_md") or "").strip()
            if not answer_md:
                raise ValueError("answer_md 为空")
            card = data.get("card") or None
            if card:  # Pydantic 契约校验 + 清洗（脏字段在模型层修好）
                kind = card.get("kind")
                card = {"recommendation": Recommendation, "compare": CompareCard,
                        "caveats": CaveatList}.get(kind, lambda **kw: None)(**card).model_dump()
            citations = [Citation(no=int(c.get("no", 0)), source=str(c.get("source", "")),
                                  heading=str(c.get("heading", "")), text=str(c.get("text", "")))
                         for c in data.get("citations") or []]
            citations = [c for c in citations if c.source]
            on_event("aggregate_end", {"tokens": tokens, "duration_s": round(time.time() - t0, 1)})
            return answer_md, card, citations, tokens
        except Exception as e:
            last_err = str(e)
            on_event("aggregate_retry", {"attempt": attempt, "error": last_err[:160]})
            messages.append({"role": "user",
                             "content": f"你的输出有误（{last_err[:200]}），严格按规则重新输出 JSON。"})
    # 两次都失败：降级为纯文本拼接（不炸链路）
    fallback = "\n\n".join(f"**[任务{r.task_id}]** {r.output}" for r in results.values())
    return fallback, None, [], 0
