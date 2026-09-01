# -*- coding: utf-8 -*-
"""ChannelVerboseDispatcher / resolve_verbose_feedback_config 单元测试（Phase 3）

覆盖计划 Phase 3 回归：
- #4（dispatcher 侧）：callback 返回速度不受慢 adapter 影响；超时/false/异常只记
  结果不重试、不影响 final；close_and_drain 超时 cancel 并 await 发送 task。
- #8（dispatcher 侧）：每轮最多一条（重复/晚到事件丢弃）。
- #13：force_disabled=true 覆盖请求、渠道、旧 waiting indicator。
- 设计 §9.2 步骤 2/5/7/8、§10 metadata 冻结、§11 配置优先级。
"""

import asyncio

import pytest

from src.channels.base import StatusDeliveryResult
from src.channels.verbose_dispatcher import (
    ChannelVerboseDispatcher,
    build_channel_verbose_metadata_entries,
    final_delivery_id,
    resolve_verbose_feedback_config,
    verbose_delivery_id,
)
from src.core.agent_events import make_verbose_event
from src.core.verbose_feedback import (
    DEFAULT_FALLBACK_MESSAGE,
    VerboseFeedbackConfig,
    VerboseFeedbackState,
)


pytestmark = pytest.mark.channels

VALID_TEXT = "正在生成报价单，请耐心等待。"


def _event(event_id="verbose_disp_1", source="policy"):
    return make_verbose_event(event_id=event_id, data=VALID_TEXT, source=source)


# ============================================================
# delivery_id 契约（设计 §9.4）
# ============================================================


class TestDeliveryIds:
    def test_final_delivery_id_formula(self):
        assert final_delivery_id("evt_1") == "evt_1:final"

    def test_verbose_delivery_id_formula(self):
        event = _event("verbose_abc")
        assert verbose_delivery_id(event) == "verbose_abc:verbose:1"

    def test_verbose_and_final_ids_differ(self):
        assert verbose_delivery_id(_event("evt_x")) != final_delivery_id("evt_x")


# ============================================================
# dispatcher：callback 速度 / 串行 / 超时 / 失败 / 收尾
# ============================================================


class TestDispatcherSubmitSpeedAndOutcome:
    @pytest.mark.asyncio
    async def test_submit_returns_immediately_with_slow_adapter(self):
        """callback 侧只 put_nowait：adapter 10s 慢发送时 submit 立即返回（回归 #4）。"""
        state = VerboseFeedbackState()
        event = _event()
        state.try_emit(event)

        started = asyncio.Event()

        async def slow_send(evt, delivery_id):
            started.set()
            await asyncio.sleep(10)
            return StatusDeliveryResult.sent()

        dispatcher = ChannelVerboseDispatcher(
            send_verbose=slow_send, state=state, timeout_seconds=5.0
        )
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        assert dispatcher.submit(event) is True
        # submit 绝不等待 adapter：远小于慢发送时长
        assert loop.time() - t0 < 1.0
        await started.wait()
        await dispatcher.cancel_and_await()

    @pytest.mark.asyncio
    async def test_timeout_recorded_and_final_unaffected(self):
        """发送超过 delivery_timeout_seconds：记 timeout，不重试；drain 后可继续 final。"""
        state = VerboseFeedbackState()
        event = _event()
        state.try_emit(event)

        async def slow_send(evt, delivery_id):
            await asyncio.sleep(5)
            return StatusDeliveryResult.sent()

        dispatcher = ChannelVerboseDispatcher(
            send_verbose=slow_send, state=state, timeout_seconds=0.1
        )
        assert dispatcher.submit(event) is True
        await dispatcher.close_and_drain(timeout=1.5)
        assert dispatcher.outcome == "timeout"
        assert dispatcher.attempts == 1, "超时只记结果，不重试"

    @pytest.mark.asyncio
    async def test_false_and_exception_recorded_not_retried(self):
        state = VerboseFeedbackState()

        async def false_send(evt, delivery_id):
            return StatusDeliveryResult.failed("adapter returned False")

        event = _event("verbose_f1")
        state.try_emit(event)
        dispatcher = ChannelVerboseDispatcher(send_verbose=false_send, state=state)
        dispatcher.submit(event)
        await dispatcher.close_and_drain(timeout=1.0)
        assert dispatcher.outcome == "failed"
        assert dispatcher.attempts == 1

        # 异常路径：send_verbose 抛异常 → dispatcher 记 failed，不向调用方泄漏
        state2 = VerboseFeedbackState()

        async def boom(evt, delivery_id):
            raise RuntimeError("network down")

        event2 = _event("verbose_f2")
        state2.try_emit(event2)
        dispatcher2 = ChannelVerboseDispatcher(send_verbose=boom, state=state2)
        dispatcher2.submit(event2)
        await dispatcher2.close_and_drain(timeout=1.0)
        assert dispatcher2.outcome == "failed"

    @pytest.mark.asyncio
    async def test_only_sent_marks_delivery_sent(self):
        """只有 adapter 判定 sent 才记 delivery="sent"（设计 §9.2 步骤 8）。"""
        state = VerboseFeedbackState()
        event = _event()
        state.try_emit(event)

        async def ok_send(evt, delivery_id):
            assert delivery_id == verbose_delivery_id(event)
            return StatusDeliveryResult.sent()

        dispatcher = ChannelVerboseDispatcher(send_verbose=ok_send, state=state)
        dispatcher.submit(event)
        await dispatcher.close_and_drain(timeout=1.0)
        assert dispatcher.outcome == "sent"
        assert dispatcher.sent_event_id == event["eventId"]

    @pytest.mark.asyncio
    async def test_suppressed_rate_limit_outcome_preserved(self):
        state = VerboseFeedbackState()
        event = _event()
        state.try_emit(event)

        async def suppress(evt, delivery_id):
            return StatusDeliveryResult.suppressed_rate_limit("only 1 quota left")

        dispatcher = ChannelVerboseDispatcher(send_verbose=suppress, state=state)
        dispatcher.submit(event)
        await dispatcher.close_and_drain(timeout=1.0)
        assert dispatcher.outcome == "suppressed_rate_limit"


class TestDispatcherGuardsAndLifecycle:
    @pytest.mark.asyncio
    async def test_duplicate_and_late_events_dropped(self):
        """容量 1 + state 注册校验：重复/未注册事件丢弃，每轮最多一条（回归 #8）。"""
        state = VerboseFeedbackState()
        first = _event("verbose_g1")
        assert state.try_emit(first) is True
        unregistered = _event("verbose_g2")  # 未 try_emit（迟到重复/伪造）

        async def fast_send(evt, delivery_id):
            return StatusDeliveryResult.sent()

        dispatcher = ChannelVerboseDispatcher(send_verbose=fast_send, state=state)
        assert dispatcher.submit(first) is True
        assert dispatcher.submit(first) is False, "重复事件（队列满）丢弃"
        assert dispatcher.submit(unregistered) is False, "非 state 本体事件丢弃"
        await dispatcher.close_and_drain(timeout=1.0)
        # 结果冻结后晚到事件不再改写
        assert dispatcher.submit(first) is False
        assert dispatcher.outcome == "sent"

    @pytest.mark.asyncio
    async def test_close_and_drain_cancels_inflight_send_and_awaits_it(self):
        """drain 超时必须 cancel 并 await 实际发送 task 后才返回（设计 §9.2 步骤 5）。"""
        state = VerboseFeedbackState()
        event = _event()
        state.try_emit(event)
        finished = asyncio.Event()
        cancelled = asyncio.Event()

        async def never_returning_send(evt, delivery_id):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            finally:
                finished.set()
            return StatusDeliveryResult.sent()

        dispatcher = ChannelVerboseDispatcher(
            send_verbose=never_returning_send, state=state, timeout_seconds=30.0
        )
        assert dispatcher.submit(event) is True
        # drain 超时 0.2s → cancel 在途发送并等待其真正结束
        await dispatcher.close_and_drain(timeout=0.2)
        assert cancelled.is_set(), "在途发送 task 必须被 cancel"
        assert finished.is_set(), "必须 await 实际发送 task 结束后才返回"
        assert dispatcher.outcome == "failed"
        assert dispatcher.worker_task is None

    @pytest.mark.asyncio
    async def test_cancel_and_await_leaves_no_task(self):
        """取消/异常路径 cancel_and_await 后无遗留 task（设计 §9.2 步骤 7）。"""
        state = VerboseFeedbackState()
        event = _event()
        state.try_emit(event)

        async def slow_send(evt, delivery_id):
            await asyncio.sleep(30)
            return StatusDeliveryResult.sent()

        dispatcher = ChannelVerboseDispatcher(send_verbose=slow_send, state=state)
        dispatcher.submit(event)
        await asyncio.sleep(0.01)
        await dispatcher.cancel_and_await()
        assert dispatcher.worker_task is None
        remaining = [
            t for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and "channel-verbose-dispatcher" in (t.get_name() or "")
        ]
        assert remaining == [], "任何路径不留 dispatcher task"

    @pytest.mark.asyncio
    async def test_submit_before_worker_started_then_drain_idle(self):
        """从未 submit（merged follower 路径）：drain 直接返回、不创建 task。"""
        state = VerboseFeedbackState()

        async def send(evt, delivery_id):
            return StatusDeliveryResult.sent()

        dispatcher = ChannelVerboseDispatcher(send_verbose=send, state=state)
        await dispatcher.close_and_drain()
        assert dispatcher.worker_task is None


# ============================================================
# metadata 冻结（设计 §10）
# ============================================================


class TestChannelVerboseMetadataEntries:
    def test_no_events_yields_no_entries(self):
        assert build_channel_verbose_metadata_entries([]) == []
        assert build_channel_verbose_metadata_entries(None) == []

    def test_entry_shape_and_dedup_by_event_id(self):
        first = _event("verbose_m1")
        duplicate = dict(first, data="重复帧")
        other = _event("verbose_m2")
        entries = build_channel_verbose_metadata_entries([first, duplicate, other])
        assert [e["eventId"] for e in entries] == ["verbose_m1", "verbose_m2"]
        for entry in entries:
            assert set(entry.keys()) == {"eventId", "data", "source", "timestamp", "delivery"}

    def test_delivery_falls_back_when_outcome_not_frozen(self):
        """dispatcher outcome 未冻结（未投递）时条目兜底为 suppressed_unsupported。"""
        event = _event()
        state = VerboseFeedbackState()
        state.try_emit(event)

        async def never_send(evt, delivery_id):
            return StatusDeliveryResult.sent()

        dispatcher = ChannelVerboseDispatcher(send_verbose=never_send, state=state)
        # 未 submit：outcome 为 None → 兜底 delivery
        entries = build_channel_verbose_metadata_entries([event], dispatcher)
        assert entries[0]["delivery"] == "suppressed_unsupported"

    @pytest.mark.asyncio
    async def test_failed_outcome_frozen_into_entry(self):
        event = _event()
        state = VerboseFeedbackState()
        state.try_emit(event)

        async def fail(evt, delivery_id):
            return StatusDeliveryResult.failed("nope")

        dispatcher = ChannelVerboseDispatcher(send_verbose=fail, state=state)
        dispatcher.submit(event)
        await asyncio.sleep(0.01)  # 让 worker 执行一次失败投递
        entries = build_channel_verbose_metadata_entries([event], dispatcher)
        assert entries[0]["delivery"] == "failed"


# ============================================================
# 配置优先级解析器（设计 §11，回归 #13）
# ============================================================


class TestResolveVerboseFeedbackConfig:
    def test_code_default_follows_global_default(self):
        """无渠道/旧配置覆盖时跟随全局默认（2026-09-01 起代码默认 enabled=true）。"""
        cfg = resolve_verbose_feedback_config(global_cfg=VerboseFeedbackConfig())
        assert cfg.effective_enabled is True
        assert cfg.max_per_turn == 1

    def test_global_disabled_still_honored_when_no_override(self):
        cfg = resolve_verbose_feedback_config(
            global_cfg=VerboseFeedbackConfig(enabled=False)
        )
        assert cfg.effective_enabled is False

    def test_global_enabled_used_when_no_override(self):
        cfg = resolve_verbose_feedback_config(
            global_cfg=VerboseFeedbackConfig(enabled=True)
        )
        assert cfg.effective_enabled is True

    def test_request_override_beats_channel_and_legacy(self):
        override = VerboseFeedbackConfig(enabled=True)
        cfg = resolve_verbose_feedback_config(
            request_override=override,
            channel_cfg={"enabled": True},
            legacy_waiting_indicator={"enabled": True, "delay_seconds": 15},
            global_cfg=VerboseFeedbackConfig(enabled=True),
        )
        assert cfg is override

    def test_channel_beats_legacy_and_global(self):
        """渠道级配置盖过旧配置与全局；delay 已无运行时含义（watchdog 删除），
        数值不再映射进运行时字段。"""
        cfg = resolve_verbose_feedback_config(
            channel_cfg={
                "enabled": True, "initial_delay_seconds": 6,
                "fallback_message": "渠道提示",
            },
            legacy_waiting_indicator={"enabled": True, "delay_seconds": 15, "message": "旧提示"},
            global_cfg=VerboseFeedbackConfig(enabled=True),
        )
        assert cfg.effective_enabled is True
        assert cfg.fallback_message == "渠道提示"
        # 未声明字段继承全局
        assert cfg.delivery_timeout_seconds == 5.0

    def test_invalid_delay_value_treated_as_enabled(self):
        """delay 非法值不再回退默认，直接视为启用（delay 已无运行时含义）。"""
        cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={"enabled": True, "delay_seconds": "abc"},
            global_cfg=VerboseFeedbackConfig(enabled=True),
        )
        assert cfg.effective_enabled is True

    def test_legacy_missing_delay_treated_as_enabled(self):
        cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={"enabled": True, "message": "旧提示"},
            global_cfg=VerboseFeedbackConfig(enabled=True),
        )
        assert cfg.effective_enabled is True
        assert cfg.fallback_message == "旧提示"

    def test_legacy_beats_global(self):
        cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={"enabled": True, "delay_seconds": 12, "message": "旧提示"},
            global_cfg=VerboseFeedbackConfig(enabled=True),
        )
        assert cfg.effective_enabled is True
        assert cfg.fallback_message == "旧提示"

    def test_channel_disabled_stays_disabled(self):
        """渠道显式 enabled=false 必须保持关闭，不得穿透到全局。

        2026-09-01 全局默认改为 enabled=true 后，若渠道显式关闭仍穿透全局，
        存量"显式关闭"租户会被全局开启（行为倒退）；显式关闭是渠道级决定，
        优先级高于全局默认。
        """
        cfg = resolve_verbose_feedback_config(
            channel_cfg={"enabled": False},
            global_cfg=VerboseFeedbackConfig(enabled=True),
        )
        assert cfg.effective_enabled is False

    def test_legacy_delay_zero_ignored_enabled(self):
        """watchdog 删除后 delay 一律忽略：开关开即启用，不再因 delay<=0 关闭。"""
        cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={"enabled": True, "delay_seconds": 0},
            global_cfg=VerboseFeedbackConfig(enabled=True),
        )
        assert cfg.effective_enabled is True

    def test_force_disabled_beats_everything(self):
        """force_disabled=true 覆盖请求、渠道与旧 waiting indicator（回归 #13）。"""
        cfg = resolve_verbose_feedback_config(
            request_override=VerboseFeedbackConfig(enabled=True),
            channel_cfg={"enabled": True, "initial_delay_seconds": 1},
            legacy_waiting_indicator={"enabled": True, "delay_seconds": 15, "message": "旧提示"},
            global_cfg=VerboseFeedbackConfig(enabled=True, force_disabled=True),
        )
        assert cfg.effective_enabled is False
        assert cfg.force_disabled is True

    def test_legacy_semantics_preserved(self):
        # watchdog 删除后 delay 一律忽略，开关开即启用
        cfg = resolve_verbose_feedback_config(
            legacy_waiting_indicator={"enabled": True, "delay_seconds": 0},
            global_cfg=VerboseFeedbackConfig(),
        )
        assert cfg.effective_enabled is True
        # 空白 message 回退旧默认话术
        cfg2 = resolve_verbose_feedback_config(
            legacy_waiting_indicator={"enabled": True, "delay_seconds": 10, "message": " "},
            global_cfg=VerboseFeedbackConfig(),
        )
        assert cfg2.fallback_message == "我正在处理您的问题，可能需要几分钟，请稍等下。"


# ============================================================
# ChannelFactory 注入渠道级 verbose_feedback（设计 §11）
# ============================================================


class TestFactoryVerboseFeedbackInjection:
    @pytest.mark.asyncio
    async def test_verbose_feedback_popped_from_config_and_set_on_adapter(self):
        """config.verbose_feedback 不进 adapter 构造器（wecom 构造器不接受未知 kwarg），
        由工厂统一挂到 adapter.verbose_feedback 属性。"""
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.saas.services import channel_factory as cf

        fake_adapter = MagicMock()
        fake_adapter.set_tenant_id = AsyncMock()
        created = {}

        def fake_create(channel_type, config):
            created["channel_type"] = channel_type
            created["config"] = config
            return fake_adapter

        with patch.object(cf.ChannelFactory, "create_adapter", side_effect=fake_create):
            adapter = await cf._build_adapter(
                "t1", "wecom",
                {
                    "corp_id": "c",
                    "verbose_feedback": {"enabled": True, "fallback_message": "渠道提示"},
                },
            )
        # 不进构造参数
        assert "verbose_feedback" not in created["config"]
        # 统一挂属性
        assert adapter.verbose_feedback == {"enabled": True, "fallback_message": "渠道提示"}

    @pytest.mark.asyncio
    async def test_no_verbose_feedback_key_keeps_config_intact(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.saas.services import channel_factory as cf

        fake_adapter = MagicMock()
        fake_adapter.set_tenant_id = AsyncMock()

        with patch.object(
            cf.ChannelFactory, "create_adapter",
            side_effect=lambda ct, cfg: fake_adapter,
        ):
            await cf._build_adapter("t1", "wecom", {"corp_id": "c2"})
        # 未配置时不注入属性（getattr 侧默认 None）
        assert getattr(fake_adapter, "verbose_feedback", None) is None or isinstance(
            fake_adapter.verbose_feedback, MagicMock
        )
