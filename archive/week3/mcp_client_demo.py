"""
Week3 A · 最小 MCP Client：亲手走一遍 JSON-RPC 通信，看清协议底层
================================================================================
不借助任何 Agent 框架，直接用官方 MCP SDK 的会话原语：
  stdio_client   → 把 server 作为子进程拉起，拿到读写流
  ClientSession  → 在流上做 JSON-RPC（initialize 握手 → list_tools → call_tool）
"""
import asyncio
import json
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent / "mcp_server.py"


async def main() -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # 用当前 conda 环境的 python 拉起 server 子进程（跨平台写法）
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            # ① 握手：交换协议版本与能力
            await session.initialize()
            print("[握手完成]")

            # ② 拿工具清单（等价于把 @mcp.tool 注册的 schema 列出来）
            tools = await session.list_tools()
            print("[工具清单]")
            for t in tools.tools:
                print(f"  - {t.name}: {t.description[:40]}…")
                print(f"    参数: {json.dumps(t.inputSchema['properties'], ensure_ascii=False)}")

            # ③ 调用工具（模型在本 demo 里不存在——MCP 本身只管通信，不管智能）
            result = await session.call_tool("product_reviews", {"product": "蓝牙耳机A"})
            print("\n[product_reviews 调用结果]")
            print(result.content[0].text)

            result = await session.call_tool("sales_rank", {"category": "数码配件"})
            print("\n[sales_rank 调用结果]")
            print(result.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
