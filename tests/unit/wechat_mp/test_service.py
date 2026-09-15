"""wechat_mp WP5 统一入库 service 单元测试（真实 PG，require_db 门禁）。

覆盖（设计 §3/§5.4/§7.4 + 计划 WP5）：
- 全链路入库（stub 抓取页 + 真实 DB/真实 chunker/mock embedding client）→ 检索可见
- 计费金额精确到分（chat_records + 余额扣减一致）、hash 不变零计费零重嵌
- 更新替换后旧 chunk 不可检索；删除页 → 软删除且检索不可见
- 余额不足 skipped_no_credit（run 预检），删除复核不受余额阻断
- unknown 计费禁止自动重扣
- 租户串行：锁占用拒入 / running 唯一约束让位 / 多 queued run 依次排空
- stale running 恢复 interrupted；queued 保持等待
- 别名收敛：同批次重复项 skipped + duplicate_of_item_id；跨批次别名行
  doc 置 deleted + merged_into_doc_id；msg_link 回显自身形态不合并

抓取走 StubFetcher（不触网）；embedding 走 FakeEmbeddingClient（恒定单位向量，
检索相似度恒 1.0，可见性断言不依赖语义）；Redis 走进程内 FakeRedis
（is_available 可控）。每用例独立随机租户，测后物理清理（conftest）。
"""

import importlib.util
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.wechat_mp import service as svc_mod
from src.wechat_mp.fetcher import (
    STATUS_DELETED,
    STATUS_FETCH_FAILED,
    STATUS_OK,
    FetchResult,
)
from src.wechat_mp.identity import normalize_url
from src.wechat_mp.service import (
    CATEGORY_SOURCE_TYPE,
    ERR_ALIAS_DUPLICATE,
    ERR_NO_CREDIT,
    PIPELINE_VERSION,
    SUB_CATEGORY_SOURCE_TYPE,
    WeChatMPSyncService,
)

from .conftest import cleanup_tenant

# ------------------------------- 测试替身 -------------------------------


class FakeRedis:
    """进程内 Redis 替身：真实锁语义 + is_available 可控（redis_client 接口子集）。"""

    def __init__(self, available: bool = True):
        self._available = available
        self._store = {}
        self._lock = threading.Lock()

    def is_available(self) -> bool:
        return self._available

    @staticmethod
    def make_key(prefix: str, identifier: str = "") -> str:
        return f"{prefix}:{identifier}" if identifier else prefix

    def acquire_lock(self, key: str, value: str, ex: int = 60) -> bool:
        with self._lock:
            if key in self._store:
                return False
            self._store[key] = value
            return True

    def release_lock(self, key: str, value: str) -> bool:
        with self._lock:
            if self._store.get(key) == value:
                del self._store[key]
                return True
            return False

    def renew_lock(self, key: str, value: str, ex: int) -> bool:
        with self._lock:
            return self._store.get(key) == value

    def get(self, key: str):
        raw = self._store.get(key)
        if raw is None:
            return None
        return json.loads(raw)


class FakeEmbeddingClient:
    """embedding 替身：恒定单位向量（相似度恒 1.0）+ 确定性 token 计数。"""

    model = "text-embedding-v3"

    def __init__(self):
        self.last_usage_tokens = 0
        self.embed_batch_calls = 0

    def reset_usage(self) -> None:
        self.last_usage_tokens = 0

    @staticmethod
    def _vec():
        return [1.0] + [0.0] * 1023

    async def embed_batch(self, texts, batch_size: int = 10):
        self.embed_batch_calls += 1
        self.last_usage_tokens += sum(max(1, len(t) // 4) for t in texts)
        return [self._vec() for _ in texts]

    async def embed(self, text: str):
        self.last_usage_tokens += max(1, len(text) // 4)
        return self._vec()


class StubFetcher:
    """抓取替身：fetch_url → FetchResult；list 值支持逐次返回不同页面。"""

    def __init__(self):
        self.pages = {}
        self.calls = []

    def set_page(self, raw_url: str, result) -> None:
        self.pages[normalize_url(raw_url).fetch_url] = result

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        result = self.pages[url]
        if isinstance(result, list):
            return result.pop(0) if len(result) > 1 else result[0]
        return result


def make_article_html(title: str, body: str, msg_link: str = None) -> str:
    """合成最小合法文章页（js_content + activity-name；msg_link 可控别名证据）。"""
    msg = ""
    if msg_link:
        escaped = msg_link.replace("&", "&amp;")
        msg = f'var msg_link = "{escaped}";'
    return (
        "<html><head>"
        f"<script>var msg_title = '{title}'.html(false);{msg}</script>"
        "</head><body>"
        f'<h1 id="activity-name">{title}</h1>'
        '<span id="js_name">WP5测试号</span>'
        f'<div id="js_content"><p>{body}</p></div>'
        "</body></html>"
    )


def make_image_only_html(title: str, image_count: int) -> str:
    """纯图文章页（js_content 仅含 data-src 图片节点，正文文字为 0）。"""
    imgs = "".join(
        f'<img data-src="https://mmecoa.qpic.cn/wp5test/img_{i}.jpg">'
        for i in range(1, image_count + 1)
    )
    return (
        "<html><head>"
        f"<script>var msg_title = '{title}'.html(false);</script>"
        "</head><body>"
        f'<h1 id="activity-name">{title}</h1>'
        f'<div id="js_content">{imgs}</div>'
        "</body></html>"
    )


def ok_result(html: str) -> FetchResult:
    return FetchResult(status=STATUS_OK, html=html, http_status=200,
                       evidence={"has_js_content": True})


# ------------------------------- DB 辅助 -------------------------------


def _load_real_vector_db():
    """按文件位置加载真实 vector_db 模块（根 conftest 已把
    src.knowledge.vector_db.vector_db 替换为 stub，无法常规导入）"""
    file_path = (
        Path(__file__).resolve().parents[3]
        / "src" / "knowledge" / "vector_db" / "vector_db.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_real_vector_db_for_wp5_test", str(file_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _real_vector_db_patch(monkeypatch):
    """service 内 get_vector_db 替换为真实 pgvector 实现（根 conftest 的是 stub）。"""
    module = _load_real_vector_db()
    monkeypatch.setattr(svc_mod, "get_vector_db", module.get_vector_db)


@pytest.fixture()
def db_conn():
    from src.db.database import get_db_connection

    return get_db_connection


def _create_tenant(tenant_id: str, balance: float = 100.0) -> None:
    from src.core.cache_utils import invalidate_tenant_cache
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tenants (tenant_id, company_name, credit_balance)
            VALUES (%s, %s, %s)
            ON CONFLICT (tenant_id)
            DO UPDATE SET credit_balance = EXCLUDED.credit_balance
            """,
            (tenant_id, "WP5测试", balance),
        )
        conn.commit()
    invalidate_tenant_cache(tenant_id)


def _add_presales_subscription(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO subscriptions (subscription_id, tenant_id, subagent_type,
                                       status, payment_status)
            VALUES (%s, %s, 'pre-sales', 'active', 'paid')
            """,
            (f"sub_{tenant_id}", tenant_id),
        )
        conn.commit()


def _enqueue(tenant_id: str, urls, trigger: str = "callback", action: str = "new",
             with_event: bool = False):
    """受理入队（镜像 WP4 三件套）：articles upsert + queued run + pending items。

    Returns: (run_id, [article_row_id], [item_id], event_id|None)
    """
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        row_ids = []
        for url in urls:
            identity = normalize_url(url)
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_articles
                    (tenant_id, external_id, original_url, fetch_url,
                     source_channel, status, processing_status)
                VALUES (%s, %s, %s, %s, 'callback', 'active', 'pending')
                ON CONFLICT (tenant_id, external_id) DO NOTHING
                RETURNING id
                """,
                (tenant_id, identity.external_id, identity.original_url, identity.fetch_url),
            )
            row = cursor.fetchone()
            if row:
                row_ids.append(row["id"])
            else:
                cursor.execute(
                    "SELECT id FROM bs_wechat_mp_articles "
                    "WHERE tenant_id = %s AND external_id = %s",
                    (tenant_id, identity.external_id),
                )
                row_ids.append(cursor.fetchone()["id"])
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_runs (tenant_id, trigger_type, status, total_count)
            VALUES (%s, %s, 'queued', %s)
            RETURNING id
            """,
            (tenant_id, trigger, len(row_ids)),
        )
        run_id = cursor.fetchone()["id"]
        item_ids = []
        for row_id in row_ids:
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_items
                    (tenant_id, run_id, article_row_id, action, status)
                VALUES (%s, %s, %s, %s, 'pending')
                RETURNING id
                """,
                (tenant_id, run_id, row_id, action),
            )
            item_ids.append(cursor.fetchone()["id"])
        event_id = None
        if with_event:
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_events
                    (tenant_id, config_id, event_key, run_id, status)
                VALUES (%s, %s, %s, %s, 'pending')
                RETURNING id
                """,
                (tenant_id, "chan_wp5test", f"evt_{run_id}", run_id),
            )
            event_id = cursor.fetchone()["id"]
        conn.commit()
    return run_id, row_ids, item_ids, event_id


def _query_one(sql, params):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchone()


def _query_all(sql, params):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchall()


class _NoModelVision:
    """无可用多模态模型的 VisionParser 替身（available()=False → 走 deferred）。"""

    def available(self) -> bool:
        return False

    async def describe_images(self, images, tenant_id):  # pragma: no cover - 不可达
        raise AssertionError("无模型时不应调用 describe_images")


class _UnusedDownloader:  # pragma: no cover - 无模型时不应触达下载器
    def download(self, *a, **kw):
        raise AssertionError("无多模态模型时不应调用图片下载")


class FakeSummarizer:
    """WP12 总结替身：默认返回 merged 截断（保留核心内容语义），记录调用。

    - usage 默认空 dict → record_background_llm_usage 首行 no-op，
      既有「embedding 计费精确到分」断言不被总结计费记录污染
    - fail=True 模拟两次尝试均失败 → service 回退 merged 原文入库
    """

    def __init__(self, summary=None, fail=False, usage=None):
        self.summary = summary
        self.fail = fail
        self.usage = usage if usage is not None else {}
        self.calls = []

    async def summarize(self, merged_text, title):
        self.calls.append({"merged_text": merged_text, "title": title})
        if self.fail:
            return None
        text = self.summary if self.summary is not None else (merged_text or "")[:100]
        return (text, "summary-test-model", dict(self.usage))


def _make_service(fetcher: StubFetcher, redis=None, embedding=None,
                  vision=None, downloader=None, summarizer=None) -> WeChatMPSyncService:
    """构造 service（WP10 起 image/vision 组件默认注入「无多模态模型」替身，
    避免单测触网/触真实 LLM；WP10 用例按场景显式传入 FakeDownloader/FakeVision；
    WP12 起总结器默认注入 FakeSummarizer，避免触真实 LLM 网关）。"""
    return WeChatMPSyncService(
        fetcher=fetcher,
        redis=redis or FakeRedis(),
        embedding_client=embedding or FakeEmbeddingClient(),
        image_downloader=downloader or _UnusedDownloader(),
        vision_parser=vision or _NoModelVision(),
        summarizer=summarizer or FakeSummarizer(),
    )


async def _retrieve_doc_ids(tenant_id: str, query: str):
    """真实混合检索（真实 pgvector + 真实 FTS SQL），返回 doc_id 集合。"""
    from src.db.database import get_db_connection
    from src.knowledge.retriever.hybrid_retriever import HybridRetriever

    module = _load_real_vector_db()
    with get_db_connection() as conn:
        vector_db = module.get_vector_db(dimension=1024, conn=conn)
        retriever = HybridRetriever(
            vector_db=vector_db, embedding_client=FakeEmbeddingClient(), conn=conn
        )
        results = await retriever.retrieve(query=query, top_k=10, tenant_id=tenant_id)
    return {r["doc_id"] for r in results}, results


def _tenant_balance(tenant_id: str) -> float:
    row = _query_one(
        "SELECT credit_balance FROM tenants WHERE tenant_id = %s", (tenant_id,)
    )
    return float(row["credit_balance"])


SHORT_URL = "https://mp.weixin.qq.com/s/Wp5TokAlpha001"
SHORT_URL_B = "https://mp.weixin.qq.com/s/Wp5TokBeta0002"
LONG_URL = (
    "https://mp.weixin.qq.com/s?__biz=MzAxWp5TestA&mid=100&idx=1&sn=snvalue01"
    "&scene=6&clicktime=1700000000"
)
LONG_URL_B = "https://mp.weixin.qq.com/s?__biz=MzAxWp5TestB&mid=200&idx=2&sn=snvalue02"

BODY_V1 = "春季活动正式开始，全场瓷砖八折优惠，欢迎到店咨询选购。"
BODY_V2 = "春季活动全面升级，全场瓷砖七五折，新版内容标记V2已生效。"


# ------------------------------- 全链路 -------------------------------


class TestFullIngest:
    async def test_ingest_then_retrievable_and_billing_exact(self, tenant_id):
        """全链路：入库 → 检索可见 → 分类惰性创建 → 售前挂接 → 计费精确到分。"""
        _create_tenant(tenant_id, balance=100.0)
        _add_presales_subscription(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("春季活动", BODY_V1,
                                                                msg_link=SHORT_URL)))
        run_id, row_ids, item_ids, event_id = _enqueue(tenant_id, [SHORT_URL],
                                                       with_event=True)

        svc = _make_service(fetcher)
        result = await svc.claim_and_run(tenant_id)

        assert result["executed"] is True and result["run_ids"] == [run_id]

        run = _query_one(
            "SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (run_id,))
        assert run["status"] == "success"
        assert run["new_count"] == 1 and run["failed_count"] == 0

        item = _query_one(
            "SELECT * FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[0],))
        assert item["status"] == "success" and item["action"] == "new"
        assert item["billing_status"] == "charged"
        assert item["billing_reference"]

        # 事件在 run 全部 item 终态后 done
        event = _query_one(
            "SELECT status FROM bs_wechat_mp_events WHERE id = %s", (event_id,))
        assert event["status"] == "done"

        article = _query_one(
            "SELECT * FROM bs_wechat_mp_articles WHERE id = %s", (row_ids[0],))
        assert article["processing_status"] == "success"
        assert article["pipeline_version"] == PIPELINE_VERSION
        assert article["content_hash"] and article["doc_id"]
        assert article["status"] == "active"  # msg_link 回显自身形态，不收敛
        doc_id = article["doc_id"]

        doc = _query_one("SELECT * FROM documents WHERE id = %s", (doc_id,))
        assert doc["origin"] == "wechat_mp"
        assert doc["external_id"] == article["external_id"]
        assert doc["title"] == "[公众号] 春季活动"
        assert doc["source_type"] == CATEGORY_SOURCE_TYPE
        assert doc["sub_category"] == SUB_CATEGORY_SOURCE_TYPE
        assert doc["status"] == "active"
        metadata = json.loads(doc["metadata"])
        assert metadata["original_url"] == SHORT_URL
        assert metadata["source_channel"] == "callback"
        assert metadata["presales_attach"]["status"] == "attached"
        # WP12：summary 列存总结文本（替身 = merged 截断口径），正文形态记
        # metadata.content_mode；chunk 文本 = 总结本身（无「文档标题：」前缀、
        # 不拼原文链接——链接存 documents.file_path 即文档位置字段）
        assert doc["summary"] == BODY_V1
        assert doc["file_path"] == SHORT_URL
        assert metadata["content_mode"] == "summary"
        assert "summary_fallback" not in metadata
        chunk_texts = [c["text"] for c in _query_all(
            "SELECT text FROM chunks WHERE doc_id = %s ORDER BY chunk_index", (doc_id,))]
        assert not any("原文链接：" in t for t in chunk_texts)
        assert not any("文档标题：" in t for t in chunk_texts)

        # 分类惰性创建
        cats = _query_all(
            "SELECT id, source_type, parent_id FROM knowledge_categories "
            "WHERE tenant_id = %s ORDER BY id", (tenant_id,))
        assert [c["source_type"] for c in cats] == [
            CATEGORY_SOURCE_TYPE, SUB_CATEGORY_SOURCE_TYPE]
        assert cats[0]["parent_id"] is None
        assert cats[1]["parent_id"] == cats[0]["id"]

        # 售前挂接：sources 含本租户自有项
        from src.db.subagent_knowledge_source_db import SubagentKnowledgeSourceDB

        sources = SubagentKnowledgeSourceDB.get(tenant_id, "pre-sales")
        assert any(
            s.get("source_type") == CATEGORY_SOURCE_TYPE and not s.get("owner_tenant_id")
            for s in sources
        )

        # 计费精确到分：chat_records 与余额扣减一致
        record = _query_one(
            "SELECT * FROM chat_records WHERE tenant_id = %s", (tenant_id,))
        assert record["source_type"] == "wechat_mp_embedding"
        assert record["embedding_tokens"] > 0
        from src.services.billing import calculate_embedding_credit_cost_with_breakdown

        expected_credit, _ = calculate_embedding_credit_cost_with_breakdown(
            embedding_tokens=record["embedding_tokens"])
        assert float(record["credit_cost"]) == expected_credit
        assert float(item["credits_charged"]) == expected_credit
        assert _tenant_balance(tenant_id) == round(100.0 - expected_credit, 2)
        assert float(run["credits_charged"]) == expected_credit

        # 检索可见
        doc_ids, _ = await _retrieve_doc_ids(tenant_id, "春季活动")
        assert doc_id in doc_ids

    async def test_ingest_without_presales_instance_records_pending(self, tenant_id):
        """无售前实例：入库成功 + metadata 记录待挂接原因，不阻断。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("活动", BODY_V1)))
        _, row_ids, _, _ = _enqueue(tenant_id, [SHORT_URL])

        svc = _make_service(fetcher)
        result = await svc.claim_and_run(tenant_id)
        assert result["executed"] is True

        article = _query_one(
            "SELECT doc_id FROM bs_wechat_mp_articles WHERE id = %s", (row_ids[0],))
        doc = _query_one("SELECT metadata FROM documents WHERE id = %s",
                         (article["doc_id"],))
        attach = json.loads(doc["metadata"])["presales_attach"]
        assert attach["status"] == "no_instance"
        assert "售前" in attach["reason"]


class TestIdempotentRecheck:
    async def test_hash_unchanged_check_zero_billing(self, tenant_id):
        """复核必抓页面；hash 未变记 check，零 embedding 零计费。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("春季活动", BODY_V1)))
        embedding = FakeEmbeddingClient()
        svc = _make_service(fetcher, embedding=embedding)

        _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)
        records_before = _query_all(
            "SELECT record_id FROM chat_records WHERE tenant_id = %s", (tenant_id,))
        calls_before = embedding.embed_batch_calls

        _, _, item_ids, _ = _enqueue(tenant_id, [SHORT_URL], trigger="recheck",
                                     action="check")
        result = await svc.claim_and_run(tenant_id)
        assert result["executed"] is True

        item = _query_one(
            "SELECT * FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[0],))
        assert item["status"] == "success" and item["action"] == "check"
        assert item["billing_status"] == "not_required"
        assert float(item["credits_charged"]) == 0.0
        # 确实重新抓取了页面（不允许抓取前按 hash 跳过），但未重新 embedding
        assert len(fetcher.calls) == 2
        assert embedding.embed_batch_calls == calls_before
        records_after = _query_all(
            "SELECT record_id FROM chat_records WHERE tenant_id = %s", (tenant_id,))
        assert len(records_after) == len(records_before)

    async def test_update_replaces_old_chunks(self, tenant_id):
        """内容更新：doc_id 快路径重建 chunks，旧文本不可检索。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(
            SHORT_URL,
            [ok_result(make_article_html("春季活动", BODY_V1)),
             ok_result(make_article_html("春季活动", BODY_V2))],
        )
        svc = _make_service(fetcher)

        _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)
        article = _query_one(
            "SELECT doc_id FROM bs_wechat_mp_articles "
            "WHERE tenant_id = %s AND fetch_url = %s",
            (tenant_id, normalize_url(SHORT_URL).fetch_url))
        doc_id = article["doc_id"]

        _, _, item_ids, _ = _enqueue(tenant_id, [SHORT_URL], trigger="recheck",
                                     action="check")
        await svc.claim_and_run(tenant_id)

        item = _query_one(
            "SELECT action, billing_status FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids[0],))
        assert item["action"] == "update" and item["billing_status"] == "charged"

        chunks = _query_all(
            "SELECT text FROM chunks WHERE doc_id = %s", (doc_id,))
        all_text = "\n".join(c["text"] for c in chunks)
        assert "新版内容标记V2" in all_text
        assert "八折优惠" not in all_text

        # UPDATE 重建路径同步补写文档位置字段（存量 NULL 行）
        doc = _query_one(
            "SELECT file_path FROM documents WHERE id = %s", (doc_id,))
        assert doc["file_path"] == SHORT_URL

        doc_ids, results = await _retrieve_doc_ids(tenant_id, "春季活动")
        assert doc_id in doc_ids
        assert all("八折优惠" not in r["text"] for r in results)


class TestSoftDelete:
    async def test_deleted_page_soft_deletes_document(self, tenant_id,
                                                      wechat_mp_fixtures):
        """删除页多信号命中 → documents 软删除，检索不可见、前台默认隐藏。"""
        _create_tenant(tenant_id)
        deleted_html = (wechat_mp_fixtures / "deleted_page.html").read_text(
            encoding="utf-8")
        fetcher = StubFetcher()
        fetcher.set_page(
            SHORT_URL,
            [ok_result(make_article_html("春季活动", BODY_V1)),
             FetchResult(status=STATUS_DELETED, html=deleted_html, http_status=200)],
        )
        svc = _make_service(fetcher)
        _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)
        doc_id = _query_one(
            "SELECT doc_id FROM bs_wechat_mp_articles WHERE tenant_id = %s",
            (tenant_id,))["doc_id"]

        _, _, item_ids, _ = _enqueue(tenant_id, [SHORT_URL], trigger="recheck",
                                     action="check")
        await svc.claim_and_run(tenant_id)

        item = _query_one(
            "SELECT status, action, billing_status FROM bs_wechat_mp_sync_items "
            "WHERE id = %s", (item_ids[0],))
        assert item["status"] == "success" and item["action"] == "delete"
        assert item["billing_status"] == "not_required"

        doc = _query_one("SELECT status FROM documents WHERE id = %s", (doc_id,))
        assert doc["status"] == "deleted"
        article = _query_one(
            "SELECT status, processing_status FROM bs_wechat_mp_articles "
            "WHERE tenant_id = %s", (tenant_id,))
        assert article["status"] == "deleted"

        doc_ids, _ = await _retrieve_doc_ids(tenant_id, "春季活动")
        assert doc_id not in doc_ids

        from src.knowledge.service import KnowledgeBaseService

        assert KnowledgeBaseService().count_documents(tenant_id=tenant_id) == 0


class TestDeferredGate:
    async def test_image_only_article_deferred_not_embedded(self, tenant_id):
        """纯图文章（12 图 0 文字）必须 deferred：占位符残留字符不得越过
        MIN_TEXT_CHARS 门槛（CR 回归：replace 前缀残留 'N]' 曾致漏判并计费）。
        P2 起 VL 组件不可用（无多模态模型，_make_service 默认替身）维持 deferred，
        不触发下载/解析/计费。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        # 12 图：旧算法残留 "1]..12]" 计 38 字符 > 20，可复现漏判路径
        fetcher.set_page(SHORT_URL, ok_result(make_image_only_html("长图活动", 12)))
        embedding = FakeEmbeddingClient()
        svc = _make_service(fetcher, embedding=embedding)

        run_id, row_ids, item_ids, _ = _enqueue(tenant_id, [SHORT_URL])
        result = await svc.claim_and_run(tenant_id)
        assert result["executed"] is True

        item = _query_one(
            "SELECT status, error_code, billing_status, credits_charged "
            "FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[0],))
        assert item["status"] == "deferred"
        assert item["error_code"] == "deferred_image_pending"
        assert item["billing_status"] == "not_required"
        assert float(item["credits_charged"]) == 0.0

        article = _query_one(
            "SELECT processing_status, doc_id, image_count FROM bs_wechat_mp_articles "
            "WHERE id = %s", (row_ids[0],))
        assert article["processing_status"] == "deferred"
        assert article["doc_id"] is None
        assert article["image_count"] == 12

        # 零 embedding 零文档零计费（非技术失败，不进退避）
        assert embedding.embed_batch_calls == 0
        assert _query_one(
            "SELECT COUNT(*) AS c FROM documents WHERE tenant_id = %s",
            (tenant_id,))["c"] == 0
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chat_records WHERE tenant_id = %s",
            (tenant_id,))["c"] == 0
        assert _query_one(
            "SELECT next_retry_at FROM bs_wechat_mp_articles WHERE id = %s",
            (row_ids[0],))["next_retry_at"] is None
        run = _query_one(
            "SELECT status, skipped_count FROM bs_wechat_mp_sync_runs WHERE id = %s",
            (run_id,))
        assert run["status"] == "success" and run["skipped_count"] == 1


class TestBalance:
    async def test_insufficient_balance_skips_run(self, tenant_id):
        """run 启动预检：余额不足 → item skipped_no_credit 终态，不产生文档。"""
        _create_tenant(tenant_id, balance=0.0)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("活动", BODY_V1)))
        run_id, row_ids, item_ids, _ = _enqueue(tenant_id, [SHORT_URL])

        svc = _make_service(fetcher)
        result = await svc.claim_and_run(tenant_id)
        assert result["executed"] is True

        run = _query_one(
            "SELECT status FROM bs_wechat_mp_sync_runs WHERE id = %s", (run_id,))
        assert run["status"] == "skipped_no_credit"
        item = _query_one(
            "SELECT status, error_code FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids[0],))
        assert item["status"] == "skipped" and item["error_code"] == ERR_NO_CREDIT
        article = _query_one(
            "SELECT processing_status, next_retry_at FROM bs_wechat_mp_articles "
            "WHERE id = %s", (row_ids[0],))
        assert article["processing_status"] == "pending"
        assert article["next_retry_at"] is not None  # 退避，不每轮重试
        assert _query_one(
            "SELECT COUNT(*) AS c FROM documents WHERE tenant_id = %s",
            (tenant_id,))["c"] == 0
        assert fetcher.calls == []  # 预检拦截，未抓取

    async def test_delete_recheck_not_blocked_by_balance(self, tenant_id,
                                                         wechat_mp_fixtures):
        """余额耗尽后，删除复核（check 类 item）仍执行软删除。"""
        _create_tenant(tenant_id, balance=100.0)
        deleted_html = (wechat_mp_fixtures / "deleted_page.html").read_text(
            encoding="utf-8")
        fetcher = StubFetcher()
        fetcher.set_page(
            SHORT_URL,
            [ok_result(make_article_html("春季活动", BODY_V1)),
             FetchResult(status=STATUS_DELETED, html=deleted_html, http_status=200)],
        )
        svc = _make_service(fetcher)
        _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)
        doc_id = _query_one(
            "SELECT doc_id FROM bs_wechat_mp_articles WHERE tenant_id = %s",
            (tenant_id,))["doc_id"]

        _create_tenant(tenant_id, balance=0.0)  # 余额耗尽
        _enqueue(tenant_id, [SHORT_URL], trigger="recheck", action="check")
        await svc.claim_and_run(tenant_id)

        assert _query_one("SELECT status FROM documents WHERE id = %s",
                          (doc_id,))["status"] == "deleted"


class TestBillingUnknown:
    async def test_unknown_billing_never_recharged(self, tenant_id, monkeypatch):
        """计费响应不可确认 → unknown；下一轮 hash 未变走 check，禁止自动重扣。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("活动", BODY_V1)))
        svc = _make_service(fetcher)

        from src.db.models import ChatRecordDB

        monkeypatch.setattr(ChatRecordDB, "create", staticmethod(lambda **kw: None))

        _, _, item_ids, _ = _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)
        item = _query_one(
            "SELECT status, billing_status, credits_charged "
            "FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[0],))
        assert item["status"] == "success"  # 计费失败不回滚内容
        assert item["billing_status"] == "unknown"
        assert float(item["credits_charged"]) == 0.0
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chat_records WHERE tenant_id = %s",
            (tenant_id,))["c"] == 0

        monkeypatch.undo()  # 恢复计费；下一轮仍不得补扣
        _, _, item_ids2, _ = _enqueue(tenant_id, [SHORT_URL], trigger="recheck",
                                      action="check")
        await svc.claim_and_run(tenant_id)
        item2 = _query_one(
            "SELECT action, billing_status FROM bs_wechat_mp_sync_items WHERE id = %s",
            (item_ids2[0],))
        assert item2["action"] == "check" and item2["billing_status"] == "not_required"
        assert _query_one(
            "SELECT COUNT(*) AS c FROM chat_records WHERE tenant_id = %s",
            (tenant_id,))["c"] == 0
        assert _tenant_balance(tenant_id) == 100.0


class TestQueueSerial:
    async def test_drains_multiple_queued_runs_in_order(self, tenant_id):
        """一次 claim_and_run 依 created 顺序排空多个 queued run。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("文章甲", BODY_V1)))
        fetcher.set_page(SHORT_URL_B, ok_result(make_article_html("文章乙", BODY_V2)))
        run1, _, _, _ = _enqueue(tenant_id, [SHORT_URL])
        run2, _, _, _ = _enqueue(tenant_id, [SHORT_URL_B])

        svc = _make_service(fetcher)
        result = await svc.claim_and_run(tenant_id)
        assert result["run_ids"] == [run1, run2]
        statuses = _query_all(
            "SELECT status FROM bs_wechat_mp_sync_runs WHERE tenant_id = %s "
            "ORDER BY id", (tenant_id,))
        assert [s["status"] for s in statuses] == ["success", "success"]
        assert _query_one(
            "SELECT COUNT(*) AS c FROM documents WHERE tenant_id = %s",
            (tenant_id,))["c"] == 2

    async def test_lock_held_refuses(self, tenant_id):
        """同租户锁被持有 → 拒绝执行，队列不动。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("活动", BODY_V1)))
        run_id, _, _, _ = _enqueue(tenant_id, [SHORT_URL])

        redis = FakeRedis()
        lock_key = redis.make_key(svc_mod.LOCK_KEY_PREFIX, tenant_id)
        redis.acquire_lock(lock_key, json.dumps("other-owner"), ex=1800)

        svc = _make_service(fetcher, redis=redis)
        result = await svc.claim_and_run(tenant_id)
        assert result["executed"] is False and result["reason"] == "locked"
        assert _query_one(
            "SELECT status FROM bs_wechat_mp_sync_runs WHERE id = %s",
            (run_id,))["status"] == "queued"

    async def test_running_unique_conflict_yields(self, tenant_id):
        """DB 已有 running run（第二道闸）→ 领取撞唯一索引让位。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("活动", BODY_V1)))
        run_id, _, _, _ = _enqueue(tenant_id, [SHORT_URL])
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_runs
                    (tenant_id, trigger_type, status, owner_token, heartbeat_at)
                VALUES (%s, 'manual', 'running', 'other-worker', now())
                """,
                (tenant_id,),
            )
            conn.commit()

        svc = _make_service(fetcher)
        result = await svc.claim_and_run(tenant_id)
        assert result["reason"] == "db_running_conflict"
        assert _query_one(
            "SELECT status FROM bs_wechat_mp_sync_runs WHERE id = %s",
            (run_id,))["status"] == "queued"

    async def test_redis_unavailable_refuses(self, tenant_id):
        """Redis 不可用拒绝执行（不无锁降级）。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        fetcher.set_page(SHORT_URL, ok_result(make_article_html("活动", BODY_V1)))
        run_id, _, _, _ = _enqueue(tenant_id, [SHORT_URL])

        svc = _make_service(fetcher, redis=FakeRedis(available=False))
        result = await svc.claim_and_run(tenant_id)
        assert result["executed"] is False
        assert result["reason"] == "redis_unavailable"
        assert _query_one(
            "SELECT status FROM bs_wechat_mp_sync_runs WHERE id = %s",
            (run_id,))["status"] == "queued"


class TestStaleRecovery:
    def test_stale_running_interrupted_queued_untouched(self, tenant_id):
        """heartbeat 超时且锁已失效 → interrupted；锁仍在 → 保留；queued 不动。"""
        from src.db.database import get_db_connection

        stale_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
        live_tenant = f"{tenant_id}-live"  # 另一租户规避 running 唯一索引
        redis = FakeRedis()
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                # stale 且锁已失效
                cursor.execute(
                    """
                    INSERT INTO bs_wechat_mp_sync_runs
                        (tenant_id, trigger_type, status, owner_token, heartbeat_at)
                    VALUES (%s, 'callback', 'running', 'dead-owner', %s)
                    RETURNING id
                    """,
                    (tenant_id, stale_time),
                )
                stale_run = cursor.fetchone()["id"]
                # stale heartbeat 但锁仍被 owner 持有（worker 活着）
                cursor.execute(
                    """
                    INSERT INTO bs_wechat_mp_sync_runs
                        (tenant_id, trigger_type, status, owner_token, heartbeat_at)
                    VALUES (%s, 'callback', 'running', 'live-owner', %s)
                    RETURNING id
                    """,
                    (live_tenant, stale_time),
                )
                live_run = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO bs_wechat_mp_sync_runs (tenant_id, trigger_type, status)
                    VALUES (%s, 'callback', 'queued')
                    """,
                    (tenant_id,),
                )
                conn.commit()
            lock_key = redis.make_key(svc_mod.LOCK_KEY_PREFIX, live_tenant)
            redis.acquire_lock(lock_key, json.dumps("live-owner"), ex=1800)

            svc = _make_service(StubFetcher(), redis=redis)
            outcome = svc.recover_stale_runs()
            assert outcome["interrupted"] == 1
            assert _query_one(
                "SELECT status FROM bs_wechat_mp_sync_runs WHERE id = %s",
                (stale_run,))["status"] == "interrupted"
            assert _query_one(
                "SELECT status FROM bs_wechat_mp_sync_runs WHERE id = %s",
                (live_run,))["status"] == "running"
            assert _query_one(
                "SELECT COUNT(*) AS c FROM bs_wechat_mp_sync_runs "
                "WHERE tenant_id = %s AND status = 'queued'", (tenant_id,))["c"] == 1
        finally:
            cleanup_tenant(live_tenant)


class TestAliasConvergence:
    async def test_same_batch_duplicate_item_skipped(self, tenant_id):
        """同批次短长链同文：短链先建为主记录，长链行 alias + item skipped
        + duplicate_of_item_id 关联主 item，不触发唯一键冲突，只入一篇文档。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        # 短链页给出对方（长链）形态
        fetcher.set_page(SHORT_URL, ok_result(
            make_article_html("同文文章", BODY_V1, msg_link=LONG_URL)))
        fetcher.set_page(LONG_URL, ok_result(
            make_article_html("同文文章", BODY_V1, msg_link=LONG_URL)))
        run_id, row_ids, item_ids, _ = _enqueue(tenant_id, [SHORT_URL, LONG_URL])

        svc = _make_service(fetcher)
        result = await svc.claim_and_run(tenant_id)
        assert result["executed"] is True

        master = _query_one(
            "SELECT * FROM bs_wechat_mp_articles WHERE id = %s", (row_ids[0],))
        alias = _query_one(
            "SELECT * FROM bs_wechat_mp_articles WHERE id = %s", (row_ids[1],))
        assert master["status"] == "active" and master["doc_id"]
        assert alias["status"] == "alias"
        assert alias["master_article_row_id"] == master["id"]

        alias_item = _query_one(
            "SELECT status, error_code, duplicate_of_item_id "
            "FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[1],))
        assert alias_item["status"] == "skipped"
        assert alias_item["error_code"] == ERR_ALIAS_DUPLICATE
        assert alias_item["duplicate_of_item_id"] == item_ids[0]

        assert _query_one(
            "SELECT COUNT(*) AS c FROM documents WHERE tenant_id = %s",
            (tenant_id,))["c"] == 1
        run = _query_one(
            "SELECT status, new_count, skipped_count FROM bs_wechat_mp_sync_runs "
            "WHERE id = %s", (run_id,))
        assert run["status"] == "success"
        assert run["new_count"] == 1 and run["skipped_count"] == 1

    async def test_cross_batch_alias_doc_merged(self, tenant_id):
        """跨批次：别名行已有 doc → 收敛时 documents 置 deleted +
        merged_into_doc_id，检索不再可见。"""
        _create_tenant(tenant_id)
        fetcher = StubFetcher()
        # 各自受理时页面回显自身形态（证据不足，不合并）→ 各自独立入库
        fetcher.set_page(SHORT_URL, ok_result(
            make_article_html("主记录文章", "主记录文章内容，独立入库。")))
        fetcher.set_page(SHORT_URL_B, ok_result(
            make_article_html("别名文章", "别名文章独有文本标记ALIAS。")))
        svc = _make_service(fetcher)
        _enqueue(tenant_id, [SHORT_URL])
        await svc.claim_and_run(tenant_id)
        _enqueue(tenant_id, [SHORT_URL_B])
        await svc.claim_and_run(tenant_id)
        master_row = _query_all(
            "SELECT id, doc_id FROM bs_wechat_mp_articles "
            "WHERE tenant_id = %s ORDER BY id", (tenant_id,))
        assert len(master_row) == 2
        master_id, master_doc = master_row[0]["id"], master_row[0]["doc_id"]
        alias_id, alias_doc = master_row[1]["id"], master_row[1]["doc_id"]

        # B 复核时页面给出 A 的短链形态 → 收敛：A 先成功入库为主记录
        fetcher.set_page(SHORT_URL_B, ok_result(
            make_article_html("别名文章", "别名文章独有文本标记ALIAS。",
                              msg_link=SHORT_URL)))
        _, _, item_ids, _ = _enqueue(tenant_id, [SHORT_URL_B], trigger="recheck",
                                     action="check")
        await svc.claim_and_run(tenant_id)

        alias_article = _query_one(
            "SELECT status, master_article_row_id FROM bs_wechat_mp_articles "
            "WHERE id = %s", (alias_id,))
        assert alias_article["status"] == "alias"
        assert alias_article["master_article_row_id"] == master_id

        alias_item = _query_one(
            "SELECT status, error_code, duplicate_of_item_id "
            "FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[0],))
        assert alias_item["status"] == "skipped"
        assert alias_item["error_code"] == ERR_ALIAS_DUPLICATE
        assert alias_item["duplicate_of_item_id"] is None  # 跨批次，主 item 不在本 run

        merged_doc = _query_one(
            "SELECT status, metadata FROM documents WHERE id = %s", (alias_doc,))
        assert merged_doc["status"] == "deleted"
        assert json.loads(merged_doc["metadata"])["merged_into_doc_id"] == master_doc

        doc_ids, _ = await _retrieve_doc_ids(tenant_id, "别名文章")
        assert alias_doc not in doc_ids
        master_ids, _ = await _retrieve_doc_ids(tenant_id, "主记录")
        assert master_doc in master_ids
