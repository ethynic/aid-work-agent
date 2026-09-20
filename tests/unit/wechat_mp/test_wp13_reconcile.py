"""WP13 清单源对账与调度第④生成器单元测试（真实 PG，require_db 门禁；微信接口全 mock）。

覆盖（计划 WP13 节 + 设计 §3.3/§3.4/§3.5）：
- 对账 diff（auto_all）：新子篇建 articles 行（source_channel='list'，tags 记
  aid/msgid/itemidx/publish_type）+ pending item → worker 走 URL 直采管道入库
  （documents.metadata.list_source 回填）；未变跳过仅推进 last_synced_at 不重拉；
  update_time 变更建 item 重建
- manual 模式：仅建行 processing_status='pending_manual' 不建 item 不入库；
  源更新仅刷新 wx_update_time 保持待挑选
- 模式切换 manual→auto_all 自动补齐积压入队；勾选入队单篇/批量（非法跳过）
- 删除：清单 is_deleted=true 直接软删 articles+documents（含他通道行的跨通道信号）
- 跨通道去重：他通道（manual）同身份行不重复建行/重拉
- 失败语义：session_expired→run failed+状态 expired；200007→account_error；
  freq_control/结构异常→run failed 不改状态；会话缺失→expired；绝不误报 success
- 调度 ④：绑定+active/expiring 到期入队（trigger_type='list_sync'，config_id 落值，
  无 items）；未绑定/expired/account_error 跳过；间隔到期（默认 1h）；防堆积
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.wechat_mp import list_session as list_session_mod
from src.wechat_mp import list_source as list_source_mod
from src.wechat_mp import service as svc_mod
from src.wechat_mp.list_source import (
    ListAccountError,
    ListFreqControlError,
    ListSessionExpiredError,
    ListStructureError,
    OwnArticle,
    OwnListScan,
)
from src.wechat_mp.scheduler import WeChatMPScheduler

from . import test_service as ts

CONFIG_ID = "chan_wp13test01"
# 短链 token（8 位以上合法字符，normalize_url 可规范身份）
TOK_A = "Wp13ReconA0001"
TOK_B = "Wp13ReconB0002"
TOK_C = "Wp13ReconC0003"
URL_A = f"https://mp.weixin.qq.com/s/{TOK_A}"
URL_B = f"https://mp.weixin.qq.com/s/{TOK_B}"
URL_C = f"https://mp.weixin.qq.com/s/{TOK_C}"
BASE_TS = 1789400000

BODY_A = "清单源文章正文A：春季装修指南内容样例，用于入库校验。"
BODY_B = "清单源文章正文B：夏季促销活动内容样例，用于入库校验。"


# ------------------------------- 替身 -------------------------------


class FakeListClient:
    """清单客户端替身：脚本化 fetch_sync_scan/fetch_all 结果/异常（不打真实接口）。

    WP13-r1：service 统一走 fetch_sync_scan（回填上限/重叠即停策略），本替身
    记录 fetch_kwargs 供断言，结果仍由脚本化 fetch_all 提供（既有用例兼容）。
    """

    def __init__(self, *args, **kwargs):
        self.scan: OwnListScan = OwnListScan()
        self.error: Exception = None
        self.calls = 0
        self.fetch_kwargs: dict = {}
        instances.append(self)

    def fetch_sync_scan(self, *, max_articles=None, page_all_known=None):
        self.fetch_kwargs = {
            "max_articles": max_articles,
            "page_all_known": page_all_known,
        }
        return self.fetch_all()

    def fetch_all(self) -> OwnListScan:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.scan

    def close(self):
        pass


instances = []


def _patch_list_client(monkeypatch):
    monkeypatch.setattr(list_source_mod, "OwnListClient", FakeListClient)


def _patch_list_session(monkeypatch, *, fields=None, missing=False):
    """替换绑定持久化层（不写真实配置表；capture 状态置位与回写）。"""
    captured = {"status_updates": [], "field_writes": []}

    def fake_load(config_id):
        if missing:
            return None
        return {
            "token": "fake-token",
            "cookie": "k=v",
            "fields": fields
            if fields is not None
            else {"list_sync_status": "active", "list_sync_mode": "auto_all"},
        }

    def fake_set_status(config_id, status):
        captured["status_updates"].append((config_id, status))

    def fake_write(config_id, fields=None, remove_keys=()):
        captured["field_writes"].append((config_id, dict(fields or {}), tuple(remove_keys)))
        return True

    monkeypatch.setattr(list_session_mod, "load_list_session", fake_load)
    monkeypatch.setattr(list_session_mod, "set_list_sync_status", fake_set_status)
    monkeypatch.setattr(list_session_mod, "write_list_config_fields", fake_write)
    return captured


def _own_article(token: str, update_ts: int, *, deleted: bool = False, title=None) -> OwnArticle:
    return OwnArticle(
        aid=f"Fk13{token[-6:]}",
        title=title if title is not None else f"清单文章{token[-4:]}",
        link=f"https://mp.weixin.qq.com/s/{token}",
        update_time=update_ts,
        create_time=update_ts - 600,
        is_deleted=deleted,
        item_show_type=9,
        itemidx=1,
        digest="摘要",
        cover="",
        author_name="测试号",
        msgid=1000 + update_ts % 1000,
        publish_type=101,
    )


def _scan(*articles, total=None, complete=True) -> OwnListScan:
    return OwnListScan(
        articles=list(articles),
        total_count=total if total is not None else len(articles),
        pages_fetched=1,
        complete=complete,
    )


# ------------------------------- DB 辅助 -------------------------------


@pytest.fixture(autouse=True)
def _real_vector_db_patch(monkeypatch):
    module = ts._load_real_vector_db()
    monkeypatch.setattr(svc_mod, "get_vector_db", module.get_vector_db)


@pytest.fixture()
def billed_tenant(tenant_id):
    ts._create_tenant(tenant_id, 100.0)
    return tenant_id


@pytest.fixture()
def channel_configs(require_db):
    """tenant_channel_configs 读写 + 按租户清理（conftest 清理不含该表）。"""
    from src.db.database import get_db_connection

    def _insert(cfg: dict, verified: int = 0) -> str:
        config_id = f"chan_{uuid.uuid4().hex[:12]}"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tenant_channel_configs (config_id, tenant_id, channel_type, config, verified)"
                " VALUES (%s, %s, 'wechat_mp', %s, %s)",
                (config_id, channel_configs.tenant, json.dumps(cfg), verified),
            )
            conn.commit()
        return config_id

    def _update_config(config_id: str, cfg: dict) -> None:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tenant_channel_configs SET config = %s WHERE config_id = %s",
                (json.dumps(cfg), config_id),
            )
            conn.commit()

    holder = type("H", (), {})()
    holder.tenant = None
    holder.insert = _insert
    holder.update_config = _update_config
    channel_configs = holder
    yield holder
    if holder.tenant:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM tenant_channel_configs WHERE tenant_id = %s", (holder.tenant,)
            )
            conn.commit()


def _create_list_sync_run(tenant_id: str, config_id: str = CONFIG_ID) -> int:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_runs
                (tenant_id, config_id, user_id, trigger_type, status, total_count)
            VALUES (%s, %s, NULL, 'list_sync', 'queued', 0)
            RETURNING id
            """,
            (tenant_id, config_id),
        )
        run_id = cursor.fetchone()["id"]
        conn.commit()
    return run_id


def _article_by_external(tenant_id: str, token: str):
    return ts._query_one(
        "SELECT * FROM bs_wechat_mp_articles WHERE tenant_id = %s AND external_id = %s",
        (tenant_id, f"mp:s:{token}"),
    )


def _run_row(run_id: int):
    return ts._query_one("SELECT * FROM bs_wechat_mp_sync_runs WHERE id = %s", (run_id,))


def _doc_row(doc_id):
    if not doc_id:
        return None
    return ts._query_one("SELECT * FROM documents WHERE id = %s", (doc_id,))


# ------------------------------- 对账：auto_all -------------------------------


async def test_auto_all_new_articles_ingest(monkeypatch, billed_tenant, channel_configs):
    """新子篇 → 建行+item → URL 直采入库（metadata.list_source 回填，run 计数正确）。"""
    tenant = billed_tenant
    channel_configs.tenant = tenant
    captured = _patch_list_session(monkeypatch)
    _patch_list_client(monkeypatch)

    fetcher = ts.StubFetcher()
    fetcher.set_page(URL_A, ts.FetchResult(status="ok", html=ts.make_article_html("清单文章A", BODY_A, URL_A)))
    fetcher.set_page(URL_B, ts.FetchResult(status="ok", html=ts.make_article_html("清单文章B", BODY_B, URL_B)))
    svc = ts._make_service(fetcher)

    run_id = _create_list_sync_run(tenant)
    pending_scan = _scan(
        _own_article(TOK_A, BASE_TS), _own_article(TOK_B, BASE_TS + 60)
    )
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: pending_scan)
    result = await svc.claim_and_run(tenant)
    assert result["executed"] and result["run_ids"] == [run_id]

    run = _run_row(run_id)
    assert run["status"] == "success"
    assert run["total_count"] == 2 and run["new_count"] == 2
    assert run["deleted_count"] == 0

    row = _article_by_external(tenant, TOK_A)
    assert row["source_channel"] == "list"
    assert row["config_id"] == CONFIG_ID
    assert row["processing_status"] == "success" and row["doc_id"]
    assert row["wx_update_time"] == datetime.fromtimestamp(
        BASE_TS, tz=timezone.utc
    ).replace(tzinfo=None)
    tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"]
    assert tags["aid"] == f"Fk13{TOK_A[-6:]}" and tags["publish_type"] == 101
    doc = _doc_row(row["doc_id"])
    assert doc["status"] == "active"
    meta = json.loads(doc["metadata"]) if isinstance(doc["metadata"], str) else doc["metadata"]
    assert meta["list_source"]["publish_type"] == 101
    assert meta["list_source"]["aid"] == f"Fk13{TOK_A[-6:]}"
    assert meta["source_channel"] == "list"
    # 对账成功回写 list_last_sync_at（field_writes 含该字段）
    assert any("list_last_sync_at" in f for _, f, _ in captured["field_writes"])
    # 状态未置位（仍 active）
    assert captured["status_updates"] == []


async def test_unchanged_skips_repull(monkeypatch, billed_tenant):
    """未变子篇：不建 item 不重拉正文，仅推进 last_synced_at 计 skipped。"""
    tenant = billed_tenant
    _patch_list_session(monkeypatch)
    _patch_list_client(monkeypatch)
    scan = _scan(_own_article(TOK_A, BASE_TS))
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan)

    fetcher = ts.StubFetcher()
    fetcher.set_page(URL_A, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_A, URL_A)))
    svc = ts._make_service(fetcher)

    _create_list_sync_run(tenant)  # 首轮
    await svc.claim_and_run(tenant)
    assert len(fetcher.calls) == 1

    run2 = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    run = _run_row(run2)
    assert run["status"] == "success"
    assert run["skipped_count"] == 1 and run["new_count"] == 0
    items = ts._query_all(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s AND tenant_id = %s",
        (run2, tenant),
    )
    assert items == []
    assert len(fetcher.calls) == 1  # 核心要求：未变不重拉
    row = _article_by_external(tenant, TOK_A)
    assert row["last_synced_at"] is not None


async def test_update_time_change_rebuilds(monkeypatch, billed_tenant):
    """源 update_time 变化 + 内容变化 → 建 item → update 重建 + wx_update_time 推进。"""
    tenant = billed_tenant
    _patch_list_session(monkeypatch)
    _patch_list_client(monkeypatch)
    scan1 = _scan(_own_article(TOK_A, BASE_TS))
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan1)
    fetcher = ts.StubFetcher()
    fetcher.set_page(URL_A, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_A, URL_A)))
    svc = ts._make_service(fetcher)
    _create_list_sync_run(tenant)  # 首轮
    await svc.claim_and_run(tenant)

    scan2 = _scan(_own_article(TOK_A, BASE_TS + 600))
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan2)
    fetcher.set_page(
        URL_A,
        ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_A + "更新段落", URL_A)),
    )
    run2 = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    run = _run_row(run2)
    assert run["status"] == "success" and run["updated_count"] == 1
    row = _article_by_external(tenant, TOK_A)
    assert row["wx_update_time"] == datetime.fromtimestamp(
        BASE_TS + 600, tz=timezone.utc
    ).replace(tzinfo=None)


# ------------------------------- 对账：manual 模式 -------------------------------


async def test_manual_mode_rows_pending_manual_no_items(monkeypatch, billed_tenant):
    """manual：仅建行 pending_manual，不建 item 不入库；run 不误报 failed。"""
    tenant = billed_tenant
    _patch_list_session(
        monkeypatch, fields={"list_sync_status": "active", "list_sync_mode": "manual"}
    )
    _patch_list_client(monkeypatch)
    scan = _scan(_own_article(TOK_A, BASE_TS), _own_article(TOK_B, BASE_TS + 1))
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan)

    fetcher = ts.StubFetcher()
    svc = ts._make_service(fetcher)
    run_id = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)

    run = _run_row(run_id)
    assert run["status"] == "success"
    assert run["total_count"] == 2 and run["skipped_count"] == 2
    items = ts._query_all(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s", (run_id,)
    )
    assert items == []
    row = _article_by_external(tenant, TOK_A)
    assert row["processing_status"] == "pending_manual"
    assert row["doc_id"] is None and fetcher.calls == []

    # 源更新：仅刷新 wx_update_time，保持待挑选、不建 item
    scan2 = _scan(_own_article(TOK_A, BASE_TS + 3600), _own_article(TOK_B, BASE_TS + 1))
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan2)
    run2 = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    row = _article_by_external(tenant, TOK_A)
    assert row["processing_status"] == "pending_manual"
    assert row["wx_update_time"] == datetime.fromtimestamp(
        BASE_TS + 3600, tz=timezone.utc
    ).replace(tzinfo=None)
    assert ts._query_all(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s", (run2,)
    ) == []


async def test_switch_mode_to_auto_all_enqueues_backlog(monkeypatch, billed_tenant, channel_configs):
    """manual→auto_all：积压 pending_manual 自动补齐入队并入账。"""
    tenant = billed_tenant
    channel_configs.tenant = tenant
    _patch_list_session(
        monkeypatch, fields={"list_sync_status": "active", "list_sync_mode": "manual"}
    )
    _patch_list_client(monkeypatch)
    scan = _scan(_own_article(TOK_A, BASE_TS), _own_article(TOK_B, BASE_TS + 1))
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan)
    svc = ts._make_service(ts.StubFetcher())
    _create_list_sync_run(tenant)  # manual 首轮
    await svc.claim_and_run(tenant)

    # 切换到 auto_all：find_bound_list_config 返回绑定配置
    monkeypatch.setattr(
        list_session_mod,
        "find_bound_list_config",
        lambda t: {"config_id": CONFIG_ID, "config": {"list_session_token": "***"}},
    )
    result = await asyncio_wrap(svc_mod.switch_list_sync_mode, tenant, "auto_all")
    assert result["enqueued"] == 2
    row = _article_by_external(tenant, TOK_A)
    assert row["processing_status"] == "pending"
    runs = ts._query_all(
        "SELECT * FROM bs_wechat_mp_sync_runs WHERE tenant_id = %s AND trigger_type = 'manual'",
        (tenant,),
    )
    assert len(runs) == 1 and runs[0]["config_id"] == CONFIG_ID

    # 勾选入队走 URL 直采入库
    fetcher = ts.StubFetcher()
    fetcher.set_page(URL_A, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_A, URL_A)))
    fetcher.set_page(URL_B, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_B, URL_B)))
    svc2 = ts._make_service(fetcher)
    await svc2.claim_and_run(tenant)
    row = _article_by_external(tenant, TOK_A)
    assert row["processing_status"] == "success" and row["doc_id"]


async def asyncio_wrap(func, *args):
    import asyncio

    return await asyncio.to_thread(func, *args)


async def test_enqueue_manual_articles_single_and_batch(monkeypatch, billed_tenant):
    """勾选入队：pending_manual 单篇/批量受理；非 pending_manual 行跳过。"""
    tenant = billed_tenant
    _patch_list_session(
        monkeypatch, fields={"list_sync_status": "active", "list_sync_mode": "manual"}
    )
    _patch_list_client(monkeypatch)
    scan = _scan(_own_article(TOK_A, BASE_TS), _own_article(TOK_B, BASE_TS + 1))
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan)
    svc = ts._make_service(ts.StubFetcher())
    _create_list_sync_run(tenant)  # manual 首轮
    await svc.claim_and_run(tenant)

    id_a = _article_by_external(tenant, TOK_A)["id"]
    id_b = _article_by_external(tenant, TOK_B)["id"]

    # 批量（含一个非法 ID）
    result = svc_mod.enqueue_manual_articles(tenant, "u-1", [id_a, id_b, 999999])
    assert result["enqueued"] == 2 and result["skipped"] == 1
    run = _run_row(result["run_id"])
    assert run["trigger_type"] == "manual" and run["status"] == "queued"
    items = ts._query_all(
        "SELECT * FROM bs_wechat_mp_sync_items WHERE run_id = %s", (result["run_id"],)
    )
    assert {i["article_row_id"] for i in items} == {id_a, id_b}
    for rid in (id_a, id_b):
        row = ts._query_one(
            "SELECT processing_status FROM bs_wechat_mp_articles WHERE id = %s", (rid,)
        )
        assert row["processing_status"] == "pending"

    # 已入队的行再次勾选 → 全部跳过（不重复排队）
    result2 = svc_mod.enqueue_manual_articles(tenant, "u-1", [id_a])
    assert result2["enqueued"] == 0 and result2["run_id"] is None


# ------------------------------- 对账：删除与跨通道 -------------------------------


async def test_is_deleted_soft_deletes_document(monkeypatch, billed_tenant):
    """清单 is_deleted=true：直接软删 articles+documents（源侧明确信号，设计 §3.4）。"""
    tenant = billed_tenant
    _patch_list_session(monkeypatch)
    _patch_list_client(monkeypatch)
    scan1 = _scan(_own_article(TOK_A, BASE_TS))
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan1)
    fetcher = ts.StubFetcher()
    fetcher.set_page(URL_A, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_A, URL_A)))
    svc = ts._make_service(fetcher)
    _create_list_sync_run(tenant)  # 首轮
    await svc.claim_and_run(tenant)
    row = _article_by_external(tenant, TOK_A)
    assert row["doc_id"]

    scan2 = _scan(_own_article(TOK_A, BASE_TS, deleted=True))
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan2)
    run2 = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    run = _run_row(run2)
    assert run["status"] == "success" and run["deleted_count"] == 1
    row = _article_by_external(tenant, TOK_A)
    assert row["status"] == "deleted"
    assert _doc_row(row["doc_id"])["status"] == "deleted"


async def test_cross_channel_dedup_and_delete_signal(monkeypatch, billed_tenant):
    """他通道（manual）同身份行：不重复建行/重拉；is_deleted 信号同样生效。"""
    tenant = billed_tenant
    from tests.unit.wechat_mp.test_wp9_reconcile import _insert_document, _seed_article

    row_id = _seed_article(
        tenant,
        f"mp:s:{TOK_A}",
        source_channel="manual",
        processing_status="success",
        wx_update_time=None,
        doc_id=_insert_document(tenant, f"mp:s:{TOK_A}"),
        title="手动导入",
    )
    _patch_list_session(monkeypatch)
    _patch_list_client(monkeypatch)
    scan = _scan(_own_article(TOK_A, BASE_TS))
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan)
    fetcher = ts.StubFetcher()
    svc = ts._make_service(fetcher)
    run_id = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    run = _run_row(run_id)
    assert run["status"] == "success" and run["skipped_count"] == 1
    assert fetcher.calls == []  # 不重拉他通道文章
    # 全租户仅一行该身份
    rows = ts._query_all(
        "SELECT id FROM bs_wechat_mp_articles WHERE tenant_id = %s AND external_id = %s",
        (tenant, f"mp:s:{TOK_A}"),
    )
    assert len(rows) == 1

    # 源侧删除：跨通道软删
    scan2 = _scan(_own_article(TOK_A, BASE_TS, deleted=True))
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan2)
    run2 = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    assert _run_row(run2)["deleted_count"] == 1
    row = ts._query_one(
        "SELECT status, doc_id FROM bs_wechat_mp_articles WHERE id = %s", (row_id,)
    )
    assert row["status"] == "deleted"
    assert _doc_row(row["doc_id"])["status"] == "deleted"


# ------------------------------- 对账：失败语义 -------------------------------


async def _prepare_bound(monkeypatch, tenant, scan=None, error=None):
    captured = _patch_list_session(monkeypatch)
    _patch_list_client(monkeypatch)

    def fetch_all(self):
        self.calls += 1
        if error is not None:
            raise error
        return scan or _scan()

    monkeypatch.setattr(FakeListClient, "fetch_all", fetch_all)
    svc = ts._make_service(ts.StubFetcher())
    run_id = _create_list_sync_run(tenant)
    return svc, run_id, captured


async def test_session_expired_marks_expired(monkeypatch, billed_tenant):
    """session_expired → run failed + list_sync_status='expired'，不误报 success。"""
    tenant = billed_tenant
    svc, run_id, captured = await _prepare_bound(
        monkeypatch, tenant, error=ListSessionExpiredError("清单登录会话已失效，请重新扫码")
    )
    await svc.claim_and_run(tenant)
    run = _run_row(run_id)
    assert run["status"] == "failed"
    assert "重新扫码" in run["error_message"]
    assert captured["status_updates"] == [(CONFIG_ID, "expired")]


async def test_account_error_marks_account_error(monkeypatch, billed_tenant):
    tenant = billed_tenant
    svc, run_id, captured = await _prepare_bound(
        monkeypatch, tenant, error=ListAccountError("公众号账号状态异常")
    )
    await svc.claim_and_run(tenant)
    assert _run_row(run_id)["status"] == "failed"
    assert captured["status_updates"] == [(CONFIG_ID, "account_error")]


async def test_freq_control_failed_without_status_change(monkeypatch, billed_tenant):
    """200013 频控：run failed（随周期重试），绑定状态不变。"""
    tenant = billed_tenant
    svc, run_id, captured = await _prepare_bound(
        monkeypatch, tenant, error=ListFreqControlError("频控")
    )
    await svc.claim_and_run(tenant)
    assert _run_row(run_id)["status"] == "failed"
    assert captured["status_updates"] == []


async def test_structure_error_not_success(monkeypatch, billed_tenant):
    """结构异常 → run failed，绝不误报 success（设计 §3.2）。"""
    tenant = billed_tenant
    svc, run_id, _ = await _prepare_bound(
        monkeypatch, tenant, error=ListStructureError("清单响应结构异常")
    )
    await svc.claim_and_run(tenant)
    assert _run_row(run_id)["status"] == "failed"


async def test_missing_session_marks_expired_and_fails(monkeypatch, billed_tenant):
    """会话缺失（解绑后残留 run 等）→ run failed + 状态 expired。"""
    tenant = billed_tenant
    captured = _patch_list_session(monkeypatch, missing=True)
    _patch_list_client(monkeypatch)
    svc = ts._make_service(ts.StubFetcher())
    run_id = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    assert _run_row(run_id)["status"] == "failed"
    assert captured["status_updates"] == [(CONFIG_ID, "expired")]


async def test_expiring_status_flip_on_reconcile(monkeypatch, billed_tenant):
    """active 且进入 expire_at-24h 窗口：对账前置位 expiring。"""
    tenant = billed_tenant
    from datetime import datetime as dt

    soon = (dt.now(timezone.utc) + timedelta(hours=2)).isoformat()
    captured = _patch_list_session(
        monkeypatch,
        fields={
            "list_sync_status": "active",
            "list_sync_mode": "auto_all",
            "list_session_expire_at": soon,
        },
    )
    _patch_list_client(monkeypatch)
    monkeypatch.setattr(
        FakeListClient, "fetch_all", lambda self: _scan(_own_article(TOK_A, BASE_TS))
    )
    svc = ts._make_service(ts.StubFetcher())
    _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    assert captured["status_updates"] == [(CONFIG_ID, "expiring")]


# ------------------------------- WP13-r1：回填上限与增量重叠即停 -------------------------------


def _r1_fields(**extra) -> dict:
    base = {"list_sync_status": "active", "list_sync_mode": "auto_all"}
    base.update(extra)
    return base


async def test_backfill_mode_caps_and_sets_done_on_complete(monkeypatch, billed_tenant):
    """首次回填（done 缺省 false）：fetch 带子篇上限 N；complete 扫描后置 done=true。"""
    tenant = billed_tenant
    captured = _patch_list_session(monkeypatch, fields=_r1_fields(list_sync_max_articles=7))
    _patch_list_client(monkeypatch)
    monkeypatch.setattr(
        FakeListClient,
        "fetch_all",
        lambda self: _scan(_own_article(TOK_A, BASE_TS), _own_article(TOK_B, BASE_TS + 1)),
    )
    fetcher = ts.StubFetcher()
    fetcher.set_page(URL_A, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_A, URL_A)))
    fetcher.set_page(URL_B, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_B, URL_B)))
    svc = ts._make_service(fetcher)
    run_id = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)

    fake = instances[-1]
    assert fake.fetch_kwargs["max_articles"] == 7  # 上限透传给分页引擎
    assert fake.fetch_kwargs["page_all_known"] is None  # 回填轮不做重叠判定
    assert _run_row(run_id)["status"] == "success"
    done_writes = [f for _, f, _ in captured["field_writes"] if "list_backfill_done" in f]
    assert done_writes and done_writes[-1]["list_backfill_done"] is True
    # done 与 list_last_sync_at 同一次回写
    assert "list_last_sync_at" in done_writes[-1]


async def test_backfill_incomplete_scan_does_not_set_done(monkeypatch, billed_tenant):
    """不完整扫描（空页/预算/重复页截断）不置 done：下轮继续按上限回填。"""
    tenant = billed_tenant
    captured = _patch_list_session(monkeypatch, fields=_r1_fields())
    _patch_list_client(monkeypatch)
    monkeypatch.setattr(
        FakeListClient,
        "fetch_all",
        lambda self: _scan(_own_article(TOK_A, BASE_TS), complete=False),
    )
    fetcher = ts.StubFetcher()
    fetcher.set_page(URL_A, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_A, URL_A)))
    svc = ts._make_service(fetcher)
    run_id = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    # 部分对账仍成功（清单源无 missing 门禁），但 done 不置位
    assert _run_row(run_id)["status"] == "success"
    assert not any("list_backfill_done" in f for _, f, _ in captured["field_writes"])


@pytest.mark.parametrize(
    "raw,expected",
    [(9999, 500), (0, 1), (-5, 1), ("350", 350)],
)
async def test_backfill_max_articles_clamped(monkeypatch, billed_tenant, raw, expected):
    """脏配置值读写两侧钳制：service 取上限时经 clamp（1~500 默认 100）。"""
    tenant = billed_tenant
    _patch_list_session(monkeypatch, fields=_r1_fields(list_sync_max_articles=raw))
    _patch_list_client(monkeypatch)
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: _scan())
    svc = ts._make_service(ts.StubFetcher())
    _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    assert instances[-1].fetch_kwargs["max_articles"] == expected


async def test_backfill_max_articles_default_when_missing(monkeypatch, billed_tenant):
    """配置缺省：上限取默认 100。"""
    tenant = billed_tenant
    _patch_list_session(monkeypatch, fields=_r1_fields())
    _patch_list_client(monkeypatch)
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: _scan())
    svc = ts._make_service(ts.StubFetcher())
    _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    assert instances[-1].fetch_kwargs["max_articles"] == 100


async def test_incremental_mode_overlap_predicate(monkeypatch, billed_tenant):
    """done=true：增量轮带重叠判定回调；已知一致页 True / 含未知或变更子篇 False。"""
    tenant = billed_tenant
    captured = _patch_list_session(monkeypatch, fields=_r1_fields())
    _patch_list_client(monkeypatch)
    monkeypatch.setattr(
        FakeListClient, "fetch_all", lambda self: _scan(_own_article(TOK_A, BASE_TS))
    )
    fetcher = ts.StubFetcher()
    fetcher.set_page(URL_A, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_A, URL_A)))
    svc = ts._make_service(fetcher)
    _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)  # 首轮回填入库 TOK_A 并置 done
    assert any(f.get("list_backfill_done") is True for _, f, _ in captured["field_writes"])

    # 第二轮：增量模式（fields 带 done=true），谓词真实查库判定
    _patch_list_session(monkeypatch, fields=_r1_fields(list_backfill_done=True))
    observed = {}

    def fake_fetch(self, *, max_articles=None, page_all_known=None):
        self.fetch_kwargs = {"max_articles": max_articles, "page_all_known": page_all_known}
        observed["known"] = page_all_known([_own_article(TOK_A, BASE_TS)])
        observed["changed"] = page_all_known([_own_article(TOK_A, BASE_TS + 600)])
        observed["unknown"] = page_all_known([_own_article(TOK_B, BASE_TS)])
        observed["mixed"] = page_all_known(
            [_own_article(TOK_A, BASE_TS), _own_article(TOK_B, BASE_TS)]
        )
        return _scan(_own_article(TOK_A, BASE_TS))

    monkeypatch.setattr(FakeListClient, "fetch_sync_scan", fake_fetch)
    run2 = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    assert observed["known"] is True  # 已入库且时间一致 → 重叠
    assert observed["changed"] is False  # 时间变化 → 继续
    assert observed["unknown"] is False  # 未知身份 → 继续
    assert observed["mixed"] is False  # 整页必须全部已知才停
    assert instances[-1].fetch_kwargs["max_articles"] is None
    assert _run_row(run2)["status"] == "success"


async def test_find_synced_external_ids_semantics(monkeypatch, billed_tenant):
    """历史清单 synced 判定（真实 DB）：active 计入、deleted 不计（可手动重导）。"""
    from src.wechat_mp.list_session import _find_synced_external_ids

    tenant = billed_tenant
    _patch_list_session(monkeypatch)
    _patch_list_client(monkeypatch)
    monkeypatch.setattr(
        FakeListClient, "fetch_all", lambda self: _scan(_own_article(TOK_A, BASE_TS))
    )
    fetcher = ts.StubFetcher()
    fetcher.set_page(URL_A, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_A, URL_A)))
    svc = ts._make_service(fetcher)
    _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)

    ext_a = f"mp:s:{TOK_A}"
    assert _find_synced_external_ids(tenant, [ext_a, "mp:s:none"]) == {ext_a}
    assert _find_synced_external_ids(tenant, []) == set()

    # 软删后视为未入库
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_wechat_mp_articles SET status = 'deleted' "
            "WHERE tenant_id = %s AND external_id = %s",
            (tenant, ext_a),
        )
        conn.commit()
    assert _find_synced_external_ids(tenant, [ext_a]) == set()


# ------------------------------- 调度第④生成器 -------------------------------

_SCHEDULER_CONFIG = "chan_wp13sched01"


def _insert_sched_config(tenant_id: str, cfg: dict) -> str:
    from src.db.database import get_db_connection

    config_id = f"chan_{uuid.uuid4().hex[:12]}"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tenant_channel_configs (config_id, tenant_id, channel_type, config)"
            " VALUES (%s, %s, 'wechat_mp', %s)",
            (config_id, tenant_id, json.dumps(cfg)),
        )
        conn.commit()
    return config_id


def _cleanup_configs(tenant_id: str) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM tenant_channel_configs WHERE tenant_id = %s", (tenant_id,)
        )
        conn.commit()


@pytest.fixture()
def sched_tenant(billed_tenant, channel_configs):
    channel_configs.tenant = billed_tenant
    yield billed_tenant
    _cleanup_configs(billed_tenant)


def _tick():
    return WeChatMPScheduler(redis=ts.FakeRedis())._run_tick()


class TestSchedulerListSyncTick:
    def test_bound_active_config_due_enqueued(self, require_db, sched_tenant):
        """绑定 + active（默认 1h，无 last run）→ 建 list_sync queued run（config_id 落值，无 items）。"""
        config_id = _insert_sched_config(
            sched_tenant, {"list_session_token": "cipher", "list_sync_status": "active"}
        )
        result = _tick()
        assert result["list_sync_enqueued"] >= 1
        runs = ts._query_all(
            "SELECT * FROM bs_wechat_mp_sync_runs WHERE tenant_id = %s AND trigger_type = 'list_sync'",
            (sched_tenant,),
        )
        assert len(runs) == 1
        assert runs[0]["status"] == "queued" and runs[0]["config_id"] == config_id
        items = ts._query_all(
            "SELECT * FROM bs_wechat_mp_sync_items WHERE tenant_id = %s", (sched_tenant,)
        )
        assert items == []  # 对账 run 无 items

    def test_not_bound_or_inactive_skipped(self, require_db, sched_tenant):
        """未绑定 / expired / account_error 不入队；expiring 正常入队。"""
        _insert_sched_config(sched_tenant, {"appid": "wx1"})  # 未绑定
        _insert_sched_config(
            sched_tenant,
            {"list_session_token": "c", "list_sync_status": "expired"},
        )
        _insert_sched_config(
            sched_tenant,
            {"list_session_token": "c", "list_sync_status": "account_error"},
        )
        assert _tick()["list_sync_enqueued"] == 0
        _insert_sched_config(
            sched_tenant,
            {"list_session_token": "c", "list_sync_status": "expiring"},
        )
        assert _tick()["list_sync_enqueued"] == 1

    def test_interval_due_with_last_run(self, require_db, sched_tenant):
        """sync_interval_hours=6 且 1h 前已跑 → 不入队；2h 前且默认 1h → 入队。"""
        from src.db.database import get_db_connection

        config_id = _insert_sched_config(
            sched_tenant,
            {
                "list_session_token": "c",
                "list_sync_status": "active",
                "sync_interval_hours": 6,
            },
        )
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_runs
                    (tenant_id, config_id, trigger_type, status, total_count, created_at)
                VALUES (%s, %s, 'list_sync', 'failed', 0, now() - interval '1 hour')
                """,
                (sched_tenant, config_id),
            )
            conn.commit()
        assert _tick()["list_sync_enqueued"] == 0

        config_id2 = _insert_sched_config(
            sched_tenant, {"list_session_token": "c2", "list_sync_status": "active"}
        )  # 无字段默认 1h
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_runs
                    (tenant_id, config_id, trigger_type, status, total_count, created_at)
                VALUES (%s, %s, 'list_sync', 'success', 0, now() - interval '2 hours')
                """,
                (sched_tenant, config_id2),
            )
            conn.commit()
        result = _tick()
        assert result["list_sync_enqueued"] == 1

    def test_no_pileup_with_active_run(self, require_db, sched_tenant):
        """已有 queued/running 的 list_sync run → 不堆积。"""
        config_id = _insert_sched_config(
            sched_tenant, {"list_session_token": "c", "list_sync_status": "active"}
        )
        _create_list_sync_run(sched_tenant, config_id)  # queued
        assert _tick()["list_sync_enqueued"] == 0

    def test_worker_processes_list_sync_run_end_to_end(self, monkeypatch, require_db, sched_tenant):
        """端到端：④ 入队 → worker 领取对账 → auto_all 入库。"""
        _insert_sched_config(
            sched_tenant, {"list_session_token": "c", "list_sync_status": "active"}
        )
        assert _tick()["list_sync_enqueued"] == 1

        _patch_list_session(monkeypatch)
        _patch_list_client(monkeypatch)
        monkeypatch.setattr(
            FakeListClient,
            "fetch_all",
            lambda self: _scan(_own_article(TOK_A, BASE_TS)),
        )
        # run 的 config_id 是生成的 config_id；load_list_session 已 mock，无需对齐
        fetcher = ts.StubFetcher()
        fetcher.set_page(URL_A, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_A, URL_A)))
        svc = ts._make_service(fetcher)
        result = _run_async(svc.claim_and_run(sched_tenant))
        assert result["executed"]
        run = _article_by_external(sched_tenant, TOK_A)
        assert run["processing_status"] == "success"


def _run_async(coro):
    import asyncio

    return asyncio.run(coro)


async def test_duplicate_article_within_scan_dedup(monkeypatch, billed_tenant):
    """同一 scan 内重复子篇（翻页边界漂移）：只处理首次出现，run success 不撞唯一键。

    生产 agent 事故根因（2026-09-20）：offset 翻页间隙源顶部插入新消息使边界
    消息跨页重复，existing 快照不含本 run 新行 → 二次走新增分支撞
    (tenant_id, run_id, article_row_id) 唯一键致整轮回滚失败。
    """
    tenant = billed_tenant
    _patch_list_session(monkeypatch)
    _patch_list_client(monkeypatch)
    art = _own_article(TOK_A, BASE_TS)
    scan = _scan(art, art, total=2)  # 同一篇出现两次（跨页重叠形态）
    monkeypatch.setattr(FakeListClient, "fetch_all", lambda self: scan)

    fetcher = ts.StubFetcher()
    fetcher.set_page(
        URL_A, ts.FetchResult(status="ok", html=ts.make_article_html("t", BODY_A, URL_A))
    )
    svc = ts._make_service(fetcher)

    run_id = _create_list_sync_run(tenant)
    await svc.claim_and_run(tenant)
    run = _run_row(run_id)
    assert run["status"] == "success"
    items = ts._query_all(
        "SELECT id FROM bs_wechat_mp_sync_items WHERE run_id = %s AND tenant_id = %s",
        (run_id, tenant),
    )
    assert len(items) == 1
    rows = ts._query_all(
        "SELECT id FROM bs_wechat_mp_articles WHERE tenant_id = %s AND external_id = %s",
        (tenant, f"mp:s:{TOK_A}"),
    )
    assert len(rows) == 1
