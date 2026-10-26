"""百度 provider 的 HTTP 契约、边界和关闭默认；所有请求均为 MockTransport。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.config.settings import SearchToolConfig, create_settings
from src.services.web_search import registry
from src.services.web_search.providers import baidu, tavily, zhipu
from src.tools.search import search_tool
from src.tools.search.search_tool import WebSearchTool

pytestmark = [pytest.mark.tools, pytest.mark.search]


@pytest.fixture
def search_context(monkeypatch):
    config = SearchToolConfig(provider="baidu", baidu_api_key="unit-baidu-key")
    monkeypatch.setattr(search_tool, "settings", SimpleNamespace(tools=SimpleNamespace(search=config)))
    original = httpx.AsyncClient
    requests, clients = [], []

    def install(payload=None, status=200, handler=None):
        async def handle(request):
            requests.append(request)
            return await handler(request) if handler else httpx.Response(status, json=payload)

        def client(**kwargs):
            instance = original(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(instance)
            return instance

        monkeypatch.setattr(baidu.httpx, "AsyncClient", client)
        return requests, clients

    return config, install


def source(url="https://www.example.com/news", **extra):
    return {"title": "公开动态", "url": url, "snippet": "供应商摘要", "type": "web", **extra}


@pytest.mark.asyncio
async def test_http_contract_and_result_mapping(search_context):
    _, install = search_context
    requests, clients = install({"references": [source(date="2026-10-14", website="公司官网", rerank_score=0.8)]})
    result = await WebSearchTool().execute(keyword="企业动态", limit=3, time_range="week", include_domains=["EXAMPLE.com."], exclude_domains=["spam.example.com"])
    assert result["success"] and result["provider"] == "baidu"
    assert result["results"] == [{"title": "公开动态", "url": "https://www.example.com/news", "content": "供应商摘要", "score": 0.8, "provider": "baidu", "content_kind": "snippet", "published_at": "2026-10-14", "source": "公司官网"}]
    assert "answer" not in result
    assert requests[0].headers["Authorization"] == "Bearer unit-baidu-key"
    assert str(requests[0].url) == baidu.SEARCH_ENDPOINT
    assert json.loads(requests[0].content) == {"messages": [{"role": "user", "content": "企业动态"}], "edition": "standard", "search_source": "baidu_search_v2", "resource_type_filter": [{"type": "web", "top_k": 3}], "search_filter": {"match": {"site": ["example.com"]}}, "block_websites": ["spam.example.com"], "search_recency_filter": "week"}
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_exact_day_range_is_rejected_before_http_without_widening(search_context):
    _, install = search_context
    requests, _ = install({"references": []})
    result = await WebSearchTool().execute(keyword="今日动态", time_range="day", include_domains=["example.com"])
    assert not result["success"] and result["debug"] == "unsupported_time_range"
    assert not requests


@pytest.mark.asyncio
async def test_strict_domain_filter_duplicate_content_and_limit(search_context):
    _, install = search_context
    install({"references": [source("https://example.com.evil.test/"), source("https://spam.example.com/"), source(), source(), source("https://example.com/2", snippet=None, content="原文片段"), source("https://example.com/3")]})
    result = await WebSearchTool().execute(keyword="资料", max_results=1, include_domains=["example.com"], exclude_domains=["spam.example.com"])
    assert result["count"] == 1 and result["truncated"]
    assert result["results"][0]["url"] == "https://www.example.com/news"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"include_domains": ["https://example.com"]}, {"include_domains": [f"site{i}.test" for i in range(21)]}, {"topic": "news"}, {"max_results": 51}, {"time_range": "invalid"}])
async def test_invalid_or_unsupported_inputs_have_zero_http(search_context, kwargs):
    _, install = search_context
    requests, _ = install({"references": []})
    assert not (await WebSearchTool().execute(keyword="查询", **kwargs))["success"]
    assert not requests


@pytest.mark.asyncio
async def test_missing_key_does_not_use_other_provider(search_context, monkeypatch):
    config, install = search_context
    config.baidu_api_key = ""
    requests, _ = install({"references": []})
    other = AsyncMock(side_effect=AssertionError("No fallback"))
    monkeypatch.setattr(tavily.TavilyProvider, "search", other)
    result = await WebSearchTool().execute(keyword="查询")
    assert result["debug"] == "missing_baidu_key" and not requests
    other.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
async def test_http_errors_are_safe_and_not_retried(search_context, status):
    _, install = search_context
    requests, clients = install({"message": "unit-baidu-key"}, status=status)
    result = await WebSearchTool().execute(keyword="查询")
    assert result["debug"] == f"http_{status}" and "unit-baidu-key" not in str(result)
    assert len(requests) == 1 and clients[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"code": "Unauthorized", "message": "unit-baidu-key"}, {}, {"references": None}, {"references": [None]}, {"references": [source(url="javascript:alert(1)")]}, {"references": [source(url="https://user:password@example.com")]}, {"references": [source(snippet={"bad": True})]}])
async def test_bad_response_never_becomes_success(search_context, payload):
    _, install = search_context
    install(payload)
    result = await WebSearchTool().execute(keyword="查询")
    assert not result["success"] and "unit-baidu-key" not in str(result)


@pytest.mark.asyncio
async def test_timeout_and_cancel_close_client(search_context):
    config, install = search_context
    async def pending(request):
        await asyncio.sleep(10)
    requests, clients = install(handler=pending)
    config.timeout_seconds = 0.01
    assert (await WebSearchTool().execute(keyword="查询"))["debug"] == "timeout"
    assert clients[0].is_closed
    config.timeout_seconds = 30
    task = asyncio.create_task(WebSearchTool().execute(keyword="查询"))
    while len(requests) < 2:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert clients[1].is_closed


@pytest.mark.asyncio
async def test_explicit_tavily_never_calls_other_providers_even_on_failure(monkeypatch):
    config = SearchToolConfig(provider="tavily", tavily_api_key="unit-tavily", baidu_api_key="unit-baidu")
    monkeypatch.setattr(search_tool, "settings", SimpleNamespace(tools=SimpleNamespace(search=config)))
    baidu_search, zhipu_search = AsyncMock(), AsyncMock()
    monkeypatch.setattr(baidu.BaiduProvider, "search", baidu_search)
    monkeypatch.setattr(zhipu.ZhipuProvider, "search", zhipu_search)
    primary = AsyncMock(return_value={"success": False, "error": "Tavily failed"})
    monkeypatch.setattr(tavily.TavilyProvider, "search", primary)
    result = await WebSearchTool().execute(keyword="查询", provider="baidu")
    assert not result["success"] and primary.await_count == 1
    baidu_search.assert_not_called(); zhipu_search.assert_not_called()
    assert set(registry.PROVIDER_FACTORIES) == {"deepseek", "tavily", "baidu", "zhipu"}


def test_baidu_key_config_does_not_enable_baidu(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("tools:\n  search:\n    provider: tavily\n", encoding="utf-8")
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    monkeypatch.setenv("BAIDU_API_KEY", "unit-baidu-key")
    cfg = create_settings(path)
    assert cfg.tools.search.provider == "tavily" and cfg.tools.search.baidu_api_key == "unit-baidu-key"
    monkeypatch.setenv("SEARCH_PROVIDER", "baidu")
    assert create_settings(path).tools.search.provider == "baidu"
