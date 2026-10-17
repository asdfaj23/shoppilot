"""
ShopPilot MCP Server：FastMCP 暴露 6 个导购工具（吸收 week3/mcp_server.py，共享 ToolRegistry）
================================================================================
MCP = Agent 宿主的 MCP Client 与独立工具进程之间的 JSON-RPC 2.0 标准协议（"USB-C 接口"）：
同一份 Server 可被 Claude Desktop / 任何支持 MCP 的 Agent 直接挂载。
与 week3 的差别：工具实现来自 ToolRegistry 共享注册中心（单一事实源）——
ShopPilot 内部 Agent 与外部 MCP 宿主调到的是**同一份**工具实现与数据，不存在两套逻辑。
注意：mcp 包保持 1.x（2.x 改了 FastMCP API），requirements.txt 已锁 <2。
跑法：python -m shoppilot.mcp.server（stdio 传输，由 MCP Client 拉起；单独运行会挂起等输入）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from shoppilot.domains.ecommerce import tools as ecommerce_tools  # noqa: E402

mcp = FastMCP("shoppilot-tools")


@mcp.tool()
def search_products(category: str = "", budget_max: float | None = None,
                    budget_min: float | None = None, keyword: str = "", limit: int = 5) -> str:
    """按品类/预算/关键词筛选商品池并返回候选商品（价格、评分、卖点）。推荐类问题先调它。"""
    return ecommerce_tools.search_products(category, budget_max, budget_min, keyword, limit)


@mcp.tool()
def get_product_info(name: str) -> str:
    """查单个商品详情：价格、评分、月销、保修时长、卖点。"""
    return ecommerce_tools.get_product_info(name)


@mcp.tool()
def query_product_reviews(product: str, aspect: str = "全部") -> str:
    """查商品评论统计：差评数、差评率、差评维度分布、典型评论原文。分析口碑/痛点时用。"""
    return ecommerce_tools.query_product_reviews(product, aspect)


@mcp.tool()
def search_reviews(keyword: str, limit: int = 5) -> str:
    """按关键词全文搜评论原文（模糊匹配）。找"提到XX的评论"时用。"""
    return ecommerce_tools.search_reviews(keyword, limit)


@mcp.tool()
def query_sales_rank(category: str) -> str:
    """查某品类销量排行榜（商品、销量、名次）。"""
    return ecommerce_tools.query_sales_rank(category)


@mcp.tool()
def daily_bad_review_trend(product: str, days: int = 14) -> str:
    """查商品最近N天的差评按日趋势，判断差评是否变多。"""
    return ecommerce_tools.daily_bad_review_trend(product, days)


if __name__ == "__main__":
    # 自检：不经 MCP 协议先验证 6 个函数可用（分层排错），再进入 stdio 服务
    assert len(ecommerce_tools.registry.names()) == 6, ecommerce_tools.registry.names()
    mcp.run()
