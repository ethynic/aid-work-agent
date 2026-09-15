"""WP6 手动粘贴入口 + 管理 API 单元测试（真实 PG，照 test_callback.py 建库/夹具模式）。

分层：
- service 级（真实 DB）：import_urls 三件套落库、批内/pending 去重、>50 拒绝、限流、
  callback 批内重复 URL 回归（WP5 CR P2 修复验证）、retry/recheck 入队语义。
- API 级（TestClient + monkeypatch require_admin，独立 FastAPI 实例不启动 src.main）：
  鉴权 401、租户隔离 404/不可见、portal 跨租户与 403、业务错误 400。

测试用 URL/token 均为伪造值；鉴权头格式 ``Bearer <role>:<tenant_id>`` 仅测试内约定。
"""

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.wechat_mp import api as wechat_mp_api
from src.wechat_mp import callback as cb
from src.wechat_mp import service as wmp_service

URL_1 = "https://mp.weixin.qq.com/s/WmpTest0001"
URL_2 = (
    "https://mp.weixin.qq.com/s?__biz=MzAxWmpTest&mid=200&idx=1&sn=feedface"
    "&scene=126#wechat_redirect"
)
URL_INVALID = "https://example.com/not-mp-article"


def _short_url(n: int) -> str:
    return f"https://mp.weixin.qq.com/s/WmpBatch{n:04d}Ab"


# ------------------------------- 夹具 -------------------------------


def _fake_require_admin(request):
    """测试用鉴权替身：Authorization: Bearer <role>:<tenant_id>（portal 允许无租户）。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    token = auth[7:]
    role, _, tenant = token.partition(":")
    if role == "platform_admin":
        return {"user_id": "u-wmp-test", "tenant_id": tenant or None, "role": role}
    if role == "tenant_admin" and tenant:
        return {"user_id": "u-wmp-test", "tenant_id": tenant, "role": role}
    raise HTTPException(status_code=401, detail="未登录或登录已过期")


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(wechat_mp_api.router)
    monkeypatch.setattr(wechat_mp_api, "require_admin", _fake_require_admin)
    return TestClient(app)


def _auth(tenant: str) -> dict:
    return {"Authorization": f"Bearer tenant_admin:{tenant}"}


def _import_urls(client, tenant_id, urls, expect_status=200):
    resp = client.post(
        "/api/saas/wechat-mp/import-urls", json={"urls": urls}, headers=_auth(tenant_id)
    )
    assert resp.status_code == expect_status
    return resp.json()


def _fetch_all(client, path, tenant_id):
    resp = client.get(path, headers=_auth(tenant_id))
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------- service 级：import_urls -------------------------------


class TestImportUrlsService:
    def test_import_ok_three_piece_persisted(self, require_db, tenant_id):
        result = wmp_service.import_urls(tenant_id, "u-1", [URL_1, URL_2, URL_INVALID])
        assert result["run_id"] is not None
        assert result["accepted"] == 2
        assert len(result["rejected"]) == 1
        assert result["rejected"][0]["url"] == URL_INVALID
        assert result["rejected"][0]["reason"]
        assert "run_id" in result["message"]  # 排队语义说明带 run_id

        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bs_wechat_mp_sync_runs WHERE tenant_id=%s", (tenant_id,)
            )
            runs = cursor.fetchall()
            assert len(runs) == 1
            assert runs[0]["trigger_type"] == "manual"
            assert runs[0]["status"] == "queued"
            assert runs[0]["total_count"] == 2
            assert runs[0]["config_id"] is None
            assert runs[0]["user_id"] == "u-1"

            cursor.execute(
                "SELECT * FROM bs_wechat_mp_sync_items WHERE tenant_id=%s ORDER BY id",
                (tenant_id,),
            )
            items = cursor.fetchall()
            assert len(items) == 2
            assert all(i["status"] == "pending" for i in items)
            assert all(i["action"] is None for i in items)  # NULL 视为 'new'

            cursor.execute(
                "SELECT * FROM bs_wechat_mp_articles WHERE tenant_id=%s ORDER BY id",
                (tenant_id,),
            )
            articles = cursor.fetchall()
            assert len(articles) == 2
            assert all(a["source_channel"] == "manual" for a in articles)
            assert all(a["config_id"] is None for a in articles)
            assert all(a["status"] == "active" for a in articles)
            assert all(a["processing_status"] == "pending" for a in articles)
            # 长链跟踪参数剔除
            assert any(a["external_id"].startswith("mp:q:") for a in articles)

    def test_import_batch_dup_dedup(self, require_db, tenant_id):
        result = wmp_service.import_urls(tenant_id, None, [URL_1, URL_1, URL_1])
        assert result["accepted"] == 1
        assert len(result["duplicates"]) == 2

        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_sync_items WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert cursor.fetchone()["c"] == 1

    def test_import_existing_article_reused_not_recreated(self, require_db, tenant_id):
        # 预置同 external_id 文章行（其他来源先受理过）
        from src.db.database import get_db_connection

        identity = wmp_service.normalize_url(URL_1)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_articles
                    (tenant_id, config_id, external_id, original_url, fetch_url,
                     source_channel, status, processing_status)
                VALUES (%s, 'chan_x', %s, %s, %s, 'callback', 'active', 'success')
                """,
                (tenant_id, identity.external_id, identity.original_url, identity.fetch_url),
            )
            conn.commit()

        # 设计 §13 硬规则：手动触发是明确刷新请求，复用行但一律建 item（无 pending 去重拦截）
        result = wmp_service.import_urls(tenant_id, None, [URL_1])
        assert result["accepted"] == 1

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_articles WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert cursor.fetchone()["c"] == 1  # 复用行不新建
            cursor.execute(
                "SELECT source_channel FROM bs_wechat_mp_articles WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert cursor.fetchone()["source_channel"] == "callback"  # 保留首受理来源
            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_sync_items WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert cursor.fetchone()["c"] == 1  # item 照建

    def test_import_pending_dedup_skips_requeue(self, require_db, tenant_id):
        first = wmp_service.import_urls(tenant_id, None, [URL_1])
        assert first["accepted"] == 1
        second = wmp_service.import_urls(tenant_id, None, [URL_1, URL_2])
        assert second["accepted"] == 1  # URL_2 新增
        assert second["run_id"] != first["run_id"]
        dup_urls = [d["url"] for d in second["duplicates"]]
        assert dup_urls == [URL_1]  # URL_1 已在待处理队列，不重复排队

        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_sync_items WHERE tenant_id=%s AND article_row_id = "
                "(SELECT id FROM bs_wechat_mp_articles WHERE tenant_id=%s AND external_id=%s)",
                (tenant_id, tenant_id, "mp:s:WmpTest0001"),
            )
            assert cursor.fetchone()["c"] == 1  # URL_1 只有一个 item

    def test_import_pending_dedup_ignores_orphaned_items(self, require_db, tenant_id):
        """interrupted run 残留的 pending item 不压制明确刷新请求（设计 §14 硬规则）。

        worker 失联回收（recover_stale_runs）只把 running item 标 interrupted，run 内
        尚未领取的 pending item 会永久滞留；去重若据此跳过，该 URL 将永远不会被处理。
        """
        from src.db.database import get_db_connection

        identity = wmp_service.normalize_url(URL_1)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_articles
                    (tenant_id, config_id, external_id, original_url, fetch_url,
                     source_channel, status, processing_status)
                VALUES (%s, NULL, %s, %s, %s, 'manual', 'active', 'pending')
                RETURNING id
                """,
                (tenant_id, identity.external_id, identity.original_url, identity.fetch_url),
            )
            article_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_runs
                    (tenant_id, trigger_type, status, total_count)
                VALUES (%s, 'manual', 'interrupted', 1)
                RETURNING id
                """,
                (tenant_id,),
            )
            orphan_run_id = cursor.fetchone()["id"]
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_items
                    (tenant_id, run_id, article_row_id, action, status)
                VALUES (%s, %s, %s, NULL, 'pending')
                """,
                (tenant_id, orphan_run_id, article_id),
            )
            conn.commit()

        result = wmp_service.import_urls(tenant_id, None, [URL_1])
        assert result["accepted"] == 1
        assert result["run_id"] not in (None, orphan_run_id)
        assert result["duplicates"] == []

    def test_import_gt50_rejected(self, require_db, tenant_id):
        urls = [_short_url(i) for i in range(51)]
        with pytest.raises(wmp_service.WeChatMPBusinessError):
            wmp_service.import_urls(tenant_id, None, urls)

    def test_import_gt50_api_400(self, require_db, tenant_id, client):
        urls = [_short_url(i) for i in range(51)]
        body = _import_urls(client, tenant_id, urls, expect_status=400)
        assert "50" in body["detail"]

    def test_import_rate_limit_queued_runs(self, require_db, tenant_id):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            for _ in range(wmp_service.MAX_QUEUED_RUNS):
                cursor.execute(
                    """
                    INSERT INTO bs_wechat_mp_sync_runs
                        (tenant_id, trigger_type, status, total_count)
                    VALUES (%s, 'manual', 'queued', 0)
                    """,
                    (tenant_id,),
                )
            conn.commit()

        with pytest.raises(wmp_service.WeChatMPBusinessError):
            wmp_service.import_urls(tenant_id, None, [URL_1])

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_sync_runs WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert cursor.fetchone()["c"] == wmp_service.MAX_QUEUED_RUNS  # 未新增

    def test_import_all_invalid_no_run(self, require_db, tenant_id):
        result = wmp_service.import_urls(tenant_id, None, [URL_INVALID, ""])
        assert result["run_id"] is None
        assert result["accepted"] == 0
        assert len(result["rejected"]) >= 1

        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_sync_runs WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert cursor.fetchone()["c"] == 0


# ------------------------------- service 级：callback 批内重复 URL 回归 -------------------------------


class TestCallbackBatchDupRegression:
    def test_duplicate_urls_in_same_event_not_500(self, require_db, tenant_id):
        """WP5 CR P2 修复验证：同一事件 ResultList 重复 URL 不再撞 items 唯一键整体回滚。"""
        config_id = f"chan_{uuid.uuid4().hex[:8]}"
        result = cb._accept_masssend_event(
            tenant_id,
            config_id,
            "9000001:MASSSENDJOBFINISH",
            {"MsgID": "9000001"},
            [("1", URL_1), ("2", URL_1), ("3", URL_2)],
        )
        assert result == cb._ACCEPT_ACCEPTED

        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bs_wechat_mp_sync_runs WHERE tenant_id=%s", (tenant_id,)
            )
            runs = cursor.fetchall()
            assert len(runs) == 1
            assert runs[0]["total_count"] == 2  # 去重后 2 条

            cursor.execute(
                "SELECT * FROM bs_wechat_mp_sync_items WHERE tenant_id=%s", (tenant_id,)
            )
            items = cursor.fetchall()
            assert len(items) == 2  # item 不重复
            assert len({i["article_row_id"] for i in items}) == 2

            cursor.execute(
                "SELECT count(*) AS c FROM bs_wechat_mp_articles WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert cursor.fetchone()["c"] == 2


# ------------------------------- API 级：runs / articles / 隔离 -------------------------------


class TestRunQueriesApi:
    def test_runs_list_and_detail_with_items(self, require_db, tenant_id, client):
        body = _import_urls(client, tenant_id, [URL_1, URL_2])
        run_id = body["run_id"]

        listed = _fetch_all(client, "/api/saas/wechat-mp/runs", tenant_id)
        assert listed["success"] is True
        assert listed["total"] == 1
        run = listed["runs"][0]
        assert run["id"] == run_id
        assert run["trigger_type"] == "manual"
        assert run["status"] == "queued"
        assert run["total_count"] == 2
        for field in (
            "new_count", "updated_count", "deleted_count", "skipped_count",
            "failed_count", "credits_charged", "created_at", "completed_at",
        ):
            assert field in run

        detail_resp = client.get(
            f"/api/saas/wechat-mp/runs/{run_id}", headers=_auth(tenant_id)
        )
        assert detail_resp.status_code == 200
        detail = detail_resp.json()
        assert detail["run"]["id"] == run_id
        assert len(detail["items"]) == 2
        item = detail["items"][0]
        for field in (
            "article_row_id", "action", "status", "error_code", "error_message",
            "billing_status", "credits_charged",
        ):
            assert field in item

    def test_run_detail_cross_tenant_404(self, require_db, tenant_id, client):
        body = _import_urls(client, tenant_id, [URL_1])
        other_tenant = f"wmp_test_{uuid.uuid4().hex[:12]}"
        try:
            resp = client.get(
                f"/api/saas/wechat-mp/runs/{body['run_id']}", headers=_auth(other_tenant)
            )
            assert resp.status_code == 404
            listed = _fetch_all(client, "/api/saas/wechat-mp/runs", other_tenant)
            assert listed["total"] == 0  # 他租户数据不可见
        finally:
            from tests.unit.wechat_mp.conftest import cleanup_tenant

            cleanup_tenant(other_tenant)

    def test_articles_list_and_filter(self, require_db, tenant_id, client):
        _import_urls(client, tenant_id, [URL_1, URL_2])

        listed = _fetch_all(client, "/api/saas/wechat-mp/articles", tenant_id)
        assert listed["total"] == 2
        article = listed["articles"][0]
        for field in (
            "external_id", "original_url", "title", "status", "processing_status",
            "last_synced_at", "last_checked_at", "next_retry_at", "error_message", "doc_id",
        ):
            assert field in article

        pending = _fetch_all(
            client, "/api/saas/wechat-mp/articles?processing_status=pending", tenant_id
        )
        assert pending["total"] == 2
        success = _fetch_all(
            client, "/api/saas/wechat-mp/articles?processing_status=success", tenant_id
        )
        assert success["total"] == 0


# ------------------------------- API 级：retry / recheck -------------------------------


def _first_article_row_id(client, tenant_id) -> int:
    listed = _fetch_all(client, "/api/saas/wechat-mp/articles", tenant_id)
    assert listed["total"] >= 1
    return listed["articles"][0]["id"]


def _set_article_status(tenant_id, article_row_id, status, processing_status=None):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_wechat_mp_articles SET status = %s, processing_status = COALESCE(%s, processing_status) "
            "WHERE id = %s AND tenant_id = %s",
            (status, processing_status, article_row_id, tenant_id),
        )
        conn.commit()


class TestRetryRecheckApi:
    def test_retry_creates_run_and_resets_article(self, require_db, tenant_id, client):
        _import_urls(client, tenant_id, [URL_1])
        article_id = _first_article_row_id(client, tenant_id)
        _set_article_status(tenant_id, article_id, "active", "sync_failed")

        resp = client.post(
            f"/api/saas/wechat-mp/articles/{article_id}/retry", headers=_auth(tenant_id)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["trigger_type"] == "retry"

        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bs_wechat_mp_sync_runs WHERE tenant_id=%s AND id=%s",
                (tenant_id, body["run_id"]),
            )
            run = cursor.fetchone()
            assert run["trigger_type"] == "retry"
            assert run["status"] == "queued"
            assert run["total_count"] == 1

            cursor.execute(
                "SELECT * FROM bs_wechat_mp_sync_items WHERE tenant_id=%s AND run_id=%s",
                (tenant_id, body["run_id"]),
            )
            items = cursor.fetchall()
            assert len(items) == 1
            assert items[0]["action"] is None
            assert items[0]["status"] == "pending"

            cursor.execute(
                "SELECT processing_status, next_retry_at FROM bs_wechat_mp_articles "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, article_id),
            )
            article = cursor.fetchone()
            assert article["processing_status"] == "pending"
            assert article["next_retry_at"] is None

    def test_retry_alias_rejected(self, require_db, tenant_id, client):
        _import_urls(client, tenant_id, [URL_1])
        article_id = _first_article_row_id(client, tenant_id)
        _set_article_status(tenant_id, article_id, "alias")
        resp = client.post(
            f"/api/saas/wechat-mp/articles/{article_id}/retry", headers=_auth(tenant_id)
        )
        assert resp.status_code == 400
        assert "别名" in resp.json()["detail"]

    def test_retry_deleted_rejected(self, require_db, tenant_id, client):
        _import_urls(client, tenant_id, [URL_1])
        article_id = _first_article_row_id(client, tenant_id)
        _set_article_status(tenant_id, article_id, "deleted")
        resp = client.post(
            f"/api/saas/wechat-mp/articles/{article_id}/retry", headers=_auth(tenant_id)
        )
        assert resp.status_code == 400
        assert "删除" in resp.json()["detail"]

    def test_retry_cross_tenant_404(self, require_db, tenant_id, client):
        _import_urls(client, tenant_id, [URL_1])
        article_id = _first_article_row_id(client, tenant_id)
        other_tenant = f"wmp_test_{uuid.uuid4().hex[:12]}"
        try:
            resp = client.post(
                f"/api/saas/wechat-mp/articles/{article_id}/retry", headers=_auth(other_tenant)
            )
            assert resp.status_code == 404
        finally:
            from tests.unit.wechat_mp.conftest import cleanup_tenant

            cleanup_tenant(other_tenant)

    def test_recheck_creates_check_item(self, require_db, tenant_id, client):
        _import_urls(client, tenant_id, [URL_1])
        article_id = _first_article_row_id(client, tenant_id)
        _set_article_status(tenant_id, article_id, "active", "success")

        resp = client.post(
            f"/api/saas/wechat-mp/articles/{article_id}/recheck", headers=_auth(tenant_id)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["trigger_type"] == "recheck"

        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT trigger_type, status FROM bs_wechat_mp_sync_runs "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, body["run_id"]),
            )
            run = cursor.fetchone()
            assert run["trigger_type"] == "recheck"
            assert run["status"] == "queued"
            cursor.execute(
                "SELECT action, status FROM bs_wechat_mp_sync_items "
                "WHERE tenant_id=%s AND run_id=%s",
                (tenant_id, body["run_id"]),
            )
            item = cursor.fetchone()
            assert item["action"] == "check"
            assert item["status"] == "pending"

    def test_recheck_non_active_rejected(self, require_db, tenant_id, client):
        _import_urls(client, tenant_id, [URL_1])
        article_id = _first_article_row_id(client, tenant_id)
        _set_article_status(tenant_id, article_id, "unconfirmed")
        resp = client.post(
            f"/api/saas/wechat-mp/articles/{article_id}/recheck", headers=_auth(tenant_id)
        )
        assert resp.status_code == 400


# ------------------------------- API 级：portal 跨租户 -------------------------------


class TestPortalApi:
    def test_portal_runs_and_articles_cross_tenant(self, require_db, tenant_id, client):
        _import_urls(client, tenant_id, [URL_1])

        platform_headers = {"Authorization": "Bearer platform_admin"}
        resp = client.get("/api/saas/wechat-mp/portal/runs", headers=platform_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["total"] >= 1
        assert any(r["tenant_id"] == tenant_id for r in body["runs"])

        resp = client.get(
            f"/api/saas/wechat-mp/portal/runs?tenant_id={tenant_id}", headers=platform_headers
        )
        assert resp.status_code == 200
        filtered = resp.json()
        assert filtered["total"] == 1
        assert filtered["runs"][0]["tenant_id"] == tenant_id

        resp = client.get(
            f"/api/saas/wechat-mp/portal/articles?tenant_id={tenant_id}",
            headers=platform_headers,
        )
        assert resp.status_code == 200
        articles = resp.json()
        assert articles["total"] == 1
        assert articles["articles"][0]["tenant_id"] == tenant_id

        # 过滤不存在的租户 → 空
        resp = client.get(
            "/api/saas/wechat-mp/portal/runs?tenant_id=no-such-tenant", headers=platform_headers
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_portal_tenant_admin_403(self, require_db, tenant_id, client):
        for path in ("/api/saas/wechat-mp/portal/runs", "/api/saas/wechat-mp/portal/articles"):
            resp = client.get(path, headers=_auth(tenant_id))
            assert resp.status_code == 403


# ------------------------------- API 级：鉴权 -------------------------------


class TestAuthApi:
    def test_missing_auth_401(self, client):
        assert client.get("/api/saas/wechat-mp/runs").status_code == 401
        assert (
            client.post(
                "/api/saas/wechat-mp/import-urls", json={"urls": [URL_1]}
            ).status_code
            == 401
        )

    def test_invalid_role_401(self, client):
        resp = client.get(
            "/api/saas/wechat-mp/runs",
            headers={"Authorization": "Bearer user:some-tenant"},
        )
        assert resp.status_code == 401
