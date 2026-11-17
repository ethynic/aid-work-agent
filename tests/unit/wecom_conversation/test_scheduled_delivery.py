"""Exercise scheduled sends through the shared dispatcher and Runtime endpoints."""

import asyncio
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import HTTPException
import pytest

from src.db.database import get_db_connection
from src.local_tools import api as runtime, repository
from src.scheduler.manager import ScheduledTaskManager
from src.wecom_conversation import proxy_api, proxy_dispatcher, proxy_monitor, proxy_tasks
from src.wecom_conversation import scheduled_delivery as delivery, scheduled_sends as schedules

from .test_proxy_tasks_service import ACCOUNT, USER, _baseline, _locator, _register
from .test_scheduled_sends import TZ, _claim, _create, _enable, _history

RECEIPT = 'send-2026-11-16T09-00-00-000Z'


def _execute(sql, params=()):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        result = cursor.fetchall() if cursor.description else None
        conn.commit()
        return result


@pytest.fixture
def scheduled(db_ready, tenant_id, monkeypatch):
    device_id = str(uuid4())
    group = tenant_id, device_id, ACCOUNT
    auth = {'tenant_id': tenant_id, 'id': device_id, 'user_id': USER,
            'capabilities_json': {'providers': ['wecom']}}
    _execute('INSERT INTO local_tool_devices (id,tenant_id,user_id,token_hash) VALUES (%s,%s,%s,%s)',
             (device_id, tenant_id, USER, device_id))
    _execute('INSERT INTO tenants (tenant_id,credit_balance) VALUES (%s,10)', (tenant_id,))
    monkeypatch.setattr(delivery.settings.deployment, 'private_cloud', False)
    monkeypatch.setattr(proxy_api, '_task_device', AsyncMock(return_value=(auth, None)))
    monkeypatch.setattr(proxy_api, '_identity_probe', AsyncMock(return_value=(ACCOUNT, None)))
    monkeypatch.setattr(runtime, '_spawn_wecom_log_callback', lambda view: None)
    yield group, auth
    delivery.set_unit_price(None)
    _execute('DELETE FROM client_usage_logs WHERE tenant_id=%s', (tenant_id,))
    _execute('DELETE FROM tenants WHERE tenant_id=%s', (tenant_id,))


def _plan(group, targets=None):
    now = _execute("SELECT clock_timestamp() AT TIME ZONE 'Asia/Shanghai' AS now")[0]['now']
    due = now.replace(tzinfo=TZ, microsecond=0) - timedelta(seconds=1)
    plan, locators = _create(group[0], group[1], targets, due=due)
    _enable(group[0], plan, due)
    _claim(plan, due)
    return plan, locators


async def _claim_device(auth):
    response = await runtime.runtime_claim(wait=0, device=auth)
    return response if response.get('invocation_id') else response.get('invocation')


async def _started(auth, invocation):
    return await runtime.runtime_started(invocation['invocation_id'],
        runtime.StartedRequest(claim_token=invocation['claim_token']), auth)


async def _result(auth, invocation, **result):
    return await runtime.runtime_result(invocation['invocation_id'],
        runtime.ResultRequest(claim_token=invocation['claim_token'], **result), auth)


def _charged(tenant_id):
    return [row['credit_cost'] for row in _execute(
        'SELECT credit_cost FROM client_usage_logs WHERE tenant_id=%s ORDER BY id', (tenant_id,))]


def _success(session):
    return {'success': True, 'effect': 'applied', 'data': {'verification': 'local-record', 'session': session}}


@pytest.mark.asyncio
async def test_schedule_only_group_binds_once_freezes_price_and_credits_once(scheduled):
    group, auth = scheduled
    tenant_id = group[0]
    plan, targets = _plan(group)
    assert group in ScheduledTaskManager()._load_wecom_proxy_groups()
    assert delivery.get_unit_price() == Decimal('0.50')
    delivery.set_unit_price('2.20')
    await asyncio.gather(proxy_dispatcher.run_tick(group), proxy_dispatcher.run_tick(group))
    first = await _claim_device(auth)
    assert first['tool_name'] == delivery.SEND_TOOL and first['arguments'] == {
        'session': targets[0], 'text': '  第一行\n第二行 '}
    with pytest.raises(HTTPException) as wrong_device:
        await _started({**auth, 'id': str(uuid4())}, first)
    assert wrong_device.value.status_code == 404
    assert (await _started(auth, first))['state'] == 'running'
    delivery.set_unit_price('0')
    assert (await _started(auth, first))['state'] == 'running'
    await asyncio.gather(_result(auth, first, **_success(targets[0])),
                         _result(auth, first, **_success(targets[0])))
    assert _charged(tenant_id) == []  # The ordinary Runtime result handler does not bill this kind.
    await proxy_dispatcher.run_tick(group)
    second = await _claim_device(auth)
    assert second['arguments']['session'] == targets[1]
    await _started(auth, second)
    await _result(auth, second, **_success(targets[1]))
    await proxy_dispatcher.run_tick(group)
    await proxy_dispatcher.run_tick(group)
    assert _charged(tenant_id) == [Decimal('2.20')]
    assert [row['status'] for row in _history(tenant_id, plan)] == ['succeeded', 'succeeded']
    assert _execute('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (tenant_id,))[0]['credit_balance'] == Decimal('7.80')
    assert _execute('SELECT 1 FROM agent_runners WHERE tenant_id=%s', (tenant_id,)) == []
    assert schedules.get_plan(tenant_id, USER, plan['task_id'])['status'] == 'completed'


@pytest.mark.asyncio
@pytest.mark.parametrize('authorize_first', [False, True])
async def test_pause_between_claim_and_start_releases_only_unstarted(scheduled, authorize_first):
    group, auth = scheduled
    plan, targets = _plan(group)
    await proxy_dispatcher.run_tick(group)
    first = await _claim_device(auth)
    if authorize_first:
        await _started(auth, first)
    schedules.pause_plan(group[0], USER, plan['task_id'])
    if authorize_first:
        assert (await _started(auth, first))['state'] == 'running'
        await _result(auth, first, **_success(targets[0]))
    else:
        with pytest.raises(HTTPException) as stopped:
            await _started(auth, first)
        assert stopped.value.status_code == 409
        await _result(auth, first, **_success(targets[0]))
    await proxy_dispatcher.run_tick(group)
    assert [row['status'] for row in _history(group[0], plan)] == [
        'succeeded' if authorize_first else 'cancelled', 'cancelled']
    assert _charged(group[0]) == ([Decimal('0.50')] if authorize_first else [])
    assert await _claim_device(auth) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('expire', [False, True])
async def test_start_rejection_is_terminal_and_does_not_replay_after_funds(scheduled, expire):
    group, auth = scheduled
    plan, targets = _plan(group)
    await proxy_dispatcher.run_tick(group)
    first = await _claim_device(auth)
    if expire:
        _execute("UPDATE bs_wecom_scheduled_sends SET deadline_at=clock_timestamp()-interval '1 ms' WHERE invocation_id=%s",
                 (first['invocation_id'],))
    else:
        _execute('UPDATE tenants SET credit_balance=0.49 WHERE tenant_id=%s', (group[0],))
    with pytest.raises(HTTPException) as rejected:
        await _started(auth, first)
    assert rejected.value.status_code == 409
    _execute('UPDATE tenants SET credit_balance=10 WHERE tenant_id=%s', (group[0],))
    await proxy_dispatcher.run_tick(group)
    second = await _claim_device(auth)
    assert second['arguments']['session'] == targets[1]
    row = _history(group[0], plan)[0]
    assert (row['status'], row['started_at'], row['error_code']) == (
        'expired' if expire else 'failed', None, 'SEND_WINDOW_EXPIRED' if expire else 'NO_CREDIT')


@pytest.mark.asyncio
async def test_lease_loss_holds_group_until_same_invocation_reports_real_completion(scheduled):
    group, auth = scheduled
    plan, targets = _plan(group)
    await proxy_dispatcher.run_tick(group)
    first = await _claim_device(auth)
    await _started(auth, first)
    _execute("UPDATE local_tool_invocations SET lease_expires_at=now()-interval '1 s' WHERE id=%s",
             (first['invocation_id'],))
    await proxy_dispatcher.run_tick(group)
    assert _history(group[0], plan)[0]['status'] == 'unknown'
    assert proxy_monitor._group_runner_busy(*group)
    assert await _claim_device(auth) is None
    await _result(auth, first, **_success(targets[0]))
    await proxy_dispatcher.run_tick(group)
    assert (await _claim_device(auth))['arguments']['session'] == targets[1]
    assert _charged(group[0]) == [Decimal('0.50')]


@pytest.mark.asyncio
@pytest.mark.parametrize('code,late,data', [
    ('VERSION_UNSUPPORTED', True, None), ('SNAPSHOT_UNSTABLE', False, None),
    ('WECOM_NOT_FOUND', False, None), ('TOOL_NOT_SUPPORTED', True, None),
    ('INTERNAL_ERROR', True, {'run_id': str(uuid4())}),
])
async def test_prewrite_failure_releases_group_and_next_target_claims(scheduled, code, late, data):
    group, auth = scheduled
    plan, targets = _plan(group)
    await proxy_dispatcher.run_tick(group)
    first = await _claim_device(auth)
    await _started(auth, first)
    if late:
        _execute("UPDATE local_tool_invocations SET lease_expires_at=now()-interval '1 s' WHERE id=%s",
                 (first['invocation_id'],))
        await proxy_dispatcher.run_tick(group)
        assert _history(group[0], plan)[0]['status'] == 'unknown'
    await _result(auth, first, success=False, effect='none', code=code, data=data)
    await proxy_dispatcher.run_tick(group)
    row, second = _history(group[0], plan)
    assert (row['status'], row['error_code'], second['status']) == ('failed', code, 'dispatched')
    assert (await _claim_device(auth))['arguments']['session'] == targets[1]
    assert _charged(group[0]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('code,data', [
    ('RESULT_TIMEOUT', {'run_id': str(uuid4())}),
    ('CANCELLED', {'run_id': str(uuid4())}),
    ('INTERNAL_ERROR', None),
    ('SNAPSHOT_UNSTABLE', {'receipt_invalidation_failed': True, 'run_id': str(uuid4())}),
])
async def test_unproven_failure_does_not_release_the_original_device_turn(scheduled, code, data):
    group, auth = scheduled
    plan, _ = _plan(group)
    await proxy_dispatcher.run_tick(group)
    first = await _claim_device(auth)
    await _started(auth, first)
    await _result(auth, first, success=False, effect='none', code=code, data=data)
    await proxy_dispatcher.run_tick(group)
    assert [row['status'] for row in _history(group[0], plan)] == ['unknown', 'queued']
    assert await _claim_device(auth) is None
    assert _charged(group[0]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('started_request_observes_loss', [False, True])
async def test_lost_unstarted_claim_recovers_same_call_only_after_original_account_is_ready(
        scheduled, monkeypatch, started_request_observes_loss):
    group, auth = scheduled
    plan, targets = _plan(group)
    await proxy_dispatcher.run_tick(group)
    original = await _claim_device(auth)
    _execute("UPDATE local_tool_invocations SET lease_expires_at=now()-interval '1 s' WHERE id=%s",
             (original['invocation_id'],))
    with pytest.raises(HTTPException) as expired_progress:
        await runtime.runtime_progress(original['invocation_id'],
            runtime.ProgressRequest(claim_token=original['claim_token'], stage='working'), auth)
    assert expired_progress.value.status_code == 404
    with pytest.raises(HTTPException) as expired_failure:
        await _result(auth, original, success=False, effect='none', code='TOOL_NOT_ALLOWED')
    assert expired_failure.value.status_code == 404
    if started_request_observes_loss:
        with pytest.raises(HTTPException) as lost:
            await _started(auth, original)
        assert lost.value.status_code == 409
    with monkeypatch.context() as temporary:
        temporary.setattr(proxy_api, '_identity_probe', AsyncMock(return_value=('wrong-account', None)))
        await proxy_dispatcher.run_tick(group)
    row, later = _history(group[0], plan)
    assert row['status'] == later['status'] == 'queued' and row['started_at'] is None
    assert str(row['invocation_id']) == original['invocation_id']
    assert await _claim_device(auth) is None  # The old account cannot be replaced by the current login.
    with pytest.raises(HTTPException) as stale:
        await _started(auth, original)
    assert stale.value.status_code == 404
    await proxy_dispatcher.run_tick(group)
    renewed = await _claim_device(auth)
    assert renewed['invocation_id'] == original['invocation_id']
    assert renewed['claim_token'] != original['claim_token']
    assert renewed['arguments']['session'] == targets[0]
    with pytest.raises(HTTPException) as stale_after_new_claim:
        await _started(auth, original)
    assert stale_after_new_claim.value.status_code == 404
    with pytest.raises(HTTPException) as stale_result:
        await _result(auth, original, **_success(targets[0]))
    assert stale_result.value.status_code == 404
    assert (await _started(auth, renewed))['state'] == 'running'


@pytest.mark.asyncio
async def test_unstarted_claim_is_never_requeued_after_the_window(scheduled):
    group, auth = scheduled
    plan, targets = _plan(group)
    await proxy_dispatcher.run_tick(group)
    original = await _claim_device(auth)
    _execute("UPDATE local_tool_invocations SET lease_expires_at=now()-interval '1 s' WHERE id=%s",
             (original['invocation_id'],))
    _execute("UPDATE bs_wecom_scheduled_sends SET deadline_at=clock_timestamp()-interval '1 ms' WHERE invocation_id=%s",
             (original['invocation_id'],))
    await proxy_dispatcher.run_tick(group)
    row, _ = _history(group[0], plan)
    assert (row['status'], row['started_at'], row['error_code']) == ('expired', None, 'SEND_WINDOW_EXPIRED')
    next_call = await _claim_device(auth)
    assert next_call['invocation_id'] != original['invocation_id']
    assert next_call['arguments']['session'] == targets[1]


@pytest.mark.asyncio
async def test_pause_and_authorization_race_has_only_one_winner(scheduled):
    group, auth = scheduled
    plan, _ = _plan(group)
    await proxy_dispatcher.run_tick(group)
    sent = await _claim_device(auth)
    started, paused = await asyncio.gather(
        _started(auth, sent), asyncio.to_thread(schedules.pause_plan, group[0], USER, plan['task_id']),
        return_exceptions=True)
    assert paused['status'] == 'paused'
    row, other = _history(group[0], plan)
    assert other['status'] == 'cancelled'
    if isinstance(started, HTTPException):
        assert started.status_code == 409 and row['status'] == 'cancelled' and row['started_at'] is None
    else:
        assert started['state'] == row['status'] == 'running' and row['started_at'] is not None


@pytest.mark.asyncio
async def test_ledger_half_failure_keeps_original_success_and_retries_without_double_debit(scheduled, monkeypatch):
    group, auth = scheduled
    plan, targets = _plan(group)
    await proxy_dispatcher.run_tick(group)
    first = await _claim_device(auth)
    await _started(auth, first)
    await _result(auth, first, **_success(targets[0]))
    insert_usage = delivery.ClientUsageLogDB.insert_tool_usage_row

    def fail_after_debit(*args, **kwargs):
        insert_usage(*args, **kwargs)
        raise RuntimeError('simulated ledger failure after debit')

    with monkeypatch.context() as temporary:
        temporary.setattr(delivery.ClientUsageLogDB, 'insert_tool_usage_row', fail_after_debit)
        await proxy_dispatcher.run_tick(group)
    assert _history(group[0], plan)[0]['status'] == 'succeeded'
    assert _charged(group[0]) == []
    assert _execute('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (group[0],))[0]['credit_balance'] == Decimal('10.00')
    assert (await _claim_device(auth))['arguments']['session'] == targets[1]
    await proxy_dispatcher.run_tick(group)
    await proxy_dispatcher.run_tick(group)
    assert _charged(group[0]) == [Decimal('0.50')]


@pytest.mark.asyncio
@pytest.mark.parametrize('stopped', [False, True])
async def test_reconciliation_bills_the_original_and_requires_separate_release_evidence(scheduled, stopped):
    group, auth = scheduled
    plan, targets = _plan(group, [_locator(1)])
    await proxy_dispatcher.run_tick(group)
    first = await _claim_device(auth)
    await _started(auth, first)
    data = {'receipt_id': RECEIPT, 'session': targets[0]}
    if stopped:
        data.update(sent_verification={'method': 'skipped', 'result': 'unverified'}, localdb_verification=[])
    await _result(auth, first, success=False, effect='unknown', code='EXECUTION_UNKNOWN', data=data)
    await proxy_dispatcher.run_tick(group)
    read = await _claim_device(auth)
    assert read['tool_name'] == delivery.READ_TOOL and read['arguments'] == {'receipt_id': RECEIPT}
    assert proxy_monitor._group_runner_busy(*group) != stopped
    await _started(auth, read)
    await _result(auth, read, success=True, effect='none', data={'receipt_id': RECEIPT, 'send_status': 'confirmed'})
    await proxy_dispatcher.run_tick(group)
    await proxy_dispatcher.run_tick(group)
    assert _history(group[0], plan)[0]['status'] == 'succeeded'
    assert _charged(group[0]) == [Decimal('0.50')]
    assert proxy_monitor._group_runner_busy(*group) != stopped
    assert await _claim_device(auth) is None
    # A later original success releases a previously held action without a
    # second charge, even after the business outcome was proven by the read.
    await _result(auth, first, **_success(targets[0]))
    await proxy_dispatcher.run_tick(group)
    assert not proxy_monitor._group_runner_busy(*group)
    assert _charged(group[0]) == [Decimal('0.50')]


@pytest.mark.asyncio
async def test_offline_or_switched_account_keeps_the_due_item_until_original_binding_returns(scheduled, monkeypatch):
    group, auth = scheduled
    plan, targets = _plan(group)
    with monkeypatch.context() as temporary:
        temporary.setattr(proxy_api, '_task_device', AsyncMock(return_value=(None, {'code': 'DEVICE_OFFLINE'})))
        await proxy_dispatcher.run_tick(group)
    with monkeypatch.context() as temporary:
        temporary.setattr(proxy_api, '_identity_probe', AsyncMock(return_value=('wrong-account', None)))
        await proxy_dispatcher.run_tick(group)
    assert await _claim_device(auth) is None
    assert [row['status'] for row in _history(group[0], plan)] == ['queued', 'queued']
    await proxy_dispatcher.run_tick(group)
    assert (await _claim_device(auth))['arguments']['session'] == targets[0]


@pytest.mark.asyncio
async def test_due_record_blocks_stale_monitor_read_and_opening_before_binding(scheduled):
    group, auth = scheduled
    tenant_id, device_id, account = group
    _, _ = _plan(group)
    locator = _locator(3)
    _register(tenant_id, locator, device=device_id)
    task = proxy_tasks.create_task(tenant_id, USER, device_id=device_id, account=account, self_user_id=account,
        title='监控', agent_id='agent-a', instruction='reply', sessions=[locator], opening_message='开场')
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=device_id, self_user_id=account,
                              baselines={locator: _baseline()})
    assert proxy_api._enqueue_monitor_read(tenant_id, USER, device_id, account, {'sessions': [locator]}) is None
    await proxy_dispatcher.run_tick(group)
    sent = await _claim_device(auth)
    assert repository.get_invocation(sent['invocation_id'], tenant_id)['business_kind'] == delivery.SEND_KIND


@pytest.mark.asyncio
async def test_previously_admitted_monitor_read_is_not_interrupted_by_due_send(scheduled):
    group, auth = scheduled
    tenant_id, device_id, account = group
    monitor = proxy_api._enqueue_monitor_read(tenant_id, USER, device_id, account, {'sessions': [_locator(3)]})
    read = await _claim_device(auth)
    assert read['invocation_id'] == monitor['id']
    await _started(auth, read)
    _, targets = _plan(group)
    await proxy_dispatcher.run_tick(group)
    assert await _claim_device(auth) is None
    await _result(auth, read, success=True, effect='none')
    await proxy_dispatcher.run_tick(group)
    assert (await _claim_device(auth))['arguments']['session'] == targets[0]


@pytest.mark.asyncio
async def test_opening_in_progress_can_release_before_due_send_and_unstarted_opening_yields(scheduled):
    group, auth = scheduled
    tenant_id, device_id, account = group
    opening_targets = [_locator(3), _locator(4)]
    for locator in opening_targets:
        _register(tenant_id, locator, device=device_id)
    task = proxy_tasks.create_task(tenant_id, USER, device_id=device_id, account=account, self_user_id=account,
        title='监控', agent_id='agent-a', instruction='reply', sessions=opening_targets, opening_message='开场')
    proxy_tasks.complete_start(tenant_id, USER, task['id'], device_id=device_id, self_user_id=account,
                              baselines={locator: _baseline() for locator in opening_targets})
    await proxy_dispatcher.run_tick(group)
    opened = await _claim_device(auth)
    assert opened['arguments']['session'] == opening_targets[0]
    await _started(auth, opened)
    _, targets = _plan(group)
    await proxy_dispatcher.run_tick(group)
    assert await _claim_device(auth) is None
    await _result(auth, opened, success=False, code='TARGET_NOT_FOUND', effect='none')
    await proxy_dispatcher.run_tick(group)
    due = await _claim_device(auth)
    assert due['arguments']['session'] == targets[0]
    opening_states = [s['opening_send_status'] for s in proxy_tasks.get_task(tenant_id, USER, task['id'])['targets']]
    assert opening_states == ['failed', 'pending']
