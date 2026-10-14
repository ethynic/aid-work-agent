"""独立验收：仅 mock 供应商边界，注册、参数规范化与工具执行均真实。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

from src.config.settings import SearchToolConfig
from src.services.web_search import registry
from src.services.web_search.domains import normalize_domains, matches_domain
from src.services.web_search.models import SearchRequest
from src.services.web_search.providers import baidu, tavily
from src.tools.search import search_tool

pytestmark = [pytest.mark.tools, pytest.mark.search]


def test_registry_only_constructs_explicit_provider(monkeypatch):
    calls = []
    selected = object()
    monkeypatch.setattr(registry, "PROVIDER_FACTORIES", {
        "tavily": lambda settings: calls.append("tavily") or selected,
        "baidu": lambda settings: pytest.fail("must not instantiate baidu"),
        "zhipu": lambda settings: pytest.fail("must not instantiate zhipu"),
    })
    assert registry.create_provider("tavily", object()) is selected
    assert registry.create_provider("unknown", object()) is None
    assert calls == ["tavily"]


@pytest.mark.asyncio
async def test_unknown_provider_fails_closed_without_fallback(monkeypatch):
    cfg = SearchToolConfig()
    cfg.provider = "unknown"
    monkeypatch.setattr(search_tool, "settings", SimpleNamespace(tools=SimpleNamespace(search=cfg)))
    result = await search_tool.WebSearchTool().execute(keyword="公开资料")
    assert result["debug"] == "invalid_provider"


def test_legacy_zhipu_entry_is_same_adapter():
    from src.services.web_search import zhipu as legacy
    from src.services.web_search.providers import zhipu as current
    assert legacy.search_zhipu is current.search_zhipu
    assert legacy.SEARCH_ENDPOINT == current.SEARCH_ENDPOINT


def test_domain_unicode_and_dns_boundary():
    included = normalize_domains(["例子.测试", "XN--FSQU00A.XN--0ZWM56D."])
    assert included == ["xn--fsqu00a.xn--0zwm56d"]
    assert matches_domain("sub." + included[0], included)
    assert not matches_domain(included[0] + ".evil.test", included)
    assert not matches_domain("evil" + included[0], included)


@pytest.mark.parametrize("domain", ["a..test", "a.test:80", "a.test/path", "*.test", "-a.test", "a-.test"])
def test_invalid_domains_are_rejected(domain):
    with pytest.raises(ValueError):
        normalize_domains([domain])


def request():
    return SearchRequest("公开资料", 3, True, "advanced", "news", "week", ["example.com"], ["blocked.example.com"])


@pytest.mark.asyncio
async def test_tavily_timeout_downgrade_preserves_all_constraints(monkeypatch):
    import tavily as sdk
    client = MagicMock()
    client.search.side_effect = [TimeoutError("unit-secret"), {"results": [], "answer": "摘要"}]
    monkeypatch.setattr(sdk, "TavilyClient", lambda **kwargs: client)
    result = await tavily.TavilyProvider(api_key="unit-secret", timeout_seconds=2).search(request())
    assert result["success"] and result["answer"] == "摘要"
    first, second = [call.kwargs for call in client.search.call_args_list]
    assert second == {**first, "search_depth": "basic"}
    assert first["topic"] == "news" and first["time_range"] == "week"
    assert first["include_domains"] == ["example.com"]
    assert first["exclude_domains"] == ["blocked.example.com"]


@pytest.mark.asyncio
async def test_tavily_auth_error_never_downgrades_or_leaks(monkeypatch):
    import tavily as sdk
    client = MagicMock()
    client.search.side_effect = RuntimeError("unit-secret")
    monkeypatch.setattr(sdk, "TavilyClient", lambda **kwargs: client)
    result = await tavily.TavilyProvider(api_key="unit-secret", timeout_seconds=2).search(request())
    assert not result["success"] and "unit-secret" not in str(result)
    assert client.search.call_count == 1


@pytest.mark.asyncio
async def test_tavily_cancel_never_starts_retry(monkeypatch):
    import tavily as sdk
    client = MagicMock()
    monkeypatch.setattr(sdk, "TavilyClient", lambda **kwargs: client)
    started = asyncio.Event()
    async def pending(executor, callback):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", pending)
    task = asyncio.create_task(tavily.TavilyProvider(api_key="unit-key", timeout_seconds=2).search(request()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    client.search.assert_not_called()


@pytest.mark.parametrize("failure", ["network", "invalid_json"])
@pytest.mark.asyncio
async def test_baidu_transport_failure_is_redacted_and_closes(monkeypatch, failure):
    original = httpx.AsyncClient
    clients, requests = [], []
    def handler(req):
        requests.append(req)
        if failure == "network":
            raise httpx.ConnectError("unit-secret", request=req)
        return httpx.Response(200, text="unit-secret-not-json")
    def create_client(**kwargs):
        instance = original(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(instance)
        return instance
    monkeypatch.setattr(baidu.httpx, "AsyncClient", create_client)
    result = await baidu.search_baidu(
        api_key="unit-secret", keyword="公开资料", max_results=3, time_range=None,
        include_domains=None, exclude_domains=None, timeout_seconds=2,
    )
    assert not result["success"]
    assert result["debug"] == ("network_error" if failure == "network" else "invalid_response")
    assert "unit-secret" not in str(result)
    assert len(requests) == 1 and clients[0].is_closed
