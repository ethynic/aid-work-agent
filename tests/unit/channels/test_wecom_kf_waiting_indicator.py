"""
微信客服处理超时等待提示单元测试

覆盖：
- WeComKfAdapter 构造时接收 config.waiting_indicator（渠道级配置注入）
- _get_waiting_indicator_cfg 配置读取/容错
- _process_with_waiting_indicator 非取消式 watchdog（超时发提示、不取消任务）
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.channels.wecom_kf.adapter import WeComKfAdapter
from src.channels.wecom_kf.prompts import (
    DEFAULT_WAITING_INDICATOR_DELAY_SECONDS,
    DEFAULT_WAITING_INDICATOR_MESSAGE,
)
from src.saas.api.channel_routes import (
    _get_waiting_indicator_cfg,
    _process_with_waiting_indicator,
)


# ---------- adapter 构造 ----------


class TestAdapterWaitingIndicator:
    def test_waiting_indicator_injected_from_kwargs(self):
        """config 顶层 waiting_indicator 经 **kwargs 注入 adapter。"""
        a = WeComKfAdapter(
            corp_id="test_corp",
            secret="test_secret_xxxxxxxxxxxxxxxx",
            waiting_indicator={"enabled": True, "delay_seconds": 20, "message": "请稍候"},
        )
        assert a.waiting_indicator == {"enabled": True, "delay_seconds": 20, "message": "请稍候"}

    def test_waiting_indicator_default_empty(self):
        """未配置时默认空 dict，读取侧回退默认值。"""
        a = WeComKfAdapter(corp_id="test_corp", secret="test_secret_xxxxxxxxxxxxxxxx")
        assert a.waiting_indicator == {}


# ---------- 配置读取 ----------


def _adapter_with(wi):
    return SimpleNamespace(waiting_indicator=wi)


class TestGetWaitingIndicatorCfg:
    def test_empty_config_returns_empty(self):
        """未配置（空 dict）默认不启用，返回 {}。"""
        assert _get_waiting_indicator_cfg(_adapter_with({})) == {}

    def test_no_attribute_returns_empty(self):
        """adapter 无 waiting_indicator 属性时同样默认不启用。"""
        assert _get_waiting_indicator_cfg(SimpleNamespace()) == {}

    def test_disabled_returns_empty(self):
        """enabled=false 时返回 {}，不启用。"""
        assert _get_waiting_indicator_cfg(_adapter_with({"enabled": False})) == {}

    def test_zero_or_negative_delay_disabled(self):
        """delay_seconds<=0 时返回 {}。"""
        assert _get_waiting_indicator_cfg(_adapter_with({"enabled": True, "delay_seconds": 0})) == {}
        assert _get_waiting_indicator_cfg(_adapter_with({"enabled": True, "delay_seconds": -5})) == {}

    def test_invalid_delay_falls_back_to_default(self):
        """启用时非数字 delay_seconds 容错回退默认 15s。"""
        cfg = _get_waiting_indicator_cfg(
            _adapter_with({"enabled": True, "delay_seconds": "abc"})
        )
        assert cfg["delay_seconds"] == DEFAULT_WAITING_INDICATOR_DELAY_SECONDS

    def test_custom_values_passed_through(self):
        """合法配置原样透传。"""
        cfg = _get_waiting_indicator_cfg(
            _adapter_with({"enabled": True, "delay_seconds": 30, "message": "请稍等~"})
        )
        assert cfg == {"delay_seconds": 30.0, "message": "请稍等~"}

    def test_blank_message_falls_back_to_default(self):
        """启用时 message 空白/缺失回退默认话术。"""
        cfg = _get_waiting_indicator_cfg(
            _adapter_with({"enabled": True, "delay_seconds": 10, "message": "   "})
        )
        assert cfg["message"] == DEFAULT_WAITING_INDICATOR_MESSAGE

    def test_missing_enabled_but_custom_values_returns_empty(self):
        """无 enabled 字段（老渠道遗留）即使有 delay/message 也默认不启用。"""
        assert _get_waiting_indicator_cfg(
            _adapter_with({"delay_seconds": 10, "message": "请稍等"})
        ) == {}


# ---------- 非取消式 watchdog ----------


class TestProcessWithWaitingIndicator:
    async def _fast_coro(self):
        return {"status": "success"}

    async def _slow_coro(self):
        await asyncio.sleep(0.1)
        return {"status": "success"}

    def _cfg(self, delay=0.02, message="请稍等"):
        return {"delay_seconds": delay, "message": message}

    def _adapter(self, send_result=True):
        adapter = SimpleNamespace()
        adapter.send_waiting_indicator = AsyncMock(return_value=send_result)
        return adapter

    @pytest.mark.asyncio
    async def test_fast_coro_no_hint(self):
        """处理快速完成时不发提示、返回原结果。"""
        adapter = self._adapter()
        result = await _process_with_waiting_indicator(
            adapter, "ext_user", self._cfg(), self._fast_coro()
        )
        assert result == {"status": "success"}
        adapter.send_waiting_indicator.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_slow_coro_sends_hint_once_and_not_cancelled(self):
        """处理超时发提示恰好 1 次，且任务未被取消、最终返回结果。"""
        adapter = self._adapter()
        result = await _process_with_waiting_indicator(
            adapter, "ext_user", self._cfg(), self._slow_coro()
        )
        assert result == {"status": "success"}  # 未被取消 → 正常返回
        adapter.send_waiting_indicator.assert_awaited_once_with("ext_user", "请稍等")

    @pytest.mark.asyncio
    async def test_hint_failure_does_not_propagate(self):
        """提示语发送失败时仅记日志，不向上抛，主结果仍返回。"""

        async def _fail_hint(*args, **kwargs):
            raise RuntimeError("send failed")

        adapter = SimpleNamespace()
        adapter.send_waiting_indicator = _fail_hint
        result = await _process_with_waiting_indicator(
            adapter, "ext_user", self._cfg(), self._slow_coro()
        )
        assert result == {"status": "success"}

    @pytest.mark.asyncio
    async def test_hint_returns_false_logs_failure_and_returns_result(self, monkeypatch):
        """send_waiting_indicator 返回 False（errcode≠0）时记未送达日志，不抛错，主结果仍返回。"""
        import src.saas.api.channel_routes as cr_module

        calls = []
        monkeypatch.setattr(cr_module, "_kf_tlog", lambda msg, **kw: calls.append((msg, kw)))
        adapter = self._adapter(send_result=False)
        result = await _process_with_waiting_indicator(
            adapter, "ext_user", self._cfg(), self._slow_coro()
        )
        assert result == {"status": "success"}
        assert any("未送达" in msg for msg, _ in calls), "返回 False 应记录未送达 tlog"

    @pytest.mark.asyncio
    async def test_hint_success_logs_sent(self, monkeypatch):
        """send_waiting_indicator 返回 True 时记录已发送。"""
        import src.saas.api.channel_routes as cr_module

        calls = []
        monkeypatch.setattr(cr_module, "_kf_tlog", lambda msg, **kw: calls.append((msg, kw)))
        adapter = self._adapter(send_result=True)
        result = await _process_with_waiting_indicator(
            adapter, "ext_user", self._cfg(), self._slow_coro()
        )
        assert result == {"status": "success"}
        assert any("已发送" in msg for msg, _ in calls)

    @pytest.mark.asyncio
    async def test_coro_exception_propagates(self):
        """任务自身异常正常传播（由外层 try/except 处理）。"""
        adapter = self._adapter()

        async def _fail_coro():
            await asyncio.sleep(0.02)
            raise ValueError("agent boom")

        with pytest.raises(ValueError, match="agent boom"):
            await _process_with_waiting_indicator(
                adapter, "ext_user", self._cfg(delay=0.01), _fail_coro()
            )
