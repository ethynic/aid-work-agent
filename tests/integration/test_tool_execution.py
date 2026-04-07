"""
集成测试：ToolExecutor + 真实 Tool 实例
"""

import pytest

pytestmark = pytest.mark.tools
from unittest.mock import AsyncMock, patch

from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor


class TestToolExecutionIntegration:
    """ToolExecutor + 真实工具集成测试"""

    @pytest.mark.asyncio
    async def test_execute_real_search_tool_with_mock_api(self):
        """搜索工具执行（mock _tavily_search 方法）"""
        from src.tools.search.search_tool import WebSearchTool

        tool = WebSearchTool()
        mock_result = {
            "success": True,
            "results": [{"title": "Test", "url": "https://example.com", "content": "Content"}],
            "count": 1,
        }

        with patch.object(WebSearchTool, "_tavily_search", new_callable=AsyncMock, return_value=mock_result):
            registry = ToolRegistry()
            registry.register(tool)
            executor = ToolExecutor(registry=registry)
            result = await executor.execute("web_search", {"keyword": "test"})
            assert result.get("success") is True or "results" in result

    @pytest.mark.asyncio
    async def test_execute_tool_not_in_registry(self):
        """执行不存在的工具"""
        registry = ToolRegistry()
        executor = ToolExecutor(registry=registry)
        result = await executor.execute("nonexistent_tool", {})
        assert result.get("success") is False


class TestToolRegistryIntegration:
    """ToolRegistry + 真实工具加载"""

    def test_register_search_tool(self):
        from src.tools.search.search_tool import WebSearchTool

        registry = ToolRegistry()
        tool = WebSearchTool()
        registry.register(tool)
        assert registry.has_tool("web_search")
        assert registry.get_tool("web_search") is tool

    def test_get_tool_definitions_with_real_tool(self):
        from src.tools.search.search_tool import WebSearchTool

        registry = ToolRegistry()
        registry.register(WebSearchTool())
        defs = registry.get_tool_definitions()
        assert len(defs) >= 1
        web_search_def = next(d for d in defs if d["name"] == "web_search")
        assert "input_schema" in web_search_def
        assert "keyword" in web_search_def["input_schema"]["properties"]
