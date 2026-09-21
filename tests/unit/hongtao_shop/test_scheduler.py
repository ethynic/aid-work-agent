"""hongtao_shop P2 调度器测试：到期生成/防堆积/间隔解析/stale 回收/claim_and_run。"""

import asyncio
from datetime import datetime, timedelta

import pytest

import src.tenant_custom.hongtao_shop.scheduler as sched_mod
from src.tenant_custom.hongtao_shop.scheduler import (
    DEFAULT_INTERVAL_HOURS,
    HongtaoIngestScheduler,
    _parse_interval_hours,
)


class _FakeRedis:
    def __init__(self):
        self.locks = {}
        self.queue = []
        self.available = True

    def is_available(self):
        return self.available

    def make_key(self, key):
        return f"aid:{key}"

    def acquire_lock(self, key, owner, ttl=None, ex=None):
        if key in self.locks:
            return False
        self.locks[key] = owner
        return True

    def renew_lock(self, key, owner, ttl=None):
        return self.locks.get(key) == owner

    def release_lock(self, key, owner):
        return self.locks.pop(key, None) == owner

    def lpop(self, key):
        return self.queue.pop(0) if self.queue else None

    def rpush(self, key, value):
        self.queue.append(value)


@pytest.fixture()
def scheduler(tenant_id):
    redis = _FakeRedis()
    s = HongtaoIngestScheduler(redis=redis)
    s._service = _NoopService()
    return s


class _NoopService:
    def recover_stale_runs(self):
        return {"interrupted": 0}

    async def claim_and_run(self, tenant_id):
        return {"executed": False, "run_ids": [], "reason": "empty"}


def _create_source(tenant_id, enabled=True, interval=24, selection="all", selected=None):
    import json as _json

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
             _json.dumps(selected) if selected else None),
        )
        conn.commit()


def _runs_of(tenant_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, trigger_type, status, created_at FROM bs_content_sync_runs "
            "WHERE tenant_id = %s AND module = 'hongtao_shop' ORDER BY id",
            (tenant_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def test_parse_interval_hours():
    assert _parse_interval_hours(24) == 24
    assert _parse_interval_hours("12") == 12
    assert _parse_interval_hours(None) == DEFAULT_INTERVAL_HOURS
    assert _parse_interval_hours("bad") == DEFAULT_INTERVAL_HOURS
    assert _parse_interval_hours(0) == DEFAULT_INTERVAL_HOURS
    assert _parse_interval_hours(-5) == DEFAULT_INTERVAL_HOURS
    assert _parse_interval_hours(float("nan")) == DEFAULT_INTERVAL_HOURS
    assert _parse_interval_hours(10 ** 9) == 24 * 365  # 超大封顶


async def test_scheduler_start_refuses_when_redis_down(scheduler):
    scheduler._redis.available = False
    assert await scheduler.start() is False


async def test_scheduler_start_single_copy(scheduler):
    assert await scheduler.start() is True
    # 第二个实例同锁 → 空转返回 False（单副本）
    other = HongtaoIngestScheduler(redis=scheduler._redis)
    assert await other.start() is False
    await scheduler.stop()


def test_tick_enqueues_due_source(tenant_id, scheduler):
    """到期源（无任何 run）→ 建 scheduled queued run。"""
    _create_source(tenant_id, interval=24)
    result = scheduler._run_tick()
    assert result["scheduled_enqueued"] == 1
    runs = _runs_of(tenant_id)
    assert len(runs) == 1 and runs[0]["trigger_type"] == "scheduled" and runs[0]["status"] == "queued"


def test_tick_no_duplicate_when_active_run(tenant_id, scheduler):
    """防堆积：已有 queued/running run 的租户跳过。"""
    _create_source(tenant_id)
    assert scheduler._run_tick()["scheduled_enqueued"] == 1
    assert scheduler._run_tick()["scheduled_enqueued"] == 0  # queued 在队不再建
    assert len(_runs_of(tenant_id)) == 1


def test_tick_skips_fresh_and_disabled(tenant_id, scheduler):
    """未到期（最近 run 在间隔内）与 disabled 源都不生成。"""
    from src.db.database import get_db_connection

    _create_source(tenant_id, interval=24)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 手动 run 一小时前 → 新鲜度计入，24h 内不再定时
        cursor.execute(
            "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status, created_at) "
            "VALUES (%s, 'hongtao_shop', 'manual', 'success', now() - interval '1 hour')",
            (tenant_id,),
        )
        conn.commit()
    assert scheduler._run_tick()["scheduled_enqueued"] == 0

    # 25 小时前的 run → 到期生成
    from src.db.database import get_db_connection as _gdb

    with _gdb() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_content_sync_runs SET created_at = now() - interval '25 hours' "
            "WHERE tenant_id = %s", (tenant_id,),
        )
        conn.commit()
    assert scheduler._run_tick()["scheduled_enqueued"] == 1

    # disabled 后不再生成（清掉活跃 run 模拟）
    _create_source(tenant_id, enabled=False)
    from src.db.database import get_db_connection as _gdb2

    with _gdb2() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_content_sync_runs SET status = 'success', "
            "created_at = now() - interval '25 hours' WHERE tenant_id = %s", (tenant_id,),
        )
        conn.commit()
    assert scheduler._run_tick()["scheduled_enqueued"] == 0


def test_recover_stale_runs_negative(tenant_id):
    """负向：新鲜 running / 终态 run 均不回收。"""
    from src.db.database import get_db_connection
    from src.tenant_custom.hongtao_shop.service import HongtaoShopSyncService

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 新鲜 running（heartbeat 100s 前）与终态 success 各一条
        cursor.execute(
            "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status, heartbeat_at) "
            "VALUES (%s, 'hongtao_shop', 'manual', 'running', now() - interval '100 seconds')",
            (tenant_id,),
        )
        cursor.execute(
            "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status) "
            "VALUES (%s, 'hongtao_shop', 'manual', 'success')", (tenant_id,),
        )
        conn.commit()
    result = HongtaoShopSyncService().recover_stale_runs()
    assert result["interrupted"] == 0
    statuses = [r["status"] for r in _runs_of(tenant_id)]
    assert statuses == ["running", "success"]


def test_recover_stale_runs(tenant_id):
    """heartbeat 超 3600s（单闸决议阈值）的 running → interrupted；items 同步终态化。"""
    from src.db.database import get_db_connection
    from src.tenant_custom.hongtao_shop.service import HongtaoShopSyncService

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_content_sync_runs
                (tenant_id, module, trigger_type, status, owner_token, heartbeat_at)
            VALUES (%s, 'hongtao_shop', 'manual', 'running', 'dead-owner',
                    now() - interval '2 hours')
            RETURNING id
            """,
            (tenant_id,),
        )
        run_id = cursor.fetchone()["id"]
        cursor.execute(
            "INSERT INTO bs_content_sync_items (tenant_id, run_id, module, native_id, status) "
            "VALUES (%s, %s, 'hongtao_shop', '1', 'running')",
            (tenant_id, run_id),
        )
        conn.commit()

    service = HongtaoShopSyncService()
    result = service.recover_stale_runs()
    assert result["interrupted"] == 1
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT status FROM bs_content_sync_runs WHERE id = %s", (run_id,)
        )
        assert cursor.fetchone()["status"] == "interrupted"
        cursor.execute(
            "SELECT status FROM bs_content_sync_items WHERE run_id = %s", (run_id,)
        )
        assert cursor.fetchone()["status"] == "interrupted"


async def test_claim_and_run_executes_queued(tenant_id, monkeypatch):
    """claim_and_run 领取 queued run 并执行（_execute_run 打桩记录）。"""
    from src.db.database import get_db_connection
    from src.tenant_custom.hongtao_shop.service import HongtaoShopSyncService

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bs_content_sync_runs (tenant_id, module, trigger_type, status) "
            "VALUES (%s, 'hongtao_shop', 'manual', 'queued')", (tenant_id,),
        )
        conn.commit()

    service = HongtaoShopSyncService()
    executed = []

    async def _fake_execute(tenant_id_, run, owner_token, limit=None):
        executed.append(run["id"])
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bs_content_sync_runs SET status = 'success', completed_at = now() "
                "WHERE id = %s", (run["id"],),
            )
            conn.commit()
        return {"run_id": run["id"], "status": "success"}

    monkeypatch.setattr(service, "_execute_run", _fake_execute)
    result = await service.claim_and_run(tenant_id)
    assert result["executed"] is True and len(executed) == 1
    # 队列排空后再领取：无事可做
    result2 = await service.claim_and_run(tenant_id)
    assert result2["executed"] is False and result2["reason"] == "empty"
