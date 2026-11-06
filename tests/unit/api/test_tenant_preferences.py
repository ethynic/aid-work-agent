"""租户偏好设置 API（PATCH /api/saas/tenants/me/preferences）。

覆盖：
- PATCH show_ai_badge=true/false：事务内只写 tenants.settings.show_ai_badge，
  保留其他租户设置键；成功后失效租户缓存（auth/me 即时生效）；
- 普通用户越权 403；缺租户上下文 403；租户不存在 404。
"""

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.saas.api import tenant_mgmt

_ADMIN = {"user_id": "admin_1", "tenant_id": "tenant_1", "role": "tenant_admin"}
_AUTH = {"Authorization": "Bearer test_token"}


class _FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "UPDATE tenants SET settings" in sql:
            self.row = {"settings": params[0]}

    def fetchone(self):
        return self.row


class _FakeConn:
    def __init__(self, row=None):
        self.cursor_obj = _FakeCursor(row)
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(tenant_mgmt.router)
    monkeypatch.setattr(tenant_mgmt, "require_admin", lambda request: dict(_ADMIN))
    yield TestClient(app, raise_server_exceptions=False)


def _settings_row(settings):
    return {"settings": settings if isinstance(settings, str) else json.dumps(settings)}


class TestUpdatePreferences:
    def test_show_ai_badge_false_preserves_other_keys(self, client):
        conn = _FakeConn(_settings_row({"scheduler": {"max_tasks_per_user": 8}}))
        with patch("src.saas.api.tenant_mgmt.get_db_connection", return_value=conn), \
             patch("src.saas.api.tenant_mgmt.invalidate_tenant_cache") as invalidate:
            resp = client.patch("/api/saas/tenants/me/preferences", headers=_AUTH,
                                json={"show_ai_badge": False})
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "data": {"show_ai_badge": False}}
        assert conn.committed
        saved = json.loads(conn.cursor_obj.executed[-1][1][0])
        assert saved == {"scheduler": {"max_tasks_per_user": 8}, "show_ai_badge": False}
        invalidate.assert_called_once_with("tenant_1")

    def test_show_ai_badge_true_roundtrip(self, client):
        conn = _FakeConn(_settings_row({"show_ai_badge": False}))
        with patch("src.saas.api.tenant_mgmt.get_db_connection", return_value=conn), \
             patch("src.saas.api.tenant_mgmt.invalidate_tenant_cache"):
            resp = client.patch("/api/saas/tenants/me/preferences", headers=_AUTH,
                                json={"show_ai_badge": True})
        assert resp.status_code == 200
        saved = json.loads(conn.cursor_obj.executed[-1][1][0])
        assert saved["show_ai_badge"] is True

    def test_missing_tenant_row_404(self, client):
        with patch("src.saas.api.tenant_mgmt.get_db_connection",
                   return_value=_FakeConn(None)):
            resp = client.patch("/api/saas/tenants/me/preferences", headers=_AUTH,
                                json={"show_ai_badge": False})
        assert resp.status_code == 404


class TestPermissions:
    def test_normal_user_forbidden(self, client, monkeypatch):
        monkeypatch.setattr(tenant_mgmt, "require_admin",
                            lambda request: {"user_id": "u1", "tenant_id": "tenant_1", "role": "user"})
        resp = client.patch("/api/saas/tenants/me/preferences", headers=_AUTH,
                            json={"show_ai_badge": False})
        assert resp.status_code == 403

    def test_platform_admin_without_tenant_forbidden(self, client, monkeypatch):
        monkeypatch.setattr(tenant_mgmt, "require_admin",
                            lambda request: {"user_id": "p1", "tenant_id": None, "role": "platform_admin"})
        resp = client.patch("/api/saas/tenants/me/preferences", headers=_AUTH,
                            json={"show_ai_badge": False})
        assert resp.status_code == 403
