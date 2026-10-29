"""scheduled 终态统计钩子（hooks.py，设计 §5.4/§14-12）单测。

覆盖：
- 非 scheduled 来源/非 web 会话静默跳过；
- 任务行不存在（已删防御路径）：警告 + 正常返回（不阻断终态事务）；
- 统计回写成功路径（委托 _record_run_result_in_tx）；
- 回写零行更新/异常 → CheckpointFailure（终态事务整体回滚，评审修正）。
"""

from unittest.mock import MagicMock, patch

import pytest

from src.core.agent_engine.contracts import CheckpointFailure
from src.scheduler.hooks import record_scheduled_run_in_tx


def _runner(**overrides):
    row = {
        "runner_id": "runner_1", "session_id": "sess_t", "source": "scheduled",
        "session_kind": "web", "status": "completed", "user_id": "u1",
        "tenant_id": "tenant_1",
        "input": {"request_data": {"scheduled": {"task_id": "sched_1",
                                                  "trigger": "scheduled"}}},
        "result": {"status": "completed", "error_code": None},
        "finished_at": "2026-10-29 09:02:00",
    }
    row.update(overrides)
    return row


def _cursor(fetchone_rows):
    cursor = MagicMock()
    cursor.fetchone.side_effect = fetchone_rows
    return cursor


class TestRecordScheduledRunInTx:
    def test_non_scheduled_source_skipped(self):
        """chat 来源不进钩子（零 DB 调用）"""
        cursor = MagicMock()
        record_scheduled_run_in_tx(cursor, _runner(source="chat"))
        cursor.execute.assert_not_called()

    def test_non_web_session_skipped(self):
        cursor = MagicMock()
        record_scheduled_run_in_tx(cursor, _runner(session_kind="channel"))
        cursor.execute.assert_not_called()

    def test_missing_task_row_warns_but_returns(self):
        """任务行不存在（防御路径）：警告 + 正常返回——执行与计费事实保留，
        不因任务已删而阻断终态事务（claim 释放/历史写入继续）"""
        cursor = _cursor([None])  # scheduled_tasks 查无行
        record_scheduled_run_in_tx(cursor, _runner())  # 不抛
        # 只查了任务行，没走统计 UPDATE
        assert cursor.execute.call_count == 1

    def test_successful_recording(self):
        """正常路径：任务行命中 → _record_run_result_in_tx 被调（含正确参数）"""
        task_row = {"task_id": "sched_1", "tenant_id": "tenant_1",
                    "user_id": "u1", "name": "x", "schedule_type": "interval",
                    "notification_target_id": None}
        cursor = _cursor([task_row])
        with patch("src.scheduler.db.ScheduledTaskDB._record_run_result_in_tx",
                   return_value=True) as mock_record:
            record_scheduled_run_in_tx(cursor, _runner())
        mock_record.assert_called_once_with(
            cursor, "sched_1", run_status="completed",
            trigger="scheduled", error_code=None,
            finished_at="2026-10-29 09:02:00")

    def test_zero_row_update_raises_checkpoint_failure(self):
        """回写零行更新（绑定/条件错误）→ CheckpointFailure 整体回滚
        （评审修正：静默忽略会让终态事务提交但统计/once 消费/自动暂停全丢）"""
        task_row = {"task_id": "sched_1", "tenant_id": "tenant_1",
                    "user_id": "u1", "name": "x", "schedule_type": "interval",
                    "notification_target_id": None}
        cursor = _cursor([task_row])
        with patch("src.scheduler.db.ScheduledTaskDB._record_run_result_in_tx",
                   return_value=False):
            with pytest.raises(CheckpointFailure, match="SCHEDULED_RUN_RECORD_MISSED"):
                record_scheduled_run_in_tx(cursor, _runner())

    def test_recording_exception_raises_checkpoint_failure(self):
        task_row = {"task_id": "sched_1", "tenant_id": "tenant_1",
                    "user_id": "u1", "name": "x", "schedule_type": "interval",
                    "notification_target_id": None}
        cursor = _cursor([task_row])
        with patch("src.scheduler.db.ScheduledTaskDB._record_run_result_in_tx",
                   side_effect=RuntimeError("SQL 炸了")):
            with pytest.raises(CheckpointFailure, match="SCHEDULED_RUN_RECORD_FAILED"):
                record_scheduled_run_in_tx(cursor, _runner())
