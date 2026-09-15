"""WP7 定时复核与队列驱动单元测试（真实 PG，对齐 test_service/test_wp6_api 模式）。

覆盖（计划 WP7 节 + §2 测试矩阵「队列与并发」行）：
- 重试 due：next_retry_at 到期入队（sync_failed / no_credit 的 pending）、未到期不入队、
  已有活跃 item（queued/running run 上的 pending/running item）不重复入队、每 tick 上限
- 复核 due：last_checked_at 超 24h 入队（action='check'）、NULLS FIRST、未到期不入队、
  上限限速、alias/deleted/unconfirmed 行不复核
- 驱动：有 queued 租户被领取执行（假 fetcher/embedding 注入，test_service 替身模式）、
  无 queued 租户不误执行、驱动锁被占/Redis 不可用拒绝启动、stop 释放锁
- 唤醒：notify 在 Redis 不可用/异常时不抛、受理路径（手动导入/retry/recheck/callback）
  调用后事务仍成功、受理成功后唤醒 key 有值（rpush/lpop 往返）
- 限流：retry/recheck 超 queued 上限被拒；advisory lock 下并发受理串行化
- 400：platform_admin 无租户上下文调租户端点

测试用 URL 均为伪造值；鉴权头 ``Bearer <role>:<tenant_id>`` 仅测试内约定。
"""

import asyncio
import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from src.wechat_mp import api as wechat_mp_api
from src.wechat_mp import callback as cb
from src.wechat_mp import notify as notify_mod
from src.wechat_mp import service as svc_mod
from src.wechat_mp.scheduler import (
    DRIVER_LOCK_PREFIX,
    RECHECK_TICK_LIMIT,
    RETRY_TICK_LIMIT,
    WeChatMPScheduler,
)
from src.wechat_mp.service import MAX_QUEUED_RUNS, WeChatMPBusinessError

from . import test_service as ts

URL_1 = "https://mp.weixin.qq.com/s/Wp7Test0001"


# ------------------------------- 测试替身 -------------------------------


class FakeQueueRedis(ts.FakeRedis):
    """进程内 Redis 替身：在 test_service.FakeRedis 锁语义上补 rpush/lpop 队列。"""

    def __init__(self, available: bool = True):
        super().__init__(available)
        self._lists: dict = {}

    def rpush(self, key, value) -> bool:
        if not self._available:
            return False
        with self._lock:
            self._lists.setdefault(key, []).append(value)
        return True

    def lpop(self, key):
        if not self._available:
            return None
        with self._lock:
            bucket = self._lists.get(key)
            if not bucket:
                return None
            return bucket.pop(0)


def _fake_require_admin(request):
    """测试用鉴权替身：Authorization: Bearer <role>:<tenant_id>（portal 允许无租户）。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    token = auth[7:]
    role, _, tenant = token.partition(":")
    if role == "platform_admin":
        return {"user_id": "u-wp7-test", "tenant_id": tenant or None, "role": role}
    if role == "tenant_admin" and tenant:
        return {"user_id": "u-wp7-test", "tenant_id": tenant, "role": role}
    raise HTTPException(status_code=401, detail="未登录或登录已过期")


@pytest.fixture()
def client(monkeypatch):
    app = FastAPI()
    app.include_router(wechat_mp_api.router)
    monkeypatch.setattr(wechat_mp_api, "require_admin", _fake_require_admin)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _real_vector_db_patch(monkeypatch):
    """service 内 get_vector_db 替换为真实 pgvector 实现（对齐 test_service）。"""
    module = ts._load_real_vector_db()
    monkeypatch.setattr(svc_mod, "get_vector_db", module.get_vector_db)


# ------------------------------- 辅助 -------------------------------


def _naive(hours_ago: float) -> datetime:
    """UTC naive 时间戳（对齐 service 的 next_retry_at 写入口径）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_ago)


def _local_naive(hours_ago: float) -> datetime:
    """本地 naive 时间戳（对齐 service 的 last_checked_at = now() 会话时区写入口径）。"""
    return datetime.now() - timedelta(hours=hours_ago)


def _insert_article(
    tenant_id: str,
    external_id: str,
    *,
    status: str = "active",
    processing_status: str = "success",
    next_retry_at: datetime = None,
    last_checked_at: datetime = None,
) -> int:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_articles
                (tenant_id, external_id, original_url, fetch_url, source_channel,
                 status, processing_status, next_retry_at, last_checked_at)
            VALUES (%s, %s, %s, %s, 'manual', %s, %s, %s, %s)
            RETURNING id
            """,
            (
                tenant_id,
                external_id,
                f"https://mp.weixin.qq.com/s/{external_id}",
                f"https://mp.weixin.qq.com/s/{external_id}",
                status,
                processing_status,
                next_retry_at,
                last_checked_at,
            ),
        )
        row_id = cursor.fetchone()["id"]
        conn.commit()
    return row_id


def _insert_run_with_item(tenant_id: str, article_row_id: int, *, run_status: str,
                          item_status: str) -> int:
    """手工构造挂在指定状态 run 上的 item（幂等/活跃判定用）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_runs
                (tenant_id, trigger_type, status, total_count, owner_token, heartbeat_at)
            VALUES (%s, 'manual', %s, 1, 'manual-owner', now())
            RETURNING id
            """,
            (tenant_id, run_status),
        )
        run_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO bs_wechat_mp_sync_items
                (tenant_id, run_id, article_row_id, action, status)
            VALUES (%s, %s, %s, NULL, %s)
            """,
            (tenant_id, run_id, article_row_id, item_status),
        )
        conn.commit()
    return run_id


def _fill_queued_runs(tenant_id: str, count: int) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for _ in range(count):
            cursor.execute(
                """
                INSERT INTO bs_wechat_mp_sync_runs
                    (tenant_id, trigger_type, status, total_count)
                VALUES (%s, 'manual', 'queued', 0)
                """,
                (tenant_id,),
            )
        conn.commit()


def _query_all(sql, params):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return cursor.fetchall()


def _query_one(sql, params):
    rows = _query_all(sql, params)
    return rows[0] if rows else None


def _all_run_items(tenant_id: str):
    return _query_all(
        """
        SELECT r.trigger_type, r.status AS run_status,
               i.action, i.status AS item_status, i.article_row_id
        FROM bs_wechat_mp_sync_runs r
        JOIN bs_wechat_mp_sync_items i ON i.run_id = r.id AND i.tenant_id = r.tenant_id
        WHERE r.tenant_id = %s
        ORDER BY r.id, i.id
        """,
        (tenant_id,),
    )


def _queued_run_count(tenant_id: str) -> int:
    return int(_query_one(
        "SELECT count(*) AS c FROM bs_wechat_mp_sync_runs "
        "WHERE tenant_id = %s AND status = 'queued'",
        (tenant_id,),
    )["c"])


def _tick() -> dict:
    return WeChatMPScheduler(redis=FakeQueueRedis())._run_tick()


async def _wait_until(predicate, timeout: float = 15.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return bool(predicate())


# ------------------------------- tick ①：重试到期入队 -------------------------------


class TestRetryDueEnqueue:
    def test_retry_due_enqueued_with_null_action(self, require_db, tenant_id):
        """到期 sync_failed 与 no_credit 退避的 pending 都入队（retry run + action=NULL）。"""
        due_failed = _insert_article(
            tenant_id, "wp7rfail01", processing_status="sync_failed",
            next_retry_at=_naive(0.1),
        )
        due_pending = _insert_article(
            tenant_id, "wp7rpend1", processing_status="pending",
            next_retry_at=_naive(0.1),
        )
        result = _tick()
        assert result["retry_enqueued"] == 2
        assert result["recheck_enqueued"] == 0

        runs = _query_all(
            "SELECT trigger_type, status, total_count FROM bs_wechat_mp_sync_runs "
            "WHERE tenant_id = %s",
            (tenant_id,),
        )
        assert len(runs) == 1
        assert runs[0]["trigger_type"] == "retry"
        assert runs[0]["status"] == "queued"
        assert runs[0]["total_count"] == 2

        items = _all_run_items(tenant_id)
        assert {i["article_row_id"] for i in items} == {due_failed, due_pending}
        assert all(i["action"] is None and i["item_status"] == "pending" for i in items)

        # tick 只生成任务不改文章状态（由 worker 处理时更新，run 中断可重新生成）
        art = _query_one(
            "SELECT processing_status, next_retry_at FROM bs_wechat_mp_articles WHERE id = %s",
            (due_failed,),
        )
        assert art["processing_status"] == "sync_failed"
        assert art["next_retry_at"] is not None

    def test_retry_not_due_skipped(self, require_db, tenant_id):
        """next_retry_at 未到期不入队。"""
        _insert_article(
            tenant_id, "wp7rfuture", processing_status="sync_failed",
            next_retry_at=_naive(-5),  # 5 小时后才到期
        )
        result = _tick()
        assert result["retry_enqueued"] == 0
        assert _queued_run_count(tenant_id) == 0

    def test_retry_idempotent_with_active_item(self, require_db, tenant_id):
        """已有挂在 queued/running run 上的 pending/running item → 不重复入队。"""
        a_queued = _insert_article(
            tenant_id, "wp7ridem1", processing_status="sync_failed",
            next_retry_at=_naive(0.1),
        )
        _insert_run_with_item(
            tenant_id, a_queued, run_status="queued", item_status="pending"
        )
        a_running = _insert_article(
            tenant_id, "wp7ridem2", processing_status="pending",
            next_retry_at=_naive(0.1),
        )
        _insert_run_with_item(
            tenant_id, a_running, run_status="running", item_status="running"
        )
        result = _tick()
        assert result["retry_enqueued"] == 0
        # tick 未新增任何 run（既有 queued 1 条 + running 1 条原样保留）
        after = _query_all(
            "SELECT id FROM bs_wechat_mp_sync_runs WHERE tenant_id = %s", (tenant_id,)
        )
        assert len(after) == 2

    def test_retry_limit_per_tick(self, require_db, tenant_id):
        """单 tick 每租户上限 RETRY_TICK_LIMIT，超出留到下轮。"""
        for n in range(RETRY_TICK_LIMIT + 5):
            _insert_article(
                tenant_id, f"wp7rlmt{n:03d}", processing_status="sync_failed",
                next_retry_at=_naive(0.1),
            )
        first = _tick()
        assert first["retry_enqueued"] == RETRY_TICK_LIMIT
        assert _queued_run_count(tenant_id) == 1  # 按租户分组：每租户每 tick 一个 run

        second = _tick()  # 前 50 条已被活跃 item 覆盖，仅补余量
        assert second["retry_enqueued"] == 5
        assert _queued_run_count(tenant_id) == 2


# ------------------------------- tick ②：复核到期入队 -------------------------------


class TestRecheckDueEnqueue:
    def test_recheck_due_enqueued_with_check_action(self, require_db, tenant_id):
        """last_checked_at 超 24h → recheck run + action='check' item。"""
        due = _insert_article(
            tenant_id, "wp7cck001", processing_status="success",
            last_checked_at=_local_naive(25),
        )
        result = _tick()
        assert result["recheck_enqueued"] == 1
        items = _all_run_items(tenant_id)
        assert len(items) == 1
        assert items[0]["trigger_type"] == "recheck"
        assert items[0]["run_status"] == "queued"
        assert items[0]["action"] == "check"
        assert items[0]["article_row_id"] == due

    def test_recheck_not_due_skipped(self, require_db, tenant_id):
        _insert_article(
            tenant_id, "wp7cckrecent", processing_status="success",
            last_checked_at=_local_naive(2),
        )
        result = _tick()
        assert result["recheck_enqueued"] == 0
        assert _queued_run_count(tenant_id) == 0

    def test_recheck_never_checked_first(self, require_db, tenant_id):
        """NULLS FIRST：从未复核过的文章优先进入本批（限速不饿死新导入）。"""
        never = _insert_article(tenant_id, "wp7ccknull1", processing_status="success",
                                last_checked_at=None)
        for n in range(RECHECK_TICK_LIMIT):
            _insert_article(
                tenant_id, f"wp7cckold{n:02d}", processing_status="success",
                last_checked_at=_local_naive(30),
            )
        result = _tick()
        assert result["recheck_enqueued"] == RECHECK_TICK_LIMIT
        enqueued_ids = {i["article_row_id"] for i in _all_run_items(tenant_id)}
        assert never in enqueued_ids

    def test_recheck_limit_per_tick(self, require_db, tenant_id):
        for n in range(RECHECK_TICK_LIMIT + 3):
            _insert_article(
                tenant_id, f"wp7clmt{n:03d}", processing_status="success",
                last_checked_at=_local_naive(30),
            )
        first = _tick()
        assert first["recheck_enqueued"] == RECHECK_TICK_LIMIT
        assert _queued_run_count(tenant_id) == 1
        second = _tick()
        assert second["recheck_enqueued"] == 3
        assert _queued_run_count(tenant_id) == 2

    def test_recheck_skips_alias_deleted_unconfirmed(self, require_db, tenant_id):
        """仅 active 主记录参与复核：alias/deleted/unconfirmed 一律不复核。"""
        for ext, st in (("wp7cskipal", "alias"), ("wp7cskipdl", "deleted"),
                        ("wp7cskipuc", "unconfirmed")):
            _insert_article(tenant_id, ext, status=st, processing_status="success",
                            last_checked_at=_local_naive(48))
        result = _tick()
        assert result["recheck_enqueued"] == 0
        assert _queued_run_count(tenant_id) == 0

    def test_recheck_includes_deferred_articles(self, require_db, tenant_id):
        """WP10 P2：deferred（图片 VL 待解析，承诺「将自动重试」）随 24h 复核自动重试。

        到期的 deferred 行入 recheck run（action='check'）；未到期（last_checked_at
        新鲜）的 deferred 不入队——deferred 复用 last_checked_at 节流，不进失败退避。
        p1 存量 deferred 文章亦凭 pipeline_version 变化在复核重建分支处理。
        """
        due = _insert_article(
            tenant_id, "wp7cckdfr1", processing_status="deferred",
            last_checked_at=_local_naive(25),
        )
        fresh = _insert_article(
            tenant_id, "wp7cckdfr2", processing_status="deferred",
            last_checked_at=_local_naive(2),
        )
        result = _tick()
        assert result["recheck_enqueued"] == 1
        items = _all_run_items(tenant_id)
        assert len(items) == 1
        assert items[0]["action"] == "check"
        assert items[0]["article_row_id"] == due
        assert items[0]["article_row_id"] != fresh


# ------------------------------- 驱动协程 -------------------------------


class TestDriver:
    async def test_claims_queued_tenant_and_releases_lock_on_stop(self, require_db, tenant_id):
        """唤醒信号触发领取：queued 租户被真实执行；stop 后驱动锁释放。"""
        ts._create_tenant(tenant_id)
        fetcher = ts.StubFetcher()
        fetcher.set_page(
            ts.SHORT_URL, ts.ok_result(ts.make_article_html("WP7驱动文章", ts.BODY_V1))
        )
        run_id, _, item_ids, _ = ts._enqueue(tenant_id, [ts.SHORT_URL], trigger="manual")

        redis = FakeQueueRedis()
        svc = ts._make_service(fetcher, redis=redis)
        sched = WeChatMPScheduler(redis=redis, service=svc)
        redis.rpush(redis.make_key(notify_mod.WAKEUP_KEY), "1")  # 预置唤醒信号
        assert await sched.start() is True
        try:
            def _done():
                row = _query_one(
                    "SELECT status FROM bs_wechat_mp_sync_runs WHERE id = %s", (run_id,)
                )
                return row is not None and row["status"] == "success"

            assert await _wait_until(_done) is True
            item = _query_one(
                "SELECT status FROM bs_wechat_mp_sync_items WHERE id = %s", (item_ids[0],)
            )
            assert item["status"] == "success"
            assert int(_query_one(
                "SELECT count(*) AS c FROM documents WHERE tenant_id = %s", (tenant_id,)
            )["c"]) == 1
        finally:
            await sched.stop()

        # stop 释放单副本驱动锁：新 owner 可立即取得
        assert redis.acquire_lock(
            redis.make_key(DRIVER_LOCK_PREFIX), "post-stop", ex=60
        ) is True

    async def test_no_queued_tenant_noop(self, require_db, tenant_id):
        """无 queued 租户：唤醒轮不产生任何 run/误执行，调度器保持运行。"""
        redis = FakeQueueRedis()
        svc = ts._make_service(ts.StubFetcher(), redis=redis)
        sched = WeChatMPScheduler(redis=redis, service=svc)
        redis.rpush(redis.make_key(notify_mod.WAKEUP_KEY), "1")
        assert await sched.start() is True
        try:
            await asyncio.sleep(1.5)  # 等驱动循环消费唤醒信号并跑完一轮
            assert sched._task is not None and not sched._task.done()
            assert _query_one(
                "SELECT count(*) AS c FROM bs_wechat_mp_sync_runs WHERE tenant_id = %s",
                (tenant_id,),
            )["c"] == 0
        finally:
            await sched.stop()

    async def test_start_refuses_when_lock_held_or_redis_down(self):
        """驱动锁被占（已有副本）→ 空转返回；Redis 不可用 → 拒绝启动不无锁降级。"""
        redis = FakeQueueRedis()
        lock_key = redis.make_key(DRIVER_LOCK_PREFIX)
        # 锁值按 JSON 存储（对齐 service._lock_value 口径，FakeRedis.get 会反序列化）
        assert redis.acquire_lock(lock_key, json.dumps("other-replica"), ex=300) is True
        sched = WeChatMPScheduler(redis=redis)
        assert await sched.start() is False
        assert sched._task is None
        assert redis.get(lock_key) == "other-replica"  # 锁未被抢占

        sched2 = WeChatMPScheduler(redis=FakeQueueRedis(available=False))
        assert await sched2.start() is False


# ------------------------------- 遗留 A 回归：状态翻转不破坏复核零计费 -------------------------------


class TestCheckZeroBillingAfterRetryFlip:
    async def test_check_item_zero_billing_despite_retry_status_flip(
        self, require_db, tenant_id, monkeypatch
    ):
        """CR 遗留 A 回归：success 文章 → recheck 入队 → manual retry 受理翻转
        processing_status='pending' → FIFO 先执行 check item 时 hash 快路径不得因
        状态非 success 落到付费重建。断言 check/retry 两个 item 均零 embedding
        零计费，终态 success、doc 不变。"""
        monkeypatch.setattr(
            notify_mod, "_get_redis", lambda: FakeQueueRedis()
        )
        ts._create_tenant(tenant_id)
        fetcher = ts.StubFetcher()
        fetcher.set_page(
            ts.SHORT_URL, ts.ok_result(ts.make_article_html("春季活动", ts.BODY_V1))
        )
        embedding = ts.FakeEmbeddingClient()
        svc = ts._make_service(fetcher, embedding=embedding)

        # 首次入库：success + doc active
        _, row_ids, _, _ = ts._enqueue(tenant_id, [ts.SHORT_URL], trigger="manual")
        assert (await svc.claim_and_run(tenant_id))["executed"] is True
        article_before = _query_one(
            "SELECT doc_id, content_hash, processing_status FROM bs_wechat_mp_articles "
            "WHERE id = %s",
            (row_ids[0],),
        )
        assert article_before["processing_status"] == "success"
        records_before = _query_all(
            "SELECT record_id FROM chat_records WHERE tenant_id = %s", (tenant_id,)
        )
        chunks_before = _query_all(
            "SELECT c.id FROM chunks c JOIN documents d ON d.id = c.doc_id "
            "WHERE d.tenant_id = %s ORDER BY c.id",
            (tenant_id,),
        )
        calls_before = embedding.embed_batch_calls
        fetches_before = len(fetcher.calls)

        # recheck 先入队，retry 受理随后翻转 processing_status（遗留 A 触发序）
        recheck_run, _, recheck_item_ids, _ = ts._enqueue(
            tenant_id, [ts.SHORT_URL], trigger="recheck", action="check"
        )
        retry_result = svc_mod.retry_article(tenant_id, "u-wp7-cr", row_ids[0])
        assert retry_result["run_id"] is not None
        flipped = _query_one(
            "SELECT processing_status FROM bs_wechat_mp_articles WHERE id = %s",
            (row_ids[0],),
        )
        assert flipped["processing_status"] == "pending"  # 受理侧已翻转（场景前提）
        assert recheck_run < retry_result["run_id"]  # FIFO：check item 先执行

        assert (await svc.claim_and_run(tenant_id))["executed"] is True

        check_item = _query_one(
            "SELECT status, action, billing_status, credits_charged, run_id "
            "FROM bs_wechat_mp_sync_items WHERE id = %s",
            (recheck_item_ids[0],),
        )
        assert check_item["status"] == "success" and check_item["action"] == "check"
        assert check_item["billing_status"] == "not_required"
        assert float(check_item["credits_charged"]) == 0.0

        # retry item 随后执行：状态已被 check item 置回 success，同样零计费 check
        retry_item = _query_one(
            "SELECT i.status, i.action, i.billing_status, i.credits_charged, r.status AS run_status "
            "FROM bs_wechat_mp_sync_items i JOIN bs_wechat_mp_sync_runs r "
            "ON r.id = i.run_id AND r.tenant_id = i.tenant_id "
            "WHERE i.run_id = %s AND i.tenant_id = %s",
            (retry_result["run_id"], tenant_id),
        )
        assert retry_item["status"] == "success" and retry_item["action"] == "check"
        assert retry_item["billing_status"] == "not_required"
        assert float(retry_item["credits_charged"]) == 0.0
        assert retry_item["run_status"] == "success"

        # 零重嵌、零计费记录、chunks 未重建；两次抓取都真实发生（不允许抓取前跳过）
        assert embedding.embed_batch_calls == calls_before
        assert len(fetcher.calls) == fetches_before + 2
        records_after = _query_all(
            "SELECT record_id FROM chat_records WHERE tenant_id = %s", (tenant_id,)
        )
        assert len(records_after) == len(records_before)
        chunks_after = _query_all(
            "SELECT c.id FROM chunks c JOIN documents d ON d.id = c.doc_id "
            "WHERE d.tenant_id = %s ORDER BY c.id",
            (tenant_id,),
        )
        assert [c["id"] for c in chunks_after] == [c["id"] for c in chunks_before]

        article_after = _query_one(
            "SELECT doc_id, content_hash, processing_status, next_retry_at, status "
            "FROM bs_wechat_mp_articles WHERE id = %s",
            (row_ids[0],),
        )
        assert article_after["doc_id"] == article_before["doc_id"]
        assert article_after["content_hash"] == article_before["content_hash"]
        assert article_after["processing_status"] == "success"
        assert article_after["next_retry_at"] is None
        assert article_after["status"] == "active"
        assert int(_query_one(
            "SELECT count(*) AS c FROM documents WHERE tenant_id = %s", (tenant_id,)
        )["c"]) == 1


# ------------------------------- 唤醒通知 -------------------------------


class TestNotify:
    def test_notify_redis_unavailable_or_error_no_raise(self, monkeypatch):
        """Redis 不可用（rpush=False）与底层异常都不外抛，返回 False。"""
        monkeypatch.setattr(
            notify_mod, "_get_redis", lambda: FakeQueueRedis(available=False)
        )
        assert notify_mod.notify_queued_work() is False

        class _Boom:
            @staticmethod
            def make_key(prefix, identifier=""):
                return prefix

            @staticmethod
            def rpush(*args, **kwargs):
                raise RuntimeError("redis connection lost")

        monkeypatch.setattr(notify_mod, "_get_redis", lambda: _Boom())
        assert notify_mod.notify_queued_work() is False

    def test_import_notify_roundtrip(self, require_db, tenant_id, monkeypatch):
        """手动导入受理成功后唤醒 key 有值（rpush/lpop 往返）。"""
        redis = FakeQueueRedis()
        monkeypatch.setattr(notify_mod, "_get_redis", lambda: redis)
        result = svc_mod.import_urls(tenant_id, "u-1", [URL_1])
        assert result["run_id"] is not None
        key = redis.make_key(notify_mod.WAKEUP_KEY)
        assert len(redis._lists.get(key, [])) >= 1
        assert redis.lpop(key) is not None

    def test_retry_recheck_notify_roundtrip(self, require_db, tenant_id, monkeypatch):
        """retry/recheck 受理成功后唤醒 key 有值。"""
        redis = FakeQueueRedis()
        monkeypatch.setattr(notify_mod, "_get_redis", lambda: redis)
        art = _insert_article(tenant_id, "wp7ntfrt01", processing_status="sync_failed")
        assert svc_mod.retry_article(tenant_id, "u-1", art)["run_id"] is not None
        assert len(redis._lists.get(redis.make_key(notify_mod.WAKEUP_KEY), [])) == 1

        art2 = _insert_article(tenant_id, "wp7ntfrt02", processing_status="success")
        assert svc_mod.recheck_article(tenant_id, "u-1", art2)["run_id"] is not None
        assert len(redis._lists.get(redis.make_key(notify_mod.WAKEUP_KEY), [])) == 2

    def test_notify_failure_does_not_break_accept(self, require_db, tenant_id, monkeypatch):
        """唤醒通道整体故障：受理事务仍成功提交（import/retry/recheck/callback）。"""
        def _boom():
            raise RuntimeError("notify channel down")

        monkeypatch.setattr(notify_mod, "_get_redis", _boom)
        result = svc_mod.import_urls(tenant_id, "u-1", [URL_1])
        assert result["run_id"] is not None
        assert _query_one(
            "SELECT status FROM bs_wechat_mp_sync_runs WHERE id = %s",
            (result["run_id"],),
        )["status"] == "queued"

        article_id = _query_one(
            "SELECT id FROM bs_wechat_mp_articles WHERE tenant_id = %s ORDER BY id LIMIT 1",
            (tenant_id,),
        )["id"]
        assert svc_mod.recheck_article(tenant_id, "u-1", article_id)["run_id"] is not None

        config_id = f"chan_{uuid.uuid4().hex[:8]}"
        event_key = f"evt_wp7_{uuid.uuid4().hex[:8]}"
        accepted = cb._accept_masssend_event(
            tenant_id, config_id, event_key, {"MsgID": "777"}, [(1, URL_1)]
        )
        assert accepted == cb._ACCEPT_ACCEPTED

    def test_callback_notify_roundtrip(self, require_db, tenant_id, monkeypatch):
        """回调受理成功后唤醒 key 有值。"""
        redis = FakeQueueRedis()
        monkeypatch.setattr(notify_mod, "_get_redis", lambda: redis)
        config_id = f"chan_{uuid.uuid4().hex[:8]}"
        event_key = f"evt_wp7rt_{uuid.uuid4().hex[:8]}"
        accepted = cb._accept_masssend_event(
            tenant_id, config_id, event_key, {"MsgID": "778"}, [(1, URL_1)]
        )
        assert accepted == cb._ACCEPT_ACCEPTED
        assert len(redis._lists.get(redis.make_key(notify_mod.WAKEUP_KEY), [])) == 1


# ------------------------------- 受理限流（WP6 CR P2） -------------------------------


class TestEnqueueRateLimit:
    def test_retry_recheck_over_queued_limit_rejected(self, require_db, tenant_id):
        """retry/recheck 与 import-urls 同口径：同租户 queued run ≥ MAX_QUEUED_RUNS 拒绝。"""
        _fill_queued_runs(tenant_id, MAX_QUEUED_RUNS)
        retry_art = _insert_article(tenant_id, "wp7rla001", processing_status="sync_failed")
        with pytest.raises(WeChatMPBusinessError):
            svc_mod.retry_article(tenant_id, "u-1", retry_art)
        recheck_art = _insert_article(tenant_id, "wp7rla002", processing_status="success")
        with pytest.raises(WeChatMPBusinessError):
            svc_mod.recheck_article(tenant_id, "u-1", recheck_art)
        # 拒绝后不新增 run
        assert _queued_run_count(tenant_id) == MAX_QUEUED_RUNS

    def test_advisory_lock_serializes_concurrent_accept(self, require_db, tenant_id):
        """advisory xact lock：前一事务未提交时同键受理被阻塞（限流检查与插入原子）。"""
        from src.db.database import get_db_connection

        lock_sql = "SELECT pg_advisory_xact_lock(hashtext(%s))"
        lock_key_param = f"{svc_mod.ENQUEUE_LOCK_PREFIX}{tenant_id}"
        acquired: dict = {}

        with get_db_connection() as conn1:
            cursor1 = conn1.cursor()
            cursor1.execute(lock_sql, (lock_key_param,))

            def _second_connection():
                try:
                    with get_db_connection() as conn2:
                        cursor2 = conn2.cursor()
                        cursor2.execute(lock_sql, (lock_key_param,))
                        acquired["ok"] = True
                except Exception as e:  # noqa: BLE001
                    acquired["error"] = str(e)

            thread = threading.Thread(target=_second_connection, daemon=True)
            thread.start()
            time.sleep(0.8)
            assert acquired == {}  # 第一事务未提交，第二连接仍被阻塞
            conn1.rollback()
            thread.join(timeout=5)
            assert acquired.get("ok") is True


# ------------------------------- API 400：租户上下文（WP6 CR P2） -------------------------------


class TestTenantContextApi:
    def test_platform_admin_without_tenant_400(self, require_db, tenant_id, client):
        """platform_admin 无 X-Tenant-Id 调租户端点 → 400 明确业务错误（不进 SQL）。"""
        headers = {"Authorization": "Bearer platform_admin"}
        for path in ("/api/saas/wechat-mp/runs", "/api/saas/wechat-mp/articles"):
            resp = client.get(path, headers=headers)
            assert resp.status_code == 400
            assert "租户上下文" in resp.json()["detail"]
        resp = client.post(
            "/api/saas/wechat-mp/import-urls", json={"urls": [URL_1]}, headers=headers
        )
        assert resp.status_code == 400
        for suffix in ("retry", "recheck"):
            resp = client.post(f"/api/saas/wechat-mp/articles/1/{suffix}", headers=headers)
            assert resp.status_code == 400

        # 带租户上下文的 platform_admin 行为不变（空列表 200）；portal 端点不受影响
        resp = client.get(
            "/api/saas/wechat-mp/runs",
            headers={"Authorization": f"Bearer platform_admin:{tenant_id}"},
        )
        assert resp.status_code == 200
        resp = client.get("/api/saas/wechat-mp/portal/runs", headers=headers)
        assert resp.status_code == 200
