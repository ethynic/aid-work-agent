"""lyjy_employment / sccy_policy P2 租户后台 + 平台管理员 API 测试
（TestClient + require_admin 依赖覆写，照 hongtao_shop/test_api.py 范式）。

覆盖：404 门控（未开通不可见）、PATCH 范围校验（非法值拒绝/空范围拒绝）、
立即运行入账本、runs 明细、admin grant/revoke 幂等与在队 run 取消、
连接配置 Fernet 加密往返（断言解密字段一致，不回显不落日志）、
非平台管理员 403、connection-sources 清单自然带上两模块。
"""

import json
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.saas.api.tenant_auth import require_admin
from src.services.content_sync.api import router as content_sync_router
from src.tenant_custom.lyjy_employment.api import router as lyjy_router
from src.tenant_custom.sccy_policy.api import router as sccy_router

CASES = [
    ("lyjy", lyjy_router, "lyjy_employment", "/api/saas/lyjy-employment"),
    ("sccy", sccy_router, "sccy_policy", "/api/saas/sccy-policy"),
]


@pytest.fixture()
def client_factory(tenant_id):
    def _make(router, role="tenant_admin"):
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_admin] = lambda: {
            "tenant_id": tenant_id, "role": role, "user_id": "admin-x",
        }
        return TestClient(app)

    return _make


@pytest.fixture()
def tenant_row(tenant_id, require_db):
    """trigger/grant 余额预检与租户存在性校验所需的 tenants 行（须带正余额）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tenants (tenant_id, company_name, credit_balance)
            VALUES (%s, '数据源授权测试租户', 10000)
            ON CONFLICT (tenant_id) DO UPDATE
            SET credit_balance = EXCLUDED.credit_balance
            """,
            (tenant_id,),
        )
        conn.commit()
    yield tenant_id


def _create_source(tenant_id, module, enabled=True, interval=24, selection="all"):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_content_sync_sources
                (tenant_id, module, enabled, sync_interval_hours, selection_mode, selected_ids)
            VALUES (%s, %s, %s, %s, %s, NULL)
            ON CONFLICT (tenant_id, module) DO UPDATE
            SET enabled = EXCLUDED.enabled,
                sync_interval_hours = EXCLUDED.sync_interval_hours,
                selection_mode = EXCLUDED.selection_mode,
                selected_ids = NULL
            """,
            (tenant_id, module, enabled, interval, selection),
        )
        conn.commit()


def _source_row(tenant_id, module):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_content_sync_sources WHERE tenant_id = %s AND module = %s",
            (tenant_id, module),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


# ==================== 租户端：404 门控 / GET+PATCH / 范围校验 ====================


@pytest.mark.parametrize("label,router,module,base", CASES)
def test_source_404_before_init(label, router, module, base, client_factory):
    client = client_factory(router)
    assert client.get(f"{base}/source").status_code == 404
    assert client.patch(f"{base}/source", json={"enabled": False}).status_code == 404
    assert client.post(f"{base}/source/trigger").status_code == 404


@pytest.mark.parametrize("label,router,module,base", CASES)
def test_source_get_and_patch(label, router, module, base, client_factory, tenant_id):
    client = client_factory(router)
    _create_source(tenant_id, module)
    resp = client.get(f"{base}/source")
    assert resp.status_code == 200
    body = resp.json()["source"]
    assert body["enabled"] is True and body["sync_interval_hours"] == 24
    assert body["selection_mode"] == "all"
    assert body["connection_configured"] is False

    resp = client.patch(f"{base}/source", json={"sync_interval_hours": 12, "enabled": False})
    assert resp.status_code == 200
    body = resp.json()["source"]
    assert body["sync_interval_hours"] == 12 and body["enabled"] is False

    assert client.patch(f"{base}/source", json={"sync_interval_hours": 0}).status_code == 422


@pytest.mark.parametrize("label,router,module,base", CASES)
def test_source_patch_scope_and_validation(label, router, module, base, client_factory, tenant_id):
    client = client_factory(router)
    _create_source(tenant_id, module)

    if module == "lyjy_employment":
        # 范围开关：合法子集保存为 ids 模式
        resp = client.patch(f"{base}/source", json={"selected_ids": ["qs_jobs", "qs_guide"]})
        assert resp.status_code == 200
        body = resp.json()["source"]
        assert body["selection_mode"] == "ids" and body["selected_ids"] == ["qs_jobs", "qs_guide"]

        # 切回全选清空范围
        resp = client.patch(f"{base}/source", json={"selection_mode": "all"})
        assert resp.status_code == 200
        assert resp.json()["source"]["selected_ids"] == []

        # 非法值拒绝（不在六分类映射表内）
        resp = client.patch(f"{base}/source", json={"selected_ids": ["qs_resume", "bogus"]})
        assert resp.status_code == 400
        # 空范围拒绝（全关无业务意义且会软删全部已入库内容）
        resp = client.patch(f"{base}/source", json={"selected_ids": []})
        assert resp.status_code == 400
        # selection_mode 与 selected_ids 组合校验
        assert client.patch(
            f"{base}/source", json={"selection_mode": "ids"}
        ).status_code == 400
        assert client.patch(
            f"{base}/source", json={"selection_mode": "bad", "selected_ids": ["qs_jobs"]}
        ).status_code == 400
        # 范围上限护栏
        assert client.patch(
            f"{base}/source", json={"selected_ids": [str(i) for i in range(101)]}
        ).status_code == 422
    else:
        # sccy 无范围开关：范围字段不改变 selection（保持 all）
        resp = client.patch(f"{base}/source", json={"selected_ids": ["qs_jobs"]})
        assert resp.status_code == 200
        body = resp.json()["source"]
        assert body["selection_mode"] == "all" and body["selected_ids"] == []


# ==================== 租户端：立即运行 + 账本 ====================


@pytest.mark.parametrize("label,router,module,base", CASES)
def test_trigger_and_runs(label, router, module, base, client_factory, tenant_id, tenant_row):
    client = client_factory(router)
    _create_source(tenant_id, module)
    resp = client.post(f"{base}/source/trigger")
    assert resp.status_code == 200
    run = resp.json()["run"]
    assert run["status"] == "queued"

    # 在队去重：再次触发返回同一 run
    resp = client.post(f"{base}/source/trigger")
    assert resp.json()["run"]["run_id"] == run["run_id"]

    # runs 列表倒序 + 详情
    resp = client.get(f"{base}/runs")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) == 1 and runs[0]["id"] == run["run_id"]

    resp = client.get(f"{base}/runs/{run['run_id']}")
    assert resp.status_code == 200
    assert resp.json()["run"]["trigger_type"] == "manual"
    assert client.get(f"{base}/runs/999999").status_code == 404


# ==================== 平台管理员：grant/revoke（幂等/加密/取消在队） ====================


def _grant_body(tenant):
    return {
        "tenant_id": tenant,
        "db_host": "192.0.2.10",
        "db_port": 3307,
        "db_database": "gt_test",
        "db_user": "ro_user",
        "db_password": "pw-secret-值",
    }


@pytest.mark.parametrize("label,router,module,base", CASES)
def test_admin_grant_revoke_roundtrip(
    label, router, module, base, client_factory, tenant_id, tenant_row
):
    platform = client_factory(router, role="platform_admin")
    tenant_view = client_factory(router, role="tenant_admin")

    # 非平台管理员 403
    assert tenant_view.get(f"{base}/admin/source", params={"tenant_id": tenant_id}).status_code == 403
    assert tenant_view.post(f"{base}/admin/grant", json=_grant_body(tenant_id)).status_code == 403

    # grant：建源行 + 连接配置加密落库
    resp = platform.post(f"{base}/admin/grant", json=_grant_body(tenant_id))
    assert resp.status_code == 200
    body = resp.json()["source"]
    assert body["connection_configured"] is True

    row = _source_row(tenant_id, module)
    assert row is not None and row["connection_encrypted"]
    # 加密往返：解密字段与录入一致（不打印凭据）
    from src.tenant_custom.lyjy_employment.fetcher import load_connection_config

    config = load_connection_config(row)
    assert config["host"] == "192.0.2.10" and config["port"] == 3307
    assert config["database"] == "gt_test" and config["user"] == "ro_user"
    assert config["password"] == "pw-secret-值"
    # 密文不回显在任何接口响应里
    assert "connection_encrypted" not in json.dumps(resp.json())

    # grant 幂等：重复开通仍一行，运营配置保留、连接配置更新
    _create_source(tenant_id, module, enabled=False, interval=12)
    body2 = dict(_grant_body(tenant_id), db_host="192.0.2.11")
    resp = platform.post(f"{base}/admin/grant", json=body2)
    assert resp.status_code == 200
    row = _source_row(tenant_id, module)
    assert row["enabled"] is False and row["sync_interval_hours"] == 12
    assert load_connection_config(row)["host"] == "192.0.2.11"

    # admin/source 查询
    resp = platform.get(f"{base}/admin/source", params={"tenant_id": tenant_id})
    assert resp.status_code == 200 and resp.json()["granted"] is True

    # revoke：删源行 + 同事务取消在队 run
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status) "
            "VALUES (%s, %s, 'manual', 'queued')",
            (tenant_id, module),
        )
        conn.commit()
    resp = platform.post(f"{base}/admin/revoke", json={"tenant_id": tenant_id})
    assert resp.status_code == 200 and resp.json()["cancelled_runs"] == 1
    assert _source_row(tenant_id, module) is None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM bs_content_sync_runs WHERE tenant_id = %s AND module = %s",
            (tenant_id, module),
        )
        assert cursor.fetchone()["status"] == "interrupted"

    # revoke 后租户侧立即 404
    assert tenant_view.get(f"{base}/source").status_code == 404

    # revoke 不存在租户的源：幂等成功（0 行取消）
    resp = platform.post(f"{base}/admin/revoke", json={"tenant_id": f"none_{uuid.uuid4().hex[:8]}"})
    assert resp.status_code == 200 and resp.json()["cancelled_runs"] == 0


def test_admin_grant_rejects_unknown_tenant(client_factory):
    platform = client_factory(lyjy_router, role="platform_admin")
    body = _grant_body(f"no_such_{uuid.uuid4().hex[:8]}")
    resp = platform.post("/api/saas/lyjy-employment/admin/grant", json=body)
    assert resp.status_code == 404


def test_admin_grant_rejects_missing_fields(client_factory, tenant_row):
    platform = client_factory(lyjy_router, role="platform_admin")
    body = _grant_body(tenant_row)
    body.pop("db_password")
    assert platform.post(
        "/api/saas/lyjy-employment/admin/grant", json=body
    ).status_code == 422


# ==================== connection-sources 清单 ====================


@pytest.mark.parametrize("label,router,module,base", CASES)
def test_connection_sources_lists_module(label, router, module, base, client_factory, tenant_id):
    client = client_factory(content_sync_router)
    _create_source(tenant_id, module)
    resp = client.get("/api/saas/connection-sources")
    assert resp.status_code == 200
    modules = {s["module"] for s in resp.json()["sources"]}
    assert module in modules
