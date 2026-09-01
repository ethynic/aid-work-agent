"""
微信客服等待提示单元测试（Phase 3 已迁移到 verbose 新机制）

覆盖：
- WeComKfAdapter 构造时接收 config.waiting_indicator（渠道级配置注入，开关保留）
- 旧 waiting_indicator 配置 → resolve_verbose_feedback_config 映射（仅 enabled 开关
  + message→fallback_message；超时秒数 delay_seconds 已随 watchdog 删除、被忽略）
- 旧配置经新机制只触发一次提示（route 外层旧 watchdog 已移除，无双发）
- _process_with_waiting_indicator / _get_waiting_indicator_cfg 已删除
  （import 失败即锁定删除完成）
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.channels.wecom_kf.adapter import WeComKfAdapter
from src.channels.verbose_dispatcher import (
    _LEGACY_WAITING_DEFAULT_MESSAGE,
    resolve_verbose_feedback_config,
)


# ---------- adapter 构造（开关注入保留） ----------


class TestAdapterWaitingIndicator:
    def test_waiting_indicator_injected_from_kwargs(self):
        """config 顶层 waiting_indicator 经 **kwargs 注入 adapter。"""
        a = WeComKfAdapter(
            corp_id="test_corp",
            secret="test_secret_xxxxxxxxxxxxxxxx",
            waiting_indicator={"enabled": True, "message": "请稍候"},
        )
        assert a.waiting_indicator == {"enabled": True, "message": "请稍候"}

    def test_waiting_indicator_default_empty(self):
        """未配置时默认空 dict，读取侧视为渠道未声明（回退全局默认）。"""
        a = WeComKfAdapter(corp_id="test_corp", secret="test_secret_xxxxxxxxxxxxxxxx")
        assert a.waiting_indicator == {}


# ---------- 旧配置映射到新 verbose 机制（Phase 3，仅开关语义） ----------


class TestLegacyWaitingIndicatorMapping:
    """旧 waiting_indicator 配置经 resolve_verbose_feedback_config 映射进新机制。
    2026-09-01 产品决策：system watchdog 删除后，超时秒数（delay_seconds /
    initial_delay_seconds）已无任何运行时含义并被忽略，
    仅保留 enabled 开关与 message→fallback_message 语义。"""

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

    def test_delay_seconds_ignored_entirely(self):
        """delay_seconds（含 <=0、非法值、缺失）一律忽略，开关开即启用。"""
        for delay in (0, -5, "abc", None):
            cfg = resolve_verbose_feedback_config(
                legacy_waiting_indicator={"enabled": True, "delay_seconds": delay}
            )
            assert cfg.effective_enabled is True, delay

    def test_blank_message_maps_to_legacy_default(self):
        cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={"enabled": True, "message": "  "}
        )
        assert cfg.fallback_message == _LEGACY_WAITING_DEFAULT_MESSAGE


class TestOldWatchdogRemoved:
    """旧 watchdog 发送链路已删除：继续 import 必须失败，锁定删除完成、避免双发。"""

    def test_process_with_waiting_indicator_no_longer_importable(self):
        with pytest.raises(ImportError):
            from src.saas.api.channel_routes import (  # noqa: F401
                _process_with_waiting_indicator,
            )

    def test_get_waiting_indicator_cfg_no_longer_importable(self):
        with pytest.raises(ImportError):
            from src.saas.api.channel_routes import (  # noqa: F401
                _get_waiting_indicator_cfg,
            )

    def test_adapter_send_waiting_indicator_removed(self):
        """各 adapter 的 send_waiting_indicator 方法已删除（旧发送通道关闭）。"""
        for adapter_cls in (
            WeComKfAdapter,
            _wecom_adapter_cls(),
            _dingtalk_adapter_cls(),
            _feishu_adapter_cls(),
        ):
            assert not hasattr(adapter_cls, "send_waiting_indicator"), adapter_cls


def _wecom_adapter_cls():
    from src.channels.wecom.adapter import WeComAdapter

    return WeComAdapter


def _dingtalk_adapter_cls():
    from src.channels.dingtalk.adapter import DingTalkAdapter

    return DingTalkAdapter


def _feishu_adapter_cls():
    from src.channels.feishu.adapter import FeishuAdapter

    return FeishuAdapter


class TestLegacyConfigTriggersNewMechanismOnce:
    """原意图保留：旧配置继续生效且只发一次（走新 verbose 机制，无双发）。"""

    @pytest.mark.asyncio
    async def test_legacy_config_sends_exactly_one_status_message(self):
        """旧 waiting_indicator 启用时：解析出的 verbose 配置生效，dispatcher
        对唯一一条 verbose 事件恰好发起一次 send_status_message。"""
        from src.channels.verbose_dispatcher import ChannelVerboseDispatcher
        from src.core.verbose_feedback import VerboseFeedbackState
        from src.core.agent_events import make_verbose_event

        adapter = SimpleNamespace()
        adapter.send_status_message = AsyncMock(
            return_value=SimpleNamespace(
                status="sent", reason="", is_sent=True, suppressed=False
            )
        )

        verbose_cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={
                "enabled": True, "message": "请稍候",
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

        # 只发一次，且走的是新机制（send_status_message）
        assert adapter.send_status_message.await_count == 1
        assert dispatcher.outcome == "sent"

        # 晚到重复事件不再改写/重发（状态冻结）
        assert dispatcher.submit(event) is False
        assert adapter.send_status_message.await_count == 1
