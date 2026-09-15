"""WP5 独立验证探针（独立测试智能体补测，覆盖开发用例未覆盖的边界）。

- restore 路径（设计 §5.2）：hash 未变 + doc 软删除 → 复用旧 chunks 恢复 active，
  零 embedding 零计费，检索恢复可见
- 锁续期失败（设计 §5.1）：run 中途失锁 → 停止执行，run 留 running 待回收，
  剩余 item 保持 pending
"""
from .test_service import (  # noqa: F401  复用开发测试的替身与 DB 辅助
    BODY_V1,
    SHORT_URL,
    SHORT_URL_B,
    FakeEmbeddingClient,
    FakeRedis,
    StubFetcher,
    _create_tenant,
    _enqueue,
    _make_service,
    _query_all,
    _query_one,
    make_article_html,
    ok_result,
)
from .conftest import cleanup_tenant  # noqa: F401

# 根 conftest 把 vector_db 换成 stub；restore 检索断言需要真实 pgvector 实现
from .test_service import (  # noqa: F401
    _load_real_vector_db,
    _real_vector_db_patch,
    _retrieve_doc_ids,
)


class TestRestoreProbe:
    async def test_hash_unchanged_deleted_doc_restored_without_reembed(self, tenant_id):
        """doc 软删后复核同文：restore 复用旧 chunks，零重嵌零计费，检索恢复可见。"""
        from src.db.database import get_db_connection

        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("春季活动", BODY_V1)))
        embedding = FakeEmbeddingClient()
        svc = _make_service(fetcher, embedding=embedding)

        _, row_ids, _, _ = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)
        doc_id = _query_one(
            "SELECT doc_id FROM bs_wechat_mp_articles WHERE id = %s", (row_ids[0],)
        )["doc_id"]
        chunk_count = _query_one(
            "SELECT COUNT(*) AS c FROM chunks WHERE doc_id = %s", (doc_id,)
        )["c"]
        assert chunk_count > 0
        embed_calls_before = embedding.embed_batch_calls

        # 模拟文档已被软删除（删除页处理/审计操作后的状态）；chunks 保留
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE documents SET status = 'deleted' WHERE id = %s AND tenant_id = %s",
                (doc_id, tenant_id),
            )
            conn.commit()

        doc_ids, _ = await _retrieve_doc_ids(tenant_id, "春季活动")
        assert doc_id not in doc_ids  # 删除态不可见

        _, _, item_ids, _ = _enqueue(tenant_id, [SHORT_URL], trigger="recheck",
                                     action="check")
        result = await svc.claim_and_run(tenant_id)
        assert result["executed"] is True

        item = _query_one(
            "SELECT status, action, billing_status, credits_charged "
            "FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[0],))
        assert item["status"] == "success" and item["action"] == "restore"
        assert item["billing_status"] == "not_required"
        assert float(item["credits_charged"]) == 0.0

        doc = _query_one("SELECT status FROM documents WHERE id = %s", (doc_id,))
        assert doc["status"] == "active"
        article = _query_one(
            "SELECT status, processing_status FROM bs_wechat_mp_articles WHERE id = %s",
            (row_ids[0],))
        assert article["status"] == "active"
        assert article["processing_status"] == "success"

        # 复用旧 chunks：无新 embedding 调用，chunk 行数不变
        assert embedding.embed_batch_calls == embed_calls_before
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chunks WHERE doc_id = %s", (doc_id,))["c"] \
            == chunk_count

        doc_ids, _ = await _retrieve_doc_ids(tenant_id, "春季活动")
        assert doc_id in doc_ids  # 检索恢复可见


class TestLockLossProbe:
    async def test_renew_failure_stops_run_leaving_it_for_recovery(self, tenant_id):
        """第 2 个 item 续锁失败：停止执行，run 留 running 待回收，剩余 item 仍 pending。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("文章甲", BODY_V1)))

        class OnceRedis(FakeRedis):
            """首次续锁成功，其后失败（模拟租约被夺/Redis 不可达）。"""

            def __init__(self):
                super().__init__()
                self.renew_calls = 0

            def renew_lock(self, key, value, ex):
                self.renew_calls += 1
                if self.renew_calls > 1:
                    return False
                return super().renew_lock(key, value, ex)

        redis = OnceRedis()
        run_id, _, _, _ = _enqueue(tenant_id, [SHORT_URL, SHORT_URL_B])
        svc = _make_service(fetcher, redis=redis)
        result = await svc.claim_and_run(tenant_id)

        # 失锁不影响"已执行"的汇报，但 run 不得伪终态
        assert result["executed"] is True
        statuses = _query_all(
            "SELECT status FROM bs_wechat_mp_sync_items WHERE tenant_id = %s ORDER BY id",
            (tenant_id,))
        assert statuses[0]["status"] == "success"  # 首个 item 正常完成
        assert statuses[1]["status"] == "pending"  # 次个 item 未被处理
        run = _query_one(
            "SELECT status FROM bs_wechat_mp_sync_runs WHERE id = %s", (run_id,))
        assert run["status"] == "running"  # 留待 recover_stale_runs 回收
