"""
ShopPilot domains/ecommerce · 6 个导购工具（注册进 ToolRegistry，schema 自动生成）
================================================================================
与 week2/tools.py 的三点升级：
  1. 参数契约用 Pydantic 模型写一遍，OpenAI schema 由 ToolRegistry 自动生成（单一事实源）；
  2. 每个工具声明 task_types → Dispatcher 按 task_type 预加载（无关工具不进上下文）；
  3. 返回统一 JSON 字符串、带 error 字段自描述——模型拿到错误能自我纠正。
工具清单：search_products / get_product_info / query_product_reviews / search_reviews /
query_sales_rank / daily_bad_review_trend
"""
from __future__ import annotations

import json

import pandas as pd
from pydantic import BaseModel, Field

from shoppilot.domains.ecommerce import adapter as domain
from shoppilot.domains.ecommerce import recommend
from shoppilot.harness.tools import ToolRegistry


def _j(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


# ---------------- 参数契约（Pydantic 单一事实源） ----------------
class SearchProductsArgs(BaseModel):
    category: str = Field(default="", description="品类：数码配件/智能穿戴/电脑外设/网络设备/智能家居，可空")
    budget_max: float | None = Field(default=None, description="预算上限（元）")
    budget_min: float | None = Field(default=None, description="预算下限（元）")
    keyword: str = Field(default="", description="关键词，如 降噪/快充/游戏")
    limit: int = Field(default=5, description="最多返回条数，默认5")


class GetProductInfoArgs(BaseModel):
    name: str = Field(description="商品名，如 蓝牙耳机A")


class QueryProductReviewsArgs(BaseModel):
    product: str = Field(description="商品名，如 蓝牙耳机A")
    aspect: str = Field(default="全部", description="分析维度：全部/物流/质量/音质/客服/描述不符/续航/降噪/兼容性，默认全部")


class SearchReviewsArgs(BaseModel):
    keyword: str = Field(description="关键词，如 退货、鼓包、降价")
    limit: int = Field(default=5, description="最多返回条数，默认5")


class QuerySalesRankArgs(BaseModel):
    category: str = Field(description="品类名，如 数码配件")


class DailyBadReviewTrendArgs(BaseModel):
    product: str = Field(description="商品名")
    days: int = Field(default=14, description="统计最近多少天，默认14")


# ---------------- 工具实现 ----------------
def search_products(category: str = "", budget_max: float | None = None,
                    budget_min: float | None = None, keyword: str = "",
                    limit: int = 5) -> str:
    """约束筛选：品类 + 预算 + 关键词（highlights 内模糊匹配）。"""
    df = domain.load_products()
    if category:
        hit = df[df["category"] == category]
        if hit.empty:  # 品类名不精确时降级为包含匹配
            hit = df[df["category"].str.contains(category, na=False)]
        df = hit
    if budget_max is not None:
        df = df[df["price"] <= budget_max]
    if budget_min is not None:
        df = df[df["price"] >= budget_min]
    if keyword:
        df = df[df["highlights"].str.contains(keyword, na=False) | df["name"].str.contains(keyword, na=False)]
    if df.empty:
        return _j({"error": "没有符合筛选条件的商品", "可选品类": domain.categories(),
                   "商品池": domain.product_names()})
    rows = df.sort_values("rating", ascending=False).head(limit)
    return _j({"count": int(len(rows)),
               "products": rows.to_dict(orient="records")})


def get_product_info(name: str) -> str:
    row = domain.product_row(name)
    if row is None:
        return _j({"error": f"未找到商品：{name}，可选：{' / '.join(domain.product_names())}"})
    return _j(row)


def query_product_reviews(product: str, aspect: str = "全部") -> str:
    rev = domain.load_reviews()
    matched = rev[rev["product"] == product]
    if matched.empty:  # 容错：模糊匹配一次，找不到则给商品池提示
        matched = rev[rev["product"].str.contains(str(product), na=False)]
    if matched.empty:
        return _j({"error": f"未找到商品：{product}，可选：{' / '.join(domain.product_names())}"})
    real_name = matched["product"].iloc[0]
    bad = matched[matched["rating"] <= domain.BAD_RATING]
    if aspect and aspect != "全部":
        bad = bad[bad["aspect"] == aspect]
    distribution = bad["aspect"].value_counts().to_dict() if not bad.empty else {}
    return _j({
        "product": real_name, "aspect": aspect,
        "total_reviews": int(len(matched)), "bad_reviews": int(len(bad)),
        "bad_rate": f"{len(bad) / max(len(matched), 1):.0%}",
        "aspect_distribution": distribution,
        "samples": [f"[{int(r.rating)}星/{r.aspect}] {r.text}" for r in bad.head(5).itertuples()],
    })


def search_reviews(keyword: str, limit: int = 5) -> str:
    rev = domain.load_reviews()
    hits = rev[rev["text"].str.contains(keyword, na=False)].head(limit)
    if hits.empty:
        return _j({"error": f"没有评论包含关键词：{keyword}",
                   "提示": "可尝试换近义词或先查商品差评分布"})
    return _j({"keyword": keyword,
               "hits": [{"product": r.product, "rating": int(r.rating),
                         "aspect": r.aspect, "date": r.date, "text": r.text}
                        for r in hits.itertuples()]})


def query_sales_rank(category: str) -> str:
    sales = domain.load_sales()
    rows = sales[sales["category"] == category].sort_values("rank")
    if rows.empty:
        rows = sales[sales["category"].str.contains(category, na=False)]
    if rows.empty:
        return _j({"error": f"未找到品类：{category}，可选品类见商品池",
                   "可选品类": domain.categories()})
    return _j({"category": category, "ranking": rows.to_dict(orient="records")})


def daily_bad_review_trend(product: str, days: int = 14) -> str:
    """差评按日趋势：判断"最近差评是不是变多了"（口碑解读场景的量化依据）。"""
    rev = domain.load_reviews()
    matched = rev[rev["product"] == product]
    if matched.empty:
        matched = rev[rev["product"].str.contains(str(product), na=False)]
    if matched.empty:
        return _j({"error": f"未找到商品：{product}，可选：{' / '.join(domain.product_names())}"})
    matched = matched.copy()
    matched["date"] = pd.to_datetime(matched["date"])
    recent = matched[matched["date"] >= matched["date"].max() - pd.Timedelta(days=days - 1)]
    bad = recent[recent["rating"] <= domain.BAD_RATING]
    trend = bad.groupby(bad["date"].dt.strftime("%m-%d")).size().to_dict()
    return _j({"product": product, "window_days": days,
               "bad_reviews_in_window": int(len(bad)),
               "daily_counts": trend,
               "note": "数据为教学样例集，日期跨度小属正常"})


# ---------------- 注册进共享 ToolRegistry ----------------
registry = ToolRegistry()
TASK_TOOL_MAP: dict[str, list[str]] = {  # task_type → 预加载工具（Aggregator/Dispatcher 共用）
    "data_query": ["search_products", "get_product_info", "query_sales_rank",
                   "daily_bad_review_trend"],
    "review_analysis": ["query_product_reviews", "search_reviews", "daily_bad_review_trend"],
    "knowledge_qa": [],
    "synthesis": [],
}


# 推荐打分单独实现在 recommend.py（业务打分逻辑），这里转出便于工具注册与 MCP 暴露
recommend_products = recommend.recommend_products


def register_all(reg: ToolRegistry | None = None) -> ToolRegistry:
    reg = reg or registry
    reg.register("recommend_products", recommend.recommend_products,
                 "按用户约束（品类/预算/在意维度）做可解释加权打分推荐：价格贴合·平台评分·评论均分·"
                 "销量热度·好评匹配·差评风险六维打分，输出得分构成、真实好评/差评原文与选择理由。"
                 "任何选购推荐类问题优先调它（比单纯筛选更准）。",
                 recommend.RecommendArgs, ["data_query"])
    reg.register("search_products", search_products,
                 "按品类/预算/关键词筛选商品池并返回候选商品（价格、评分、卖点）。仅在需要粗筛或"
                 "推荐工具不适用时使用。",
                 SearchProductsArgs, ["data_query"])
    reg.register("get_product_info", get_product_info,
                 "查单个商品详情：价格、评分、月销、保修时长、卖点。",
                 GetProductInfoArgs, ["data_query"])
    reg.register("query_product_reviews", query_product_reviews,
                 "查某商品评论统计：差评数、差评率、差评维度分布、典型评论原文。分析口碑/痛点/翻车点时用。",
                 QueryProductReviewsArgs, ["review_analysis"])
    reg.register("search_reviews", search_reviews,
                 "按关键词全文搜评论原文（模糊匹配）。找'提到XX的评论'时用。",
                 SearchReviewsArgs, ["review_analysis"])
    reg.register("query_sales_rank", query_sales_rank,
                 "查某品类销量排行榜（商品、销量、名次）。",
                 QuerySalesRankArgs, ["data_query"])
    reg.register("daily_bad_review_trend", daily_bad_review_trend,
                 "查某商品最近N天的差评按日趋势，判断差评是否变多。",
                 DailyBadReviewTrendArgs, ["review_analysis", "data_query"])
    return reg


register_all()


if __name__ == "__main__":
    # 自测：先测工具再测 Agent（分层排错），不调模型
    print(search_products(budget_max=500, keyword="降噪"))
    print(query_product_reviews("蓝牙耳机A"))
    print(daily_bad_review_trend("蓝牙耳机A"))
    print(query_sales_rank("数码配件"))
    print(search_reviews("鼓包"))
    print(get_product_info("蓝牙耳机F"))
    print("预加载(data_query):", [s["function"]["name"] for s in registry.preload("data_query")])
