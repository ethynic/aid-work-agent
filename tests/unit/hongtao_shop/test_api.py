"""hongtao_shop P2 租户后台 API 测试（TestClient + require_admin 依赖覆写）。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.saas.api.tenant_auth import require_admin
from src.tenant_custom.hongtao_shop.api import router


@pytest.fixture()
def client(tenant_id):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: {
        "tenant_id": tenant_id, "role": "tenant_admin", "user_id": "admin-x",
    }
    return TestClient(app)


def _create_source(tenant_id, enabled=True, interval=24, selection="all", selected=None):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_content_sync_sources
                (tenant_id, module, enabled, sync_interval_hours, selection_mode, selected_ids)
            VALUES (%s, 'hongtao_shop', %s, %s, %s, %s)
            ON CONFLICT (tenant_id, module) DO UPDATE
            SET enabled = EXCLUDED.enabled,
                sync_interval_hours = EXCLUDED.sync_interval_hours,
                selection_mode = EXCLUDED.selection_mode,
                selected_ids = EXCLUDED.selected_ids
            """,
            (tenant_id, enabled, interval, selection,
             json.dumps(selected) if selected else None),
        )
        conn.commit()


def test_source_404_before_init(client):
    resp = client.get("/api/saas/hongtao-shop/source")
    assert resp.status_code == 404


def test_source_get_and_patch(client, tenant_id):
    _create_source(tenant_id)
    resp = client.get("/api/saas/hongtao-shop/source")
    assert resp.status_code == 200
    body = resp.json()["source"]
    assert body["enabled"] is True and body["sync_interval_hours"] == 24
    assert body["selection_mode"] == "all" and body["selected_ids"] == []

    # PATCH：改频率 + 挑选白名单
    resp = client.patch(
        "/api/saas/hongtao-shop/source",
        json={"sync_interval_hours": 12, "selection_mode": "ids", "selected_ids": ["1", "2"]},
    )
    assert resp.status_code == 200
    body = resp.json()["source"]
    assert body["sync_interval_hours"] == 12
    assert body["selection_mode"] == "ids" and body["selected_ids"] == ["1", "2"]

    # 切回 all 清空白名单
    resp = client.patch("/api/saas/hongtao-shop/source", json={"selection_mode": "all"})
    assert resp.status_code == 200
    assert resp.json()["source"]["selected_ids"] == []

    # 单独传 selected_ids 等价于 ids 模式（不再静默忽略）
    resp = client.patch("/api/saas/hongtao-shop/source", json={"selected_ids": ["7"]})
    assert resp.status_code == 200
    body = resp.json()["source"]
    assert body["selection_mode"] == "ids" and body["selected_ids"] == ["7"]

    # 白名单数量上限（防超大数组进 JSONB 逐行判定）
    resp = client.patch(
        "/api/saas/hongtao-shop/source", json={"selected_ids": [str(i) for i in range(2001)]}
    )
    assert resp.status_code == 422

    # 非法值校验
    assert client.patch(
        "/api/saas/hongtao-shop/source", json={"selection_mode": "bad"}
    ).status_code == 400
    assert client.patch(
        "/api/saas/hongtao-shop/source", json={"selection_mode": "ids"}
    ).status_code == 400
    assert client.patch(
        "/api/saas/hongtao-shop/source", json={"sync_interval_hours": 0}
    ).status_code == 422


def test_trigger_and_runs(client, tenant_id, monkeypatch):
    _create_source(tenant_id)
    from src.tenant_custom.hongtao_shop import api as api_mod

    monkeypatch.setattr(
        api_mod, "_load_source_row", lambda t: {"enabled": True}
    )
    monkeypatch.setattr(
        "src.tenant_custom.hongtao_shop.service.hongtao_shop_service.trigger_sync",
        lambda t, trigger_type="manual": {"run_id": 101, "status": "queued"},
    )
    resp = client.post("/api/saas/hongtao-shop/source/trigger")
    assert resp.status_code == 200
    assert resp.json()["run"] == {"run_id": 101, "status": "queued"}

    # runs 列表：插入两条 run
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for status in ("success", "partial_failed"):
            cursor.execute(
                """
                INSERT INTO bs_content_sync_runs
                    (tenant_id, module, trigger_type, status, new_count)
                VALUES (%s, 'hongtao_shop', 'scheduled', %s, 3)
                """,
                (tenant_id, status),
            )
        conn.commit()
    resp = client.get("/api/saas/hongtao-shop/runs")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) == 2 and runs[0]["status"] == "partial_failed"  # 倒序
    run_id = runs[0]["id"]

    # run 详情 + items
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bs_content_sync_items (tenant_id, run_id, module, native_id, action, status) "
            "VALUES (%s, %s, 'hongtao_shop', '1', 'new', 'success')",
            (tenant_id, run_id),
        )
        conn.commit()
    resp = client.get(f"/api/saas/hongtao-shop/runs/{run_id}")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    assert client.get("/api/saas/hongtao-shop/runs/99999").status_code == 404


def test_products_list_filters(client, tenant_id):
    from src.db.database import get_db_connection

    _create_source(tenant_id, selection="ids", selected=["1"])
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for nid, name, model in (("1", "TFZJ1890014欧典米灰", "TFZJ1890014"),
                                 ("2", "TPJ157042米克萨斯", "TPJ157042")):
            cursor.execute(
                """
                INSERT INTO bs_content_sync_records
                    (tenant_id, module, native_id, external_id, payload, last_seen_at)
                VALUES (%s, 'hongtao_shop', %s, %s, %s, now())
                ON CONFLICT (tenant_id, module, native_id) DO UPDATE
                SET payload = EXCLUDED.payload, last_seen_at = now()
                """,
                (tenant_id, nid, f"product:{nid}",
                 json.dumps({"name": name, "model": model, "procode": "",
                             "status": "1"}, ensure_ascii=False)),
            )
        conn.commit()

    resp = client.get("/api/saas/hongtao-shop/products")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    names = [p["name"] for p in body["products"]]
    assert names == sorted(names)  # 按 name 排序

    # 关键词过滤（name / model / procode ILIKE）
    resp = client.get("/api/saas/hongtao-shop/products", params={"keyword": "米克萨斯"})
    assert resp.json()["total"] == 1
    resp = client.get("/api/saas/hongtao-shop/products", params={"keyword": "TFZJ1890014"})
    assert resp.json()["total"] == 1

    # selected 过滤（ids 模式白名单只含 "1"）
    resp = client.get("/api/saas/hongtao-shop/products", params={"selected": True})
    assert resp.json()["total"] == 1 and resp.json()["products"][0]["native_id"] == "1"
    resp = client.get("/api/saas/hongtao-shop/products", params={"selected": False})
    assert resp.json()["total"] == 1 and resp.json()["products"][0]["native_id"] == "2"

    # all 模式下全部视为选中（selected=False → 0 条）
    _create_source(tenant_id, selection="all")
    resp = client.get("/api/saas/hongtao-shop/products", params={"selected": False})
    assert resp.json()["total"] == 0
    resp = client.get("/api/saas/hongtao-shop/products", params={"selected": True})
    assert resp.json()["total"] == 2


# ==================== 平台管理员数据源授权（portal 企业管理） ====================

from src.services.content_sync.api import router as content_sync_router  # noqa: E402


@pytest.fixture()
def platform_client(tenant_id):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: {
        "tenant_id": tenant_id, "role": "platform_admin", "user_id": "root-x",
    }
    return TestClient(app)


@pytest.fixture()
def cs_client(tenant_id):
    app = FastAPI()
    app.include_router(content_sync_router)
    app.dependency_overrides[require_admin] = lambda: {
        "tenant_id": tenant_id, "role": "tenant_admin", "user_id": "admin-x",
    }
    return TestClient(app)


@pytest.fixture()
def tenant_row(tenant_id, require_db):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tenants (tenant_id, company_name, tenant_type)
            VALUES (%s, '数据源授权测试租户', 'test')
            ON CONFLICT (tenant_id) DO NOTHING
            """,
            (tenant_id,),
        )
        conn.commit()
    yield tenant_id
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
        conn.commit()


def test_admin_endpoints_require_platform_admin(client, tenant_id, require_db):
    resp = client.get("/api/saas/hongtao-shop/admin/source", params={"tenant_id": tenant_id})
    assert resp.status_code == 403
    resp = client.post("/api/saas/hongtao-shop/admin/grant", json={"tenant_id": tenant_id})
    assert resp.status_code == 403
    resp = client.post("/api/saas/hongtao-shop/admin/revoke", json={"tenant_id": tenant_id})
    assert resp.status_code == 403


def test_admin_grant_status_revoke_roundtrip(platform_client, client, tenant_row, require_db):
    # 未开通：granted=False
    resp = platform_client.get("/api/saas/hongtao-shop/admin/source", params={"tenant_id": tenant_row})
    assert resp.status_code == 200
    assert resp.json()["granted"] is False and resp.json()["source"] is None

    # 开通（幂等，连开两次不报错不重复建行）：默认 enabled/24h/all
    for _ in range(2):
        resp = platform_client.post("/api/saas/hongtao-shop/admin/grant", json={"tenant_id": tenant_row})
        assert resp.status_code == 200
    body = resp.json()
    assert body["granted"] is True
    assert body["source"]["enabled"] is True
    assert body["source"]["sync_interval_hours"] == 24
    assert body["source"]["selection_mode"] == "all"

    # 租户侧从 404 变 200
    resp = client.get("/api/saas/hongtao-shop/source")
    assert resp.status_code == 200

    # 不存在的租户拒绝开通
    resp = platform_client.post(
        "/api/saas/hongtao-shop/admin/grant", json={"tenant_id": "tenant_not_exists_xx"}
    )
    assert resp.status_code == 404

    # 停用：删授权行，租户侧回到 404
    resp = platform_client.post("/api/saas/hongtao-shop/admin/revoke", json={"tenant_id": tenant_row})
    assert resp.status_code == 200 and resp.json()["granted"] is False
    resp = client.get("/api/saas/hongtao-shop/source")
    assert resp.status_code == 404


def test_revoke_cancels_queued_runs(platform_client, tenant_row, require_db):
    """停用即取消在队 run（P1 修复）：不取消会照常执行，且源行已删时
    _load_selection 按 'all' 语义绕过白名单全量入库并计费。"""
    from src.db.database import get_db_connection

    platform_client.post("/api/saas/hongtao-shop/admin/grant", json={"tenant_id": tenant_row})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status) "
            "VALUES (%s, 'hongtao_shop', 'manual', 'queued')",
            (tenant_row,),
        )
        conn.commit()

    resp = platform_client.post(
        "/api/saas/hongtao-shop/admin/revoke", json={"tenant_id": tenant_row}
    )
    assert resp.status_code == 200
    assert resp.json()["cancelled_runs"] == 1
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status, error_message FROM bs_content_sync_runs "
            "WHERE tenant_id = %s AND module = 'hongtao_shop'",
            (tenant_row,),
        )
        row = cursor.fetchone()
    assert row["status"] == "interrupted"
    assert "已停用" in row["error_message"]


def test_connection_sources_listing_scoped_by_tenant(cs_client, client, tenant_id, require_db):
    # 无授权行 → 空清单
    resp = cs_client.get("/api/saas/connection-sources")
    assert resp.status_code == 200
    assert resp.json()["sources"] == []

    # 开通后仅列出本租户的行；enabled=False（仅手动）仍算已授权
    _create_source(tenant_id, enabled=False)
    resp = cs_client.get("/api/saas/connection-sources")
    sources = resp.json()["sources"]
    assert [s["module"] for s in sources] == ["hongtao_shop"]
    assert sources[0]["enabled"] is False
