"""定时任务调度循环（dispatch）单元测试（全 mock，不触真实 DB）。

覆盖（plan-scheduled-task-runner-refactor.md Phase 3 + 评审修正）：
- 提交映射闭合表（设计 §4.1）：202 清 pending / 402→credit_blocked+同事务
  通知 / 403 类→tenant_unavailable / 422→definition_invalid / 409
  IDEMPOTENCY_*→submit_conflict / 未列举 4xx→submit_rejected / 网络未知与
  5xx→退避且 pending 保留；
- 通知键：提交拒绝透传 occurrence_key（评审 P1——暂停→恢复→同原因再次
  暂停不得被 ON CONFLICT 去重吞掉）；
- 迟到拒绝：pause_for_occurrence_with_notify 返回 False 不发通知；
- 快照重建：提交体与 pending_snapshot 一致（text/attachments/augmentations
  原样透传，client_request_id 拼接，request_data.trigger 取 pending_trigger）；
- 认领：list_due→claim_due 传非交互指令；认领成功即提交；单任务异常隔离；
- 恢复驱动（设计 §4.6）：控制键格式（runner/attempt/n）、rejected 计数
  递增换键、耗尽→pause_interrupted_with_notify（回合保护+同事务通知）、
  RESUME_PENDING/网络异常不计数且带退避、受理前 402/403 直接按原因暂停；
- DAL SQL 条件。
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.scheduler.db import ScheduledTaskDB
from src.scheduler.dispatch import (
    NON_INTERACTIVE_AUGMENTATION,
    DispatcherConfig,
    ScheduledTaskDispatcher,
    _pause_reason_for,
    _is_transient,
)
from src.services.agent_runner.contracts import RunnerError


def make_dispatcher(db=None, client=None, **config_kw):
    return ScheduledTaskDispatcher(
        client=client if client is not None else AsyncMock(),
        config=DispatcherConfig(poll_seconds=15, submit_retry_seconds=60,
                                recovery_max_attempts=3, **config_kw),
        db=db if db is not None else MagicMock(spec=ScheduledTaskDB),
    )


def pending_task(**kw):
    row = {
        "task_id": "sched_abc123",
        "session_id": "session_def456",
        "pending_occurrence_key": "2026-10-28T09:00:00",
        "pending_trigger": "scheduled",
        "pending_snapshot": {
            "text": "生成日报",
            "attachments": [{"file_id": "file_1", "name": "销售表"}],
            "profile_id": "data-analysis",
            "instance_id": None,
            "prompt_augmentations": [NON_INTERACTIVE_AUGMENTATION],
        },
    }
    row.update(kw)
    return row


def interrupted_row(**kw):
    row = {
        "runner_id": "runner_1",
        "session_id": "session_def456",
        "attempt": 2,
        "task_id": "sched_abc123",
        "tenant_id": "tenant_1",
        "user_id": "user_1",
        "name": "每日日报",
        "notification_target_id": None,
    }
    row.update(kw)
    return row


# ==================== 提交映射闭合表（设计 §4.1） ====================

class TestSubmitMapping:
    @pytest.mark.asyncio
    async def test_success_clears_pending_with_key_condition(self):
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = [pending_task()]
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        body = client.request.call_args.kwargs["body"]
        assert client.request.call_args.args == ("POST", "/v1/runners")
        assert body["source"] == "scheduled"
        # 202 成功：清 pending 必须带 occurrence_key 条件
        db.clear_pending.assert_called_once_with(
            "sched_abc123", "2026-10-28T09:00:00")
        db.pause_for_occurrence_with_notify.assert_not_called()

    @pytest.mark.parametrize("code,status,reason", [
        ("CREDIT_BLOCKED", 402, "credit_blocked"),
        ("PROFILE_FORBIDDEN", 403, "profile_forbidden"),
        ("TENANT_FORBIDDEN", 403, "tenant_unavailable"),
        ("TENANT_UNAVAILABLE", 403, "tenant_unavailable"),
        ("TENANT_EXPIRED", 403, "tenant_unavailable"),
        ("INVALID_REQUEST", 422, "definition_invalid"),
        ("IDEMPOTENCY_INPUT_MISMATCH", 409, "submit_conflict"),
        ("SOME_UNLISTED_REJECTION", 400, "submit_rejected"),
    ])
    @pytest.mark.asyncio
    async def test_deterministic_rejection_pauses_and_notifies(self, code, status, reason):
        """确定性拒绝：pause 与通知账本同一事务，occurrence_key 透传作事件幂等
        尾——每次 occurrence 的同原因暂停是独立通知（评审 P1）。"""
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = [pending_task()]
        db.pause_for_occurrence_with_notify.return_value = True
        client.request.side_effect = RunnerError(code, status)
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        db.pause_for_occurrence_with_notify.assert_called_once_with(
            "sched_abc123", "2026-10-28T09:00:00", reason,
            pytest.approx([]) if False else
            {"credit_blocked": "积分额度不足", "profile_forbidden": "数字员工不可用",
             "tenant_unavailable": "租户不可用或已过期", "definition_invalid":
             "任务定义校验未通过", "submit_conflict": "提交冲突（幂等键命中不同内容）",
             "submit_rejected": "提交被拒绝"}[reason],
            occurrence_notify_key="2026-10-28T09:00:00")
        db.clear_pending.assert_not_called()
        db.backoff_pending.assert_not_called()

    @pytest.mark.asyncio
    async def test_late_rejection_after_pause_resume_notifies_nothing(self):
        """迟到拒绝：用户已暂停→恢复出新 pending，pause_for_occurrence_with_notify
        返回 False——不得覆盖状态，不得发布通知（Phase 1 CR 修正）。"""
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = [pending_task()]
        db.pause_for_occurrence_with_notify.return_value = False
        db.clear_pending.return_value = False  # key 已换：条件清理 0 行命中
        client.request.side_effect = RunnerError("CREDIT_BLOCKED", 402)
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        db.pause_for_occurrence_with_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_stale_manual_pending_after_once_completed_is_cleared(self):
        """once 边界（评审 P2）：定时执行先完成置 completed，尚未受理的 manual
        pending 被接单守卫拒绝且任务已非 active——pause 必然 False，残留槽按
        key 条件清理，不再每轮重提。"""
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        manual = pending_task(pending_trigger="manual",
                              pending_occurrence_key="manual-uuid-1")
        # 第一轮返回待提交行；clear_pending 后第二轮不再返回（真实库行为）
        db.list_pending.side_effect = [[manual], []]
        db.pause_for_occurrence_with_notify.return_value = False  # 任务 completed
        db.clear_pending.return_value = True
        client.request.side_effect = RunnerError("SCHEDULED_TASK_NOT_ACCEPTABLE", 409)
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()
        await dispatcher.run_once()  # 二轮不再重提同一请求

        # 残留槽按 key 条件清理（不误伤新 pending：clear 带 occurrence_key 条件）
        db.clear_pending.assert_called_with("sched_abc123", "manual-uuid-1")
        assert client.request.await_count == 1  # 第二轮 list_pending 不再返回

    @pytest.mark.parametrize("error", [
        RunnerError("RUNNER_SERVICE_UNAVAILABLE", 503),
        RunnerError("RUNNER_GATEWAY_NOT_CONFIGURED", 503),
        RunnerError("RUNNER_SERVICE_INVALID_RESPONSE", 502),
        RunnerError("STORAGE_EXPLODED", 500),
        ConnectionError("network down"),
    ])
    @pytest.mark.asyncio
    async def test_transient_failure_backs_off_and_keeps_pending(self, error):
        """5xx/网络未知（含超时）：不当作确定未接单——退避重试，pending 保留。"""
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = [pending_task()]
        client.request.side_effect = error
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        db.backoff_pending.assert_called_once_with(
            "sched_abc123", "2026-10-28T09:00:00", 60)
        db.clear_pending.assert_not_called()
        db.pause_for_occurrence_with_notify.assert_not_called()

    def test_pause_reason_classification_is_closed(self):
        # 幂等键冲突（409）必须归 submit_conflict 而非通用拒绝
        assert _pause_reason_for(RunnerError("IDEMPOTENCY_INPUT_MISMATCH", 409)) == "submit_conflict"
        assert _pause_reason_for(RunnerError("IDEMPOTENCY_STILL_RUNNING", 409)) == "submit_conflict"
        # 未列举确定性 4xx 归 submit_rejected；5xx 非确定性
        assert _pause_reason_for(RunnerError("X_Y", 405)) == "submit_rejected"
        assert _pause_reason_for(RunnerError("X_Y", 500)) is None
        assert _is_transient(RunnerError("X_Y", 500)) is True
        assert _is_transient(RunnerError("X_Y", 400)) is False
        assert _is_transient(ValueError("boom")) is True


# ==================== 通知键（评审 P1：occurrence 透传） ====================

class TestPausedEventKey:
    def test_occurrence_key_makes_each_pause_distinct(self):
        from src.scheduler.notify import _event_key
        # 同原因两个不同 occurrence：键必须不同（否则恢复后再次同原因暂停被去重）
        k1 = _event_key("sched_1", "credit_blocked", occurrence_key="2026-10-28T09:00:00")
        k2 = _event_key("sched_1", "credit_blocked", occurrence_key="2026-10-29T09:00:00")
        assert k1 != k2
        assert k1 == "paused:sched_1:credit_blocked:2026-10-28T09:00:00"

    def test_recovery_key_includes_runner_and_attempt(self):
        from src.scheduler.notify import _event_key
        assert _event_key("sched_1", "interrupted", attempt=2,
                          runner_id="runner_1") == \
            "paused:sched_1:interrupted:attempt:runner_1:2"


# ==================== 快照重建（不可变提交意图） ====================

class TestSnapshotRebuild:
    @pytest.mark.asyncio
    async def test_body_rebuilt_verbatim_from_snapshot(self):
        client = AsyncMock()
        db = MagicMock(spec=ScheduledTaskDB)
        db.list_pending.return_value = [pending_task(pending_trigger="manual")]
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        body = client.request.call_args.kwargs["body"]
        snapshot = pending_task()["pending_snapshot"]
        # 重试原样重建，不重新生成
        assert body["text"] == snapshot["text"]
        assert body["attachments"] == snapshot["attachments"]
        assert body["profile_id"] == snapshot["profile_id"]
        assert body["instance_id"] == snapshot["instance_id"]
        assert body["prompt_augmentations"] == snapshot["prompt_augmentations"]
        # 幂等键拼接与触发原因
        assert body["client_request_id"] == "sched_abc123:2026-10-28T09:00:00"
        assert body["request_data"] == {"scheduled": {
            "task_id": "sched_abc123", "trigger": "manual"}}
        assert body["session"] == {"kind": "web", "session_id": "session_def456"}


# ==================== 认领路径 ====================

class TestClaimPath:
    @pytest.mark.asyncio
    async def test_claim_passes_non_interactive_augmentation_then_submits(self):
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        due = {"task_id": "sched_due1", "session_id": "session_due1"}
        claimed = pending_task(task_id="sched_due1", session_id="session_due1")
        db.list_pending.return_value = []
        db.list_interrupted.return_value = []
        db.list_due.return_value = [due]
        db.claim_due.return_value = claimed
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        # 认领传固定非交互指令（随快照固化，重试不重新生成）
        db.claim_due.assert_called_once_with(
            "sched_due1", prompt_augmentations=[NON_INTERACTIVE_AUGMENTATION])
        body = client.request.call_args.kwargs["body"]
        assert body["client_request_id"] == "sched_due1:2026-10-28T09:00:00"
        assert body["prompt_augmentations"] == [NON_INTERACTIVE_AUGMENTATION]
        db.clear_pending.assert_called_once_with("sched_due1", "2026-10-28T09:00:00")

    @pytest.mark.asyncio
    async def test_claim_lost_or_once_expired_skips_submit(self):
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = []
        db.list_interrupted.return_value = []
        db.list_due.return_value = [{"task_id": "sched_lost"}]
        db.claim_due.return_value = None  # 已被其他实例认领/暂停/once 作废
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        client.request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_bad_task_does_not_break_the_round(self):
        """单任务异常隔离：一条坏任务不中断整轮。"""
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = []
        db.list_interrupted.return_value = []
        db.list_due.return_value = [{"task_id": "sched_bad"}, {"task_id": "sched_good"}]
        db.claim_due.side_effect = [RuntimeError("bad row"), pending_task(
            task_id="sched_good", session_id="session_good")]
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()  # 不抛异常

        assert db.claim_due.call_count == 2
        client.request.assert_awaited_once()


# ==================== 恢复驱动（设计 §4.6） ====================

class TestResumeDrive:
    @pytest.mark.asyncio
    async def test_control_key_format_and_no_supplement_body(self):
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = []
        db.list_due.return_value = []
        db.list_interrupted.return_value = [interrupted_row()]
        db.count_rejected_resumes.return_value = 0
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        db.count_rejected_resumes.assert_any_call("runner_1", 2, None)  # 预算
        db.count_rejected_resumes.assert_any_call("runner_1", 2)  # 键序（总 rejected）
        path, body = client.request.call_args.args[1], client.request.call_args.kwargs["body"]
        assert path == "/v1/runners/runner_1/controls"
        # 回合键：attempt 区分中断回合，n 为本回合 rejected 计数
        assert body == {"action": "resume",
                        "client_request_id": "sched-resume:runner_1:2:0",
                        "answer": "", "attachments": []}
        db.pause_interrupted_with_notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejected_count_shifts_key_next_round(self):
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = []
        db.list_due.return_value = []
        db.list_interrupted.return_value = [interrupted_row()]
        db.count_rejected_resumes.return_value = 1  # 账本已落一条 rejected
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        body = client.request.call_args.kwargs["body"]
        assert body["client_request_id"] == "sched-resume:runner_1:2:1"

    @pytest.mark.asyncio
    async def test_attempts_exhausted_pauses_with_round_guard_and_notify(self):
        """耗尽：pause_interrupted_with_notify（回合保护+通知同事务），携带
        runner/attempt；旧回合决定在事务内失效时返回 False 零通知。"""
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = []
        db.list_due.return_value = []
        db.list_interrupted.return_value = [interrupted_row()]
        db.count_rejected_resumes.return_value = 3  # >= recovery_max_attempts
        db.pause_interrupted_with_notify.return_value = True
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        client.request.assert_not_awaited()
        db.pause_interrupted_with_notify.assert_called_once_with(
            "sched_abc123", "runner_1", 2, "执行中断且自动恢复失败")
        db.pause.assert_not_called()

    @pytest.mark.asyncio
    async def test_exhaustion_pause_race_does_not_notify(self):
        """回合保护失效（另一实例已恢复/attempt 已推进）：False，无第二次暂停。"""
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = []
        db.list_due.return_value = []
        db.list_interrupted.return_value = [interrupted_row()]
        db.count_rejected_resumes.return_value = 3
        db.pause_interrupted_with_notify.return_value = False
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        db.pause.assert_not_called()
        db.pause_interrupted_with_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_transient_resume_error_defers_recovery_on_task(self):
        """恢复退避（评审修正）：网络类错误登记任务级 recovery_retry_at
        （列表过滤即跳过，不改 runner 公开状态）。"""
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = []
        db.list_due.return_value = []
        db.list_interrupted.return_value = [interrupted_row()]
        db.count_rejected_resumes.return_value = 0
        client.request.side_effect = ConnectionError("down")
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        db.defer_recovery.assert_called_once_with("sched_abc123", 60)
        db.pause_interrupted_with_notify.assert_not_called()

    @pytest.mark.parametrize("error", [
        RunnerError("RUNNER_RESUME_PENDING", 409),   # 已有受理待消费，不是失败
        RunnerError("RUNNER_SERVICE_UNAVAILABLE", 503),  # 网络未知同键重试
    ])
    @pytest.mark.asyncio
    async def test_resume_pending_and_network_errors_do_not_count(self, error):
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = []
        db.list_due.return_value = []
        db.list_interrupted.return_value = [interrupted_row()]
        db.count_rejected_resumes.return_value = 0
        client.request.side_effect = error
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        # 不暂停、不通知、不换键（下轮同键重试）；带退避登记
        db.pause_interrupted_with_notify.assert_not_called()
        db.defer_recovery.assert_called_once_with("sched_abc123", 60)
        db.count_rejected_resumes.assert_any_call("runner_1", 2, None)  # 预算
        db.count_rejected_resumes.assert_any_call("runner_1", 2)  # 键序（总 rejected）

    @pytest.mark.parametrize("code,status,reason", [
        ("CREDIT_BLOCKED", 402, "credit_blocked"),
        ("PROFILE_FORBIDDEN", 403, "profile_forbidden"),
        ("TENANT_EXPIRED", 403, "tenant_unavailable"),
        ("UNLISTED_CONTROL_REJECT", 400, "interrupted"),
    ])
    @pytest.mark.asyncio
    async def test_pre_acceptance_deterministic_rejection_pauses(self, code, status, reason):
        """受理前确定性拒绝（无 rejected 落账）：按原因暂停+通知，不无限重复。"""
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = []
        db.list_due.return_value = []
        db.list_interrupted.return_value = [interrupted_row()]
        db.count_rejected_resumes.return_value = 0
        db.pause_interrupted_with_notify.return_value = True
        client.request.side_effect = RunnerError(code, status)
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        db.pause_interrupted_with_notify.assert_called_once_with(
            "sched_abc123", "runner_1", 2,
            {"credit_blocked": "积分额度不足", "profile_forbidden": "数字员工不可用",
             "tenant_unavailable": "租户不可用或已过期",
             "interrupted": "执行中断且自动恢复失败"}[reason],
            pause_reason=reason)

    @pytest.mark.asyncio
    async def test_state_self_resolved_errors_are_skipped(self):
        """runner 已离开 interrupted（自行恢复/终态/取消）：跳过等待下轮自然退出。"""
        db, client = MagicMock(spec=ScheduledTaskDB), AsyncMock()
        db.list_pending.return_value = []
        db.list_due.return_value = []
        db.list_interrupted.return_value = [interrupted_row()]
        db.count_rejected_resumes.return_value = 0
        client.request.side_effect = RunnerError("RUNNER_NOT_PARKED", 409)
        dispatcher = make_dispatcher(db=db, client=client)

        await dispatcher.run_once()

        db.pause_interrupted_with_notify.assert_not_called()


# ==================== DAL SQL 条件（list_pending/list_interrupted/计数） ====================

_UNSET = object()


def _dal_cursor(fetchone=_UNSET, fetchall=None, rowcount=1):
    cursor = MagicMock()
    if fetchone is not _UNSET:
        cursor.fetchone.side_effect = fetchone if isinstance(fetchone, list) else [fetchone]
    if fetchall is not None:
        cursor.fetchall.return_value = fetchall
    cursor.rowcount = rowcount
    return cursor


class TestDispatchDalSql:
    @patch("src.scheduler.db.get_db_connection")
    def test_list_pending_filters_by_retry_time(self, mock_conn):
        cursor = _dal_cursor(fetchall=[])
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        ScheduledTaskDB.list_pending("2026-10-28 10:00", limit=7)

        sql, params = cursor.execute.call_args.args
        assert "pending_occurrence_key IS NOT NULL" in sql
        assert "pending_retry_at <= %s" in sql
        assert params == ("2026-10-28 10:00", 7)

    @patch("src.scheduler.db.get_db_connection")
    def test_list_interrupted_excludes_only_recovery_stopped_states(self, mock_conn):
        """恢复门禁（评审 P2 修正）：用户主动 pause 的任务已受理中断执行仍恢复，
        仅排除调度侧主动停止恢复（paused+interrupted）与终态；cancel_requested/
        lease 未过期行排除。"""
        cursor = _dal_cursor(fetchall=[])
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        ScheduledTaskDB.list_interrupted(limit=5)

        sql, params = cursor.execute.call_args.args
        assert "JOIN scheduled_tasks t ON t.session_id = r.session_id" in sql
        assert "r.source = 'scheduled'" in sql
        assert "r.status = 'interrupted'" in sql
        assert "(r.lease_until IS NULL OR r.lease_until <= now())" in sql
        assert "r.cancel_requested = FALSE" in sql
        # 不再以 active 一刀切：仅排除恢复已停止（paused+interrupted）与终态
        # NULL 安全（评审复现：用户暂停 pause_reason=NULL 时 NOT(...) 为 NULL 被排除）
        assert "NOT (t.status = 'paused' AND t.pause_reason IS NOT DISTINCT FROM 'interrupted')" in sql
        assert "t.status NOT IN ('completed', 'expired')" in sql
        assert "(t.recovery_retry_at IS NULL OR t.recovery_retry_at <= now())" in sql
        assert params == (5,)

    @patch("src.scheduler.db.get_db_connection")
    def test_count_rejected_resumes_escapes_like_wildcards(self, mock_conn):
        """按键前缀统计本回合 rejected；runner_id 下划线转义不做通配。"""
        cursor = _dal_cursor(fetchone={"cnt": 2})
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor

        n = ScheduledTaskDB.count_rejected_resumes("runner_a%b_1", 3)

        assert n == 2
        sql, params = cursor.execute.call_args.args
        assert "action = 'resume'" in sql
        assert "status = 'rejected'" in sql
        assert "client_request_id LIKE %s" in sql
        assert params == ("runner_a%b_1", "sched-resume:runner\\_a\\%b\\_1:3:%")


# ==================== 恢复计数真实 SQL（Phase 4 三轮评审 Fix1） ====================

import sqlite3


class TestCountRejectedResumesRealSql:
    def _count(self, rows, since=None):
        """在 sqlite 真实执行生产 SQL（accepted_at 列 + since 过滤语义）。"""
        from unittest.mock import MagicMock, patch
        cursor = MagicMock()
        cursor.rowcount = 1
        with patch("src.scheduler.db.get_db_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor
            # runner_id 不含下划线：sqlite LIKE 无 PG 反斜杠转义语义，
            # 跨引擎保持同 pattern 匹配行为（PG 侧转义已有专项用例覆盖）
            ScheduledTaskDB.count_rejected_resumes(
                "runnerX1", 2, since)
        sql, params = cursor.execute.call_args.args
        conn = sqlite3.connect(":memory:")
        conn.execute("""CREATE TABLE agent_runner_controls (
            control_id TEXT PRIMARY KEY, runner_id TEXT, action TEXT,
            status TEXT, client_request_id TEXT,
            accepted_at TEXT, consumed_at TEXT)""")
        for i, (status, accepted_at) in enumerate(rows):
            conn.execute(
                "INSERT INTO agent_runner_controls VALUES (?,?,?,?,?,?,?)",
                (f"c{i}", "runnerX1", "resume", status,
                 f"sched-resume:runnerX1:2:{i}", accepted_at, None))
        # %s→?，LIKE 前缀的 ESCAPE 语法 sqlite 兼容（\ 默认）
        sqlite_sql = sql.replace("%s", "?")
        cursor2 = conn.execute(sqlite_sql, params)
        count = cursor2.fetchone()[0]
        conn.close()
        return count

    def test_since_filter_counts_only_after_reset(self):
        """用户恢复后（since）：只统计其后的 rejected——旧 rejected 不计预算
        但保留在账本（键序不过滤）"""
        rows = [
            ("rejected", "2026-10-28 08:00"),  # 旧 rejected（恢复前）
            ("rejected", "2026-10-28 09:00"),  # 旧 rejected（恢复前）
            ("rejected", "2026-10-28 12:00"),  # 新一轮 rejected（恢复后）
        ]
        # since=10:00 → 只计 12:00 那条 → 预算=1
        assert self._count(rows, since="2026-10-28 10:00") == 1
        # 不过滤 → 键序=3（永不回退，幂等键不撞旧控制）
        assert self._count(rows) == 3

    def test_sql_uses_accepted_at_not_created_at(self):
        """生产 SQL 必须用控制账本实际时间列 accepted_at（评审 P1：
        created_at 列不存在，SQL 错误致恢复控制无法提交）"""
        from unittest.mock import MagicMock, patch
        cursor = MagicMock()
        with patch("src.scheduler.db.get_db_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor
            ScheduledTaskDB.count_rejected_resumes("runner_1", 2, since="2026-01-01")
        sql, _ = cursor.execute.call_args.args
        assert "accepted_at >= %s" in sql
        assert "created_at" not in sql
