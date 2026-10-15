"""真实 Provider HTTP 错误必须触发故障转移，不能被异常包装或响应正文掩盖。"""

import json
from unittest.mock import MagicMock

import httpx
import pytest

from src.llm.failover import CircuitBreaker, FailoverGateway, ProviderSlot, is_retryable_error
from src.llm.key_pool import KeyPool
from src.llm.providers.deepseek import DeepSeekProvider
from src.llm.providers.moonshot import MoonshotProvider
from src.llm.providers.qwen import QwenProvider
from src.llm.providers.zhipu import ZhipuProvider


PROVIDERS = [DeepSeekProvider, QwenProvider, ZhipuProvider, MoonshotProvider]
BUSY_BODY = json.dumps({"error": {
    "message": "Service is too busy. Please use an alternative provider.",
    "type": "service_unavailable_error",
    "code": "service_unavailable_error",
}})
MESSAGES = [{"role": "user", "content": "早上好"}]


class _BodyStream(httpx.AsyncByteStream):
    """保留 stream=True 时未预读正文的真实行为。"""

    def __init__(self, body):
        self.body = body.encode()

    async def __aiter__(self):
        yield self.body


def _mock_http(monkeypatch, handler):
    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def make_client(*args, **kwargs):
        return client_class(*args, transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", make_client)
    for module in ("deepseek", "qwen", "zhipu"):
        monkeypatch.setattr(f"src.llm.providers.{module}.log_llm_invoke", lambda **kwargs: None)


def _gateway(monkeypatch, primary, backup):
    """只隔离配置和日志；请求、Provider、KeyPool 和切换循环均执行真实实现。"""
    gateway = FailoverGateway.__new__(FailoverGateway)
    gateway._primary_name = "primary"
    gateway._model_codes = {}
    gateway._slots = [ProviderSlot(
        provider_name=name,
        key_pool=KeyPool(keys=["test-key"], max_concurrent_per_key=1, queue_timeout=1),
        circuit_breaker=CircuitBreaker(provider_name=name),
    ) for name in ("primary", "backup")]
    gateway._log_failover_event = MagicMock()
    providers = {"primary": primary, "backup": backup}
    monkeypatch.setattr("src.llm.failover._build_provider",
                        lambda name, api_key, model=None: providers[name])
    return gateway


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True], ids=["chat", "stream"])
@pytest.mark.parametrize("status", [503, 500, 429, 400])
async def test_provider_preserves_http_status_without_changing_runtime_error_contract(
    monkeypatch, provider_class, streaming, status,
):
    """正文不含状态码时仍可诊断；400 正文含 503/timeout 也不能误切换。"""
    body = BUSY_BODY if status != 400 else "invalid request: timeout reference 503"
    _mock_http(monkeypatch, lambda request: httpx.Response(status, stream=_BodyStream(body)))
    provider = provider_class(api_key="test-key", model="test-model")

    with pytest.raises(RuntimeError) as caught:
        if streaming:
            async for _ in provider.stream_chat(MESSAGES):
                pass
        else:
            await provider.chat(MESSAGES)

    assert caught.value.status_code == status
    assert body in str(caught.value)
    assert is_retryable_error(caught.value) is (status != 400)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", PROVIDERS)
@pytest.mark.parametrize("streaming", [False, True], ids=["chat", "stream"])
@pytest.mark.parametrize("status", [503, 429, 400])
async def test_http_response_controls_fallback_and_circuit_failure_count(
    monkeypatch, provider_class, streaming, status,
):
    """复现生产链路：HTTP 错误经 Provider 包装后必须正确切换并计入熔断。"""
    primary = provider_class(api_key="test-key", model="test-model", base_url="https://primary.test")
    backup = ZhipuProvider(api_key="test-key", base_url="https://backup.test")
    gateway = _gateway(monkeypatch, primary, backup)
    attempts = []

    def respond(request):
        attempts.append(request.url.host)
        if request.url.host == "primary.test":
            body = BUSY_BODY if status != 400 else "invalid request: timeout reference 503"
            return httpx.Response(status, stream=_BodyStream(body))
        if streaming:
            body = 'data: {"choices":[{"delta":{"content":"备用回复"}}]}\n\ndata: [DONE]\n\n'
            return httpx.Response(200, stream=_BodyStream(body))
        return httpx.Response(200, json={"choices": [{"message": {"content": "备用回复"}}]})

    _mock_http(monkeypatch, respond)

    async def call():
        if streaming:
            return "".join([chunk async for chunk in gateway.stream_chat(MESSAGES)])
        return (await gateway.chat(MESSAGES))["content"]

    if status == 400:
        with pytest.raises(RuntimeError):
            await call()
        assert attempts == ["primary.test"]
        assert gateway._slots[0].circuit_breaker._failure_count == 0
        gateway._log_failover_event.assert_not_called()
    else:
        assert await call() == "备用回复"
        assert attempts == ["primary.test", "backup.test"]
        assert gateway._slots[0].circuit_breaker._failure_count == 1
        assert gateway._slots[1].circuit_breaker._failure_count == 0
        gateway._log_failover_event.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_class", PROVIDERS)
@pytest.mark.parametrize("read_error", [httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError])
@pytest.mark.parametrize("status", [503, 429, 400])
async def test_broken_error_body_preserves_known_status_for_fallback(
    monkeypatch, provider_class, read_error, status,
):
    """错误正文只是诊断信息；读取失败不能丢弃已收到的 HTTP 状态。"""
    primary = provider_class(api_key="test-key", model="test-model", base_url="https://primary.test")
    backup = ZhipuProvider(api_key="test-key", base_url="https://backup.test")
    gateway = _gateway(monkeypatch, primary, backup)
    attempts = []

    class BrokenErrorBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'{"error":'
            raise read_error("error body interrupted")

    def respond(request):
        attempts.append(request.url.host)
        if request.url.host == "primary.test":
            return httpx.Response(status, stream=BrokenErrorBody())
        return httpx.Response(200, stream=_BodyStream(
            'data: {"choices":[{"delta":{"content":"备用回复"}}]}\n\ndata: [DONE]\n\n'))

    _mock_http(monkeypatch, respond)

    async def call():
        return "".join([chunk async for chunk in gateway.stream_chat(MESSAGES)])

    if status == 400:
        with pytest.raises(RuntimeError) as caught:
            await call()
        assert caught.value.status_code == 400
        assert read_error.__name__ in str(caught.value)
        assert attempts == ["primary.test"]
        assert gateway._slots[0].circuit_breaker._failure_count == 0
        gateway._log_failover_event.assert_not_called()
    else:
        assert await call() == "备用回复"
        assert attempts == ["primary.test", "backup.test"]
        assert gateway._slots[0].circuit_breaker._failure_count == 1
        gateway._log_failover_event.assert_called_once()


@pytest.mark.asyncio
async def test_stream_failure_after_output_does_not_append_another_models_reply(monkeypatch):
    """回复已交付一部分时，断线必须显式失败，不能拼接备用模型的新回复。"""
    primary = DeepSeekProvider(api_key="test-key", base_url="https://primary.test")
    backup = ZhipuProvider(api_key="test-key", base_url="https://backup.test")
    gateway = _gateway(monkeypatch, primary, backup)
    attempts = []

    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise httpx.ConnectError("connection lost after output")

    def respond(request):
        attempts.append(request.url.host)
        if request.url.host == "primary.test":
            return httpx.Response(200, stream=InterruptedStream())
        return httpx.Response(200, stream=_BodyStream(
            'data: {"choices":[{"delta":{"content":"another reply"}}]}\n\ndata: [DONE]\n\n'))

    _mock_http(monkeypatch, respond)
    chunks = []
    with pytest.raises(httpx.ConnectError):
        async for chunk in gateway.stream_chat(MESSAGES):
            chunks.append(chunk)

    assert chunks == ["partial"]
    assert attempts == ["primary.test"]
    gateway._log_failover_event.assert_not_called()
