"""quota 测试（R9：并发预留不超限 / 固定 scope 顺序死锁规避 / 落账-释放 / 窗口对齐）"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from src.db.database import get_db_connection
from src.desktop_automation import quota
from src.desktop_automation.constants import QUOTA_SCOPE_ORDER

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 8, 12, 3, 21, tzinfo=timezone.utc)


def _scope(scope_type, scope_id, limit, window=3600):
    return quota.QuotaScope(
        scope_type=scope_type, scope_id=scope_id, limit_count=limit, window_seconds=window
    )


def _reserve(tenant_id, scopes, now=NOW):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            reservations = quota.reserve_quota(cursor, tenant_id, scopes, now)
            conn.commit()
            return reservations, None
        except quota.QuotaExhausted as e:
            conn.rollback()
            return None, e
        except Exception:
            conn.rollback()
            raise


class TestWindowAlignment:
    def test_bucket_start_floor_aligned(self):
        at = datetime(2026, 9, 8, 12, 3, 21, tzinfo=timezone.utc)
        assert quota.bucket_start_for(3600, at) == datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
        assert quota.bucket_start_for(60, at) == datetime(2026, 9, 8, 12, 3, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            quota.bucket_start_for(0, at)


class TestSortOrder:
    def test_fixed_scope_order(self):
        """R9 固定顺序：tenant < task < target < account < resource（显式列表，非字典序）"""
        assert QUOTA_SCOPE_ORDER == ("tenant", "task", "target", "account", "resource")
        shuffled = [
            _scope("resource", "r1", 5), _scope("tenant", "t1", 5),
            _scope("account", "a1", 5), _scope("task", "k1", 5), _scope("target", "g1", 5),
        ]
        ordered = [s.scope_type for s in quota.sort_scopes(shuffled)]
        assert ordered == ["tenant", "task", "target", "account", "resource"]
        # scope_id 次级升序
        ids = [s.scope_id for s in quota.sort_scopes([_scope("task", "b", 1), _scope("task", "a", 1)])]
        assert ids == ["a", "b"]


class TestReserveSettleRelease:
    def test_reserve_then_settle_moves_reserved_to_used(self, tenant_id):
        scope = _scope("tenant", tenant_id, 5)
        reservations, err = _reserve(tenant_id, [scope])
        assert err is None and len(reservations) == 1
        with get_db_connection() as conn:
            cursor = conn.cursor()
            quota.settle_quota(cursor, tenant_id, [r.as_dict() for r in reservations])
            conn.commit()
        bucket = quota.get_bucket(tenant_id, "tenant", tenant_id, quota.bucket_start_for(3600, NOW))
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 1

    def test_release_decrements_reserved(self, tenant_id):
        scope = _scope("task", "task-x", 3)
        reservations, _ = _reserve(tenant_id, [scope])
        with get_db_connection() as conn:
            cursor = conn.cursor()
            quota.release_quota(cursor, tenant_id, [r.as_dict() for r in reservations])
            conn.commit()
        bucket = quota.get_bucket(tenant_id, "task", "task-x", quota.bucket_start_for(3600, NOW))
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 0

    def test_insufficient_scope_rolls_back_all(self, tenant_id):
        """任一层不足整体回滚：前层不留半预留"""
        ok_scope = _scope("tenant", tenant_id, 5)
        full_scope = _scope("task", "task-full", 1)
        _reserve(tenant_id, [full_scope])  # 占满
        reservations, err = _reserve(tenant_id, [ok_scope, full_scope])
        assert reservations is None
        assert err is not None and err.scope.scope_type == "task"
        bucket = quota.get_bucket(tenant_id, "tenant", tenant_id, quota.bucket_start_for(3600, NOW))
        assert bucket is None or bucket["reserved_count"] == 0  # 无半预留

    def test_reserve_rejects_at_limit(self, tenant_id):
        scope = _scope("target", "tgt-1", 2)
        assert _reserve(tenant_id, [scope])[1] is None
        assert _reserve(tenant_id, [scope])[1] is None
        _, err = _reserve(tenant_id, [scope])
        assert err is not None
        bucket = quota.get_bucket(tenant_id, "target", "tgt-1", quota.bucket_start_for(3600, NOW))
        assert bucket["reserved_count"] == 2  # 恰好到限，不超


class TestUsedCountsTowardLimit:
    """R19：额度判定为「已使用＋未决预留」——settle 后同窗口继续占用"""

    def test_settle_then_reserve_same_window_rejected(self, tenant_id):
        """limit=1：第一次预留+落账（used=1）后，同窗口第二次预留被拒"""
        scope = _scope("tenant", tenant_id, 1)
        reservations, err = _reserve(tenant_id, [scope])
        assert err is None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            quota.settle_quota(cursor, tenant_id, [r.as_dict() for r in reservations])
            conn.commit()
        bucket = quota.get_bucket(tenant_id, "tenant", tenant_id, quota.bucket_start_for(3600, NOW))
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 1
        _, err2 = _reserve(tenant_id, [scope])
        assert err2 is not None  # reserved(0) + used(1) < limit(1) 不成立 → 拒绝

    def test_concurrent_reserve_limit_one_exactly_one_succeeds(self, tenant_id):
        """并发多线程申请 limit=1：恰好 1 成功"""
        scope = _scope("tenant", tenant_id, 1)

        def attempt(i):
            return _reserve(tenant_id, [scope])

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        succeeded = [r for r, e in results if e is None]
        failed = [e for r, e in results if e is not None]
        assert len(succeeded) == 1 and len(failed) == 7
        bucket = quota.get_bucket(tenant_id, "tenant", tenant_id, quota.bucket_start_for(3600, NOW))
        assert bucket["reserved_count"] == 1 and bucket["used_count"] == 0


class TestConcurrency:
    def test_concurrent_reserve_never_exceeds_limit(self, tenant_id):
        """8 线程并发预留 limit=5：恰好 5 成功 3 拒绝"""
        scope = _scope("tenant", tenant_id, 5)

        def attempt(i):
            return _reserve(tenant_id, [scope])

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        succeeded = [r for r, e in results if e is None]
        failed = [e for r, e in results if e is not None]
        assert len(succeeded) == 5 and len(failed) == 3
        bucket = quota.get_bucket(tenant_id, "tenant", tenant_id, quota.bucket_start_for(3600, NOW))
        assert bucket["reserved_count"] == 5

    def test_fixed_scope_order_avoids_deadlock(self, tenant_id):
        """两个线程以相反 scope 顺序并发预留同两层：内部固定排序串行化，无死锁完成"""
        scopes_a = [_scope("tenant", tenant_id, 50), _scope("task", "task-d", 50)]
        scopes_b = [_scope("task", "task-d", 50), _scope("tenant", tenant_id, 50)]
        outcomes = []

        def worker(scopes):
            outcomes.append(_reserve(tenant_id, scopes)[1] is None)

        threads = [
            threading.Thread(target=worker, args=(scopes_a,)),
            threading.Thread(target=worker, args=(scopes_b,)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)  # 死锁时 join 超时，线程仍存活 → 断言失败
        assert not any(t.is_alive() for t in threads), "固定 scope 顺序下不应死锁"
        assert outcomes == [True, True]
        for scope_type, scope_id in (("tenant", tenant_id), ("task", "task-d")):
            bucket = quota.get_bucket(tenant_id, scope_type, scope_id, quota.bucket_start_for(3600, NOW))
            assert bucket["reserved_count"] == 2
