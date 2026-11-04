"""首次接单守卫 lock_for_acceptance_in_tx 单测（设计 §4.1/§4.3，Phase 2）。

覆盖 scheduled_tasks 行锁复核：SQL 含 FOR UPDATE + task_id 参数化、
active+pending 匹配返回任务行、三种失配原因（task_missing/not_active/
pending_changed）抛 ScheduledTaskNotAcceptable。
"""

from unittest.mock import MagicMock

import pytest

from src.scheduler.db import ScheduledTaskNotAcceptable, lock_for_acceptance_in_tx


_OCCURRENCE = "2026-10-28T09:00:00"

_ROW = {
    "task_id": "sched_x1", "tenant_id": "tenant_1", "user_id": "u1",
    "status": "active", "pending_occurrence_key": _OCCURRENCE,
}


def test_guard_sql_locks_row_with_for_update():
    cursor = MagicMock()
    cursor.fetchone.return_value = dict(_ROW)
    task = lock_for_acceptance_in_tx(cursor, "sched_x1", _OCCURRENCE)
    assert task["status"] == "active"
    cursor.execute.assert_called_once()
    sql, params = cursor.execute.call_args[0]
    assert "FOR UPDATE" in sql and "task_id = %s" in sql
    assert "status" in sql and "pending_occurrence_key" in sql
    assert params == ("sched_x1",)


def test_guard_passes_on_active_and_pending_match():
    cursor = MagicMock()
    cursor.fetchone.return_value = dict(_ROW, pending_occurrence_key="manual-uuid")
    task = lock_for_acceptance_in_tx(cursor, "sched_x1", "manual-uuid")
    assert task["task_id"] == "sched_x1" and task["tenant_id"] == "tenant_1"


@pytest.mark.parametrize("row,reason", [
    (None, "task_missing"),
    (dict(_ROW, status="paused"), "not_active"),
    (dict(_ROW, status="completed"), "not_active"),
    (dict(_ROW, status="expired"), "not_active"),
    (dict(_ROW, pending_occurrence_key="other-occurrence"), "pending_changed"),
    (dict(_ROW, pending_occurrence_key=None), "pending_changed"),
], ids=["missing", "paused", "completed", "expired", "pending-replaced", "pending-cleared"])
def test_guard_failures_carry_reason(row, reason):
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    with pytest.raises(ScheduledTaskNotAcceptable) as error:
        lock_for_acceptance_in_tx(cursor, "sched_x1", _OCCURRENCE)
    assert error.value.reason == reason
    assert isinstance(error.value, RuntimeError)


# ==================== 时间窗自动完成行的 manual 接单（设计 §15.2.4） ====================

def test_guard_passes_on_window_completed_recurring_row():
    """因时间窗自动完成的循环任务（completed + 非 once + 设有 ends_at）：
    manual 触发的接单放行（不受时间窗限制）。"""
    cursor = MagicMock()
    cursor.fetchone.return_value = dict(
        _ROW, status="completed", schedule_type="interval",
        ends_at="2026-12-01 00:00:00", pending_occurrence_key="manual-uuid")
    task = lock_for_acceptance_in_tx(cursor, "sched_x1", "manual-uuid")
    assert task["status"] == "completed"


def test_guard_rejects_completed_once_row():
    """once 的 completed 行为不变：终态不可接单（额度已消费）。"""
    cursor = MagicMock()
    cursor.fetchone.return_value = dict(
        _ROW, status="completed", schedule_type="once",
        ends_at=None, pending_occurrence_key=_OCCURRENCE)
    with pytest.raises(ScheduledTaskNotAcceptable) as error:
        lock_for_acceptance_in_tx(cursor, "sched_x1", _OCCURRENCE)
    assert error.value.reason == "not_active"


def test_guard_rejects_completed_without_window():
    """无时间窗的 completed 行（非窗口收尾来源）不放行 manual 接单。"""
    cursor = MagicMock()
    cursor.fetchone.return_value = dict(
        _ROW, status="completed", schedule_type="interval",
        ends_at=None, pending_occurrence_key=_OCCURRENCE)
    with pytest.raises(ScheduledTaskNotAcceptable) as error:
        lock_for_acceptance_in_tx(cursor, "sched_x1", _OCCURRENCE)
    assert error.value.reason == "not_active"
