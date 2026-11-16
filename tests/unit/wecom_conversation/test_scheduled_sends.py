"""Dedicated WeCom schedules retain exact occurrence and device ordering."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from src.db.database import get_db_connection
from src.scheduler.db import ScheduledTaskDB
from src.scheduler.dispatch import ScheduledTaskDispatcher
from src.scheduler.special_plans import ordinary_task_prompt_allowed
from src.wecom_conversation import proxy_tasks, scheduled_sends
from src.wecom_conversation.service import ConversationServiceError

from .test_proxy_tasks_service import ACCOUNT, USER, _baseline, _locator, _register

TZ = ZoneInfo('Asia/Shanghai')
DUE = datetime(2026, 11, 16, 16, 0, tzinfo=TZ)


@pytest.fixture
def device(db_ready, tenant_id):
    return str(uuid4())


def _create(tenant, device, locators, *, kind='once', name='通知', due=DUE):
    targets = locators or [_locator(1), _locator(2)]
    for loc in targets:
        _register(tenant, loc, name=f'会话 {loc[-5:]}', device=device)
    fields = ({'run_at': due.strftime('%Y-%m-%d %H:%M:%S')}
              if kind == 'once' else {'hour': due.hour, 'minute': due.minute})
    if kind == 'weekly':
        fields['weekdays'] = ['mon', 'wed']
    plan = scheduled_sends.create_plan(
        tenant, USER, device_id=device, account=ACCOUNT, name=name, sessions=targets,
        text='  第一行\n第二行 ', schedule_type=kind, time_config=fields)
    return plan, targets


def _enable(tenant, plan, due=DUE):
    return scheduled_sends.enable_plan(tenant, USER, plan['task_id'], now=due - timedelta(seconds=1))


def _claim(plan, now=DUE):
    return ScheduledTaskDB.claim_due(plan['task_id'], now=now)


def _history(tenant, plan):
    return scheduled_sends.list_executions(tenant, USER, task_id=plan['task_id'])


def test_paused_plan_lifecycle_and_ordinary_task_visibility(tenant_id, device):
    plan, _ = _create(tenant_id, device, [])
    assert plan['status'] == 'paused' and plan['next_due_at'] is None
    assert scheduled_sends.get_plan('another-tenant', USER, plan['task_id']) is None
    assert scheduled_sends.get_plan(tenant_id, 'another-user', plan['task_id']) is None
    assert ScheduledTaskDB.list_by_user(USER, tenant_id=tenant_id) == []
    assert not ordinary_task_prompt_allowed(' \t[wecom-conversation:scheduled-send]\ninvalid')
    assert _enable(tenant_id, plan)['status'] == 'active'
    assert ScheduledTaskDB.count_by_user(USER, tenant_id=tenant_id) == 0
    assert not ScheduledTaskDB.pause(plan['task_id'])
    assert ScheduledTaskDB.submit_manual(plan['task_id'], occurrence_key='fake') is None
    with pytest.raises(ConversationServiceError, match='暂停'):
        scheduled_sends.edit_plan(
            tenant_id, USER, plan['task_id'], name='变更', text='修改', sessions=plan['sessions'],
            schedule_type='daily', time_config={'hour': 10, 'minute': 0})
    _claim(plan)
    assert all(row['status'] == 'queued' for row in _history(tenant_id, plan))
    assert scheduled_sends.pause_plan(tenant_id, USER, plan['task_id'])['status'] == 'paused'
    assert all(row['status'] == 'cancelled' for row in _history(tenant_id, plan))
    edited = scheduled_sends.edit_plan(
        tenant_id, USER, plan['task_id'], name='变更', text='修改', sessions=plan['sessions'],
        schedule_type='weekly', time_config={'hour': 10, 'minute': 0, 'weekdays': ['wed', 'mon']})
    assert (edited['device_id'], edited['account']) == (device, ACCOUNT)
    assert _enable(tenant_id, plan)['next_due_at'] == datetime(2026, 11, 18, 10, 0)
    scheduled_sends.delete_plan(tenant_id, USER, plan['task_id'])
    assert scheduled_sends.get_plan(tenant_id, USER, plan['task_id']) is None
    assert scheduled_sends.list_plans(tenant_id, USER) == []
    assert len(_history(tenant_id, plan)) == 2
    assert len(scheduled_sends.list_executions(tenant_id, USER)) == 2
    assert scheduled_sends.list_executions(tenant_id, 'another-user', task_id=plan['task_id']) == []


@pytest.mark.parametrize('late, status', [(timedelta(minutes=10), 'queued'),
                                        (timedelta(minutes=10, microseconds=1), 'expired')])
def test_once_window_records_every_target_without_runner_handoff(tenant_id, device, late, status):
    plan, targets = _create(tenant_id, device, [])
    _enable(tenant_id, plan)
    assert _claim(plan, DUE + late) is None
    rows = _history(tenant_id, plan)
    assert {row['session'] for row in rows} == set(targets)
    assert all(row['status'] == status and row['due_at'] == DUE for row in rows)
    assert all(row['started_at'] is None for row in rows)
    current = ScheduledTaskDB.get_by_id(plan['task_id'])
    assert current['next_due_at'] is None and current['pending_occurrence_key'] is None
    assert current['status'] == ('expired' if status == 'expired' else 'active')


def test_concurrent_claim_and_long_outage_keep_old_expiry_and_timely_occurrence(tenant_id, device):
    old = DUE - timedelta(days=2)
    plan, targets = _create(tenant_id, device, [], kind='daily', due=old)
    _enable(tenant_id, plan, old)
    with ThreadPoolExecutor(max_workers=2) as threads:
        futures = [threads.submit(_claim, plan, DUE + timedelta(minutes=2)) for _ in range(2)]
        assert [item.result(10) for item in futures] == [None, None]
    rows = _history(tenant_id, plan)
    assert len(rows) == 2 * len(targets)
    assert {(row['due_at'], row['status']) for row in rows} == {(old, 'expired'), (DUE, 'queued')}
    assert ScheduledTaskDB.get_by_id(plan['task_id'])['next_due_at'] == (DUE + timedelta(days=1)).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_real_dispatcher_uses_database_beijing_time_and_never_contacts_runner(tenant_id, device):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT statement_timestamp() AT TIME ZONE 'Asia/Shanghai' AS now")
        due = cursor.fetchone()['now'].replace(tzinfo=TZ, microsecond=0) - timedelta(seconds=1)
    plan, targets = _create(tenant_id, device, [], due=due)
    _enable(tenant_id, plan, due)
    assert plan['task_id'] in {row['task_id'] for row in ScheduledTaskDB.list_due(datetime(1970, 1, 1))}
    runner = AsyncMock()
    dispatcher = ScheduledTaskDispatcher(client=runner)
    await dispatcher.run_once()
    await dispatcher.run_once()
    assert [(row['session'], row['status']) for row in _history(tenant_id, plan)] == [
        (target, 'queued') for target in targets]
    assert ScheduledTaskDB.list_pending(datetime.now(), 50) == []
    runner.request.assert_not_called()


def test_concurrent_pause_and_claim_never_leave_a_send_queued(tenant_id, device):
    plan, targets = _create(tenant_id, device, [])
    _enable(tenant_id, plan)
    together = Barrier(2, timeout=5)

    def claim():
        together.wait()
        return _claim(plan)

    def pause():
        together.wait()
        return scheduled_sends.pause_plan(tenant_id, USER, plan['task_id'])

    with ThreadPoolExecutor(max_workers=2) as threads:
        claimed, paused = threads.submit(claim), threads.submit(pause)
        assert claimed.result(10) is None
        assert paused.result(10)['status'] == 'paused'
    rows = _history(tenant_id, plan)
    assert not rows or {row['session'] for row in rows} == set(targets)
    assert all(row['status'] == 'cancelled' and row['error_code'] == 'PLAN_PAUSED' for row in rows)
    assert _claim(plan) is None


def test_group_candidate_waits_for_running_reply_and_orders_targets(tenant_id, device):
    first, targets = _create(tenant_id, device, [], name='先创建')
    later, _ = _create(tenant_id, device, targets, name='后创建')
    _enable(tenant_id, first)
    _enable(tenant_id, later)
    _claim(later)
    _claim(first)
    other_device = str(uuid4())
    other_group = (tenant_id, other_device, ACCOUNT)
    independent, _ = _create(tenant_id, other_device, [_locator(5)], name='另一台设备')
    _enable(tenant_id, independent)
    _claim(independent)
    reply_locator = _locator(3)
    _register(tenant_id, reply_locator, device=device)
    reply = proxy_tasks.create_task(
        tenant_id, USER, device_id=device, account=ACCOUNT, self_user_id=ACCOUNT,
        title='监控', agent_id='agent-a', instruction='reply', sessions=[reply_locator])
    proxy_tasks.complete_start(tenant_id, USER, reply['id'], device_id=device, self_user_id=ACCOUNT,
                              baselines={reply_locator: _baseline()})
    target = proxy_tasks.get_task(tenant_id, USER, reply['id'])['targets'][0]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        batch = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=reply['id'], session=reply_locator,
            batch_end_msg_id=101, input_snapshot={'messages': []}, agent_id='agent-a',
            instruction_snapshot='reply', web_session_id=target['web_session_id'])
        proxy_tasks.start_batch_in_tx(cursor, tenant_id, batch['id'])
        conn.commit()
    group = (tenant_id, device, ACCOUNT)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        assert scheduled_sends.next_execution_in_tx(cursor, group, now=DUE) is None
        assert scheduled_sends.next_execution_in_tx(cursor, other_group, now=DUE)['task_id'] == independent['task_id']
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.transition_batch_in_tx(cursor, tenant_id, batch['id'], 'failed')
        conn.commit()
    opening_locator = _locator(4)
    _register(tenant_id, opening_locator, device=device)
    opening = proxy_tasks.create_task(
        tenant_id, USER, device_id=device, account=ACCOUNT, self_user_id=ACCOUNT,
        title='首启监控', agent_id='agent-a', instruction='reply', sessions=[opening_locator],
        opening_message='首启问候')
    proxy_tasks.complete_start(tenant_id, USER, opening['id'], device_id=device, self_user_id=ACCOUNT,
                              baselines={opening_locator: _baseline()})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = scheduled_sends.next_execution_in_tx(cursor, group, now=DUE)
        assert (row['task_id'], row['session'], row['text']) == (first['task_id'], targets[0], '  第一行\n第二行 ')
        cursor.execute("""UPDATE bs_wecom_proxy_task_sessions SET opening_send_status='sending'
                          WHERE tenant_id=%s AND task_id=%s""", (tenant_id, opening['id']))
        assert scheduled_sends.next_execution_in_tx(cursor, group, now=DUE) is None
