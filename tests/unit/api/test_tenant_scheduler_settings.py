"""租户计划任务设置 API（GET/PATCH /api/saas/tenants/me/scheduler，设计 §15.2.6）。

覆盖：
- GET：全局最短间隔（只读）+ 本租户覆盖值 + 当前生效值；
- PATCH 正整数：事务内只写 tenants.settings.scheduler.max_tasks_per_user，
  保留其他租户设置键；
- PATCH null：清除覆盖恢复配置默认（scheduler 键为空时整体移除）；
- 非法值（0/负数/非整数）422；未带租户上下文 403。
"""

import json
from unittest.mock import MagicMock, patch

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
        # UPDATE 落库后，后续读取（_tenant_scheduler_override 复用同一连接工厂
        # 的返回）应看到新 settings——模拟真实 DB 的读写一致性
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


class TestGetSchedulerSettings:
    def test_returns_thresholds_and_effective_value(self, client, monkeypatch):
        monkeypatch.setattr("src.scheduler.schedule.min_interval_hours", lambda: 2)
        monkeypatch.setattr("src.scheduler.schedule.max_tasks_per_user",
                            lambda tenant_id: 8)
        with patch("src.saas.api.tenant_mgmt.get_db_connection",
                   return_value=_FakeConn(_settings_row({"scheduler": {"max_tasks_per_user": 8}}))):
            resp = client.get("/api/saas/tenants/me/scheduler", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data == {"min_interval_hours": 2, "max_tasks_per_user": 8,
                        "effective_max_tasks_per_user": 8}

    def test_no_override_shows_null_and_config_default(self, client, monkeypatch):
        monkeypatch.setattr("src.scheduler.schedule.min_interval_hours", lambda: 1)
        monkeypatch.setattr("src.scheduler.schedule.max_tasks_per_user", lambda tenant_id: 5)
        with patch("src.saas.api.tenant_mgmt.get_db_connection",
                   return_value=_FakeConn(_settings_row({"other": {"k": 1}}))):
            resp = client.get("/api/saas/tenants/me/scheduler", headers=_AUTH)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["max_tasks_per_user"] is None
        assert data["effective_max_tasks_per_user"] == 5

    def test_invalid_override_treated_as_absent(self, client, monkeypatch):
        """覆盖值非正整数（0/小数/布尔）按未设置处理，不放大不放宽"""
        monkeypatch.setattr("src.scheduler.schedule.min_interval_hours", lambda: 1)
        monkeypatch.setattr("src.scheduler.schedule.max_tasks_per_user", lambda tenant_id: 5)
        for bad in (0, 2.5, True):
            with patch("src.saas.api.tenant_mgmt.get_db_connection",
                       return_value=_FakeConn(
                           _settings_row({"scheduler": {"max_tasks_per_user": bad}}))):
                resp = client.get("/api/saas/tenants/me/scheduler", headers=_AUTH)
                assert resp.json()["data"]["max_tasks_per_user"] is None


class TestAdminRoleGate:
    """P0（CR）：require_admin 只认证不校验角色——端点必须显式复核管理员角色，
    防止普通用户越权改全租户配额。"""

    @pytest.mark.parametrize("role", ["user", "member", None])
    def test_non_admin_role_rejected_403(self, client, monkeypatch, role):
        monkeypatch.setattr(tenant_mgmt, "require_admin",
                            lambda request: dict(_ADMIN, role=role))
        resp = client.get("/api/saas/tenants/me/scheduler", headers=_AUTH)
        assert resp.status_code == 403
        resp = client.patch("/api/saas/tenants/me/scheduler", headers=_AUTH,
                            json={"max_tasks_per_user": 10})
        assert resp.status_code == 403

    @pytest.mark.parametrize("role", ["tenant_admin", "platform_admin"])
    def test_admin_roles_pass(self, client, monkeypatch, role):
        monkeypatch.setattr(tenant_mgmt, "require_admin",
                            lambda request: dict(_ADMIN, role=role))
        monkeypatch.setattr("src.scheduler.schedule.min_interval_hours", lambda: 1)
        monkeypatch.setattr("src.scheduler.schedule.max_tasks_per_user", lambda tenant_id: 5)
        with patch("src.saas.api.tenant_mgmt.get_db_connection",
                   return_value=_FakeConn(_settings_row({}))):
            resp = client.get("/api/saas/tenants/me/scheduler", headers=_AUTH)
        assert resp.status_code == 200


class TestPatchSchedulerSettings:
    def test_patch_writes_override_preserving_other_keys(self, client):
        conn = _FakeConn(_settings_row({"feature_x": {"on": True}}))
        with patch("src.saas.api.tenant_mgmt.get_db_connection", return_value=conn):
            resp = client.patch("/api/saas/tenants/me/scheduler", headers=_AUTH,
                                json={"max_tasks_per_user": 10})
        assert resp.status_code == 200
        assert resp.json()["data"]["max_tasks_per_user"] == 10
        # 只写 scheduler.max_tasks_per_user，保留其他租户设置键
        #（executed 末尾是回读 SELECT，须按语句定位 UPDATE）
        update = next((sql, params) for sql, params in conn.cursor_obj.executed
                      if "UPDATE tenants SET settings" in sql)
        update_sql, params = update
        saved = json.loads(params[0])
        assert saved["feature_x"] == {"on": True}
        assert saved["scheduler"]["max_tasks_per_user"] == 10
        assert conn.committed is True

    def test_patch_null_clears_override_and_drops_empty_scheduler(self, client):
        conn = _FakeConn(_settings_row({"scheduler": {"max_tasks_per_user": 10}}))
        with patch("src.saas.api.tenant_mgmt.get_db_connection", return_value=conn):
            resp = client.patch("/api/saas/tenants/me/scheduler", headers=_AUTH,
                                json={"max_tasks_per_user": None})
        assert resp.status_code == 200
        assert resp.json()["data"]["max_tasks_per_user"] is None
        _, params = next((sql, params) for sql, params in conn.cursor_obj.executed
                         if "UPDATE tenants SET settings" in sql)
        saved = json.loads(params[0])
        assert "scheduler" not in saved  # 空对象不残留

    @pytest.mark.parametrize("bad", [0, -1, 1.5])
    def test_patch_invalid_value_rejected_422(self, client, bad):
        resp = client.patch("/api/saas/tenants/me/scheduler", headers=_AUTH,
                            json={"max_tasks_per_user": bad})
        assert resp.status_code == 422
