"""
MCP Client 示例 — 通过 stdio 连接 AID Work Agent MCP Server

使用前：
1. 安装依赖: pip install mcp

用法:
    python client_stdio_example.py
"""

import asyncio
from mcp.client import Client
from mcp.client.stdio import stdio_client, StdioServerParameters

# MCP Server 启动参数
SERVER_PARAMS = StdioServerParameters(
    command="python",
    args=["-m", "src.mcp.server"],
    cwd="/path/to/aid-work-agent",  # 修改为实际路径
    env={
        "MCP_ENABLED": "true",
        "MCP_TRANSPORT": "stdio",
    },
)


async def main():
    async with stdio_client(SERVER_PARAMS) as (read, write):
        async with Client(read, write) as client:
            # 列出可用工具
            tools = await client.list_tools()
            print(f"可用工具 ({len(tools)} 个):")
            for tool in tools:
                print(f"  - {tool.name}: {tool.description}")

            # 调用工具示例
            if tools:
                result = await client.call_tool(
                    tools[0].name,
                    {"params": '{"query": "测试"}'}
                )
                print(f"\n调用结果: {result}")


if __name__ == "__main__":
    asyncio.run(main())
