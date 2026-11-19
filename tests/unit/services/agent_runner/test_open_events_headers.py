"""open_events 请求头口径：authorization 为空时不写 Authorization header。

docs/audit/weekly-2026-11-19.md P1：web_c 渠道事件流以服务凭据 + 渠道头调用
open_events（authorization=None），旧实现无条件写 'Authorization': None，
httpx 抛 TypeError('Header value must be str or bytes, not NoneType')，
C 端 SSE 每次握手必败。修复口径与 request() 一致：仅非空时才写该 header。
"""

from types import SimpleNamespace

import httpx
import pytest

from src.services.agent_runner import web_client as web_client_module
from src.services.agent_runner.web_client import RunnerServiceClient

pytestmark = pytest.mark.unit


def _install_transport(monkeypatch):
    """注入 MockTransport，捕获上游实际收到的请求头并返回 SSE 200 响应。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=b"")

    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(web_client_module.httpx, "AsyncClient", patched)
    return captured


def _config():
    return SimpleNamespace(api_url="http://runner.test", web_service_id="svc", web_enabled=True)


@pytest.mark.asyncio
async def test_open_events_without_authorization_omits_header(monkeypatch):
    """web_c：authorization=None 时握手成功且不携带 Authorization header。"""
    captured = _install_transport(monkeypatch)
    client = RunnerServiceClient(_config(), token="service-token")

    response, upstream = await client.open_events(
        "/v1/runners/r1/events", authorization=None,
        channel_source="web_c", channel_user="user-1", after_seq=7)
    try:
        assert response.status_code == 200
        headers = captured["headers"]
        assert "authorization" not in headers
        assert headers["x-agentrunner-service"] == "svc"
        assert headers["x-agentrunner-service-token"] == "service-token"
        assert headers["x-agentrunner-source"] == "web_c"
        assert headers["x-agentrunner-channel-user"] == "user-1"
        assert captured["params"] == {"after_seq": "7"}
    finally:
        await response.aclose()
        await upstream.aclose()


@pytest.mark.asyncio
async def test_open_events_with_authorization_forwards_header(monkeypatch):
    """B 端 web：authorization 非空时按原值转发。"""
    captured = _install_transport(monkeypatch)
    client = RunnerServiceClient(_config(), token="service-token")

    response, upstream = await client.open_events(
        "/v1/runners/r1/events", authorization="Bearer user-token")
    try:
        assert captured["headers"]["authorization"] == "Bearer user-token"
    finally:
        await response.aclose()
        await upstream.aclose()
