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


# ==================== 时间窗 / 整数小时间隔 / 租户上限（设计 §15，2026-11-03） ====================

import json as _json
from unittest.mock import patch

from src.scheduler.schedule import (
    MIN_INTERVAL_HOURS_FLOOR,
    ScheduleConfigError,
    build_schedule_fields,
    compute_resume_due,
    max_tasks_per_user,
    min_interval_hours,
)


def _window_task(schedule_type, *, cron=None, interval=None, next_due=None,
                 start_at=None, ends_at=None):
    return {
        "schedule_type": schedule_type,
        "cron_expression": cron,
        "interval_seconds": interval,
        "next_due_at": next_due,
        "run_at": None,
        "start_at": start_at,
        "ends_at": ends_at,
    }


class TestConfigThresholds:
    def test_min_interval_hours_floor_on_invalid_config(self):
        """配置只接受 >=1 的整数：0.5/0/布尔/字符串一律回落硬下限 1（§15.2.5）"""
        for bad in (0.5, 0, -1, True, "2"):
            with patch("src.config.settings.settings") as mock_settings:
                mock_settings.scheduler.min_interval_hours = bad
                assert min_interval_hours() == MIN_INTERVAL_HOURS_FLOOR
        with patch("src.config.settings.settings") as mock_settings:
            mock_settings.scheduler.min_interval_hours = 2
            assert min_interval_hours() == 2

    @pytest.mark.parametrize("settings_json,expected", [
        (_json.dumps({"scheduler": {"max_tasks_per_user": 8}}), 8),
        (_json.dumps({"scheduler": {"max_tasks_per_user": 0}}), 5),
        (_json.dumps({"scheduler": {"max_tasks_per_user": 2.5}}), 5),
        (_json.dumps({"scheduler": {"max_tasks_per_user": True}}), 5),
        (_json.dumps({"other": 1}), 5),
        (None, 5),
    ])
    def test_max_tasks_per_user_tenant_override(self, settings_json, expected):
        """租户覆盖值优先（只认正整数），无覆盖/非法值回落配置默认 5（§15.2.6）"""
        with patch("src.db.database.get_db_connection") as mock_conn:
            cursor = mock_conn.return_value.__enter__.return_value.cursor.return_value
            cursor.fetchone.return_value = {"settings": settings_json}
            assert max_tasks_per_user("tenant_1") == expected

    def test_max_tasks_per_user_reads_parsed_dict_row(self):
        """DAL 兼容已解析 dict 形态的 settings（真实驱动下为 JSON 字符串）"""
        with patch("src.db.database.get_db_connection") as mock_conn:
            cursor = mock_conn.return_value.__enter__.return_value.cursor.return_value
            cursor.fetchone.return_value = {"settings": {"scheduler": {"max_tasks_per_user": 3}}}
            assert max_tasks_per_user("tenant_1") == 3

    def test_max_tasks_per_user_db_error_falls_back_to_default(self):
        """覆盖值读取失败回落配置默认值，不中断创建/恢复主流程"""
        with patch("src.db.database.get_db_connection",
                   side_effect=RuntimeError("db down")):
            assert max_tasks_per_user("tenant_1") == 5

    def test_max_tasks_per_user_without_tenant_uses_config_default(self):
        assert max_tasks_per_user(None) == 5


class TestIntervalIntegerHours:
    """interval_hours 只接受整数小时（≥ 配置阈值且 ≥1 硬下限）——§15.2.5。

    拒绝用例避免的错误：0.5/1.5 小时曾可入库为 1800/5400 秒，分钟/秒精度
    绕过最短间隔；布尔 True 曾被当作 1。"""

    @pytest.mark.parametrize("value", [0.5, 1.5, 0, -1, True, False, "1.5", "abc",
                                       None, [1], {"h": 1}])
    def test_invalid_interval_hours_rejected(self, value):
        with pytest.raises(ScheduleConfigError):
            build_schedule_fields(
                "interval", {"interval_hours": value},
                now=datetime(2026, 11, 1, 8, 0, 0))

    @pytest.mark.parametrize("value,expected_hours", [
        (1, 1), (2, 2), ("2", 2), (2.0, 2), ("6", 6)])
    def test_integer_hours_accepted(self, value, expected_hours):
        _, interval_seconds, _, next_due, start_at, ends_at = build_schedule_fields(
            "interval", {"interval_hours": value},
            now=datetime(2026, 11, 1, 8, 0, 0))
        assert interval_seconds == expected_hours * 3600
        assert start_at is None and ends_at is None

    def test_raised_config_threshold_rejects_lower_integer(self):
        """配置调高最短间隔（如 2）后：1 小时被拒、2 小时可用；配置只能调高，
        不能把下限放宽到 1 小时以下。"""
        with patch("src.scheduler.schedule.min_interval_hours", return_value=2):
            with pytest.raises(ScheduleConfigError):
                build_schedule_fields(
                    "interval", {"interval_hours": 1},
                    now=datetime(2026, 11, 1, 8, 0, 0))
            _, interval_seconds, _, _, _, _ = build_schedule_fields(
                "interval", {"interval_hours": 2},
                now=datetime(2026, 11, 1, 8, 0, 0))
            assert interval_seconds == 7200


class TestIntervalFirstTrigger:
    def test_future_start_first_due_is_start(self):
        """§15.2.3：设未来 start_at 时，开始时刻就是首次计划触发点"""
        _, _, _, next_due, start_at, _ = build_schedule_fields(
            "interval", {"interval_hours": 2,
                         "start_at": "2026-11-02 09:30:00"},
            now=datetime(2026, 11, 1, 8, 0, 0))
        assert next_due == datetime(2026, 11, 2, 9, 30, 0)
        assert start_at == datetime(2026, 11, 2, 9, 30, 0)

    def test_no_start_first_due_is_now(self):
        """不设起点：创建即生效，当前时刻为首个计划点（此后每隔 N 小时）"""
        now = datetime(2026, 11, 1, 8, 0, 0)
        _, _, _, next_due, _, _ = build_schedule_fields(
            "interval", {"interval_hours": 3}, now=now)
        assert next_due == now

    def test_past_start_first_due_is_now(self):
        """start_at 早于当前时间：从当前时间首次触发，过去的点不补跑"""
        now = datetime(2026, 11, 1, 8, 0, 0)
        _, _, _, next_due, start_at, _ = build_schedule_fields(
            "interval", {"interval_hours": 3,
                         "start_at": "2026-10-30 00:00:00"}, now=now)
        assert next_due == now
        assert start_at == datetime(2026, 10, 30)

    def test_start_outside_window_rejected(self):
        """首个触发点（start_at）不在 ends_at 之前：窗口内没有可执行点，拒绝"""
        with pytest.raises(ScheduleConfigError):
            build_schedule_fields(
                "interval", {"interval_hours": 1,
                             "start_at": "2026-11-02 00:00:00",
                             "ends_at": "2026-11-01 12:00:00"},
                now=datetime(2026, 11, 1, 8, 0, 0))

    def test_whole_window_in_past_rejected(self):
        with pytest.raises(ScheduleConfigError):
            build_schedule_fields(
                "interval", {"interval_hours": 1,
                             "start_at": "2026-10-01 00:00:00",
                             "ends_at": "2026-10-02 00:00:00"},
                now=datetime(2026, 11, 1, 8, 0, 0))


class TestWindowValidation:
    def test_once_rejects_window_fields(self):
        """once 不接受窗口字段（run_at 自带单次语义，原 10 分钟宽限不变）"""
        with pytest.raises(ScheduleConfigError):
            build_schedule_fields(
                "once", {"run_at": "2026-11-05 09:00:00",
                         "ends_at": "2026-11-06 00:00:00"},
                now=datetime(2026, 11, 1, 8, 0, 0))

    def test_start_must_be_before_ends(self):
        with pytest.raises(ScheduleConfigError):
            build_schedule_fields(
                "daily", {"hour": 9, "minute": 0,
                          "start_at": "2026-12-01 00:00:00",
                          "ends_at": "2026-11-01 00:00:00"},
                now=datetime(2026, 11, 1, 8, 0, 0))

    def test_equal_bounds_rejected(self):
        with pytest.raises(ScheduleConfigError):
            build_schedule_fields(
                "daily", {"hour": 9, "minute": 0,
                          "start_at": "2026-11-01 00:00:00",
                          "ends_at": "2026-11-01 00:00:00"},
                now=datetime(2026, 10, 1, 8, 0, 0))

    def test_unparseable_bound_rejected(self):
        with pytest.raises(ScheduleConfigError):
            build_schedule_fields(
                "daily", {"hour": 9, "minute": 0, "start_at": "11月"},
                now=datetime(2026, 11, 1, 8, 0, 0))

    def test_only_one_bound_allowed(self):
        _, _, _, _, start_at, ends_at = build_schedule_fields(
            "daily", {"hour": 9, "minute": 0, "ends_at": "2026-12-01 00:00:00"},
            now=datetime(2026, 11, 1, 8, 0, 0))
        assert start_at is None and ends_at == datetime(2026, 12, 1)


class TestCronWindowFirstPoint:
    """整月 daily 09:00：窗内首个触发点与推进（§15.2.1/15.2.2）"""

    NOW = datetime(2026, 10, 28, 10, 30)

    def test_full_month_daily_first_point_within_window(self):
        """「11 月整月每天 09:00」= start 11-01 00:00 / end 12-01 00:00：
        创建于 10-28 时首个计划点 = 11-01 09:00（起点之后首个匹配）。"""
        cron, _, run_at, next_due, start_at, ends_at = build_schedule_fields(
            "daily", {"hour": 9, "minute": 0,
                      "start_at": "2026-11-01 00:00:00",
                      "ends_at": "2026-12-01 00:00:00"}, now=self.NOW)
        assert cron == "0 9 * * *"
        assert next_due == datetime(2026, 11, 1, 9, 0)
        assert start_at == datetime(2026, 11, 1)
        assert ends_at == datetime(2026, 12, 1)

    def test_start_exactly_on_trigger_point_takes_it(self):
        """start_at 恰为 09:00：允许取该点（起点即首点，不推迟到次日）。"""
        _, _, _, next_due, _, _ = build_schedule_fields(
            "daily", {"hour": 9, "minute": 0,
                      "start_at": "2026-11-01 09:00:00",
                      "ends_at": "2026-12-01 00:00:00"},
            now=datetime(2026, 10, 28, 10, 30))
        assert next_due == datetime(2026, 11, 1, 9, 0)

    def test_end_exactly_on_trigger_point_excludes_it(self):
        """ends_at 恰为 12-01 09:00：该点不属于本窗（结束边界不含，避免
        00:00/09:00 属于两段）→ 推进到该点时返回 None。"""
        task = _window_task("daily", cron="0 9 * * *",
                            next_due=datetime(2026, 11, 30, 9, 0),
                            ends_at=datetime(2026, 12, 1, 9, 0))
        assert compute_next_due(task, datetime(2026, 11, 30, 9, 30)) is None

    def test_advance_beyond_window_returns_none(self):
        """11-30 09:00 认领（ends=12-01 00:00）：下次推进 12-01 09:00 出窗
        → None——最后一个窗内点认领后无后续计划，任务待终态收尾。"""
        task = _window_task("daily", cron="0 9 * * *",
                            next_due=datetime(2026, 11, 30, 9, 0),
                            ends_at=datetime(2026, 12, 1))
        assert compute_next_due(task, datetime(2026, 11, 30, 9, 30)) is None

    def test_no_point_in_window_rejected(self):
        """从当前时间起到 ends_at 无匹配点（窗口已整体过去）：创建拒绝。"""
        with pytest.raises(ScheduleConfigError):
            build_schedule_fields(
                "daily", {"hour": 9, "minute": 0,
                          "start_at": "2026-10-01 00:00:00",
                          "ends_at": "2026-10-20 00:00:00"},
                now=datetime(2026, 10, 28, 10, 30))

    def test_cron_advance_respects_future_start(self):
        """推进遇未来 start_at（恢复后场景）：取窗内首个匹配点，跳过更早的点。"""
        task = _window_task("daily", cron="0 9 * * *",
                            next_due=datetime(2026, 10, 28, 9, 0),
                            start_at=datetime(2026, 11, 5))
        assert compute_next_due(task, datetime(2026, 10, 28, 10, 30)) \
            == datetime(2026, 11, 5, 9, 0)

    def test_interval_advance_outside_window_returns_none(self):
        task = _window_task("interval", interval=3600,
                            next_due=datetime(2026, 11, 30, 23, 0),
                            ends_at=datetime(2026, 12, 1))
        assert compute_next_due(task, datetime(2026, 11, 30, 23, 30)) is None

    def test_interval_advance_inside_window_keeps_phase(self):
        task = _window_task("interval", interval=3600,
                            next_due=datetime(2026, 11, 30, 22, 0),
                            ends_at=datetime(2026, 12, 1))
        assert compute_next_due(task, datetime(2026, 11, 30, 22, 30)) \
            == datetime(2026, 11, 30, 23, 0)


class TestResumeWithWindow:
    def test_interval_resume_first_point_is_resume_moment(self):
        """§15.2.3：恢复时刻为首个新计划点（恰在恢复点重启周期，不补跑）"""
        task = _window_task("interval", interval=7200,
                            next_due=datetime(2026, 11, 1, 8, 0))
        resume_at = datetime(2026, 11, 3, 10, 15)
        next_due, will_expire, window_finished = compute_resume_due(task, resume_at)
        assert next_due == resume_at
        assert will_expire is False and window_finished is False

    def test_interval_resume_with_future_start_takes_start(self):
        """恢复时刻早于未来 start_at：首点取 start_at（窗口判定对 interval
        同样成立，不在窗口开始前触发，CR P2-4）。"""
        task = _window_task("interval", interval=7200,
                            start_at=datetime(2026, 11, 5, 8, 0))
        next_due, will_expire, window_finished = compute_resume_due(
            task, datetime(2026, 11, 3, 10, 15))
        assert next_due == datetime(2026, 11, 5, 8, 0)
        assert window_finished is False and will_expire is False

    def test_interval_resume_after_window_finished(self):
        """恢复时窗口已结束：window_finished=True（resume 事务统一置
        completed，不补跑）。"""
        task = _window_task("interval", interval=7200, ends_at=datetime(2026, 11, 2))
        next_due, will_expire, window_finished = compute_resume_due(
            task, datetime(2026, 11, 3, 10, 15))
        assert next_due is None and window_finished is True and will_expire is False

    def test_cron_resume_after_window_finished(self):
        task = _window_task("daily", cron="0 9 * * *",
                            ends_at=datetime(2026, 11, 10))
        next_due, _, window_finished = compute_resume_due(
            task, datetime(2026, 11, 20, 10, 0))
        assert next_due is None and window_finished is True

    def test_cron_resume_inside_window_gets_next_point(self):
        task = _window_task("daily", cron="0 9 * * *",
                            ends_at=datetime(2026, 12, 1))
        next_due, _, window_finished = compute_resume_due(
            task, datetime(2026, 11, 5, 10, 0))
        assert next_due == datetime(2026, 11, 6, 9, 0)
        assert window_finished is False

    def test_once_resume_keeps_run_at_semantics(self):
        task = {"schedule_type": "once", "run_at": datetime(2026, 11, 5, 9, 0),
                "next_due_at": datetime(2026, 11, 5, 9, 0)}
        next_due, will_expire, window_finished = compute_resume_due(
            task, datetime(2026, 11, 1, 8, 0))
        assert next_due == datetime(2026, 11, 5, 9, 0)
        assert will_expire is False and window_finished is False
