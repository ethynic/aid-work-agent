"""Prepared actual Context normal; mutable production is never imported here.

Only platform HTTP state/pull replies are fictional. The original worker,
history repositories, SQL transaction, identity and classification stay real.
Forbidden legacy recap/Redis calls are observed fail-closed, not fake results.
"""
import asyncio

import pytest

from .kf_context_fixtures import context_receipts, kf_scope, human_text, assert_full_history_binding
from .kf_admission_peer import StateReply

pytestmark = pytest.mark.integration


def test_actual_context_customer_and_servicer_original_history_are_once_and_recap_is_only_pending_adapter(
        context_receipts, monkeypatch):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.services.recap import runner as old_recap
    import redis

    c, s = context_receipts, context_receipts.scope
    customer_text, employee_text = 'Fictional human customer context', 'Fictional employee answer'
    customer, employee = c.receive(
        human_text(s, 'context_customer_' + s.marker, customer_text),
        human_text(s, 'context_employee_' + s.marker, employee_text, employee=True))
    before = {locator.message_id: c.receipt(locator) for locator in (customer, employee)}
    assert s.rows('SELECT user_id,subagent_id FROM channel_sessions WHERE session_id=%s',
        (s.legacy_sid,)) == [{'user_id': None, 'subagent_id': ''}]
    assert all(row['accepted_input_ref'] is None for row in before.values())
    assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
    c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3}),
        StateReply({'errcode': 0, 'service_state': 3})]
    forbidden_calls = {'old_void_recap': 0, 'redis_io': 0}

    def forbidden_recap(*args, **kwargs):
        forbidden_calls['old_void_recap'] += 1
        raise AssertionError('OLD_VOID_RECAP_MUST_NOT_EXECUTE')

    def unavailable_redis(*args, **kwargs):
        forbidden_calls['redis_io'] += 1
        raise ConnectionError('FICTIONAL_CONTEXT_REDIS_UNAVAILABLE')

    monkeypatch.setattr(old_recap, 'enqueue_human_period_tasks', forbidden_recap)
    monkeypatch.setattr(redis.Redis, 'execute_command', unavailable_redis)
    repository = ContextRepository(s.database.connect)

    async def consume():
        worker = ContextWorker(c.config.wecom_kf, repository=repository,
            client_factory=c.original_client)
        try:
            first, second = await worker.run_once(), await worker.run_once()
            assert await worker.run_once() is None
            assert not worker.tasks
            return first, second
        finally:
            await worker.close()
            assert not worker.tasks

    results = asyncio.run(consume())
    assert [result['disposition'] for result in results] == ['persisted', 'persisted']
    assert len({result['history_id'] for result in results}) == 2
    history = c.stored_history()
    assert len(history) == 3 and history[0]['content'] == 'Original fictional history'
    current = history[1:]
    assert [row['role'] for row in current] == ['user', 'user']
    assert [row['content'] for row in current] == [customer_text, '[人工客服] ' + employee_text]
    for row, locator, result in zip(current, (customer, employee), results):
        assert row['message_id'] == result['history_id']
        metadata = assert_full_history_binding(row, before[locator.message_id], s)
        assert metadata['source'] == ('servicer' if locator == employee else 'customer_human')
        assert metadata['namespace'] == 'sync' and metadata['actor_id'] == s.actor_id
        assert metadata['context_ref'] == row['message_id']
        assert not row['is_recalled']
    assert current[1]['metadata']['servicer_userid'] == 'fictional_servicer_' + s.marker
    # Original legacy and new Runtime readers both apply the real persisted
    # metadata. The employee is an assistant, never a customer instruction.
    for reader in (c.original_history, c.runtime_history):
        roles = [(row['role'], row['content']) for row in reader()]
        assert ('user', customer_text) in roles
        assert ('assistant', '[人工客服] ' + employee_text) in roles
        assert ('user', '[人工客服] ' + employee_text) not in roles
    classifications = s.rows('SELECT message_id,classification,observed_state,scope '
        'FROM wecom_kf_receipt_classifications WHERE account_id=%s ORDER BY message_id',
        (c.account['account_id'],))
    assert len(classifications) == 2
    assert {row['classification'] for row in classifications} == {'human', 'employee'}
    assert all(row['observed_state'] == 3 and row['scope']['session_id'] == s.legacy_sid
        and row['scope']['user_id'] is None for row in classifications)
    consumptions = s.rows('SELECT message_id,history_id,disposition FROM '
        'wecom_kf_context_consumptions WHERE account_id=%s', (c.account['account_id'],))
    intents = s.rows('SELECT * FROM wecom_kf_context_task_intents WHERE account_id=%s '
        'ORDER BY task_id', (c.account['account_id'],))
    assert len(consumptions) == 2 and all(row['disposition'] == 'persisted' for row in consumptions)
    assert len(intents) == 4
    assert {row['task_name'] for row in intents} == {'lead_refresh', 'external_push_human'}
    assert all(row['state'] == 'pending_adapter' and row['operation_version'] == 1
        and row['scope']['route_id'] == before[row['message_id']]['route_id']
        and row['history_id'] in {result['history_id'] for result in results} for row in intents)
    # Stable original same-TX domain retry creates no second history or intent,
    # performs no fresh SDK call and cannot become AI after current state1.
    for locator, result in zip((customer, employee), results):
        repeat = repository.commit(locator)
        assert repeat['history_id'] == result['history_id'] and repeat['disposition'] == 'persisted'
    assert c.stored_history() == history
    assert s.rows('SELECT * FROM wecom_kf_context_task_intents WHERE account_id=%s '
        'ORDER BY task_id', (c.account['account_id'],)) == intents
    assert all(c.receipt(locator) == before[locator.message_id] for locator in (customer, employee))
    assert len(c.platform.calls) == 2 and not c.platform.errors
    assert forbidden_calls == {'old_void_recap': 0, 'redis_io': 0}
    assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
    assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
