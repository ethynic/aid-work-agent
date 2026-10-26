"""DeepSeek默认路由、HTTP契约及约束不丢失；外部网络全部替身。"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.config.settings import LLMProviderConfig, SearchToolConfig, create_settings
from src.services.web_search.providers import baidu, deepseek, tavily, zhipu
from src.tools.search import search_tool

pytestmark = [pytest.mark.tools, pytest.mark.search]
NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


@pytest.fixture
def context(monkeypatch):
    config = SearchToolConfig()
    llm = LLMProviderConfig(api_keys=["unit-deepseek-key"], base_url="https://unused.invalid/llm")
    monkeypatch.setattr(search_tool, "settings", SimpleNamespace(
        tools=SimpleNamespace(search=config), llm=SimpleNamespace(deepseek=llm),
    ))
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

        monkeypatch.setattr(deepseek.httpx, "AsyncClient", client)
        return requests, clients

    return config, llm, install


def source(url="https://example.com/news", **extra):
    return {"title": "企业动态", "url": url, "snippet": "短摘要", "summary": "长摘要",
            "content": "网页正文", "date": "2026-10-22T10:00:00Z", "site_name": "公司官网",
            "rerank_score": 2.4, "authority": 3, **extra}


@pytest.mark.asyncio
@pytest.mark.parametrize("depth,extra,kind,content", [
    ("basic", {}, "summary", "长摘要"),
    ("advanced", {}, "full_text", "网页正文"),
    ("advanced", {"content": ""}, "summary", "长摘要"),
    ("basic", {"summary": ""}, "snippet", "短摘要"),
])
async def test_default_provider_maps_real_http_without_llm(context, depth, extra, kind, content):
    _, _, install = context
    requests, clients = install({"results": [source(**extra)]})
    result = await search_tool.WebSearchTool().execute(keyword="企业动态", limit=3, search_depth=depth)
    assert result["success"] and result["provider"] == "deepseek" and result["count"] == 1
    assert result["results"] == [{"title": "企业动态", "url": "https://example.com/news",
        "content": content, "content_kind": kind, "score": 2.4, "authority": 3,
        "provider": "deepseek", "published_at": "2026-10-22T10:00:00Z", "source": "公司官网"}]
    assert "answer" not in result
    assert str(requests[0].url) == deepseek.SEARCH_ENDPOINT
    assert requests[0].headers["Authorization"] == "Bearer unit-deepseek-key"
    assert requests[0].headers["content-type"] == "application/json"
    assert json.loads(requests[0].content) == {"query": "企业动态", "count": 3, "need_content": depth == "advanced"}
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_domain_operators_are_backed_by_strict_filtering_and_dedup(context):
    _, _, install = context
    requests, _ = install({"results": [source("https://example.com.evil.test/"),
        source("https://blocked.example.com/"), source(), source(), source("https://other.test/"),
        source("https://www.example.com/two")]})
    result = await search_tool.WebSearchTool().execute(keyword="资料", max_results=1,
        include_domains=["EXAMPLE.com.", "other.test"], exclude_domains=["blocked.example.com"])
    assert result["count"] == 1 and result["truncated"]
    assert result["results"][0]["url"] == "https://example.com/news"
    body = json.loads(requests[0].content)
    assert body["query"] == "资料 site:example.com OR site:other.test -site:blocked.example.com"
    assert body["count"] == 50


@pytest.mark.asyncio
@pytest.mark.parametrize("time_range,days", [("day", 1), ("week", 7), ("month", 30), ("year", 365)])
async def test_recency_never_claims_unknown_old_or_future_dates_match(context, monkeypatch, time_range, days):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(deepseek, "datetime", FrozenDateTime)
    cutoff = NOW - timedelta(days=days)
    dates = [cutoff.isoformat(), NOW.isoformat(), (cutoff - timedelta(microseconds=1)).isoformat(),
             (NOW + timedelta(seconds=1)).isoformat(), None, "bad", "2026-10-22", "2026-10-22T11:00:00",
             NOW.astimezone(timezone(timedelta(hours=8))).isoformat()]
    _, _, install = context
    requests, _ = install({"results": [source(f"https://example.com/{i}", date=date) for i, date in enumerate(dates)]})
    result = await search_tool.WebSearchTool().execute(keyword="动态", time_range=time_range)
    assert [r["url"].rsplit("/", 1)[1] for r in result["results"]] == ["0", "1", "8"]
    assert result["applied_filters"]["published_after"] == cutoff.isoformat()
    assert result["applied_filters"]["date_verified"] is True
    assert json.loads(requests[0].content)["query"] == f"动态 after:{(cutoff - timedelta(days=1)).date().isoformat()}"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    {"keyword": "字" * 129}, {"keyword": "字" * 120, "include_domains": ["example.com"]},
    {"keyword": "字" * 120, "time_range": "week"}, {"topic": "news"},
    {"include_domains": ["https://example.com"]}, {"time_range": "invalid"}, {"max_results": 51},
])
async def test_invalid_constraints_fail_before_any_http(context, kwargs):
    _, _, install = context
    requests, _ = install({"results": []})
    result = await search_tool.WebSearchTool().execute(**{"keyword": "资料", **kwargs})
    assert not result["success"] and not requests


@pytest.mark.asyncio
async def test_128_unicode_characters_are_not_silently_truncated(context):
    _, _, install = context
    requests, _ = install({"results": []})
    query = "字" * 127 + "😀"
    result = await search_tool.WebSearchTool().execute(keyword=query)
    assert result["success"] and result["count"] == 0
    assert json.loads(requests[0].content)["query"] == query


@pytest.mark.asyncio
@pytest.mark.parametrize("score", [10 ** 1000, -1, 4, True, None, "2.4"])
async def test_invalid_ranking_score_does_not_discard_valid_source_or_crash(context, score):
    _, _, install = context
    install({"results": [source(rerank_score=score)]})
    result = await search_tool.WebSearchTool().execute(keyword="资料")
    assert result["success"] and result["results"][0]["score"] == 0


@pytest.mark.asyncio
async def test_default_missing_key_does_not_fallback_or_obey_agent_provider(context, monkeypatch):
    _, llm, install = context
    llm.api_keys = []
    requests, _ = install({"results": []})
    others = []
    for cls in [tavily.TavilyProvider, baidu.BaiduProvider, zhipu.ZhipuProvider]:
        mock = AsyncMock(side_effect=AssertionError("No fallback"))
        monkeypatch.setattr(cls, "search", mock)
        others.append(mock)
    result = await search_tool.WebSearchTool().execute(keyword="资料", provider="tavily")
    assert result["debug"] == "missing_deepseek_key" and not requests
    assert all(mock.await_count == 0 for mock in others)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 401, 402, 403, 429, 500, 504])
async def test_http_errors_are_redacted_not_retried_and_close_client(context, status):
    _, _, install = context
    requests, clients = install({"error": "unit-deepseek-key"}, status=status)
    result = await search_tool.WebSearchTool().execute(keyword="资料")
    assert result["debug"] == f"http_{status}" and "unit-deepseek-key" not in str(result)
    assert len(requests) == 1 and clients[0].is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, {}, {"error": "unit-deepseek-key"}, {"results": {}},
    {"results": [None]}, {"results": [source(url="javascript:alert(1)")]},
    {"results": [source(url="https://user:pass@example.com/")]},
    {"results": [source(url="https://example.com:bad/")]}, {"results": [source(summary={"bad": 1})]},
])
async def test_malformed_response_never_becomes_success(context, payload):
    _, _, install = context
    install(payload)
    result = await search_tool.WebSearchTool().execute(keyword="资料")
    assert not result["success"] and "unit-deepseek-key" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["network", "json", "timeout"])
async def test_transport_failures_are_redacted_and_close_client(context, failure):
    async def handler(request):
        if failure == "network":
            raise httpx.ConnectError("unit-deepseek-key", request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout("unit-deepseek-key", request=request)
        return httpx.Response(200, text="unit-deepseek-key")

    _, _, install = context
    requests, clients = install(handler=handler)
    result = await search_tool.WebSearchTool().execute(keyword="资料")
    assert result["debug"] == {"network": "network_error", "json": "invalid_response", "timeout": "timeout"}[failure]
    assert "unit-deepseek-key" not in str(result)
    assert len(requests) == 1 and clients[0].is_closed


@pytest.mark.asyncio
async def test_total_timeout_and_cancellation_release_http_client(context):
    started = asyncio.Event()
    async def pending(request):
        started.set()
        await asyncio.Event().wait()

    config, _, install = context
    requests, clients = install(handler=pending)
    config.timeout_seconds = 0.01
    assert (await search_tool.WebSearchTool().execute(keyword="资料"))["debug"] == "timeout"
    assert clients[0].is_closed
    started.clear()
    config.timeout_seconds = 30
    task = asyncio.create_task(search_tool.WebSearchTool().execute(keyword="资料"))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(requests) == 2 and clients[1].is_closed


def test_deepseek_default_key_reuse_and_explicit_provider_override(monkeypatch, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("tools:\n  search: {}\n", encoding="utf-8")
    monkeypatch.delenv("SEARCH_PROVIDER", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEYS", "unit-first,unit-second")
    cfg = create_settings(path)
    assert cfg.tools.search.provider == "deepseek"
    assert cfg.llm.deepseek.get_effective_keys() == ["unit-first", "unit-second"]
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    assert create_settings(path).tools.search.provider == "tavily"
