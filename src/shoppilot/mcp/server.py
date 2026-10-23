"""
ShopPilot MCP Server：按三原语暴露能力（吸收 week3 + kamacoder《MCP协议》）
================================================================================
MCP = AI 应用（Host）与工具进程（Server）之间的标准接入协议，数据层 JSON-RPC、
传输层 stdio（本地）/ Streamable HTTP（远程）。与 Function Calling 的分层关系（面试必背）：
  Function Calling 是**模型侧**调用机制（模型决定调哪个函数、传什么参数）；
  MCP 是**应用侧**接入协议（工具从哪来、怎么发现 tools/list、怎么通信、怎么暴露给不同宿主）。
  实际系统：Host 经 MCP 发现工具 → 转成模型可用的 function calling 格式 → 模型决定调用。

三原语（记忆口诀：Tools 能做什么 / Resources 能看什么 / Prompts 怎么做）：
  - Tools：7 个导购查询/推荐工具（模型主动调用，全部只读无副作用）；
  - Resources：商品池、售后知识库文档（应用被动读取，避免把业务数据全塞进 Prompt）；
  - Prompts：预置"导购推荐"提示模板（规范化工具使用顺序，怎么做更稳）。

安全边界（对齐协议要求）：
  - 权限决策在 Host：本 Server 不接触用户对话，只应答 tools/call 与 resources/read；
  - 工具最小暴露：仅只读查询，无删除/下单类高危动作；文件资源限定在知识库目录内（防目录穿越）；
  - 参数校验：ToolRegistry 层 Pydantic 契约校验，非法参数返回结构化错误；
  - 审计：调用留待 Host 侧记录（本仓库由 Tracer JSONL 承担同等职责）。

注意：mcp 包保持 1.x（2.x 改了 FastMCP API），requirements.txt 已锁 <2。
跑法：python -m shoppilot.mcp.server（stdio 传输，由 MCP Client 拉起；单独运行会挂起等输入）
挂载示例（Claude Desktop / 任意 MCP Host 的配置）：
  {"mcpServers": {"shoppilot": {"command": "D:/conda_envs/agent/python.exe",
                                "args": ["-X", "utf8", "-m", "shoppilot.mcp.server"],
                                "cwd": "G:/agent_internship"}}}
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from shoppilot.core.settings import ECOM_DIR  # noqa: E402
from shoppilot.domains.ecommerce import tools as ecommerce_tools  # noqa: E402

mcp = FastMCP("shoppilot-tools")


# ================= Tools：Agent 能做什么（7 个只读导购工具，共享 ToolRegistry） =================
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


@mcp.tool()
def recommend_products(category: str = "", budget_max: float | None = None,
                       budget_min: float | None = None, keyword: str = "",
                       concerns: list[str] | None = None, limit: int = 5) -> str:
    """综合评分 + 卖点硬召回的 Top 推荐（预算内、带物流时效与差评提醒）。推荐类问题首选。"""
    return ecommerce_tools.recommend_products(
        category, budget_max, budget_min, keyword, concerns, limit)


# ================= Resources：Agent 能看什么（被动提供上下文，不占对话 Prompt） =================
@mcp.resource("shoppilot://products")
def product_pool() -> str:
    """商品池全量清单（名称/品类/价格/评分/保修），宿主可直接读取，不必逐个调 get_product_info。"""
    df = ecommerce_tools.domain.load_products()
    return json.dumps(df.to_dict(orient="records"), ensure_ascii=False, indent=1)


@mcp.resource("shoppilot://knowledge/{doc_name}")
def knowledge_doc(doc_name: str) -> str:
    """售后知识库文档原文（return_policy/buying_guide/product_manual_bluetooth/logistics_faq/seller_guide）。"""
    safe = Path(doc_name).name  # 只取文件名部分，防目录穿越
    if not safe.endswith(".md"):
        safe += ".md"
    path = ECOM_DIR / "knowledge" / safe
    if not path.resolve().parent.samefile(ECOM_DIR / "knowledge"):
        return json.dumps({"error": "非法路径"}, ensure_ascii=False)
    if not path.exists():
        avail = [p.stem for p in (ECOM_DIR / "knowledge").glob("*.md")]
        return json.dumps({"error": f"文档不存在：{safe}", "available": avail}, ensure_ascii=False)
    return path.read_text(encoding="utf-8")


# ================= Prompts：Agent 怎么做（预置提示模板，规范化工具使用顺序） =================
@mcp.prompt()
def shopping_guide(question: str) -> str:
    """导购推荐模板：先筛商品 → 再查口碑 → 后看售后，全程带依据。"""
    return (f"你是电商导购助手。请按以下顺序处理用户问题：{question}\n"
            "1. search_products 按预算/品类筛选候选商品（禁止推荐商品池外商品）；\n"
            "2. query_product_reviews 查候选商品的差评分布，给出避坑提示；\n"
            "3. 涉及保修/退换货时用 shoppilot://knowledge/ 资源查阅售后政策；\n"
            "4. 输出推荐清单：商品全称 + 价格 + 推荐理由 + 口碑摘要 + 依据来源。")


if __name__ == "__main__":
    # 自检：不经 MCP 协议先验证 Tools/Resources 可用（分层排错），再进入 stdio 服务
    assert len(ecommerce_tools.registry.names()) == 7, ecommerce_tools.registry.names()
    assert "蓝牙耳机A" in product_pool()
    assert "七天无理由" in knowledge_doc("return_policy")
    mcp.run()
