"""
集成测试：ToolExecutor + 真实 Tool 实例
"""

import pytest
import json
from types import SimpleNamespace
import httpx

pytestmark = pytest.mark.tools
from unittest.mock import AsyncMock, patch

from src.tools.registry import ToolRegistry
from src.tools.executor import ToolExecutor

from src.config.settings import SearchToolConfig
from src.services.web_search.providers import baidu, tavily
from src.tools.search import search_tool


@pytest.fixture(scope="session", autouse=True)
def init_db_pool():
    """本文件仅隔离搜索工具组件链，不连接 DB，覆盖目录级数据库 fixture。"""
    yield


class TestToolExecutionIntegration:
    """ToolExecutor + 真实工具集成测试"""

    @pytest.mark.asyncio
    async def test_execute_real_search_tool_with_mock_api(self, monkeypatch):
        """搜索工具执行（mock Tavily provider，不绕过注册表路由）"""
        from src.tools.search.search_tool import WebSearchTool
        from src.services.web_search.providers.tavily import TavilyProvider
        from src.tools.search import search_tool
        from src.config.settings import SearchToolConfig

        monkeypatch.setattr(search_tool, "settings", SimpleNamespace(
            tools=SimpleNamespace(search=SearchToolConfig(provider="tavily", tavily_api_key="unit-key")),
        ))

        tool = WebSearchTool()
        mock_result = {
            "success": True,
            "results": [{"title": "Test", "url": "https://example.com", "content": "Content"}],
            "count": 1,
        }

        with patch.object(TavilyProvider, "search", new_callable=AsyncMock, return_value=mock_result):
            registry = ToolRegistry()
            registry.register(tool)
            executor = ToolExecutor(registry=registry)
            result = await executor.execute("web_search", {"keyword": "test"})
            assert result == mock_result

    @pytest.mark.asyncio
    async def test_execute_zhipu_search_with_http_transport(self, monkeypatch):
        """真实参数规范化、注册与派发，仅 mock 最外层 HTTP。"""
        from src.config.settings import SearchToolConfig, LLMProviderConfig
        from src.tools.search import search_tool
        from src.services.web_search import zhipu

        monkeypatch.setattr(search_tool, "settings", SimpleNamespace(
            tools=SimpleNamespace(search=SearchToolConfig(provider="zhipu")),
            llm=SimpleNamespace(zhipu=LLMProviderConfig(api_keys=["unit-zhipu-key"])),
        ))
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"search_result": [
                {"title": "官网", "link": "https://example.com/", "content": "企业公开资料"},
            ]})

        original_client = httpx.AsyncClient
        monkeypatch.setattr(zhipu.httpx, "AsyncClient", lambda **kwargs: original_client(
            transport=httpx.MockTransport(handler), **kwargs,
        ))
        registry = ToolRegistry()
        registry.register(search_tool.WebSearchTool())
        executor = ToolExecutor(registry=registry)
        result = await executor.execute("web_search", {
            "keyword": "企业资料", "max_results": "3", "include_answer": "false", "time_range": "week",
        })
        assert result["success"] and result["provider"] == "zhipu"
        assert result["count"] == 1
        assert len(requests) == 1
        assert json.loads(requests[0].content)["count"] == 3
        assert json.loads(requests[0].content)["search_recency_filter"] == "oneWeek"

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


@pytest.mark.asyncio
async def test_executor_routes_baidu_and_normalizes_string_parameters(monkeypatch):
    cfg = SearchToolConfig(provider="baidu", baidu_api_key="unit-key")
    monkeypatch.setattr(search_tool, "settings", SimpleNamespace(tools=SimpleNamespace(search=cfg)))
    requests, clients = [], []
    original = httpx.AsyncClient
    def handler(req):
        requests.append(req)
        return httpx.Response(200, json={"references": [{"title": "官网", "url": "https://example.com/", "snippet": "公开资料"}]})
    def client(**kwargs):
        obj = original(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(obj)
        return obj
    monkeypatch.setattr(baidu.httpx, "AsyncClient", client)
    tools = ToolRegistry()
    tools.register(search_tool.WebSearchTool())
    result = await ToolExecutor(registry=tools).execute("web_search", {
        "keyword": "公开资料", "max_results": "2", "include_answer": "false", "time_range": "month",
    })
    assert result["success"] and result["provider"] == "baidu"
    assert result["count"] == 1 and "answer" not in result
    assert json.loads(requests[0].content)["resource_type_filter"][0]["top_k"] == 2
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_executor_tavily_registration_and_schema(monkeypatch):
    cfg = SearchToolConfig(tavily_api_key="unit-key")
    monkeypatch.setattr(search_tool, "settings", SimpleNamespace(tools=SimpleNamespace(search=cfg)))
    mocked = AsyncMock(return_value={"success": True, "results": [], "count": 0})
    monkeypatch.setattr(tavily.TavilyProvider, "search", mocked)
    tools = ToolRegistry()
    instance = search_tool.WebSearchTool()
    tools.register(instance)
    assert tools.get_tool("web_search") is instance
    definition = next(item for item in tools.get_tool_definitions() if item["name"] == "web_search")
    assert "keyword" in definition["input_schema"]["properties"]
    result = await ToolExecutor(registry=tools).execute("web_search", {"keyword": "公开资料"})
    assert result["success"] and mocked.await_count == 1
