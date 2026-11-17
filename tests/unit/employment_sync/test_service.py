"""lyjy_employment service 单测：hash 三态、expires_at、软删→restore、删除事件
集合差、到期扫描、user_deleted 自愈、六分类路由、水位推进、账本。

注入点：_fetch_plan（脚本化四步拉取结果，替代真实 MySQL）+ FakeEmbeddingClient；
真实 DB（documents/chunks/chunks_vec/knowledge_categories + content_sync 四表）。
"""

import json
from typing import Dict, List

import pytest

import src.tenant_custom.lyjy_employment.fetcher as fetcher
from src.tenant_custom.lyjy_employment.service import (
    LyjyEmploymentSyncService,
    article_catalog_valid,
    jobs_catalog_valid,
)


class FakeEmbeddingClient:
    def __init__(self):
        self.calls = 0
        self.chunks = 0
        self.last_usage_tokens = 100

    def reset_usage(self):
        pass

    async def embed_batch(self, texts, batch_size=10):
        self.calls += 1
        self.chunks += len(texts)
        return [[0.01] * 1024 for _ in texts]


NOW = 1800000000  # 固定"当前时间"（目录有效判定用）


def _jsonb(harness, sql, tenant):
    """JSONB 列读取（psycopg2 已回 dict；兼容存量 str 形态）。"""
    value = harness["query"](sql, (tenant,))[0][sql.split("SELECT ")[1].split(" FROM")[0].strip()]
    if isinstance(value, str):
        value = json.loads(value)
    return value


def _job_row(job_id=101, refreshtime=1700000100, deadline=0, **overrides):
    row = {
        "id": job_id, "jobs_name": f"岗位{job_id}", "companyname": "测试公司",
        # 目录状态列（真实目录拉取必有；缺省即被判无效下架）
        "audit": 1, "display": 1, "close_type": None,
        "company_id": 1, "trade_cn": "", "nature_cn": "全职",
        "district_cn": "山东/聊城", "district": "", "tag_cn": "五险",
        "amount": 2, "category_cn": "运输类", "subclass": 0, "education_cn": "不限",
        "experience_cn": "不限", "minwage": 4000, "maxwage": 6000, "negotiable": 0,
        "contents": "<p>岗位职责内容</p>", "addtime": 1700000000,
        "deadline": deadline, "refreshtime": refreshtime, "focus_group": 0,
        "is_ex_serviceman": 0, "is_disabled": 0, "disabilityType": "",
        "disability": "", "sex_cn": "", "age": "", "language": "", "jobAsk": "",
        "cert": "", "scale_cn": "",
    }
    row.update(overrides)
    return row


def _article_row(table, row_id, addtime, title="标题", content="<p>正文</p>", **overrides):
    row = {
        "id": row_id, "type_id": 0, "title": title, "content": content,
        "author": "", "source": "", "addtime": addtime, "subsite_id": 0,
        # 目录状态列（真实目录拉取必有）
        "audit": 1, "is_display": 1,
    }
    if table == "qs_guide":
        row["type_id"] = 2  # qs_guide_category 的生涯指南
    row.update(overrides)
    return row


def _plan(tables=None, catalogs=None, deletes=None, increments=None,
          expired=None, limit=None) -> Dict:
    tables = tables or ["qs_jobs"]
    catalogs = catalogs if catalogs is not None else {t: [] for t in tables}
    increments = increments if increments is not None else {t: [] for t in tables}
    return {
        "tables": tables,
        "catalogs": catalogs,
        "deletes": deletes or {},
        "expired_job_ids": set(expired or ()),
        "increments": increments,
        "dict_cache": {
            "qs_category_jobs": {}, "qs_category_district": {},
            "qs_article_category": {}, "qs_guide_category": {
                "2": {"id": 2, "categoryname": "生涯指南"}},
        },
        "contacts": {}, "profiles": {},
        "fetch_complete": limit is None,
        "total_reported": sum(len(v) for v in catalogs.values()),
        "now_seconds": NOW,
    }


@pytest.fixture()
def harness(tenant_id, monkeypatch):
    """脚本化 _fetch_plan + fake embedding；真实 Postgres 落库。"""
    plans: List[Dict] = []

    def fake_fetch_plan(self, engine, tables, sync_state, limit):
        return plans.pop(0) if plans else _plan(tables=tables)

    monkeypatch.setattr(LyjyEmploymentSyncService, "_fetch_plan", fake_fetch_plan)

    class _FakeEngine:
        def dispose(self):
            pass

    monkeypatch.setattr(fetcher, "build_engine", lambda config: _FakeEngine())
    monkeypatch.setattr(
        fetcher, "load_connection_config",
        lambda source_row: {"host": "x", "port": 3306, "database": "gt_lyjy",
                            "user": "ro", "password": "pw"},
    )

    service = LyjyEmploymentSyncService(embedding_client=FakeEmbeddingClient())

    from src.core.cache_utils import invalidate_tenant_cache
    from src.db.database import get_db_connection

    def create_source(selection_mode="all", selected_ids=None):
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO tenants (tenant_id, company_name, credit_balance)
                VALUES (%s, 'employment 测试', 10000)
                ON CONFLICT (tenant_id) DO UPDATE
                SET credit_balance = EXCLUDED.credit_balance
                """,
                (tenant_id,),
            )
            cursor.execute(
                """
                INSERT INTO bs_content_sync_sources
                    (tenant_id, module, enabled, sync_interval_hours,
                     selection_mode, selected_ids)
                VALUES (%s, 'lyjy_employment', TRUE, 1, %s, %s)
                ON CONFLICT (tenant_id, module) DO UPDATE
                SET selection_mode = EXCLUDED.selection_mode,
                    selected_ids = EXCLUDED.selected_ids
                """,
                (tenant_id, selection_mode,
                 json.dumps(selected_ids) if selected_ids else None),
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
        "tenant_id": tenant_id, "service": service, "plans": plans,
        "create_source": create_source, "query": query,
        "embedding": service._embedding_client,
    }


# ------------------------------- 基础链路 -------------------------------


async def test_cold_start_new_and_ledger(harness):
    """冷启动：新建全链路 + runs/items/records 账本 + 水位推进。"""
    plan = _plan(
        tables=["qs_jobs", "qs_notice"],
        catalogs={"qs_jobs": [_job_row(101)], "qs_notice": [_article_row("qs_notice", 11, 1700000500)]},
        increments={"qs_jobs": [_job_row(101)], "qs_notice": [_article_row("qs_notice", 11, 1700000500)]},
        deletes={"qs_jobs": set()},
    )
    harness["plans"].append(plan)
    harness["create_source"]()
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["status"] == "success"
    assert result["counts"]["new"] == 2 and result["counts"]["skip"] == 0

    docs = harness["query"](
        "SELECT title, origin, external_id, status, source_type, expires_at "
        "FROM documents WHERE tenant_id = %s ORDER BY id", (harness["tenant_id"],))
    assert {d["external_id"] for d in docs} == {"job:101", "notice:11"}
    assert all(d["origin"] == "lyjy_employment" and d["status"] == "active" for d in docs)
    # deadline=0 → expires_at 不写
    assert all(d["expires_at"] is None for d in docs)

    # 分类路由：岗位→在招职位、通知→通知公告（两分类各自自举）
    cats = harness["query"](
        "SELECT display_name, source_type FROM knowledge_categories "
        "WHERE tenant_id = %s AND parent_id IS NULL", (harness["tenant_id"],))
    by_name = {c["display_name"]: c["source_type"] for c in cats}
    assert {"在招职位", "通知公告"} <= set(by_name)
    doc_cats = {d["external_id"]: d["source_type"] for d in docs}
    assert doc_cats["job:101"] == by_name["在招职位"]
    assert doc_cats["notice:11"] == by_name["通知公告"]

    # records：hash/doc_id/pipeline 就位
    records = harness["query"](
        "SELECT native_id, content_hash, doc_id, pipeline_version, processing_status, "
        "miss_streak, payload FROM bs_content_sync_records WHERE tenant_id = %s",
        (harness["tenant_id"],))
    assert all(r["content_hash"] and r["doc_id"] and r["miss_streak"] == 0
               for r in records)
    job_payload = next(r for r in records if r["native_id"] == "job:101")
    payload = job_payload["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload["name"] == "岗位101"

    # items：两条 new success；runs：计数；水位在 sources.sync_state
    items = harness["query"](
        "SELECT action, status, billing_status FROM bs_content_sync_items "
        "WHERE tenant_id = %s", (harness["tenant_id"],))
    assert {(i["action"], i["status"]) for i in items} == {("new", "success")}
    run = harness["query"](
        "SELECT status, new_count, embedding_tokens "
        "FROM bs_content_sync_runs WHERE tenant_id = %s", (harness["tenant_id"],))[0]
    assert run["status"] == "success" and run["new_count"] == 2
    assert run["embedding_tokens"] > 0
    # 水位推进：qs_jobs=1700000100、qs_notice=1700000500
    state = _jsonb(harness, "SELECT sync_state FROM bs_content_sync_sources "
                   "WHERE tenant_id = %s", harness["tenant_id"])
    assert state["watermarks"] == {"qs_jobs": 1700000100, "qs_notice": 1700000500}
    assert state["processed_delete_ids"] == {}  # 无新删除事件不入集合


async def test_rerun_unchanged_skips_without_updated_at_jitter(harness):
    """hash 未变重跑：全 skip 零 embedding；updated_at 不抖；metadata 不重写。"""
    plan = _plan(
        catalogs={"qs_jobs": [_job_row(101)]},
        increments={"qs_jobs": [_job_row(101)]},
    )
    harness["plans"].append(plan)
    harness["create_source"]()
    await harness["service"].run_now(harness["tenant_id"])
    calls_after_first = harness["embedding"].calls

    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101)]},
        increments={"qs_jobs": [_job_row(101)]},
    ))
    second = await harness["service"].run_now(harness["tenant_id"])
    assert second["status"] == "success", second
    assert second["counts"]["skip"] == 1, second
    assert harness["embedding"].calls == calls_after_first  # 零重嵌
    doc = harness["query"](
        "SELECT updated_at, created_at FROM documents WHERE tenant_id = %s "
        "AND external_id = 'job:101'", (harness["tenant_id"],))[0]
    assert doc["updated_at"] == doc["created_at"]  # 未变跳过不抖 updated_at
    item = harness["query"](
        "SELECT action, status FROM bs_content_sync_items WHERE run_id = %s",
        (second["run_id"],))[0]
    assert (item["action"], item["status"]) == ("skip", "skipped")


async def test_content_change_rebuilds_chunks(harness):
    """内容变更：删旧 chunks/chunks_vec 重建、重嵌计费、doc 行复用不换新。"""
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101)]},
        increments={"qs_jobs": [_job_row(101)]},
    ))
    harness["create_source"]()
    await harness["service"].run_now(harness["tenant_id"])
    doc_before = harness["query"](
        "SELECT id, raw_text FROM documents WHERE external_id = 'job:101' "
        "AND tenant_id = %s", (harness["tenant_id"],))[0]
    old_chunk = harness["query"](
        "SELECT id FROM chunks WHERE doc_id = %s", (doc_before["id"],))

    changed = _job_row(101, contents="<p>变更后的职责</p>")
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [changed]},
        increments={"qs_jobs": [changed]},
    ))
    second = await harness["service"].run_now(harness["tenant_id"])
    assert second["counts"]["update"] == 1
    doc_after = harness["query"](
        "SELECT id, raw_text FROM documents WHERE external_id = 'job:101' "
        "AND tenant_id = %s", (harness["tenant_id"],))[0]
    assert doc_after["id"] == doc_before["id"]  # doc 行复用
    assert "变更后的职责" in doc_after["raw_text"]
    new_chunk = harness["query"](
        "SELECT id FROM chunks WHERE doc_id = %s", (doc_before["id"],))
    assert old_chunk and new_chunk and old_chunk[0]["id"] != new_chunk[0]["id"]


async def test_expires_at_written_and_refreshed(harness):
    """deadline>0 → expires_at 写入；deadline 延长（内容未变）→ skip 路径轻量
    更新顺带刷新 expires_at（时效归状态机管，不进 hash）。"""
    deadline = NOW + 86400
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101, deadline=deadline)]},
        increments={"qs_jobs": [_job_row(101, deadline=deadline)]},
    ))
    harness["create_source"]()
    await harness["service"].run_now(harness["tenant_id"])
    doc = harness["query"](
        "SELECT expires_at FROM documents WHERE external_id = 'job:101' "
        "AND tenant_id = %s", (harness["tenant_id"],))[0]
    assert doc["expires_at"] is not None

    extended = NOW + 7 * 86400
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101, deadline=extended)]},
        increments={"qs_jobs": [_job_row(101, deadline=extended)]},
    ))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["skip"] == 1  # hash 未变走 skip
    doc2 = harness["query"](
        "SELECT expires_at FROM documents WHERE external_id = 'job:101' "
        "AND tenant_id = %s", (harness["tenant_id"],))[0]
    assert doc2["expires_at"] > doc["expires_at"]  # 有效期顺延


# ------------------------------- 删除与恢复 -------------------------------


async def test_off_shelf_soft_delete_and_restore(harness):
    """下架（display=0）→ 当轮软删；重新上架 → 目录比对 restore 零计费复用 chunks。"""
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101)]},
        increments={"qs_jobs": [_job_row(101)]},
    ))
    harness["create_source"]()
    await harness["service"].run_now(harness["tenant_id"])
    chunks_before = harness["query"](
        "SELECT COUNT(*) c FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "WHERE d.external_id = 'job:101' AND d.tenant_id = %s",
        (harness["tenant_id"],))[0]["c"]
    assert chunks_before == 1

    off = _job_row(101, display=0)
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [off]}, increments={"qs_jobs": []},
    ))
    second = await harness["service"].run_now(harness["tenant_id"])
    assert second["counts"]["delete"] == 1
    doc = harness["query"](
        "SELECT status FROM documents WHERE external_id = 'job:101' "
        "AND tenant_id = %s", (harness["tenant_id"],))[0]
    assert doc["status"] == "deleted"

    back = _job_row(101)
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [back]}, increments={"qs_jobs": []},
    ))
    third = await harness["service"].run_now(harness["tenant_id"])
    assert third["counts"]["restore"] == 1
    doc2 = harness["query"](
        "SELECT status FROM documents WHERE external_id = 'job:101' "
        "AND tenant_id = %s", (harness["tenant_id"],))[0]
    assert doc2["status"] == "active"
    chunks_after = harness["query"](
        "SELECT COUNT(*) c FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "WHERE d.external_id = 'job:101' AND d.tenant_id = %s",
        (harness["tenant_id"],))[0]["c"]
    assert chunks_after == chunks_before  # 复用旧 chunks 零重建


async def test_delete_event_set_diff_and_idempotent_replay(harness):
    """② 删除事件：新 id 集合差→当轮软删并记入 processed；重放差集为空不重复出 item。"""
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101)]},
        increments={"qs_jobs": [_job_row(101)]},
        deletes={"qs_jobs": {90}},  # 90 从未同步：无记录可删也不入已处理集合
    ))
    harness["create_source"]()
    await harness["service"].run_now(harness["tenant_id"])

    harness["plans"].append(_plan(
        catalogs={"qs_jobs": []}, increments={"qs_jobs": []},
        deletes={"qs_jobs": {90, 101}},
    ))
    second = await harness["service"].run_now(harness["tenant_id"])
    assert second["status"] == "success"
    assert second["counts"]["delete"] == 1  # 101 有记录 → 当轮软删；90 无记录不动作
    state = _jsonb(harness, "SELECT sync_state FROM bs_content_sync_sources "
                   "WHERE tenant_id = %s", harness["tenant_id"])
    assert state["processed_delete_ids"] == {"qs_jobs": [101]}
    # 第三轮同样的全量 delete 集合 → 差集仅剩 90（无记录），无新增 delete item
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": []}, increments={"qs_jobs": []},
        deletes={"qs_jobs": {90, 101}},
    ))
    third = await harness["service"].run_now(harness["tenant_id"])
    assert third["counts"]["delete"] == 0


async def test_expiry_scan_soft_deletes_expired(harness):
    """③ 到期扫描 + 目录有效判定含到期：过期在招岗位软删且不允许 restore 循环。"""
    expired_deadline = NOW - 3600
    row = _job_row(101, deadline=expired_deadline)
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101, deadline=NOW + 86400)]},
        increments={"qs_jobs": [_job_row(101, deadline=NOW + 86400)]},
    ))
    harness["create_source"]()
    await harness["service"].run_now(harness["tenant_id"])
    assert harness["query"](
        "SELECT status FROM documents WHERE external_id = 'job:101' "
        "AND tenant_id = %s", (harness["tenant_id"],))[0]["status"] == "active"

    # 到期后状态位仍有效（实测坑①）：目录判定为无效 → 软删；expired 集合兜底
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [row]}, increments={"qs_jobs": []},
        expired={101},
    ))
    second = await harness["service"].run_now(harness["tenant_id"])
    assert second["counts"]["delete"] == 1
    assert harness["query"](
        "SELECT status FROM documents WHERE external_id = 'job:101' "
        "AND tenant_id = %s", (harness["tenant_id"],))[0]["status"] == "deleted"
    # 状态位不变的下轮不复活
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [row]}, increments={"qs_jobs": []},
        expired={101},
    ))
    third = await harness["service"].run_now(harness["tenant_id"])
    assert third["counts"]["restore"] == 0


async def test_two_strike_missing(harness):
    """消失两击：目录消失第一轮 miss_streak=1，第二轮软删（门禁 fetch_complete）。"""
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101)]},
        increments={"qs_jobs": [_job_row(101)]},
    ))
    harness["create_source"]()
    await harness["service"].run_now(harness["tenant_id"])

    harness["plans"].append(_plan(catalogs={"qs_jobs": []}, increments={"qs_jobs": []}))
    await harness["service"].run_now(harness["tenant_id"])
    assert harness["query"](
        "SELECT miss_streak FROM bs_content_sync_records WHERE native_id = 'job:101' "
        "AND tenant_id = %s", (harness["tenant_id"],))[0]["miss_streak"] == 1
    assert harness["query"](
        "SELECT status FROM documents WHERE external_id = 'job:101' "
        "AND tenant_id = %s", (harness["tenant_id"],))[0]["status"] == "active"

    harness["plans"].append(_plan(catalogs={"qs_jobs": []}, increments={"qs_jobs": []}))
    third = await harness["service"].run_now(harness["tenant_id"])
    assert third["counts"]["delete"] == 1


# ------------------------------- user_deleted 自愈 -------------------------------


async def test_user_deleted_self_heal_and_republish(harness):
    """用户知识库删除 → doc 行消失：置 user_deleted 静默跳过零计费；
    hash 变化视为重新发布清标记重建。"""
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101)]},
        increments={"qs_jobs": [_job_row(101)]},
    ))
    harness["create_source"]()
    await harness["service"].run_now(harness["tenant_id"])
    # 模拟用户硬删（知识库删除是行消失唯一路径）
    harness["query"](
        "DELETE FROM chunks_vec WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id IN "
        "(SELECT id FROM documents WHERE tenant_id = %s AND external_id = 'job:101'))",
        (harness["tenant_id"],))
    harness["query"](
        "DELETE FROM chunks WHERE doc_id IN (SELECT id FROM documents "
        "WHERE tenant_id = %s AND external_id = 'job:101')", (harness["tenant_id"],))
    harness["query"](
        "DELETE FROM documents WHERE tenant_id = %s AND external_id = 'job:101'",
        (harness["tenant_id"],))

    calls_before = harness["embedding"].calls
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101)]},
        increments={"qs_jobs": [_job_row(101)]},
    ))
    second = await harness["service"].run_now(harness["tenant_id"])
    assert second["counts"]["skip"] == 1  # user_deleted + hash 未变 → 静默跳过
    assert harness["embedding"].calls == calls_before
    record = harness["query"](
        "SELECT user_deleted, doc_id FROM bs_content_sync_records "
        "WHERE native_id = 'job:101' AND tenant_id = %s", (harness["tenant_id"],))[0]
    assert record["user_deleted"] is True

    # hash 变化 → 重新发布：清标记重建
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101, contents="<p>全新职责</p>")]},
        increments={"qs_jobs": [_job_row(101, contents="<p>全新职责</p>")]},
    ))
    third = await harness["service"].run_now(harness["tenant_id"])
    assert third["counts"]["new"] == 1
    record2 = harness["query"](
        "SELECT user_deleted FROM bs_content_sync_records "
        "WHERE native_id = 'job:101' AND tenant_id = %s", (harness["tenant_id"],))[0]
    assert record2["user_deleted"] is False
    assert harness["query"](
        "SELECT status FROM documents WHERE external_id = 'job:101' "
        "AND tenant_id = %s", (harness["tenant_id"],))[0]["status"] == "active"


# ------------------------------- 范围开关与失败路径 -------------------------------


async def test_reconcile_run_ignores_watermarks(harness, monkeypatch):
    """对账 run（trigger_type='reconcile'）：水位清零全量拉取（④全量 hash 比对），
    常规 run 仍走水位增量（每日对账兜底 refreshtime 不随编辑更新的滞后）。"""
    from src.tenant_custom.lyjy_employment.service import LyjyEmploymentSyncService

    base_fake = LyjyEmploymentSyncService._fetch_plan
    seen: Dict[str, Dict] = {}
    current = {"trigger_type": ""}

    def spy_fetch_plan(self, engine, tables, sync_state, limit):
        seen[current["trigger_type"]] = dict(sync_state or {})
        return base_fake(self, engine, tables, sync_state, limit)

    monkeypatch.setattr(LyjyEmploymentSyncService, "_fetch_plan", spy_fetch_plan)

    stale_row = _job_row(201, refreshtime=1)  # 旧 refreshtime：水位下不进增量
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [stale_row]},
        increments={"qs_jobs": [stale_row]},
    ))
    harness["create_source"]()
    # 预置水位：常规 run 不再拉 refreshtime=1 的旧行
    harness["service"]._save_sync_state(
        harness["tenant_id"], {"watermarks": {"qs_jobs": 1700000100}}
    )

    async def run_with(trigger_type):
        current["trigger_type"] = trigger_type
        from src.db.database import get_db_connection

        with get_db_connection() as conn:  # RETURNING 路径须显式 commit（query 助手不提交）
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO bs_content_sync_runs "
                "(tenant_id, module, trigger_type, status, owner_token, started_at, heartbeat_at) "
                "VALUES (%s, 'lyjy_employment', %s, 'running', 'tok', now(), now()) "
                "RETURNING id",
                (harness["tenant_id"], trigger_type),
            )
            run_id = cursor.fetchone()["id"]
            conn.commit()
        return await harness["service"]._execute_run(
            harness["tenant_id"], {"id": run_id, "trigger_type": trigger_type}, "tok"
        )

    reconcile_result = await run_with("reconcile")
    assert reconcile_result["status"] == "success"
    assert seen["reconcile"] == {}  # 水位清零 → 全量比对

    # 常规 run：保留水位语义
    fresh_row = _job_row(202, refreshtime=1700000200)
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [fresh_row]},
        increments={"qs_jobs": [fresh_row]},
    ))
    manual_result = await run_with("manual")
    assert manual_result["status"] == "success"
    assert seen["manual"].get("watermarks", {}).get("qs_jobs") == 1700000100


async def test_selection_ids_filters_tables(harness):
    """selection_mode='ids'：selected_ids=源表白名单，范围外不拉不建。"""
    plan = _plan(
        tables=["qs_notice"],
        catalogs={"qs_notice": [_article_row("qs_notice", 11, 1700000500)]},
        increments={"qs_notice": [_article_row("qs_notice", 11, 1700000500)]},
    )
    harness["plans"].append(plan)
    harness["create_source"](selection_mode="ids", selected_ids=["qs_notice"])
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["counts"]["new"] == 1
    docs = harness["query"](
        "SELECT external_id FROM documents WHERE tenant_id = %s",
        (harness["tenant_id"],))
    assert {d["external_id"] for d in docs} == {"notice:11"}


async def test_pii_failure_blocks_watermark(harness):
    """PII 出站拦截：item 失败 run partial_failed；水位不推进（重试自然重放）。"""
    poison = _job_row(101, contents="<p>联系我 13912345678</p>")
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [poison]},
        increments={"qs_jobs": [poison]},
    ))
    harness["create_source"]()
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["status"] == "partial_failed" and result["failed"] == 1
    item = harness["query"](
        "SELECT status, error_code FROM bs_content_sync_items WHERE run_id = %s",
        (result["run_id"],))[0]
    assert item["status"] == "failed" and item["error_code"] == "pii_rejected"
    state = harness["query"](
        "SELECT sync_state FROM bs_content_sync_sources WHERE tenant_id = %s",
        (harness["tenant_id"],))[0]["sync_state"]
    assert not state  # 失败不推进水位
    record = harness["query"](
        "SELECT processing_status, next_retry_at FROM bs_content_sync_records "
        "WHERE native_id = 'job:101' AND tenant_id = %s", (harness["tenant_id"],))[0]
    assert record["processing_status"] == "sync_failed"
    assert record["next_retry_at"] is not None  # 退避期内不再重试该行


async def test_no_source_row_aborts(harness):
    """源行守卫：无源行（revoke 删行）→ interrupted，不拉取不落库。"""
    harness["create_source"]()
    harness["query"](
        "DELETE FROM bs_content_sync_sources WHERE tenant_id = %s "
        "AND module = 'lyjy_employment'", (harness["tenant_id"],))
    result = await harness["service"].run_now(harness["tenant_id"])
    assert result["status"] == "interrupted"
    assert harness["query"](
        "SELECT COUNT(*) c FROM documents WHERE tenant_id = %s",
        (harness["tenant_id"],))[0]["c"] == 0


# ------------------------------- 纯函数判定 -------------------------------


def test_jobs_catalog_valid_conditions():
    """目录有效判定：audit/display/close_type/到期（集中一处待客户确认语义）。"""
    assert jobs_catalog_valid({"audit": 1, "display": 1, "close_type": None,
                               "deadline": 0}, NOW)
    assert jobs_catalog_valid({"audit": 1, "display": 1, "close_type": 0,
                               "deadline": NOW + 1}, NOW)
    assert not jobs_catalog_valid({"audit": 2, "display": 1, "close_type": None,
                                   "deadline": 0}, NOW)  # 未审核
    assert not jobs_catalog_valid({"audit": 1, "display": 0, "close_type": None,
                                   "deadline": 0}, NOW)  # 下架
    assert not jobs_catalog_valid({"audit": 1, "display": 1, "close_type": 1,
                                   "deadline": 0}, NOW)  # 手动关闭
    assert not jobs_catalog_valid({"audit": 1, "display": 1, "close_type": 2,
                                   "deadline": 0}, NOW)  # 到期关闭
    assert not jobs_catalog_valid({"audit": 1, "display": 1, "close_type": None,
                                   "deadline": NOW - 1}, NOW)  # 已过期（不允许 restore）
    assert article_catalog_valid("qs_guide", {"audit": 1, "is_display": 1})
    assert not article_catalog_valid("qs_guide", {"audit": 0, "is_display": 1})
    assert article_catalog_valid("qs_notice", {"is_display": 1})  # 无 audit 列
    assert not article_catalog_valid("qs_explain", {"is_display": 0})


async def test_summary_populated_and_backfilled_via_skip(harness):
    """列表摘要：new 写入正文概要；存量空 summary 由 skip 路径零计费回填且幂等。"""
    plan = _plan(
        catalogs={"qs_jobs": [_job_row(101)]},
        increments={"qs_jobs": [_job_row(101)]},
    )
    harness["plans"].append(plan)
    harness["create_source"]()
    first = await harness["service"].run_now(harness["tenant_id"])
    assert first["status"] == "success" and first["counts"]["new"] == 1

    doc = harness["query"](
        "SELECT raw_text, summary, updated_at, created_at FROM documents "
        "WHERE tenant_id = %s AND external_id = 'job:101'", (harness["tenant_id"],))[0]
    expected = " ".join((doc["raw_text"] or "").split())[:200]
    assert doc["summary"] == expected and doc["summary"]

    # 模拟存量旧数据：清空 summary 后走 skip 路径，应零计费回填、不抖 updated_at
    harness["query"]("UPDATE documents SET summary = NULL, updated_at = created_at "
                     "WHERE tenant_id = %s AND external_id = 'job:101'", (harness["tenant_id"],))
    calls_after_first = harness["embedding"].calls
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101)]},
        increments={"qs_jobs": [_job_row(101)]},
    ))
    second = await harness["service"].run_now(harness["tenant_id"])
    assert second["status"] == "success" and second["counts"]["skip"] == 1, second
    assert harness["embedding"].calls == calls_after_first  # 回填零重嵌
    doc2 = harness["query"](
        "SELECT summary, updated_at, created_at FROM documents "
        "WHERE tenant_id = %s AND external_id = 'job:101'", (harness["tenant_id"],))[0]
    assert doc2["summary"] == expected  # 已回填
    # 回填属于值变化，允许 updated_at 刷新；但 summary 幂等：第三轮不再 UPDATE
    harness["plans"].append(_plan(
        catalogs={"qs_jobs": [_job_row(101)]},
        increments={"qs_jobs": [_job_row(101)]},
    ))
    third = await harness["service"].run_now(harness["tenant_id"])
    assert third["counts"]["skip"] == 1
    doc3 = harness["query"](
        "SELECT summary, updated_at FROM documents "
        "WHERE tenant_id = %s AND external_id = 'job:101'", (harness["tenant_id"],))[0]
    assert doc3["summary"] == expected
    assert doc3["updated_at"] == doc2["updated_at"]  # 值相等不再 UPDATE
