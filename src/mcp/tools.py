#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Tool Registry — MCP 工具注册表

从项目的 Skill 系统自动生成 MCP 工具 Schema。
将配置中声明的 skill 命令映射为 MCP 协议格式的工具定义，
供外部 AI 智能体通过 MCP 协议调用。

配置示例 (configs/config.yaml):
    mcp:
      tools:
        - skill: order-core
          commands: [create-order, get-order, list-orders]
        - skill: trade-customer
          commands: [save-customer, search-customers]
"""

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from src.core.skill_registry import skill_registry


@dataclass
class MCPToolDefinition:
    """MCP 工具定义

    将一个 skill 脚本命令封装为 MCP 协议工具。
    """

    name: str
    """MCP 工具名称，如 'create-order'"""

    description: str
    """工具的人类可读描述"""

    input_schema: dict = field(default_factory=dict)
    """JSON Schema，描述工具接受的参数"""

    skill_name: str = ""
    """该工具所属的 skill 名称"""

    script_command: str = ""
    """CLI 命令模板，用于实际执行该工具"""


# 通用参数 Schema —— skill 脚本统一接收 JSON 字符串参数
_GENERIC_INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "params": {
            "type": "string",
            "description": "JSON格式参数",
        }
    },
    "required": ["params"],
}


class MCPToolRegistry:
    """MCP 工具注册表

    根据 mcp.tools 配置，扫描对应 skill 的 scripts 目录，
    自动生成 MCP 工具定义。

    用法::

        config = [{"skill": "order-core", "commands": ["create-order"]}]
        registry = MCPToolRegistry(config)
        await registry.discover_tools()
        tools = registry.to_mcp_tool_list()
    """

    def __init__(self, tools_config: list[dict]) -> None:
        """
        Args:
            tools_config: mcp.tools 配置列表，
                每项包含 skill (str) 和 commands (list[str])。
        """
        self._config: list[dict] = tools_config
        self._tools: dict[str, MCPToolDefinition] = {}

    # ------------------------------------------------------------------
    # 核心方法
    # ------------------------------------------------------------------

    async def discover_tools(self) -> None:
        """扫描配置中的 skill，为每个 command 生成工具定义。

        对于每个 command，尝试在 skill 的 scripts/ 目录下找到同名 .py
        脚本。如果找不到，仍然注册工具但 script_command 为空。
        """
        self._tools.clear()

        for entry in self._config:
            skill_name: str = entry.get("skill", "")
            commands: list[str] = entry.get("commands", [])

            if not skill_name or not commands:
                logger.warning(f"MCP tools 配置项缺少 skill 或 commands: {entry}")
                continue

            skill = skill_registry.get(skill_name)
            if skill is None:
                logger.warning(f"MCP tools 引用了未加载的 skill: {skill_name}")
                continue

            for cmd_name in commands:
                script_path = self._find_script(skill, cmd_name)

                if script_path:
                    script_command = f"python3 {script_path} '<JSON>'"
                    description = f"{skill.description} — {cmd_name}"
                else:
                    script_command = ""
                    description = f"{skill.description} — {cmd_name}（需要手动配置命令）"
                    logger.warning(
                        f"Skill '{skill_name}' 中未找到命令 '{cmd_name}' 对应的脚本"
                    )

                tool = MCPToolDefinition(
                    name=cmd_name,
                    description=description,
                    input_schema=_GENERIC_INPUT_SCHEMA.copy(),
                    skill_name=skill_name,
                    script_command=script_command,
                )
                self._tools[cmd_name] = tool
                logger.debug(f"MCP tool 注册: {cmd_name} -> {skill_name}")

        logger.info(f"MCP Tool Registry 共注册 {len(self._tools)} 个工具")

    # ------------------------------------------------------------------
    # 查询方法
    # ------------------------------------------------------------------

    def get_tools(self) -> list[MCPToolDefinition]:
        """返回所有已注册的工具定义"""
        return list(self._tools.values())

    def get_tool(self, name: str) -> Optional[MCPToolDefinition]:
        """按名称查找工具

        Args:
            name: 工具名称，如 'create-order'

        Returns:
            工具定义，不存在返回 None
        """
        return self._tools.get(name)

    def to_mcp_tool_list(self) -> list[dict]:
        """返回 MCP 协议格式的 tools 数组

        每项包含 name、description、inputSchema，
        可直接用于 MCP initialize 响应。

        Returns:
            MCP tools 列表
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _find_script(skill, command_name: str) -> Optional[str]:
        """在 skill 的 scripts 目录下查找与 command 匹配的脚本。

        匹配策略：文件名（不含 .py 后缀）与 command_name 相同。
        例如 command_name='create-order' 匹配 'create-order.py'。

        Args:
            skill: Skill 对象
            command_name: 命令名称

        Returns:
            匹配脚本的相对路径字符串，未找到返回 None
        """
        for script_path in skill.scripts:
            stem = script_path.stem  # 不含后缀的文件名
            if stem == command_name:
                # 返回相对于 skill 目录的路径，如 scripts/create-order.py
                try:
                    rel = script_path.relative_to(skill.dir)
                    return str(rel)
                except ValueError:
                    return str(script_path)
        return None

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
