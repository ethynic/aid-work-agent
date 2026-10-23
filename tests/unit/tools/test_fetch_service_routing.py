"""Independent routing checks: retrieval availability must not upgrade provenance."""

import asyncio
import hashlib
import importlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml
from pydantic import ValidationError

from src.config.settings import ToolsConfig, WebFetchToolConfig, create_settings
from src.services.web_search import fetch, remote_fetch
from src.services.web_search.evidence import source_body
from src.tools.context import ToolExecutionContext
from src.tools.executor import ToolExecutor
from src.tools.file.read_tool import ReadTool
from src.tools.registry import ToolRegistry
from src.tools.search.fetch_tool import WebFetchTool


pytestmark = [pytest.mark.tools, pytest.mark.search]
URL = "https://acme.test/about?ref=public"
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
BODY = "Acme Factory manufactures windows and employs 120 employees."


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch):
    """Never use deployment credentials or an accidental real HTTP transport."""
    config = WebFetchToolConfig()
    monkeypatch.setattr(fetch, "_runtime_config", lambda: config)
    settings = SimpleNamespace(
        tools=SimpleNamespace(search=SimpleNamespace(tavily_api_key="synthetic-tavily-key")),
        llm=SimpleNamespace(deepseek=SimpleNamespace(get_effective_keys=lambda: ["synthetic-deepseek-key"])),
    )
    monkeypatch.setattr(importlib.import_module("src.config.settings"), "settings", settings)
    def forbidden_client(**kwargs):
        raise AssertionError("Unmocked HTTP boundary")
    monkeypatch.setattr(fetch.httpx, "AsyncClient", forbidden_client)
    return config


@pytest.fixture
async def wire(monkeypatch):
    original = httpx._client.AsyncClient
    dns = AsyncMock(return_value=PUBLIC.copy())
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", dns)
    requests, clients, options = [], [], []
    def install(handler):
        async def handle(request):
            requests.append(request)
            return await handler(request)
        def client(**kwargs):
            options.append(kwargs)
            item = original(transport=httpx.MockTransport(handle), **kwargs)
            clients.append(item)
            return item
        monkeypatch.setattr(fetch.httpx, "AsyncClient", client)
        return SimpleNamespace(requests=requests, clients=clients, options=options, dns=dns)
    return install


def direct_result():
    return {"success": True, "count": 1, "results": [{"url": URL, "requested_url": URL,
        "content": BODY, "content_kind": "direct_html", "final_url": URL, "final_url_verified": True}]}


def remote_result():
    return remote_fetch._document_result(URL, "Acme Factory", BODY, "tavily_extract", remote_fetch.TAVILY_PROVENANCE_NOTICE)


def test_defaults_and_checked_in_yaml_choose_native_then_tavily_without_changing_search():
    config = ToolsConfig()
    assert config.fetch.provider == "auto" and config.fetch.remote_provider == "tavily"
    assert config.fetch.total_timeout_seconds == 90
    assert config.search.provider == "deepseek"
    path = Path(__file__).resolve().parents[3] / "configs" / "config.yaml"
    checked_in = yaml.safe_load(path.read_text(encoding="utf-8"))["tools"]
    assert checked_in["fetch"]["provider"] == "auto"
    assert checked_in["fetch"]["remote_provider"] == "tavily"
    assert checked_in["fetch"]["total_timeout_seconds"] == 90
    assert checked_in["search"]["provider"] == "deepseek"


@pytest.mark.parametrize("mode,remote", [("auto", "tavily"), ("native", "deepseek"), ("remote", "tavily"), ("remote", "deepseek")])
def test_environment_changes_only_trusted_route_settings(tmp_path, monkeypatch, mode, remote):
    config_file = tmp_path / "empty.yaml"
    config_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("WEB_FETCH_PROVIDER", mode)
    monkeypatch.setenv("WEB_FETCH_REMOTE_PROVIDER", remote)
    monkeypatch.setenv("WEB_FETCH_TOTAL_TIMEOUT_SECONDS", "80")
    loaded = create_settings(config_file)
    assert loaded.tools.fetch.provider == mode and loaded.tools.fetch.remote_provider == remote
    assert loaded.tools.fetch.total_timeout_seconds == 80
    assert loaded.tools.search.provider == "deepseek"


@pytest.mark.parametrize("field,value", [("provider", "unknown"), ("remote_provider", "unknown"), ("total_timeout_seconds", float("inf"))])
def test_invalid_route_configuration_fails_loud(field, value):
    with pytest.raises(ValidationError):
        WebFetchToolConfig(**{field: value})


async def test_default_native_success_never_calls_paid_remote(monkeypatch):
    native = AsyncMock(return_value=direct_result())
    remote = AsyncMock(side_effect=AssertionError("unnecessary remote request"))
    monkeypatch.setattr(fetch, "_native_document", native)
    monkeypatch.setattr(fetch, "_remote_document", remote)
    result = await fetch.fetch_public_document(URL)
    assert result["success"] and result["results"][0]["content_kind"] == "direct_html"
    assert result["attempts"] == [{"method": "native", "success": True, "debug": None, "stage": None}]
    native.assert_awaited_once_with(URL, 25 * 1024 * 1024)
    remote.assert_not_awaited()


@pytest.mark.parametrize("code", ["fetch_network_failed", "fetch_tls_failed", "fetch_connect_failed", "fetch_response_failed", "fetch_timeout"])
async def test_only_network_failures_get_one_explicit_remote_attempt(monkeypatch, code):
    native = AsyncMock(return_value={"success": False, "debug": code, "stage": "tls"})
    remote = AsyncMock(return_value=remote_result())
    monkeypatch.setattr(fetch, "_native_document", native)
    monkeypatch.setattr(fetch, "_remote_document", remote)
    result = await fetch.fetch_public_document(URL)
    assert result["success"] and result["results"][0]["content_kind"] == "provider_fetch"
    assert [(item["method"], item["success"]) for item in result["attempts"]] == [("native", False), ("tavily", True)]
    assert result["attempts"][0]["debug"] == code
    assert native.await_count == remote.await_count == 1


@pytest.mark.parametrize("code", ["access_challenge", "requires_rendering", "http_status_403", "http_status_429", "http_status_500",
    "unsafe_url", "non_public_address", "dns_failed", "download_too_large", "text_too_large", "pdf_parse_failed",
    "pdf_no_text", "pdf_password_protected", "unsupported_content_type", "document_parse_failed"])
async def test_remote_is_not_an_escape_from_admission_or_document_errors(monkeypatch, code):
    monkeypatch.setattr(fetch, "_native_document", AsyncMock(return_value={"success": False, "debug": code}))
    remote = AsyncMock(side_effect=AssertionError("forbidden fallback"))
    monkeypatch.setattr(fetch, "_remote_document", remote)
    result = await fetch.fetch_public_document(URL)
    assert not result["success"] and result["debug"] == code
    assert result["requested_url"] == URL and len(result["attempts"]) == 1
    remote.assert_not_awaited()


async def test_explicit_native_retains_no_remote_behavior(monkeypatch, isolated_config):
    isolated_config.provider = "native"
    monkeypatch.setattr(fetch, "_native_document", AsyncMock(return_value={"success": False, "debug": "fetch_tls_failed"}))
    remote = AsyncMock()
    monkeypatch.setattr(fetch, "_remote_document", remote)
    assert (await fetch.fetch_public_document(URL))["debug"] == "fetch_tls_failed"
    remote.assert_not_awaited()


@pytest.mark.parametrize("url", ["https://user:secret@acme.test", "http://127.0.0.1", "http://169.254.169.254", "http://[::1]", "file:///tmp/private"])
async def test_remote_mode_never_discloses_unsafe_url_to_provider(monkeypatch, isolated_config, url):
    isolated_config.provider = "remote"
    remote = AsyncMock()
    monkeypatch.setattr(remote_fetch, "fetch_tavily_document", remote)
    result = await fetch.fetch_public_document(url)
    assert not result["success"] and "secret" not in json.dumps(result)
    remote.assert_not_awaited()


async def test_remote_mode_checks_all_dns_answers_before_sending_url(wire, isolated_config):
    isolated_config.provider = "remote"
    async def never(request):
        raise AssertionError("must reject before connecting")
    net = wire(never)
    net.dns.return_value = PUBLIC + [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 443))]
    result = await fetch.fetch_public_document(URL)
    assert result["debug"] == "non_public_address" and net.requests == []
    assert result["attempts"][-1]["method"] == "tavily"
    assert result["attempts"][-1]["success"] is False


async def test_rebinding_between_native_failure_and_remote_admission_sends_no_post(wire):
    async def handler(request):
        assert request.method == "GET" and request.url.host == "93.184.216.34"
        raise httpx.ConnectError("synthetic connect failure", request=request)
    net = wire(handler)
    net.dns.side_effect = [PUBLIC, [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]]
    result = await fetch.fetch_public_document(URL)
    assert result["debug"] == "non_public_address"
    assert [r.method for r in net.requests] == ["GET"] and net.dns.await_count == 2
    assert [a["method"] for a in result["attempts"]] == ["native", "tavily"]
    assert all(client.is_closed for client in net.clients)


@pytest.mark.parametrize("provider,key,endpoint", [
    ("tavily", "synthetic-tavily-key", "https://api.tavily.com/extract"),
    ("deepseek", "synthetic-deepseek-key", "https://api.deepseek.com/v1/tools/fetch"),
])
async def test_real_route_adapter_posts_only_to_fixed_endpoint_with_its_own_key(wire, monkeypatch, isolated_config, provider, key, endpoint):
    isolated_config.remote_provider = provider
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    async def handler(request):
        if request.method == "GET":
            assert "authorization" not in request.headers and "cookie" not in request.headers
            raise httpx.ConnectError("synthetic native failure", request=request)
        assert str(request.url) == endpoint and request.headers["authorization"] == "Bearer " + key
        body = json.loads(request.content)
        assert body == ({"url": URL} if provider == "deepseek" else {
            "urls": [URL], "extract_depth": "advanced", "format": "markdown", "timeout": 30})
        assert "cookie" not in request.headers and request.headers["host"] != "acme.test"
        payload = {"title": "Acme", "content": BODY} if provider == "deepseek" else {
            "results": [{"url": URL, "raw_content": BODY}], "failed_results": []}
        return httpx.Response(200, json=payload)
    net = wire(handler)
    result = await fetch.fetch_public_document(URL)
    source = result["results"][0]
    assert source["content_kind"] == "provider_fetch" and source["final_url"] is None
    assert source["final_url_verified"] is False and source["freshness"] == "unknown"
    assert source["notice"] and source["provider"] == ("deepseek_fetch" if provider == "deepseek" else "tavily_extract")
    assert [r.method for r in net.requests] == ["GET", "POST"]
    assert all(option["trust_env"] is False and option["follow_redirects"] is False for option in net.options)
    assert all(client.is_closed for client in net.clients)
    assert key not in json.dumps(result)


async def test_remote_failure_does_not_switch_provider_or_retry(wire):
    async def handler(request):
        if request.method == "GET":
            raise httpx.ConnectError("synthetic failure", request=request)
        return httpx.Response(503, text="synthetic-tavily-key provider secret")
    net = wire(handler)
    result = await fetch.fetch_public_document(URL)
    assert not result["success"] and result["debug"] == "provider_http_503"
    assert [r.method for r in net.requests] == ["GET", "POST"]
    assert [a["method"] for a in result["attempts"]] == ["native", "tavily"]
    assert "secret" not in json.dumps(result) and all(c.is_closed for c in net.clients)


async def test_missing_selected_key_does_not_implicitly_switch_to_other_remote(wire, isolated_config, monkeypatch):
    isolated_config.provider = "remote"
    settings = importlib.import_module("src.config.settings").settings
    settings.tools.search.tavily_api_key = ""
    async def never(request):
        raise AssertionError("No selected provider key; must not send any request")
    net = wire(never)
    result = await fetch.fetch_public_document(URL)
    assert result["debug"] == "missing_tavily_key" and not net.requests
    assert [a["method"] for a in result["attempts"]] == ["tavily"]


async def test_remote_mode_is_explicit_and_does_not_issue_native_get(wire, isolated_config):
    isolated_config.provider = "remote"
    async def handler(request):
        assert request.method == "POST"
        return httpx.Response(200, json={"results": [{"url": URL, "raw_content": BODY}], "failed_results": []})
    net = wire(handler)
    result = await fetch.fetch_public_document(URL)
    assert result["success"] and len(net.requests) == 1 and net.dns.await_count == 1
    assert [a["method"] for a in result["attempts"]] == ["tavily"]


@pytest.mark.parametrize("stage", ["native", "remote"])
async def test_one_total_deadline_cancels_inflight_method_and_records_failure(monkeypatch, isolated_config, stage):
    isolated_config.total_timeout_seconds = .02
    closed = asyncio.Event()
    async def forever(*args):
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    if stage == "native":
        monkeypatch.setattr(fetch, "_native_document", forever)
    else:
        monkeypatch.setattr(fetch, "_native_document", AsyncMock(return_value={"success": False, "debug": "fetch_connect_failed"}))
        monkeypatch.setattr(fetch, "_remote_document", forever)
    result = await asyncio.wait_for(fetch.fetch_public_document(URL), 1)
    assert result["debug"] == "fetch_timeout" and closed.is_set()
    assert result["attempts"][-1]["method"] == ("native" if stage == "native" else "tavily")
    assert not result["attempts"][-1]["success"] and result["attempts"][-1]["debug"] == "fetch_timeout"


async def test_remote_response_cancel_propagates_and_closes_real_http_boundaries(wire, isolated_config):
    isolated_config.provider = "remote"
    entered, closed = asyncio.Event(), asyncio.Event()
    class WaitingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            entered.set()
            await asyncio.Event().wait()
            yield b"unreachable"
        async def aclose(self):
            closed.set()
    async def handler(request):
        return httpx.Response(200, stream=WaitingStream())
    net = wire(handler)
    task = asyncio.create_task(fetch.fetch_public_document(URL))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and all(c.is_closed for c in net.clients)


async def test_extra_tool_parameters_cannot_forward_headers_or_override_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_STORAGE_ROOT", str(tmp_path))
    registry = ToolRegistry()
    registry.register(WebFetchTool())
    result = await ToolExecutor(registry).execute("web_fetch", {"url": URL, "headers": {"Cookie": "synthetic"}, "provider": "remote"},
        context=ToolExecutionContext(tenant_id="route", user_id="user", session_id="s"))
    assert not result["success"]


async def test_real_executor_publishes_and_reads_remote_body_without_claiming_direct(wire, isolated_config, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_STORAGE_ROOT", str(tmp_path / "storage"))
    context = ToolExecutionContext(tenant_id="routing", user_id="researcher", session_id="research")
    body = BODY + "\n" + "\n".join(f"Original evidence line {i:03}." for i in range(100))
    async def handler(request):
        if request.method == "GET":
            raise httpx.ConnectError("synthetic failure", request=request)
        if "/broken" in json.loads(request.content)["urls"][0]:
            return httpx.Response(503)
        return httpx.Response(200, json={"results": [{"url": URL, "raw_content": body}], "failed_results": []})
    net = wire(handler)
    registry = ToolRegistry()
    for tool in (WebFetchTool(), ReadTool()):
        registry.register(tool)
    executor = ToolExecutor(registry)
    result = await executor.execute("web_fetch", {"url": URL}, context=context)
    assert result["success"] and result["_no_truncate"]
    source = result["results"][0]
    assert source_body(source, context) == body
    assert source["provider"] == "tavily_extract" and source["content_kind"] == "provider_fetch"
    assert source["final_url"] is None and source["final_url_verified"] is False
    assert source["freshness"] == "unknown" and source["notice"]
    assert hashlib.sha256(Path(source["body_file_path"]).read_bytes()).hexdigest() == source["body_sha256"]
    read = await executor.execute("read", {"file_path": source["body_file_path"], "offset": 52, "limit": 1}, context=context)
    assert "Original evidence line 050." in read["content"]
    assert "Original evidence line 050." not in source["content"]
    assert [r.method for r in net.requests] == ["GET", "POST"]


@pytest.mark.parametrize("identity", [{"tenant_id": "other"}, {"user_id": "other"}, {"session_id": "other"}])
async def test_provider_body_cannot_be_read_by_another_trusted_identity(wire, isolated_config, tmp_path, monkeypatch, identity):
    monkeypatch.setenv("AGENT_RUNNER_STORAGE_ROOT", str(tmp_path / "storage"))
    isolated_config.provider = "remote"
    async def handler(request):
        return httpx.Response(200, json={"results": [{"url": URL, "raw_content": "SYNTHETIC_PRIVATE_BODY"}], "failed_results": []})
    wire(handler)
    context = ToolExecutionContext(tenant_id="routing", user_id="researcher", session_id="research")
    registry = ToolRegistry()
    registry.register(WebFetchTool())
    registry.register(ReadTool())
    executor = ToolExecutor(registry)
    result = await executor.execute("web_fetch", {"url": URL}, context=context)
    assert result["success"]
    source = result["results"][0]
    other = context.derive(**identity)
    with pytest.raises(ValueError, match="source_scope_invalid"):
        source_body(source, other)
    denied = await executor.execute("read", {"file_path": source["body_file_path"]}, context=other)
    assert "research_scope_invalid" in str(denied) and "SYNTHETIC_PRIVATE_BODY" not in str(denied)

async def test_provider_navigation_survives_real_executor_publisher_and_read(wire, isolated_config, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_STORAGE_ROOT", str(tmp_path / "storage"))
    isolated_config.provider = "remote"
    link = "https://acme.test/official-company-profile.pdf"
    async def handler(request):
        return httpx.Response(200, json={"results": [{"url": URL, "raw_content": BODY, "links": [link]}], "failed_results": []})
    wire(handler)
    context = ToolExecutionContext(tenant_id="routing", user_id="researcher", session_id="links-proof")
    registry = ToolRegistry()
    registry.register(WebFetchTool())
    registry.register(ReadTool())
    executor = ToolExecutor(registry)
    result = await executor.execute("web_fetch", {"url": URL}, context=context)
    assert result["success"]
    source = result["results"][0]
    assert link in source_body(source, context)
    page = await executor.execute("read", {"file_path": source["body_file_path"]}, context=context)
    assert link in page["content"] and "Provider-reported navigation links" in page["content"]
