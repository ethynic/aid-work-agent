"""wecom_proxy 受信来源单测（代理任务契约 §6，P6）。

- 鉴权：批次行定位＋web_session_id 绑定、身份按任务持久行核定（system
  Principal）、派发检查点（任务 running＋未 stop_requested＋目标 active）、
  已受理重校验不核对 stop_requested、读取/控制仅服务身份；
- 工具派发门：批次占用、任务未软删、目标 active、会话限定（每次企微工具
  派发核对，契约 §6）；
- 非零历史：ContextAssembler 按 wecom_proxy 加载 web_session_id 会话历史/摘要
  （区别于 scheduled 零历史）；chat/scheduled 原语义回归。
"""

import copy
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.config.settings import AgentRunnerConfig
from src.core.agent_engine.contracts import AgentMode, Identity
from src.services.agent_runner.authorization import RunnerAuthorizer
from src.services.agent_runner.contracts import (RunnerError, RunnerSubmit, SessionQuery,
                                                 SessionRef)
from src.services.agent_runner.wecom_device_binding import assert_proxy_dispatch_in_tx

_BATCH = {
    "id": 12, "task_id": 7, "session": "ws1.abc", "web_session_id": "wecom_proxy_sess1",
    "agent_id": "agent-a", "tenant_id": "tenant_1", "user_id": "u1",
    "task_status": "running", "stop_requested": False,
}

_TARGET = {"status": "active"}

_USER = {"user_id": "u1", "tenant_id": "tenant_1", "role": "employee", "status": "active"}

_SESSION = {"tenant_id": "tenant_1", "user_id": "u1"}

_TENANT = {"status": "active", "expire_at": None}


class ProxyAuthStore:
    """SQL 文本路由的最小内存 connection（覆盖 wecom_proxy 鉴权查询形状）。"""

    def __init__(self, *, batch=_BATCH, target=_TARGET, user=_USER, session=_SESSION,
                 tenant=_TENANT):
        self.batch, self.target, self.user = batch, target, user
        self.session, self.tenant = session, tenant
        self.executed = []

    @contextmanager
    def connection(self):
        store = self

        class Cursor:
            def execute(self, sql, params=()):
                store.executed.append(sql)
                if "FROM bs_wecom_proxy_batches" in sql:
                    self.row = store.batch
                elif "FROM bs_wecom_proxy_task_sessions" in sql:
                    self.row = store.target
                elif "FROM users" in sql:
                    self.row = store.user
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


def make_config():
    return AgentRunnerConfig(
        web_service_id="web", web_service_token="svc-token",
        peers={"web": {"sources": ["chat", "scheduled"]},
               "wecom_proxy": {"sources": ["wecom_proxy"]}})


def make_authorizer(store, config=None):
    return RunnerAuthorizer(config or make_config(),
                            connection_factory=store.connection,
                            token_verifier=lambda token, auto_refresh=False: None)


def proxy_request(client_request_id="12:0", **extra):
    payload = dict(
        client_request_id=client_request_id,
        text="本批消息",
        session=SessionRef(kind="web", session_id=_BATCH["web_session_id"]),
        source="wecom_proxy",
        profile_id="agent-a",
        prompt_augmentations=["任务指令", "回复纪律"],
        request_data={"wecom_proxy_batch": {"batch_id": 12}},
    )
    payload.update(extra)
    return RunnerSubmit(**payload)


# ==================== 提交鉴权（execute=True） ====================


def test_submit_builds_system_principal_from_task_row():
    store = ProxyAuthStore()
    authorizer = make_authorizer(store)
    principal = authorizer.authorize(proxy_request(), "wecom_proxy", "svc-token", execute=True)
    # 身份按任务持久行核定（tenant/user 来自批次行），非请求参数
    assert principal.identity == Identity("tenant_1", "u1", "wecom_proxy_sess1",
                                          "wecom_proxy", "web")
    assert principal.actor_kind == "system" and principal.actor_id == "wecom_proxy"
    assert any("FROM bs_wecom_proxy_batches" in sql for sql in store.executed)
    assert any("FROM bs_wecom_proxy_task_sessions" in sql for sql in store.executed)


def test_forged_or_wrong_peer_rejected():
    authorizer = make_authorizer(ProxyAuthStore())
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(proxy_request(), "wecom_proxy", "wrong-token", execute=True)
    assert error.value.code == "SERVICE_UNAUTHORIZED"
    # web 网关 peer 不含 wecom_proxy 来源
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(proxy_request(), "web", "svc-token", execute=True)
    assert error.value.code == "SERVICE_SOURCE_FORBIDDEN"


def test_session_must_match_batch_web_session_id():
    """web_session_id 绑定核对：会话与批次登记不符不泄露行存在性（404）。"""
    authorizer = make_authorizer(ProxyAuthStore())
    request = proxy_request(session=SessionRef(kind="web", session_id="other-session"))
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(request, "wecom_proxy", "svc-token", execute=True)
    assert error.value.code == "RUNNER_NOT_FOUND"


def test_malformed_client_request_id_422():
    authorizer = make_authorizer(ProxyAuthStore())
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(proxy_request(client_request_id="12"), "wecom_proxy",
                             "svc-token", execute=True)
    assert error.value.code == "WECOM_PROXY_REQUEST_INVALID"


def test_dispatch_checkpoint_requires_running_task_without_stop():
    """派发检查点：任务非 running 或 stop_requested 提交拒绝（契约 §6）。"""
    authorizer = make_authorizer(ProxyAuthStore(
        batch=dict(_BATCH, task_status="stopped")))
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(proxy_request(), "wecom_proxy", "svc-token", execute=True)
    assert error.value.code == "WECOM_PROXY_TASK_NOT_RUNNING"
    authorizer = make_authorizer(ProxyAuthStore(batch=dict(_BATCH, stop_requested=True)))
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(proxy_request(), "wecom_proxy", "svc-token", execute=True)
    assert error.value.code == "WECOM_PROXY_TASK_NOT_RUNNING"


def test_dispatch_checkpoint_requires_active_target():
    authorizer = make_authorizer(ProxyAuthStore(target={"status": "removed"}))
    with pytest.raises(RunnerError) as error:
        authorizer.authorize(proxy_request(), "wecom_proxy", "svc-token", execute=True)
    assert error.value.code == "WECOM_PROXY_TARGET_INVALID"


def test_profile_subscription_checked_for_agent():
    """智能体（非 main profile）提交执行订阅＋用户授权校验（契约 §9.5 失权拦截）。"""
    store = ProxyAuthStore()
    authorizer = make_authorizer(store)
    authorizer.authorize(proxy_request(), "wecom_proxy", "svc-token", execute=True)
    assert any("FROM subscriptions" in sql for sql in store.executed)
    assert any("FROM user_agent_permissions" in sql for sql in store.executed)


def _accepted_row():
    """已受理行形状：input 为意图字典（repository.submit 存 intent，无幂等键）。"""
    intent = proxy_request().model_dump(mode="json")
    intent.pop("client_request_id")
    return {"runner_id": "runner_x", "client_request_id": "12:0",
            "service_id": "wecom_proxy", "session_kind": "web",
            "tenant_id": "tenant_1", "user_id": "u1", "profile_id": "agent-a",
            "scope_key": "tenant:tenant_1", "source": "wecom_proxy",
            "session_id": _BATCH["web_session_id"], "actor_kind": "system",
            "actor_id": "wecom_proxy", "input": intent}


# ==================== 已受理重校验（persisted，worker 执行/恢复前） ====================



def test_persisted_revalidation_ignores_stop_requested():
    """停止请求不阻断已运行批次：已受理重校验不核对 stop_requested（契约 §6）。"""
    store = ProxyAuthStore(batch=dict(_BATCH, stop_requested=True))
    authorizer = make_authorizer(store)
    principal = authorizer.authorize_persisted(_accepted_row())
    assert principal.identity.source == "wecom_proxy"


def test_persisted_revalidation_requires_active_target():
    row = _accepted_row()
    authorizer = make_authorizer(ProxyAuthStore(target={"status": "halted"}))
    with pytest.raises(RunnerError) as error:
        authorizer.authorize_persisted(row)
    assert error.value.code == "WECOM_PROXY_TARGET_INVALID"


def test_persisted_revalidation_rejects_deleted_task():
    row = _accepted_row()
    authorizer = make_authorizer(ProxyAuthStore(batch=dict(_BATCH, task_status="deleted")))
    with pytest.raises(RunnerError) as error:
        authorizer.authorize_persisted(row)
    assert error.value.code == "WECOM_PROXY_TASK_DELETED"


# ==================== 读取/控制（execute=False） ====================


def test_read_control_without_execute_skips_dispatch_checkpoint():
    """读取/控制不核对派发检查点（resume 已受理批次允许任务已停止等状态）。"""
    store = ProxyAuthStore(batch=dict(_BATCH, task_status="stopped"))
    authorizer = make_authorizer(store)
    principal = authorizer.authorize(proxy_request(), "wecom_proxy", "svc-token", execute=False)
    assert principal.actor_kind == "system"


# ==================== 工具派发门（契约 §6 每次企微工具派发核对） ====================


def _gate_row(status="running", task_status="running", target_status="active"):
    return {"batch_status": status, "session": "ws1.abc", "task_status": task_status,
            "task_id": 7, "task_user_id": "u1", "task_agent_id": "agent-a",
            "target_status": target_status}


def _gate_cursor(row):
    return SimpleNamespace(execute=lambda sql, params=(): None, fetchone=lambda: row)


def _gate_raise(row, source_row, tool, arguments, *, agent_allowed=True):
    from unittest.mock import patch

    from src.local_tools.repository import DeviceOperationUnresolved
    from src.saas.permissions import checker

    cursor = _gate_cursor(row)
    with patch.object(checker, 'check_agent_access', lambda agent_id, user: agent_allowed):
        with pytest.raises(DeviceOperationUnresolved) as error:
            assert_proxy_dispatch_in_tx(cursor, source_row, tool, arguments)
    return error.value


def test_gate_passes_for_running_batch_target_session():
    from unittest.mock import patch

    from src.saas.permissions import checker

    row = _gate_row()
    with patch.object(checker, 'check_agent_access', lambda agent_id, user: True):
        assert_proxy_dispatch_in_tx(_gate_cursor(row),
                                    {"source": "wecom_proxy", "tenant_id": "t",
                                     "runner_id": "r"},
                                    "wecom_message_send", {"session": "ws1.abc", "text": "hi"})


def test_gate_blocks_revoked_agent_and_stops_task():
    """智能体失权：fail-closed 拒绝派发（AGENT_REVOKED）并停任务（契约 §6）。"""
    from unittest.mock import patch

    stops = []
    with patch('src.services.agent_runner.wecom_device_binding._revoke_stop_task_in_tx',
               side_effect=lambda tenant_id, task_id: stops.append((tenant_id, task_id))):
        error = _gate_raise(_gate_row(), {"source": "wecom_proxy", "tenant_id": "t",
                                          "runner_id": "r"},
                            "wecom_message_send", {"session": "ws1.abc", "text": "x"},
                            agent_allowed=False)
    assert error.args[0] == "WECOM_PROXY_AGENT_REVOKED"
    assert stops == [("t", 7)]  # _gate_row 的 task_id


def test_gate_blocks_when_queue_not_acquired():
    """等待 Runner 未重新取得队列占用（批次非 running）禁止派发企微工具。"""
    error = _gate_raise(_gate_row(status="waiting"),
                        {"source": "wecom_proxy", "tenant_id": "t", "runner_id": "r"},
                        "wecom_read_session", {"session": "ws1.abc"})
    assert error.args[0] == "WECOM_PROXY_QUEUE_NOT_ACQUIRED"


def test_gate_blocks_deleted_task_and_inactive_target():
    from unittest.mock import patch

    from src.saas.permissions import checker

    with patch.object(checker, 'check_agent_access', lambda agent_id, user: True):
        error = _gate_raise(_gate_row(task_status="deleted"),
                        {"source": "wecom_proxy", "tenant_id": "t", "runner_id": "r"},
                            "wecom_read_session", {"session": "ws1.abc"})
        assert error.args[0] == "WECOM_PROXY_TASK_DELETED"
        error = _gate_raise(_gate_row(target_status="halted"),
                            {"source": "wecom_proxy", "tenant_id": "t", "runner_id": "r"},
                            "wecom_message_send", {"session": "ws1.abc", "text": "x"})
        assert error.args[0] == "WECOM_PROXY_TARGET_INVALID"


def test_gate_scopes_session_and_named_send():
    """会话限定：非目标会话与按名发送均拒绝（指令快照已给稳定标识）。"""
    from unittest.mock import patch

    from src.saas.permissions import checker

    source_row = {"source": "wecom_proxy", "tenant_id": "t", "runner_id": "r"}
    with patch.object(checker, 'check_agent_access', lambda agent_id, user: True):
        for arguments in ({"session": "ws1.other", "text": "x"},
                          {"session": "张三", "text": "x"},
                          {"target_name": "张三", "text": "x"}):
            error = _gate_raise(_gate_row(), source_row, "wecom_message_send", arguments)
            assert error.args[0] == "WECOM_PROXY_SESSION_SCOPE", arguments
        # 批量读取仅限目标会话
        assert_proxy_dispatch_in_tx(_gate_cursor(_gate_row()), source_row,
                                    "wecom_read_sessions", {"sessions": ["ws1.abc"]})
        error = _gate_raise(_gate_row(), source_row, "wecom_read_sessions",
                            {"sessions": ["ws1.abc", "ws1.x"]})
        assert error.args[0] == "WECOM_PROXY_SESSION_SCOPE"


def test_gate_missing_batch_rejects():
    """批次行缺失（重试已换新 runner）：原 runner 不再允许派发。"""
    error = _gate_raise(None, {"source": "wecom_proxy", "tenant_id": "t", "runner_id": "r"},
                        "wecom_read_session", {"session": "ws1.abc"})
    assert error.args[0] == "WECOM_PROXY_BATCH_MISSING"


def test_gate_noop_for_other_sources():
    """其他来源零开销直通（不触库）。"""

    class NoQueryCursor:
        def execute(self, sql, params=()):
            raise AssertionError("unexpected query for non-proxy source")

    assert_proxy_dispatch_in_tx(NoQueryCursor(), {"source": "scheduled"}, "wecom_message_send",
                                {"session": "ws1.other"})


# ==================== 非零历史装配（契约 §6 区别于 scheduled 零历史） ====================


def _make_assembler(identity):
    from unittest.mock import AsyncMock, MagicMock

    from src.services.agent_runner.runtime.context_assembler import ContextAssembler

    attachments = MagicMock()
    attachments.prepare.return_value = ("enhanced-text", None, None)
    attachments.build_image_reference_content.return_value = (None, None)
    attachments._build_multimodal_user_content.return_value = None
    history = MagicMock()
    history.reader = SimpleNamespace(assert_authorized=MagicMock())
    history.memory.get_context.return_value = [
        {"role": "user", "content": "上一轮历史"},
        {"role": "assistant", "content": "好的"},
    ]
    history.active_summary.return_value = "历史摘要"
    compression = MagicMock()
    compression.event = None
    compression._run_compression_phase = AsyncMock()
    remember = MagicMock()
    remember._handle_remember_intent = AsyncMock()
    assembler = ContextAssembler(identity=identity, role=AgentMode.MASTER,
        profile_config=None, history=history, compression=compression,
        prompt_sources=MagicMock(), skills=MagicMock(),
        remember=remember, attachments=attachments, visibility=MagicMock())
    assembler.visibility.prime = AsyncMock()
    return assembler, history, compression


def test_wecom_proxy_loads_session_history_by_web_session_id():
    """wecom_proxy 非零历史：按 web_session_id 加载历史＋摘要（契约 §6）。"""
    import asyncio

    from src.core.request_context import AgentRequestContext

    identity = Identity("t1", "u1", "wecom_proxy_sess1", "wecom_proxy", "web")
    assembler, history, compression = _make_assembler(identity)
    state, _ = asyncio.run(assembler.prepare(
        "本批消息", execution_id="ae_1",
        request_context=AgentRequestContext(prompt_augmentations=("任务指令",))))
    # 与 scheduled 不同：压缩、历史重载、摘要全部执行
    compression._run_compression_phase.assert_awaited_once_with("wecom_proxy_sess1")
    history._reload_memory_from_db.assert_called_once_with("wecom_proxy_sess1", "本批消息")
    history.memory.get_context.assert_called_once_with("wecom_proxy_sess1")
    history.active_summary.assert_called_once_with("wecom_proxy_sess1")
    contents = [message["content"] for message in state.messages]
    assert any("之前对话摘要" in content for content in contents)  # 摘要注入
    assert "上一轮历史" in contents and "enhanced-text" in contents  # 历史＋本批
    assert "任务指令" in contents  # 补充指令（受信追加）


def test_scheduled_zero_history_semantics_unchanged():
    """回归：scheduled 仍零历史（不读历史/摘要/压缩），chat 仍读历史。"""
    import asyncio

    identity = Identity("t1", "u1", "sess_task", "scheduled", "web")
    assembler, history, compression = _make_assembler(identity)
    asyncio.run(assembler.prepare("生成日报", execution_id="ae_2"))
    history._reload_memory_from_db.assert_not_called()
    history.active_summary.assert_not_called()
    compression._run_compression_phase.assert_not_called()

    identity = Identity("t1", "u1", "sess_chat", "chat", "web")
    assembler, history, compression = _make_assembler(identity)
    asyncio.run(assembler.prepare("你好", execution_id="ae_3"))
    history._reload_memory_from_db.assert_called_once()
    compression._run_compression_phase.assert_called_once()


def test_source_literal_accepts_wecom_proxy_only_in_contract():
    """新增 source 为加性：wecom_proxy 合法；旧来源不受影响。"""
    for source in ("chat", "scheduled", "wecom_kf", "feishu", "dingtalk", "wecom"):
        SessionQuery(session=SessionRef(kind="web", session_id="s"), source=source)
    SessionQuery(session=SessionRef(kind="web", session_id="s"), source="wecom_proxy")
    with pytest.raises(Exception):
        SessionQuery(session=SessionRef(kind="web", session_id="s"), source="wecom_proxy_x")


# ==================== 提交体与用量归属（契约 §6） ====================


def test_submit_body_identity_free_and_snapshot_frozen():
    """提交体不含租户/用户字段（身份只从批次行核定→用量/积分按该 tenant/user
    记账）；本批原文进 text，冻结指令快照进 prompt_augmentations。"""
    from src.wecom_conversation.proxy_dispatcher import build_submit_body

    batch = {'id': 12, 'task_id': 7, 'session': 'ws1.abc', 'batch_end_msg_id': 100,
             'web_session_id': 'wecom_proxy_sess1', 'agent_id': 'agent-a',
             'device_id': 'dev-1', 'self_user_id': 'acc1', 'retry_count': 0,
             'instruction_snapshot': '有客户提问时回答',
             'input_snapshot': {'schema_version': 1, 'session': 'ws1.abc',
                                'source_generation': 'gen-2',
                                'messages': [{'msg_id': 99, 'side': 'peer', 'text': '在吗',
                                              'sender_id': 'c1', 'sender_name': '客户',
                                              'sender_platform': 'wecom',
                                              'send_time': 1760000000, 'mentions': [],
                                              'mentions_complete': True}]}}
    body = build_submit_body(batch)
    serialized = str(body)
    assert 'tenant' not in serialized.lower() or 'tenant_id' not in body.get('request_data', {})
    for identity_key in ('tenant_id', 'user_id'):
        assert identity_key not in body
    assert body['text'].startswith('目标会话：ws1.abc') and 'msg_id=99' in body['text']
    assert body['prompt_augmentations'][0] == '有客户提问时回答'  # 冻结指令快照
    assert body['request_data']['wecom_proxy_batch']['self_user_id'] == 'acc1'  # @我核定基准


def test_zero_send_retry_changes_idempotency_key():
    """零发送重试（retry_count+1）换新幂等键 → 新 runner（契约 §5 规则 3）；
    同 retry_count 重试同键 → 幂等命中原 Runner。"""
    from src.wecom_conversation.proxy_dispatcher import build_submit_body

    batch = {'id': 12, 'task_id': 7, 'session': 'ws1.abc', 'batch_end_msg_id': 100,
             'web_session_id': 's', 'agent_id': 'agent-a', 'device_id': 'dev-1',
             'self_user_id': 'acc1', 'retry_count': 1, 'instruction_snapshot': 'I',
             'input_snapshot': {'messages': []}}
    assert build_submit_body(batch)['client_request_id'] == '12:1'
    batch['retry_count'] = 0
    assert build_submit_body(batch)['client_request_id'] == '12:0'
