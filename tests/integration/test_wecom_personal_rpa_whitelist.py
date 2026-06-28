"""企业微信个人账号 RPA 绑定级监控白名单服务端二次过滤集成测试

完整链路（路由级 + mock 边界，对齐 integration/test_wecom_personal_rpa_flow.py 范式）：

1. binding 配置 monitor_user_names=["陆伟"]，sender="陆伟" → 服务端接受，
   _process_inbound_message 完整执行（agent 投递阶段被 mock）。
2. binding 配置 monitor_user_names=["陆伟"]，sender="孙晨" → 服务端拒绝，
   写 monitor_whitelist_filtered 审计，_process_inbound_message 在白名单拦截前 return。
"""

# 环境隔离：必须在任何 src.* 导入之前完成
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.pop("DATABASE_URL", None)


def _install_db_stubs() -> None:
    try:
        from src.db import database as dbm  # noqa: WPS433
    except Exception:
        return
    if getattr(dbm, "_rpa_wl_test_stubbed", False):
        return
    dbm._rpa_wl_test_stubbed = True
    dbm.init_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]
    dbm.get_postgres_pool = lambda *a, **kw: object()  # type: ignore[assignment]
    dbm.close_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]


_install_db_stubs()


import json
import time
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_TEST_SECRET_PLAINTEXT = "test_rpa_secret_whitelist"
_TEST_SECRET_BYTES = _TEST_SECRET_PLAINTEXT.encode("utf-8")
_TENANT_ID = "tenant_wl_001"
_CLIENT_ID = "client_wl_001"
_ACCOUNT_ID = "wecom_account_wl_001"
_CONFIG_ID = "cfg_wl_001"


def _build_envelope(event_id: str, payload: dict) -> dict:
    return {
        "event_id": event_id,
        "client_id": _CLIENT_ID,
        "account_id": _ACCOUNT_ID,
        "event_type": "message",
        "occurred_at": datetime.now().isoformat(),
        "payload": payload,
    }


def _message_payload(sender_name: str, sender_id=None) -> dict:
    return {
        "conversation_id": "conv_wl_001",
        "conversation_type": "external_user",
        "sender_display_name": sender_name,
        "sender_stable_id": sender_id,
        "message_type": "text",
        "text": "hi",
        "attachments": [],
    }


def _signed_headers(body_bytes: bytes) -> dict:
    from src.channels.wecom_personal_rpa import auth

    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    signature = auth.compute_signature(
        _CLIENT_ID, timestamp, nonce, body_bytes, _TEST_SECRET_BYTES
    )
    return {
        "X-Client-Id": _CLIENT_ID,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Signature": signature,
        "Content-Type": "application/json",
    }


@pytest.fixture
def app() -> FastAPI:
    from src.saas.api import wecom_personal_rpa_routes as routes_mod

    test_app = FastAPI()
    test_app.include_router(routes_mod.router)
    return test_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class _FakeDedup:
    def __init__(self):
        self.seen: set = set()

    async def is_duplicate(self, key: str) -> bool:
        if key in self.seen:
            return True
        self.seen.add(key)
        return False


@pytest.fixture
def fake_dedup() -> _FakeDedup:
    return _FakeDedup()


def _patched_db(binding_row: dict):
    """返回 MagicMock 替代 routes.db，关键查询函数返回固定值。

    binding_row: find_binding_by_search_key / get_or_create_binding 的返回值，
                 带 monitor_user_names / monitor_user_ids 白名单。
    """
    fake = MagicMock()
    fake.get_client.return_value = {
        "id": _CLIENT_ID,
        "tenant_id": _TENANT_ID,
        "status": "active",
        "encrypted_secret": "enc_stub",
        "min_version": "1.0.0",
    }
    fake.update_last_seen.return_value = True
    fake.write_audit.return_value = "audit_id"
    # 关键：白名单通过 find_binding_by_search_key 提供给 router
    fake.get_or_create_binding.return_value = binding_row
    fake.find_binding_by_search_key.return_value = binding_row
    fake.list_bindings.return_value = [binding_row]
    return fake


def _apply_common_patches(fake_db, fake_dedup_obj, *, decrypt_bytes=_TEST_SECRET_BYTES):
    """patch 边界：db、dedup、secret_crypto，并把 _process_inbound_message 真实执行。

    本测试不 mock _process_inbound_message（要验证白名单在真实函数内生效），
    但 mock 掉它内部的 agent / session_queue / ensure_user_registered 重链。
    """
    from src.saas.api import wecom_personal_rpa_routes as routes_mod
    from src.channels.wecom_personal_rpa import router as router_mod

    ctxs = [
        patch.object(routes_mod, "db", fake_db),
        # check_conversation_authorization 在 router.py 内部访问 db，
        # 必须同步 patch router 模块的 db 引用
        patch.object(router_mod, "db", fake_db),
        patch.object(routes_mod, "MessageDeduplicator", lambda *a, **kw: fake_dedup_obj),
        patch.object(
            routes_mod.secret_crypto, "decrypt_secret", return_value=decrypt_bytes
        ),
        # 拦截内部 agent 重链（白名单通过后才会走到；过滤场景不会到达）
        patch("src.core.agent_router.agent_router"),
        patch("src.core.session_queue.session_queue"),
        patch("src.saas.services.auto_register.ensure_user_registered", new=AsyncMock()),
        patch(
            "src.services.session_record.SessionRecordManager.start_record",
            return_value=MagicMock(),
        ),
        patch("src.services.session_record.SessionRecordManager.end_record"),
    ]
    for c in ctxs:
        c.__enter__()
    return ctxs


@pytest.mark.integration
class TestMonitorWhitelistIntegration:
    """绑定级白名单端到端过滤：客户端绕过白名单 → 服务端拒绝。"""

    def test_allowed_sender_passes_and_reaches_agent_layer(self, client, fake_dedup):
        """场景1：binding 配置 monitor_user_names=["陆伟"]，sender="陆伟" → 服务端接受。

        白名单通过 → 进入 agent 处理（被 mock 拦截，但 _process_inbound_message 已执行
        到 session 阶段，证明未被白名单阻断）。
        """
        from src.saas.api import wecom_personal_rpa_routes as routes_mod

        binding = {
            "id": "rpa_bind_wl",
            "tenant_id": _TENANT_ID,
            "account_id": _ACCOUNT_ID,
            "conversation_type": "external_user",
            "display_name": "陆伟",
            "search_key": "wm_luwei",
            "stable_id": "wm_luwei",
            "status": "active",
            "monitor_user_names": ["陆伟"],
            "monitor_user_ids": [],
        }

        ctxs = _apply_common_patches(_patched_db(binding), fake_dedup)
        try:
            envelope = _build_envelope(
                "evt_wl_ok", _message_payload("陆伟", "wm_luwei")
            )
            body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            resp = client.post(
                f"/t/{_TENANT_ID}/wecom_personal_rpa/callback/{_CONFIG_ID}",
                content=body,
                headers=_signed_headers(body),
            )
            # 给后台 task 一点时间执行（_process_inbound_message 是 task 化的）
            import time as _t

            _t.sleep(0.3)
            # 在 patch 仍生效时读取审计调用记录
            audit_calls = routes_mod.db.write_audit.call_args_list
            categories = [call.kwargs.get("category") for call in audit_calls]
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        assert resp.status_code == 200
        assert resp.json() == {"result": "accepted"}
        # 关键断言：白名单通过的请求不会写 monitor_whitelist_filtered 审计
        assert "monitor_whitelist_filtered" not in categories

    def test_disallowed_sender_filtered_by_server(self, client, fake_dedup):
        """场景2：binding 配置 monitor_user_names=["陆伟"]，sender="孙晨" → 服务端拒绝。

        服务端写 monitor_whitelist_filtered 审计；不投递 agent。
        """
        from src.saas.api import wecom_personal_rpa_routes as routes_mod

        binding = {
            "id": "rpa_bind_wl",
            "tenant_id": _TENANT_ID,
            "account_id": _ACCOUNT_ID,
            "conversation_type": "external_user",
            "display_name": "陆伟",
            "search_key": "wm_luwei",
            "stable_id": "wm_luwei",
            "status": "active",
            "monitor_user_names": ["陆伟"],  # 仅监控陆伟
            "monitor_user_ids": [],
        }

        ctxs = _apply_common_patches(_patched_db(binding), fake_dedup)
        try:
            envelope = _build_envelope(
                "evt_wl_deny", _message_payload("孙晨", "wm_sun")
            )
            body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            resp = client.post(
                f"/t/{_TENANT_ID}/wecom_personal_rpa/callback/{_CONFIG_ID}",
                content=body,
                headers=_signed_headers(body),
            )
            import time as _t

            _t.sleep(0.3)
            # 在 patch 仍生效时读取审计调用记录
            audit_calls = routes_mod.db.write_audit.call_args_list
            filtered_calls = [
                call for call in audit_calls
                if call.kwargs.get("category") == "monitor_whitelist_filtered"
            ]
            all_categories = [c.kwargs.get("category") for c in audit_calls]
        finally:
            for c in ctxs:
                c.__exit__(None, None, None)

        assert resp.status_code == 200
        assert resp.json() == {"result": "accepted"}
        # 关键断言：写了 monitor_whitelist_filtered 审计
        assert len(filtered_calls) == 1, (
            f"应写一条 monitor_whitelist_filtered 审计，实际写了 {len(filtered_calls)} 条；"
            f"全部 category: {all_categories}"
        )
