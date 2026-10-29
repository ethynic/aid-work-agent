"""调度推进计算纯函数测试（schedule.py，设计 §4.2）"""

from datetime import datetime

import pytest

from src.scheduler.schedule import ONCE_EXPIRY_GRACE, compute_next_due, once_expired


def _task(schedule_type, *, cron=None, interval=None, next_due=None, run_at=None):
    return {
        "schedule_type": schedule_type,
        "cron_expression": cron,
        "interval_seconds": interval,
        "next_due_at": next_due,
        "run_at": run_at,
    }


# ==================== cron：停机合并（从 now 推进） ====================


class TestCronAdvance:
    def test_next_after_now_when_today_slot_missed(self):
        """当天 09:00 已过（now=10:30）：推进到明天 09:00，不逐 tick 追赶"""
        task = _task("daily", cron="0 9 * * *")
        assert compute_next_due(task, datetime(2026, 10, 28, 10, 30)) == datetime(2026, 10, 29, 9, 0)

    def test_today_slot_when_now_before_fire(self):
        task = _task("daily", cron="0 9 * * *")
        assert compute_next_due(task, datetime(2026, 10, 28, 8, 0)) == datetime(2026, 10, 28, 9, 0)

    def test_long_downtime_coalesces_to_single_catchup(self):
        """停机 2 小时的每小时任务：醒来只执行错过的一次，节奏按 now 之后继续"""
        task = _task("interval_cron", cron="0 * * * *")
        assert compute_next_due(task, datetime(2026, 10, 28, 12, 5)) == datetime(2026, 10, 28, 13, 0)

    def test_exact_fire_boundary_advances_strictly(self):
        """回归（验收复核 P2）：now 恰好等于 cron 触发时刻 → 推进必须严格大于
        now（明天 09:00），否则 next_due_at 仍到期引发额外认领/幂等重提"""
        task = _task("daily", cron="0 9 * * *")
        assert compute_next_due(task, datetime(2026, 10, 28, 9, 0, 0)) == datetime(2026, 10, 29, 9, 0)

    def test_invalid_cron_raises(self):
        with pytest.raises(ValueError):
            compute_next_due(_task("daily", cron="not a cron"),
                             datetime(2026, 10, 28, 10, 0))


# ==================== interval：相位锚定不漂移 ====================


class TestIntervalAdvance:
    def test_anchor_plus_smallest_future_step(self):
        task = _task("interval", interval=3600, next_due=datetime(2026, 10, 28, 9, 0))
        # 锚点 09:00 + 3600s 序列中大于 now(10:30) 的最小值 = 11:00
        assert compute_next_due(task, datetime(2026, 10, 28, 10, 30)) == datetime(2026, 10, 28, 11, 0)

    def test_phase_preserved_across_long_gap(self):
        """错过多个周期：跳到最小的未来锚点，相位不变"""
        task = _task("interval", interval=3600, next_due=datetime(2026, 10, 28, 9, 0))
        assert compute_next_due(task, datetime(2026, 10, 28, 14, 30)) == datetime(2026, 10, 28, 15, 0)

    def test_nonpositive_interval_raises(self):
        with pytest.raises(ValueError):
            compute_next_due(_task("interval", interval=0, next_due=datetime(2026, 10, 28, 9, 0)),
                             datetime(2026, 10, 28, 10, 0))


# ==================== once ====================


class TestOnce:
    def test_once_has_no_next(self):
        assert compute_next_due(_task("once"), datetime(2026, 10, 28, 10, 0)) is None

    def test_once_expired_within_grace_false(self):
        task = _task("once", run_at=datetime(2026, 10, 28, 10, 25),
                     next_due=datetime(2026, 10, 28, 10, 25))
        assert once_expired(task, datetime(2026, 10, 28, 10, 30)) is False

    def test_once_expired_past_grace_true(self):
        task = _task("once", run_at=datetime(2026, 10, 28, 10, 25),
                     next_due=datetime(2026, 10, 28, 10, 25))
        past = datetime(2026, 10, 28, 10, 25) + ONCE_EXPIRY_GRACE + __import__("datetime").timedelta(seconds=1)
        assert once_expired(task, past) is True

    def test_non_once_never_expired(self):
        task = _task("interval", interval=3600, next_due=datetime(2020, 1, 1))
        assert once_expired(task, datetime(2026, 10, 28, 10, 0)) is False
