"""租户「允许修改LLM提供者」开关端点（PATCH /api/saas/tenants/{tenant_id}/llm-provider-edit）。

覆盖：
- 仅 platform_admin 可设置（tenant_admin / 普通用户 403）；
- 写入只改 allow_llm_provider_edit 键，保留 settings 其他键（scheduler/show_ai_badge 不丢）；
- 成功后失效租户缓存（保租户前台显隐即时）；
- 租户不存在 404。
"""

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.saas.api import tenant_mgmt

_PLATFORM_ADMIN = {"user_id": "p1", "tenant_id": None, "role": "platform_admin"}
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


def _settings_row(settings):
    return {"settings": settings if isinstance(settings, str) else json.dumps(settings)}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(tenant_mgmt, "require_admin", lambda request: dict(_PLATFORM_ADMIN))
    app = FastAPI()
    app.include_router(tenant_mgmt.router)
    return TestClient(app, raise_server_exceptions=False)


class TestSetTenantLlmProviderEdit:
    def test_enable_preserves_other_settings_keys(self, client):
        conn = _FakeConn(_settings_row({"scheduler": {"max_tasks_per_user": 8},
                                        "show_ai_badge": False}))
        with patch("src.saas.api.tenant_mgmt.get_db_connection", return_value=conn), \
             patch("src.saas.api.tenant_mgmt.invalidate_tenant_cache") as invalidate:
            resp = client.patch("/api/saas/tenants/tenant_1/llm-provider-edit", headers=_AUTH,
                                json={"allow_llm_provider_edit": True})
        assert resp.status_code == 200
        assert resp.json() == {"success": True, "data": {"allow_llm_provider_edit": True}}
        assert conn.committed
        saved = json.loads(conn.cursor_obj.executed[-1][1][0])
        assert saved == {"scheduler": {"max_tasks_per_user": 8},
                         "show_ai_badge": False,
                         "allow_llm_provider_edit": True}
        invalidate.assert_called_once_with("tenant_1")

    def test_disable_roundtrip(self, client):
        conn = _FakeConn(_settings_row({"allow_llm_provider_edit": True}))
        with patch("src.saas.api.tenant_mgmt.get_db_connection", return_value=conn), \
             patch("src.saas.api.tenant_mgmt.invalidate_tenant_cache"):
            resp = client.patch("/api/saas/tenants/tenant_1/llm-provider-edit", headers=_AUTH,
                                json={"allow_llm_provider_edit": False})
        assert resp.status_code == 200
        saved = json.loads(conn.cursor_obj.executed[-1][1][0])
        assert saved["allow_llm_provider_edit"] is False

    def test_missing_tenant_404(self, client):
        with patch("src.saas.api.tenant_mgmt.get_db_connection",
                   return_value=_FakeConn(None)):
            resp = client.patch("/api/saas/tenants/tenant_1/llm-provider-edit", headers=_AUTH,
                                json={"allow_llm_provider_edit": True})
        assert resp.status_code == 404

    def test_non_platform_admin_403(self, monkeypatch):
        monkeypatch.setattr(tenant_mgmt, "require_admin",
                            lambda request: {"user_id": "a1", "tenant_id": "tenant_1",
                                             "role": "tenant_admin"})
        app = FastAPI()
        app.include_router(tenant_mgmt.router)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch("/api/saas/tenants/tenant_1/llm-provider-edit", headers=_AUTH,
                            json={"allow_llm_provider_edit": True})
        assert resp.status_code == 403
