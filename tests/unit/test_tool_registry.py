"""
ToolRegistry 和 ToolExecutor 单元测试
"""

import pytest

pytestmark = pytest.mark.tools
from unittest.mock import AsyncMock, MagicMock

from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor


class TestToolRegistry:
    """工具注册表测试"""

    def test_register_and_get_tool(self, mock_tool):
        registry = ToolRegistry()
        tool = mock_tool(name="test_tool")
        registry.register(tool)
        assert registry.get_tool("test_tool") == tool

    def test_unregister_tool(self, mock_tool):
        registry = ToolRegistry()
        tool = mock_tool(name="test_tool")
        registry.register(tool)
        registry.unregister("test_tool")
        assert registry.get_tool("test_tool") is None

    def test_has_tool(self, mock_tool):
        registry = ToolRegistry()
        tool = mock_tool(name="test_tool")
        registry.register(tool)
        assert registry.has_tool("test_tool")
        assert not registry.has_tool("nonexistent")

    def test_list_tools(self, mock_tool):
        registry = ToolRegistry()
        registry.register(mock_tool(name="tool_a"))
        registry.register(mock_tool(name="tool_b"))
        names = registry.list_tools()
        assert "tool_a" in names
        assert "tool_b" in names

    def test_get_tool_definitions(self, mock_tool):
        registry = ToolRegistry()
        registry.register(mock_tool(name="tool_a"))
        defs = registry.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["name"] == "tool_a"

    def test_get_nonexistent_tool_returns_none(self):
        registry = ToolRegistry()
        assert registry.get_tool("nonexistent") is None


class TestToolExecutor:
    """工具执行器测试"""

    @pytest.mark.asyncio
    async def test_execute_known_tool(self, mock_tool):
        registry = ToolRegistry()
        registry.register(mock_tool(
            name="web_search",
            execute_return={"success": True, "results": []},
        ))
        executor = ToolExecutor(registry=registry)
        result = await executor.execute("web_search", {"keyword": "test"})
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        executor = ToolExecutor(registry=registry)
        result = await executor.execute("nonexistent", {})
        assert result.get("success") is False

    @pytest.mark.asyncio
    async def test_execute_batch(self, mock_tool):
        registry = ToolRegistry()
        registry.register(mock_tool(name="tool_a", execute_return={"success": True}))
        registry.register(mock_tool(name="tool_b", execute_return={"success": True}))
        executor = ToolExecutor(registry=registry)

        results = await executor.execute_batch([
            {"tool_name": "tool_a", "parameters": {}},
            {"tool_name": "tool_b", "parameters": {}},
        ])
        assert len(results) == 2
