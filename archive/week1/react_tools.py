"""
Week1 · 电商工具：Function Calling 的"合同" + 真实实现
====================================================================
一个工具由四部分组成（面试常问"你怎么定义工具的"）：
  ① name        函数名（模型按这个名字调）
  ② description 描述（模型靠它判断"什么时候该调我"，写得越清楚调用越准）
  ③ parameters  参数 JSON Schema（约束模型怎么传参，等价于"函数签名"）
  ④ 真实函数    本地执行，结果序列化后以 role="tool" 喂回模型

Week1 用内存 mock 数据，方便聚焦 Loop 本身；
Week2 换成 data/*.csv（pandas 读，见 week2/tools.py），逻辑完全一样。
"""
import json

# ---------- 1) mock 数据（模拟业务库表） ----------
REVIEWS = [
    {"product": "蓝牙耳机A", "rating": 1, "aspect": "物流", "text": "快递走了12天，外包装破损"},
    {"product": "蓝牙耳机A", "rating": 2, "aspect": "音质", "text": "低音杂音明显，戴久了耳朵疼"},
    {"product": "蓝牙耳机A", "rating": 2, "aspect": "质量", "text": "用了一周左耳就连不上，返修两次"},
    {"product": "蓝牙耳机A", "rating": 3, "aspect": "描述不符", "text": "宣传降噪40db，地铁里基本听不清"},
    {"product": "蓝牙耳机A", "rating": 1, "aspect": "客服", "text": "申请退货，客服三天不回消息"},
    {"product": "蓝牙耳机A", "rating": 4, "aspect": "物流", "text": "包装完好，但物流信息三天没更新"},
    {"product": "蓝牙耳机A", "rating": 5, "aspect": "质量", "text": "音质超出预期，续航也够用"},
    {"product": "充电宝B", "rating": 1, "aspect": "质量", "text": "充到80%就断电，用了一个月鼓包"},
    {"product": "充电宝B", "rating": 2, "aspect": "描述不符", "text": "标称20000mAh，实测只能充手机1.5次"},
    {"product": "充电宝B", "rating": 2, "aspect": "物流", "text": "发货慢，等了9天"},
    {"product": "充电宝B", "rating": 5, "aspect": "质量", "text": "轻便，出差带着方便"},
    {"product": "手机壳C", "rating": 1, "aspect": "质量", "text": "用了两周边角开裂"},
    {"product": "手机壳C", "rating": 2, "aspect": "描述不符", "text": "实物颜色比图片深很多"},
    {"product": "手机壳C", "rating": 3, "aspect": "客服", "text": "咨询尺码，客服答非所问"},
    {"product": "手机壳C", "rating": 5, "aspect": "物流", "text": "次日达，手感不错"},
]

SALES = {
    "数码配件": [
        {"product": "手机壳C", "sales": 24560, "rank": 1},
        {"product": "蓝牙耳机A", "sales": 15230, "rank": 2},
        {"product": "充电宝B", "sales": 9870, "rank": 3},
    ]
}


# ---------- 2) 工具实现（真实函数，可接数据库/API） ----------
def query_product_reviews(product: str, aspect: str = "全部") -> str:
    """按商品 + 维度统计差评（rating<=3 视为差评），返回 JSON 字符串。"""
    matched = [r for r in REVIEWS if r["product"] == product]
    if not matched:
        return json.dumps(
            {"error": f"未找到商品：{product}，可选：蓝牙耳机A / 充电宝B / 手机壳C"},
            ensure_ascii=False,
        )

    bad = [r for r in matched if r["rating"] <= 3]
    if aspect and aspect != "全部":
        bad = [r for r in bad if r["aspect"] == aspect]

    # 差评维度分布，帮模型快速定位"痛点集中在哪"
    distribution = {}
    for r in bad:
        distribution[r["aspect"]] = distribution.get(r["aspect"], 0) + 1

    return json.dumps(
        {
            "product": product,
            "aspect": aspect,
            "total_reviews": len(matched),
            "bad_reviews": len(bad),
            "bad_rate": f"{len(bad) / len(matched):.0%}",
            "aspect_distribution": distribution,
            "samples": [f"[{r['rating']}星/{r['aspect']}] {r['text']}" for r in bad[:5]],
        },
        ensure_ascii=False,
    )


def query_sales_rank(category: str) -> str:
    """查询某品类销量排行，返回 JSON 字符串。"""
    items = SALES.get(category)
    if not items:
        return json.dumps(
            {"error": f"未找到品类：{category}，可选：{' / '.join(SALES)}"},
            ensure_ascii=False,
        )
    return json.dumps({"category": category, "ranking": items}, ensure_ascii=False)


# ---------- 3) 工具 Schema：发给模型的"合同" ----------
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
                    "product": {
                        "type": "string",
                        "description": "商品名，如 蓝牙耳机A、充电宝B、手机壳C",
                    },
                    "aspect": {
                        "type": "string",
                        "description": "分析维度：全部/物流/质量/音质/客服/描述不符，默认全部",
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
            "description": "查询某品类的销量排行榜（商品、销量、名次）。当需要销量、排名、热销情况时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "品类名，如 数码配件"},
                },
                "required": ["category"],
            },
        },
    },
]

# 模型返回的是函数名字符串，本地靠这张表分发（这就是"工具路由"最小版）
TOOL_MAP = {
    "query_product_reviews": query_product_reviews,
    "query_sales_rank": query_sales_rank,
}
