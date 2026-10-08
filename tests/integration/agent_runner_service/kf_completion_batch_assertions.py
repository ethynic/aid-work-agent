"""Actual normal-chain batch stage, prepared against current declared ports.

Not a separate test or passed result. The final completion normal invokes this
stage after real business handling, and then performs original delivery/recap.
Mutable production is imported only inside a future authorized frozen run.
"""
import asyncio
from decimal import Decimal
import time

from .kf_admission_service import start_original_worker
from .test_api import require_status
from .test_worker import decoded, runner, terminal


async def accept_original_sealed_batch(completion, api, locators):
    from src.channels.wecom_kf.admission_repository import KfBatchRepository
    from src.services.agent_runner.source_client import SourceClient
    from src.services.agent_runner.source_receipts import SourceBatch
    scope = completion.scope
    expected = SourceBatch(tuple(locators))
    repository = KfBatchRepository(scope.database.connect)
    batch = await asyncio.to_thread(repository.collect, locators[0])
    deadline = time.monotonic() + 5
    while batch is None and time.monotonic() < deadline:
        await asyncio.sleep(.1)
        batch = await asyncio.to_thread(repository.collect, locators[0])
    assert batch is not None and batch.members == expected.members
    client = SourceClient(completion.config, token=api._credential)
    try:
        first = await client.accept_batch(batch)
        repeat = await client.accept_batch(batch)
    finally:
        await client.close()
    assert first['success'] is True and first['created'] is True
    assert repeat['success'] is True and repeat['created'] is False
    assert first['batch_ref'] == repeat['batch_ref'] == expected.stable_key
    assert first['current_runner_id'] == repeat['current_runner_id']
    assert len(first['members']) == len(repeat['members']) == len(locators)
    for locator, actual, again in zip(locators, first['members'], repeat['members']):
        assert actual['input_ref'] == again['input_ref'] == locator.stable_key
        assert actual['accepted_runner_id'] == again['accepted_runner_id'] == first['current_runner_id']
        assert actual['current_runner_id'] == again['current_runner_id'] == first['current_runner_id']
    manifests = scope.rows('SELECT b.phase,b.accepted_runner_id,m.ordinal,m.message_id,m.payload_digest '
        'FROM wecom_kf_input_batches b JOIN wecom_kf_input_batch_members m USING(batch_ref) '
        'WHERE m.account_id=%s ORDER BY b.opened_at,m.ordinal', (completion.account['account_id'],))
    selected = [row for row in manifests if row['message_id'] in {locator.message_id for locator in locators}]
    assert len(selected) == len(locators)
    assert [row['ordinal'] for row in selected] == list(range(len(locators)))
    assert all(row['phase'] == 'accepted' and row['accepted_runner_id'] == first['current_runner_id'] for row in selected)
    return first


def run_original_batch_to_terminal(completion, api, fleet, provider, locators,
                                   texts, reply, model_marker, *, accepted_response=None,
                                   receipt_snapshots=None):
    """One original resident root/model/fee, preserving each receipt's identity."""
    scope = completion.scope
    assert len(locators) == len(texts) == 2
    if receipt_snapshots is None:
        assert accepted_response is None
        snapshots = scope.rows('SELECT message_id,payload,payload_digest,accepted_input_ref '
            'FROM wecom_kf_inbox WHERE account_id=%s AND message_id=ANY(%s) ORDER BY receive_seq',
            (completion.account['account_id'], [locator.message_id for locator in locators]))
    else:
        assert accepted_response is not None
        snapshots = receipt_snapshots
    assert len(snapshots) == 2 and all(row['accepted_input_ref'] is None for row in snapshots)
    balance = scope.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (scope.tenant_id,))[0]['credit_balance']
    completion.config.api_url = api.url
    accepted = accepted_response if accepted_response is not None else asyncio.run(
        accept_original_sealed_batch(completion, api, locators))
    identifier = accepted['current_runner_id']
    refs = [locator.stable_key for locator in locators]
    group_ref = accepted['batch_ref'] + ':user'
    stored = runner(scope.database, identifier)
    assert stored['source'] == 'wecom_kf' and stored['status'] == 'queued'
    assert stored['user_id'] is None and stored['session_id'] == scope.legacy_sid
    assert stored['attempt'] == 0 and stored['worker_id'] is None
    assert decoded(stored['checkpoint'])['source_initial_ref'] == refs[0]
    facts = scope.rows('SELECT input_ref,intent,provenance,phase,accepted_runner_id,current_runner_id '
        'FROM agent_runner_inputs WHERE input_ref=ANY(%s) ORDER BY receipt_seq', (refs,))
    assert [fact['input_ref'] for fact in facts] == refs
    assert [decoded(fact['intent'])['text'] for fact in facts] == list(texts)
    assert all(fact['phase'] == 'accepted' and fact['accepted_runner_id'] == fact['current_runner_id'] == identifier for fact in facts)
    assert len({decoded(fact['provenance'])['execution_binding'] for fact in facts}) == 1
    headers = api.headers(scope=scope, input_ref=refs[0])
    assert require_status(api.call('GET', '/v1/runners/' + identifier, headers=headers), 200)['runner']['status'] == 'queued'
    child, _ = start_original_worker(fleet, api, maximum=1)
    completion.record_resources('actual_resident_worker_started', [api.child, child])
    try:
        assert reply.arrived.wait(15), 'OWN_ORIGINAL_BATCH_MODEL_NOT_ARRIVED'
        running = runner(scope.database, identifier)
        assert running['status'] == 'running' and running['attempt'] == 1
        execution = decoded(running['checkpoint'])['execution']
        incoming = [message for message in execution['messages'] if message.get('role') == 'user'
            and (message.get('metadata') or {}).get('input_refs') == refs]
        assert len(incoming) == 1
        content = incoming[0]['content']
        assert isinstance(content, str) and content.startswith('[当前时间: ')
        time_header, submitted = content.split('\n\n', 1)
        assert time_header.endswith('年]') and submitted == '\n'.join(texts)
        metadata = incoming[0]['metadata']
        assert metadata['batch_ref'] == accepted['batch_ref'] and metadata['history_group_ref'] == group_ref
        assert [segment['input_ref'] for segment in metadata['merged_segments']] == refs
        assert [segment['text'] for segment in metadata['merged_segments']] == list(texts)
        assert len(provider.requests(model_marker)) == 1
        reply.release.set()
        finished = terminal(scope.database, identifier)
        fleet.assert_clean_exit(child)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        assert decoded(finished['result'])['output'] == reply.content
        public = require_status(api.call('GET', '/v1/runners/' + identifier, headers=headers), 200)['runner']
        assert public['status'] == 'completed' and public['result']['output'] == reply.content
        assert len(provider.requests(model_marker)) == 1 and not provider.errors
        records = scope.rows('SELECT record_id,total_token_count,credit_cost FROM chat_records WHERE record_id=%s', (finished['record_id'],))
        assert records == [{'record_id': finished['record_id'], 'total_token_count': 18, 'credit_cost': Decimal('0.01')}]
        receipts = scope.rows('SELECT phase,applied,record_id FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert receipts == [{'phase': 'observed', 'applied': True, 'record_id': finished['record_id']}]
        assert scope.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (scope.tenant_id,))[0]['credit_balance'] == balance - Decimal('0.01')
        history = scope.rows('SELECT message_id,content,metadata FROM channel_messages WHERE message_id=%s', (group_ref,))
        assert len(history) == 1 and history[0]['content'] == '\n'.join(texts)
        saved = decoded(history[0]['metadata'])
        assert saved['input_refs'] == refs and saved['history_group_ref'] == group_ref
        assert [segment['text'] for segment in saved['merged_segments']] == list(texts)
        assert scope.rows('SELECT 1 FROM channel_messages WHERE message_id=ANY(%s)', ([ref + ':user' for ref in refs],)) == []
        phases = scope.rows('SELECT phase FROM agent_runner_inputs WHERE input_ref=ANY(%s)', (refs,))
        assert len(phases) == 2 and all(row['phase'] == 'applied' for row in phases)
        current = scope.rows('SELECT message_id,payload,payload_digest,accepted_input_ref '
            'FROM wecom_kf_inbox WHERE account_id=%s AND message_id=ANY(%s) ORDER BY receive_seq',
            (completion.account['account_id'], [locator.message_id for locator in locators]))
        for before, after, ref in zip(snapshots, current, refs):
            assert {key: after[key] for key in ('message_id', 'payload', 'payload_digest')} == {key: before[key] for key in ('message_id', 'payload', 'payload_digest')}
            assert after['accepted_input_ref'] == ref
        claim = scope.rows('SELECT gate,owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,))
        assert claim == [{'gate': 'delivery', 'owner_runner_id': identifier}]
        return accepted, finished, public
    finally:
        reply.release.set()
