"""企业微信个人账号 RPA /config 字段 + admin update_binding 接口单元测试

覆盖任务：
- /config 返回 archive_enabled + monitor_users 新字段
- admin PATCH /api/saas/wecom-personal-rpa/bindings/{binding_id} 接受白名单字段

采用路由级 FastAPI app + mock 边界（与 integration/test_wecom_personal_rpa_flow.py 同款，
避免 src.main 的 master_agent 单例等重链副作用）。
"""

# 环境隔离：占位 DB pool（防 integration conftest autouse fixture 真实连库）
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
    if getattr(dbm, "_rpa_cfg_test_stubbed", False):
        return
    dbm._rpa_cfg_test_stubbed = True
    dbm.init_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]
    dbm.get_postgres_pool = lambda *a, **kw: object()  # type: ignore[assignment]
    dbm.close_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]


_install_db_stubs()


import json
import time
import uuid
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


_TEST_SECRET_PLAINTEXT = "test_rpa_secret_for_hmac_cfg"
_TEST_SECRET_BYTES = _TEST_SECRET_PLAINTEXT.encode("utf-8")
_TENANT_ID = "tenant_cfg_001"
_CLIENT_ID = "client_cfg_001"


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
    }


# ============================================================================
# /config 新字段
# ============================================================================


@pytest.fixture
def routes_app() -> FastAPI:
    """仅挂 RPA 渠道路由的最小 FastAPI app。"""
    from src.saas.api import wecom_personal_rpa_routes as routes_mod

    test_app = FastAPI()
    test_app.include_router(routes_mod.router)
    return test_app


@pytest.fixture
def routes_client(routes_app: FastAPI) -> TestClient:
    return TestClient(routes_app)


class TestConfigFields:
    """/config 返回 archive_enabled + monitor_users 字段。"""

    def test_config_returns_archive_enabled_and_monitor_users(self, routes_client):
        """/config 包含 archive_enabled 字段和 monitor_users dict。"""
        from src.saas.api import wecom_personal_rpa_routes as routes_mod

        fake_db = MagicMock()
        fake_db.get_client.return_value = {
            "id": _CLIENT_ID,
            "tenant_id": _TENANT_ID,
            "status": "active",
            "encrypted_secret": "enc_stub",
            "min_version": "1.0.0",
        }
        fake_db.list_accounts.return_value = []
        fake_db.list_bindings_by_client.return_value = [
            {
                "id": "rpa_bind_a",
                "monitor_user_names": ["陆伟"],
                "monitor_user_ids": [],
            },
            {
                "id": "rpa_bind_b",
                "monitor_user_names": [],
                "monitor_user_ids": ["wm_xxx"],
            },
            # 白名单为空 → 不下发
            {
                "id": "rpa_bind_c",
                "monitor_user_names": [],
                "monitor_user_ids": [],
            },
        ]

        with patch.object(routes_mod, "db", fake_db), patch.object(
            routes_mod.secret_crypto, "decrypt_secret", return_value=_TEST_SECRET_BYTES
        ):
            body = b""
            resp = routes_client.get(
                "/api/v1/channels/wecom-personal-rpa/config",
                headers=_signed_headers(body),
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "archive_enabled" in data
        assert isinstance(data["archive_enabled"], bool)
        assert "monitor_users" in data
        # 仅白名单非空的两条下发
        assert set(data["monitor_users"].keys()) == {"rpa_bind_a", "rpa_bind_b"}
        assert data["monitor_users"]["rpa_bind_a"]["user_names"] == ["陆伟"]
        assert data["monitor_users"]["rpa_bind_b"]["user_ids"] == ["wm_xxx"]

    def test_config_archive_enabled_reads_env(self, routes_client, monkeypatch):
        """archive_enabled 由环境变量 WECOM_RPA_ARCHIVE_ENABLED 控制。"""
        from src.saas.api import wecom_personal_rpa_routes as routes_mod

        fake_db = MagicMock()
        fake_db.get_client.return_value = {
            "id": _CLIENT_ID,
            "tenant_id": _TENANT_ID,
            "status": "active",
            "encrypted_secret": "enc_stub",
            "min_version": "1.0.0",
        }
        fake_db.list_accounts.return_value = []
        fake_db.list_bindings_by_client.return_value = []

        monkeypatch.setenv("WECOM_RPA_ARCHIVE_ENABLED", "true")
        with patch.object(routes_mod, "db", fake_db), patch.object(
            routes_mod.secret_crypto, "decrypt_secret", return_value=_TEST_SECRET_BYTES
        ):
            body = b""
            resp = routes_client.get(
                "/api/v1/channels/wecom-personal-rpa/config",
                headers=_signed_headers(body),
            )

        assert resp.status_code == 200, resp.text
        assert resp.json()["archive_enabled"] is True


# ============================================================================
# admin PATCH /bindings/{binding_id}
# ============================================================================


@pytest.fixture
def admin_app() -> FastAPI:
    """仅挂 RPA admin 路由的最小 FastAPI app。"""
    from src.saas.api import wecom_personal_rpa_admin as admin_mod

    test_app = FastAPI()
    test_app.include_router(admin_mod.router)
    return test_app


@pytest.fixture
def admin_client(admin_app: FastAPI) -> TestClient:
    return TestClient(admin_app)


def _admin_request_headers() -> dict:
    """构造管理员请求头（携带 X-Tenant-Id + 模拟鉴权）。"""
    return {
        "X-Tenant-Id": _TENANT_ID,
        "Content-Type": "application/json",
    }


class TestAccountIdentityAdmin:
    def test_short_userid_is_fully_masked(self):
        from src.saas.api.wecom_personal_rpa_admin import _account_public

        public = _account_public({"wecom_user_id": "abc", "status": "offline"})
        assert public["wecom_user_id_masked"] == "***"
        assert "abc" not in str(public)

    def test_update_identity_is_tenant_scoped_and_masked(self, admin_client):
        from src.saas.api import wecom_personal_rpa_admin as admin_mod

        fake_db = MagicMock()
        fake_db.get_account_for_tenant.side_effect = [
            {"id": "acc1", "tenant_id": _TENANT_ID, "client_id": "client1"},
            {
                "id": "acc1", "tenant_id": _TENANT_ID, "client_id": "client1",
                "wecom_user_id": "zhangsan001", "identity_verified_at": "2026-07-13",
                "status": "offline",
            },
        ]
        fake_db.update_account_identity.return_value = True
        fake_db.write_audit.return_value = "audit_1"
        admin_payload = {"tenant_id": _TENANT_ID, "user_id": "u1", "role": "tenant_admin"}
        with patch.object(admin_mod, "rpa_db", fake_db), patch.object(
            admin_mod, "require_admin", lambda req: admin_payload
        ), patch.object(admin_mod, "_ensure_saas_enabled", lambda: None):
            resp = admin_client.patch(
                "/api/saas/wecom-personal-rpa/accounts/acc1/identity",
                headers=_admin_request_headers(),
                json={"wecom_user_id": "zhangsan001", "wecom_user_aliases": [" alias1 "]},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["wecom_user_id_configured"] is True
        assert data["wecom_user_id_masked"] == "***001"
        assert "wecom_user_id" not in data
        fake_db.update_account_identity.assert_called_once_with(
            _TENANT_ID, "acc1", "zhangsan001", ["alias1"], "u1"
        )

    def test_update_identity_conflict_does_not_leak_userid(self, admin_client):
        from src.saas.api import wecom_personal_rpa_admin as admin_mod

        fake_db = MagicMock()
        fake_db.get_account_for_tenant.return_value = {
            "id": "acc1", "tenant_id": _TENANT_ID, "client_id": "client1"
        }
        fake_db.update_account_identity.side_effect = ValueError("account_identity_conflict")
        with patch.object(admin_mod, "rpa_db", fake_db), patch.object(
            admin_mod, "require_admin",
            lambda req: {"tenant_id": _TENANT_ID, "user_id": "u1", "role": "tenant_admin"},
        ), patch.object(admin_mod, "_ensure_saas_enabled", lambda: None):
            resp = admin_client.patch(
                "/api/saas/wecom-personal-rpa/accounts/acc1/identity",
                headers=_admin_request_headers(),
                json={"wecom_user_id": "sensitive_userid", "wecom_user_aliases": ["alias1"]},
            )

        assert resp.status_code == 409
        assert "sensitive_userid" not in resp.text

    def test_update_identity_rejects_whitespace_only_userid(self, admin_client):
        from src.saas.api import wecom_personal_rpa_admin as admin_mod

        with patch.object(admin_mod, "require_admin", lambda req: {
            "tenant_id": _TENANT_ID, "user_id": "u1", "role": "tenant_admin"
        }), patch.object(admin_mod, "_ensure_saas_enabled", lambda: None):
            resp = admin_client.patch(
                "/api/saas/wecom-personal-rpa/accounts/acc1/identity",
                headers=_admin_request_headers(),
                json={"wecom_user_id": "   ", "wecom_user_aliases": []},
            )

        assert resp.status_code == 422


def test_resume_account_keeps_paused_when_echo_window_cannot_be_cleared():
    from src.saas.api import wecom_personal_rpa_admin as admin_mod

    fake_db = MagicMock()
    fake_db.get_account_status.return_value = "paused"
    with patch.object(admin_mod, "rpa_db", fake_db), patch.object(
        admin_mod.redis_client, "is_available", return_value=True
    ), patch.object(admin_mod.redis_client, "delete", side_effect=RuntimeError("redis down")):
        with pytest.raises(RuntimeError, match="redis down"):
            admin_mod._do_resume_account(_TENANT_ID, "acc1")

    fake_db.get_account_status.assert_called_once_with(_TENANT_ID, "acc1")
    fake_db.set_account_status.assert_not_called()


class TestUpdateBindingAdmin:
    """admin PATCH /bindings/{binding_id} 接受 monitor_user_names / monitor_user_ids。"""

    def test_update_binding_accepts_whitelist_fields(self, admin_client):
        """调用方传入白名单字段 → 透传到 db.update_binding，返回 _binding_public。"""
        from src.saas.api import wecom_personal_rpa_admin as admin_mod

        fake_db = MagicMock()
        fake_db.get_binding.return_value = {
            "id": "rpa_bind_x",
            "tenant_id": _TENANT_ID,
            "account_id": "acc1",
            "conversation_type": "external_user",
            "display_name": "张三",
            "search_key": "sk1",
            "stable_id": "sid1",
            "status": "active",
            "monitor_user_names": ["陆伟"],
            "monitor_user_ids": ["wm_x"],
        }
        fake_db.update_binding.return_value = True
        fake_db.write_audit.return_value = "audit_id"

        # require_admin 依赖 request.state.admin；用 patch 替换
        admin_payload = {
            "tenant_id": _TENANT_ID,
            "user_id": "u1",
            "role": "tenant_admin",
        }
        body = {
            "monitor_user_names": ["陆伟"],
            "monitor_user_ids": ["wm_x"],
        }
        with patch.object(admin_mod, "rpa_db", fake_db), patch.object(
            admin_mod, "require_admin", lambda req: admin_payload
        ), patch.object(admin_mod, "_ensure_saas_enabled", lambda: None):
            resp = admin_client.patch(
                f"/api/saas/wecom-personal-rpa/bindings/rpa_bind_x",
                headers=_admin_request_headers(),
                json=body,
            )

        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["success"] is True
        # 透传到 db.update_binding
        fake_db.update_binding.assert_called_once_with(
            tenant_id=_TENANT_ID,
            binding_id="rpa_bind_x",
            monitor_user_names=["陆伟"],
            monitor_user_ids=["wm_x"],
        )
        # _binding_public 携带白名单字段
        assert result["data"]["monitor_user_names"] == ["陆伟"]
        assert result["data"]["monitor_user_ids"] == ["wm_x"]

    def test_update_binding_only_names(self, admin_client):
        """只传 monitor_user_names，monitor_user_ids 保持不变（None）。"""
        from src.saas.api import wecom_personal_rpa_admin as admin_mod

        fake_db = MagicMock()
        fake_db.get_binding.return_value = {
            "id": "rpa_bind_y",
            "tenant_id": _TENANT_ID,
            "account_id": "acc1",
            "status": "active",
            "monitor_user_names": ["陆伟"],
            "monitor_user_ids": [],
        }
        fake_db.update_binding.return_value = True
        fake_db.write_audit.return_value = "audit_id"

        admin_payload = {"tenant_id": _TENANT_ID, "user_id": "u1", "role": "tenant_admin"}
        with patch.object(admin_mod, "rpa_db", fake_db), patch.object(
            admin_mod, "require_admin", lambda req: admin_payload
        ), patch.object(admin_mod, "_ensure_saas_enabled", lambda: None):
            resp = admin_client.patch(
                f"/api/saas/wecom-personal-rpa/bindings/rpa_bind_y",
                headers=_admin_request_headers(),
                json={"monitor_user_names": ["陆伟"]},
            )

        assert resp.status_code == 200, resp.text
        fake_db.update_binding.assert_called_once_with(
            tenant_id=_TENANT_ID,
            binding_id="rpa_bind_y",
            monitor_user_names=["陆伟"],
            monitor_user_ids=None,
        )

    def test_update_binding_cross_tenant_forbidden(self, admin_client):
        """跨租户访问 → 403。"""
        from src.saas.api import wecom_personal_rpa_admin as admin_mod

        fake_db = MagicMock()
        fake_db.get_binding.return_value = {
            "id": "rpa_bind_z",
            "tenant_id": "another_tenant",  # 不是 _TENANT_ID
            "account_id": "acc1",
            "status": "active",
        }

        admin_payload = {"tenant_id": _TENANT_ID, "user_id": "u1", "role": "tenant_admin"}
        with patch.object(admin_mod, "rpa_db", fake_db), patch.object(
            admin_mod, "require_admin", lambda req: admin_payload
        ), patch.object(admin_mod, "_ensure_saas_enabled", lambda: None):
            resp = admin_client.patch(
                f"/api/saas/wecom-personal-rpa/bindings/rpa_bind_z",
                headers=_admin_request_headers(),
                json={"monitor_user_names": ["x"]},
            )

        assert resp.status_code == 403
