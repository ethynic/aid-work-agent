"""
MCP Server 配置模块

从 configs/config.yaml 的 mcp 节加载 MCP 服务配置，
支持环境变量覆盖（MCP_API_KEYS）。
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict

from loguru import logger

from src.config.settings import settings


@dataclass
class MCPConfig:
    """MCP Server 配置"""

    enabled: bool = False
    transport: str = "sse"          # stdio | sse | streamable-http
    host: str = "0.0.0.0"
    port: int = 8765
    auth_enabled: bool = True
    api_keys: List[str] = field(default_factory=list)
    tools_config: List[Dict] = field(default_factory=list)


def load_mcp_config() -> MCPConfig:
    """
    加载 MCP 配置。

    读取 settings.mcp（_AttrDict），缺失字段回退默认值。
    api_keys 优先使用配置文件值，为空时回退到环境变量 MCP_API_KEYS（逗号分隔）。
    """
    mcp = getattr(settings, "mcp", None)

    enabled: bool = False
    transport: str = "sse"
    host: str = "0.0.0.0"
    port: int = 8765
    auth_enabled: bool = True
    api_keys: List[str] = []
    tools_config: List[Dict] = []

    if mcp is not None:
        enabled = bool(getattr(mcp, "enabled", enabled))
        transport = str(getattr(mcp, "transport", transport))
        host = str(getattr(mcp, "host", host))
        port = int(getattr(mcp, "port", port))

        auth = getattr(mcp, "auth", None)
        if auth is not None:
            auth_enabled = getattr(auth, "enabled", auth_enabled)
            api_keys = getattr(auth, "api_keys", api_keys)
            if isinstance(api_keys, str):
                api_keys = [k.strip() for k in api_keys.split(",") if k.strip()]

        tools_config = getattr(mcp, "tools", tools_config)
        # 确保是 list[dict]
        if not isinstance(tools_config, list):
            tools_config = []

    # api_keys 为空时回退到环境变量
    if not api_keys:
        env_keys = os.getenv("MCP_API_KEYS", "").strip()
        if env_keys:
            api_keys = [k.strip() for k in env_keys.split(",") if k.strip()]

    # 校验 transport 合法值
    valid_transports = ("stdio", "sse", "streamable-http")
    if transport not in valid_transports:
        logger.warning(
            f"MCP 配置: 不支持的 transport='{transport}'，回退为 'sse'"
        )
        transport = "sse"

    config = MCPConfig(
        enabled=enabled,
        transport=transport,
        host=host,
        port=port,
        auth_enabled=auth_enabled,
        api_keys=api_keys,
        tools_config=tools_config,
    )

    logger.debug(f"MCP 配置加载完成: enabled={config.enabled}, transport={config.transport}")
    return config


mcp_config = load_mcp_config()
