"""
ShopPilot domains/ecommerce · 推荐打分（用户选择理由的可解释建模）
================================================================================
推荐不是"过滤+按评分排序"（那是 SQL）。真实用户挑商品主要看这六件事，本文件把它们做成
**可解释加权打分**，并把**好评/差评原文**一起带出来，让 LLM 直接讲成"为什么推荐它"：

  ① 价格     预算贴合度（买得起且不浪费预算）
  ② 平台评分 商品表 rating（4 分制）
  ③ 评论均分 真实评论星级均值（比平台评分更抗刷）
  ④ 销量热度 月销量归一（大众选择的统计信号）
  ⑤ 好评匹配 好评主题是否命中用户在意维度（降噪/续航/拍照…）
  ⑥ 差评风险 1 - 差评率，并对"集中在关键维度"的差评追加惩罚
  ⑦ 物流时效 次日达>2-3天>慢递（电商转化关键信号，急用决策依据）

输出：总分 + 分项得分 + 优点(好评原文) + 缺点(差评原文) + 选择理由 decision_reason
"""
from __future__ import annotations

import json

import pandas as pd
from pydantic import BaseModel, Field

from shoppilot.domains.ecommerce import adapter as domain

WEIGHTS = {"price": 0.13, "rating": 0.16, "review_avg": 0.10, "popularity": 0.08,
           "pros": 0.22, "cons": 0.18, "delivery": 0.13}

# 在意维度 → (卖点关键词, 好评关键词)  卖点用于硬召回，好评用于软加分
CONCERN_MAP = {
    "降噪": (["降噪", "静音", "NC"], ["降噪强", "听不到", "安静", "降噪好"]),
    "续航": (["续航", "电池", "mAh", "充电"], ["续航好", "一天半", "够用", "充电快"]),
    "拍照": (["拍照", "长焦", "潜望", "影像", "像素"], ["拍照好看", "直出", "夜景", "很爽"]),
    "游戏": (["游戏", "高刷", "120Hz", "144Hz", "帧"], ["跟得上", "不掉帧", "延迟低"]),
    "性能": (["骁龙", "天玑", "芯片", "RTX", "处理器"], ["不烫手", "够用", "跟得上"]),
    "轻薄": (["轻薄", "便携", "重量"], ["很轻", "没负担", "便携"]),
    "音质": (["音质", "LDAC", "编解码", "环绕声"], ["音质好", "细腻", "有质感"]),
    "安全": (["锁芯", "指纹", "防水"], ["安全", "放心", "稳"]),
    "性价比": (["性价比", "千元", "入门"], ["很强", "值", "够用"]),
    "静音": (["静音", "降噪"], ["安静", "听不到"]),
}

# 命中即为"硬伤"的差评维度（这些维度出问题会影响决策，需降权）
CRITICAL_ASPECTS = {"质量", "做工", "续航", "发热", "降价", "价格"}


class RecommendArgs(BaseModel):
    category: str = Field(default="", description="品类，可空")
    budget_max: float | None = Field(default=None, description="预算上限（元）")
    budget_min: float | None = Field(default=None, description="预算下限（元）")
    concerns: list[str] = Field(default_factory=list,
                               description="在意维度：降噪/续航/拍照/游戏/性能/轻薄/音质…")
    limit: int = Field(default=5, description="返回条数，默认5")


def review_aggregates(concerns: list[str] | None = None) -> dict[str, dict]:
    """每个商品 → 评论均分、好评/差评统计与原文样本。
    好评样本优先取 5 星且命中在意维度；差评样本优先取 1-2 星（严重问题）。"""
    concerns = concerns or []
    pro_kws = [k for c in concerns for k in CONCERN_MAP.get(c, (c, []))[1]]
    rev = domain.load_reviews()
    out: dict[str, dict] = {}
    for name, grp in rev.groupby("product"):
        pros = grp[grp["rating"] >= 4]
        cons = grp[grp["rating"] <= domain.BAD_RATING]
        five = pros[pros["rating"] == 5]
        if pro_kws and not five.empty:      # 优先展示"五星且命中在意维度"的好评
            hit5 = five[five["text"].str.contains("|".join(pro_kws), na=False)]
            if not hit5.empty:
                five = hit5
        hard = cons[cons["rating"] <= 2] if not cons.empty else cons
        pick_cons = hard if not hard.empty else cons
        out[name] = {
            "total": len(grp),
            "avg": float(grp["rating"].mean()),
            "bad_rate": len(cons) / len(grp),
            "bad_aspects": cons["aspect"].value_counts().to_dict(),
            "good_aspects": pros["aspect"].value_counts().to_dict(),
            "pro_samples": [f"{r.aspect}：{r.text}" for r in five.head(2).itertuples()],
            "con_samples": [f"{r.aspect}：{r.text}" for r in pick_cons.head(2).itertuples()],
        }
    return out


def recommend_products(category: str = "", budget_max: float | None = None,
                       budget_min: float | None = None, concerns: list[str] | None = None,
                       limit: int = 5) -> str:
    """按加权打分推荐商品，输出得分构成 + 好评/差评 + 选择理由。"""
    df = domain.load_products().copy()
    if category:
        hit = df[df["category"] == category]
        df = hit if not hit.empty else df[df["category"].str.contains(category, na=False)]
    if budget_max is not None:
        df = df[df["price"] <= budget_max]
    if budget_min is not None:
        df = df[df["price"] >= budget_min]
    if df.empty:
        return json.dumps({"error": "没有符合约束的商品", "可选品类": domain.categories()},
                          ensure_ascii=False)

    df["rating_n"] = (df["rating"] - 3.0) / 2.0
    max_sales = max(df["sales_month"].max(), 1)
    df["pop_n"] = df["sales_month"] / max_sales
    if budget_max:
        span = max(budget_max - df["price"].min(), 1e-6)
        df["budget_n"] = (1 - (budget_max - df["price"]) / span).clip(0, 1)
    else:
        df["budget_n"] = 0.5

    # 物流时效归一化：1天=1.0（最快），4天=0.25；次日达是电商转化关键信号
    if "delivery_days" in df.columns:
        df["deliver_n"] = (1 - (df["delivery_days"].clip(1, 5) - 1) / 4).clip(0, 1)
    else:
        df["deliver_n"] = 0.5

    concerns = concerns or []
    agg = review_aggregates(concerns)
    weight_of = {"价格(预算贴合)": WEIGHTS["price"], "平台评分": WEIGHTS["rating"],
                 "评论均分": WEIGHTS["review_avg"], "销量热度": WEIGHTS["popularity"],
                 "好评匹配": WEIGHTS["pros"], "差评风险(1-差评率)": WEIGHTS["cons"],
                 "物流时效": WEIGHTS["delivery"]}

    results = []
    for row in df.itertuples():
        a = agg.get(row.name)
        hl = str(row.highlights).lower()

        # 卖点硬召回：至少命中一个在意维度
        hit_sell = [c for c in concerns
                    if any(k.lower() in hl for k in CONCERN_MAP.get(c, (c, []))[0])]
        if concerns and not hit_sell:
            continue

        # 好评匹配：在意维度是否出现在好评里
        pro_txt = " ".join(a["pro_samples"]) if a else ""
        pro_aspects = set(a["good_aspects"]) if a else set()
        hit_pro = [c for c in concerns
                   if c in pro_aspects or any(k in pro_txt for k in CONCERN_MAP.get(c, (c, []))[1])]
        pros_n = len(hit_pro) / len(concerns) if concerns else (0.7 if a else 0.4)

        # 差评风险：差评率 + 关键维度集中惩罚
        if a:
            cons_n = 1 - min(a["bad_rate"], 1.0)
            critical = sum(v for k, v in a["bad_aspects"].items() if k in CRITICAL_ASPECTS)
            cons_n *= (1 - min(critical / max(a["total"], 1), 0.5))
        else:
            cons_n = 0.55  # 无评论数据：中性偏低，避免无依据地吹

        parts = {
            "价格(预算贴合)": WEIGHTS["price"] * row.budget_n,
            "平台评分": WEIGHTS["rating"] * row.rating_n,
            "评论均分": WEIGHTS["review_avg"] * ((a["avg"] - 3) / 2 if a else 0.4),
            "销量热度": WEIGHTS["popularity"] * row.pop_n,
            "好评匹配": WEIGHTS["pros"] * pros_n,
            "差评风险(1-差评率)": WEIGHTS["cons"] * cons_n,
            "物流时效": WEIGHTS["delivery"] * row.deliver_n,
        }
        total = sum(parts.values())

        pros = a["pro_samples"] if a else []
        cons = a["con_samples"] if a else []
        reasons = []
        if hit_sell:
            reasons.append(f"卖点命中{'、'.join(hit_sell)}")
        if hit_pro:
            reasons.append(f"好评印证：{'、'.join(hit_pro)}")
        if row.rating >= 4.5:
            reasons.append(f"平台评分 {row.rating} 属高分档")
        if row.price <= (budget_max or row.price):
            reasons.append(f"价格在预算内（¥{int(row.price)}）")
        ddays = int(getattr(row, "delivery_days", 2))
        if ddays <= 1:
            reasons.append("次日达，物流快")
        elif ddays >= 4:
            warnings.append(f"物流时效 {ddays} 天，急用慎选")

        warnings = []
        if a and a["bad_rate"] >= 0.3:
            top = "、".join(list(a["bad_aspects"])[:2]) if a["bad_aspects"] else "未知"
            warnings.append(f"差评率 {a['bad_rate']:.0%}，集中在{top}")
        if budget_max and row.price > budget_max:
            warnings.append("超预算")

        top_dim = max(parts, key=parts.get)
        decision = (f"选它主要因为{top_dim.replace('(1-差评率)', '')}占优"
                    f"（得分 {parts[top_dim]:.2f} / 权重 {weight_of[top_dim]:.2f}）"
                    + (f"；命中你在意的{'、'.join(hit_sell or hit_pro)}" if (hit_sell or hit_pro) else "")
                    + (f"；需注意{'；'.join(warnings)}" if warnings else ""))

        results.append({
            "name": row.name, "category": row.category, "price": float(row.price),
            "rating": float(row.rating), "sales_month": int(row.sales_month),
            "warranty_months": int(row.warranty_months),
            "delivery_days": int(getattr(row, "delivery_days", 2)),
            "review_avg": round(a["avg"], 2) if a else None,
            "bad_rate": round(a["bad_rate"], 3) if a else None,
            "total_score": round(total, 3),
            "score_parts": {k: round(v, 3) for k, v in parts.items()},
            "pros": pros, "cons": cons,
            "reasons": reasons, "warnings": warnings,
            "decision_reason": decision,
            "highlights": str(row.highlights).split("；"),
        })

    results.sort(key=lambda x: -x["total_score"])
    return json.dumps({
        "weights": WEIGHTS, "concerns": concerns, "candidates": len(results),
        "recommendations": results[:limit],
        "note": "评分维度=价格/平台评分/评论均分/销量/好评匹配/差评风险/物流时效；decision_reason 为可直接引用的选择理由",
    }, ensure_ascii=False)


if __name__ == "__main__":
    for q in [dict(budget_max=500, concerns=["降噪", "续航"]),
              dict(category="手机通讯", budget_max=4000, concerns=["拍照", "续航"]),
              dict(category="电脑整机", budget_max=6000, concerns=["轻薄", "性能"])]:
        print(recommend_products(**q)[:900], "\n" + "-" * 60)
