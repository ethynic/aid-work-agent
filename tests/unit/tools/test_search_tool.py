"""
网络搜索工具单元测试
"""

import pytest

pytestmark = [pytest.mark.tools, pytest.mark.search]
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from src.tools.search.search_tool import WebSearchTool
from src.config.settings import SearchToolConfig


class TestWebSearchToolDefinition:
    """工具定义测试"""

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
    async def test_search_returns_results(self):
        """搜索返回结果（mock Tavily SDK）"""
        # TavilyClient 在方法内部动态 import，用 sys.modules mock
        mock_client = MagicMock()
        mock_client.search.return_value = {
            "results": [
                {"title": "Test Result", "url": "https://example.com", "content": "Test content", "score": 0.9}
            ],
            "answer": "Test answer",
        }

        mock_tavily_cls = MagicMock(return_value=mock_client)
        fake_settings = SimpleNamespace(tools=SimpleNamespace(search=SearchToolConfig(
            provider="tavily",
            tavily_api_key="test-search-key", max_results=5,
            include_answer=True, search_depth="basic",
        )))
        tool = WebSearchTool()
        with patch("src.tools.search.search_tool.settings", fake_settings), \
             patch.dict("sys.modules", {"tavily": SimpleNamespace(TavilyClient=mock_tavily_cls)}):
            result = await tool.execute(keyword="test query", max_results=3)

        assert result["success"] is True
        assert result["results"] == [{
            "title": "Test Result", "url": "https://example.com",
            "content": "Test content", "score": 0.9,
        }]
        assert result["count"] == 1
        assert result["answer"] == "Test answer"
        mock_tavily_cls.assert_called_once_with(api_key="test-search-key")
        mock_client.search.assert_called_once_with(
            query="test query", max_results=3, include_answer=True,
            search_depth="basic", topic="general",
            timeout=30.0,
        )

    @pytest.mark.asyncio
    async def test_tavily_timeout_retry_keeps_filters(self):
        from tavily.errors import TimeoutError as TavilyTimeoutError

        client = MagicMock()
        client.search.side_effect = [TavilyTimeoutError(30), {"results": []}]
        fake_settings = SimpleNamespace(tools=SimpleNamespace(search=SearchToolConfig(
            provider="tavily", tavily_api_key="test-search-key",
        )))
        with patch("src.tools.search.search_tool.settings", fake_settings), patch.dict(
            "sys.modules", {"tavily": SimpleNamespace(TavilyClient=MagicMock(return_value=client))},
        ):
            result = await WebSearchTool().execute(
                keyword="公告", search_depth="advanced", include_domains=["example.com"],
                exclude_domains=["spam.example.com"], time_range="week", limit=3,
            )
        assert result["success"] is True
        first, second = [call.kwargs for call in client.search.call_args_list]
        assert second == {**first, "search_depth": "basic"}
        assert second["include_domains"] == ["example.com"]
        assert second["exclude_domains"] == ["spam.example.com"]
        assert second["time_range"] == "week"
        assert second["max_results"] == 3

    @pytest.mark.asyncio
    async def test_tavily_auth_error_does_not_retry_or_echo_secret(self):
        client = MagicMock()
        client.search.side_effect = RuntimeError("unit-sensitive-key")
        fake_settings = SimpleNamespace(tools=SimpleNamespace(search=SearchToolConfig(
            provider="tavily", tavily_api_key="unit-sensitive-key",
        )))
        with patch("src.tools.search.search_tool.settings", fake_settings), patch.dict(
            "sys.modules", {"tavily": SimpleNamespace(TavilyClient=MagicMock(return_value=client))},
        ):
            result = await WebSearchTool().execute(keyword="公告", search_depth="advanced")
        assert not result["success"]
        assert "unit-sensitive-key" not in str(result)
        assert client.search.call_count == 1
