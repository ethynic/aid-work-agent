"""D2 dedicated plan timing and accepted scope facts (no external database)."""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.wecom_conversation import plans
from src.scheduler.special_plans import (first_line_sql, ordinary_task_prompt_allowed,
                                         ordinary_task_sql)


def test_today_interval_waits_a_complete_period_and_rejects_other_frequencies():
    before = datetime.now(plans.ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    schedule = plans.daily_plan_schedule('today', interval_hours=3)
    after = datetime.now(plans.ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    assert schedule['schedule_type'] == 'interval'
    assert schedule['interval_seconds'] == 3 * 3600
    assert before + timedelta(hours=3) <= schedule['next_due_at'] <= after + timedelta(hours=3)
    for hours in (0, 25, 1.5, True):
        with pytest.raises(ValueError):
            plans.daily_plan_schedule('today', interval_hours=hours)
    with pytest.raises(ValueError):
        plans.daily_plan_schedule('today', interval_hours=1, hour=2)


def test_yesterday_defaults_to_shanghai_two_am_only():
    schedule = plans.daily_plan_schedule('yesterday')
    assert schedule['schedule_type'] == 'daily'
    assert schedule['cron_expression'] == '0 2 * * *'
    assert schedule['next_due_at'] > datetime.now(plans.ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    with pytest.raises(ValueError):
        plans.daily_plan_schedule('yesterday', interval_hours=1)


def test_today_edit_restarts_full_interval_under_plan_lock(monkeypatch):
    """编辑时从该次保存时点重算，不沿旧 next_due_at 立刻触发。"""
    config = plans.daily_plan_config(account='acc1', device='device-a',
                                     target_day='today', scope_mode='all')
    existing = {'task_id': 'sched-one', 'task_prompt': plans.plan_prompt('daily', config),
                'status': 'active', 'notification_rule_id': None}
    connection = MagicMock()
    cursor = connection.__enter__.return_value.cursor.return_value
    cursor.fetchall.return_value = [existing]
    cursor.fetchone.return_value = existing

    class Clock:
        current = datetime(2026, 11, 4, 12)

        @classmethod
        def now(cls, tz):
            return cls.current.replace(tzinfo=tz)

    monkeypatch.setattr(plans, 'datetime', Clock)
    with patch('src.db.database.get_db_connection', return_value=connection):
        plans.save_daily_plan('tenant-a', 'user-a', create=False, account='acc1',
                              device='device-a', target_day='today', scope_mode='all',
                              conversation_ids=[], interval_hours=3)
        Clock.current = datetime(2026, 11, 4, 14)
        plans.save_daily_plan('tenant-a', 'user-a', create=False, account='acc1',
                              device='device-a', target_day='today', scope_mode='all',
                              conversation_ids=[], interval_hours=3)
    updates = [call.args[1] for call in cursor.execute.call_args_list
               if 'UPDATE scheduled_tasks SET task_prompt=' in call.args[0]]
    assert all(first_line_sql() in call.args[0]
               for call in cursor.execute.call_args_list
               if 'SELECT * FROM scheduled_tasks WHERE tenant_id=' in call.args[0])
    assert [params[4] for params in updates] == [datetime(2026, 11, 4, 15),
                                                  datetime(2026, 11, 4, 17)]
    assert connection.__enter__.return_value.commit.call_count == 2


class ScopeCursor:
    def __init__(self, ids):
        self.ids = ids
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchone(self):
        return {'business_day': date(2026, 11, 4)}

    def fetchall(self):
        return [{'id': item} for item in self.ids]


def _task(scope_mode, target_day='yesterday'):
    config = plans.daily_plan_config(account='acc1', device='device-a',
                                     target_day=target_day, scope_mode=scope_mode)
    return {'task_id': 'sched_one', 'tenant_id': 'tenant-a', 'user_id': 'user-a',
            'task_prompt': plans.plan_prompt('daily', config)}


def test_claim_snapshot_freezes_selected_scope_and_business_date():
    cursor = ScopeCursor([3, 7])
    descriptor, error = plans.freeze_scheduled_plan_in_tx(cursor, _task('selected'))
    assert error is None
    assert descriptor == {
        'schema_version': 2, 'kind': 'daily', 'trigger': 'scheduled',
        'task_id': 'sched_one', 'business_date': '2026-11-03',
        'timezone': 'Asia/Shanghai', 'account': 'acc1', 'device': 'device-a',
        'conversation_ids': [3, 7]}
    sql, params = cursor.queries[-1]
    assert 'daily_plan_task_id=%s' in sql
    assert params == ('tenant-a', 'user-a', 'acc1', 'device-a', 'sched_one')


def test_dynamic_all_uses_same_source_without_membership_and_empty_pauses():
    cursor = ScopeCursor([])
    descriptor, error = plans.freeze_scheduled_plan_in_tx(cursor, _task('all', 'today'))
    assert error == 'scope_empty'
    assert descriptor['business_date'] == '2026-11-04'
    assert descriptor['conversation_ids'] == []
    sql, params = cursor.queries[-1]
    assert 'daily_plan_task_id' not in sql
    assert params == ('tenant-a', 'user-a', 'acc1', 'device-a')


def test_retired_backfill_marker_does_not_enter_business_descriptor():
    assert plans.parse_plan_prompt('[wecom-conversation:backfill]{"device":"d"}') is None
    with pytest.raises(ValueError):
        plans.plan_prompt('backfill', {'device': 'd'})


def test_reserved_first_line_is_rejected_even_with_whitespace_or_invalid_json():
    """普通入口不可伪造专属计划；退役补录也不得重新变为普通定义。"""
    for marker in ('[wecom-conversation:daily]', '[wecom-conversation:backfill]'):
        for leading in (' \t', '\v\f', '\u3000'):
            assert not ordinary_task_prompt_allowed(f'{leading}{marker}not-json\nbody')
    assert ordinary_task_prompt_allowed('普通日报任务')
    sql = ordinary_task_sql()
    assert 'ltrim(split_part(' in sql
    assert '[wecom-conversation:daily]' in sql
    assert '[wecom-conversation:backfill]' not in sql
    assert all(f'chr({point})' in first_line_sql()
               for point in (11, 12, 160, 12288))
