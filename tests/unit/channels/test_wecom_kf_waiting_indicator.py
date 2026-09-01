"""
微信客服等待提示单元测试（Phase 3 已迁移到 verbose 新机制）

覆盖：
- WeComKfAdapter 构造时接收 config.waiting_indicator（渠道级配置注入）
- _get_waiting_indicator_cfg 配置读取/容错（读兼容保留，route 层不再走旧 watchdog）
- 旧 waiting_indicator 配置 → resolve_verbose_feedback_config 映射（enabled/delay_seconds/
  message 原样保留语义）
- 旧配置经新机制只触发一次提示（route 外层旧 watchdog 已移除，无双发）
- _process_with_waiting_indicator 已删除（import 失败即锁定迁移完成）
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.channels.wecom_kf.adapter import WeComKfAdapter
from src.channels.wecom_kf.prompts import (
    DEFAULT_WAITING_INDICATOR_DELAY_SECONDS,
    DEFAULT_WAITING_INDICATOR_MESSAGE,
)
from src.channels.verbose_dispatcher import resolve_verbose_feedback_config
from src.saas.api.channel_routes import _get_waiting_indicator_cfg


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


# ---------- 配置读取（读兼容保留） ----------


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


# ---------- 旧配置映射到新 verbose 机制（Phase 3） ----------


class TestLegacyWaitingIndicatorMapping:
    """旧 waiting_indicator 配置经 resolve_verbose_feedback_config 映射进新机制。
    2026-09-01 产品决策：system watchdog 删除后 delay 已无运行时含义，
    仅保留 enabled（含 delay<=0 显式关闭）与 message→fallback_message 语义。"""

    def test_enabled_legacy_config_maps_to_verbose(self):
        cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={
                "enabled": True, "delay_seconds": 20, "message": "请稍候",
            }
        )
        assert cfg.effective_enabled is True
        assert cfg.fallback_message == "请稍候"

    def test_disabled_legacy_config_stays_disabled(self):
        cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={"enabled": False, "delay_seconds": 10}
        )
        assert cfg.effective_enabled is False

    def test_invalid_delay_treated_as_enabled(self):
        """delay 非法值不再回退默认，直接视为启用（delay 已无运行时含义）。"""
        cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={"enabled": True, "delay_seconds": "abc"}
        )
        assert cfg.effective_enabled is True

    def test_blank_message_maps_to_legacy_default(self):
        cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={"enabled": True, "message": "  "}
        )
        assert cfg.fallback_message == DEFAULT_WAITING_INDICATOR_MESSAGE


class TestOldWatchdogRemoved:
    """route 外层旧 watchdog 已删除：继续 import 必须失败，锁定迁移完成、避免双发。"""

    def test_process_with_waiting_indicator_no_longer_importable(self):
        with pytest.raises(ImportError):
            from src.saas.api.channel_routes import (  # noqa: F401
                _process_with_waiting_indicator,
            )


class TestLegacyConfigTriggersNewMechanismOnce:
    """原意图保留：旧配置继续生效且只发一次（走新 verbose 机制，无双发）。"""

    @pytest.mark.asyncio
    async def test_legacy_config_sends_exactly_one_status_message(self):
        """旧 waiting_indicator 启用时：解析出的 verbose 配置生效，dispatcher
        对唯一一条 verbose 事件恰好发起一次 send_status_message；旧
        send_waiting_indicator 不再被调用（route 层已不消费）。"""
        import asyncio

        from src.channels.verbose_dispatcher import (
            ChannelVerboseDispatcher,
            verbose_delivery_id,
        )
        from src.core.verbose_feedback import VerboseFeedbackState
        from src.core.agent_events import make_verbose_event

        adapter = SimpleNamespace()
        adapter.send_waiting_indicator = AsyncMock(return_value=True)
        adapter.send_status_message = AsyncMock(
            return_value=SimpleNamespace(
                status="sent", reason="", is_sent=True, suppressed=False
            )
        )

        verbose_cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={
                "enabled": True, "delay_seconds": 0.05, "message": "请稍候",
            }
        )
        assert verbose_cfg.effective_enabled is True

        state = VerboseFeedbackState()
        from src.channels.session import ChannelSessionManager

        manager = ChannelSessionManager.__new__(ChannelSessionManager)
        send_verbose = manager.make_send_verbose(
            adapter=adapter,
            event_id="msg_legacy_1",
            reply_to="ext_user_legacy",
            log_tag="[wecom_kf]",
        )

        event = make_verbose_event(
            event_id="verbose_legacy_1", data="请稍候", source="system"
        )
        assert state.try_emit(event) is True

        dispatcher = ChannelVerboseDispatcher(
            send_verbose=send_verbose,
            state=state,
            timeout_seconds=verbose_cfg.delivery_timeout_seconds,
        )
        assert dispatcher.submit(event) is True
        await dispatcher.close_and_drain(timeout=2.0)

        # 只发一次，且走的是新机制（send_status_message），旧指示器方法零调用
        assert adapter.send_status_message.await_count == 1
        adapter.send_waiting_indicator.assert_not_awaited()
        assert dispatcher.outcome == "sent"

        # 晚到重复事件不再改写/重发（状态冻结）
        assert dispatcher.submit(event) is False
        assert adapter.send_status_message.await_count == 1
