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
