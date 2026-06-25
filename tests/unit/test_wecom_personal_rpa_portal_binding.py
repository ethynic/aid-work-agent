"""企业微信个人账号 RPA 平台后台绑定管理 — 后端单元测试

覆盖 docs/system/wecom-personal-rpa-portal-binding-design.md 新增/补齐的能力，
以及 2026-06 改造（列表粒度由 binding → client，修复「创建 client 后列表看不到」bug）：
1. _client_public 输出包含 client_status / agent_base_url / last_heartbeat_at / account_count 等
2. GET /api/saas/wecom-personal-rpa/all_bindings
   - platform_admin 可访问，跨租户返回所有 client（含未连上来的）
   - tenant_admin 调用被 403 拒绝
   - 默认（不传 status）返回所有 client
   - status=needs_review_only 排除 active client
3. PATCH /api/saas/wecom-personal-rpa/clients/{client_id}/agent_base_url
   - 平台管理员可更新任意租户客户端
   - 不存在的 client → 404
   - 空字符串清除字段
4. list_all_bindings_rich SQL 以 wecom_rpa_clients 为基准
5. POST /api/saas/wecom-personal-rpa/clients（register_client）
   - platform_admin 带 X-Tenant-Id 代管理 → 写到目标租户
   - 普通租户管理员（自身 tenant_id）回归路径
6. 核心回归：创建 client 但没有 account/binding 时，/all_bindings 应返回该 client
7. POST /clients/{client_id}/pause /resume — client 级状态切换
"""

# ===========================================================================
# 0. 环境隔离（必须在 src.* 导入前）
# ===========================================================================

import os

os.environ.pop("DATABASE_URL", None)


def _install_db_stubs() -> None:
    try:
        from src.db import database as dbm  # noqa: WPS433
    except Exception:
        return
    if getattr(dbm, "_rpa_portal_test_stubbed", False):
        return
    dbm._rpa_portal_test_stubbed = True
    dbm.init_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]
    dbm.get_postgres_pool = lambda *a, **kw: object()  # type: ignore[assignment]
    dbm.close_postgres_pool = lambda *a, **kw: None  # type: ignore[assignment]


_install_db_stubs()

# ===========================================================================
# 1. 正式 imports
# ===========================================================================

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def app(monkeypatch) -> FastAPI:
    """最小 FastAPI app：仅挂 RPA admin 路由，mock require_admin 与 rpa_db。"""
    from src.saas.api import wecom_personal_rpa_admin as admin_mod

    # 默认 require_admin 返回 platform_admin（单个测试可覆盖）
    # 模拟真实 require_admin 的「平台管理员代管理」行为：platform_admin + X-Tenant-Id
    # 时切换到目标租户（对齐 src/saas/api/tenant_auth.py:326-333）
    def _fake_require_admin(request):
        x_tenant_id = request.headers.get("X-Tenant-Id")
        return {
            "role": "platform_admin",
            "tenant_id": x_tenant_id or None,  # 代管理时切换到目标租户
            "user_id": "u_admin",
        }

    monkeypatch.setattr(admin_mod, "require_admin", _fake_require_admin)

    test_app = FastAPI()
    test_app.include_router(admin_mod.router)
    return test_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ===========================================================================
# 1. _client_public 单元测试（纯函数，无 IO）
# ===========================================================================


def test_client_public_returns_full_field_set():
    """_client_public 必须暴露 client_status / agent_base_url / last_heartbeat_at / account_count 等。

    WHY: 平台后台「RPA 绑定管理」Tab 以 client 为粒度，需要这些字段。
    """
    from src.saas.api.wecom_personal_rpa_admin import _client_public

    c = {
        "client_id": "rpa_client_001",
        "tenant_id": "tenant_A",
        "client_name": "销售部-PC01",
        "client_status": "active",
        "agent_base_url": "https://agent.example.com",
        "last_heartbeat_at": "2026-06-24T09:59:00",
        "min_version": "1.0.0",
        "created_at": "2026-06-01T10:00:00",
        "updated_at": "2026-06-24T10:00:00",
        "account_count": 3,
        "binding_count": 5,
        "last_account_name": "张三",
    }
    out = _client_public(c)
    assert out["client_id"] == "rpa_client_001"
    assert out["tenant_id"] == "tenant_A"
    assert out["client_name"] == "销售部-PC01"
    assert out["client_status"] == "active"
    assert out["agent_base_url"] == "https://agent.example.com"
    assert out["last_heartbeat_at"] == "2026-06-24T09:59:00"
    assert out["min_version"] == "1.0.0"
    assert out["account_count"] == 3
    assert out["binding_count"] == 5
    assert out["last_account_name"] == "张三"


def test_client_public_handles_missing_aggregate_fields():
    """聚合字段缺失时默认为 0（不抛 KeyError）。

    WHY: 旧版 client 可能没有 account_count（如手写 mock），调用方不应崩溃。
    """
    from src.saas.api.wecom_personal_rpa_admin import _client_public

    c = {
        "client_id": "rpa_client_002",
        "tenant_id": "tenant_B",
        "client_status": "active",
    }
    out = _client_public(c)
    assert out["account_count"] == 0
    assert out["binding_count"] == 0
    assert out["last_account_name"] is None


# ===========================================================================
# 2. GET /all_bindings — 跨租户 + 权限隔离 + 默认全部
# ===========================================================================


def test_list_all_bindings_platform_admin_ok(client, monkeypatch):
    """platform_admin 可访问 /all_bindings，返回跨租户 client（含聚合字段）。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    fake_rows = [
        {
            "client_id": "rpa_client_a",
            "tenant_id": "tenant_A",
            "client_name": "客户端A",
            "client_status": "active",
            "agent_base_url": "https://prod.example.com",
            "last_heartbeat_at": "2026-06-24T09:00:00",
            "min_version": "1.0.0",
            "created_at": "2026-06-01T10:00:00",
            "updated_at": "2026-06-24T10:00:00",
            "account_count": 2,
            "binding_count": 3,
            "last_account_name": "张三",
        },
        {
            "client_id": "rpa_client_b",
            "tenant_id": "tenant_B",
            "client_name": "客户端B",
            "client_status": "disabled",
            "agent_base_url": None,
            "last_heartbeat_at": None,
            "min_version": "1.0.0",
            "created_at": "2026-06-02T10:00:00",
            "updated_at": "2026-06-24T10:00:00",
            "account_count": 0,
            "binding_count": 0,
            "last_account_name": None,
        },
    ]
    monkeypatch.setattr(rpa_db, "list_all_bindings_rich", lambda **kw: fake_rows)

    resp = client.get("/api/saas/wecom-personal-rpa/all_bindings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert len(body["data"]) == 2
    tenants = {c["tenant_id"] for c in body["data"]}
    assert tenants == {"tenant_A", "tenant_B"}  # 跨租户可见
    # 字段透传
    a = next(c for c in body["data"] if c["client_id"] == "rpa_client_a")
    assert a["agent_base_url"] == "https://prod.example.com"
    assert a["client_name"] == "客户端A"
    assert a["account_count"] == 2
    assert a["binding_count"] == 3
    assert a["last_account_name"] == "张三"


def test_list_all_bindings_tenant_admin_forbidden(client, monkeypatch):
    """tenant_admin 调用 /all_bindings 必须被 403 拒绝。

    WHY: 跨租户视角是平台管理员专属，租户管理员不能看到其他租户的 client。
    """
    from src.saas.api import wecom_personal_rpa_admin as admin_mod

    def _tenant_admin(request):
        return {"role": "tenant_admin", "tenant_id": "tenant_A", "user_id": "u_t"}

    monkeypatch.setattr(admin_mod, "require_admin", _tenant_admin)

    resp = client.get("/api/saas/wecom-personal-rpa/all_bindings")
    assert resp.status_code == 403


def test_list_all_bindings_passes_filters(client, monkeypatch):
    """status / tenant_id 过滤参数应透传给 db 层。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    captured = {}

    def _capture(tenant_id_filter=None, status_filter=None):
        captured["tenant_id_filter"] = tenant_id_filter
        captured["status_filter"] = status_filter
        return []

    monkeypatch.setattr(rpa_db, "list_all_bindings_rich", _capture)

    client.get("/api/saas/wecom-personal-rpa/all_bindings?status=disabled&tenant_id=tenant_X")
    assert captured["status_filter"] == "disabled"
    assert captured["tenant_id_filter"] == "tenant_X"


def test_list_all_bindings_default_returns_all_including_unconnected(client, monkeypatch):
    """【核心 bug 回归】默认（不传 status）应返回所有 client，包括从未连上来的。

    WHY: 这是本次改造的核心目标——创建 client 后即使 client 还没连上来、
    没有 account/binding，也应该能在平台后台列表看到。如果数据源仍以
    wecom_rpa_conversation_bindings 为基准，未连上来的 client 会被漏掉。
    """
    from src.channels.wecom_personal_rpa import db as rpa_db

    fake_rows = [
        {
            "client_id": "rpa_client_new",
            "tenant_id": "tenant_A",
            "client_name": "新建未连接",
            "client_status": "active",
            "agent_base_url": None,
            "last_heartbeat_at": None,  # 从未连上来
            "min_version": "1.0.0",
            "created_at": "2026-06-25T10:00:00",
            "updated_at": "2026-06-25T10:00:00",
            "account_count": 0,  # 还没有 account
            "binding_count": 0,  # 还没有 binding
            "last_account_name": None,
        },
    ]
    monkeypatch.setattr(rpa_db, "list_all_bindings_rich", lambda **kw: fake_rows)

    resp = client.get("/api/saas/wecom-personal-rpa/all_bindings")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 1
    row = body["data"][0]
    assert row["client_id"] == "rpa_client_new"
    assert row["last_heartbeat_at"] is None  # 未连接
    assert row["account_count"] == 0


def test_list_all_bindings_needs_review_excludes_active(client, monkeypatch):
    """status=needs_review_only 排除 active client，显示其他状态。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    captured = {}

    def _capture(tenant_id_filter=None, status_filter=None):
        captured["status_filter"] = status_filter
        # 模拟 db 层过滤：needs_review_only 时排除 active
        all_rows = [
            {"client_id": "c1", "tenant_id": "t1", "client_status": "active", "account_count": 0, "binding_count": 0},
            {"client_id": "c2", "tenant_id": "t1", "client_status": "disabled", "account_count": 0, "binding_count": 0},
        ]
        if status_filter == "needs_review_only":
            return [r for r in all_rows if r["client_status"] != "active"]
        return all_rows

    monkeypatch.setattr(rpa_db, "list_all_bindings_rich", _capture)

    resp = client.get("/api/saas/wecom-personal-rpa/all_bindings?status=needs_review_only")
    body = resp.json()
    ids = [c["client_id"] for c in body["data"]]
    assert "c2" in ids
    assert "c1" not in ids  # active 被排除
    assert captured["status_filter"] == "needs_review_only"


# ===========================================================================
# 3. PATCH /clients/{client_id}/agent_base_url
# ===========================================================================


def test_update_agent_base_url_ok(client, monkeypatch):
    """平台管理员可更新任意客户端的 agent_base_url，并写审计。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_client", lambda cid: {
        "id": cid, "tenant_id": "tenant_A", "name": "客户端A",
    })
    updated = {"called": False}

    def _upd(tenant_id, client_id, agent_base_url):
        updated["called"] = True
        updated["args"] = (tenant_id, client_id, agent_base_url)
        return True

    monkeypatch.setattr(rpa_db, "update_client_agent_base_url", _upd)
    audit_written = {"called": False}
    monkeypatch.setattr(rpa_db, "write_audit", lambda **kw: audit_written.__setitem__("called", True) or "audit_id")

    resp = client.patch(
        "/api/saas/wecom-personal-rpa/clients/rpa_client_A/agent_base_url",
        json={"agent_base_url": "https://prod.example.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["agent_base_url"] == "https://prod.example.com"
    assert updated["called"] is True
    assert updated["args"] == ("tenant_A", "rpa_client_A", "https://prod.example.com")
    assert audit_written["called"] is True


def test_update_agent_base_url_empty_string_clears(client, monkeypatch):
    """空字符串清除字段（传 None 给 db 层）。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_client", lambda cid: {
        "id": cid, "tenant_id": "tenant_A", "name": "客户端A",
    })
    captured = {}
    monkeypatch.setattr(rpa_db, "update_client_agent_base_url",
                        lambda tenant_id, client_id, agent_base_url: captured.update({"v": agent_base_url}) or True)
    monkeypatch.setattr(rpa_db, "write_audit", lambda **kw: "audit_id")

    resp = client.patch(
        "/api/saas/wecom-personal-rpa/clients/rpa_client_A/agent_base_url",
        json={"agent_base_url": "   "},
    )
    assert resp.status_code == 200
    assert captured["v"] is None  # 空白被清除


def test_update_agent_base_url_not_found(client, monkeypatch):
    """不存在的客户端 → 404。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_client", lambda cid: None)
    resp = client.patch(
        "/api/saas/wecom-personal-rpa/clients/rpa_missing/agent_base_url",
        json={"agent_base_url": "https://x.example.com"},
    )
    assert resp.status_code == 404


def test_update_agent_base_url_tenant_admin_cross_tenant_forbidden(client, monkeypatch):
    """租户管理员更新其他租户的客户端 → 403。"""
    from src.saas.api import wecom_personal_rpa_admin as admin_mod
    from src.channels.wecom_personal_rpa import db as rpa_db

    def _tenant_admin(request):
        return {"role": "tenant_admin", "tenant_id": "tenant_A", "user_id": "u_t"}

    monkeypatch.setattr(admin_mod, "require_admin", _tenant_admin)
    monkeypatch.setattr(rpa_db, "get_client", lambda cid: {
        "id": cid, "tenant_id": "tenant_B", "name": "客户端B",
    })

    resp = client.patch(
        "/api/saas/wecom-personal-rpa/clients/rpa_client_B/agent_base_url",
        json={"agent_base_url": "https://x.example.com"},
    )
    assert resp.status_code == 403


# ===========================================================================
# 4. db 层 SQL 契约（不连真实库，只验证参数透传与 SQL 以 clients 为基准）
# ===========================================================================


def test_list_all_bindings_rich_query_uses_clients_as_base(monkeypatch):
    """list_all_bindings_rich 的 SQL 必须以 wecom_rpa_clients 为基准（FROM wecom_rpa_clients）。

    WHY: 这是「创建 client 即可见」bug 修复的核心保证。如果 SQL FROM 仍是
    wecom_rpa_conversation_bindings，未连上来的 client 仍会漏掉。
    """
    from src.channels.wecom_personal_rpa import db as rpa_db

    captured = {}

    class _FakeCursor:
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return []

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def cursor(self):
            return _FakeCursor()

    monkeypatch.setattr(rpa_db, "get_db_connection", lambda: _FakeConn())

    rpa_db.list_all_bindings_rich()
    sql = captured["sql"]
    # 关键断言：FROM wecom_rpa_clients（不再是 wecom_rpa_conversation_bindings）
    assert "FROM wecom_rpa_clients c" in sql
    # 子查询聚合账号数 / 绑定数
    assert "account_count" in sql
    assert "binding_count" in sql
    # client_status / agent_base_url / last_heartbeat_at 字段都在
    assert "client_status" in sql
    assert "agent_base_url" in sql
    assert "last_heartbeat_at" in sql  # last_seen_at 被 alias 成 last_heartbeat_at


def test_list_all_bindings_rich_needs_review_filter_excludes_active(monkeypatch):
    """status_filter=needs_review_only 时 SQL WHERE 应包含 c.status <> 'active'。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    captured = {}

    class _FakeCursor:
        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params

        def fetchall(self):
            return []

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def cursor(self):
            return _FakeCursor()

    monkeypatch.setattr(rpa_db, "get_db_connection", lambda: _FakeConn())

    rpa_db.list_all_bindings_rich(status_filter="needs_review_only")
    sql = captured["sql"]
    assert "c.status <> 'active'" in sql


def test_update_client_agent_base_url_sql_contains_field(monkeypatch):
    """update_client_agent_base_url 的 SQL 必须更新 agent_base_url 列。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    captured = {}

    class _FakeCursor:
        def __init__(self):
            self.rowcount = 1

        def execute(self, sql, params=None):
            captured["sql"] = sql
            captured["params"] = params

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def commit(self):
            pass

        def cursor(self):
            return _FakeCursor()

    monkeypatch.setattr(rpa_db, "get_db_connection", lambda: _FakeConn())

    ok = rpa_db.update_client_agent_base_url("tenant_A", "rpa_client_A", "https://prod.example.com")
    assert ok is True
    assert "agent_base_url" in captured["sql"]
    assert captured["params"] == ("https://prod.example.com", "rpa_client_A", "tenant_A")


# ===========================================================================
# 5. POST /clients（register_client）— 平台管理员代管理 + 租户回归
# ===========================================================================


def test_register_client_platform_admin_with_x_tenant_id_writes_to_target_tenant(client, monkeypatch):
    """platform_admin 带 X-Tenant-Id 调用 register_client → client 记录写到目标租户。"""
    from src.channels.wecom_personal_rpa import db as rpa_db
    from src.saas.api import wecom_personal_rpa_admin as admin_mod

    monkeypatch.setattr(admin_mod, "encrypt_secret", lambda s: "enc_stub_xyz")

    captured = {}

    def _create_client(tenant_id, client_id, name, encrypted_secret, min_version, user_id):
        captured["tenant_id"] = tenant_id
        captured["client_id"] = client_id
        captured["name"] = name
        captured["encrypted_secret"] = encrypted_secret
        captured["min_version"] = min_version
        captured["user_id"] = user_id
        return {"id": client_id, "tenant_id": tenant_id, "name": name}

    monkeypatch.setattr(rpa_db, "create_client", _create_client)
    monkeypatch.setattr(rpa_db, "write_audit", lambda **kw: "audit_id")

    from src.saas.db import channel_config_db as cfg_db_mod
    monkeypatch.setattr(cfg_db_mod.ChannelConfigDB, "create", classmethod(lambda cls, **kw: {"config_id": "cfg_stub"}))

    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients",
        headers={"X-Tenant-Id": "tenant_target_001"},
        json={"name": "销售一组-客户机01", "min_version": "1.0.0", "subagent_type": "trade-specialist"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert "client_secret" in data
    assert len(data["client_secret"]) > 0
    assert data["client_id"].startswith("rpa_client_")
    assert captured["tenant_id"] == "tenant_target_001"
    assert captured["name"] == "销售一组-客户机01"
    assert captured["encrypted_secret"] == "enc_stub_xyz"
    assert "encrypted_secret" not in data


def test_register_client_returns_secret_warning_once_only(client, monkeypatch):
    """register_client 响应必须包含 secret_warning 提醒调用方密钥仅显示一次。"""
    from src.channels.wecom_personal_rpa import db as rpa_db
    from src.saas.api import wecom_personal_rpa_admin as admin_mod
    from src.saas.db import channel_config_db as cfg_db_mod

    monkeypatch.setattr(admin_mod, "encrypt_secret", lambda s: "enc_stub")
    monkeypatch.setattr(rpa_db, "create_client", lambda **kw: {"id": kw["client_id"], "tenant_id": kw["tenant_id"]})
    monkeypatch.setattr(rpa_db, "write_audit", lambda **kw: "audit_id")
    monkeypatch.setattr(cfg_db_mod.ChannelConfigDB, "create", classmethod(lambda cls, **kw: {"config_id": "cfg_stub"}))

    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients",
        headers={"X-Tenant-Id": "tenant_A"},
        json={"name": "客户端X"},
    )
    body = resp.json()
    assert body["success"] is True
    assert "仅" in body["data"]["secret_warning"] or "一次" in body["data"]["secret_warning"]


def test_register_client_tenant_admin_no_x_tenant_id_writes_to_own_tenant(client, monkeypatch):
    """普通租户管理员（不传 X-Tenant-Id）调 register_client → 写到自己租户（既有路径回归）。"""
    from src.channels.wecom_personal_rpa import db as rpa_db
    from src.saas.api import wecom_personal_rpa_admin as admin_mod
    from src.saas.db import channel_config_db as cfg_db_mod

    def _tenant_admin(request):
        x_tid = request.headers.get("X-Tenant-Id")
        assert x_tid is None, "此用例验证不带 X-Tenant-Id 的租户回归路径"
        return {"role": "tenant_admin", "tenant_id": "tenant_self_001", "user_id": "u_self"}

    monkeypatch.setattr(admin_mod, "require_admin", _tenant_admin)
    monkeypatch.setattr(admin_mod, "encrypt_secret", lambda s: "enc_self")
    captured = {}
    monkeypatch.setattr(
        rpa_db,
        "create_client",
        lambda **kw: captured.update({"tenant_id": kw["tenant_id"]}) or {"id": kw["client_id"]},
    )
    monkeypatch.setattr(rpa_db, "write_audit", lambda **kw: "audit_id")
    monkeypatch.setattr(cfg_db_mod.ChannelConfigDB, "create", classmethod(lambda cls, **kw: {"config_id": "cfg"}))

    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients",
        json={"name": "租户自建客户端"},
    )
    assert resp.status_code == 200
    assert captured["tenant_id"] == "tenant_self_001"


# ===========================================================================
# 6. POST /clients/{client_id}/rotate-secret — 平台管理员代管理 + 跨租户隔离
# ===========================================================================


def test_rotate_secret_platform_admin_with_x_tenant_id_writes_to_target_tenant(client, monkeypatch):
    """platform_admin 带 X-Tenant-Id 调 rotate → 写到 X-Tenant-Id 指定的目标租户的 client。"""
    from src.channels.wecom_personal_rpa import db as rpa_db
    from src.saas.api import wecom_personal_rpa_admin as admin_mod

    monkeypatch.setattr(admin_mod, "encrypt_secret", lambda s: "enc_new_stub")

    monkeypatch.setattr(rpa_db, "get_client", lambda cid: {
        "id": cid, "tenant_id": "tenant_target_001", "name": "客户端-target",
    })
    captured = {}

    def _rotate(tenant_id, client_id, encrypted):
        captured["tenant_id"] = tenant_id
        captured["client_id"] = client_id
        captured["encrypted"] = encrypted
        return True

    monkeypatch.setattr(rpa_db, "rotate_secret", _rotate)
    monkeypatch.setattr(rpa_db, "write_audit", lambda **kw: "audit_id")

    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients/rpa_client_target/rotate-secret",
        headers={"X-Tenant-Id": "tenant_target_001"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert "client_secret" in data
    assert len(data["client_secret"]) > 0
    assert data["client_id"] == "rpa_client_target"
    assert captured["tenant_id"] == "tenant_target_001"
    assert captured["client_id"] == "rpa_client_target"
    assert captured["encrypted"] == "enc_new_stub"
    assert "encrypted_secret" not in data


def test_rotate_secret_cross_tenant_forbidden(client, monkeypatch):
    """X-Tenant-Id=A 但 client 属于 B → 403（跨租户隔离）。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_client", lambda cid: {
        "id": cid, "tenant_id": "tenant_B", "name": "客户端B",
    })
    rotate_called = {"v": False}
    monkeypatch.setattr(rpa_db, "rotate_secret", lambda *a, **kw: rotate_called.__setitem__("v", True) or True)

    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients/rpa_client_B/rotate-secret",
        headers={"X-Tenant-Id": "tenant_A"},
    )
    assert resp.status_code == 403
    assert rotate_called["v"] is False


def test_rotate_secret_client_not_found(client, monkeypatch):
    """轮换不存在的 client → 404。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_client", lambda cid: None)
    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients/rpa_missing/rotate-secret",
        headers={"X-Tenant-Id": "tenant_A"},
    )
    assert resp.status_code == 404


def test_rotate_secret_returns_warning_once_only(client, monkeypatch):
    """rotate 响应必须包含 secret_warning 提醒新密钥仅显示一次。"""
    from src.channels.wecom_personal_rpa import db as rpa_db
    from src.saas.api import wecom_personal_rpa_admin as admin_mod

    monkeypatch.setattr(admin_mod, "encrypt_secret", lambda s: "enc_x")
    monkeypatch.setattr(rpa_db, "get_client", lambda cid: {
        "id": cid, "tenant_id": "tenant_A", "name": "客户端A",
    })
    monkeypatch.setattr(rpa_db, "rotate_secret", lambda *a, **kw: True)
    monkeypatch.setattr(rpa_db, "write_audit", lambda **kw: "audit_id")

    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients/rpa_client_A/rotate-secret",
        headers={"X-Tenant-Id": "tenant_A"},
    )
    body = resp.json()
    assert body["success"] is True
    assert "仅" in body["data"]["secret_warning"] or "一次" in body["data"]["secret_warning"]


# ===========================================================================
# 7. POST /clients/{client_id}/pause /resume — client 级状态切换
# ===========================================================================


def test_pause_client_active_to_disabled(client, monkeypatch):
    """暂停 active client → status 变 disabled，写审计。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_client", lambda cid: {
        "id": cid, "tenant_id": "tenant_A", "name": "客户端A", "status": "active",
    })
    captured = {}

    def _upd(tenant_id, client_id, status):
        captured["tenant_id"] = tenant_id
        captured["client_id"] = client_id
        captured["status"] = status
        return True

    monkeypatch.setattr(rpa_db, "update_client_status", _upd)
    audit_written = {"v": False}
    monkeypatch.setattr(rpa_db, "write_audit", lambda **kw: audit_written.__setitem__("v", True) or "audit_id")

    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients/rpa_client_A/pause",
        headers={"X-Tenant-Id": "tenant_A"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["client_status"] == "disabled"
    assert captured["status"] == "disabled"
    assert audit_written["v"] is True


def test_pause_client_idempotent_when_already_disabled(client, monkeypatch):
    """已是 disabled 的 client 再次暂停 → 幂等成功，不调用 update。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_client", lambda cid: {
        "id": cid, "tenant_id": "tenant_A", "status": "disabled",
    })
    upd_called = {"v": False}
    monkeypatch.setattr(rpa_db, "update_client_status", lambda *a, **kw: upd_called.__setitem__("v", True) or True)

    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients/rpa_client_A/pause",
        headers={"X-Tenant-Id": "tenant_A"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["client_status"] == "disabled"
    assert upd_called["v"] is False  # 未执行更新


def test_resume_client_disabled_to_active(client, monkeypatch):
    """恢复 disabled client → status 变 active。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_client", lambda cid: {
        "id": cid, "tenant_id": "tenant_A", "status": "disabled",
    })
    captured = {}
    monkeypatch.setattr(rpa_db, "update_client_status",
                        lambda tenant_id, client_id, status: captured.update({"status": status}) or True)
    monkeypatch.setattr(rpa_db, "write_audit", lambda **kw: "audit_id")

    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients/rpa_client_A/resume",
        headers={"X-Tenant-Id": "tenant_A"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["client_status"] == "active"
    assert captured["status"] == "active"


def test_pause_client_cross_tenant_forbidden(client, monkeypatch):
    """X-Tenant-Id=A 但 client 属于 B → 403。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_client", lambda cid: {
        "id": cid, "tenant_id": "tenant_B", "status": "active",
    })
    monkeypatch.setattr(rpa_db, "update_client_status", lambda *a, **kw: True)

    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients/rpa_client_B/pause",
        headers={"X-Tenant-Id": "tenant_A"},
    )
    assert resp.status_code == 403


def test_pause_client_not_found(client, monkeypatch):
    """暂停不存在的 client → 404。"""
    from src.channels.wecom_personal_rpa import db as rpa_db

    monkeypatch.setattr(rpa_db, "get_client", lambda cid: None)
    resp = client.post(
        "/api/saas/wecom-personal-rpa/clients/rpa_missing/pause",
        headers={"X-Tenant-Id": "tenant_A"},
    )
    assert resp.status_code == 404
