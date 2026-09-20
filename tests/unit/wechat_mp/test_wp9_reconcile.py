"""WP9 接口通道对账与 freepublish 单 item 管道单元测试（真实 PG，require_db 门禁）。

覆盖（计划 WP9 节 + 设计 §5.2/§5.4/§6.1 P3 粒度 + §2 测试矩阵）：
- 对账 diff：新增建行+item、未变跳过不重拉正文、update_time/sync_failed/pipeline
  变化建 item、活跃 item 门禁、missing 重现恢复、deleted 重现（源时间变化才恢复）
- 整条缺失防护：首次缺失置 missing；连续两轮缺失 + getarticle 53600 才软删；
  其他 errcode/异常保留 missing；不完整扫描（complete=False）不迁移
- 对账失败：API/结构异常 → run=failed（不得误报 success）
- 调度生成器 ③：verified/enabled/appid/secret 过滤、interval 到期（含解析容错）、
  queued/running 防堆积、scheduled run 无 items
- 单 item 管道：多图文合并保序（子篇标题前置）、is_deleted 子篇排除、全删软删、
  空数组/结构异常失败保留旧版（绝不判删除）、hash 快路径零计费（recheck）、
  跨来源互标 related_doc_ids、file_path/original_url 回填首个未删子篇 url

微信接口全部 mock（FakeMPAPIClient），不打真实接口；队列/落库/计费链路走真实
DB + test_service 替身组件（FakeEmbedding/FakeSummarizer/FakeRedis）。真实
getarticle content 夹具：tests/fixtures/wechat_mp/freepublish_article_content.html。
"""

import json
from datetime import datetime, timezone

import pytest

from src.wechat_mp import service as svc_mod
from src.wechat_mp.client import ArticleNotFoundError, WeChatMPAPIError
from src.wechat_mp.service import (
    FREEPUBLISH_CHANNEL,
    PIPELINE_VERSION,
    freepublish_external_id,
)
from src.wechat_mp.scheduler import WeChatMPScheduler

from . import test_service as ts

# ------------------------------- 常量与替身 -------------------------------

MP_APPID = "wxwp9testappid01"
MP_SECRET = "wp9-unit-test-secret"
CONFIG_ID = "chan_wp9test01"
ARTICLE_ID = "A" * 64
EXTERNAL_ID = freepublish_external_id(MP_APPID, ARTICLE_ID)
UPDATE_TS = 1789374363  # unix 秒（主控者探测同数量级）
UPDATE_TS_NAIVE = datetime.fromtimestamp(UPDATE_TS, tz=timezone.utc).replace(tzinfo=None)
SHORT_URL = "https://mp.weixin.qq.com/s/Wp9SubDefault1"
CROSS_TOKEN = "Wp9TokCross001"
CROSS_URL = f"https://mp.weixin.qq.com/s/{CROSS_TOKEN}"
LONG_URL = (
    "https://mp.weixin.qq.com/s?__biz=MzWP9Test&mid=2247483728&idx=1&sn=wp9snvalue001"
)
URL_A = "https://mp.weixin.qq.com/s/Wp9SubKeepA001"
URL_C = "https://mp.weixin.qq.com/s/Wp9SubKeepC001"


class FakeMPAPIClient:
    """freepublish 接口替身：脚本化 batchget/getarticle，记录调用（不打真实接口）。"""

    def __init__(self):
        self.messages = []  # batchget_all 返回（client 输出形态：已规整的消息元数据）
        self.complete = True
        self.articles = {}  # article_id -> 详情 dict 或 Exception
        self.batchget_error = None
        self.batchget_calls = 0
        self.getarticle_calls = []

    def batchget_all(self, *, no_content: int = 1):
        from src.wechat_mp.client import BatchgetScanResult

        self.batchget_calls += 1
        if self.batchget_error is not None:
            raise self.batchget_error
        result = BatchgetScanResult(
            messages=list(self.messages),
            total_count=len(self.messages),
            fetched=len(self.messages),
        )
        result.complete = self.complete and result.fetched == result.total_count
        return result

    def getarticle(self, article_id: str):
        self.getarticle_calls.append(article_id)
        item = self.articles[article_id]
        if isinstance(item, Exception):
            raise item
        return dict(item)


def _patch_api_client(monkeypatch, fake: FakeMPAPIClient) -> None:
    """monkeypatch service 的客户端构造点（_build_api_client 经模块名查找）。"""
    monkeypatch.setattr(svc_mod, "WeChatMPAPIClient", lambda **kwargs: fake)


def _patch_channel_config(monkeypatch, tenant_id: str, config_id: str = CONFIG_ID, config=None):
    """替换 ChannelConfigDB.get_by_id_decrypted（API 层 mock，不写真实配置表）。"""
    from src.saas.db import channel_config_db

    payload = {
        "tenant_id": tenant_id,
        "channel_type": "wechat_mp",
        "config": config if config is not None else {"appid": MP_APPID, "secret": MP_SECRET},
    }

    def _fake_get(cid):
        return dict(payload) if cid == config_id else None

    monkeypatch.setattr(
        channel_config_db.ChannelConfigDB, "get_by_id_decrypted", staticmethod(_fake_get)
    )


@pytest.fixture(autouse=True)
def _real_vector_db_patch(monkeypatch):
    """service 内 get_vector_db 替换为真实 pgvector 实现（对齐 test_service）。"""
    module = ts._load_real_vector_db()
    monkeypatch.setattr(svc_mod, "get_vector_db", module.get_vector_db)


@pytest.fixture()
def billed_tenant(tenant_id):
    ts._create_tenant(tenant_id, 100.0)
    return tenant_id


@pytest.fixture()
def fixture_html(wechat_mp_fixtures):
    """主控者 2026-09-15 真实 getarticle content（freepublish 正文片段，UTF-8）。"""
    return (wechat_mp_fixtures / "freepublish_article_content.html").read_text(encoding="utf-8")


# ------------------------------- DB 辅助 -------------------------------


def _batch_message(article_id: str = ARTICLE_ID, update_ts: int = UPDATE_TS, **extra):
    msg = {
        "article_id": article_id,
        "update_time": update_ts,
        "create_time": update_ts - 3600,
        "first_title": "接口通道首篇",
        "first_url": SHORT_URL,
    }
    msg.update(extra)
    return msg


def _sub(**over):
    """getarticle news_item 子篇（结构对齐主控者探测记录）。"""
    base = {
        "title": "子篇标题",
        "author": "WP9测试号",
        "digest": "摘要",
        "content": '<div id="js_content"><p>默认正文</p></div>',
        "content_source_url": "",
        "thumb_media_id": "thumb",
        "show_cover_pic": 0,
        "url": SHORT_URL,
        "thumb_url": "",
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
        "is_deleted": False,
    }
    base.update(over)
    return base


def _detail(*subs, create_ts=None, update_ts=UPDATE_TS):
    detail = {"news_item": list(subs), "update_time": update_ts}
    if create_ts is not None:
        detail["create_time"] = create_ts
    return detail


def _create_scheduled_run(tenant_id: str, config_id: str = CONFIG_ID, trigger: str = "scheduled"):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_runs
                (tenant_id, config_id, user_id, trigger_type, status, total_count)
            VALUES (%s, %s, NULL, %s, 'queued', 0)
            RETURNING id
            """,
            (tenant_id, config_id, trigger),
        )
        run_id = cursor.fetchone()["id"]
        conn.commit()
    return run_id


def _seed_article(
    tenant_id: str,
    external_id: str = EXTERNAL_ID,
    *,
    status: str = "active",
    processing_status: str = "success",
    wx_update_time=UPDATE_TS_NAIVE,
    pipeline_version: str = PIPELINE_VERSION,
    doc_id=None,
    config_id: str = CONFIG_ID,
    source_channel: str = FREEPUBLISH_CHANNEL,
    content_hash: str = "seedhash",
    title: str = "存量文章",
) -> int:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_articles
                (tenant_id, config_id, external_id, original_url, fetch_url,
                 source_channel, title, wx_update_time, content_hash, doc_id,
                 status, processing_status, pipeline_version)
            VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                tenant_id, config_id, external_id, SHORT_URL, source_channel, title,
                wx_update_time, content_hash, doc_id, status, processing_status,
                pipeline_version,
            ),
        )
        row_id = cursor.fetchone()["id"]
        conn.commit()
    return row_id


def _insert_document(tenant_id: str, external_id: str, status: str = "active") -> int:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO documents (
                tenant_id, title, source_type, sub_category, file_type, file_path,
                file_size, total_chunks, embedding_model, raw_text, metadata, summary,
                uuid, origin, external_id, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                tenant_id, "WP9测试文档", "k_wechat_mp", "k_wechat_mp_uncategorized",
                "html", SHORT_URL, 100, 1, "text-embedding-v3", "raw", "{}", None,
                f"doc_wp9_{external_id[:20]}", "wechat_mp", external_id, status,
            ),
        )
        doc_id = cursor.fetchone()["id"]
        conn.commit()
    return doc_id


def _enqueue_item(tenant_id: str, run_id: int, article_row_id: int, action=None) -> int:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_items
                (tenant_id, user_id, run_id, article_row_id, action, status)
            VALUES (%s, NULL, %s, %s, %s, 'pending')
            RETURNING id
            """,
            (tenant_id, run_id, article_row_id, action),
        )
        item_id = cursor.fetchone()["id"]
        conn.commit()
    return item_id


def _article_row(tenant_id: str, external_id: str = EXTERNAL_ID):
    return ts._query_one(
        "SELECT * FROM bs_wechat_mp_articles WHERE tenant_id = %s AND external_id = %s",
        (tenant_id, external_id),
    )


# ------------------------------- 对账 diff -------------------------------


async def test_reconcile_new_message_ingests_document(monkeypatch, billed_tenant, fixture_html):
    """新消息 → 建 articles 行 + item → getarticle 入库（run success new_count=1）。"""
    tenant = billed_tenant
    fake = FakeMPAPIClient()
    fake.messages = [_batch_message()]
    fake.articles = {ARTICLE_ID: _detail(_sub(content=fixture_html, url=LONG_URL))}
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)

    run_id = _create_scheduled_run(tenant)
    svc = ts._make_service(ts.StubFetcher())
    result = await svc.claim_and_run(tenant)
    assert result["executed"] and result["run_ids"] == [run_id]

    run = ts._query_one("SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (run_id,))
    assert run["status"] == "success"
    assert run["total_count"] == 1 and run["new_count"] == 1 and run["skipped_count"] == 0

    row = _article_row(tenant)
    assert row["source_channel"] == FREEPUBLISH_CHANNEL
    assert row["config_id"] == CONFIG_ID
    assert row["processing_status"] == "success" and row["doc_id"]
    assert row["wx_update_time"] == UPDATE_TS_NAIVE
    # original_url 回填首个未删子篇 url
    assert row["original_url"] == LONG_URL

    doc = ts._query_one("SELECT * FROM documents WHERE id = %s", (row["doc_id"],))
    assert doc["status"] == "active"
    assert doc["file_path"] == LONG_URL  # WP12 语义：文档位置字段
    meta = json.loads(doc["metadata"])
    assert meta["sub_articles"][0]["is_deleted"] is False
    assert fake.getarticle_calls == [ARTICLE_ID]


async def test_reconcile_unchanged_skips_content_repull(monkeypatch, billed_tenant, fixture_html):
    """未变且 success 且 pipeline 相同：不建 item 不重拉正文，仅计数 skipped。"""
    tenant = billed_tenant
    fake = FakeMPAPIClient()
    fake.messages = [_batch_message()]
    fake.articles = {ARTICLE_ID: _detail(_sub(content=fixture_html))}
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)

    svc = ts._make_service(ts.StubFetcher())
    _create_scheduled_run(tenant)  # 首轮 run（结果经 article/doc 断言，不引用 run id）
    await svc.claim_and_run(tenant)
    assert fake.getarticle_calls == [ARTICLE_ID]

    run2 = _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)
    run = ts._query_one("SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (run2,))
    assert run["status"] == "success"
    assert run["total_count"] == 1 and run["skipped_count"] == 1 and run["new_count"] == 0
    items = ts._query_all(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s AND tenant_id = %s",
        (run2, tenant),
    )
    assert items == []  # 未变不建 item
    assert fake.getarticle_calls == [ARTICLE_ID]  # 核心要求：不重复拉取正文


async def test_reconcile_source_change_builds_update_item(monkeypatch, billed_tenant, fixture_html):
    """源 update_time 变化 + 内容变化 → 建 item → update 路径 + wx_update_time 推进。"""
    tenant = billed_tenant
    fake = FakeMPAPIClient()
    fake.messages = [_batch_message(update_ts=UPDATE_TS)]
    fake.articles = {ARTICLE_ID: _detail(_sub(content=fixture_html), update_ts=UPDATE_TS)}
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)
    svc = ts._make_service(ts.StubFetcher())

    _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)  # 首轮入库

    # 源时间变化且正文变化（追加段落）：hash 变化 → 走 update 重建
    new_ts = UPDATE_TS + 600
    changed_html = fixture_html + "<section><p>新增段落：WP9 更新后内容变更标记。</p></section>"
    fake.messages = [_batch_message(update_ts=new_ts)]
    fake.articles = {ARTICLE_ID: _detail(_sub(content=changed_html), update_ts=new_ts)}
    run2 = _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)

    run = ts._query_one("SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (run2,))
    assert run["status"] == "success" and run["updated_count"] == 1
    item = ts._query_one(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s AND tenant_id = %s",
        (run2, tenant),
    )
    assert item["status"] == "success" and item["action"] == "update"
    row = _article_row(tenant)
    assert row["wx_update_time"] == datetime.fromtimestamp(
        new_ts, tz=timezone.utc
    ).replace(tzinfo=None)
    doc = ts._query_one("SELECT raw_text FROM documents WHERE id = %s", (row["doc_id"],))
    assert "WP9 更新后内容变更标记" in doc["raw_text"]


async def test_reconcile_sync_failed_and_pipeline_mismatch_build_items(
    monkeypatch, billed_tenant, fixture_html
):
    """sync_failed / pipeline_version 不匹配：即使源时间未变也建 item。"""
    tenant = billed_tenant
    fake = FakeMPAPIClient()
    fake.messages = [_batch_message()]
    fake.articles = {ARTICLE_ID: _detail(_sub(content=fixture_html))}
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)
    svc = ts._make_service(ts.StubFetcher())

    # sync_failed 行（源时间一致、pipeline 一致）
    failed_row = _seed_article(tenant, processing_status="sync_failed")
    run1 = _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)
    item = ts._query_one(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s AND article_row_id = %s",
        (run1, failed_row),
    )
    assert item is not None and item["status"] == "success"

    # pipeline 不匹配行（独立 article_id，避免与场景 1 的身份冲突）
    article_id_2 = "B" * 64
    stale_row = _seed_article(
        tenant, freepublish_external_id(MP_APPID, article_id_2),
        pipeline_version="p1", content_hash="oldhash",
    )
    fake.messages = [
        _batch_message(),
        _batch_message(article_id=article_id_2),
    ]
    fake.articles = {
        ARTICLE_ID: _detail(_sub(content=fixture_html)),
        article_id_2: _detail(_sub(content=fixture_html)),
    }
    run2 = _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)
    item2 = ts._query_one(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s AND article_row_id = %s",
        (run2, stale_row),
    )
    assert item2 is not None and item2["status"] == "success"


async def test_reconcile_skips_when_active_item_exists(monkeypatch, billed_tenant, fixture_html):
    """文章已有挂在 queued run 上的 pending item：对账不重复建 item（NOT EXISTS 门禁）。

    直接调 _reconcile_config（不经 claim_and_run）：claim 会先消化既有 queued run，
    门禁前提（item 仍 pending）就消失了。
    """
    tenant = billed_tenant
    fake = FakeMPAPIClient()
    fake.messages = [_batch_message()]
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)

    row_id = _seed_article(tenant, processing_status="sync_failed")
    _create_scheduled_run(tenant, trigger="retry")
    queued_run_id = ts._query_one(
        """
        SELECT id FROM bs_wechat_mp_sync_runs
        WHERE tenant_id = %s AND trigger_type = 'retry'
        """,
        (tenant,),
    )["id"]
    _enqueue_item(tenant, queued_run_id, row_id)

    scheduled_run = _create_scheduled_run(tenant)
    svc = ts._make_service(ts.StubFetcher())
    stats = svc._reconcile_config(
        tenant, {"id": scheduled_run, "config_id": CONFIG_ID},
        {"appid": MP_APPID, "secret": MP_SECRET},
    )
    assert stats == (1, 0)
    scheduled_items = ts._query_all(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s", (scheduled_run,)
    )
    assert scheduled_items == []


# ------------------------------- 整条缺失防护 -------------------------------


async def test_missing_two_rounds_then_53600_soft_deletes(monkeypatch, billed_tenant):
    """完整扫描缺失：首轮置 missing；次轮仍缺失且 53600 → 同事务软删 article+doc。"""
    tenant = billed_tenant
    doc_id = _insert_document(tenant, EXTERNAL_ID)
    _seed_article(tenant, doc_id=doc_id)
    fake = FakeMPAPIClient()
    fake.messages = []  # 源空集合（total=0，complete=True）
    fake.articles = {ARTICLE_ID: ArticleNotFoundError()}
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)
    svc = ts._make_service(ts.StubFetcher())

    run1 = _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)
    assert _article_row(tenant)["status"] == "missing"  # 首轮仅标记
    doc_state = ts._query_one("SELECT status FROM documents WHERE id = %s", (doc_id,))
    assert doc_state["status"] == "active"  # 未删
    assert fake.getarticle_calls == []

    run2 = _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)
    row = _article_row(tenant)
    assert row["status"] == "deleted" and row["processing_status"] == "success"
    doc = ts._query_one("SELECT status FROM documents WHERE id = %s", (doc_id,))
    assert doc["status"] == "deleted"
    assert fake.getarticle_calls == [ARTICLE_ID]  # 详情复核
    assert (
        ts._query_one("SELECT status FROM bs_wechat_mp_sync_runs WHERE id = %s", (run2,))[
            "status"
        ]
        == "success"
    )
    assert (
        ts._query_one("SELECT status FROM bs_wechat_mp_sync_runs WHERE id = %s", (run1,))[
            "status"
        ]
        == "success"
    )


async def test_missing_recheck_other_error_keeps_missing(monkeypatch, billed_tenant):
    """次轮复核遇非 53600 错误（限频等）：保留 missing 不删，run 不失败。"""
    tenant = billed_tenant
    _seed_article(tenant, status="missing")
    fake = FakeMPAPIClient()
    fake.messages = []
    fake.articles = {ARTICLE_ID: WeChatMPAPIError(45009, "微信接口限频（45009），稍后重试")}
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)
    svc = ts._make_service(ts.StubFetcher())

    run_id = _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)
    assert _article_row(tenant)["status"] == "missing"  # 保留待审计
    run = ts._query_one("SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (run_id,))
    assert run["status"] == "success"


async def test_incomplete_scan_skips_missing_migration(monkeypatch, billed_tenant):
    """本轮不完整可靠（重复页/总数漂移）：不做任何 missing 迁移，连续两轮亦然。"""
    tenant = billed_tenant
    _seed_article(tenant)  # active 且不在本轮集合
    fake = FakeMPAPIClient()
    fake.messages = []
    fake.complete = False
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)
    svc = ts._make_service(ts.StubFetcher())

    await svc.claim_and_run(tenant)
    await svc.claim_and_run(tenant)
    assert _article_row(tenant)["status"] == "active"  # 两轮都未迁移
    assert fake.getarticle_calls == []


async def test_missing_row_reappears_and_restores(monkeypatch, billed_tenant, fixture_html):
    """missing 行重新出现在集合：置回 active 并建 item（重现恢复语义）。"""
    tenant = billed_tenant
    _seed_article(tenant, status="missing", processing_status="success")
    fake = FakeMPAPIClient()
    fake.messages = [_batch_message()]
    fake.articles = {ARTICLE_ID: _detail(_sub(content=fixture_html))}
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)
    svc = ts._make_service(ts.StubFetcher())

    run_id = _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)
    row = _article_row(tenant)
    assert row["status"] == "active" and row["doc_id"]
    item = ts._query_one(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s", (run_id,)
    )
    assert item is not None and item["status"] == "success"


async def test_deleted_row_republish_restores(monkeypatch, billed_tenant, fixture_html):
    """deleted 行重现且源时间变化（重新发布）→ 恢复 active 并重建；源时间未变保持删除。"""
    tenant = billed_tenant
    deleted_row = _seed_article(tenant, status="deleted", processing_status="success")
    fake = FakeMPAPIClient()
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)
    svc = ts._make_service(ts.StubFetcher())

    # 源时间未变：保持 deleted 静默跳过
    fake.messages = [_batch_message(update_ts=UPDATE_TS)]
    _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)
    assert _article_row(tenant, )[  # deleted_row 仍是 EXTERNAL_ID
        "status"
    ] == "deleted"
    assert fake.getarticle_calls == []

    # 源时间变化：恢复 active + 建 item → 重建入库
    new_ts = UPDATE_TS + 7200
    fake.messages = [_batch_message(update_ts=new_ts)]
    fake.articles = {ARTICLE_ID: _detail(_sub(content=fixture_html), update_ts=new_ts)}
    run2 = _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)
    row = _article_row(tenant)
    assert row["id"] == deleted_row and row["status"] == "active" and row["doc_id"]
    item = ts._query_one(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s", (run2,)
    )
    assert item["status"] == "success"


async def test_reconcile_api_failure_marks_run_failed(monkeypatch, billed_tenant):
    """batchget 失败/结构异常 → run=failed（脱敏 error_message），不得误报 success。"""
    tenant = billed_tenant
    fake = FakeMPAPIClient()
    fake.batchget_error = WeChatMPAPIError(None, "batchget 响应结构异常（缺 article_id）")
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)
    svc = ts._make_service(ts.StubFetcher())

    run_id = _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)
    run = ts._query_one("SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (run_id,))
    assert run["status"] == "failed"
    assert run["error_message"] and "batchget" in run["error_message"]


async def test_reconcile_config_missing_marks_run_failed(monkeypatch, billed_tenant):
    """配置不可用（secret 缺失）→ run=failed，不误报 success。"""
    tenant = billed_tenant
    fake = FakeMPAPIClient()
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(
        monkeypatch, tenant, config={"appid": MP_APPID, "secret": ""}
    )
    svc = ts._make_service(ts.StubFetcher())
    run_id = _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)
    run = ts._query_one("SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (run_id,))
    assert run["status"] == "failed"
    assert fake.batchget_calls == 0


# ------------------------------- 调度生成器 ③ -------------------------------


def _insert_mp_config(
    tenant_id: str,
    config_id: str,
    *,
    verified: int = 1,
    cfg: dict = None,
) -> str:
    from src.db.database import get_db_connection

    cfg = cfg if cfg is not None else {
        "enabled": True, "appid": "wxmpscan01", "secret": "scan-secret", "sync_interval_hours": 6,
    }
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tenant_channel_configs (config_id, tenant_id, channel_type, config, verified)
            VALUES (%s, %s, 'wechat_mp', %s, %s)
            """,
            (config_id, tenant_id, json.dumps(cfg, ensure_ascii=False), verified),
        )
        conn.commit()
    return config_id


@pytest.fixture()
def mp_config_cleanup():
    from src.db.database import get_db_connection

    ids = []

    yield ids.append

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for config_id in ids:
            cursor.execute(
                "DELETE FROM tenant_channel_configs WHERE config_id = %s", (config_id,)
            )
        conn.commit()


def _scheduled_runs(tenant_id: str):
    return ts._query_all(
        """
        SELECT r.* FROM bs_wechat_mp_sync_runs r
        WHERE r.tenant_id = %s AND r.trigger_type = 'scheduled'
        ORDER BY r.id ASC
        """,
        (tenant_id,),
    )


def _backfill_last_scheduled_run(tenant_id: str, config_id: str, hours_ago: float, status="success"):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_runs
                (tenant_id, config_id, trigger_type, status, total_count,
                 created_at, completed_at)
            VALUES (%s, %s, 'scheduled', %s, 0,
                    now() - make_interval(hours => %s), now())
            """,
            (tenant_id, config_id, status, hours_ago),
        )
        conn.commit()


def test_tick_scheduled_creates_due_run(mp_config_cleanup, tenant_id):
    scheduler = WeChatMPScheduler(redis=ts.FakeRedis(), service=object())
    config_id = _insert_mp_config(tenant_id, "chan_wp9scan01")
    mp_config_cleanup(config_id)

    assert scheduler._tick_scheduled() == 1
    runs = _scheduled_runs(tenant_id)
    assert len(runs) == 1
    assert runs[0]["config_id"] == config_id and runs[0]["status"] == "queued"
    # scheduled run 无 items（worker 对账后按 diff 建）
    items = ts._query_all(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s", (runs[0]["id"],)
    )
    assert items == []

    # 防堆积：queued 存在 → 不再生成
    assert scheduler._tick_scheduled() == 0

    # 刚跑完（未到 interval）→ 不生成
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_wechat_mp_sync_runs SET status = 'success' WHERE tenant_id = %s",
            (tenant_id,),
        )
        conn.commit()
    assert scheduler._tick_scheduled() == 0  # created_at 仍在 interval 内

    # 回拨到 7h 前（默认 6h）→ 到期重新生成
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_wechat_mp_sync_runs
            SET created_at = now() - make_interval(hours => 7)
            WHERE tenant_id = %s
            """,
            (tenant_id,),
        )
        conn.commit()
    assert scheduler._tick_scheduled() == 1
    assert len(_scheduled_runs(tenant_id)) == 2


def test_tick_scheduled_filters_and_interval(mp_config_cleanup, tenant_id):
    scheduler = WeChatMPScheduler(redis=ts.FakeRedis(), service=object())
    # 合格配置（12h 周期）
    ok_id = _insert_mp_config(
        tenant_id, "chan_wp9ok", cfg={"enabled": True, "appid": "wxok", "secret": "s",
                                      "sync_interval_hours": 12}
    )
    mp_config_cleanup(ok_id)
    # enabled=False / 缺 secret / 未验证：全部跳过
    mp_config_cleanup(
        _insert_mp_config(tenant_id, "chan_wp9off",
                          cfg={"enabled": False, "appid": "wx1", "secret": "s"})
    )
    mp_config_cleanup(
        _insert_mp_config(tenant_id, "chan_wp9nosecret",
                          cfg={"enabled": True, "appid": "wx2"})
    )
    mp_config_cleanup(
        _insert_mp_config(tenant_id, "chan_wp9unverified", verified=0,
                          cfg={"enabled": True, "appid": "wx3", "secret": "s"})
    )

    assert scheduler._tick_scheduled() == 1
    runs = _scheduled_runs(tenant_id)
    assert len(runs) == 1 and runs[0]["config_id"] == ok_id

    # 12h 周期：7h 前跑过 → 未到期；13h 前（且无 queued）→ 到期
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_wechat_mp_sync_runs SET status = 'success', "
            "created_at = now() - make_interval(hours => 7) WHERE config_id = %s",
            (ok_id,),
        )
        conn.commit()
    assert scheduler._tick_scheduled() == 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_wechat_mp_sync_runs SET created_at = now() - make_interval(hours => 13) "
            "WHERE config_id = %s",
            (ok_id,),
        )
        conn.commit()
    assert scheduler._tick_scheduled() == 1


def test_parse_sync_interval_hours_extreme_values_clamped():
    """CR 回归：NaN/inf/超大值不得溢出 timedelta 或穿透为非法周期（殃及整个生成器）。

    float("1e999")=inf、float(10**400) 抛 OverflowError、json 可产出超大 int——
    解析必须统一回落默认 6h 或夹到上限，绝不向上抛异常。
    """
    parse = WeChatMPScheduler._parse_sync_interval_hours
    from datetime import timedelta

    for bad in ("abc", None, 0, -3, float("nan"), float("inf"), float("-inf"), 10**400):
        value = parse(bad)
        assert value == 6.0 or value == 24 * 365
        timedelta(hours=value)  # 不抛 OverflowError/ValueError
    assert parse("9") == 9.0
    assert parse(0.5) == 0.5
    assert parse(10**6) == 24 * 365  # 超大合法数夹到上限


def test_tick_scheduled_interval_parse_fallback(mp_config_cleanup, tenant_id):
    """sync_interval_hours 非法（"abc"）→ 默认 6h：5h 前不算到期、7h 前到期。"""
    scheduler = WeChatMPScheduler(redis=ts.FakeRedis(), service=object())
    config_id = _insert_mp_config(
        tenant_id, "chan_wp9bad", cfg={"enabled": True, "appid": "wxbad",
                                       "secret": "s", "sync_interval_hours": "abc"}
    )
    mp_config_cleanup(config_id)
    assert scheduler._tick_scheduled() == 1
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_wechat_mp_sync_runs SET status='success', created_at = now() - make_interval(hours => 5) "
            "WHERE config_id = %s",
            (config_id,),
        )
        conn.commit()
    assert scheduler._tick_scheduled() == 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_wechat_mp_sync_runs SET created_at = now() - make_interval(hours => 7) "
            "WHERE config_id = %s",
            (config_id,),
        )
        conn.commit()
    assert scheduler._tick_scheduled() == 1


# ------------------------------- 单 item 管道（freepublish 分支） -------------------------------


async def test_freepublish_multi_article_merge_order_and_deleted_excluded(
    monkeypatch, billed_tenant, fixture_html
):
    """多图文合并：未删子篇按序「标题→正文」保序拼接；is_deleted 子篇排除。"""
    tenant = billed_tenant
    row_id = _seed_article(
        tenant, processing_status="pending", wx_update_time=None,
        content_hash=None, title=None,
    )
    run_id = _create_scheduled_run(tenant, trigger="manual")
    _enqueue_item(tenant, run_id, row_id)

    sub_a = _sub(title="子篇甲", content=fixture_html, url=URL_A)  # 真实夹具（17 图）
    sub_b = _sub(
        title="子篇乙已删",
        content='<div id="js_content"><p>乙篇不应出现</p></div>',
        url=URL_C,
        is_deleted=True,
    )
    sub_c = _sub(title="子篇丙", content='<div id="js_content"><p>丙篇正文内容。</p></div>',
                 url=CROSS_URL)
    fake = FakeMPAPIClient()
    fake.articles = {ARTICLE_ID: _detail(sub_a, sub_b, sub_c)}
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)

    svc = ts._make_service(ts.StubFetcher())
    await svc.claim_and_run(tenant)

    row = _article_row(tenant)
    assert row["processing_status"] == "success" and row["doc_id"]
    doc = ts._query_one("SELECT * FROM documents WHERE id = %s", (row["doc_id"],))
    raw = doc["raw_text"]
    assert raw.index("子篇甲") < raw.index("丙篇正文内容。")  # 保序
    assert "乙篇不应出现" not in raw and "子篇乙已删" not in raw  # 已删子篇排除
    assert doc["file_path"] == URL_A  # 首个未删子篇 url
    meta = json.loads(doc["metadata"])
    assert [s["is_deleted"] for s in meta["sub_articles"]] == [False, True, False]
    assert len(meta["sub_articles"]) == 3  # 全子篇留痕（含删除标记）
    item = ts._query_one(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s", (run_id,)
    )
    assert item["status"] == "success" and item["action"] == "new"


async def test_freepublish_all_sub_deleted_soft_deletes(monkeypatch, billed_tenant):
    """全部子篇明确 is_deleted=true → 复用 _handle_deleted 软删整篇。"""
    tenant = billed_tenant
    doc_id = _insert_document(tenant, EXTERNAL_ID)
    row_id = _seed_article(tenant, doc_id=doc_id, processing_status="success")
    run_id = _create_scheduled_run(tenant, trigger="retry")
    _enqueue_item(tenant, run_id, row_id)

    fake = FakeMPAPIClient()
    fake.articles = {
        ARTICLE_ID: _detail(_sub(is_deleted=True), _sub(title="另一篇", is_deleted=True))
    }
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)

    svc = ts._make_service(ts.StubFetcher())
    await svc.claim_and_run(tenant)

    row = _article_row(tenant)
    assert row["status"] == "deleted" and row["processing_status"] == "success"
    doc = ts._query_one("SELECT status FROM documents WHERE id = %s", (doc_id,))
    assert doc["status"] == "deleted"
    item = ts._query_one(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s", (run_id,)
    )
    assert item["status"] == "success" and item["action"] == "delete"
    run = ts._query_one("SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (run_id,))
    assert run["deleted_count"] == 1


@pytest.mark.parametrize("bad_detail", [
    {"news_item": []},  # 空数组
    {"create_time": 1, "update_time": 2},  # 字段缺失
    {"news_item": "not-a-list"},  # 类型异常
    {"news_item": ["not-a-dict"]},  # 子篇类型异常
])
async def test_freepublish_structure_anomaly_fails_keeps_old_version(
    monkeypatch, billed_tenant, bad_detail
):
    """news_item 空数组/字段缺失/类型异常：item 失败保留旧版，绝不判删除。"""
    tenant = billed_tenant
    doc_id = _insert_document(tenant, EXTERNAL_ID)
    row_id = _seed_article(tenant, doc_id=doc_id, processing_status="success")
    run_id = _create_scheduled_run(tenant, trigger="recheck")
    _enqueue_item(tenant, run_id, row_id, action="check")

    fake = FakeMPAPIClient()
    fake.articles = {ARTICLE_ID: dict(bad_detail)}
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)

    svc = ts._make_service(ts.StubFetcher())
    await svc.claim_and_run(tenant)

    item = ts._query_one(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s", (run_id,)
    )
    assert item["status"] == "failed"
    row = _article_row(tenant)
    assert row["status"] == "active"  # 未被判删除
    doc = ts._query_one("SELECT status FROM documents WHERE id = %s", (doc_id,))
    assert doc["status"] == "active"  # 旧版可检索


async def test_freepublish_recheck_hash_fast_path_zero_billing(
    monkeypatch, billed_tenant, fixture_html
):
    """内容未变的复核：check 快路径零 embedding 零计费，并刷新滞后的 wx_update_time。"""
    tenant = billed_tenant
    fake = FakeMPAPIClient()
    fake.messages = [_batch_message()]
    fake.articles = {ARTICLE_ID: _detail(_sub(content=fixture_html))}
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)
    embedding = ts.FakeEmbeddingClient()
    svc = ts._make_service(ts.StubFetcher(), embedding=embedding)

    _create_scheduled_run(tenant)
    await svc.claim_and_run(tenant)  # 首轮入库
    row = _article_row(tenant)
    first_doc = ts._query_one("SELECT raw_text FROM documents WHERE id = %s", (row["doc_id"],))
    embed_calls_after_new = embedding.embed_batch_calls
    assert embed_calls_after_new >= 1

    # 回拨 wx_update_time 模拟滞后（对账未刷新的历史状态）
    from src.db.database import get_db_connection
    from datetime import timedelta

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_wechat_mp_articles SET wx_update_time = %s WHERE id = %s",
            (UPDATE_TS_NAIVE - timedelta(hours=1), row["id"]),
        )
        conn.commit()

    recheck = svc_mod.recheck_article(tenant, None, row["id"])
    await svc.claim_and_run(tenant)
    item = ts._query_one(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s", (recheck["run_id"],)
    )
    assert item["status"] == "success" and item["action"] == "check"
    assert item["billing_status"] == "not_required"
    assert embedding.embed_batch_calls == embed_calls_after_new  # 零 embedding
    row2 = _article_row(tenant)
    assert row2["wx_update_time"] == UPDATE_TS_NAIVE  # 滞后的源时间已刷新
    doc2 = ts._query_one("SELECT raw_text FROM documents WHERE id = %s", (row2["doc_id"],))
    assert doc2["raw_text"] == first_doc["raw_text"]  # 文档未被重建


async def test_freepublish_cross_source_related_doc_linking(
    monkeypatch, billed_tenant, fixture_html
):
    """跨来源重叠（§5.4）：子篇 url 命中本租户 URL 文档 → 双方 metadata 互标。"""
    tenant = billed_tenant
    # 既有 URL 通道文章（manual + 短链身份）已入库
    url_doc_id = _insert_document(tenant, f"mp:s:{CROSS_TOKEN}")
    _seed_article(
        tenant,
        external_id=f"mp:s:{CROSS_TOKEN}",
        source_channel="manual",
        config_id=None,
        processing_status="success",
        doc_id=url_doc_id,
        pipeline_version=PIPELINE_VERSION,
        content_hash="urlhash",
    )

    # freepublish 消息：子篇 url 与上同文（短链形态）
    row_id = _seed_article(
        tenant, processing_status="pending", wx_update_time=None,
        content_hash=None, title=None,
    )
    run_id = _create_scheduled_run(tenant, trigger="manual")
    _enqueue_item(tenant, run_id, row_id)
    fake = FakeMPAPIClient()
    fake.articles = {
        ARTICLE_ID: _detail(_sub(title="跨来源同文", content=fixture_html, url=CROSS_URL))
    }
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)

    svc = ts._make_service(ts.StubFetcher())
    await svc.claim_and_run(tenant)

    fp_doc = ts._query_one(
        "SELECT doc_id FROM bs_wechat_mp_articles WHERE id = %s", (row_id,)
    )
    meta_fp = ts._query_one(
        "SELECT metadata FROM documents WHERE id = %s", (fp_doc["doc_id"],)
    )
    meta_url = ts._query_one(
        "SELECT metadata FROM documents WHERE id = %s", (url_doc_id,)
    )
    assert fp_doc["doc_id"] in json.loads(meta_url["metadata"]).get("related_doc_ids", [])
    assert url_doc_id in json.loads(meta_fp["metadata"]).get("related_doc_ids", [])
    # 不物理合并：两个文档都还在
    assert ts._query_one("SELECT status FROM documents WHERE id = %s", (url_doc_id,))["status"] == "active"


async def test_duplicate_message_within_scan_dedup(monkeypatch, billed_tenant, fixture_html):
    """同一 scan 内重复消息（翻页边界漂移）：只处理首次出现，run success 不撞唯一键。"""
    tenant = billed_tenant
    fake = FakeMPAPIClient()
    msg = _batch_message()
    fake.messages = [msg, msg]  # 同一消息出现两次（跨页重叠形态）
    fake.articles = {ARTICLE_ID: _detail(_sub(content=fixture_html, url=LONG_URL))}
    _patch_api_client(monkeypatch, fake)
    _patch_channel_config(monkeypatch, tenant)

    run_id = _create_scheduled_run(tenant)
    svc = ts._make_service(ts.StubFetcher())
    await svc.claim_and_run(tenant)

    run = ts._query_one("SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (run_id,))
    assert run["status"] == "success"
    assert run["new_count"] == 1
    items = ts._query_all(
        "SELECT id FROM bs_wechat_mp_sync_items WHERE run_id = %s AND tenant_id = %s",
        (run_id, tenant),
    )
    assert len(items) == 1
    rows = ts._query_all(
        "SELECT id FROM bs_wechat_mp_articles WHERE tenant_id = %s AND source_channel = %s",
        (tenant, FREEPUBLISH_CHANNEL),
    )
    assert len(rows) == 1
