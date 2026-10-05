from __future__ import annotations
from typing import List, Dict, Any
from src.core.agent_engine.contracts import AgentMode

class ToolCatalog:
    def __init__(self, mode, registry, controls, visibility, skill_registry=None):
        self.mode, self.tool_registry, self._tool_controls = mode, registry, controls
        self._get_available_subagents = visibility.available
        self.skill_registry = skill_registry
    def _get_tools(self) -> List[Dict[str, Any]]:
        """
        Get tool definitions from ToolRegistry + virtual tools + dynamic tools

        Schema 来源：每个工具类通过 Pydantic InputModel 或 parameters_schema 定义。
        MASTER：包含委派工具
        SUBAGENT / STANDALONE：不包含委派工具
        """
        # 1. 从 ToolRegistry 获取所有已注册工具的 schema
        tools = self.tool_registry.get_tool_definitions()

        available_subagents = None
        if self.mode == AgentMode.MASTER:
            available_subagents = self._get_available_subagents()
        tools.extend(self._tool_controls.definitions(
            available_subagents=available_subagents
        ))
        
        return tools

    def _get_tool_display_name(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        """
        将工具名称转换为用户友好的显示名称

        优先从 ToolRegistry / 虚拟工具实例的 get_display_name() 获取。

        Args:
            tool_name: 原始工具名称
            tool_args: 工具参数

        Returns:
            用户友好的显示名称
        """
        # 1. 从 ToolRegistry 查找
        tool = self.tool_registry.get_tool(tool_name)
        if tool:
            return tool.get_display_name(tool_args or {})

        control_name = self._tool_controls.get_display_name(tool_name, tool_args or {})
        if control_name:
            return control_name

        # 4. Fallback
        return tool_name

    def _collect_tool_usage_guides(self) -> str:
        """
        从 ToolRegistry 和虚拟工具中收集 usage_guide，合并为系统提示词文本。
        仅包含 usage_guide 非空的工具。
        """
        # 从 ToolRegistry 收集注册工具的指南
        guides = self.tool_registry.get_usage_guides()

        control_guides = self._tool_controls.usage_guides()
        if control_guides:
            guides = f"{guides}\n\n{control_guides}" if guides else control_guides

        return guides
