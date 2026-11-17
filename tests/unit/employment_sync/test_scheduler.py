"""lyjy_employment / sccy_policy P2 调度器测试：到期生成/防堆积/单副本锁/
余额预检（skipped_no_credit）/每日对账窗口判定。照 hongtao_shop/test_scheduler.py 范式。

tick 走真实 service.trigger_sync（与定时/CLI/后台同一受理入口），余额预检依赖
tenants 行：有行 → queued；无行 → skipped_no_credit 终态（两条路径都有断言）。
"""

import asyncio
from datetime import datetime, timedelta

import pytest

import src.tenant_custom.lyjy_employment.scheduler as lyjy_sched_mod
from src.tenant_custom.lyjy_employment.scheduler import (
    RECONCILE_WINDOW_HOURS,
    LyjyEmploymentScheduler,
)
from src.tenant_custom.sccy_policy.scheduler import SccyPolicyScheduler


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


def _make_scheduler(cls, redis):
    s = cls(redis=redis)
    # tick 用真实 service（trigger_sync 与定时/手动同入口）；claim 路径不在本文件覆盖
    if cls is LyjyEmploymentScheduler:
        from src.tenant_custom.lyjy_employment.service import lyjy_employment_service

        s._service = lyjy_employment_service
    else:
        from src.tenant_custom.sccy_policy.service import sccy_policy_service

        s._service = sccy_policy_service
    return s


@pytest.fixture()
def scheduler(tenant_id):
    return _make_scheduler(LyjyEmploymentScheduler, _FakeRedis())


@pytest.fixture()
def sccy_scheduler(tenant_id):
    return _make_scheduler(SccyPolicyScheduler, _FakeRedis())


@pytest.fixture()
def tenant_row(tenant_id, require_db):
    """余额预检通过所需的 tenants 行（须带正余额；测后由 conftest cleanup 删除）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO tenants (tenant_id, company_name, credit_balance)
            VALUES (%s, '调度器测试租户', 10000)
            ON CONFLICT (tenant_id) DO UPDATE
            SET credit_balance = EXCLUDED.credit_balance
            """,
            (tenant_id,),
        )
        conn.commit()
    yield tenant_id


def _create_source(tenant_id, module, enabled=True, interval=24):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO bs_content_sync_sources
                (tenant_id, module, enabled, sync_interval_hours, selection_mode)
            VALUES (%s, %s, %s, %s, 'all')
            ON CONFLICT (tenant_id, module) DO UPDATE
            SET enabled = EXCLUDED.enabled,
                sync_interval_hours = EXCLUDED.sync_interval_hours
            """,
            (tenant_id, module, enabled, interval),
        )
        conn.commit()


def _runs_of(tenant_id, module):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, trigger_type, status, created_at FROM bs_content_sync_runs "
            "WHERE tenant_id = %s AND module = %s ORDER BY id",
            (tenant_id, module),
        )
        return [dict(r) for r in cursor.fetchall()]


def _insert_run(tenant_id, module, trigger_type, status, created_at_sql):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bs_content_sync_runs "
            "(tenant_id, module, trigger_type, status, created_at) "
            f"VALUES (%s, %s, %s, %s, {created_at_sql})",
            (tenant_id, module, trigger_type, status),
        )
        conn.commit()


# ==================== 单副本锁语义（照宏陶回归） ====================


async def test_scheduler_start_refuses_when_redis_down(scheduler):
    scheduler._redis.available = False
    assert await scheduler.start() is False
    assert scheduler._task is None


async def test_scheduler_start_single_copy(scheduler, sccy_scheduler):
    """两模块各自独立锁：同模块锁被占仍启动驱动任务（幽灵锁自愈）；异模块互不干扰。"""
    assert await scheduler.start() is True
    other = LyjyEmploymentScheduler(redis=scheduler._redis)
    from src.tenant_custom.lyjy_employment.service import lyjy_employment_service

    other._service = lyjy_employment_service
    assert await other.start() is True
    assert other._lock_key == scheduler._lock_key  # 同一把模块锁
    # 异模块锁 key 不同，互不干扰
    assert await sccy_scheduler.start() is True
    assert sccy_scheduler._lock_key != scheduler._lock_key
    await scheduler.stop()
    await other.stop()
    await sccy_scheduler.stop()


# ==================== tick：interval 到期生成 ====================


def test_tick_enqueues_due_source(tenant_id, tenant_row, scheduler):
    _create_source(tenant_id, "lyjy_employment", interval=24)
    result = scheduler._run_tick()
    assert result["scheduled_enqueued"] == 1
    runs = _runs_of(tenant_id, "lyjy_employment")
    assert len(runs) == 1
    assert runs[0]["trigger_type"] == "scheduled" and runs[0]["status"] == "queued"


def test_tick_no_duplicate_when_active_run(tenant_id, tenant_row, scheduler):
    """防堆积：已有 queued run 的租户跳过（在队去重 + tick 内 active 判定）。"""
    _create_source(tenant_id, "lyjy_employment")
    assert scheduler._run_tick()["scheduled_enqueued"] == 1
    assert scheduler._run_tick()["scheduled_enqueued"] == 0
    assert len(_runs_of(tenant_id, "lyjy_employment")) == 1


def test_tick_skips_fresh_and_disabled(tenant_id, tenant_row, scheduler):
    """未到期（最近 run 在间隔内）与 disabled 源都不生成。"""
    _create_source(tenant_id, "lyjy_employment", interval=24)
    _insert_run(tenant_id, "lyjy_employment", "manual", "success", "now() - interval '1 hour'")
    assert scheduler._run_tick()["scheduled_enqueued"] == 0

    # 25 小时前的 run → 到期生成
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_content_sync_runs SET created_at = now() - interval '25 hours' "
            "WHERE tenant_id = %s",
            (tenant_id,),
        )
        conn.commit()
    assert scheduler._run_tick()["scheduled_enqueued"] == 1

    # disabled 后不再生成（清掉活跃 run 模拟）
    _create_source(tenant_id, "lyjy_employment", enabled=False)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_content_sync_runs SET status = 'success', "
            "created_at = now() - interval '25 hours' WHERE tenant_id = %s",
            (tenant_id,),
        )
        conn.commit()
    assert scheduler._run_tick()["scheduled_enqueued"] == 0


def test_tick_credit_precheck_skipped_no_credit(tenant_id, scheduler):
    """余额预检：无 tenants 行（余额检查不通过）→ skipped_no_credit 终态入账本，
    不建 queued（与手动 trigger_sync 同一入口同一语义）。"""
    _create_source(tenant_id, "lyjy_employment")
    assert scheduler._run_tick()["scheduled_enqueued"] == 1
    runs = _runs_of(tenant_id, "lyjy_employment")
    assert len(runs) == 1
    assert runs[0]["status"] == "skipped_no_credit"


# ==================== tick：每日对账窗口判定（lyjy） ====================


def test_reconcile_due_pure_judgement(scheduler):
    """窗口外不到期；窗口内无对账史到期；窗口内距上次 <24h 不到期。"""
    db_now = datetime(2026, 11, 17, 3, 30)  # 窗口 [1,6) 内
    assert scheduler._reconcile_due(db_now, None) is True
    assert scheduler._reconcile_due(db_now, db_now - timedelta(hours=23)) is False
    assert scheduler._reconcile_due(db_now, db_now - timedelta(hours=25)) is True
    noon = datetime(2026, 11, 17, 12, 0)  # 窗口外
    assert scheduler._reconcile_due(noon, None) is False


def test_tick_reconcile_window_gate(tenant_id, tenant_row, scheduler, monkeypatch):
    """对账 run 只在低峰窗口 + 距上次 ≥24h 时生成，且优先于 interval run；
    生成后 24h 内不重复生成。"""
    # 窗口放宽为全天（不依赖测试运行时刻），隔离窗口判定本身
    monkeypatch.setattr(lyjy_sched_mod, "RECONCILE_WINDOW_HOURS", (0, 24))
    _create_source(tenant_id, "lyjy_employment", interval=24)
    # 无任何 run：reconcile 与 interval 同时到期 → reconcile 优先
    assert scheduler._run_tick()["scheduled_enqueued"] == 1
    runs = _runs_of(tenant_id, "lyjy_employment")
    assert runs[0]["trigger_type"] == "reconcile"

    # 刚生成过对账 run → 24h 内不再生成（新不到期 run）
    assert scheduler._run_tick()["scheduled_enqueued"] == 0

    # 对账 run 25h 前（置终态解除在队占位）→ 再到期
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_content_sync_runs SET status = 'success', "
            "created_at = now() - interval '25 hours' "
            "WHERE tenant_id = %s AND trigger_type = 'reconcile'",
            (tenant_id,),
        )
        conn.commit()
    assert scheduler._run_tick()["scheduled_enqueued"] == 1
    assert _runs_of(tenant_id, "lyjy_employment")[-1]["trigger_type"] == "reconcile"


def test_tick_reconcile_blocked_outside_window(tenant_id, tenant_row, scheduler, monkeypatch):
    """窗口关死（start==end）时对账永不生成；interval 未到期 → tick 零生成。"""
    monkeypatch.setattr(lyjy_sched_mod, "RECONCILE_WINDOW_HOURS", (0, 0))
    _create_source(tenant_id, "lyjy_employment", interval=24)
    _insert_run(tenant_id, "lyjy_employment", "scheduled", "success", "now()")
    assert scheduler._run_tick()["scheduled_enqueued"] == 0


def test_sccy_tick_has_no_reconcile(tenant_id, tenant_row, sccy_scheduler, monkeypatch):
    """sccy 无对账类型（每个 run 即全量）：对账史到期也只生成 scheduled。"""
    monkeypatch.setattr(lyjy_sched_mod, "RECONCILE_WINDOW_HOURS", (0, 24))
    _create_source(tenant_id, "sccy_policy", interval=24)
    _insert_run(tenant_id, "sccy_policy", "reconcile", "success", "now() - interval '25 hours'")
    assert sccy_scheduler._run_tick()["scheduled_enqueued"] == 1
    runs = _runs_of(tenant_id, "sccy_policy")
    assert runs[-1]["trigger_type"] == "scheduled"


def test_sccy_and_lyjy_ticks_isolated(tenant_id, tenant_row, scheduler, sccy_scheduler):
    """两模块互不影响：同租户两源各自独立到期生成。"""
    _create_source(tenant_id, "lyjy_employment", interval=24)
    _create_source(tenant_id, "sccy_policy", interval=24)
    assert scheduler._run_tick()["scheduled_enqueued"] == 1
    assert sccy_scheduler._run_tick()["scheduled_enqueued"] == 1
    assert len(_runs_of(tenant_id, "lyjy_employment")) == 1
    assert len(_runs_of(tenant_id, "sccy_policy")) == 1


# ==================== 常量护栏 ====================


def test_reconcile_window_constant_shape():
    start, end = RECONCILE_WINDOW_HOURS
    assert 0 <= start < end <= 24
