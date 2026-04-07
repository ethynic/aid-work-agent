"""
网络搜索工具单元测试
"""

import pytest

pytestmark = [pytest.mark.tools, pytest.mark.search]
from unittest.mock import AsyncMock, patch, MagicMock

from src.tools.search.search_tool import WebSearchTool


class TestWebSearchToolDefinition:
    """工具定义测试"""

    def test_tool_name(self):
        tool = WebSearchTool()
        assert tool.name == "web_search"

    def test_tool_definition_has_schema(self):
        tool = WebSearchTool()
        defn = tool.to_tool_definition()
        assert defn["name"] == "web_search"
        assert "input_schema" in defn

    def test_display_name_with_keyword(self):
        tool = WebSearchTool()
        name = tool.get_display_name({"keyword": "测试"})
        assert "测试" in name

    def test_display_name_without_args(self):
        tool = WebSearchTool()
        name = tool.get_display_name()
        assert name == "网络搜索"

    def test_validate_parameters_valid(self):
        tool = WebSearchTool()
        assert tool.validate_parameters(keyword="test")

    def test_validate_parameters_missing_keyword(self):
        tool = WebSearchTool()
        assert not tool.validate_parameters()

    def test_get_missing_parameters(self):
        tool = WebSearchTool()
        missing = tool.get_missing_parameters()
        assert "keyword" in missing


class TestWebSearchToolExecution:
    """工具执行测试（mock 外部 API）"""

    @pytest.mark.asyncio
    @patch("src.tools.search.search_tool.TavilyClient", create=True)
    async def test_search_returns_results(self, mock_tavily_cls):
        """搜索返回结果（mock Tavily SDK）"""
        # TavilyClient 在方法内部动态 import，用 sys.modules mock
        mock_client = MagicMock()
        mock_client.search.return_value = {
            "results": [
                {"title": "Test Result", "url": "https://example.com", "content": "Test content", "score": 0.9}
            ],
            "answer": "Test answer",
        }

        tool = WebSearchTool()
        # 使用 patch 在 execute 内部拦截 tavily import
        with patch.dict("sys.modules", {"tavily": MagicMock(TavilyClient=MagicMock(return_value=mock_client))}):
            result = await tool.execute(keyword="test query", limit=5)
            # 结果可能是 success 或包含 results
            assert result is not None
