"""scheduled 首次接单守卫单测（设计 §4.1/§4.3，Phase 2）。

覆盖 repository.submit_in_tx 内的守卫挂接（与 INSERT 同事务、先于其执行）：
- active + pending 匹配通过且守卫 SQL（FOR UPDATE）先于 INSERT；
- paused / pending 失配 / 任务缺失 → 409 SCHEDULED_TASK_NOT_ACCEPTABLE 且
  INSERT 未执行；
- 幂等命中（守卫失配但同键 runner 行已存在）跳过守卫拒绝，走 ON CONFLICT
  幂等路径返回原行；
- 非 scheduled 来源不触发守卫。
"""

import copy
from unittest.mock import patch

import pytest

from src.core.agent_engine.contracts import Identity
from src.services.agent_runner.contracts import Principal, RunnerError, RunnerSubmit, SessionRef
from src.services.agent_runner.repository import RunnerRepository


_OCCURRENCE = "2026-10-28T09:00:00"

_GUARD_TASK = {
    "task_id": "sched_x1", "tenant_id": "tenant_1", "user_id": "u1",
    "status": "active", "pending_occurrence_key": _OCCURRENCE,
}

_PRINCIPAL = Principal(Identity("tenant_1", "u1", "sess_task", "scheduled", "web"),
                       "system", "scheduler", "scheduler")


def scheduled_request():
    return RunnerSubmit(client_request_id="sched_x1:" + _OCCURRENCE, text="生成日报",
                        session=SessionRef(kind="web", session_id="sess_task"),
                        source="scheduled", profile_id="main")


class GuardCursor:
    """记录 SQL 执行顺序并按 SQL 文本路由 fetchone 结果的内存 cursor。"""

    def __init__(self, *, guard_task=_GUARD_TASK, inserted_row=None,
                 existing_runner=None):
        self.guard_task, self.inserted_row = guard_task, inserted_row
        self.existing_runner = existing_runner
        self.sql_log = []
        self._next = None

    def execute(self, sql, params=()):
        self.sql_log.append(sql)
        if "FROM scheduled_tasks" in sql:
            self._next = self.guard_task
        elif "INSERT INTO agent_runners" in sql:
            self._next = self.inserted_row
        elif "FROM agent_runners" in sql:
            self._next = self.existing_runner
        else:
            self._next = None

    def fetchone(self):
        return copy.deepcopy(self._next)


def _inserted_runner_row(request):
    return {
        "runner_id": "runner_new", "scope_key": "tenant:tenant_1", "session_kind": "web",
        "session_id": "sess_task", "actor_kind": "system", "actor_id": "scheduler",
        "user_id": "u1", "tenant_id": "tenant_1", "source": "scheduled",
        "client_request_id": request.client_request_id, "input_digest": request.digest(),
        "input": {}, "checkpoint": {}, "public_snapshot": {}, "result": None,
    }


# ==================== 守卫通过（首次接单） ====================


def test_guard_passes_and_precedes_insert():
    """active+pending 匹配：守卫 SQL 含 FOR UPDATE 且先于 INSERT 同事务执行。"""
    request = scheduled_request()
    cursor = GuardCursor(inserted_row=_inserted_runner_row(request))
    with patch("src.services.agent_runner.repository.EventRepository.notify_in_tx",
               side_effect=lambda cursor, before, **kwargs: kwargs["after"]):
        row, created = RunnerRepository().submit_in_tx(cursor, _PRINCIPAL, request, "fp")
    assert created is True and row["runner_id"] == "runner_new"
    guard_sql = [sql for sql in cursor.sql_log if "FROM scheduled_tasks" in sql]
    insert_indexes = [i for i, sql in enumerate(cursor.sql_log) if "INSERT INTO agent_runners" in sql]
    assert guard_sql and "FOR UPDATE" in guard_sql[0]
    assert insert_indexes and cursor.sql_log.index(guard_sql[0]) < insert_indexes[0]


def test_chat_source_skips_guard():
    """非 scheduled 提交不触发守卫（无 scheduled_tasks 查询）。"""
    request = RunnerSubmit(client_request_id="web-1", text="hi",
                           session=SessionRef(kind="web", session_id="sess_chat"), source="chat")
    principal = Principal(Identity("tenant_1", "u1", "sess_chat", "chat", "web"),
                          "user", "u1", "web")
    cursor = GuardCursor(inserted_row=_inserted_runner_row(request))
    with patch("src.services.agent_runner.repository.EventRepository.notify_in_tx",
               side_effect=lambda cursor, before, **kwargs: kwargs["after"]):
        _, created = RunnerRepository().submit_in_tx(cursor, principal, request, "fp")
    assert created is True
    assert not any("FROM scheduled_tasks" in sql for sql in cursor.sql_log)


# ==================== 守卫拒绝（409，INSERT 未执行） ====================


@pytest.mark.parametrize("guard_task,reason", [
    (dict(_GUARD_TASK, status="paused"), "not_active"),
    (dict(_GUARD_TASK, status="completed"), "not_active"),
    (dict(_GUARD_TASK, pending_occurrence_key="other-occurrence"), "pending_changed"),
    (dict(_GUARD_TASK, pending_occurrence_key=None), "pending_changed"),
    (None, "task_missing"),
], ids=["paused", "completed", "pending-replaced", "pending-cleared", "task-missing"])
def test_guard_rejection_blocks_insert(guard_task, reason):
    """暂停/认领替换/删除后的首次接单被拒：409 且 INSERT 从未执行（同事务串行化）。"""
    request = scheduled_request()
    cursor = GuardCursor(guard_task=guard_task, inserted_row=None, existing_runner=None)
    with pytest.raises(RunnerError) as error:
        RunnerRepository().submit_in_tx(cursor, _PRINCIPAL, request, "fp")
    assert error.value.code == "SCHEDULED_TASK_NOT_ACCEPTABLE" and error.value.status == 409
    assert not any("INSERT INTO agent_runners" in sql for sql in cursor.sql_log)


# ==================== 幂等命中跳过守卫 ====================


def test_idempotent_hit_skips_guard_rejection():
    """守卫失配但同键 runner 已存在：是幂等重查不是首次接单，返回原行不抛 409。"""
    request = scheduled_request()
    # 任务 pending 已清（接单成功后 clear_pending），守卫必然失配
    guard_task = dict(_GUARD_TASK, status="active", pending_occurrence_key=None)
    existing = _inserted_runner_row(request)
    existing["runner_id"] = "runner_original"
    # INSERT 走 ON CONFLICT DO NOTHING 冲突（fetchone=None），随后幂等 SELECT 返回原行
    cursor = GuardCursor(guard_task=guard_task, inserted_row=None, existing_runner=existing)
    row, created = RunnerRepository().submit_in_tx(cursor, _PRINCIPAL, request, "fp")
    assert created is False and row["runner_id"] == "runner_original"
