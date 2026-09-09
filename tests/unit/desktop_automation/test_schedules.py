"""schedules 时间计算测试（interval anchor+n×interval / cron 月末/闰年/DST/周几 / miss 策略入口）"""

from datetime import datetime, timedelta, timezone

import pytest

from src.desktop_automation import schedules

pytestmark = pytest.mark.unit

UTC = timezone.utc


def dt(*args):
    return datetime(*args, tzinfo=UTC)


class TestIntervalSlots:
    def test_next_slot_on_or_after(self):
        anchor = dt(2026, 9, 8, 10, 0, 0)
        # at < anchor → anchor 本身
        assert schedules.next_slot_on_or_after(anchor, 3600, dt(2026, 9, 8, 9, 0)) == anchor
        # at 恰为网格点 → 该点
        assert schedules.next_slot_on_or_after(anchor, 3600, dt(2026, 9, 8, 12, 0)) == dt(2026, 9, 8, 12, 0)
        # 非对齐 → 上取整
        assert schedules.next_slot_on_or_after(anchor, 3600, dt(2026, 9, 8, 12, 30)) == dt(2026, 9, 8, 13, 0)

    def test_nth_slot_is_anchor_plus_n_interval(self):
        """interval 第 n 次为 anchor + n×interval_seconds（【计划 §3.1】）"""
        anchor = dt(2026, 9, 8, 10, 0, 0)
        for n in (1, 5, 48):
            expect = anchor + timedelta(seconds=n * 1800)
            assert schedules.next_slot_on_or_after(
                anchor, 1800, anchor + timedelta(seconds=1)
            ) == anchor + timedelta(seconds=1800)
            got = schedules.latest_slot_at_or_before(anchor, 1800, expect + timedelta(seconds=1))
            assert got == expect

    def test_next_slot_strictly_after(self):
        anchor = dt(2026, 9, 8, 10, 0, 0)
        assert schedules.next_slot_strictly_after(anchor, 1800, anchor) == anchor + timedelta(seconds=1800)
        slot = anchor + timedelta(hours=3)
        assert schedules.next_slot_strictly_after(anchor, 1800, slot) == slot + timedelta(seconds=1800)

    def test_latest_slot_at_or_before_before_anchor_is_none(self):
        anchor = dt(2026, 9, 8, 10, 0, 0)
        assert schedules.latest_slot_at_or_before(anchor, 3600, dt(2026, 9, 8, 9, 59)) is None

    def test_slots_between_counts_missed(self):
        anchor = dt(2026, 9, 8, 10, 0, 0)
        first = anchor
        last = anchor + timedelta(hours=2)
        assert schedules.slots_between(anchor, 3600, first, last) == 2  # [first, last)
        assert schedules.slots_between(anchor, 3600, first, first) == 0

    def test_naive_treated_as_utc(self):
        naive = datetime(2026, 9, 8, 10, 0, 0)
        assert schedules.ensure_utc(naive) == dt(2026, 9, 8, 10, 0, 0)


class TestCron:
    def test_month_end_jan31_skips_feb(self):
        """每月 31 日：1/31 后下一槽为 3/31（2 月无 31 日）"""
        spec = {"cron_expr": "0 0 31 * *"}
        nxt = schedules.next_cron_fire(spec, dt(2026, 1, 31, 0, 0), strictly_after=True)
        assert nxt == dt(2026, 3, 31, 0, 0)

    def test_leap_year_feb29(self):
        """闰年 2/29 存在，平年跳过"""
        spec = {"cron_expr": "0 0 29 2 *"}
        nxt = schedules.next_cron_fire(spec, dt(2023, 6, 1))
        assert nxt == dt(2024, 2, 29, 0, 0)
        nxt2 = schedules.next_cron_fire(spec, dt(2024, 3, 1))
        assert nxt2 == dt(2028, 2, 29, 0, 0)

    def test_dst_spring_forward_keeps_local_time(self):
        """DST 春令时（Europe/Berlin 2026-03-29 02:00→03:00）：02:30 本地不存在，
        APScheduler 顺延到可表达时刻（03:30 本地 = 01:30 UTC）"""
        spec = {"cron_expr": "30 2 * * *", "timezone": "Europe/Berlin"}
        # 从 3/28 的 02:30（CET，=01:30 UTC）之后起算：下一槽落在切换日 3/29
        nxt = schedules.next_cron_fire(spec, dt(2026, 3, 28, 1, 31))
        # 2026-03-29 02:30 CET 不存在 → 03:30 CEST（UTC+2）= 01:30 UTC
        assert nxt is not None
        assert nxt.astimezone(UTC) == dt(2026, 3, 29, 1, 30)

    def test_day_of_week_mon_sun_strings(self):
        """星期用 mon..sun 字符串：周一 cron 从周五起下一次是下周一"""
        spec = {"day_of_week": "mon", "hour": 9, "minute": 0, "timezone": "UTC"}
        friday = dt(2026, 9, 4, 10, 0)  # 周五
        nxt = schedules.next_cron_fire(spec, friday)
        assert nxt == dt(2026, 9, 7, 9, 0)  # 下周一
        spec2 = {"day_of_week": "sun,mon", "hour": 9, "minute": 0, "timezone": "UTC"}
        nxt2 = schedules.next_cron_fire(spec2, friday)
        assert nxt2 == dt(2026, 9, 6, 9, 0)  # 周日先于周一

    def test_initial_next_fire_at_cron(self):
        spec = {"cron_expr": "0 9 * * *", "timezone": "UTC"}
        nxt = schedules.initial_next_fire_at(spec)
        assert nxt is not None and nxt.hour == 9 and nxt.minute == 0

    def test_initial_next_fire_interval_and_oneshot(self):
        anchor = dt(2026, 9, 8, 10, 0)
        assert schedules.initial_next_fire_at({"interval_seconds": 600, "anchor_at": anchor}) == anchor
        assert schedules.initial_next_fire_at({"anchor_at": anchor}) == anchor  # 一次性
        assert schedules.initial_next_fire_at({"kind": "event", "source_ref": "s"}) is None

    def test_cron_respects_ends_at(self):
        spec = {"cron_expr": "0 9 * * *", "ends_at": dt(2026, 9, 8, 23, 59)}
        assert schedules.next_cron_fire(spec, dt(2026, 9, 8, 8, 0)) == dt(2026, 9, 8, 9, 0)
        assert schedules.next_cron_fire(spec, dt(2026, 9, 8, 10, 0)) is None


class TestTriggerKeyDefaults:
    def test_default_trigger_key(self):
        assert schedules.default_trigger_key("time", {}) == "time"
        assert schedules.default_trigger_key("event", {"source_ref": "s", "event_type": "t"}) == "event:s:t"
        assert schedules.default_trigger_key("event", {"source_ref": "s"}) == "event:s:*"
        with pytest.raises(ValueError):
            schedules.default_trigger_key("event", {})
        with pytest.raises(ValueError):
            schedules.default_trigger_key("other", {})
