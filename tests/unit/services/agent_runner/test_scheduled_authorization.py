"""scheduled 来源三语义鉴权单测（设计 §5.2，Phase 2）。

全 mock DB（内存 connection + SQL 路由 cursor），不触真实库：
- 提交（scheduler 服务身份，execute=True）：构造 system Principal；伪造服务 401；
  非 peers 来源 403；用户失效 401；任务行缺失 404；profile 订阅校验（非 main）；
- 幂等重查（服务身份，execute=False）：任务 paused 放行（不要求 active）；
- authorize_persisted：任务 paused 已受理执行可继续（execute=True/False 均放行）；
- 用户 Bearer token：只读放行且要求属主匹配；提交拒绝 403；非属主 404；
- chat 提交任务会话拒绝 TASK_SESSION_CHAT_FORBIDDEN；
- 幂等键格式缺段 422；携带渠道 actor 字段 400。
"""

import copy
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.config.settings import AgentRunnerConfig
from src.core.agent_engine.contracts import Identity
from src.services.agent_runner.authorization import RunnerAuthorizer
from src.services.agent_runner.contracts import RunnerError, RunnerSubmit, SessionQuery, SessionRef


# _expired 对 naive datetime 用 datetime.now()（本地）比较，构造未过期 token
_EXPIRES_SOON = datetime.now() + timedelta(hours=1)

_TASK = {
    "task_id": "sched_x1",
    "tenant_id": "tenant_1",
    "user_id": "u1",
    "session_id": "sess_task",
    "status": "active",
    "pending_occurrence_key": "2026-10-28T09:00:00",
}

_USER = {"user_id": "u1", "tenant_id": "tenant_1", "role": "employee", "status": "active"}

_TOKEN = {"user_id": "u1", "expires_at": _EXPIRES_SOON}

_SESSION = {"session_id": "sess_task", "tenant_id": "tenant_1", "user_id": "u1",
            "instance_id": None, "metadata": None}

_TENANT = {"status": "active", "expire_at": None}


class ScheduledAuthStore:
    """SQL 文本路由的最小内存 connection（覆盖鉴权全部查询形状）。"""

    def __init__(self, *, task=_TASK, user=_USER, token=_TOKEN, session=_SESSION,
                 tenant=_TENANT):
        self.task, self.user, self.token = task, user, token
        self.session, self.tenant = session, tenant
        self.executed = []

    @contextmanager
    def connection(self):
        store = self

        class Cursor:
            def execute(self, sql, params=()):
                store.executed.append(sql)
                if "FROM scheduled_tasks" in sql:
                    self.row = store.task
                elif "FROM users" in sql:
                    self.row = store.user
                elif "FROM tokens" in sql:
                    self.row = store.token
                elif "FROM tenants" in sql:
                    self.row = store.tenant
                elif "FROM chat_sessions" in sql:
                    self.row = store.session
                elif "FROM subscriptions" in sql:
                    self.row = {"ok": 1}
                elif "FROM tenant_agent_instances" in sql:
                    self.row = None
                elif "FROM user_agent_permissions" in sql:
                    self.row = {"ok": 1}
                else:
                    raise AssertionError("Unexpected database I/O: " + sql)

            def fetchone(self):
                return copy.deepcopy(self.row)

        yield SimpleNamespace(cursor=lambda: Cursor())


@contextmanager
def store_connection(store):
    """直接借用 store 的 cursor（authorize_read_in_tx 行级读取路径用）。"""
    with store.connection() as conn:
        yield conn.cursor()


def make_config(*, web_scheduled=True):
    web_sources = ["chat", "wecom_kf", "feishu", "dingtalk", "wecom"]
    if web_scheduled:
        web_sources.append("scheduled")
    return AgentRunnerConfig(
        web_service_id="web", web_service_token="svc-token",
        peers={"web": {"sources": web_sources}, "scheduler": {"sources": ["scheduled"]}})


def make_authorizer(store, config=None, token_users=None):
    token_users = token_users if token_users is not None else {"tk-u1": "u1"}

    def verify(token, auto_refresh=False):
        return token_users.get(token)

    return RunnerAuthorizer(config or make_config(), connection_factory=store.connection,
                            token_verifier=verify)


def scheduled_request(client_request_id="sched_x1:2026-10-28T09:00:00", **extra):
    payload = dict(
        client_request_id=client_request_id,
        text="生成日报",
        session=SessionRef(kind="web", session_id="sess_task"),
        source="scheduled",
        profile_id="main",
        request_data={"scheduled": {"task_id": "sched_x1", "trigger": "scheduled"}},
    )
    payload.update(extra)
    return RunnerSubmit(**payload)


def chat_request():
    return RunnerSubmit(client_request_id="web-1", text="hi",
                        session=SessionRef(kind="web", session_id="sess_task"), source="chat")


# ==================== 提交（服务身份，execute=True） ====================


def test_service_submit_builds_system_principal():
    store = ScheduledAuthStore()
    authorizer = make_authorizer(store)
    principal = authorizer.authorize(scheduled_request(), "scheduler", "svc-token", execute=True)
    assert principal.actor_kind == "system" and principal.actor_id == "scheduler"
    assert principal.identity == Identity("tenant_1", "u1", "sess_task", "scheduled", "web")
    assert principal.service_id == "scheduler"
    # 任务行按 session_id + client_request_id 前缀 task_id 命中
    task_sql = [sql for sql in store.executed if "FROM scheduled_tasks" in sql][0]
    assert "session_id=%s" in task_sql and "task_id=%s" in task_sql
    # 提交语义要求租户 active（_tenant 查询已执行）
    assert any("FROM tenants" in sql for sql in store.executed)


def test_forged_service_token_rejected_401():
    authorizer = make_authorizer(ScheduledAuthStore())
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(), "scheduler", "wrong-token", execute=True)
    assert error.value.code == "SERVICE_UNAUTHORIZED" and error.value.status == 401


def test_unknown_service_id_rejected_401():
    authorizer = make_authorizer(ScheduledAuthStore())
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(), "ghost", "svc-token", execute=True)
    assert error.value.code == "SERVICE_UNAUTHORIZED"


def test_service_without_scheduled_source_403():
    store = ScheduledAuthStore()
    authorizer = make_authorizer(store, make_config(web_scheduled=False))
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(), "web", "svc-token", execute=True)
    assert error.value.code == "SERVICE_SOURCE_FORBIDDEN" and error.value.status == 403


def test_task_row_missing_404():
    authorizer = make_authorizer(ScheduledAuthStore(task=None))
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(), "scheduler", "svc-token", execute=True)
    assert error.value.code == "SCHEDULED_TASK_NOT_FOUND" and error.value.status == 404


def test_inactive_task_user_401():
    authorizer = make_authorizer(ScheduledAuthStore(user=dict(_USER, status="disabled")))
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(), "scheduler", "svc-token", execute=True)
    assert error.value.code == "USER_UNAUTHORIZED"


def test_submit_with_profile_subscription_check():
    """非 main profile 提交执行 _profile 订阅校验（命中 active 订阅放行）。"""
    store = ScheduledAuthStore()
    authorizer = make_authorizer(store)
    request = scheduled_request(profile_id="data-analysis")
    principal = authorizer.authorize(request, "scheduler", "svc-token", execute=True)
    assert principal.identity.source == "scheduled"
    assert any("FROM subscriptions" in sql for sql in store.executed)


def test_malformed_client_request_id_422():
    authorizer = make_authorizer(ScheduledAuthStore())
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(client_request_id="sched_x1"), "scheduler",
                             "svc-token", execute=True)
    assert error.value.code == "SCHEDULED_REQUEST_INVALID" and error.value.status == 422


def test_scheduled_with_channel_actor_fields_400():
    authorizer = make_authorizer(ScheduledAuthStore())
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(channel_user_id="ou1"), "scheduler",
                             "svc-token", execute=True)
    assert error.value.code == "SESSION_SOURCE_MISMATCH" and error.value.status == 400


# ==================== 幂等重查（服务身份，execute=False） ====================


def test_requery_allows_paused_task():
    """幂等重查不要求任务 active：暂停后同键重试必须能找回原 runner。"""
    store = ScheduledAuthStore(task=dict(_TASK, status="paused", pending_occurrence_key=None))
    authorizer = make_authorizer(store)
    principal = authorizer.authorize(scheduled_request(), "scheduler", "svc-token", execute=False)
    assert principal.actor_kind == "system" and principal.identity.user_id == "u1"
    # 重查语义不执行租户/订阅校验（仅任务行 + 属主）
    assert not any("FROM tenants" in sql for sql in store.executed)
    assert not any("FROM subscriptions" in sql for sql in store.executed)


# ==================== authorize_persisted（已受理重校验） ====================


def _persisted_row(**extra):
    row = {
        "runner_id": "runner_1", "scope_key": "tenant:tenant_1", "session_kind": "web",
        "session_id": "sess_task", "actor_kind": "system", "actor_id": "scheduler",
        "user_id": "u1", "tenant_id": "tenant_1", "source": "scheduled",
        "service_id": "scheduler", "client_request_id": "sched_x1:2026-10-28T09:00:00",
        "profile_id": "main",
        "input": {"session": {"kind": "web", "session_id": "sess_task"}, "source": "scheduled",
                  "text": "生成日报"},
        "checkpoint": {}, "public_snapshot": {}, "result": None,
    }
    row.update(extra)
    return row


def test_persisted_not_require_task_active_execute():
    """已受理执行在任务暂停后继续完成（execute=True 仍放行）。"""
    store = ScheduledAuthStore(task=dict(_TASK, status="paused", pending_occurrence_key=None))
    authorizer = make_authorizer(store)
    principal = authorizer.authorize_persisted(_persisted_row(), execute=True)
    assert principal.actor_kind == "system" and principal.identity.user_id == "u1"


def test_persisted_execute_false_for_recovery_scan():
    store = ScheduledAuthStore(task=dict(_TASK, status="paused"))
    authorizer = make_authorizer(store)
    principal = authorizer.authorize_persisted(_persisted_row(), execute=False)
    assert principal.identity.source == "scheduled"


def test_persisted_owner_mismatch_404():
    """任务行属主与已受理行不一致（任务会话被换绑）fail-closed。"""
    store = ScheduledAuthStore(task=dict(_TASK, user_id="u2"))
    authorizer = make_authorizer(store)
    with pytest.raises(RunnerError) as error:
        authorizer.authorize_persisted(_persisted_row(), execute=True)
    assert error.value.code == "SCHEDULED_TASK_NOT_FOUND"


# ==================== 用户 Bearer token 只读 ====================


def test_user_token_readonly_allows_owner():
    store = ScheduledAuthStore(task=dict(_TASK, status="paused"))
    authorizer = make_authorizer(store)
    principal = authorizer.authorize(scheduled_request(), "web", "svc-token",
                                     user_token="tk-u1", execute=False)
    # 返回行属主 Principal：既有 assert_owner 行级所有权校验自然通过
    assert principal.identity.user_id == "u1" and principal.identity.tenant_id == "tenant_1"
    assert principal.actor_kind == "system"


def test_user_token_submit_forbidden_403():
    authorizer = make_authorizer(ScheduledAuthStore())
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(), "web", "svc-token",
                             user_token="tk-u1", execute=True)
    assert error.value.code == "SCHEDULED_SUBMIT_FORBIDDEN" and error.value.status == 403


def test_user_token_non_owner_404():
    # tokens 表存在 u2 的有效 token（真实认证通过），但不是任务属主 → 404
    authorizer = make_authorizer(
        ScheduledAuthStore(token={"user_id": "u2", "expires_at": _EXPIRES_SOON}),
        token_users={"tk-u2": "u2"})
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(), "web", "svc-token",
                             user_token="tk-u2", execute=False)
    assert error.value.code == "RUNNER_NOT_FOUND" and error.value.status == 404


def test_user_token_revoked_401():
    authorizer = make_authorizer(ScheduledAuthStore(token=None))
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(), "web", "svc-token",
                             user_token="tk-u1", execute=False)
    assert error.value.code == "USER_UNAUTHORIZED"


def test_user_token_invalid_401():
    authorizer = make_authorizer(ScheduledAuthStore(), token_users={})
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(), "web", "svc-token",
                             user_token="tk-bad", execute=False)
    assert error.value.code == "USER_UNAUTHORIZED"


# ==================== chat 提交任务会话拒绝 ====================


def test_chat_submit_to_task_session_forbidden():
    session = dict(_SESSION, metadata={"scheduled_task": "sched_x1"})
    authorizer = make_authorizer(ScheduledAuthStore(session=session))
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(chat_request(), "web", "svc-token", user_token="tk-u1", execute=True)
    assert error.value.code == "TASK_SESSION_CHAT_FORBIDDEN" and error.value.status == 400


def test_chat_submit_to_normal_session_still_allowed():
    authorizer = make_authorizer(ScheduledAuthStore())
    principal = authorizer.authorize(chat_request(), "web", "svc-token",
                                     user_token="tk-u1", execute=True)
    assert principal.identity.source == "chat" and principal.actor_kind == "user"


def test_chat_read_of_task_session_not_rejected():
    """只读（execute=False）不做任务会话拒绝：读路径 inert，由行级属主校验兜底。"""
    session = dict(_SESSION, metadata={"scheduled_task": "sched_x1"})
    authorizer = make_authorizer(ScheduledAuthStore(session=session))
    principal = authorizer.authorize(chat_request(), "web", "svc-token",
                                     user_token="tk-u1", execute=False)
    assert principal.identity.session_id == "sess_task"


# ==================== 网关收紧与行级读取路由 ====================


def test_web_gateway_without_user_token_rejected_on_scheduled():
    """web 网关服务身份不得代表任何用户操作 scheduled 行（必须带用户 token）。"""
    authorizer = make_authorizer(ScheduledAuthStore())
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(scheduled_request(), "web", "svc-token", execute=False)
    assert error.value.code == "USER_UNAUTHORIZED" and error.value.status == 401


def test_authorize_read_in_tx_routes_scheduled_rows():
    """行级读取（事件流页）对 scheduled 行走任务行路径：用户 token 属主只读放行。"""
    authorizer = make_authorizer(ScheduledAuthStore())
    row = _persisted_row()
    with store_connection(ScheduledAuthStore()) as cursor:
        principal = authorizer.authorize_read_in_tx(cursor, row,
            {"service_id": "web", "user_token": "tk-u1"}, verified_user_id="u1")
    assert principal.identity.source == "scheduled" and principal.identity.user_id == "u1"


def test_authorize_read_in_tx_rejects_non_owner_user_token():
    store = ScheduledAuthStore(token={"user_id": "u2", "expires_at": _EXPIRES_SOON})
    authorizer = make_authorizer(store, token_users={"tk-u2": "u2"})
    row = _persisted_row()
    with store_connection(store) as cursor:
        with pytest.raises(RunnerError) as error:
            authorizer.authorize_read_in_tx(cursor, row,
                {"service_id": "web", "user_token": "tk-u2"}, verified_user_id="u2")
    assert error.value.code == "RUNNER_NOT_FOUND"


def test_list_session_query_authorizes_by_session_only():
    """任务视图执行列表（SessionQuery 无幂等键）：按任务会话唯一定位任务行。

    回归：SessionQuery 没有 client_request_id 字段，_scheduled 直接读取会
    AttributeError → 500；必须按 session_id（UNIQUE）定位并放行属主只读。
    """
    store = ScheduledAuthStore()
    authorizer = make_authorizer(store)
    query = SessionQuery(session=SessionRef(kind="web", session_id="sess_task"),
                         source="scheduled")
    principal = authorizer.authorize(query, "web", "svc-token",
                                     user_token="tk-u1", execute=False)
    assert principal.identity.user_id == "u1" and principal.actor_kind == "system"
    task_sql = [sql for sql in store.executed if "FROM scheduled_tasks" in sql][0]
    assert "session_id=%s" in task_sql and "task_id=%s" not in task_sql


# ==================== 控制操作范围矩阵（设计 §5.2） ====================


def _control_app(row, principal):
    from unittest.mock import MagicMock
    from src.services.agent_runner.control_application import ControlApplication
    manager = SimpleNamespace(
        authorizer=SimpleNamespace(verify_service=MagicMock()),
        repository=SimpleNamespace(get=MagicMock(return_value=row)),
        _authorize_row=MagicMock(return_value=principal))
    return ControlApplication(manager, controls=MagicMock())


def _control(action, **extra):
    from src.services.agent_runner.control_contracts import RunnerControl
    payload = dict(client_request_id="ctl-1", action=action)
    payload.update(extra)
    return RunnerControl(**payload)


def test_user_reply_control_on_scheduled_forbidden():
    """用户（Bearer token）对 scheduled runner 的 reply 一律拒绝（非交互要求）。"""
    app = _control_app(_persisted_row(), _principal_stub())
    with pytest.raises(RunnerError) as error:
        app._find("runner_1", _control("reply", wait_id="w1", target_execution_id="e1",
                                       answer="补充信息"),
                  {"service_id": "web", "service_token": "t", "user_token": "tk-u1"})
    assert error.value.code == "SCHEDULED_CONTROL_FORBIDDEN" and error.value.status == 403


def test_user_pause_and_resume_control_on_scheduled_forbidden():
    app = _control_app(_persisted_row(), _principal_stub())
    for action in ("pause", "resume"):
        with pytest.raises(RunnerError) as error:
            app._find("runner_1", _control(action),
                      {"service_id": "web", "service_token": "t", "user_token": "tk-u1"})
        assert error.value.code == "SCHEDULED_CONTROL_FORBIDDEN"


def test_scheduler_service_resume_without_user_token_allowed():
    """scheduler 服务（无用户 token）的无补充输入 resume 放行（恢复驱动 §4.6）。"""
    row = _persisted_row()
    app = _control_app(row, _principal_stub())
    _, principal, _previous = app._find("runner_1", _control("resume"),
                                        {"service_id": "scheduler", "service_token": "t"})
    assert principal.actor_kind == "system"


def test_scheduler_resume_with_supplemental_input_forbidden():
    """scheduler 的 resume 仅限无补充输入（设计 §5.2 矩阵）：携带 answer/
    attachments 的 resume 同样 SCHEDULED_CONTROL_FORBIDDEN。"""
    app = _control_app(_persisted_row(), _principal_stub())
    with pytest.raises(RunnerError) as error:
        app._find("runner_1", _control("resume", answer="补充信息"),
                  {"service_id": "scheduler", "service_token": "t"})
    assert error.value.code == "SCHEDULED_CONTROL_FORBIDDEN" and error.value.status == 403
    with pytest.raises(RunnerError) as error:
        attachment = {"file_id": "f1", "name": "a.txt", "type": "file"}
        app._find("runner_1", _control("resume", attachments=[attachment]),
                  {"service_id": "scheduler", "service_token": "t"})
    assert error.value.code == "SCHEDULED_CONTROL_FORBIDDEN"


def test_scheduler_service_pause_on_scheduled_forbidden():
    """scheduler 服务也仅可 resume：pause/reply 不在调度侧操作范围。"""
    app = _control_app(_persisted_row(), _principal_stub())
    with pytest.raises(RunnerError) as error:
        app._find("runner_1", _control("pause"),
                  {"service_id": "scheduler", "service_token": "t"})
    assert error.value.code == "SCHEDULED_CONTROL_FORBIDDEN"


def test_chat_runner_controls_unaffected():
    """chat 来源 runner 控制动作不受 scheduled 矩阵影响。"""
    row = _persisted_row()
    row.update({"source": "chat", "actor_kind": "user", "actor_id": "u1",
                "client_request_id": "web-1",
                "input": {"session": {"kind": "web", "session_id": "sess_task"},
                          "source": "chat", "text": "hi"}})
    app = _control_app(row, _principal_stub(source="chat"))
    _, principal, _previous = app._find("runner_1", _control("pause"),
                                        {"service_id": "web", "service_token": "t",
                                         "user_token": "tk-u1"})
    assert principal.actor_kind == "user"


def _principal_stub(source="scheduled"):
    from src.services.agent_runner.contracts import Principal
    return Principal(Identity("tenant_1", "u1", "sess_task", source, "web"),
                     "system" if source == "scheduled" else "user",
                     "scheduler" if source == "scheduled" else "u1", "scheduler")
