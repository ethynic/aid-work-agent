"""sccy_policy 单测：article_valid/hidden 过滤、cate 路径（防环）、全量即对账
（new → skip 零计费 → closed 软删 → 恢复 restore）、截断 run 不做软删对账、
来源标注与创业政策分类路由。
"""

import json

import pytest

import src.tenant_custom.sccy_policy.fetcher as sccy_fetcher
import src.tenant_custom.sccy_policy.service as sccy_service
from src.tenant_custom.lyjy_employment.renderer import render_article
from src.tenant_custom.sccy_policy.fetcher import article_valid, build_cate_maps
from src.tenant_custom.sccy_policy.service import SccyPolicySyncService

# 测试用例与 test_service.py 共用 conftest（tenant_id/require_db fixtures）


# ------------------------------- 纯函数 -------------------------------


def test_article_valid_filters():
    hidden = {"117"}
    assert article_valid({"audit": 1, "closed": 0, "cate_id": 105}, hidden)
    assert not article_valid({"audit": 2, "closed": 0, "cate_id": 105}, hidden)  # 待审
    assert not article_valid({"audit": 1, "closed": 1, "cate_id": 105}, hidden)  # 关闭
    assert not article_valid({"audit": 1, "closed": 0, "cate_id": 117}, hidden)  # 隐藏分类
    assert not article_valid({"audit": 0, "closed": 0, "cate_id": None}, hidden)


def test_build_cate_maps_path_and_cycle_guard():
    rows = [
        {"cate_id": 1, "parent_id": 0, "title": "行业资讯", "hidden": 0},
        {"cate_id": 105, "parent_id": 1, "title": "政策东风", "hidden": 0},
        {"cate_id": 117, "parent_id": 1, "title": "客户端", "hidden": 1},
        # 防环：A→B→A
        {"cate_id": 200, "parent_id": 201, "title": "环A", "hidden": 0},
        {"cate_id": 201, "parent_id": 200, "title": "环B", "hidden": 0},
    ]
    maps = build_cate_maps(rows)
    assert maps["path_of"]["105"] == "行业资讯 / 政策东风"
    assert maps["hidden_ids"] == {"117"}
    assert maps["path_of"]["200"] == "环B / 环A"  # 上溯被 seen 截断，不死循环


def test_sccy_renderer_source_and_category():
    doc = render_article(
        "zc_article",
        {"article_id": 9, "cate_id": 105, "title": "担保贷款新政",
         "desc": "摘要", "details": "<p>细则</p>", "dateline": 1700000000},
        category_path="行业资讯 / 政策东风", region="",
        source_name="水城创业网", id_column="article_id",
    )
    assert doc.external_id == "zcarticle:9"
    assert doc.category_display_name == "创业政策"
    assert doc.content_md.endswith("数据来源：水城创业网。\n")


# ------------------------------- 服务链路（真实 DB） -------------------------------


class FakeEmbeddingClient:
    def __init__(self):
        self.calls = 0
        self.last_usage_tokens = 50

    def reset_usage(self):
        pass

    async def embed_batch(self, texts, batch_size=10):
        self.calls += 1
        return [[0.02] * 1024 for _ in texts]


CATE_ROWS = [
    {"cate_id": 1, "parent_id": 0, "title": "行业资讯", "hidden": 0},
    {"cate_id": 105, "parent_id": 1, "title": "政策东风", "hidden": 0},
    {"cate_id": 117, "parent_id": 1, "title": "客户端", "hidden": 1},
]


def _zc_row(article_id, title="政策", details="<p>政策正文</p>",
            audit=1, closed=0, cate_id=105):
    return {"article_id": article_id, "cate_id": cate_id, "title": title,
            "desc": "摘要", "details": details, "linkurl": "",
            "audit": audit, "closed": closed, "dateline": 1700000000}


@pytest.fixture()
def sccy_harness(tenant_id, monkeypatch):
    """脚本化 sccy 拉取（fetch_articles/fetch_cate_rows）+ fake 连接与 embedding。"""
    articles: list = []

    def fake_fetch(engine):
        return articles

    monkeypatch.setattr(sccy_fetcher, "fetch_articles", fake_fetch)
    monkeypatch.setattr(sccy_fetcher, "fetch_cate_rows", lambda engine: list(CATE_ROWS))

    class _FakeEngine:
        def dispose(self):
            pass

    monkeypatch.setattr(sccy_service, "build_engine", lambda config: _FakeEngine())
    monkeypatch.setattr(
        sccy_service, "load_connection_config",
        lambda source_row: {"host": "x", "port": 3306, "database": "gt_sccy",
                            "user": "ro", "password": "pw"},
    )

    service = SccyPolicySyncService(embedding_client=FakeEmbeddingClient())

    from src.core.cache_utils import invalidate_tenant_cache
    from src.db.database import get_db_connection

    def create_source():
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO tenants (tenant_id, company_name, credit_balance)
                VALUES (%s, 'sccy 测试', 10000)
                ON CONFLICT (tenant_id) DO UPDATE
                SET credit_balance = EXCLUDED.credit_balance
                """,
                (tenant_id,),
            )
            cursor.execute(
                """
                INSERT INTO bs_content_sync_sources
                    (tenant_id, module, enabled, sync_interval_hours, selection_mode)
                VALUES (%s, 'sccy_policy', TRUE, 24, 'all')
                ON CONFLICT (tenant_id, module) DO NOTHING
                """,
                (tenant_id,),
            )
            conn.commit()
        invalidate_tenant_cache(tenant_id)

    def query(sql, params=()):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            if cursor.description is None:
                conn.commit()
                return []
            return [dict(r) for r in cursor.fetchall()]

    return {
        "tenant_id": tenant_id, "service": service, "articles": articles,
        "create_source": create_source, "query": query,
        "embedding": service._embedding_client,
    }


async def test_sccy_full_compare_new_then_skip_zero_billing(sccy_harness):
    """每日全量：首轮 new；重跑全 skip 零 embedding（全量即对账）。"""
    sccy_harness["create_source"]()
    sccy_harness["articles"].append(_zc_row(9))
    first = await sccy_harness["service"].run_now(sccy_harness["tenant_id"])
    assert first["status"] == "success" and first["counts"]["new"] == 1
    calls = sccy_harness["embedding"].calls
    assert calls >= 1

    second = await sccy_harness["service"].run_now(sccy_harness["tenant_id"])
    assert second["counts"]["skip"] == 1 and second["counts"]["new"] == 0
    assert sccy_harness["embedding"].calls == calls  # 零重嵌零计费

    doc = sccy_harness["query"](
        "SELECT d.source_type, d.raw_text FROM documents d "
        "WHERE d.tenant_id = %s AND d.external_id = 'zcarticle:9'",
        (sccy_harness["tenant_id"],))[0]
    cats = sccy_harness["query"](
        "SELECT source_type FROM knowledge_categories "
        "WHERE tenant_id = %s AND display_name = '创业政策' AND parent_id IS NULL",
        (sccy_harness["tenant_id"],))
    assert doc["source_type"] == cats[0]["source_type"]  # 创业政策分类
    assert "数据来源：水城创业网" in doc["raw_text"]
    # 记录账本 payload 带状态原值（对账用；JSONB 列 psycopg2 已回 dict）
    record = sccy_harness["query"](
        "SELECT payload FROM bs_content_sync_records WHERE native_id = 'zcarticle:9' "
        "AND tenant_id = %s", (sccy_harness["tenant_id"],))[0]
    payload = record["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["audit"] == 1 and payload["closed"] == 0 and payload["cate_id"] == "105"
    # 无水位：sccy 不写 sync_state（全量即对账）
    source = sccy_harness["query"](
        "SELECT sync_state FROM bs_content_sync_sources WHERE tenant_id = %s",
        (sccy_harness["tenant_id"],))[0]
    assert source["sync_state"] is None


async def test_sccy_closed_soft_delete_and_restore(sccy_harness):
    """closed=1 → 当轮软删；恢复 closed=0 → restore 复用 chunks。"""
    sccy_harness["create_source"]()
    sccy_harness["articles"].append(_zc_row(9))
    await sccy_harness["service"].run_now(sccy_harness["tenant_id"])

    sccy_harness["articles"][0] = _zc_row(9, closed=1)
    second = await sccy_harness["service"].run_now(sccy_harness["tenant_id"])
    assert second["counts"]["delete"] == 1
    assert sccy_harness["query"](
        "SELECT status FROM documents WHERE external_id = 'zcarticle:9' "
        "AND tenant_id = %s", (sccy_harness["tenant_id"],))[0]["status"] == "deleted"

    sccy_harness["articles"][0] = _zc_row(9)
    third = await sccy_harness["service"].run_now(sccy_harness["tenant_id"])
    assert third["counts"]["restore"] == 1
    assert sccy_harness["query"](
        "SELECT status FROM documents WHERE external_id = 'zcarticle:9' "
        "AND tenant_id = %s", (sccy_harness["tenant_id"],))[0]["status"] == "active"


async def test_sccy_truncated_run_skips_off_shelf_reconcile(sccy_harness):
    """截断 run（--limit 冒烟）：有效集不完整，不做软删对账防误删。"""
    sccy_harness["create_source"]()
    sccy_harness["articles"].extend([_zc_row(9), _zc_row(10)])
    await sccy_harness["service"].run_now(sccy_harness["tenant_id"])
    assert sccy_harness["query"](
        "SELECT COUNT(*) c FROM documents WHERE tenant_id = %s",
        (sccy_harness["tenant_id"],))[0]["c"] == 2

    # limit=1：只看到 article 9，article 10 不在有效集——但截断 run 不软删
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, "
            "status, owner_token) VALUES (%s, 'sccy_policy', 'manual', 'running', 'tok') "
            "RETURNING id",
            (sccy_harness["tenant_id"],))
        run_id = cursor.fetchone()["id"]
        conn.commit()

    result = await sccy_harness["service"]._execute_run(
        sccy_harness["tenant_id"], {"id": run_id}, "tok", limit=1)
    assert result["status"] == "partial_failed"  # 截断 run 不算完整
    assert sccy_harness["query"](
        "SELECT COUNT(*) c FROM documents WHERE tenant_id = %s AND status = 'active'",
        (sccy_harness["tenant_id"],))[0]["c"] == 2  # 无误删
