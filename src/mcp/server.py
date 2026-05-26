#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Server 主类 — 基于 FastMCP SDK

组合 Protocol + Transport + ToolRegistry + Executor，
提供 stdio 和 SSE/streamable-http 两种传输模式。

启动方式:
    # stdio 模式（本地 CLI / 编辑器集成）
    python -m src.mcp.server

    # SSE/HTTP 模式（远程连接）
    MCP_TRANSPORT=sse python -m src.mcp.server
"""

import asyncio
import sys
from typing import Any

from loguru import logger
from mcp.server.fastmcp import FastMCP

from src.mcp.config import mcp_config, MCPConfig
from src.mcp.tools import MCPToolRegistry
from src.mcp.executor import MCPExecutor, authenticate
from src.core.skill_executor import SkillExecutor
from src.core.skill_registry import skill_registry


def create_mcp_server(config: MCPConfig | None = None) -> FastMCP:
    """
    创建并配置 MCP Server 实例。

    Args:
        config: MCP 配置，默认使用全局 mcp_config

    Returns:
        配置好的 FastMCP 实例
    """
    if config is None:
        config = mcp_config

    server = FastMCP("AID Work Agent MCP Server")

    # 创建组件（延迟初始化，避免模块加载时触发 DB 连接）
    tool_registry = MCPToolRegistry(config.tools_config)
    skill_executor = SkillExecutor(skill_registry)
    executor = MCPExecutor(skill_executor, tool_registry)

    # 将组件挂到 server 实例上，供 run 时使用
    server._mcp_tool_registry = tool_registry  # type: ignore[attr-defined]
    server._mcp_executor = executor  # type: ignore[attr-defined]
    server._mcp_config = config  # type: ignore[attr-defined]

    return server


async def _discover_and_register(server: FastMCP) -> None:
    """发现 skill 工具并注册到 FastMCP"""
    tool_registry: MCPToolRegistry = server._mcp_tool_registry  # type: ignore[attr-defined]
    executor: MCPExecutor = server._mcp_executor  # type: ignore[attr-defined]
    config: MCPConfig = server._mcp_config  # type: ignore[attr-defined]

    await tool_registry.discover_tools()

    for tool_def in tool_registry.get_tools():

        def _make_handler(td=tool_def):
            async def handler(**kwargs: Any) -> str:
                result = await executor.execute_tool(
                    tool_name=td.name,
                    arguments=kwargs,
                )
                contents = result.get("content", [])
                is_error = result.get("isError", False)
                parts = [c.get("text", "") for c in contents]
                text = "\n".join(parts)
                if is_error:
                    return f"[ERROR] {text}"
                return text

            return handler

        server.tool(
            name=tool_def.name,
            description=tool_def.description,
        )(_make_handler())

    logger.info(f"MCP Server 注册了 {len(tool_registry)} 个工具")


def run_server(config: MCPConfig | None = None) -> None:
    """
    启动 MCP Server。

    根据配置选择 stdio 或 SSE/streamable-http 传输模式。
    """
    if config is None:
        config = mcp_config

    if not config.enabled:
        logger.info("MCP Server 未启用（mcp.enabled=false）")
        return

    logger.info(
        f"MCP Server 启动: transport={config.transport}, "
        f"host={config.host}, port={config.port}"
    )

    server = create_mcp_server(config)

    # 同步发现并注册工具
    asyncio.get_event_loop().run_until_complete(_discover_and_register(server))

    transport = config.transport
    if transport == "stdio":
        server.run(transport="stdio")
    elif transport == "streamable-http":
        server.run(
            transport="streamable-http",
            host=config.host,
            port=config.port,
        )
    else:
        # 默认使用 SSE
        server.run(
            transport="sse",
            host=config.host,
            port=config.port,
        )


if __name__ == "__main__":
    # python -m src.mcp.server
    run_server()
