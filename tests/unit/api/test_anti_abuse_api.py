"""防滥用中心 API 单元测试：租户隔离 / 参数校验 / 平台默认回显 / 用户停用接口。"""
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config.settings import settings
from src.saas.api import abuse as abuse_api
from src.saas.api import tenant_users as tenant_users_api


@pytest.fixture
def client(monkeypatch):
    """最小 app 挂 abuse + tenant_users 路由，鉴权与 DB 边界全部 mock"""
    app = FastAPI()
    app.include_router(abuse_api.router)
    app.include_router(tenant_users_api.router)

    admin = {"user_id": "admin1", "tenant_id": "t1", "role": "tenant_admin"}
    monkeypatch.setattr(abuse_api, "require_admin", lambda request: admin)
    monkeypatch.setattr(tenant_users_api, "require_admin", lambda request: admin)

    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = None  # 归属校验默认查无行 -> 404
    conn.cursor.return_value = cursor

    @contextmanager
    def fake_db():
        yield cursor

    monkeypatch.setattr(abuse_api, "get_db_connection", fake_db)
    monkeypatch.setattr(tenant_users_api, "get_db_connection", fake_db)

    service = MagicMock()
    service.load_policy_row.return_value = None
    service.upsert_policy.return_value = None
    monkeypatch.setattr(abuse_api, "get_anti_abuse_service", lambda: service)

    # 审计装饰器 record_behavior 落库边界 mock（不验证审计本身）
    from src.services import behavior_log
    monkeypatch.setattr(behavior_log, "record_behavior", AsyncMock())

    yield TestClient(app, raise_server_exceptions=False)


def _policy_row(**overrides):
    row = {
        "tenant_id": "t1", "scope_type": "user", "scope_id": "u1",
        "mode": "observe",
        "policies": [{"rule_id": "credit_budget",
                      "params": {"period": "day", "warn_threshold": 400, "hard_threshold": 800}}],
        "updated_by": "admin1", "created_at": "2026-10-11 10:00:00",
        "updated_at": "2026-10-11 10:00:00",
    }
    row.update(overrides)
    return row


# ============== presets ==============

class TestPresets:
    def test_returns_catalog(self, client):
        res = client.get("/api/saas/abuse/presets")
        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        rule_ids = [r["rule_id"] for r in body["rules"]]
        assert rule_ids == ["credit_budget"]


# ============== policies 读 ==============

class TestGetPolicies:
    def test_no_row_returns_platform_default(self, client):
        res = client.get("/api/saas/abuse/policies", params={"scope_type": "tenant"})
        body = res.json()
        assert body["exists"] is False
        assert body["is_platform_default"] is True
        default = settings.anti_abuse.tenant_default
        assert body["mode"] == default.mode
        assert body["policies"] == default.policies
        assert body["scope_id"] == "t1"  # tenant 兜底可省 scope_id

    def test_existing_row_returned(self, client, monkeypatch):
        monkeypatch.setattr(
            abuse_api, "get_anti_abuse_service",
            lambda: MagicMock(load_policy_row=lambda *a: _policy_row()))
        # 归属校验查到行（用户属于本租户）
        cursor = MagicMock()
        cursor.fetchone.return_value = {"ok": 1}

        @contextmanager
        def fake_db():
            yield cursor

        monkeypatch.setattr(abuse_api, "get_db_connection", fake_db)
        res = client.get(
            "/api/saas/abuse/policies", params={"scope_type": "user", "scope_id": "u1"})
        body = res.json()
        assert body["exists"] is True and body["scope_id"] == "u1"

    def test_tenant_scope_with_other_tenant_id_forbidden(self, client):
        res = client.get(
            "/api/saas/abuse/policies",
            params={"scope_type": "tenant", "scope_id": "t_other"})
        assert res.status_code == 403

    def test_user_scope_requires_scope_id(self, client):
        res = client.get("/api/saas/abuse/policies", params={"scope_type": "user"})
        assert res.status_code == 400

    def test_scope_not_in_tenant_404(self, client):
        # user 不属于本租户（查询无行）
        res = client.get(
            "/api/saas/abuse/policies",
            params={"scope_type": "user", "scope_id": "u_out"})
        assert res.status_code == 404

    def test_invalid_scope_type(self, client):
        res = client.get("/api/saas/abuse/policies", params={"scope_type": "global"})
        assert res.status_code == 400


# ============== policies 写 ==============

def _put(client, policies, mode="observe", scope_type="tenant", scope_id=None):
    params = {"scope_type": scope_type}
    if scope_id:
        params["scope_id"] = scope_id
    return client.put(
        "/api/saas/abuse/policies", params=params,
        json={"mode": mode, "policies": policies})


class TestPutPolicies:
    def test_valid_save(self, client, monkeypatch):
        service = MagicMock()
        monkeypatch.setattr(abuse_api, "get_anti_abuse_service", lambda: service)
        res = _put(client, [{"rule_id": "credit_budget",
                             "params": {"period": "day", "warn_threshold": 400,
                                        "hard_threshold": 800}}])
        assert res.status_code == 200 and res.json()["success"] is True
        args = service.upsert_policy.call_args.args
        assert args[0] == "t1" and args[1] == "tenant" and args[2] == "t1"
        assert args[3] == "observe" and args[4][0]["rule_id"] == "credit_budget"
        assert service.upsert_policy.call_args.kwargs["updated_by"] == "admin1"

    def test_duplicate_rule_rejected(self, client):
        entry = {"rule_id": "credit_budget",
                 "params": {"period": "day", "hard_threshold": 100}}
        res = _put(client, [entry, dict(entry)])
        assert res.status_code == 400 and "重复" in res.json()["detail"]

    def test_invalid_params_rejected(self, client):
        res = _put(client, [{"rule_id": "credit_budget", "params": {"period": "week"}}])
        assert res.status_code == 400

    def test_unknown_rule_rejected(self, client):
        res = _put(client, [{"rule_id": "no_such_rule", "params": {}}])
        assert res.status_code == 400


# ============== events ==============

class TestListEvents:
    def test_tenant_filter_and_pagination(self, client, monkeypatch):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"cnt": 1}
        cursor.fetchall.return_value = [{
            "id": 1, "tenant_id": "t1", "scope_type": "user", "scope_id": "u1",
            "user_id": "u1", "rule_id": "credit_budget", "action": "warned",
            "session_id": None, "channel_type": None, "detail": {"level": "warn"},
            "created_at": "2026-10-11 10:00:00",
        }]

        @contextmanager
        def fake_db():
            yield cursor

        monkeypatch.setattr(abuse_api, "get_db_connection", fake_db)

        res = client.get("/api/saas/abuse/events", params={"page": 1, "page_size": 20})
        body = res.json()
        assert body["success"] is True and body["total"] == 1 and len(body["items"]) == 1
        count_sql = cursor.execute.call_args_list[0].args[0]
        select_sql = cursor.execute.call_args_list[1].args[0]
        assert "tenant_id = %s" in count_sql and "ORDER BY created_at DESC" in select_sql
        assert cursor.execute.call_args_list[0].args[1] == ("t1",)

    def test_user_filter_passes_tenant_and_user(self, client, monkeypatch):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"cnt": 0}
        cursor.fetchall.return_value = []

        @contextmanager
        def fake_db():
            yield cursor

        monkeypatch.setattr(abuse_api, "get_db_connection", fake_db)
        res = client.get(
            "/api/saas/abuse/events",
            params={"scope_type": "user", "scope_id": "u1", "user_id": "u1"})
        assert res.status_code == 200
        count_params = cursor.execute.call_args_list[0].args[1]
        assert count_params == ("t1", "user", "u1", "u1")


# ============== usage ==============

class TestUsage:
    def test_invalid_window_400(self, client):
        res = client.get("/api/saas/abuse/usage", params={"window": "week"})
        assert res.status_code == 400

    def test_summary_tenant_scoped(self, client, monkeypatch):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{
            "user_id": "u1", "username": "usera", "nickname": "小明",
            "channel_source": None, "user_status": "active",
            "total_credits": 120.5, "request_count": 8,
            "last_request_at": "2026-10-11 11:00:00",
        }]

        @contextmanager
        def fake_db():
            yield cursor

        monkeypatch.setattr(abuse_api, "get_db_connection", fake_db)
        res = client.get("/api/saas/abuse/usage", params={"window": "day"})
        body = res.json()
        assert body["success"] is True and body["items"][0]["user_id"] == "u1"
        sql, params = cursor.execute.call_args.args
        assert "tenant_id = %s" in sql and params[0] == "t1"
        assert "GROUP BY r.user_id" in sql

    def test_detail_for_user(self, client, monkeypatch):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{
            "id": 1, "session_id": "web_x", "user_message": "帮我写500个场景",
            "response_message": None, "credit_cost": 3.2, "model": "qwen",
            "source_type": "chat", "created_at": "2026-10-11 11:00:00",
        }]
        fetchone_results = iter([{"total": 3.2}])

        def fetchone():
            return next(fetchone_results)

        cursor.fetchone = fetchone

        @contextmanager
        def fake_db():
            yield cursor

        monkeypatch.setattr(abuse_api, "get_db_connection", fake_db)
        res = client.get(
            "/api/saas/abuse/usage", params={"window": "hour", "user_id": "u1"})
        body = res.json()
        assert body["total_credits"] == 3.2 and len(body["items"]) == 1
        # 明细与合计两条 SQL 都带租户过滤
        for call in cursor.execute.call_args_list:
            assert "tenant_id = %s" in call.args[0]


# ============== 用户停用/启用接口 ==============

class TestUpdateUserStatus:
    def _setup_userdb(self, monkeypatch, user_row):
        userdb = MagicMock()
        userdb.get_by_id.return_value = user_row
        userdb.update_info.return_value = True
        monkeypatch.setattr(tenant_users_api, "UserDB", userdb)
        return userdb

    def test_cross_tenant_user_404(self, client, monkeypatch):
        self._setup_userdb(monkeypatch, {"user_id": "u_x", "tenant_id": "t_other",
                                         "role": "user"})
        res = client.put("/api/saas/users/u_x/status", json={"status": "suspended"})
        assert res.status_code == 404

    def test_invalid_status_rejected(self, client, monkeypatch):
        self._setup_userdb(
            monkeypatch, {"user_id": "u1", "tenant_id": "t1", "role": "user"})
        res = client.put("/api/saas/users/u1/status", json={"status": "deleted"})
        body = res.json()
        assert body["success"] is False and "active 或 suspended" in body["message"]

    def test_platform_admin_rejected(self, client, monkeypatch):
        self._setup_userdb(
            monkeypatch, {"user_id": "p1", "tenant_id": "t1", "role": "platform_admin"})
        res = client.put("/api/saas/users/p1/status", json={"status": "suspended"})
        assert res.json()["success"] is False

    def test_suspend_clears_token_cache(self, client, monkeypatch):
        userdb = self._setup_userdb(
            monkeypatch, {"user_id": "u1", "tenant_id": "t1", "role": "user"})
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"token": "tok_a"}, {"token": "tok_b"}]

        @contextmanager
        def fake_db():
            yield cursor

        monkeypatch.setattr(tenant_users_api, "get_db_connection", fake_db)

        deleted = []
        import src.core.cache_utils as cache_utils
        monkeypatch.setattr(
            cache_utils, "delete_cached",
            lambda prefix, token: deleted.append(token))

        res = client.put("/api/saas/users/u1/status", json={"status": "suspended"})
        assert res.json()["success"] is True
        userdb.update_info.assert_called_once_with("u1", status="suspended")
        assert sorted(deleted) == ["tok_a", "tok_b"]

    def test_cache_cleanup_failure_not_blocking(self, client, monkeypatch):
        userdb = self._setup_userdb(
            monkeypatch, {"user_id": "u1", "tenant_id": "t1", "role": "user"})
        cursor = MagicMock()

        def fetch_all_fail():
            raise RuntimeError("redis down")

        cursor.fetchall = fetch_all_fail

        @contextmanager
        def fake_db():
            yield cursor

        monkeypatch.setattr(tenant_users_api, "get_db_connection", fake_db)

        res = client.put("/api/saas/users/u1/status", json={"status": "suspended"})
        assert res.json()["success"] is True
        userdb.update_info.assert_called_once()
