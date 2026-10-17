"""
Week3 A · 把商品/订单查询封装成 MCP Server（stdio 传输）
================================================================================
MCP（Model Context Protocol）= Agent 宿主里的 MCP Client 与独立工具进程（MCP Server）
之间用 JSON-RPC 2.0 通信的标准协议。价值：工具实现与 Agent 解耦——
同一份 Server 可以被 Claude Desktop / 任何支持 MCP 的 Agent 直接挂载（"USB-C 接口"）。

通信过程（本文件是 Server 侧）：
  1. Agent 启动本进程（子进程，stdio 传输）；
  2. Client ↔ Server 先 initialize 握手（交换协议版本与能力）；
  3. Client 发 tools/list 拿到工具清单（每个工具 = name + description + JSON Schema）；
  4. Client 发 tools/call{name, arguments}，Server 执行后回 JSON-RPC result。

跑法：本文件不直接交互，由 week3/mcp_client_demo.py 或 Agents SDK 拉起；
     单独验证：python week3/mcp_server.py（会挂起等待 stdio 输入，Ctrl+C 退出属正常）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from week2.tools import query_product_reviews, query_sales_rank, search_reviews  # noqa: E402

# FastMCP：官方 SDK 的高层封装。@mcp.tool() 自动把「函数签名 + docstring」转成 JSON Schema 注册，
# 省掉了手写 schema 的胶水——但你要知道它底下干了什么（第 10 章 MCP 架构）。
mcp = FastMCP("ecommerce-tools")


@mcp.tool()
def product_reviews(product: str, aspect: str = "全部") -> str:
    """查询某商品评论统计：差评数、差评率、差评维度分布、典型评论原文。分析差评/口碑/痛点时用。"""
    return query_product_reviews(product, aspect)


@mcp.tool()
def sales_rank(category: str) -> str:
    """查询某品类销量排行榜（商品、销量、名次）。"""
    return query_sales_rank(category)


@mcp.tool()
def review_search(keyword: str, limit: int = 5) -> str:
    """按关键词搜索评论原文（模糊匹配）。找"提到XX的评论"时用。"""
    return search_reviews(keyword, limit)


if __name__ == "__main__":
    mcp.run()  # 默认 stdio 传输：stdin 收 JSON-RPC 请求，stdout 回响应
