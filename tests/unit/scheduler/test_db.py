"""定时任务 v2 DAL（ScheduledTaskDB）单元测试（全 mock，不触真实 DB）

沿用旧 scheduler 单测的 mock 模式：patch src.scheduler.db.get_db_connection，
用 MagicMock cursor 模拟 fetchone/fetchall/rowcount，断言 SQL 关键条件与参数。

覆盖（plan-scheduled-task-runner-refactor.md Phase 1）：
- claim_due：原子认领（WHERE 全条件 + pending_snapshot 参数）；不满足返回 None
- submit_manual：不触碰 next_due_at；pending 占用时拒绝
- clear_pending / backoff_pending：必须带 occurrence_key 条件
- pause：清 pending 字段 + pause_reason 落库 + 归属条件进 UPDATE
- record_run_result：once+scheduled 置 completed；manual 不置；recurring+
  error_code 置 paused+reason；纯成功只累统计
- delete_with_session：非终态 runner 抛 ScheduledTaskBusyError 且不删除；
  正常路径三表删除顺序 + 单次 commit
- create：同事务插入任务会话（metadata 含 scheduled_task 标记）与任务行
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.scheduler.db import ScheduledTaskDB, ScheduledTaskBusyError


_UNSET = object()


def _make_cursor(fetchone=_UNSET, fetchall=None, rowcount=1):
    cursor = MagicMock()
    if fetchone is not _UNSET:
        cursor.fetchone.side_effect = (
            fetchone if isinstance(fetchone, list) else [fetchone]
        )
    if fetchall is not None:
        cursor.fetchall.return_value = fetchall
    cursor.rowcount = rowcount
    return cursor


def _bind_conn(mock_get_conn, cursor):
    """get_db_connection() 作为上下文管理器返回 conn，conn.cursor() 返回 cursor"""
    mock_get_conn.return_value.__enter__.return_value.cursor.return_value = cursor


_TASK_ROW = {
    "task_id": "sched_abc123",
    "tenant_id": "tenant_1",
    "user_id": "user_1",
    "session_id": "session_def456",
    "schedule_type": "daily",
    "status": "active",
}


# ==================== claim_due ====================

_DUE = datetime(2026, 10, 28, 9, 0, 0)

_TASK_ROW_FULL = dict(
    _TASK_ROW,
    task_prompt="生成日报（当前定义）",
    attachments=[{"file_id": "file_1", "name": "销售表"}],
    profile_id="data-analysis",
    instance_id=None,
    schedule_type="interval",
    interval_seconds=3600,
    next_due_at=_DUE,
)


class TestClaimDue:
    @patch("src.scheduler.db.get_db_connection")
    def test_claim_locks_row_and_derives_all_params_from_current_row(self, mock_conn):
        """认领单事务：条件 SELECT FOR UPDATE → occurrence/推进/快照全部派生自锁定行"""
        updated_row = dict(_TASK_ROW_FULL, pending_occurrence_key=_DUE.isoformat())
        cursor = _make_cursor(fetchone=[_TASK_ROW_FULL, updated_row])
        _bind_conn(mock_conn, cursor)

        result = ScheduledTaskDB.claim_due(
            "sched_abc123", prompt_augmentations=["非交互指令"],
            now=datetime(2026, 10, 28, 10, 30))

        assert result is not None
        executed = cursor.execute.call_args_list
        select_sql = executed[0].args[0]
        # SELECT 即校验（DB 时钟）+ 行锁：与编辑/改期/暂停/删除串行化
        assert "FOR UPDATE" in select_sql
        assert "status = 'active'" in select_sql
        assert "next_due_at <= now()" in select_sql
        assert "pending_occurrence_key IS NULL" in select_sql
        update_sql, params = cursor.execute.call_args[0]
        assert "pending_trigger = 'scheduled'" in update_sql
        assert "pending_snapshot = %s::jsonb" in update_sql
        # occurrence = 锁定行的 next_due_at；推进 = 锚点 09:00 + 3600s 序列中
        # 大于 now(10:30) 的最小值 11:00（interval 相位不漂移）
        assert params[0] == datetime(2026, 10, 28, 11, 0, 0)
        assert params[1] == _DUE.isoformat()
        # 快照从锁定的当前行构建（含非交互指令），调用方不带快照
        snapshot = json.loads(params[2])
        assert snapshot == {
            "text": "生成日报（当前定义）",
            "attachments": [{"file_id": "file_1", "name": "销售表"}],
            "profile_id": "data-analysis",
            "instance_id": None,
            "prompt_augmentations": ["非交互指令"],
        }
        assert params[3] == "sched_abc123"
        mock_conn.return_value.__enter__.return_value.commit.assert_called_once()

    @patch("src.scheduler.db.get_db_connection")
    def test_claim_params_come_from_locked_row_after_reschedule(self, mock_conn):
        """回归（评审 P1）：扫描后发生改期——锁定行已是新调度，旧候选不得覆盖。

        调度器扫描时看到 due=09:00/每小时；用户改期为 09:30/每半小时；
        认领锁定的当前行是 09:30/1800s——occurrence 与推进必须按新调度派生
        （09:30 锚点 + 1800s 序列中大于 now(10:00) 的最小值 10:30）。"""
        rescheduled_row = dict(
            _TASK_ROW_FULL, interval_seconds=1800,
            next_due_at=datetime(2026, 10, 28, 9, 30, 0))
        updated_row = dict(rescheduled_row,
                           pending_occurrence_key=rescheduled_row["next_due_at"].isoformat())
        cursor = _make_cursor(fetchone=[rescheduled_row, updated_row])
        _bind_conn(mock_conn, cursor)

        result = ScheduledTaskDB.claim_due(
            "sched_abc123", now=datetime(2026, 10, 28, 10, 0))

        assert result is not None
        _, params = cursor.execute.call_args[0]
        assert params[0] == datetime(2026, 10, 28, 10, 30, 0)  # 新节奏推进
        assert params[1] == "2026-10-28T09:30:00"              # 新 occurrence

    @patch("src.scheduler.db.get_db_connection")
    def test_claim_snapshot_reflects_edit_before_claim(self, mock_conn):
        """回归（评审 P1）：候选读取后、认领前发生编辑——快照必须是编辑后的定义"""
        edited_row = dict(_TASK_ROW_FULL, task_prompt="新任务定义（编辑后）",
                          attachments=[])
        updated_row = dict(edited_row, pending_occurrence_key=_DUE.isoformat())
        cursor = _make_cursor(fetchone=[edited_row, updated_row])
        _bind_conn(mock_conn, cursor)

        result = ScheduledTaskDB.claim_due(
            "sched_abc123", now=datetime(2026, 10, 28, 10, 30))

        assert result is not None
        snapshot = json.loads(cursor.execute.call_args_list[1].args[1][2])
        assert snapshot["text"] == "新任务定义（编辑后）"
        assert snapshot["attachments"] == []

    @patch("src.scheduler.db.get_db_connection")
    def test_claim_once_past_grace_expires_without_claiming(self, mock_conn):
        """once 已过执行宽限：置 expired 不认领（20261026-1530 修复语义）"""
        once_row = dict(_TASK_ROW_FULL, schedule_type="once",
                        interval_seconds=None, run_at=datetime(2026, 10, 28, 8, 0),
                        next_due_at=datetime(2026, 10, 28, 8, 0))
        cursor = _make_cursor(fetchone=once_row, rowcount=1)
        _bind_conn(mock_conn, cursor)

        result = ScheduledTaskDB.claim_due(
            "sched_abc123", now=datetime(2026, 10, 28, 19, 0))

        assert result is None
        executed = cursor.execute.call_args_list
        assert len(executed) == 2  # 锁行 + 作废 UPDATE，无认领 UPDATE
        expired_sql, expired_params = executed[1].args
        assert "status = 'expired'" in expired_sql
        assert "status = 'active'" in expired_sql
        assert expired_params == ("sched_abc123",)
        mock_conn.return_value.__enter__.return_value.commit.assert_called_once()

    @patch("src.scheduler.db.get_db_connection")
    def test_claim_once_within_grace_claims_with_null_advance(self, mock_conn):
        """once 宽限内：正常认领，推进为 NULL（终态钩子收尾 completed）"""
        once_row = dict(_TASK_ROW_FULL, schedule_type="once",
                        interval_seconds=None, run_at=datetime(2026, 10, 28, 10, 25),
                        next_due_at=datetime(2026, 10, 28, 10, 25))
        updated_row = dict(once_row,
                           pending_occurrence_key=once_row["next_due_at"].isoformat())
        cursor = _make_cursor(fetchone=[once_row, updated_row])
        _bind_conn(mock_conn, cursor)

        result = ScheduledTaskDB.claim_due(
            "sched_abc123", now=datetime(2026, 10, 28, 10, 30))

        assert result is not None
        _, params = cursor.execute.call_args[0]
        assert params[0] is None  # once 无下次
        assert params[1] == "2026-10-28T10:25:00"

    @patch("src.scheduler.db.get_db_connection")
    def test_claim_returns_none_when_condition_unmet(self, mock_conn):
        """条件不满足（并发已认领/已暂停/未到期/pending 占用）：SELECT 0 行即返回"""
        cursor = _make_cursor(fetchone=None)
        _bind_conn(mock_conn, cursor)

        result = ScheduledTaskDB.claim_due("sched_abc123")

        assert result is None
        # 仅执行了条件 SELECT，无 UPDATE、无 commit
        assert cursor.execute.call_count == 1
        mock_conn.return_value.__enter__.return_value.commit.assert_not_called()


# ==================== submit_manual ====================

class TestSubmitManual:
    @patch("src.scheduler.db.get_db_connection")
    def test_manual_slot_does_not_touch_next_due_at(self, mock_conn):
        """manual pending 槽：锁行固化当前定义快照，SET 不出现 next_due_at"""
        updated_row = dict(_TASK_ROW_FULL, pending_trigger="manual")
        cursor = _make_cursor(fetchone=[_TASK_ROW_FULL, updated_row])
        _bind_conn(mock_conn, cursor)

        result = ScheduledTaskDB.submit_manual(
            "sched_abc123", occurrence_key="m-uuid-1")

        assert result is not None
        executed = cursor.execute.call_args_list
        select_sql = executed[0].args[0]
        assert "FOR UPDATE" in select_sql
        assert "status = 'active'" in select_sql
        assert "pending_occurrence_key IS NULL" in select_sql
        update_sql, params = cursor.execute.call_args[0]
        assert "pending_trigger = 'manual'" in update_sql
        set_clause = update_sql.split("WHERE")[0]
        assert "next_due_at" not in set_clause
        assert params[0] == "m-uuid-1"
        # 快照同样取自锁定的当前行
        assert json.loads(params[1])["text"] == "生成日报（当前定义）"

    @patch("src.scheduler.db.get_db_connection")
    def test_manual_rejected_when_pending_occupied(self, mock_conn):
        """pending 已被占用（SELECT 0 行）：返回 None，不产生任何 UPDATE"""
        cursor = _make_cursor(fetchone=None)
        _bind_conn(mock_conn, cursor)

        assert ScheduledTaskDB.submit_manual("sched_abc123", occurrence_key="m2") is None
        assert cursor.execute.call_count == 1
        mock_conn.return_value.__enter__.return_value.commit.assert_not_called()


# ==================== clear_pending / backoff_pending ====================

class TestPendingGuards:
    @patch("src.scheduler.db.get_db_connection")
    def test_clear_pending_carries_occurrence_key(self, mock_conn):
        """clear 必须带 occurrence_key 条件：迟到响应不得清掉新 pending"""
        cursor = _make_cursor(rowcount=1)
        _bind_conn(mock_conn, cursor)

        assert ScheduledTaskDB.clear_pending("sched_abc123", "occ-1") is True
        sql, params = cursor.execute.call_args[0]
        assert "pending_occurrence_key IS NULL" not in sql.split("WHERE")[0]
        assert "pending_occurrence_key = NULL" in sql
        assert "WHERE task_id = %s AND pending_occurrence_key = %s" in sql
        assert params == ("sched_abc123", "occ-1")

    @patch("src.scheduler.db.get_db_connection")
    def test_clear_pending_no_match_returns_false(self, mock_conn):
        cursor = _make_cursor(rowcount=0)
        _bind_conn(mock_conn, cursor)

        assert ScheduledTaskDB.clear_pending("sched_abc123", "stale") is False

    @patch("src.scheduler.db.get_db_connection")
    def test_backoff_pending_carries_occurrence_key(self, mock_conn):
        """backoff 仅延长本次 pending 重试时间，同样带 key 条件"""
        cursor = _make_cursor(rowcount=1)
        _bind_conn(mock_conn, cursor)

        assert ScheduledTaskDB.backoff_pending("sched_abc123", "occ-1", 60) is True
        sql, params = cursor.execute.call_args[0]
        assert "now() + %s * interval '1 second'" in sql
        assert "WHERE task_id = %s AND pending_occurrence_key = %s" in sql
        assert params == (60, "sched_abc123", "occ-1")


# ==================== pause / pause_for_occurrence ====================

class TestPause:
    @patch("src.scheduler.db.get_db_connection")
    def test_pause_clears_pending_and_writes_reason(self, mock_conn):
        """用户暂停：置 paused + pause_reason 落库 + 清空 pending 槽"""
        cursor = _make_cursor(rowcount=1)
        _bind_conn(mock_conn, cursor)

        ok = ScheduledTaskDB.pause(
            "sched_abc123", tenant_id="tenant_1", user_id="user_1",
            pause_reason="credit_blocked")

        assert ok is True
        sql, params = cursor.execute.call_args[0]
        assert "status = 'paused'" in sql
        assert "pause_reason = %s" in sql
        for field in ("pending_occurrence_key", "pending_trigger",
                      "pending_retry_at", "pending_snapshot"):
            assert f"{field} = NULL" in sql
        # 仅 active 可暂停（终态不得经暂停/恢复复活）+ 归属条件进 UPDATE
        assert "status = 'active'" in sql
        assert "tenant_id = %s" in sql and "user_id = %s" in sql
        assert params == ("credit_blocked", "sched_abc123", "tenant_1", "user_1")


class TestPauseForOccurrence:
    @patch("src.scheduler.notify.publish_task_paused_in_tx")
    @patch("src.scheduler.db.get_db_connection")
    def test_auto_pause_matches_current_occurrence_and_notifies_in_tx(self, mock_conn,
                                                                      mock_publish):
        """提交拒绝触发的自动暂停：occurrence 仍是当前 pending 时暂停并清空；
        通知与 pause 同事务（评审修正：分两事务会永久丢通知）。"""
        task_row = {"task_id": "sched_abc123", "tenant_id": "tenant_1",
                    "user_id": "user_1", "name": "日报", "notification_target_id": None}
        cursor = _make_cursor(fetchone=[task_row], rowcount=1)
        _bind_conn(mock_conn, cursor)

        ok = ScheduledTaskDB.pause_for_occurrence_with_notify(
            "sched_abc123", "occ-1", "credit_blocked", "积分额度不足",
            occurrence_notify_key="occ-1")

        assert ok is True
        update_sql, update_params = cursor.execute.call_args_list[0].args
        assert "status = 'paused'" in update_sql
        # 三重条件：active + 该 occurrence 仍是当前 pending
        assert "status = 'active'" in update_sql
        assert "pending_occurrence_key = %s" in update_sql
        assert update_params == ("credit_blocked", "sched_abc123", "occ-1")
        # 同事务通知（RETURNING 行直传，通知键含 occurrence）
        mock_publish.assert_called_once()
        assert mock_publish.call_args.args[2] == "积分额度不足"
        assert mock_publish.call_args.kwargs["pause_reason"] == "credit_blocked"
        assert mock_publish.call_args.kwargs["occurrence_key"] == "occ-1"

    @patch("src.scheduler.notify.publish_task_paused_in_tx")
    @patch("src.scheduler.db.get_db_connection")
    def test_stale_rejection_does_not_touch_new_pending(self, mock_conn, mock_publish):
        """回归（评审 P1）：迟到拒绝（用户已暂停→恢复→新 pending）不得覆盖新状态

        WHERE 带 occurrence_key 条件，旧 occurrence 失配时 0 行命中返回 False，
        不写通知（同事务回滚路径不存在）。"""
        cursor = _make_cursor(fetchone=None)
        _bind_conn(mock_conn, cursor)

        ok = ScheduledTaskDB.pause_for_occurrence_with_notify(
            "sched_abc123", "stale-occ", "credit_blocked", "积分额度不足")

        assert ok is False
        sql, _ = cursor.execute.call_args_list[0].args
        assert "WHERE task_id = %s" in sql
        assert "AND status = 'active'" in sql
        assert "AND pending_occurrence_key = %s" in sql
        mock_publish.assert_not_called()


class TestPauseInterruptedWithNotify:
    @patch("src.scheduler.notify.publish_task_paused_in_tx")
    @patch("src.scheduler.db.get_db_connection")
    def test_round_guard_checks_runner_attempt_before_pause(self, mock_conn, mock_publish):
        """恢复耗尽/拒绝的回合保护（评审 P1）：事务内复核 runner 仍处于本扫描
        所见 (runner_id, attempt) interrupted 态——过期决定不修改状态不通知。"""
        task_row = {"task_id": "sched_abc123", "tenant_id": "tenant_1",
                    "user_id": "user_1", "name": "日报", "notification_target_id": None}
        # 锁定任务行 → runner 复核 0 行（已恢复/attempt 推进）→ 不暂停
        cursor = _make_cursor(fetchone=[task_row, None])
        _bind_conn(mock_conn, cursor)

        ok = ScheduledTaskDB.pause_interrupted_with_notify(
            "sched_abc123", "runner_1", 2, "执行中断且自动恢复失败")

        assert ok is False
        executed = [c.args[0] for c in cursor.execute.call_args_list]
        # 锁序：先 runner（FOR UPDATE 稳定回合）再任务行——与终态链路同向
        assert "FOR UPDATE" in executed[0]
        assert "agent_runners" in executed[0]
        assert "attempt = %s" in executed[0]
        assert "status = 'interrupted'" in executed[0]
        assert "cancel_requested = FALSE" in executed[0]
        assert not any(sql.lstrip().startswith("UPDATE scheduled_tasks") for sql in executed)
        mock_publish.assert_not_called()

    @patch("src.scheduler.notify.publish_task_paused_in_tx")
    @patch("src.scheduler.db.get_db_connection")
    def test_round_guard_hit_pauses_and_notifies_in_tx(self, mock_conn, mock_publish):
        cursor = _make_cursor(fetchone=[
            {"ok": 1},
            {"task_id": "sched_abc123", "tenant_id": "tenant_1", "user_id": "user_1",
             "name": "日报", "notification_target_id": None}])
        _bind_conn(mock_conn, cursor)

        ok = ScheduledTaskDB.pause_interrupted_with_notify(
            "sched_abc123", "runner_1", 2, "执行中断且自动恢复失败")

        assert ok is True
        executed = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("status = 'paused'" in sql for sql in executed)
        mock_publish.assert_called_once()
        assert mock_publish.call_args.kwargs["attempt"] == 2
        assert mock_publish.call_args.kwargs["runner_id"] == "runner_1"


class TestResume:
    @patch("src.scheduler.db.get_db_connection")
    def test_resume_only_from_paused(self, mock_conn):
        """恢复仅限 paused 来源（评审 P2）：completed/expired 终态不可复活，
        once 已消费额度不得经 pause→resume 重新进入调度"""
        cursor = _make_cursor(rowcount=1)
        _bind_conn(mock_conn, cursor)

        ok = ScheduledTaskDB.resume(
            "sched_abc123", next_due_at="2026-10-29 09:00:00",
            tenant_id="tenant_1", user_id="user_1")

        assert ok is True
        sql, params = cursor.execute.call_args[0]
        assert "status = 'paused'" in sql
        assert "pause_reason = NULL" in sql
        assert "next_due_at = %s" in sql
        assert params == ("2026-10-29 09:00:00", "sched_abc123", "tenant_1", "user_1")


# ==================== record_run_result ====================

class TestRecordRunResult:
    def _sql_and_params(self, cursor):
        return cursor.execute.call_args[0]

    @patch("src.scheduler.db.get_db_connection")
    def test_once_scheduled_terminal_consumes_once(self, mock_conn):
        """once + scheduled 终态：SQL 的 once 消费子句以 trigger='scheduled' 激活"""
        cursor = _make_cursor(rowcount=1)
        _bind_conn(mock_conn, cursor)

        assert ScheduledTaskDB.record_run_result(
            "sched_abc123", run_status="completed", trigger="scheduled") is True
        sql, params = self._sql_and_params(cursor)
        # 仅 scheduled 触发消费 once（manual 终态不置 completed）
        assert "WHEN %s = 'scheduled' AND schedule_type = 'once' THEN 'completed'" in sql
        # 统计回写：total_runs 累加 + success/fail 二选一
        assert "total_runs = total_runs + 1" in sql
        assert "last_status = %s" in sql
        assert params[0] == "completed"  # Runner 成功终态计成功
        assert params[4] == "scheduled"

    @patch("src.scheduler.db.get_db_connection")
    def test_once_manual_terminal_does_not_consume(self, mock_conn):
        """once + manual 终态：trigger='manual' 不满足消费子句，仅计统计"""
        cursor = _make_cursor(rowcount=1)
        _bind_conn(mock_conn, cursor)

        assert ScheduledTaskDB.record_run_result(
            "sched_abc123", run_status="completed", trigger="manual") is True
        sql, params = self._sql_and_params(cursor)
        assert params[4] == "manual"

    @pytest.mark.parametrize("error_code,reason", [
        ("CREDIT_BLOCKED", "credit_blocked"),
        ("PROFILE_FORBIDDEN", "profile_forbidden"),
    ])
    @patch("src.scheduler.db.get_db_connection")
    def test_recurring_error_pauses_with_reason(self, mock_conn, error_code, reason):
        """recurring 执行侧 CREDIT_BLOCKED/PROFILE_FORBIDDEN：置 paused+对应 reason"""
        cursor = _make_cursor(rowcount=1)
        _bind_conn(mock_conn, cursor)

        assert ScheduledTaskDB.record_run_result(
            "sched_abc123", run_status="failed", trigger="scheduled",
            error_code=error_code) is True
        sql, params = self._sql_and_params(cursor)
        assert "THEN 'paused'" in sql
        assert params[0] == "failed" and params[1] == "failed"
        assert params[5] == error_code   # status CASE 的 error_code
        assert list(params[6:10]) == [error_code] * 4  # 四个 pending 清理 CASE
        assert params[10] == error_code  # pause_reason CASE 的 IN 值
        assert params[11] == reason      # 映射后的 pause_reason 值
        assert params[12] == "sched_abc123"  # WHERE task_id（此前错绑 error_code）

    @patch("src.scheduler.db.get_db_connection")
    def test_completed_counts_success_failed_counts_fail(self, mock_conn):
        """Runner 状态口径（评审 P1）：completed 计成功、failed 计失败——
        两个 CASE 子句分别绑定同一 run_status 参数，不再有 'success' 旧口径"""
        cursor = _make_cursor(rowcount=1)
        _bind_conn(mock_conn, cursor)

        assert ScheduledTaskDB.record_run_result(
            "sched_abc123", run_status="completed", trigger="scheduled") is True
        sql, params = self._sql_and_params(cursor)
        assert ("success_count = success_count + CASE WHEN %s = 'completed' THEN 1 ELSE 0 END"
                in sql)
        assert ("fail_count = fail_count + CASE WHEN %s = 'failed' THEN 1 ELSE 0 END"
                in sql)
        # 旧口径必须彻底移除
        assert "'success'" not in sql
        assert params[0] == params[1] == "completed"

    @patch("src.scheduler.db.get_db_connection")
    def test_cancelled_counts_neither(self, mock_conn):
        """cancelled 口径：既不计成功也不计失败（两个 CASE 均不命中），
        仅 total_runs 与 last_status 呈现"""
        cursor = _make_cursor(rowcount=1)
        _bind_conn(mock_conn, cursor)

        assert ScheduledTaskDB.record_run_result(
            "sched_abc123", run_status="cancelled", trigger="scheduled") is True
        sql, params = self._sql_and_params(cursor)
        assert "WHEN %s = 'completed'" in sql
        assert "WHEN %s = 'failed'" in sql
        assert params[0] == params[1] == "cancelled"

    @patch("src.scheduler.db.get_db_connection")
    def test_plain_success_only_accumulates_stats(self, mock_conn):
        """纯成功（无 error_code）：SQL 不含 pause 置位激活路径，reason 参数为 None"""
        cursor = _make_cursor(rowcount=1)
        _bind_conn(mock_conn, cursor)

        assert ScheduledTaskDB.record_run_result(
            "sched_abc123", run_status="completed", trigger="scheduled") is True
        sql, params = self._sql_and_params(cursor)
        # 无 error_code：IN 列表参数为 None（不命中），pause_reason 写入值为 None
        assert params[5] is None and params[6] is None and params[7] is None
        assert ("success_count = success_count + CASE WHEN %s = 'completed' THEN 1 ELSE 0 END"
                in sql)


# ==================== delete_with_session ====================

class TestDeleteWithSession:
    @patch("src.scheduler.db.get_db_connection")
    def test_busy_runner_raises_and_deletes_nothing(self, mock_conn):
        """任务会话存在非终态 runner：抛 ScheduledTaskBusyError，不执行任何 DELETE"""
        runner_row = {"runner_id": "run_1"}
        cursor = _make_cursor(fetchone=[dict(_TASK_ROW), runner_row])
        _bind_conn(mock_conn, cursor)

        with pytest.raises(ScheduledTaskBusyError, match="任务正在执行"):
            ScheduledTaskDB.delete_with_session(
                "sched_abc123", tenant_id="tenant_1", user_id="user_1")

        executed = [c.args[0] for c in cursor.execute.call_args_list]
        assert len(executed) == 2  # 锁行 + 查 runner，之后即抛出
        assert "FOR UPDATE" in executed[0]
        assert "agent_runners" in executed[1]
        assert not any(sql.lstrip().upper().startswith("DELETE") for sql in executed)
        conn = mock_conn.return_value.__enter__.return_value
        conn.commit.assert_not_called()

    @patch("src.scheduler.db.get_db_connection")
    def test_normal_path_deletes_in_order_and_commits_once(self, mock_conn):
        """正常路径：锁行 → 查 runner（无）→ 删消息 → 删会话 → 删任务 → 单次 commit"""
        cursor = _make_cursor(fetchone=[dict(_TASK_ROW), None])
        _bind_conn(mock_conn, cursor)

        task = ScheduledTaskDB.delete_with_session(
            "sched_abc123", tenant_id="tenant_1", user_id="user_1")

        assert task is not None and task["task_id"] == "sched_abc123"
        executed = [c.args[0] for c in cursor.execute.call_args_list]
        assert len(executed) == 5
        assert "FOR UPDATE" in executed[0]
        assert "agent_runners" in executed[1]
        assert "DELETE FROM chat_messages" in executed[2]
        assert "DELETE FROM chat_sessions" in executed[3]
        assert "DELETE FROM scheduled_tasks" in executed[4]
        # 删除的消息/会话必须限定任务专属会话
        assert cursor.execute.call_args_list[2].args[1] == ("session_def456",)
        assert cursor.execute.call_args_list[3].args[1] == ("session_def456",)
        conn = mock_conn.return_value.__enter__.return_value
        conn.commit.assert_called_once()

    @patch("src.scheduler.db.get_db_connection")
    def test_task_not_found_returns_none(self, mock_conn):
        """归属不匹配（越权/不存在）：返回 None，不触发任何写"""
        cursor = _make_cursor(fetchone=None)
        _bind_conn(mock_conn, cursor)

        assert ScheduledTaskDB.delete_with_session(
            "sched_ghost", tenant_id="tenant_1", user_id="user_1") is None
        conn = mock_conn.return_value.__enter__.return_value
        conn.commit.assert_not_called()


# ==================== create ====================

class TestCreate:
    @patch("src.scheduler.db.get_db_connection")
    def test_create_inserts_session_and_task_in_one_tx(self, mock_conn):
        """同事务：先插任务专属会话（metadata 含 scheduled_task 标记），再插任务行"""
        created = dict(_TASK_ROW, name="日报", task_prompt="生成日报")
        cursor = _make_cursor(fetchone=created)
        _bind_conn(mock_conn, cursor)

        row = ScheduledTaskDB.create(
            user_id="user_1", tenant_id="tenant_1", name="日报",
            task_prompt="生成日报", schedule_type="daily",
            cron_expression="0 9 * * *", subagent_id="sub_x",
            instance_id="inst_1", next_due_at="2026-10-29 09:00:00")

        assert row["task_id"] == "sched_abc123"
        calls = cursor.execute.call_args_list
        assert len(calls) == 3  # 插会话 + 插任务 + 回读
        # 1) 任务专属会话：title=任务名，metadata 带 scheduled_task 标记
        session_sql, session_params = calls[0].args
        assert "INSERT INTO chat_sessions" in session_sql
        session_id = session_params[0]
        assert session_id.startswith("session_")
        metadata = json.loads(session_params[6])
        assert set(metadata.keys()) == {"scheduled_task"}
        assert metadata["scheduled_task"].startswith("sched_")
        assert session_params[1] == "user_1"       # user_id
        assert session_params[2] == "tenant_1"     # tenant_id
        assert session_params[3] == "sub_x"        # subagent_id
        assert session_params[4] == "inst_1"       # instance_id
        assert session_params[5] == "日报"          # title
        # 2) 任务行：task_id sched_ 前缀，session_id 与会话行一致，初始 active
        task_sql, task_params = calls[1].args
        assert "INSERT INTO scheduled_tasks" in task_sql
        assert task_params[0].startswith("sched_")
        assert task_params[0] == metadata["scheduled_task"]
        assert task_params[12] == session_id
        assert "'active'" in task_sql
        assert task_params[4] == "生成日报"
        # 单事务：一次 commit
        conn = mock_conn.return_value.__enter__.return_value
        conn.commit.assert_called_once()


# ==================== list_due / get_by_id 归属条件 ====================

class TestQueryConditions:
    @patch("src.scheduler.db.get_db_connection")
    def test_list_due_filters_active_due_and_idle(self, mock_conn):
        """到期扫描三条件：active + next_due_at 到期 + 无 pending 占用"""
        cursor = _make_cursor(fetchall=[dict(_TASK_ROW)])
        _bind_conn(mock_conn, cursor)

        rows = ScheduledTaskDB.list_due("2026-10-28 09:00:00")

        assert len(rows) == 1
        sql, params = cursor.execute.call_args[0]
        assert "status = 'active'" in sql
        assert "next_due_at <= %s" in sql
        assert "pending_occurrence_key IS NULL" in sql
        assert "ORDER BY next_due_at" in sql
        assert params[0] == "2026-10-28 09:00:00"

    @patch("src.scheduler.db.get_db_connection")
    def test_get_by_id_with_owner_conditions(self, mock_conn):
        """归属条件进 WHERE：越权行查不到（fail-closed）"""
        cursor = _make_cursor(fetchone=dict(_TASK_ROW))
        _bind_conn(mock_conn, cursor)

        row = ScheduledTaskDB.get_by_id(
            "sched_abc123", tenant_id="tenant_1", user_id="user_1")

        assert row["task_id"] == "sched_abc123"
        sql, params = cursor.execute.call_args[0]
        assert "task_id = %s" in sql
        assert "tenant_id = %s" in sql and "user_id = %s" in sql
        assert params == ("sched_abc123", "tenant_1", "user_1")
