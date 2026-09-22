# -*- coding: utf-8 -*-
"""聊天入口会话归属校验（安全 hotfix，2026-09-22）单元测试

背景（B00 §1.1.7，P0 安全）：/api/chat 与 /api/chat/stream 曾直接采信调用方传入的
session_id，不校验归属；知道他人 session_id 即可以其会话历史为上下文执行并写回
消息（跨用户/跨租户读取与污染）。cancel/history/delete 三端点同样无鉴权。

覆盖：
- _validate_session_access 规则矩阵：登录/匿名 × 会话存在/不存在/他人/跨租户/
  存量空租户/归属查询故障（fail-closed 503）；
- /api/chat/stream 与 /api/chat 拒绝时短路（不触达 Agent 构造）；
- /api/chat/stream 自有会话放行（到达 StreamingResponse）；
- cancel/history/delete 三端点拒绝与放行接线（匿名内存会话放行保留演示连续性）。

测试策略：直接调用端点协程 + monkeypatch（auth/SessionDB/租户上下文/积分检查/
agent_router），不依赖真实 DB/Redis/LLM。
"""

from types import SimpleNamespace
from threading import Lock

import pytest

import src.main as main_module
import src.saas.context as saas_context
from src.main import ChatRequest, _validate_session_access

pytestmark = pytest.mark.api


USER_A = {"user_id": "user_a", "tenant_id": "tenant_1", "username": "A"}
USER_B = {"user_id": "user_b", "tenant_id": "tenant_1", "username": "B"}

SESSION_DB = {
    "sess_a": {"session_id": "sess_a", "user_id": "user_a", "tenant_id": "tenant_1"},
    # 存量会话：tenant_id 为空（非 SaaS / 早期数据），应按 user 匹配放行
    "sess_legacy": {"session_id": "sess_legacy", "user_id": "user_a", "tenant_id": None},
    # 他租户会话：user 匹配但租户不同
    "sess_other_tenant": {"session_id": "sess_other_tenant", "user_id": "user_a", "tenant_id": "tenant_2"},
}


class FakeRequest:
    """最小 Request 替身：只需 headers / state / json()。"""

    def __init__(self, tenant_id="tenant_1", json_body=None):
        self.headers = {}
        self.state = SimpleNamespace(tenant_id=tenant_id)
        self._json = json_body or {}
        self.client = SimpleNamespace(host="127.0.0.1")

    async def json(self):
        return self._json


class FakeSSEManager:
    """sse_manager 内存替身：不触 Redis。"""

    def __init__(self):
        self.sessions = {"mem_anon": {"history": [], "files": []}}
        self.sse_connections = {}
        self.lock = Lock()
        self.cancelled = []

    def get_or_create_session(self, session_id=None):
        return session_id or "mem_new"

    def cancel_session(self, session_id):
        self.cancelled.append(session_id)

    def get_history(self, session_id):
        return self.sessions.get(session_id, {}).get("history", [])


# ============================================================
# _validate_session_access 规则矩阵
# ============================================================

@pytest.fixture
def patch_session_db(monkeypatch):
    def _install(sessions=None, error=None):
        if error is not None:
            monkeypatch.setattr("src.db.models.SessionDB.get_by_id", lambda sid: (_ for _ in ()).throw(error))
        else:
            data = sessions if sessions is not None else SESSION_DB
            monkeypatch.setattr("src.db.models.SessionDB.get_by_id", lambda sid: data.get(sid))
    return _install


@pytest.fixture(autouse=True)
def _default_channel_check(monkeypatch):
    """默认 stub 渠道会话判定为 False（避免测试触达真实 DB）；
    需要渠道场景的用例再覆写为 True。"""
    monkeypatch.setattr(
        "src.channels.session.ChannelSessionManager.is_channel_session",
        lambda self, sid: False,
    )


def test_no_session_id_passes(patch_session_db):
    patch_session_db()
    assert _validate_session_access(None, USER_A, "tenant_1") is None
    assert _validate_session_access("", USER_A, "tenant_1") is None


def test_logged_in_own_session_passes(patch_session_db):
    patch_session_db()
    assert _validate_session_access("sess_a", USER_A, "tenant_1") is None


def test_logged_in_legacy_null_tenant_session_passes(patch_session_db):
    # 存量空 tenant_id 会话：按 user 匹配放行，不因租户列为空误杀
    patch_session_db()
    assert _validate_session_access("sess_legacy", USER_A, "tenant_1") is None


def test_logged_in_foreign_user_denied(patch_session_db):
    patch_session_db()
    resp = _validate_session_access("sess_a", USER_B, "tenant_1")
    assert resp is not None and resp.status_code == 403
    assert b"SESSION_ACCESS_DENIED" in resp.body


def test_logged_in_missing_session_denied(patch_session_db):
    # 传入不存在的 session_id（含渠道会话 id——不在 chat_sessions 表）一律拒绝
    patch_session_db()
    resp = _validate_session_access("sess_not_exist", USER_A, "tenant_1")
    assert resp is not None and resp.status_code == 403


def test_logged_in_tenant_mismatch_denied(patch_session_db):
    patch_session_db()
    resp = _validate_session_access("sess_other_tenant", USER_A, "tenant_1")
    assert resp is not None and resp.status_code == 403


def test_anonymous_db_session_denied(patch_session_db):
    # 匿名请求带他人 DB 会话 id：拒绝，堵住越权读写
    patch_session_db()
    resp = _validate_session_access("sess_a", None, None)
    assert resp is not None and resp.status_code == 403


def test_anonymous_memory_session_passes(patch_session_db):
    # 匿名请求带非 DB 的内存会话 id：放行，保留匿名演示连续性
    patch_session_db()
    assert _validate_session_access("mem_anon", None, None) is None


def test_query_failure_fail_closed(patch_session_db):
    patch_session_db(error=RuntimeError("db down"))
    resp = _validate_session_access("sess_a", USER_A, "tenant_1")
    assert resp is not None and resp.status_code == 503
    assert b"SESSION_ACCESS_CHECK_FAILED" in resp.body


def test_channel_session_denied_for_logged_in(patch_session_db, monkeypatch):
    # 渠道会话（channel_sessions 登记）不允许经 Web 聊天入口访问：登录也拒绝，
    # 防止以渠道用户历史为上下文执行并写回 channel_messages
    patch_session_db()
    monkeypatch.setattr(
        "src.channels.session.ChannelSessionManager.is_channel_session",
        lambda self, sid: sid == "tenant_1_wecom_kf_x",
    )
    resp = _validate_session_access("tenant_1_wecom_kf_x", USER_A, "tenant_1")
    assert resp is not None and resp.status_code == 403


def test_channel_session_denied_for_anonymous(patch_session_db, monkeypatch):
    patch_session_db()
    monkeypatch.setattr(
        "src.channels.session.ChannelSessionManager.is_channel_session",
        lambda self, sid: True,
    )
    resp = _validate_session_access("tenant_1_wecom_kf_x", None, None)
    assert resp is not None and resp.status_code == 403


# ============================================================
# /api/chat/stream 端点接线
# ============================================================

@pytest.fixture
def stream_env(monkeypatch):
    """stream 端点最小环境：auth/租户/积分/SessionDB 可注入。"""
    state = {"user": USER_A, "sessions": SESSION_DB, "agent_called": False}

    monkeypatch.setattr(main_module.auth, "get_current_user", lambda req: state["user"])
    monkeypatch.setattr(saas_context, "get_current_tenant_id", lambda: "tenant_1")
    monkeypatch.setattr(main_module, "_check_tenant_credit_blocked", lambda tid: None)

    def fake_get_by_id(sid):
        return state["sessions"].get(sid)

    monkeypatch.setattr("src.db.models.SessionDB.get_by_id", fake_get_by_id)

    def exploding_get_agent(*args, **kwargs):
        state["agent_called"] = True
        raise AssertionError("归属校验未拦截，Agent 被构造")

    monkeypatch.setattr(main_module.agent_router, "get_agent", exploding_get_agent)
    return state


def test_stream_foreign_session_short_circuits(stream_env):
    # 登录用户 A 传入用户 B 的会话：403 且不触达 Agent 构造
    stream_env["sessions"] = {"sess_b": {"session_id": "sess_b", "user_id": "user_b", "tenant_id": "tenant_1"}}
    req = FakeRequest()
    resp = asyncio_run(
        main_module.chat_stream(req, ChatRequest(message="hi", session_id="sess_b"))
    )
    assert resp.status_code == 403
    assert b"SESSION_ACCESS_DENIED" in resp.body
    assert stream_env["agent_called"] is False


def test_stream_anonymous_db_session_short_circuits(stream_env):
    stream_env["user"] = None
    req = FakeRequest()
    resp = asyncio_run(
        main_module.chat_stream(req, ChatRequest(message="hi", session_id="sess_a"))
    )
    assert resp.status_code == 403
    assert stream_env["agent_called"] is False


def test_stream_own_session_proceeds(stream_env, monkeypatch):
    # 自有会话放行：校验通过后继续执行链（此处 stub 掉后续 DB 依赖，
    # 断言到达 StreamingResponse 而非被校验拦截）
    monkeypatch.setattr(main_module, "_resolve_default_subagent", lambda *a, **k: None)
    monkeypatch.setattr(main_module.agent_router, "get_agent", lambda *a, **k: SimpleNamespace(_init_tenant_id=True))
    monkeypatch.setattr(
        "src.tools.browser.resume_store.get_active_session_suspension",
        _async_return_none,
    )
    req = FakeRequest()
    resp = asyncio_run(
        main_module.chat_stream(req, ChatRequest(message="hi", session_id="sess_a"))
    )
    assert resp.status_code == 200
    assert resp.media_type == "text/event-stream"


def test_stream_new_session_unaffected(stream_env, monkeypatch):
    # 不传 session_id（旧行为）：校验直接放行（匿名内存会话路径）
    monkeypatch.setattr(main_module, "_resolve_default_subagent", lambda *a, **k: None)
    monkeypatch.setattr(main_module.agent_router, "get_agent", lambda *a, **k: SimpleNamespace(_init_tenant_id=True))
    monkeypatch.setattr(
        "src.tools.browser.resume_store.get_active_session_suspension",
        _async_return_none,
    )
    monkeypatch.setattr(main_module.sse_manager, "get_or_create_session", lambda sid=None: "mem_new")
    stream_env["user"] = None
    req = FakeRequest()
    resp = asyncio_run(main_module.chat_stream(req, ChatRequest(message="hi")))
    assert resp.status_code == 200


# ============================================================
# /api/chat（非流式）端点接线
# ============================================================

def test_chat_nonstream_foreign_session_denied(stream_env):
    stream_env["sessions"] = {"sess_b": {"session_id": "sess_b", "user_id": "user_b", "tenant_id": "tenant_1"}}
    req = FakeRequest(json_body={"message": "hi", "session_id": "sess_b"})
    resp = asyncio_run(main_module.chat(req))
    assert resp.status_code == 403
    assert b"SESSION_ACCESS_DENIED" in resp.body
    assert stream_env["agent_called"] is False


def test_stream_channel_session_denied(stream_env, monkeypatch):
    monkeypatch.setattr(
        "src.channels.session.ChannelSessionManager.is_channel_session",
        lambda self, sid: True,
    )
    req = FakeRequest()
    resp = asyncio_run(
        main_module.chat_stream(req, ChatRequest(message="hi", session_id="tenant_1_wecom_kf_x"))
    )
    assert resp.status_code == 403
    assert stream_env["agent_called"] is False


def test_chat_nonstream_creates_db_session_for_logged_in(stream_env, monkeypatch):
    """登录用户不传 session_id 时落库 chat_sessions（对齐 stream 端点），
    保证下一轮携带该 id 能通过归属校验（多轮对话回归修复）。"""
    created_kwargs = {}

    def fake_create(**kwargs):
        created_kwargs.update(kwargs)
        return {"session_id": "sess_new_a", "user_id": "user_a", "tenant_id": "tenant_1"}

    monkeypatch.setattr("src.db.models.SessionDB.create", fake_create)
    monkeypatch.setattr(main_module, "_resolve_default_subagent", lambda *a, **k: None)

    async def fake_sync(**kwargs):
        return "ok"

    fake_agent = SimpleNamespace(
        _init_tenant_id=True,
        mode=SimpleNamespace(value="master"),
        process_message_sync=fake_sync,
    )
    monkeypatch.setattr(main_module.agent_router, "get_agent", lambda *a, **k: fake_agent)

    # 第一轮：无 session_id → 落库并返回新 id
    resp = asyncio_run(main_module.chat(FakeRequest(json_body={"message": "hello world"})))
    assert resp.status_code == 200
    assert b"sess_new_a" in resp.body
    assert created_kwargs.get("user_id") == "user_a"
    assert created_kwargs.get("tenant_id") == "tenant_1"

    # 第二轮：携带第一轮返回的 id → 归属校验通过（不再 403）
    stream_env["sessions"]["sess_new_a"] = {
        "session_id": "sess_new_a", "user_id": "user_a", "tenant_id": "tenant_1",
    }
    resp2 = asyncio_run(
        main_module.chat(FakeRequest(json_body={"message": "again", "session_id": "sess_new_a"}))
    )
    assert resp2.status_code == 200


# ============================================================
# cancel / history / delete 端点接线
# ============================================================

@pytest.fixture
def session_endpoints_env(stream_env, monkeypatch):
    fake_sse = FakeSSEManager()
    monkeypatch.setattr(main_module, "sse_manager", fake_sse)
    return fake_sse


def test_cancel_own_session_allowed(session_endpoints_env):
    resp = asyncio_run(main_module.cancel_chat_generation("sess_a", FakeRequest()))
    assert resp.status_code == 200
    assert "sess_a" in session_endpoints_env.cancelled


def test_cancel_foreign_session_denied(session_endpoints_env, stream_env):
    stream_env["sessions"] = {"sess_b": {"session_id": "sess_b", "user_id": "user_b", "tenant_id": "tenant_1"}}
    resp = asyncio_run(main_module.cancel_chat_generation("sess_b", FakeRequest()))
    assert resp.status_code == 403
    assert "sess_b" not in session_endpoints_env.cancelled


def test_cancel_anonymous_memory_session_allowed(session_endpoints_env, stream_env):
    # 匿名（无 token）取消自己的内存会话：放行（前端 abortStreaming 依赖此路径）
    stream_env["user"] = None
    resp = asyncio_run(main_module.cancel_chat_generation("mem_anon", FakeRequest()))
    assert resp.status_code == 200
    assert "mem_anon" in session_endpoints_env.cancelled


def test_history_foreign_session_denied(session_endpoints_env, stream_env):
    stream_env["sessions"] = {"sess_b": {"session_id": "sess_b", "user_id": "user_b", "tenant_id": "tenant_1"}}
    resp = asyncio_run(main_module.get_chat_history("sess_b", FakeRequest()))
    assert resp.status_code == 403


def test_delete_anonymous_memory_session_allowed(session_endpoints_env, stream_env):
    stream_env["user"] = None
    resp = asyncio_run(main_module.delete_chat_session("mem_anon", FakeRequest()))
    assert resp.status_code == 200


# ============================================================
# helpers
# ============================================================

def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


async def _async_return_none(*args, **kwargs):
    return None
