from __future__ import annotations
import asyncio
from typing import List, Dict, Any
from loguru import logger
from src.core.agent_engine.contracts import AgentMode

class ToolCatalog:
    def __init__(self, mode, registry, controls, visibility, skill_registry=None, supports_sleep=False,
                 configured_tools=()):
        self.mode, self.tool_registry, self._tool_controls = mode, registry, controls
        self._get_available_subagents = visibility.available
        self.skill_registry = skill_registry
        self.supports_sleep = supports_sleep
        # None is unknown availability, not a new authorization decision.
        self._device_tools = None
        self._capability_lookup_failed = False
        from src.local_tools.catalog import expand_agent_aliases
        self._configured_tools = set(expand_agent_aliases(configured_tools))

    async def prime_local_tools(self, identity, provider_devices=None):
        """Refresh once per activation, using only the actor's reported capabilities."""
        from src.local_tools import catalog, repository
        self._capability_lookup_failed = False
        if not identity.tenant_id or not identity.user_id:
            self._device_tools = set()
            return
        try:
            devices = await asyncio.to_thread(repository.list_devices, identity.tenant_id, identity.user_id)
        except Exception:
            # Visibility is advisory. Preserve executable definitions and let the
            # existing dispatch path report/recover transient device failures.
            logger.opt(exception=True).warning('本地工具能力查询暂时失败，沿原调用链报告设备状态')
            self._device_tools = None
            self._capability_lookup_failed = True
            return
        selected = next((d for d in devices if d.get('selected') and d.get('status') == 'active'), None)
        overrides = provider_devices or {}
        self._device_tools = set()
        for key in catalog.TRUSTED_PROVIDERS:
            device = selected
            if key in overrides:
                device = next((d for d in devices if str(d['id']) == overrides[key]
                               and d.get('status') == 'active'), None)
            if device and key in catalog.get_provider_keys_for_device(device.get('capabilities_json')):
                self._device_tools.update(catalog.agent_tools(key))

    def _visible_definitions(self):
        from src.local_tools.catalog import project_agent_definitions
        # Discovery adds capabilities; it must not hide an existing explicit
        # profile tool (including legacy tools with a cloud-only branch).
        available = None if self._device_tools is None else self._device_tools | self._configured_tools
        return project_agent_definitions(self.tool_registry.get_tool_definitions(), available)

    def _get_tools(self) -> List[Dict[str, Any]]:
        """
        Get tool definitions from ToolRegistry + virtual tools + dynamic tools

        Schema 来源：每个工具类通过 Pydantic InputModel 或 parameters_schema 定义。
        MASTER：包含委派工具
        SUBAGENT / STANDALONE：不包含委派工具
        """
        # 1. 从 ToolRegistry 获取所有已注册工具的 schema
        tools = self._visible_definitions()

        available_subagents = None
        if self.mode == AgentMode.MASTER:
            available_subagents = self._get_available_subagents()
        tools.extend(self._tool_controls.definitions(
            available_subagents=available_subagents
        ))
        if not self.supports_sleep:
            tools = [tool for tool in tools if tool.get('name')!='agent_sleep']
        
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
        guides = '\n\n'.join(
            f"### {item['name']}\n{guide}"
            for item in self._visible_definitions()
            if (guide := self.tool_registry.get_tool(item['name']).get_usage_guide()))
        if self._capability_lookup_failed:
            guides += '\n\n本次Runtime设备能力查询暂时失败，可用性未知；本地工具调用沿原链路返回实际状态，可稍后重试。'

        control_guides = self._tool_controls.usage_guides()
        if control_guides:
            guides = f"{guides}\n\n{control_guides}" if guides else control_guides
        if self.supports_sleep:
            guides += '\n\n### agent_sleep\n'+self._tool_controls.get('agent_sleep').get_usage_guide()

        return guides
