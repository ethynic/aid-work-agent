"""
LLM Failover 单元测试

覆盖：
- CircuitBreaker 状态机
- 错误分类
- FailoverGateway 非流式/流式调用
- Gateway 集成
- 告警逻辑
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import httpx
import pytest

from src.llm.failover import (
    CircuitBreaker,
    FailoverGateway,
    LLMAllProvidersFailedError,
    ProviderSlot,
    is_retryable_error,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cb():
    return CircuitBreaker(
        failure_threshold=3,
        recovery_timeout=1,
        provider_name="test_provider",
    )


@pytest.fixture
def mock_key_pool():
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value="test-key")
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.stats.return_value = [{"key_suffix": "...test", "active": 0, "total": 0, "max_concurrent": 2}]
    return pool


@pytest.fixture
def mock_slot(mock_key_pool):
    return ProviderSlot(
        provider_name="mock",
        key_pool=mock_key_pool,
        circuit_breaker=CircuitBreaker(failure_threshold=3, recovery_timeout=1, provider_name="mock"),
    )


def _make_slot(name, key_pool=None, cb=None):
    """创建一个 ProviderSlot"""
    if key_pool is None:
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value="test-key")
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        pool.stats.return_value = []
        key_pool = pool
    if cb is None:
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1, provider_name=name)
    return ProviderSlot(provider_name=name, key_pool=key_pool, circuit_breaker=cb)


# ---------------------------------------------------------------------------
# 5.1 CircuitBreaker 测试
# ---------------------------------------------------------------------------

class TestCircuitBreaker:

    def test_initial_state_is_closed(self, cb):
        assert cb.state == "closed"
        assert cb.can_attempt() is True

    def test_record_success_resets_failures(self, cb):
        cb.record_failure()
        cb.record_failure()
        assert cb._failure_count == 2
        cb.record_success()
        assert cb._failure_count == 0
        assert cb.state == "closed"

    def test_consecutive_failures_trigger_open(self, cb):
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "closed"
        cb.record_failure()
        assert cb.state == "open"

    def test_open_state_blocks_attempts(self, cb):
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "open"
        assert cb.can_attempt() is False

    def test_open_state_transitions_to_half_open_after_timeout(self, cb):
        for _ in range(3):
            cb.record_failure()
        assert cb.state == "open"

        # 模拟超时时间已过
        cb._last_failure_time = time.monotonic() - 2  # recovery_timeout=1
        assert cb.can_attempt() is True
        assert cb.state == "half_open"

    def test_half_open_success_returns_to_closed(self, cb):
        for _ in range(3):
            cb.record_failure()
        cb._last_failure_time = time.monotonic() - 2
        cb.can_attempt()  # 触发进入 half_open
        assert cb.state == "half_open"

        cb.record_success()
        assert cb.state == "closed"
        assert cb._failure_count == 0

    def test_half_open_failure_returns_to_open(self, cb):
        for _ in range(3):
            cb.record_failure()
        cb._last_failure_time = time.monotonic() - 2
        cb.can_attempt()
        assert cb.state == "half_open"

        cb.record_failure()
        assert cb.state == "open"

    def test_open_not_yet_timed_out_stays_open(self, cb):
        for _ in range(3):
            cb.record_failure()
        # last_failure_time is very recent
        assert cb.state == "open"
        assert cb.can_attempt() is False


# ---------------------------------------------------------------------------
# 5.2 错误分类测试
# ---------------------------------------------------------------------------

class TestErrorClassification:

    def test_asyncio_timeout_is_retryable(self):
        assert is_retryable_error(asyncio.TimeoutError()) is True

    def test_timeout_error_is_retryable(self):
        assert is_retryable_error(TimeoutError("connection timed out")) is True

    def test_connection_error_is_retryable(self):
        assert is_retryable_error(ConnectionError("refused")) is True

    def test_httpx_connect_error_is_retryable(self):
        assert is_retryable_error(httpx.ConnectError("dns failed")) is True

    def test_http_500_is_retryable(self):
        resp = MagicMock()
        resp.status_code = 500
        err = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=resp)
        assert is_retryable_error(err) is True

    def test_http_502_is_retryable(self):
        resp = MagicMock()
        resp.status_code = 502
        err = httpx.HTTPStatusError("Bad Gateway", request=MagicMock(), response=resp)
        assert is_retryable_error(err) is True

    def test_http_429_is_retryable(self):
        resp = MagicMock()
        resp.status_code = 429
        err = httpx.HTTPStatusError("Rate Limited", request=MagicMock(), response=resp)
        assert is_retryable_error(err) is True

    def test_http_400_is_not_retryable(self):
        resp = MagicMock()
        resp.status_code = 400
        err = httpx.HTTPStatusError("Bad Request", request=MagicMock(), response=resp)
        assert is_retryable_error(err) is False

    def test_http_401_is_not_retryable(self):
        resp = MagicMock()
        resp.status_code = 401
        err = httpx.HTTPStatusError("Unauthorized", request=MagicMock(), response=resp)
        assert is_retryable_error(err) is False

    def test_runtime_error_503_is_retryable(self):
        assert is_retryable_error(RuntimeError("HTTP 503: Service Unavailable")) is True

    def test_runtime_error_429_is_retryable(self):
        assert is_retryable_error(RuntimeError("Rate limit 429")) is True

    def test_runtime_error_timeout_is_retryable(self):
        assert is_retryable_error(RuntimeError("Connection timeout")) is True

    def test_runtime_error_generic_is_not_retryable(self):
        assert is_retryable_error(RuntimeError("unknown processing error")) is False

    def test_runtime_error_authentication_is_retryable(self):
        assert is_retryable_error(RuntimeError("Authentication Fails, Your api key is invalid")) is True

    def test_runtime_error_invalid_key_is_retryable(self):
        assert is_retryable_error(RuntimeError("invalid api key")) is True

    def test_value_error_is_not_retryable(self):
        assert is_retryable_error(ValueError("bad value")) is False

    def test_type_error_is_not_retryable(self):
        assert is_retryable_error(TypeError("wrong type")) is False


# ---------------------------------------------------------------------------
# 5.3 FailoverGateway 非流式调用测试
# ---------------------------------------------------------------------------

class TestFailoverGatewayNonStreaming:

    @pytest.mark.asyncio
    async def test_primary_success_returns_directly(self):
        gw = MagicMock(spec=FailoverGateway)
        gw._get_provider_chain = MagicMock(return_value=[])
        gw._slots = [_make_slot("primary")]

        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "primary"
            fg._slots = [_make_slot("primary")]
            fg._last_failover = None
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.enabled = False

            # Mock _call_slot to succeed
            async def mock_call_slot(slot, fn_name, **kwargs):
                return {"content": "hello", "tool_calls": None}

            fg._call_slot = mock_call_slot
            fg._get_provider_chain = lambda: fg._slots

            result = await fg.call_with_failover("chat", messages=[])
            assert result == {"content": "hello", "tool_calls": None}

    @pytest.mark.asyncio
    async def test_primary_failure_fallback_to_secondary(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "primary"
            primary = _make_slot("primary")
            secondary = _make_slot("secondary")
            fg._slots = [primary, secondary]
            fg._last_failover = None
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.enabled = False

            call_count = 0

            async def mock_call_slot(slot, fn_name, **kwargs):
                nonlocal call_count
                call_count += 1
                if slot.provider_name == "primary":
                    raise asyncio.TimeoutError("timeout")
                return {"content": "fallback response"}

            fg._call_slot = mock_call_slot
            fg._get_provider_chain = lambda: fg._slots
            fg._log_failover_event = MagicMock()

            result = await fg.call_with_failover("chat", messages=[])
            assert result == {"content": "fallback response"}
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_non_retryable_error_does_not_fallback(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "primary"
            primary = _make_slot("primary")
            secondary = _make_slot("secondary")
            fg._slots = [primary, secondary]
            fg._last_failover = None
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.enabled = False

            async def mock_call_slot(slot, fn_name, **kwargs):
                resp = MagicMock()
                resp.status_code = 400
                raise httpx.HTTPStatusError("Bad Request", request=MagicMock(), response=resp)

            fg._call_slot = mock_call_slot
            fg._get_provider_chain = lambda: fg._slots

            with pytest.raises(httpx.HTTPStatusError):
                await fg.call_with_failover("chat", messages=[])

    @pytest.mark.asyncio
    async def test_all_providers_failed_raises_error(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "a"
            fg._slots = [_make_slot("a"), _make_slot("b")]
            fg._last_failover = None
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.enabled = False

            async def mock_call_slot(slot, fn_name, **kwargs):
                raise asyncio.TimeoutError("timeout")

            fg._call_slot = mock_call_slot
            fg._get_provider_chain = lambda: fg._slots
            fg._log_failover_event = MagicMock()

            with pytest.raises(LLMAllProvidersFailedError) as exc_info:
                await fg.call_with_failover("chat", messages=[])

            assert len(exc_info.value.errors) == 2

    @pytest.mark.asyncio
    async def test_open_circuit_breaker_skips_provider(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "primary"
            primary = _make_slot("primary")
            # 手动将 primary 的熔断器设为 open
            primary.circuit_breaker._state = "open"
            primary.circuit_breaker._last_failure_time = time.monotonic()
            secondary = _make_slot("secondary")
            fg._slots = [primary, secondary]
            fg._last_failover = None
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.enabled = False

            async def mock_call_slot(slot, fn_name, **kwargs):
                return {"content": f"from {slot.provider_name}"}

            fg._call_slot = mock_call_slot
            # is_available() 对 open 状态返回 False
            fg._get_provider_chain = lambda: [s for s in fg._slots if s.is_available()]

            result = await fg.call_with_failover("chat", messages=[])
            assert result == {"content": "from secondary"}


# ---------------------------------------------------------------------------
# 5.4 FailoverGateway 流式调用测试
# ---------------------------------------------------------------------------

class TestFailoverGatewayStreaming:

    @pytest.mark.asyncio
    async def test_stream_primary_success(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "primary"
            primary = _make_slot("primary")
            fg._slots = [primary]
            fg._last_failover = None
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.enabled = False

            async def mock_stream(slot, fn_name, **kwargs):
                for chunk in ["hello", " world"]:
                    yield chunk

            fg._stream_slot = mock_stream
            fg._get_provider_chain = lambda: fg._slots

            chunks = []
            async for chunk in fg.stream_with_failover("stream_chat", messages=[]):
                chunks.append(chunk)

            assert chunks == ["hello", " world"]

    @pytest.mark.asyncio
    async def test_stream_connection_failure_fallback(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "primary"
            primary = _make_slot("primary")
            secondary = _make_slot("secondary")
            fg._slots = [primary, secondary]
            fg._last_failover = None
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.enabled = False

            stream_attempt = 0

            async def mock_stream(slot, fn_name, **kwargs):
                nonlocal stream_attempt
                stream_attempt += 1
                if slot.provider_name == "primary":
                    raise asyncio.TimeoutError("connection failed")
                for chunk in ["fallback", " data"]:
                    yield chunk

            fg._stream_slot = mock_stream
            fg._get_provider_chain = lambda: fg._slots
            fg._log_failover_event = MagicMock()

            chunks = []
            async for chunk in fg.stream_with_failover("stream_chat", messages=[]):
                chunks.append(chunk)

            assert chunks == ["fallback", " data"]

    @pytest.mark.asyncio
    async def test_stream_all_providers_failed(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "a"
            fg._slots = [_make_slot("a")]
            fg._last_failover = None
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.enabled = False

            async def mock_stream(slot, fn_name, **kwargs):
                raise asyncio.TimeoutError("timeout")
                yield  # make it async generator

            fg._stream_slot = mock_stream
            fg._get_provider_chain = lambda: fg._slots
            fg._log_failover_event = MagicMock()

            with pytest.raises(LLMAllProvidersFailedError):
                async for _ in fg.stream_with_failover("stream_chat", messages=[]):
                    pass


# ---------------------------------------------------------------------------
# 5.4b 显式 model kwarg 处理测试（仅作用于主 provider 槽位）
# ---------------------------------------------------------------------------

class TestExplicitModelHandling:
    """显式 model kwarg 仅作用于主 provider 槽位，防止跨 provider 模型错配

    背景：各 provider 的 request_body.update(kwargs) 会用 kwargs 里的 model
    覆盖请求体模型，若 failover 原样透传，链上所有 provider 都会收到同一个
    模型串（如 qwen/deepseek-v4-flash）。
    """

    def _make_fg(self, slots):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
        fg._slots = slots
        fg._primary_name = slots[0].provider_name
        fg._model_codes = {}
        fg._last_failover = None
        fg._failover_cfg = MagicMock()
        fg._failover_cfg.alert.enabled = False
        fg._get_provider_chain = lambda: fg._slots
        fg._log_failover_event = MagicMock()
        return fg

    @pytest.mark.asyncio
    async def test_explicit_model_only_applies_to_primary_slot(self):
        fg = self._make_fg([_make_slot("primary"), _make_slot("secondary")])
        captured = []

        async def mock_call_slot(slot, fn_name, **kwargs):
            captured.append((slot.provider_name, kwargs.get("model_override")))
            if slot.provider_name == "primary":
                raise asyncio.TimeoutError("timeout")
            return {"content": "ok"}

        fg._call_slot = mock_call_slot

        await fg.call_with_failover("chat", messages=[], model="deepseek-v4-flash")

        assert captured == [("primary", "deepseek-v4-flash"), ("secondary", None)]

    @pytest.mark.asyncio
    async def test_model_kwarg_removed_from_provider_kwargs(self):
        fg = self._make_fg([_make_slot("primary")])
        captured_kwargs = {}

        async def mock_call_slot(slot, fn_name, **kwargs):
            captured_kwargs.update(kwargs)
            return {"content": "ok"}

        fg._call_slot = mock_call_slot

        await fg.call_with_failover("chat", messages=[], model="deepseek-v4-flash")

        assert "model" not in captured_kwargs
        assert captured_kwargs["model_override"] == "deepseek-v4-flash"

    @pytest.mark.asyncio
    async def test_no_explicit_model_all_slots_use_own_config(self):
        fg = self._make_fg([_make_slot("primary"), _make_slot("secondary")])
        captured = []

        async def mock_call_slot(slot, fn_name, **kwargs):
            captured.append((slot.provider_name, kwargs.get("model_override")))
            if slot.provider_name == "primary":
                raise asyncio.TimeoutError("timeout")
            return {"content": "ok"}

        fg._call_slot = mock_call_slot

        await fg.call_with_failover("chat", messages=[])

        assert captured == [("primary", None), ("secondary", None)]

    @pytest.mark.asyncio
    async def test_stream_explicit_model_only_applies_to_primary_slot(self):
        fg = self._make_fg([_make_slot("primary"), _make_slot("secondary")])
        captured = []

        async def mock_stream(slot, fn_name, **kwargs):
            captured.append((slot.provider_name, kwargs.get("model_override")))
            if slot.provider_name == "primary":
                raise asyncio.TimeoutError("connection failed")
            for chunk in ["fallback"]:
                yield chunk

        fg._stream_slot = mock_stream

        chunks = []
        async for chunk in fg.stream_with_failover("stream_chat", messages=[], model="deepseek-v4-flash"):
            chunks.append(chunk)

        assert chunks == ["fallback"]
        assert captured == [("primary", "deepseek-v4-flash"), ("secondary", None)]

    @pytest.mark.asyncio
    async def test_thinking_kwarg_only_kept_for_primary_slot(self):
        """thinking 是 deepseek 专属参数：备用槽剥离（zhipu 对该字段 400），主槽保留"""
        fg = self._make_fg([_make_slot("primary"), _make_slot("secondary")])
        captured = []

        async def mock_call_slot(slot, fn_name, **kwargs):
            captured.append((slot.provider_name, kwargs.get("thinking")))
            if slot.provider_name == "primary":
                raise asyncio.TimeoutError("timeout")
            return {"content": "ok"}

        fg._call_slot = mock_call_slot

        await fg.call_with_failover(
            "chat", messages=[], thinking={"type": "disabled"}
        )

        assert captured == [("primary", {"type": "disabled"}), ("secondary", None)]

    @pytest.mark.asyncio
    async def test_thinking_kwarg_absent_no_leak(self):
        """未传 thinking 时备用槽 kwargs 中不出现该键"""
        fg = self._make_fg([_make_slot("primary"), _make_slot("secondary")])
        captured_keys = []

        async def mock_call_slot(slot, fn_name, **kwargs):
            captured_keys.append(sorted(kwargs.keys()))
            if slot.provider_name == "primary":
                raise asyncio.TimeoutError("timeout")
            return {"content": "ok"}

        fg._call_slot = mock_call_slot

        await fg.call_with_failover("chat", messages=[])

        assert all("thinking" not in keys for keys in captured_keys)

    @pytest.mark.asyncio
    async def test_primary_only_thinking_params_stripped_for_fallback(self):
        """PRIMARY_ONLY_KWARGS 三参数（thinking/enable_thinking/reasoning_effort）仅主槽保留"""
        fg = self._make_fg([_make_slot("primary"), _make_slot("secondary")])
        captured = []

        async def mock_call_slot(slot, fn_name, **kwargs):
            captured.append({
                k: kwargs.get(k)
                for k in ("thinking", "enable_thinking", "reasoning_effort")
            })
            if slot.provider_name == "primary":
                raise asyncio.TimeoutError("timeout")
            return {"content": "ok"}

        fg._call_slot = mock_call_slot

        await fg.call_with_failover(
            "chat", messages=[],
            thinking={"type": "disabled"},
            enable_thinking=False,
            reasoning_effort="low",
        )

        assert captured == [
            {"thinking": {"type": "disabled"}, "enable_thinking": False, "reasoning_effort": "low"},
            {"thinking": None, "enable_thinking": None, "reasoning_effort": None},
        ]

    @pytest.mark.asyncio
    async def test_stream_thinking_kwarg_only_kept_for_primary_slot(self):
        fg = self._make_fg([_make_slot("primary"), _make_slot("secondary")])
        captured = []

        async def mock_stream(slot, fn_name, **kwargs):
            captured.append((slot.provider_name, kwargs.get("thinking")))
            if slot.provider_name == "primary":
                raise asyncio.TimeoutError("connection failed")
            for chunk in ["fallback"]:
                yield chunk

        fg._stream_slot = mock_stream

        chunks = []
        async for chunk in fg.stream_with_failover(
            "stream_chat", messages=[], thinking={"type": "disabled"}
        ):
            chunks.append(chunk)

        assert chunks == ["fallback"]
        assert captured == [("primary", {"type": "disabled"}), ("secondary", None)]

    @pytest.mark.asyncio
    async def test_call_slot_explicit_override_wins_over_model_codes(self):
        fg = self._make_fg([_make_slot("primary")])
        fg._model_codes = {"primary": "from-model-codes"}
        built = {}

        def fake_build(provider_name, api_key, model=None):
            built["model"] = model
            provider = MagicMock()
            provider.chat = AsyncMock(return_value={"content": "ok"})
            return provider

        with patch("src.llm.failover._build_provider", side_effect=fake_build):
            result = await fg._call_slot(fg._slots[0], "chat", model_override="explicit-model")

        assert result == {"content": "ok"}
        assert built["model"] == "explicit-model"

    @pytest.mark.asyncio
    async def test_call_slot_falls_back_to_model_codes(self):
        fg = self._make_fg([_make_slot("secondary")])
        fg._model_codes = {"secondary": "from-model-codes"}
        built = {}

        def fake_build(provider_name, api_key, model=None):
            built["model"] = model
            provider = MagicMock()
            provider.chat = AsyncMock(return_value={"content": "ok"})
            return provider

        with patch("src.llm.failover._build_provider", side_effect=fake_build):
            await fg._call_slot(fg._slots[0], "chat")

        assert built["model"] == "from-model-codes"


# ---------------------------------------------------------------------------
# 5.4c 链去重测试（主 provider 与备用链同名时跳过重复槽位）
# ---------------------------------------------------------------------------

class TestProviderChainDedup:

    def test_duplicate_provider_in_chain_deduped(self):
        """LLM_PROVIDER=qwen + 备用链 [qwen, zhipu] 时，qwen 只注册一个槽位"""
        cfg = MagicMock()
        cfg.get_effective_keys.return_value = ["test-key"]
        failover_cfg = MagicMock()
        failover_cfg.circuit_breaker.failure_threshold = 3
        failover_cfg.circuit_breaker.recovery_timeout = 60

        with patch.object(FailoverGateway, "_get_provider_cfg", return_value=cfg):
            fg = FailoverGateway(
                primary_name="qwen",
                fallback_names=["qwen", "zhipu", "qwen"],
                failover_cfg=failover_cfg,
            )

        names = [s.provider_name for s in fg._slots]
        assert names == ["qwen", "zhipu"]

    def test_dedup_keeps_first_occurrence(self):
        """备用链内部重复时保留首个"""
        cfg = MagicMock()
        cfg.get_effective_keys.return_value = ["test-key"]
        failover_cfg = MagicMock()
        failover_cfg.circuit_breaker.failure_threshold = 3
        failover_cfg.circuit_breaker.recovery_timeout = 60

        with patch.object(FailoverGateway, "_get_provider_cfg", return_value=cfg):
            fg = FailoverGateway(
                primary_name="deepseek",
                fallback_names=["qwen", "qwen"],
                failover_cfg=failover_cfg,
            )

        names = [s.provider_name for s in fg._slots]
        assert names == ["deepseek", "qwen"]


# ---------------------------------------------------------------------------
# 5.5 Gateway 集成测试
# ---------------------------------------------------------------------------

class TestGatewayIntegration:

    def test_failover_disabled_uses_key_pool(self):
        """failover 禁用时走原有逻辑"""
        from src.llm.gateway import LLMGateway
        from src.config.settings import settings

        original = settings.llm.failover.enabled
        try:
            settings.llm.failover.enabled = False
            gw = LLMGateway()
            assert gw._failover_enabled is False
            assert hasattr(gw, '_key_pool')
        finally:
            settings.llm.failover.enabled = original

    def test_failover_enabled_uses_failover_gateway(self):
        """failover 启用时走 FailoverGateway"""
        from src.llm.gateway import LLMGateway
        from src.config.settings import settings

        original = settings.llm.failover.enabled
        try:
            settings.llm.failover.enabled = True
            gw = LLMGateway()
            assert gw._failover_enabled is True
            assert hasattr(gw, '_failover')
            assert not hasattr(gw, '_key_pool')
        finally:
            settings.llm.failover.enabled = original

    def test_public_interface_unchanged(self):
        """公共接口签名不变"""
        from src.llm.gateway import LLMGateway
        import inspect

        gw = LLMGateway()

        # 确认方法存在
        assert hasattr(gw, 'chat')
        assert hasattr(gw, 'stream_chat')
        assert hasattr(gw, 'chat_with_tools')
        assert hasattr(gw, 'get_provider_name')
        assert hasattr(gw, 'get_model_name')
        assert hasattr(gw, 'key_pool_stats')

        # 确认 chat 的参数签名
        sig = inspect.signature(gw.chat)
        params = list(sig.parameters.keys())
        assert 'messages' in params
        assert 'tools' in params

    def test_get_provider_name_returns_primary(self):
        from src.llm.gateway import LLMGateway
        gw = LLMGateway()
        name = gw.get_provider_name()
        assert isinstance(name, str)
        assert len(name) > 0


# ---------------------------------------------------------------------------
# 5.6 告警逻辑测试
# ---------------------------------------------------------------------------

class TestAlertLogic:

    def test_alert_cooldown_blocks_duplicate(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "primary"
            fg._alert_times = {}
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.cooldown = 300

            assert fg._check_alert_cooldown("zhipu") is True
            assert fg._check_alert_cooldown("zhipu") is False  # 冷却中

    def test_alert_cooldown_allows_after_timeout(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "primary"
            fg._alert_times = {}
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.cooldown = 1  # 1s cooldown

            assert fg._check_alert_cooldown("zhipu") is True
            # 模拟冷却时间已过
            fg._alert_times["zhipu"] = time.monotonic() - 2
            assert fg._check_alert_cooldown("zhipu") is True

    @pytest.mark.asyncio
    async def test_alert_disabled_does_not_send(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "primary"
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.enabled = False

            # 不应抛异常
            await fg._send_alert("high", "test", "test content")

    @pytest.mark.asyncio
    async def test_alert_sends_via_notification_service(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._primary_name = "primary"
            fg._failover_cfg = MagicMock()
            fg._failover_cfg.alert.enabled = True
            fg._failover_cfg.alert.channel = "webhook"

            mock_ns = MagicMock()
            mock_ns.send = AsyncMock(return_value=True)

            with patch.dict("sys.modules", {"src.services.notification_service": MagicMock(
                notification_service=mock_ns,
                NotificationChannel=MagicMock(WEBHOOK="webhook", EMAIL="email"),
                NotificationMessage=MagicMock(return_value=MagicMock()),
            )}):
                await fg._send_alert("high", "test", "test content")

    def test_health_status_structure(self):
        with patch.object(FailoverGateway, '__init__', lambda self, *a, **kw: None):
            fg = FailoverGateway.__new__(FailoverGateway)
            fg._slots = [_make_slot("primary"), _make_slot("fallback")]
            fg._primary_name = "primary"
            fg._last_failover = {"from": "a", "to": "b", "reason": "timeout", "time": "2026-01-01"}

            status = fg.health_status()
            assert "primary" in status
            assert "fallbacks" in status
            assert "last_failover" in status
            assert status["primary"]["provider"] == "primary"
            assert status["primary"]["circuit_breaker"] == "closed"
            assert len(status["fallbacks"]) == 1
            assert status["fallbacks"][0]["provider"] == "fallback"
