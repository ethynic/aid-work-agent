"""Phase 2 评审修复回归（六项）：

1. 释放证明字段域：credit_cost 等展示字段不参与执行权证明（终态后心跳
   不误判 RUNNER_ATTEMPT_EXPIRED）；
2. scheduled 归属双重核对：属主转租户拒绝（platform_admin 跨租户放行）、
   会话行缺失/属主不一致拒绝；
3. 既有等待处理入口对 scheduled 开放（本地恢复投影 + verify_local 门）；
4. 补结算同步公开 credit_cost；
5. 事件读取复用事务外验证结果（事务内不重复认证）；
6. cancel 操作矩阵：scheduler 服务禁取消、用户放行、chat 不受限。
"""

import json
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services.agent_runner.contracts import RunnerError
from test_scheduled_authorization import (
    _SESSION, _TASK, _USER, ScheduledAuthStore, make_authorizer, make_config,
    scheduled_request)


# ==================== 1. 释放证明字段域（评审 P1） ====================


def _finalized_row(result, pending):
    from src.services.agent_runner.ownership import Attempt
    attempt = Attempt("run_1", "worker_1", 1)
    row = {
        "attempt": 1, "worker_id": None, "lease_until": None,
        "status": "completed",
        "checkpoint": {
            "released_attempt": {"attempt": 1, "worker_id": "worker_1",
                                 "status": "completed", "reason": "finalized"},
            "pending_finalization": pending,
        },
        "result": result,
    }
    return attempt, row


def test_released_by_accepts_result_with_credit_cost():
    """finalizer 写入 credit_cost 后，正常收尾的释放证明仍成立（心跳衔接）"""
    from src.services.agent_runner.execution_repository import released_by
    attempt, row = _finalized_row(
        {"status": "completed", "output": "done", "images": [], "error_code": None,
         "credit_cost": 1.25},
        {"status": "completed", "output": "done", "images": [], "error_code": None})
    assert released_by(row, attempt) is True


def test_released_by_still_rejects_changed_execution_fields():
    """字段域仍强制：稳定执行结果字段被改动即拒绝（展示字段不影响）"""
    from src.services.agent_runner.execution_repository import released_by
    attempt, row = _finalized_row(
        {"status": "completed", "output": "篡改后的输出", "images": [],
         "error_code": None, "credit_cost": 1.25},
        {"status": "completed", "output": "done", "images": [], "error_code": None})
    assert released_by(row, attempt) is False


# ==================== 2. 归属双重核对（评审 P1） ====================


def test_submit_rejects_owner_who_moved_to_another_tenant():
    """任务属主已转入其他租户：不得继续以旧租户身份执行/计费"""
    store = ScheduledAuthStore(user=dict(_USER, tenant_id="tenant_2"))
    authorizer = make_authorizer(store)
    with pytest.raises(RunnerError) as err:
        authorizer.authorize(scheduled_request(), "scheduler", "svc-token", execute=True)
    assert err.value.code == "TENANT_FORBIDDEN" and err.value.status == 403


def test_platform_admin_owner_cross_tenant_allowed():
    """platform_admin 属主沿用既有跨租户规则放行"""
    store = ScheduledAuthStore(
        user=dict(_USER, role="platform_admin", tenant_id=None))
    authorizer = make_authorizer(store)
    principal = authorizer.authorize(scheduled_request(), "scheduler", "svc-token",
                                      execute=True)
    assert principal.identity.tenant_id == "tenant_1"


def test_submit_rejects_missing_session_row():
    store = ScheduledAuthStore(session=None)
    authorizer = make_authorizer(store)
    with pytest.raises(RunnerError) as err:
        authorizer.authorize(scheduled_request(), "scheduler", "svc-token", execute=True)
    assert err.value.code == "SESSION_NOT_FOUND" and err.value.status == 404


def test_submit_rejects_session_owner_mismatch():
    store = ScheduledAuthStore(session=dict(_SESSION, user_id="u2"))
    authorizer = make_authorizer(store)
    with pytest.raises(RunnerError) as err:
        authorizer.authorize(scheduled_request(), "scheduler", "svc-token", execute=True)
    assert err.value.code == "SESSION_NOT_FOUND"


def test_submit_rejects_session_tenant_null():
    """会话租户为 NULL 不是通配：与 write_history 终态归属同口径拒绝（复核 P2）"""
    store = ScheduledAuthStore(session=dict(_SESSION, tenant_id=None))
    authorizer = make_authorizer(store)
    with pytest.raises(RunnerError) as err:
        authorizer.authorize(scheduled_request(), "scheduler", "svc-token", execute=True)
    assert err.value.code == "SESSION_NOT_FOUND" and err.value.status == 404


def test_submit_rejects_session_tenant_mismatch():
    store = ScheduledAuthStore(session=dict(_SESSION, tenant_id="tenant_2"))
    authorizer = make_authorizer(store)
    with pytest.raises(RunnerError) as err:
        authorizer.authorize(scheduled_request(), "scheduler", "svc-token", execute=True)
    assert err.value.code == "SESSION_NOT_FOUND"


# ==================== 3. 既有等待处理入口对 scheduled 开放（评审 P1） ====================


def _local_wait_row(source):
    node = {"tools": {"call": {"phase": "waiting"}}, "resources": {
        "local_invocations": {"key": {"tool_call_id": "call"}}}}
    return {"session_kind": "web", "source": source, "status": "interrupted",
            "worker_id": None, "lease_until": None,
            "checkpoint": {"execution": node}}


def test_local_recovery_applicable_accepts_scheduled():
    """本地恢复投影（设备核对动作可见性）对 scheduled 与 chat 同样适用"""
    from src.services.agent_runner.public_view import local_recovery_applicable
    assert local_recovery_applicable(_local_wait_row("scheduled"))
    assert local_recovery_applicable(_local_wait_row("chat"))
    assert not local_recovery_applicable(_local_wait_row("wecom_kf"))


def _recovery_runner_row(source, *, owned=True, cancel_requested=False):
    # owned=True：引用带全 execution_id/invocation_id（owned_invocation_ids 命中，
    # original_operation 形态）；owned=False：引用缺属主标识（pending 但 unconfirmed，
    # dispatch_unconfirmed 形态）
    invocation_ref = ({"tool_call_id": "call_1", "execution_id": "exec_1",
                       "invocation_id": "inv_1"} if owned
                      else {"tool_call_id": "call_1"})
    node = {
        "execution_id": "exec_1",
        "tools": {"call_1": {"phase": "waiting",
                             "call": {"id": "call_1", "name": "local_tool", "arguments": {}}}},
        "resources": {"local_invocations": {"key": invocation_ref}},
    }
    return {
        "runner_id": "run_1", "session_kind": "web", "session_id": "sess_task",
        "source": source, "status": "waiting", "settlement_status": "settled",
        "client_request_id": "c1", "profile_id": "main", "queue_order": 1,
        "revision": 1, "view_revision": 1, "control_revision": 1,
        "cancel_requested": cancel_requested, "pause_requested": False,
        "accepted_at": "2026-10-28", "updated_at": "2026-10-28",
        "public_snapshot": {"input": {"message_id": "m", "text": "t", "attachments": []},
                            "progress": None, "output": "", "images": []},
        "attempt": 1,
        "checkpoint": {"execution": node},
    }


def test_public_recovery_actions_match_matrix_for_scheduled():
    """公开恢复动作与真实入口权限一致（复核 P2）：scheduled 只宣称 verify/stop
    （cancel 走独立入口），resume/end 被矩阵与 end-waiting 门拒绝不得展示"""
    from src.services.agent_runner.public_view import public_runner
    assert public_runner(_recovery_runner_row("scheduled"))["local_recovery_actions"] == ["verify", "stop"]
    assert public_runner(_recovery_runner_row("scheduled",
                                              cancel_requested=True))["local_recovery_actions"] == ["verify", "stop"]
    assert public_runner(_recovery_runner_row("scheduled", owned=False))["local_recovery_actions"] == []
    # chat 行为不变
    assert public_runner(_recovery_runner_row("chat"))["local_recovery_actions"] == ["verify", "stop", "resume", "end"]


def test_verify_local_source_gate_passes_for_scheduled():
    """verify_local 不再对 scheduled 抛 LOCAL_VERIFICATION_FORBIDDEN——
    到达后续就绪/适用性判定（NOT_APPLICABLE）即证明 source 门已开放"""
    from src.services.agent_runner import local_verification

    def run(source):
        row = _local_wait_row(source)
        row["status"] = "waiting"
        row["runner_id"] = "run_1"
        row["tenant_id"] = "tenant_1"
        # 无 pending 本地调用：通过 source 门后落在适用性判定（NOT_APPLICABLE），
        # 证明门已开放即可，不推进到准入锁
        row["checkpoint"]["execution"]["tools"]["call"]["phase"] = "completed"
        repository = SimpleNamespace(
            connection_factory=_cursor_factory(),
            assert_owner=lambda principal, r: None)
        with patch.object(local_verification, "lock_runner", return_value=row):
            with pytest.raises(RunnerError) as err:
                local_verification.verify_local(
                    repository, SimpleNamespace(), "run_1", "req_1")
        return err.value.code

    # scheduled 通过 source 门，落在适用性判定；channel 仍被门拒绝
    assert run("scheduled") == "LOCAL_VERIFICATION_NOT_APPLICABLE"
    assert run("wecom_kf") == "LOCAL_VERIFICATION_FORBIDDEN"


class _FakeCursor:
    def execute(self, sql, params=()):
        raise AssertionError("不应触达后续存储 I/O: " + sql)

    def fetchone(self):
        return None


def _cursor_factory():
    @contextmanager
    def _factory():
        yield SimpleNamespace(cursor=lambda: _FakeCursor(), commit=lambda: None)
    return _factory


# ==================== 4. 补结算同步公开费用（评审 P2） ====================


def test_settle_pending_usage_updates_public_credit_cost():
    """迟到 receipt 补结算：公开 result.credit_cost 与权威累计费用同步"""
    from src.services.agent_runner import finalizer as finalizer_mod
    from src.services.agent_runner.finalizer import RunnerFinalizer

    @contextmanager
    def _factory():
        yield SimpleNamespace(cursor=lambda: _RecordingCursor(), commit=lambda: None)

    row = {"runner_id": "run_1", "status": "completed",
           "result": {"status": "completed", "credit_cost": 0.5},
           "checkpoint": {"pending_finalization": {"status": "completed"}}}
    finalizer = RunnerFinalizer(connection_factory=_factory)
    with patch.object(finalizer_mod, "lock_runner", return_value=row), \
            patch.object(finalizer_mod, "decoded", lambda value: value), \
            patch.object(RunnerFinalizer, "_settle",
                         return_value=("settled", Decimal("0.75"), Decimal("1.25"))), \
            patch.object(finalizer_mod.EventRepository, "notify_in_tx",
                         staticmethod(lambda cursor, before, after=None, **kw: after)), \
            patch.object(RunnerFinalizer, "_after_commit", staticmethod(lambda *a, **k: None)):
        finalizer.settle_pending_usage("run_1")

    update = _RecordingCursor.updates[-1]
    assert "result=%s::jsonb" in update[0]
    assert json.loads(update[1][1])["credit_cost"] == 1.25
    assert json.loads(update[1][1])["status"] == "completed"  # 其余结果字段保留


class _RecordingCursor:
    updates = []

    def execute(self, sql, params=()):
        if sql.strip().upper().startswith("UPDATE"):
            _RecordingCursor.updates.append((sql, params))

    def fetchone(self):
        return {"runner_id": "run_1", "status": "completed",
                "result": {"status": "completed", "credit_cost": 0.5}}


# ==================== 5. 读取免重复认证（评审 P2） ====================


def _scheduled_runner_row():
    return {
        "runner_id": "run_1", "session_kind": "web", "source": "scheduled",
        "scope_key": "tenant:tenant_1", "tenant_id": "tenant_1",
        "session_id": "sess_task", "user_id": "u1",
        "actor_kind": "system", "actor_id": "scheduler",
        "client_request_id": "sched_x1:2026-10-28T09:00:00",
        "input": {"text": "生成日报",
                  "session": {"kind": "web", "session_id": "sess_task"},
                  "source": "scheduled", "profile_id": "main",
                  "request_data": {"scheduled": {"task_id": "sched_x1",
                                                 "trigger": "scheduled"}}},
    }


def test_read_reuses_verified_user_id_without_reverification():
    """事件读取（事务外已验证）传入 verified_user_id：事务内不再调用认证依赖，
    只复核 tokens 权威行"""
    store = ScheduledAuthStore()
    calls = []

    def verify(token, auto_refresh=False):
        calls.append(token)
        return "u1"

    from src.services.agent_runner.authorization import RunnerAuthorizer
    authorizer = RunnerAuthorizer(make_config(), connection_factory=store.connection,
                                  token_verifier=verify)
    credentials = {"service_id": "web", "service_token": "svc-token",
                   "user_token": "tk-u1"}
    with store.connection() as conn:
        authorizer.authorize_read_in_tx(conn.cursor(), _scheduled_runner_row(),
                                        credentials, verified_user_id="u1")
    assert calls == []  # 事务内零次认证调用
    assert any("FROM tokens" in sql for sql in store.executed)  # 权威行仍复核

    # 未携带 verified_user_id 的常规路径仍完成认证
    store2 = ScheduledAuthStore()
    authorizer2 = RunnerAuthorizer(make_config(), connection_factory=store2.connection,
                                   token_verifier=verify)
    with store2.connection() as conn:
        authorizer2.authorize_read_in_tx(conn.cursor(), _scheduled_runner_row(),
                                         credentials)
    assert calls == ["tk-u1"]


# ==================== 6. cancel 操作矩阵（评审 P2） ====================


def test_cancel_matrix_scheduler_forbidden_user_allowed_chat_unaffected():
    """scheduled 行：scheduler 服务身份取消 403；用户 token 取消放行；
    chat 行不受矩阵影响"""
    from src.services.agent_runner.manager import RunnerManager

    def make_manager(source):
        manager = RunnerManager(repository=MagicMock(), authorizer=MagicMock(),
                                profiles=MagicMock())
        row = {"source": source, "runner_id": "run_1"}
        manager.repository.get.return_value = row
        manager.repository.cancel.return_value = row
        return manager, row

    principal = SimpleNamespace(actor_kind="system", actor_id="scheduler")
    service_creds = {"service_id": "scheduler", "service_token": "svc-token"}
    user_creds = {"service_id": "web", "service_token": "svc-token",
                  "user_token": "tk-u1"}

    manager, _ = make_manager("scheduled")
    with patch.object(manager, "_authorize_row", return_value=principal), \
            patch("src.services.agent_runner.manager.public_runner", lambda row: row):
        with pytest.raises(RunnerError) as err:
            manager.cancel("run_1", service_creds)
        assert err.value.code == "SCHEDULED_CONTROL_FORBIDDEN"
        assert err.value.status == 403
        manager.repository.cancel.assert_not_called()

        manager.cancel("run_1", user_creds)
        manager.repository.cancel.assert_called_once()

    manager_chat, _ = make_manager("chat")
    with patch.object(manager_chat, "_authorize_row", return_value=principal), \
            patch("src.services.agent_runner.manager.public_runner", lambda row: row):
        manager_chat.cancel("run_1", service_creds)  # chat 行不受矩阵限制
    manager_chat.repository.cancel.assert_called_once()
