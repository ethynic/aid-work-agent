"""定时任务尽力投递测试：目标解析 -> 直投/补投 -> 入站合并补投

覆盖渠道场景（wecom_kf）定时提醒三层投递：
- resolve_channel_target：会话回查、渠道/租户校验（fail-closed）
- deliver_task_result：直投成功 / 失败登记 pending / 无目标跳过
- deliver_pending_for_channel_user：入站合并补投成功/失败/无记录
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scheduler import delivery
from src.scheduler.delivery import (
    build_delivery_content,
    deliver_pending_for_channel_user,
    deliver_task_result,
    resolve_channel_target,
)


def _make_task(**overrides):
    task = {
        "task_id": "task-1",
        "user_id": "user-1",
        "name": "电话提醒",
        "tenant_id": "tenant_a",
        "session_id": "tenant_tenant_a_wecom_kf_wxuser_sub",
    }
    task.update(overrides)
    return task


def _make_session(**overrides):
    session = {
        "session_id": "tenant_tenant_a_wecom_kf_wxuser_sub",
        "tenant_id": "tenant_a",
        "channel_type": "wecom_kf",
        "channel_chat_id": "wkABC",
        "channel_user_id": "wxuser",
    }
    session.update(overrides)
    return session


# ==================== resolve_channel_target ====================


class TestResolveChannelTarget:
    def test_wecom_kf_session_resolved(self):
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   return_value=_make_session()):
            target = resolve_channel_target(_make_task())
        assert target == {
            "tenant_id": "tenant_a",
            "channel_chat_id": "wkABC",
            "channel_user_id": "wxuser",
            "session_id": "tenant_tenant_a_wecom_kf_wxuser_sub",
        }

    def test_missing_session_returns_none(self):
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   return_value=None):
            assert resolve_channel_target(_make_task()) is None

    def test_non_wecom_kf_channel_returns_none(self):
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   return_value=_make_session(channel_type="wecom")):
            assert resolve_channel_target(_make_task()) is None

    def test_tenant_mismatch_rejected(self):
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   return_value=_make_session(tenant_id="tenant_b")):
            assert resolve_channel_target(_make_task()) is None

    def test_empty_session_id_returns_none(self):
        assert resolve_channel_target(_make_task(session_id="")) is None
        assert resolve_channel_target(_make_task(session_id=None)) is None

    def test_empty_session_tenant_falls_back_to_task_tenant(self):
        # 会话行租户为空（遗留数据）时用任务行租户兜底
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   return_value=_make_session(tenant_id="")):
            target = resolve_channel_target(_make_task())
        assert target is not None
        assert target["tenant_id"] == "tenant_a"

    def test_both_tenants_empty_rejected(self):
        # 双空租户无法定位渠道配置，fail-closed 拒绝（避免落不可达 pending）
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   return_value=_make_session(tenant_id="")):
            assert resolve_channel_target(_make_task(tenant_id="")) is None

    def test_lookup_exception_returns_none(self):
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   side_effect=RuntimeError("db down")):
            assert resolve_channel_target(_make_task()) is None


# ==================== build_delivery_content ====================


class TestBuildDeliveryContent:
    def test_normal_content(self):
        content = build_delivery_content(_make_task(name="电话提醒"), "记得打电话")
        assert content == "【定时提醒】电话提醒\n记得打电话"

    def test_empty_result_fallback(self):
        content = build_delivery_content(_make_task(), "  ")
        assert content == "【定时提醒】电话提醒\n（任务已完成，无输出内容）"

    def test_long_result_truncated(self):
        content = build_delivery_content(_make_task(), "x" * 2000)
        assert len(content) < 500


# ==================== deliver_task_result ====================


def _mock_factory(adapter):
    """patch ChannelFactory.create_from_tenant_config（函数内 import）"""
    return patch("src.saas.services.channel_factory.ChannelFactory.create_from_tenant_config",
                 new=AsyncMock(return_value=(adapter, "cfg_1", "sub")))


class TestDeliverTaskResult:
    @pytest.mark.asyncio
    async def test_direct_send_success_no_pending(self):
        adapter = MagicMock()
        adapter.send_text = AsyncMock(return_value=True)
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   return_value=_make_session()), \
             _mock_factory(adapter), \
             patch.object(delivery, "ScheduledTaskDeliveryDB") as mock_db:
            result = await deliver_task_result(_make_task(), "提醒内容")
        assert result == {"delivered": True, "reason": "sent"}
        adapter.send_text.assert_awaited_once_with(
            "【定时提醒】电话提醒\n提醒内容", "wxuser", open_kfid="wkABC")
        mock_db.create_pending.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_failure_creates_pending(self):
        adapter = MagicMock()
        adapter.send_text = AsyncMock(return_value=False)
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   return_value=_make_session()), \
             _mock_factory(adapter), \
             patch.object(delivery, "ScheduledTaskDeliveryDB") as mock_db:
            mock_db.create_pending.return_value = {"delivery_id": "sdlv_1"}
            result = await deliver_task_result(_make_task(), "提醒内容")
        assert result == {"delivered": False, "reason": "send_failed_pending"}
        mock_db.create_pending.assert_called_once()
        kwargs = mock_db.create_pending.call_args.kwargs
        assert kwargs["task_id"] == "task-1"
        assert kwargs["tenant_id"] == "tenant_a"
        assert kwargs["channel_type"] == "wecom_kf"
        assert kwargs["channel_chat_id"] == "wkABC"
        assert kwargs["channel_user_id"] == "wxuser"
        assert "电话提醒" in kwargs["content"]

    @pytest.mark.asyncio
    async def test_no_channel_target_skips(self):
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   return_value=None), \
             patch.object(delivery, "ScheduledTaskDeliveryDB") as mock_db:
            result = await deliver_task_result(_make_task(), "提醒内容")
        assert result == {"delivered": False, "reason": "no_channel_target"}
        mock_db.create_pending.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_exception_creates_pending(self):
        adapter = MagicMock()
        adapter.send_text = AsyncMock(side_effect=RuntimeError("api down"))
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   return_value=_make_session()), \
             _mock_factory(adapter), \
             patch.object(delivery, "ScheduledTaskDeliveryDB") as mock_db:
            mock_db.create_pending.return_value = {"delivery_id": "sdlv_1"}
            result = await deliver_task_result(_make_task(), "提醒内容")
        assert result == {"delivered": False, "reason": "send_failed_pending"}

    @pytest.mark.asyncio
    async def test_factory_no_adapter_creates_pending(self):
        with patch("src.channels.session.channel_session_manager.get_session_by_id",
                   return_value=_make_session()), \
             patch("src.saas.services.channel_factory.ChannelFactory.create_from_tenant_config",
                   new=AsyncMock(return_value=(None, None, None))), \
             patch.object(delivery, "ScheduledTaskDeliveryDB") as mock_db:
            mock_db.create_pending.return_value = {"delivery_id": "sdlv_1"}
            result = await deliver_task_result(_make_task(), "提醒内容")
        assert result == {"delivered": False, "reason": "send_failed_pending"}


# ==================== deliver_pending_for_channel_user ====================


class TestDeliverPendingForChannelUser:
    @pytest.mark.asyncio
    async def test_no_pending_no_send(self):
        adapter = MagicMock()
        adapter.send_text = AsyncMock(return_value=True)
        with patch.object(delivery, "ScheduledTaskDeliveryDB") as mock_db:
            mock_db.list_pending.return_value = []
            await deliver_pending_for_channel_user("tenant_a", "wkABC", "wxuser", adapter)
        adapter.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_pending_merged_and_marked_sent(self):
        rows = [
            {"delivery_id": "sdlv_1", "content": "【定时提醒】A\n内容A"},
            {"delivery_id": "sdlv_2", "content": "【定时提醒】B\n内容B"},
        ]
        adapter = MagicMock()
        adapter.send_text = AsyncMock(return_value=True)
        with patch.object(delivery, "ScheduledTaskDeliveryDB") as mock_db:
            mock_db.list_pending.return_value = rows
            await deliver_pending_for_channel_user("tenant_a", "wkABC", "wxuser", adapter)
        sent_text = adapter.send_text.await_args.args[0]
        assert "内容A" in sent_text and "内容B" in sent_text
        adapter.send_text.assert_awaited_once_with(sent_text, "wxuser")
        assert mock_db.mark_sent.call_count == 2
        mock_db.mark_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_failure_keeps_pending(self):
        rows = [{"delivery_id": "sdlv_1", "content": "【定时提醒】A\n内容A"}]
        adapter = MagicMock()
        adapter.send_text = AsyncMock(return_value=False)
        with patch.object(delivery, "ScheduledTaskDeliveryDB") as mock_db:
            mock_db.list_pending.return_value = rows
            await deliver_pending_for_channel_user("tenant_a", "wkABC", "wxuser", adapter)
        assert mock_db.mark_failed.call_count == 1
        mock_db.mark_sent.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_exception_keeps_pending(self):
        rows = [{"delivery_id": "sdlv_1", "content": "【定时提醒】A\n内容A"}]
        adapter = MagicMock()
        adapter.send_text = AsyncMock(side_effect=RuntimeError("quota"))
        with patch.object(delivery, "ScheduledTaskDeliveryDB") as mock_db:
            mock_db.list_pending.return_value = rows
            await deliver_pending_for_channel_user("tenant_a", "wkABC", "wxuser", adapter)
        mock_db.mark_failed.assert_called_once()
        args, kwargs = mock_db.mark_failed.call_args
        assert args[0] == "sdlv_1"
        assert "quota" in args[1]

    @pytest.mark.asyncio
    async def test_list_exception_swallows(self):
        adapter = MagicMock()
        with patch.object(delivery, "ScheduledTaskDeliveryDB") as mock_db:
            mock_db.list_pending.side_effect = RuntimeError("db down")
            await deliver_pending_for_channel_user("tenant_a", "wkABC", "wxuser", adapter)
        adapter.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_merged_text_capped(self):
        rows = [{"delivery_id": f"sdlv_{i}",
                 "content": "x" * 400} for i in range(10)]
        adapter = MagicMock()
        adapter.send_text = AsyncMock(return_value=True)
        with patch.object(delivery, "ScheduledTaskDeliveryDB") as mock_db:
            mock_db.list_pending.return_value = rows
            await deliver_pending_for_channel_user("tenant_a", "wkABC", "wxuser", adapter)
        sent_text = adapter.send_text.await_args.args[0]
        assert len(sent_text) <= 2000
