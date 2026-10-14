"""智谱搜索：真实 HTTP 序列化边界使用 MockTransport，无外网调用。"""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from src.config.settings import LLMProviderConfig, SearchToolConfig
from src.services.web_search import zhipu
from src.tools.search import search_tool
from src.tools.search.search_tool import WebSearchTool


pytestmark = [pytest.mark.tools, pytest.mark.search]


def source(url="https://example.com/news", **extra):
    return {"title": "企业动态", "link": url, "content": "公开资料摘要", **extra}


@pytest.fixture
def search_context(monkeypatch):
    cfg = SearchToolConfig(provider="zhipu", max_results=5)
    llm = LLMProviderConfig(api_keys=["unit-zhipu-key"], base_url="https://unused.example/coding")
    fake_settings = SimpleNamespace(
        tools=SimpleNamespace(search=cfg),
        llm=SimpleNamespace(provider="qwen", zhipu=llm),
    )
    monkeypatch.setattr(search_tool, "settings", fake_settings)
    original_client = httpx.AsyncClient
    requests = []
    clients = []

    def install(payload=None, *, status=200, handler=None):
        async def handle(request):
            requests.append(request)
            if handler:
                return await handler(request)
            return httpx.Response(status, json=payload)

        def create_client(**kwargs):
            client = original_client(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(zhipu.httpx, "AsyncClient", create_client)
        return requests, clients

    return cfg, llm, install


@pytest.mark.asyncio
async def test_shared_llm_key_and_official_search_endpoint(search_context):
    cfg, _, install = search_context
    requests, clients = install({"search_result": [source(
        publish_date="2026-10-12", media="企业官网",
    )]})
    result = await WebSearchTool().execute(keyword="企业最新动态", max_results=3, time_range="week")
    assert result["success"] is True
    assert result["count"] == 1
    assert result["results"][0] == {
        "title": "企业动态", "url": "https://example.com/news", "content": "公开资料摘要",
        "score": 0, "provider": "zhipu", "content_kind": "snippet",
        "published_at": "2026-10-12", "source": "企业官网",
    }
    assert "answer" not in result  # 未发起模型生成，不能伪造答案摘要。
    assert str(requests[0].url) == zhipu.SEARCH_ENDPOINT
    assert requests[0].headers["Authorization"] == "Bearer unit-zhipu-key"
    assert json.loads(requests[0].content) == {
        "search_query": "企业最新动态", "search_engine": cfg.zhipu_search_engine,
        "search_intent": False, "content_size": "medium", "count": 3,
        "search_recency_filter": "oneWeek",
    }
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_domain_filters_are_strict_and_results_deduplicated(search_context):
    _, _, install = search_context
    requests, _ = install({"search_result": [
        source("https://example.com.evil.test/news"),
        source("https://blocked.example.com/news"),
        source("https://www.example.com/news"),
        source("https://other.test/news"),
        source("https://www.example.com/news"),
        source("https://outside.test/news"),
    ]})
    result = await WebSearchTool().execute(
        keyword="公开新闻", include_domains=["EXAMPLE.COM.", "other.test"],
        exclude_domains=["blocked.example.com"], limit=1,
    )
    assert result["count"] == 1
    assert result["results"][0]["url"] == "https://www.example.com/news"
    assert result["truncated"] is True
    assert json.loads(requests[0].content)["count"] == 50


@pytest.mark.asyncio
async def test_single_domain_sent_upstream_and_checked_locally(search_context):
    _, _, install = search_context
    requests, _ = install({"search_result": [source(), source("https://wrong.test/page")]})
    result = await WebSearchTool().execute(keyword="企业官网", include_domains=["example.com"])
    assert result["count"] == 1
    assert json.loads(requests[0].content)["search_domain_filter"] == "example.com"


@pytest.mark.parametrize("engine,count,sent_count", [
    ("search_std", 3, 3), ("search_pro", 3, 3),
    ("search_pro_sogou", 3, 10), ("search_pro_sogou", 11, 20),
    ("search_pro_quark", 3, None),
])
@pytest.mark.asyncio
async def test_engine_specific_count_parameters(search_context, engine, count, sent_count):
    cfg, _, install = search_context
    cfg.zhipu_search_engine = engine
    requests, _ = install({"search_result": [source(f"https://example.com/{i}") for i in range(15)]})
    result = await WebSearchTool().execute(keyword="企业资料", max_results=count)
    assert result["count"] == count
    assert json.loads(requests[0].content).get("count") == sent_count


@pytest.mark.asyncio
async def test_quark_domain_filter_is_local(search_context):
    cfg, _, install = search_context
    cfg.zhipu_search_engine = "search_pro_quark"
    requests, _ = install({"search_result": [source(), source("https://wrong.test/page")]})
    result = await WebSearchTool().execute(keyword="企业资料", include_domains=["example.com"])
    assert result["count"] == 1
    assert "search_domain_filter" not in json.loads(requests[0].content)


@pytest.mark.asyncio
async def test_explicit_none_uses_defaults_and_max_results_beats_limit(search_context):
    _, _, install = search_context
    requests, _ = install({"search_result": []})
    result = await WebSearchTool().execute(
        keyword="企业资料", max_results=None, limit=None, include_answer=None, search_depth=None,
    )
    assert result["success"] and result["count"] == 0
    assert json.loads(requests[0].content)["count"] == 5
    await WebSearchTool().execute(keyword="企业资料", max_results=2, limit=4, search_depth="advanced")
    assert json.loads(requests[1].content)["count"] == 2
    assert json.loads(requests[1].content)["content_size"] == "high"


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500])
@pytest.mark.asyncio
async def test_http_failures_never_echo_body_or_retry(search_context, status):
    _, _, install = search_context
    requests, clients = install({"error": {"message": "unit-zhipu-key"}}, status=status)
    result = await WebSearchTool().execute(keyword="企业资料")
    assert result["success"] is False
    assert result["debug"] == f"http_{status}"
    assert "unit-zhipu-key" not in str(result)
    assert len(requests) == 1
    assert clients[0].is_closed


@pytest.mark.parametrize("payload", [
    {"error": {"message": "unit-zhipu-key"}}, {}, [],
    {"search_result": "invalid"}, {"search_result": [None]},
    {"search_result": [source("javascript:alert(1)")]},
    {"search_result": [source("https://user:password@example.com/")]},
])
@pytest.mark.asyncio
async def test_malformed_or_business_error_response_fails(search_context, payload):
    _, _, install = search_context
    install(payload)
    result = await WebSearchTool().execute(keyword="企业资料")
    assert result["success"] is False
    assert "unit-zhipu-key" not in str(result)
    assert "password" not in str(result)


@pytest.mark.asyncio
async def test_network_error_is_redacted(search_context):
    _, _, install = search_context

    async def fail(request):
        raise httpx.ConnectError("unit-zhipu-key", request=request)

    _, clients = install(handler=fail)
    result = await WebSearchTool().execute(keyword="企业资料")
    assert result["debug"] == "network_error"
    assert "unit-zhipu-key" not in str(result)
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_timeout_closes_client(search_context):
    cfg, _, install = search_context
    cfg.timeout_seconds = 0.01

    async def wait_forever(request):
        await asyncio.Event().wait()

    _, clients = install(handler=wait_forever)
    result = await WebSearchTool().execute(keyword="企业资料")
    assert result["debug"] == "timeout"
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_cancel_propagates_and_closes_client(search_context):
    _, _, install = search_context
    started = asyncio.Event()

    async def wait_forever(request):
        started.set()
        await asyncio.Event().wait()

    requests, clients = install(handler=wait_forever)
    task = asyncio.create_task(WebSearchTool().execute(keyword="企业资料"))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(requests) == 1
    assert clients[0].is_closed


@pytest.mark.parametrize("kwargs", [
    {"keyword": " "}, {"keyword": "字" * 71}, {"keyword": "企业", "max_results": 0},
    {"keyword": "企业", "time_range": "invalid"}, {"keyword": "企业", "topic": "news"},
    {"keyword": "企业", "include_domains": ["https://example.com/path"]},
])
@pytest.mark.asyncio
async def test_invalid_inputs_do_not_dispatch(search_context, kwargs):
    _, _, install = search_context
    requests, _ = install({"search_result": []})
    result = await WebSearchTool().execute(**kwargs)
    assert result["success"] is False
    assert not requests


@pytest.mark.asyncio
async def test_missing_llm_key_does_not_use_tavily_key(search_context):
    cfg, llm, install = search_context
    llm.api_keys = []
    cfg.tavily_api_key = "unit-tavily-key"
    requests, _ = install({"search_result": []})
    result = await WebSearchTool().execute(keyword="企业资料")
    assert result["debug"] == "missing_zhipu_key"
    assert not requests
