"""企微计划入口的调度约束测试（§15 四轮评审 P1，2026-11-04，全 mock 无 DB）。

覆盖：
- cron 直传粒度：分钟级（'* * * * *'、'*/30 * * * *'）被拒，'0 * * * *'
  （每小时）合法——否则该入口可绕过整数小时最短间隔规则；
- 时间窗不丢失：time_config 带 start_at/ends_at 时随 create 落库；
- 每人活跃上限：create 传租户有效 max_active；达上限时ensure_daily_plan
  转换为用户可见的 ConversationServiceError（409），补录路径异常由调用方
  兜底记录（既有行为）。
"""

from unittest.mock import MagicMock, patch

import pytest

from src.wecom_conversation import plans
from src.wecom_conversation.service import ConversationServiceError


class TestCronGranularity:
    """到结束时间停止的前提是窗口能落库；分钟级 cron 则直接拒绝创建。"""

    @pytest.mark.parametrize("cron", ["* * * * *", "*/30 * * * *", "*/5 * * * *"])
    def test_minute_level_cron_rejected(self, cron):
        with pytest.raises(ValueError, match="粒度过细|至多每小时一次"):
            plans._schedule_fields({"schedule_type": "daily",
                                    "cron_expression": cron})

    def test_hourly_cron_accepted(self):
        fields = plans._schedule_fields({"schedule_type": "daily",
                                         "cron_expression": "0 * * * *"})
        assert fields["cron_expression"] == "0 * * * *"
        assert fields["start_at"] is None and fields["ends_at"] is None

    def test_daily_cron_still_accepted(self):
        fields = plans._schedule_fields({"schedule_type": "daily",
                                         "cron_expression": "0 9 * * *"})
        assert fields["next_due_at"] is not None


def _plan_kwargs():
    return dict(tenant_id="tenant_1", user_id="user_1",
                locators=["ws1:acc/session-1"], schedule={
                    "schedule_type": "daily",
                    "time_config": {"hour": 9, "minute": 0,
                                    "start_at": "2026-11-01 00:00:00",
                                    "ends_at": "2026-12-01 00:00:00"}})


def _mock_service_layers(monkeypatch):
    """隔离 wecom service 层（定位/登记），保证用例只验证调度约束。
    plans 内为函数级 `from . import service`——逐属性 patch 源模块。"""
    monkeypatch.setattr("src.wecom_conversation.service.valid_timezone",
                        lambda name: True)
    # 评审四：来源解析与候选计划演进后的 service 层隔离（定位/登记/候选）。
    monkeypatch.setattr(
        "src.wecom_conversation.service.conversations_for_locators",
        lambda *a, **k: [])
    monkeypatch.setattr(
        "src.wecom_conversation.service.candidate_plans_for_source",
        lambda *a, **k: [])
    monkeypatch.setattr("src.wecom_conversation.plans.resolve_runner_source_device",
                        lambda *a, **k: None)
    monkeypatch.setattr(
        "src.wecom_conversation.service.enroll_conversations_for_plan",
        lambda *a, **k: [("conv", True)])
    monkeypatch.setattr("src.wecom_conversation.adapters.decode_locator",
                        lambda item: ("acc", "session-1"))
    monkeypatch.setattr(plans, "_schedule_backfill_for", lambda *a, **k: None)


class TestDailyPlanConstraints:
    def test_window_and_quota_passed_to_create(self, monkeypatch):
        """窗口随 create 落库（不丢 start_at/ends_at）；max_active 用租户有效值。"""
        _mock_service_layers(monkeypatch)
        created = {}

        def fake_create(**kwargs):
            created.update(kwargs)
            return {"task_id": "sched_new", "status": "active"}

        with patch("src.scheduler.db.ScheduledTaskDB.create",
                   side_effect=fake_create), \
             patch("src.scheduler.schedule.max_tasks_per_user", return_value=7) as max_mock:
            result = plans.ensure_daily_plan(**_plan_kwargs())
        assert result["created"] is True
        from datetime import datetime
        assert created["start_at"] == datetime(2026, 11, 1)
        assert created["ends_at"] == datetime(2026, 12, 1)
        assert created["max_active"] == 7
        max_mock.assert_called_once_with("tenant_1")

    def test_quota_exceeded_raises_service_error(self, monkeypatch):
        """达上限：create 抛 QuotaError → 用户可见 409（不静默绕过配额）。"""
        from src.scheduler.db import ScheduledTaskQuotaError
        _mock_service_layers(monkeypatch)
        with patch("src.scheduler.db.ScheduledTaskDB.create",
                   side_effect=ScheduledTaskQuotaError("定时任务已达上限（7个）")), \
             patch("src.scheduler.schedule.max_tasks_per_user", return_value=7):
            with pytest.raises(ConversationServiceError) as error:
                plans.ensure_daily_plan(**_plan_kwargs())
        assert error.value.code == "TASK_QUOTA_EXCEEDED"
        assert error.value.status == 409
        assert "上限" in str(error.value)

    def test_backfill_once_uses_quota_too(self, monkeypatch):
        """首录补录（once）同样传租户上限——once 在执行前也是 active 占名额。"""
        _ensure_session = MagicMock()
        monkeypatch.setattr(plans, "_ensure_session_committed", _ensure_session)
        created = {}
        with patch("src.scheduler.db.ScheduledTaskDB.create",
                   side_effect=lambda **kw: created.update(kw) or {"task_id": "t"}), \
             patch("src.scheduler.schedule.max_tasks_per_user", return_value=5):
            plans.schedule_backfill_task(
                "tenant_1", "user_1",
                {"session_locator": "ws1:acc/s1", "timezone": "Asia/Shanghai",
                 "name": "客户会话", "id": 1}, device_id=None)
        assert created["max_active"] == 5
