"""First-start text uses the real invocation lifecycle without a Runner."""
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.db.database import get_db_connection
from src.local_tools import repository
from src.wecom_conversation import proxy_api, proxy_dispatcher, proxy_monitor, proxy_opening, proxy_tasks
from src.wecom_conversation.service import ConversationServiceError
from src.wecom_conversation.tables import init_tables

from .test_proxy_tasks_service import ACCOUNT, USER, _baseline, _locator, _register


@pytest.fixture
def opening_task(db_ready, tenant_id, monkeypatch):
    device_id = str(uuid4())
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO local_tool_devices (id,tenant_id,user_id,token_hash) VALUES (%s,%s,%s,%s)',
                       (device_id, tenant_id, USER, device_id))
        conn.commit()
    targets = [_locator(1), _locator(2)]
    for locator in targets:
        _register(tenant_id, locator, device=device_id)
    task = proxy_tasks.create_task(
        tenant_id, USER, device_id=device_id, account=ACCOUNT, self_user_id=ACCOUNT,
        title='opening', agent_id='agent-a', instruction='reply', sessions=targets,
        opening_message='  第一行\n第二行  ')
    monkeypatch.setattr(proxy_api, '_task_device', AsyncMock(return_value=({'id': device_id}, None)))
    return (tenant_id, device_id, ACCOUNT), task, targets


def _start(key, task, locators):
    return proxy_tasks.complete_start(key[0], USER, task['id'], device_id=key[1], self_user_id=key[2],
                                      baselines={locator: _baseline() for locator in locators})


def _targets(key, task):
    return proxy_tasks.get_task(key[0], USER, task['id'])['targets']


def _claim(key):
    row = repository.claim_next(key[1], key[0], 'test-token', 60, provider_keys=['wecom'])
    return row


def _result(key, row, *, success=False, code=None, effect='none', data=None):
    return repository.write_runtime_result(row['id'], key[0], 'test-token', success, code=code,
                                           effect=effect, data=data, device_id=key[1])


@pytest.mark.parametrize('invalid', ['', '  \n  ', '🙂' * 1001, 'a\0b'])
def test_opening_message_refuses_values_the_device_cannot_send(invalid):
    with pytest.raises(ConversationServiceError, match='首次发送消息'):
        proxy_tasks.validate_opening_message(invalid)


def test_previous_start_is_recognized_even_if_the_old_target_was_removed(opening_task):
    key, task, locators = opening_task
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """UPDATE bs_wecom_proxy_task_sessions SET status='removed',web_session_id='legacy-session'
                WHERE tenant_id=%s AND task_id=%s AND session=%s""", (key[0], task['id'], locators[0]))
        conn.commit()
        init_tables(conn)
    assert proxy_tasks.get_task(key[0], USER, task['id'])['has_started']
    with pytest.raises(ConversationServiceError, match='首次启动后'):
        proxy_tasks.edit_stopped_task(key[0], USER, task['id'], opening_message='不能补发')
    _start(key, task, locators[1:])
    assert all(row['opening_send_status'] is None for row in _targets(key, task))


@pytest.mark.asyncio
async def test_failed_start_then_one_shot_serial_send_and_restart(opening_task):
    key, task, locators = opening_task
    with pytest.raises(ConversationServiceError):
        _start(key, task, locators[:1])
    assert all(target['opening_send_status'] is None for target in _targets(key, task))
    assert _claim(key) is None

    assert _start(key, task, locators)['has_started']
    assert proxy_monitor._group_runner_busy(*key)
    await proxy_dispatcher.run_tick(key)
    first = _claim(key)
    assert first['tool_name'] == 'wecom_message_send'
    assert first['arguments_json'] == {'session': locators[0], 'text': task['opening_message']}
    assert first['business_ref'] == {'version': 1, 'task_id': task['id'],
                                     'target_id': _targets(key, task)[0]['id']}
    await proxy_dispatcher.run_tick(key)
    assert _claim(key) is None  # The first invocation still owns the device turn.
    assert repository.mark_started(first['id'], key[0], 'test-token')['state'] == 'running'
    _result(key, first, code='TARGET_NOT_FOUND')
    await proxy_dispatcher.run_tick(key)
    second = _claim(key)
    assert second['arguments_json']['session'] == locators[1]
    assert repository.mark_started(second['id'], key[0], 'test-token')['state'] == 'running'
    _result(key, second, success=True, effect='applied',
            data={'verification': 'local-record', 'session': locators[1]})
    await proxy_dispatcher.run_tick(key)
    assert not proxy_monitor._group_runner_busy(*key)
    records = _targets(key, task)
    assert [(row['opening_send_status'], row['opening_send_error']) for row in records] == [
        ('failed', 'TARGET_NOT_FOUND'), ('succeeded', None)]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT count(*) AS count FROM agent_runners WHERE tenant_id=%s', (key[0],))
        assert cursor.fetchone()['count'] == 0

    proxy_tasks.request_stop(key[0], USER, task['id'])
    third = _locator(3)
    _register(key[0], third, device=key[1])
    proxy_tasks.edit_stopped_task(key[0], USER, task['id'], add_sessions=[third], add_baselines={third: _baseline()})
    _start(key, task, [])
    await proxy_dispatcher.run_tick(key)
    assert _claim(key) is None
    assert [row['opening_send_status'] for row in _targets(key, task)] == ['failed', 'succeeded', None]


@pytest.mark.asyncio
async def test_stop_before_started_cancels_claim_and_does_not_resume_opening(opening_task):
    key, task, locators = opening_task
    _start(key, task, locators)
    await proxy_dispatcher.run_tick(key)
    first = _claim(key)
    assert proxy_tasks.request_stop(key[0], USER, task['id'])['status'] == 'stopped'
    assert repository.mark_started(first['id'], key[0], 'test-token')['state'] == 'cancelled'
    assert [row['opening_send_status'] for row in _targets(key, task)] == ['cancelled', 'cancelled']
    _start(key, task, [])
    await proxy_dispatcher.run_tick(key)
    assert _claim(key) is None


@pytest.mark.asyncio
async def test_device_group_fences_another_dispatchers_stale_opening_snapshot(opening_task, monkeypatch):
    key, task, locators = opening_task
    _start(key, task, locators)
    entered = asyncio.Event()
    resume = asyncio.Event()

    async def device_check(*_):
        entered.set()
        await resume.wait()
        return {'id': key[1]}, None

    monkeypatch.setattr(proxy_api, '_task_device', device_check)
    first = proxy_opening._load_group(key)[0]
    # B is already in an independent dispatcher turn, after reading both pending
    # targets. Bypassing the in-process lock here models a separate process.
    dispatcher_b = asyncio.create_task(proxy_opening.run_tick(key, runner_busy=False))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert await asyncio.to_thread(proxy_opening._bind, first)
    finally:
        resume.set()
        await asyncio.wait_for(dispatcher_b, timeout=5)
    assert [row['opening_send_status'] for row in _targets(key, task)] == ['sending', 'pending']
    # The actual durable boundary must also deny a stale snapshot of the second target.
    second = proxy_opening._load_group(key)[1]
    assert not proxy_opening._bind(second)


@pytest.mark.parametrize('agent_allowed', [True, False])
@pytest.mark.asyncio
async def test_opening_published_after_dispatch_precheck_keeps_reply_pending(
        opening_task, monkeypatch, agent_allowed):
    key, opening, locators = opening_task
    tenant_id, device_id, account = key
    reply_locator = _locator(3)
    _register(tenant_id, reply_locator, device=device_id)
    reply = proxy_tasks.create_task(
        tenant_id, USER, device_id=device_id, account=account, self_user_id=account,
        title='reply', agent_id='agent-a', instruction='reply', sessions=[reply_locator])
    _start(key, reply, [reply_locator])
    with get_db_connection() as conn:
        cursor = conn.cursor()
        batch = proxy_tasks.create_batch_in_tx(
            cursor, tenant_id, USER, task_id=reply['id'], session=reply_locator,
            batch_end_msg_id=101, input_snapshot={'messages': []}, agent_id='agent-a',
            instruction_snapshot='reply', web_session_id=_targets(key, reply)[0]['web_session_id'])
        conn.commit()

    async def check_agent(_):
        # The ordinary tick already observed an empty opening queue. Another
        # transaction publishes first-start work before the ordinary claim.
        await asyncio.to_thread(_start, key, opening, locators)
        return agent_allowed

    monkeypatch.setattr(proxy_dispatcher, '_agent_allowed', check_agent)
    monkeypatch.setattr(proxy_dispatcher, '_resolve_submit_text', AsyncMock(return_value='text'))
    submit = AsyncMock(return_value={'runner': {'runner_id': 'unexpected-runner'}})
    monkeypatch.setattr(proxy_dispatcher, '_submit_runner', submit)
    await proxy_dispatcher.run_tick(key)
    assert proxy_tasks.get_batch(tenant_id, batch['id'])['status'] == 'pending'
    assert proxy_tasks.get_task(tenant_id, USER, reply['id'])['status'] == 'running'
    assert all(row['opening_send_status'] == 'pending' for row in _targets(key, opening))
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_sends_are_never_replayed_and_reconcile_original_after_next_send(opening_task):
    key, task, locators = opening_task
    _start(key, task, locators)
    await proxy_dispatcher.run_tick(key)
    first = _claim(key)
    repository.mark_started(first['id'], key[0], 'test-token')
    receipt_id = 'send-2026-11-10T01-02-03-444Z'
    _result(key, first, code='EXECUTION_UNKNOWN', effect='unknown', data={'receipt_id': receipt_id})
    await proxy_dispatcher.run_tick(key)
    second = _claim(key)
    assert second['tool_name'] == 'wecom_message_send' and second['arguments_json']['session'] == locators[1]
    repository.mark_started(second['id'], key[0], 'test-token')
    _result(key, second, success=True, effect='applied',
            data={'verification': 'local-record', 'session': locators[1]})
    await proxy_dispatcher.run_tick(key)
    await proxy_dispatcher.run_tick(key)
    read = _claim(key)
    assert read['tool_name'] == 'wecom_send_status' and read['arguments_json'] == {'receipt_id': receipt_id}
    assert read['business_ref']['send_invocation_id'] == str(first['id'])
    repository.mark_started(read['id'], key[0], 'test-token')
    _result(key, read, success=True, data={'send_status': 'confirmed'})
    await proxy_dispatcher.run_tick(key)
    await proxy_dispatcher.run_tick(key)
    assert _claim(key) is None
    assert _targets(key, task)[0]['opening_send_status'] == 'succeeded'
    assert repository.get_invocation(first['id'], key[0])['state'] == 'unknown'
