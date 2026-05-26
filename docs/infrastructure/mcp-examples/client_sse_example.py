"""
MCP Client 示例 — 通过 SSE 连接 AID Work Agent MCP Server

使用前：
1. 启动 MCP Server: MCP_ENABLED=true MCP_TRANSPORT=sse python -m src.mcp.server
2. 安装依赖: pip install mcp

用法:
    python client_sse_example.py
"""

import asyncio
from mcp.client import Client
from mcp.client.sse import sse_client

MCP_SERVER_URL = "http://localhost:8765/sse"


async def main():
    async with sse_client(MCP_SERVER_URL) as (read, write):
        async with Client(read, write) as client:
            # 1. 初始化（自动完成）
            print("已连接到 MCP Server")

            # 2. 列出可用工具
            tools = await client.list_tools()
            print(f"\n可用工具 ({len(tools)} 个):")
            for tool in tools:
                print(f"  - {tool.name}: {tool.description}")

            if not tools:
                print("没有可用工具，请检查 config.yaml 中 mcp.tools 配置")
                return

            # 3. 调用第一个工具作为示例
            first_tool = tools[0]
            print(f"\n调用工具: {first_tool.name}")

            result = await client.call_tool(
                first_tool.name,
                {"params": '{"query": "测试"}'}
            )
            print(f"结果: {result}")


if __name__ == "__main__":
    asyncio.run(main())
