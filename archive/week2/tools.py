"""
Week2 · 业务工具层：pandas 读 data/ 下的 CSV（Week1 mock 数据的真实化版本）
================================================================================
与 week1/react_tools.py 的差别只有一处：数据从内存列表 → CSV 文件。
工具 schema、函数签名、返回格式完全一致 —— 这就是"业务换了，工具接口不变"的分层思想。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

REVIEWS = pd.read_csv(DATA_DIR / "reviews.csv", encoding="utf-8")
SALES = pd.read_csv(DATA_DIR / "sales.csv", encoding="utf-8")
PRODUCTS = sorted(REVIEWS["product"].unique().tolist())
CATEGORIES = sorted(SALES["category"].unique().tolist())


def query_product_reviews(product: str, aspect: str = "全部") -> str:
    """按商品 + 维度统计差评（rating<=3 视为差评），返回 JSON 字符串。"""
    matched = REVIEWS[REVIEWS["product"] == product]
    if matched.empty:
        return json.dumps(
            {"error": f"未找到商品：{product}，可选：{' / '.join(PRODUCTS)}"},
            ensure_ascii=False,
        )

    bad = matched[matched["rating"] <= 3]
    if aspect and aspect != "全部":
        bad = bad[bad["aspect"] == aspect]

    # pandas 一行算出"差评维度分布"，比手写循环简洁
    distribution = bad["aspect"].value_counts().to_dict() if not bad.empty else {}

    return json.dumps(
        {
            "product": product,
            "aspect": aspect,
            "total_reviews": int(len(matched)),
            "bad_reviews": int(len(bad)),
            "bad_rate": f"{len(bad) / len(matched):.0%}",
            "aspect_distribution": distribution,
            "samples": [
                f"[{int(r.rating)}星/{r.aspect}] {r.text}" for r in bad.head(5).itertuples()
            ],
        },
        ensure_ascii=False,
    )


def query_sales_rank(category: str) -> str:
    """查询某品类销量排行，返回 JSON 字符串。"""
    rows = SALES[SALES["category"] == category].sort_values("rank")
    if rows.empty:
        return json.dumps(
            {"error": f"未找到品类：{category}，可选：{' / '.join(CATEGORIES)}"},
            ensure_ascii=False,
        )
    return json.dumps(
        {"category": category, "ranking": rows.to_dict(orient="records")},
        ensure_ascii=False,
    )


def search_reviews(keyword: str, limit: int = 5) -> str:
    """按关键词全文搜评论原文（模糊匹配），返回 JSON 字符串。"""
    hits = REVIEWS[REVIEWS["text"].str.contains(keyword, na=False)].head(limit)
    if hits.empty:
        return json.dumps({"error": f"没有评论包含关键词：{keyword}"}, ensure_ascii=False)
    return json.dumps(
        {
            "keyword": keyword,
            "hits": [
                {"product": r.product, "rating": int(r.rating), "aspect": r.aspect, "text": r.text}
                for r in hits.itertuples()
            ],
        },
        ensure_ascii=False,
    )


# ---------- 工具 Schema（发给模型的"合同"，与 Week1 同款写法） ----------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_product_reviews",
            "description": (
                "查询某商品的评论统计：差评数量、差评率、差评维度分布和典型评论原文。"
                "当用户要分析差评、口碑、用户痛点、退货原因时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "商品名，如 蓝牙耳机A"},
                    "aspect": {
                        "type": "string",
                        "description": "分析维度：全部/物流/质量/音质/客服/描述不符/续航，默认全部",
                    },
                },
                "required": ["product"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_sales_rank",
            "description": "查询某品类的销量排行榜。当需要销量、排名、热销情况时调用。",
            "parameters": {
                "type": "object",
                "properties": {"category": {"type": "string", "description": "品类名，如 数码配件"}},
                "required": ["category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_reviews",
            "description": "按关键词搜索评论原文（模糊匹配），适合找'提到XX的评论'这类问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "关键词，如 退货、鼓包、降噪"},
                    "limit": {"type": "integer", "description": "最多返回条数，默认5"},
                },
                "required": ["keyword"],
            },
        },
    },
]

TOOL_MAP = {
    "query_product_reviews": query_product_reviews,
    "query_sales_rank": query_sales_rank,
    "search_reviews": search_reviews,
}


if __name__ == "__main__":
    # 自测：不调模型也能先验证工具本身对不对（先测工具、再测 Agent，分层排错）
    print(query_product_reviews("蓝牙耳机A"))
    print(query_sales_rank("数码配件"))
    print(search_reviews("退货"))
