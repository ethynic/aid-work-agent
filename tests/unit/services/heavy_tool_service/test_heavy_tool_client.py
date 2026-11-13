"""HeavyToolClient 重试与失败语义单测（设计 §4.1/§8）。

覆盖（重试范围强制限定）：
- 仅连接建立阶段失败重试一次（DNS/connect 拒绝/connect 超时）；
- 已送达请求的读超时严禁重试（防 modify 类原地写双执行损坏用户文件）；
- busy 结构化透传、不自动重试；
- 401/5xx → HeavyToolUnavailable；400/业务级 success=false 透传为普通 dict。"""

import httpx
import pytest

from src.services.heavy_tool_service import client
from src.services.heavy_tool_service.client import (
    HeavyToolBusy,
    HeavyToolUnavailable,
    execute,
)


class _FlakyTransport(httpx.AsyncBaseTransport):
    """第 N 次请求抛异常，其后返回成功响应。"""

    def __init__(self, fail_times, error, response=None):
        self.fail_times = fail_times
        self.error = error
        self.response = response or httpx.Response(
            200, json={"success": True, "result": {"success": True}})
        self.requests = 0

    async def handle_async_request(self, request):
        self.requests += 1
        if self.requests <= self.fail_times:
            raise self.error
        return self.response


class _CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self, response):
        self.response = response
        self.requests = 0

    async def handle_async_request(self, request):
        self.requests += 1
        return self.response


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_WEB_SERVICE_TOKEN", "tok")
    monkeypatch.setenv("HEAVY_TOOLS_URL", "http://heavy-tools-test:8093")


def _patch_async_client(monkeypatch, **client_kwargs):
    """注入 transport 等构造参数：先捕获真实类再打补丁（防替身递归）。"""
    real_cls = client.httpx.AsyncClient

    def factory(**kwargs):
        return real_cls(**{**client_kwargs, **kwargs})

    monkeypatch.setattr(client.httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_connect_error_retries_once_then_succeeds(monkeypatch):
    transport = _FlakyTransport(1, httpx.ConnectError("dns down"))
    _patch_async_client(monkeypatch, transport=transport)
    result = await execute(tool="paddleocr_doc_parsing", params={"file_path": "/app/storage/a.pdf"})
    # 解包语义：返回工具 execute 原始返回
    assert result == {"success": True}
    assert transport.requests == 2


@pytest.mark.asyncio
async def test_connect_failure_twice_raises_unavailable(monkeypatch):
    transport = _FlakyTransport(2, httpx.ConnectError("still down"))
    _patch_async_client(monkeypatch, transport=transport)
    with pytest.raises(HeavyToolUnavailable):
        await execute(tool="paddleocr_doc_parsing", params={})
    assert transport.requests == 2  # 仅重试一次


@pytest.mark.asyncio
async def test_read_timeout_never_retried(monkeypatch):
    # 已送达请求的读超时：严禁重试（§4.1 不变量）
    transport = _FlakyTransport(1, httpx.ReadTimeout("read timeout"))
    _patch_async_client(monkeypatch, transport=transport)
    with pytest.raises(HeavyToolUnavailable):
        await execute(tool="excel_process", handler="modify", params={})
    assert transport.requests == 1


@pytest.mark.asyncio
async def test_busy_raised_without_retry(monkeypatch):
    response = httpx.Response(429, json={
        "success": False, "error_code": "busy", "error": "重工具服务繁忙，请稍后重试"})
    transport = _CountingTransport(response)
    _patch_async_client(monkeypatch, transport=transport)
    with pytest.raises(HeavyToolBusy):
        await execute(tool="excel_process", handler="export", params={})
    assert transport.requests == 1


@pytest.mark.asyncio
async def test_unauthorized_maps_to_unavailable(monkeypatch):
    transport = _CountingTransport(httpx.Response(401, json={"success": False}))
    _patch_async_client(monkeypatch, transport=transport)
    with pytest.raises(HeavyToolUnavailable, match="认证"):
        await execute(tool="excel_process", params={})


@pytest.mark.asyncio
async def test_path_violation_400_passed_through_as_business_failure(monkeypatch):
    transport = _CountingTransport(httpx.Response(
        400, json={"success": False, "error": "路径超出重工具服务允许范围: /etc/passwd"}))
    _patch_async_client(monkeypatch, transport=transport)
    result = await execute(tool="excel_process", params={"file_paths": ["/etc/passwd"]})
    assert result["success"] is False
    assert "允许范围" in result["error"]


@pytest.mark.asyncio
async def test_business_failure_passed_through(monkeypatch):
    transport = _CountingTransport(httpx.Response(200, json={
        "success": False, "result": {"success": False, "error": "文件不存在"}}))
    _patch_async_client(monkeypatch, transport=transport)
    result = await execute(tool="excel_process", handler="read", params={})
    assert result == {"success": False, "error": "文件不存在"}


def test_serialize_context_excludes_gateway_and_inventory():
    """可序列化核对（§4.2）：llm_gateway 与 knowledge_search_inventory 不进 payload。"""
    from src.tools.context import ToolExecutionContext

    context = ToolExecutionContext(
        tenant_id="t1", user_id="u1", session_id="s1", tool_call_id="call_1",
        llm_gateway=object(), knowledge_search_inventory=object())
    payload = client.serialize_context(context)
    assert payload["tenant_id"] == "t1"
    assert payload["tool_call_id"] == "call_1"
    assert "llm_gateway" not in payload
    assert "knowledge_search_inventory" not in payload
