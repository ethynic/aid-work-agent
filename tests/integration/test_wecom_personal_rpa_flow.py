"""企业微信个人账号 RPA 渠道回调流程集成测试

覆盖 docs/system/wecom-personal-rpa-protocol.md §A 线协议的核心服务端路径，
验证 wire 层（src/saas/api/wecom_personal_rpa_routes.py）与实现层模块的协作：

1. message 事件 + 正确 HMAC → 返回 accepted，后台 task 被调度。
2. 同一 event_id 第二次上报 → 仍返回 accepted，但去重命中、不重复处理。
3. 客户端离线时 reply → db.enqueue_action 被调用（落 outbox）。
4. action_result 回执幂等（action_result_id 维度去重）。
5. 鉴权失败（签名错误）→ 返回 401 + RpaErrorResponse。

采用方式说明（按任务要求如实记录）：
- 不启动完整 ``src.main`` app（会触发 master_agent 单例 + apscheduler 等重链副作用），
  改为 **路由级最小 FastAPI app + mock 边界**：只 include ``wecom_personal_rpa_routes.router``，
  mock 掉 ``db``（无真实 PostgreSQL，本会话环境远程 DB 不可达）、``secret_crypto.decrypt_secret``
  （返回已知 secret bytes，配合真实 ``auth.compute_signature`` 构造正确签名）、
  ``MessageDeduplicator.is_duplicate``（控制去重状态机）、
  ``_process_inbound_message``（避免后台 task 导入 agent_router/session_queue 的重链）。
- 签名使用 **真实** ``auth.compute_signature``（契约逐字对齐），不 mock。
- ``tests/integration/conftest.py`` 的 autouse ``init_db_pool`` fixture 会因远程 DB 不可达
  调用 ``pytest.skip``，本文件在模块顶层把 ``init_postgres_pool`` / ``get_postgres_pool``
  替换为占位，使该 fixture 认为 pool 已就绪而跳过初始化（**仅测试隔离用，不进生产**）。
"""

# ===========================================================================
# 0. 环境隔离：必须在任何 src.* 导入之前完成
# ===========================================================================

import os
import sys
import types
from unittest.mock import MagicMock

# 关闭远程 DB 连接尝试（tests/integration/conftest.py autouse fixture 会读 DATABASE_URL）
os.environ.pop("DATABASE_URL", None)

# 占位 src.db.database 的 pool 函数：让 integration conftest 的 init_db_pool fixture
# 在 get_postgres_pool() 非 None 时直接 yield，不再尝试真实连接。
_db_mod = types.ModuleType("src.db.database_stubs")


def _install_db_stubs() -> None:
    """注入 pool 占位，避免 conftest autouse fixture 真实连库。

    必须在 conftest 的 init_db_pool fixture 首次调用前生效，因此在模块顶层执行。
    """
    try:
        from src.db import database as dbm  # noqa: WPS433（测试隔离，故意延迟到函数内）
    except Exception:  # pragma: no cover - 导入失败说明环境异常，让后续测试自然报错
        return
    if getattr(dbm, "_rpa_test_stubbed", False):
        return
    dbm._rpa_test_stubbed = True
    dbm.init_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]
    # 返回非 None 标记，使 conftest 走 "已初始化" 分支
    dbm.get_postgres_pool = lambda *a, **kw: object()  # type: ignore[assignment]
    dbm.close_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]


_install_db_stubs()


# ===========================================================================
# 1. 正式 imports（此刻 src.* 已可安全导入）
# ===========================================================================

import json
import time
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ===========================================================================
# Fixtures
# ===========================================================================


# 已知的客户端 secret（明文，仅在测试中参与 HMAC；secret_crypto.decrypt_secret 被 mock 返回它）
_TEST_SECRET_PLAINTEXT = "test_rpa_secret_for_hmac"
_TEST_SECRET_BYTES = _TEST_SECRET_PLAINTEXT.encode("utf-8")
_TENANT_ID = "tenant_test_001"
_CLIENT_ID = "client_001"
_ACCOUNT_ID = "wecom_account_001"
_CONFIG_ID = "cfg_001"


@pytest.fixture
def app() -> FastAPI:
    """最小 FastAPI app：仅挂 RPA 渠道路由，避免 src.main 的重链副作用。"""
    from src.saas.api import wecom_personal_rpa_routes as routes_mod

    test_app = FastAPI()
    test_app.include_router(routes_mod.router)
    return test_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def _build_envelope(event_id: str, event_type: str, payload: dict) -> dict:
    """构造一个合法的 RpaCallbackEnvelope dict。"""
    return {
        "event_id": event_id,
        "client_id": _CLIENT_ID,
        "account_id": _ACCOUNT_ID,
        "event_type": event_type,
        "occurred_at": datetime.now().isoformat(),
        "payload": payload,
    }


def _message_payload(text: str = "你好") -> dict:
    return {
        "conversation_id": "conv_abc",
        "conversation_type": "external_user",
        "sender_display_name": "张三",
        "sender_stable_id": None,
        "message_type": "text",
        "text": text,
        "attachments": [],
    }


def _action_result_payload(action_result_id: str, success: bool = True) -> dict:
    return {
        "request_id": "req_test_001",
        "action_result_id": action_result_id,
        "action_index": 0,
        "action_type": "send_text",
        "success": success,
        "error_code": None,
        "error_message": None,
        "executed_at": datetime.now().isoformat(),
    }


def _signed_headers(body_bytes: bytes, secret: bytes = _TEST_SECRET_BYTES) -> dict:
    """构造携带正确 HMAC 签名的请求头（使用真实 auth.compute_signature）。"""
    from src.channels.wecom_personal_rpa import auth

    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = auth.compute_signature(_CLIENT_ID, timestamp, nonce, body_bytes, secret)
    return {
        "X-Client-Id": _CLIENT_ID,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
        "Content-Type": "application/json",
    }


def _signed_ws_query(secret: bytes = _TEST_SECRET_BYTES) -> str:
    """构造 WebSocket 握手使用的 HMAC query string。"""
    from src.channels.wecom_personal_rpa import auth

    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = auth.compute_signature(_CLIENT_ID, timestamp, nonce, b"", secret)
    return (
        f"client_id={_CLIENT_ID}&timestamp={timestamp}&nonce={nonce}"
        f"&signature={signature}"
    )


def _patched_db(client_row: dict | None = None):
    """返回一个 MagicMock 替代 routes 模块里的 db，提供路由用到的全部函数。"""
    fake = MagicMock()
    default_client = client_row or {
        "id": _CLIENT_ID,
        "tenant_id": _TENANT_ID,
        "status": "active",
        "encrypted_secret": "enc_stub",
        "min_version": "1.0.0",
    }
    fake.get_client.return_value = default_client
    fake.update_last_seen.return_value = True
    fake.write_audit.return_value = "audit_id"
    fake.set_account_status.return_value = True
    fake.list_outbox.return_value = []
    fake.mark_outbox_status.return_value = True
    fake.list_accounts.return_value = []
    return fake


class _FakeDedup:
    """可编程的 MessageDeduplicator 替身：seen 集合控制 is_duplicate 返回值。"""

    def __init__(self):
        self.seen: set[str] = set()
        self.calls: list[str] = []

    async def is_duplicate(self, key: str) -> bool:
        self.calls.append(key)
        if key in self.seen:
            return True
        self.seen.add(key)
        return False


@pytest.fixture
def fake_dedup() -> _FakeDedup:
    return _FakeDedup()


def _apply_common_patches(fake_db, fake_dedup_obj, *, decrypt_bytes=_TEST_SECRET_BYTES):
    """统一 patch 路由用到的边界依赖。

    返回 ``(ctxs, mocks)``：
    - ``ctxs``：已 enter 的 context managers 列表（用完需 exit）。
    - ``mocks``：对关键 mock 的引用，供测试在 patch 仍生效时断言。
      ``mocks['process_inbound']`` 是替换 ``_process_inbound_message`` 的 AsyncMock。
    """
    from src.saas.api import wecom_personal_rpa_routes as routes_mod

    process_mock = AsyncMock()
    ctxs = [
        patch.object(routes_mod, "db", fake_db),
        patch.object(routes_mod, "MessageDeduplicator", lambda *a, **kw: fake_dedup_obj),
        patch.object(
            routes_mod.secret_crypto, "decrypt_secret", return_value=decrypt_bytes
        ),
        # 后台任务：避免导入 agent_router/session_queue 的重链
        patch.object(routes_mod, "_process_inbound_message", new=process_mock),
    ]
    for c in ctxs:
        c.__enter__()
    return ctxs, {"process_inbound": process_mock}


# ===========================================================================
# 测试
# ===========================================================================


@pytest.mark.integration
class TestWeComPersonalRpaCallbackFlow:
    """RPA 渠道回调路由端到端（路由级 + mock 边界）。"""

    def test_message_event_with_valid_hmac_returns_accepted(self, client, fake_dedup):
        """场景1：message 事件 + 正确 HMAC → accepted，后台 task 被调度。"""
        envelope = _build_envelope("evt_001", "message", _message_payload("你好"))
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        headers = _signed_headers(body)

        ctxs, mocks = _apply_common_patches(_patched_db(), fake_dedup)
        try:
            resp = client.post(
                f"/t/{_TENANT_ID}/wecom_personal_rpa/callback/{_CONFIG_ID}",
                content=body,
                headers=headers,
            )
            # 在 patch 仍生效时读取调度记录（exit 后模块属性会还原为原函数）
            process_await_count = mocks["process_inbound"].await_count
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"result": "accepted"}
        # message 事件 task 化后台处理：_process_inbound_message 被调度至少一次
        assert process_await_count >= 1

    def test_duplicate_event_id_still_accepted_but_not_reprocessed(
        self, client, fake_dedup
    ):
        """场景2：同一 event_id 第二次上报 → accepted，但去重命中、后台 task 不再调度。"""
        envelope = _build_envelope("evt_dup_001", "message", _message_payload())
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")

        ctxs, mocks = _apply_common_patches(_patched_db(), fake_dedup)
        try:
            # 第一次
            h1 = _signed_headers(body)
            r1 = client.post(
                f"/t/{_TENANT_ID}/wecom_personal_rpa/callback/{_CONFIG_ID}",
                content=body,
                headers=h1,
            )
            # 第二次（相同 event_id，需要新的 nonce/时间戳/签名）
            h2 = _signed_headers(body)
            r2 = client.post(
                f"/t/{_TENANT_ID}/wecom_personal_rpa/callback/{_CONFIG_ID}",
                content=body,
                headers=h2,
            )
            process_await_count = mocks["process_inbound"].await_count
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        assert r1.status_code == 200 and r1.json() == {"result": "accepted"}
        assert r2.status_code == 200 and r2.json() == {"result": "accepted"}
        # 去重键被查询两次，且键相同
        assert len(fake_dedup.calls) == 2
        assert fake_dedup.calls[0] == fake_dedup.calls[1]
        # 第二次返回 True（去重命中）→ 后台 task 仅调度一次
        assert process_await_count == 1

    def test_action_result_receipt_is_idempotent(self, client, fake_dedup):
        """场景4：action_result 回执幂等（action_result_id 维度去重）。"""
        ar_id = "res_idem_001"
        envelope = _build_envelope(
            "evt_ar_001", "action_result", _action_result_payload(ar_id, success=True)
        )
        body1 = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        # 第二次回执（不同 event_id，相同 action_result_id → 去重）
        envelope2 = {**envelope, "event_id": "evt_ar_002"}
        body2 = json.dumps(envelope2, ensure_ascii=False).encode("utf-8")

        fake_db = _patched_db()
        # 提供 pending outbox 行供 _handle_action_result 匹配并 mark
        fake_db.list_outbox.return_value = [
            {"id": "act_row_1", "request_id": "req_test_001", "status": "running"}
        ]
        ctxs, _mocks = _apply_common_patches(fake_db, fake_dedup)
        try:
            r1 = client.post(
                f"/t/{_TENANT_ID}/wecom_personal_rpa/callback/{_CONFIG_ID}",
                content=body1,
                headers=_signed_headers(body1),
            )
            r2 = client.post(
                f"/t/{_TENANT_ID}/wecom_personal_rpa/callback/{_CONFIG_ID}",
                content=body2,
                headers=_signed_headers(body2),
            )
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        assert r1.status_code == 200 and r1.json() == {"result": "accepted"}
        assert r2.status_code == 200 and r2.json() == {"result": "accepted"}
        # mark_outbox_status 仅被调用一次（第二次回执被 action_result_id 去重）
        assert fake_db.mark_outbox_status.call_count == 1

    def test_invalid_signature_returns_401_error_envelope(self, client, fake_dedup):
        """场景5：签名错误 → 401 + RpaErrorResponse（error=auth_failed）。"""
        body = json.dumps(
            _build_envelope("evt_bad_sig", "message", _message_payload()),
            ensure_ascii=False,
        ).encode("utf-8")

        headers = _signed_headers(body)
        # 篡改签名
        headers["X-Signature"] = "0" * 64

        ctxs, _mocks = _apply_common_patches(_patched_db(), fake_dedup)
        try:
            resp = client.post(
                f"/t/{_TENANT_ID}/wecom_personal_rpa/callback/{_CONFIG_ID}",
                content=body,
                headers=headers,
            )
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        assert resp.status_code == 401
        body_json = resp.json()
        assert body_json["error"] == "auth_failed"
        assert "message" in body_json
        # debug 字段不得包含 secret / 签名串
        assert _TEST_SECRET_PLAINTEXT not in (body_json.get("debug") or "")


@pytest.mark.integration
class TestWeComPersonalRpaWebSocketHeartbeat:
    """WebSocket 心跳兼容文本、二进制帧及正常断开。"""

    def test_binary_and_text_heartbeats_keep_connection_alive(self, client, fake_dedup):
        """旧版二进制心跳不会断线，后续文本心跳仍可被处理。"""
        fake_db = _patched_db()
        ctxs, _mocks = _apply_common_patches(fake_db, fake_dedup)
        try:
            path = (
                f"/t/{_TENANT_ID}/wecom_personal_rpa/ws/{_CONFIG_ID}"
                f"?{_signed_ws_query()}"
            )
            with client.websocket_connect(path) as websocket:
                websocket.send_bytes(b"")
                websocket.send_text("ping")
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        assert fake_db.update_last_seen.call_count == 2

    def test_disconnect_does_not_update_last_seen(self, client, fake_dedup):
        """连接关闭事件仅退出接收循环，不误记为一次心跳。"""
        fake_db = _patched_db()
        ctxs, _mocks = _apply_common_patches(fake_db, fake_dedup)
        try:
            path = (
                f"/t/{_TENANT_ID}/wecom_personal_rpa/ws/{_CONFIG_ID}"
                f"?{_signed_ws_query()}"
            )
            with client.websocket_connect(path):
                pass
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        fake_db.update_last_seen.assert_not_called()


@pytest.mark.integration
class TestActionDeliverOfflineOutbox:
    """客户端离线时出站动作落 outbox（直接验证 action_client.deliver_actions）。"""

    @pytest.mark.asyncio
    async def test_offline_client_enqueues_action_to_outbox(self):
        """客户端离线 → db.enqueue_action 被调用（落 outbox）。

        采用直接调用 deliver_actions 的方式（路由层 message 事件走后台 task，
        难以在 TestClient 内联断言；投递边界单独验证更确定）。
        """
        from src.channels.wecom_personal_rpa import action_client
        from src.channels.wecom_personal_rpa import db as rpa_db

        with patch.object(rpa_db, "get_account") as m_get_acct, patch.object(
            rpa_db, "enqueue_action"
        ) as m_enqueue, patch.object(
            action_client.client_connection_registry, "is_online", return_value=False
        ):
            # 账号存在但客户端离线
            m_get_acct.return_value = {
                "id": _ACCOUNT_ID,
                "client_id": _CLIENT_ID,
                "status": "offline",
            }
            m_enqueue.return_value = {"id": "act_row_1", "status": "pending"}

            await action_client.deliver_actions(
                tenant_id=_TENANT_ID,
                account_id=_ACCOUNT_ID,
                conversation_id="conv_abc",
                request_id="req_test_002",
                session_id=f"wecom_personal_rpa:{_ACCOUNT_ID}:conv_abc",
                actions=[{"type": "send_text", "text": "您好，已收到。"}],
            )

            m_enqueue.assert_called_once()
            _, kwargs = m_enqueue.call_args
            assert kwargs["dedup_key"] == f"wecom_personal_rpa:{_TENANT_ID}:req_test_002"
            assert kwargs["account_id"] == _ACCOUNT_ID

    @pytest.mark.asyncio
    async def test_online_client_skips_outbox(self):
        """客户端在线 → 直接推送，不写 outbox（enqueue_action 不被调用）。"""
        from src.channels.wecom_personal_rpa import action_client
        from src.channels.wecom_personal_rpa import db as rpa_db

        with patch.object(rpa_db, "get_account") as m_get_acct, patch.object(
            rpa_db, "enqueue_action"
        ) as m_enqueue, patch.object(
            action_client.client_connection_registry, "is_online", return_value=True
        ) as m_online, patch.object(
            action_client.client_connection_registry, "send", new=AsyncMock(
                return_value=True
            )
        ) as m_send:
            m_get_acct.return_value = {
                "id": _ACCOUNT_ID,
                "client_id": _CLIENT_ID,
                "status": "online",
            }

            await action_client.deliver_actions(
                tenant_id=_TENANT_ID,
                account_id=_ACCOUNT_ID,
                conversation_id="conv_abc",
                request_id="req_test_003",
                session_id=f"wecom_personal_rpa:{_ACCOUNT_ID}:conv_abc",
                actions=[{"type": "send_text", "text": "在线直推"}],
            )

            m_online.assert_called_once_with(_CLIENT_ID)
            m_send.assert_awaited_once()
            m_enqueue.assert_not_called()


# ===========================================================================
# 6. GET /config 响应字段契约（client_id / tenant_id / config_id）
# ===========================================================================


@pytest.mark.integration
class TestConfigResponseFields:
    """GET /api/v1/channels/wecom-personal-rpa/config 响应必须包含
    client_id / tenant_id / config_id，供客户端构造 callback/ws 路径。"""

    def test_config_response_contains_client_tenant_config_ids(self, client, fake_dedup):
        """WHY: 客户端 AgentApiClient.CallbackPath 需要这三个字段拼接
        ``t/{tenant_id}/wecom_personal_rpa/callback/{config_id}``，缺失则客户端无法上报。
        config_id 必须来自 tenant_channel_configs 表（register_client 时写入）。
        """
        fake_db = _patched_db()
        ctxs, _ = _apply_common_patches(fake_db, fake_dedup)
        try:
            from src.saas.db import channel_config_db as cfg_mod

            captured = {}

            def _fake_list_by_tenant(tenant_id, channel_type=None):
                captured["tenant_id"] = tenant_id
                captured["channel_type"] = channel_type
                # 真实 tenant_channel_configs 行：id 是主键，register_client 时写入。
                # 后端用 str(id) 作为 config_id 返回给客户端构造 callback 路径。
                return [{"id": "cfg_from_db_001"}]

            with patch.object(
                cfg_mod.ChannelConfigDB, "list_by_tenant", _fake_list_by_tenant
            ):
                # /config 路由用 X-Tenant-Id 解析鉴权 get_secret（缺则空串鉴权失败）
                headers = {**_signed_headers(b""), "X-Tenant-Id": _TENANT_ID}
                resp = client.get(
                    "/api/v1/channels/wecom-personal-rpa/config",
                    headers=headers,
                )
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["client_id"] == _CLIENT_ID
        assert body["tenant_id"] == _TENANT_ID
        assert body["config_id"] == "cfg_from_db_001"
        # list_by_tenant 收到的过滤参数正确
        assert captured["tenant_id"] == _TENANT_ID
        assert captured["channel_type"] == "wecom_personal_rpa"

    def test_config_response_config_id_none_when_no_channel_config(self, client, fake_dedup):
        """WHY: register_client 时 ChannelConfigDB.create 失败的边缘情况下，
        tenant_channel_configs 表无该 client 记录 → config_id=None。
        客户端据此能识别并提示管理员补建（合理失败，不静默）。
        """
        fake_db = _patched_db()
        ctxs, _ = _apply_common_patches(fake_db, fake_dedup)
        try:
            from src.saas.db import channel_config_db as cfg_mod

            with patch.object(
                cfg_mod.ChannelConfigDB,
                "list_by_tenant",
                lambda tenant_id, channel_type=None: [],
            ):
                headers = {**_signed_headers(b""), "X-Tenant-Id": _TENANT_ID}
                resp = client.get(
                    "/api/v1/channels/wecom-personal-rpa/config",
                    headers=headers,
                )
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["client_id"] == _CLIENT_ID
        assert body["tenant_id"] == _TENANT_ID
        assert body["config_id"] is None  # 边缘情况：无 channel_config 记录

    def test_config_response_config_id_none_when_db_query_raises(self, client, fake_dedup):
        """WHY: ChannelConfigDB.list_by_tenant 抛异常时（如临时 DB 不可达），
        /config 不能 500，应降级为 config_id=None 并继续返回其它字段。
        """
        fake_db = _patched_db()
        ctxs, _ = _apply_common_patches(fake_db, fake_dedup)
        try:
            from src.saas.db import channel_config_db as cfg_mod

            def _raise(tenant_id, channel_type=None):
                raise RuntimeError("db down")

            with patch.object(
                cfg_mod.ChannelConfigDB, "list_by_tenant", _raise
            ):
                headers = {**_signed_headers(b""), "X-Tenant-Id": _TENANT_ID}
                resp = client.get(
                    "/api/v1/channels/wecom-personal-rpa/config",
                    headers=headers,
                )
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 降级：client_id / tenant_id 仍返回，config_id 为 None
        assert body["client_id"] == _CLIENT_ID
        assert body["tenant_id"] == _TENANT_ID
        assert body["config_id"] is None
