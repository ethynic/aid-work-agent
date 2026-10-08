"""Prepared real SDK/PG classification boundaries, no behavior run yet."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading

import pytest

from .kf_context_fixtures import context_receipts, kf_scope, human_text
from .kf_admission_peer import StateReply
from .kf_admission_service import KfSourceApi

pytestmark = pytest.mark.integration


def test_first_reliable_observation_after_transient_sdk_failure_can_classify_ai(context_receipts):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.channels.wecom_kf.ingress_auth import KfIngressError
    c, s = context_receipts, context_receipts.scope
    locator = c.receive(human_text(s, 'retryable_context_' + s.marker,
        'Fictional retryable current-state input'))[0]
    original = c.receipt(locator)
    c.platform.states[:] = [StateReply({'errcode': -1}), StateReply()]
    repository = ContextRepository(s.database.connect)

    async def classify():
        worker = ContextWorker(c.config.wecom_kf, repository=repository,
            client_factory=c.original_client)
        try:
            with pytest.raises(KfIngressError) as captured:
                await worker.run_once()
            assert captured.value.code == 'KF_CONTEXT_STATE_UNOBSERVED'
            assert s.rows('SELECT 1 FROM wecom_kf_receipt_classifications WHERE account_id=%s',
                (c.account['account_id'],)) == []
            assert c.receipt(locator) == original
            assert await worker.run_once() is None  # bounded keyset reaches its end
            return await worker.run_once()  # next poll wraps; original SDK again
        finally:
            await worker.close()
            assert not worker.tasks

    result = asyncio.run(classify())
    assert result == {'disposition': 'ai', 'history_id': None}
    rows = s.rows('SELECT classification,observed_state,classification_resolved,business_pending '
        'FROM wecom_kf_receipt_classifications WHERE account_id=%s AND namespace=%s AND message_id=%s',
        (locator.account_id, locator.namespace, locator.message_id))
    assert rows == [{'classification': 'ai', 'observed_state': 1,
        'classification_resolved': True, 'business_pending': False}]
    assert c.receipt(locator) == original
    assert len(c.stored_history()) == 1
    assert s.rows('SELECT 1 FROM wecom_kf_context_consumptions WHERE account_id=%s',
        (c.account['account_id'],)) == []
    assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',
        (c.account['account_id'],)) == []
    assert len(c.platform.calls) == 2 and not c.platform.errors


def test_reliable_human_ended_and_proven_conflict_never_upgrade_from_later_state_one(context_receipts):
    """Actual reliable non-AI SDK facts plus real received event conflicts."""
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.channels.wecom_kf.lifecycle_repository import StateObservation, route_scope
    c, s = context_receipts, context_receipts.scope
    states = (3, 4, 0, 2)
    locators = c.receive(*(human_text(s, 'fixed_context_' + str(state) + s.marker,
        'Fictional fixed context ' + str(state)) for state in states))
    c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': state}) for state in states]
    c.platform.states.extend(StateReply() for _ in states)
    repository = ContextRepository(s.database.connect)

    async def classify():
        worker = ContextWorker(c.config.wecom_kf, repository=repository,
            client_factory=c.original_client)
        try:
            return [await worker.run_once() for _ in states]
        finally:
            await worker.close()
    results = asyncio.run(classify())
    assert [row['disposition'] for row in results] == ['persisted', 'persisted',
        'pending_domain', 'pending_domain']
    original_classes = s.rows('SELECT * FROM wecom_kf_receipt_classifications '
        'WHERE account_id=%s ORDER BY message_id', (c.account['account_id'],))
    assert len(original_classes) == 4
    assert {row['classification'] for row in original_classes} == {'human', 'ended', 'unknown'}
    history = c.stored_history()
    intents = s.rows('SELECT * FROM wecom_kf_context_task_intents '
        'WHERE account_id=%s ORDER BY task_id', (c.account['account_id'],))
    assert len(history) == 3 and len(intents) == 4

    # These are actual pull receipts, not manufactured classification rows.
    # Same-second different state and backwards provider time are proven
    # conflicts; event receive order alone is never treated as a causal clock.
    def status_event(suffix, state, sent):
        return {'msgid': suffix+s.marker, 'external_userid':s.actor_id,
            'open_kfid':s.open_kfid, 'origin':0, 'send_time':sent, 'msgtype':'event',
            'event':{'event_type':'session_status_change','external_userid':s.actor_id,
                'open_kfid':s.open_kfid,'session_status':state}}
    events = c.receive(status_event('event_baseline_',3,200),
        status_event('event_same_second_',4,200),status_event('event_backwards_',1,199))
    event_results = [repository.commit(locator) for locator in events]
    assert [row['disposition'] for row in event_results] == [
        'pending_business','pending_scope','pending_scope']
    event_classes = s.rows('SELECT message_id,event_state,classification_resolved,business_pending '
        'FROM wecom_kf_receipt_classifications WHERE account_id=%s AND classification=%s',
        (c.account['account_id'],'event'))
    by_id = {row['message_id']:row for row in event_classes}
    assert by_id[events[0].message_id]['classification_resolved'] is True
    assert all(by_id[item.message_id]['classification_resolved'] is False for item in events[1:])
    assert all(row['business_pending'] for row in event_classes)
    original_classes = s.rows('SELECT * FROM wecom_kf_receipt_classifications WHERE account_id=%s '
        'ORDER BY message_id',(c.account['account_id'],))
    c.platform.states.extend(StateReply() for _ in events)

    async def reliable_later_one(locator):
        current, inbox, route = repository.read(locator)
        client = c.original_client(current.proof.corp_id, current.secret)
        client.enable_ingress_mode(c.config.wecom_kf.page_bytes)
        try:
            reply = await client.get_service_state(current.proof.open_kfid, inbox['actor_id'])
            observed = datetime.now(timezone.utc)
        finally:
            await client.close()
        assert type(reply['errcode']) is int and reply['errcode'] == 0
        assert type(reply['service_state']) is int and reply['service_state'] == 1
        return StateObservation(reply['service_state'], observed, current.proof.config_version,
            inbox['payload_digest'], route_scope(route))

    for locator, prior in zip(locators, results):
        observation = asyncio.run(reliable_later_one(locator))
        repository.classify(locator, observation)
        again = repository.commit(locator)
        assert again['disposition'] == prior['disposition']
        assert again.get('history_id') == prior.get('history_id')
    assert s.rows('SELECT * FROM wecom_kf_receipt_classifications WHERE account_id=%s '
        'ORDER BY message_id', (c.account['account_id'],)) == original_classes
    assert c.stored_history() == history
    assert s.rows('SELECT * FROM wecom_kf_context_task_intents WHERE account_id=%s '
        'ORDER BY task_id', (c.account['account_id'],)) == intents
    assert all(c.receipt(locator)['accepted_input_ref'] is None for locator in locators)
    assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
    for locator in events:
        repository.classify(locator,asyncio.run(reliable_later_one(locator)))
        assert repository.commit(locator)['disposition'] == event_results[events.index(locator)]['disposition']
    assert s.rows('SELECT * FROM wecom_kf_receipt_classifications WHERE account_id=%s '
        'ORDER BY message_id',(c.account['account_id'],)) == original_classes
    assert len(c.platform.calls) == 11 and not c.platform.errors


def test_original_inbox_lock_classification_and_source_acceptance_have_one_domain_winner(
        context_receipts, service_processes, provider_peer):
    """The real acceptance wins during delayed original SDK classification.

    This delay changes only fictional platform HTTP response timing. It cannot
    alter the inbox winner, SourceProvider proof or any returned SDK authority.
    The reciprocal branch uses actual SDK classification first and a real
    second HTTP acceptance attempt; no winner row is supplied by the fixture.
    """
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from .kf_admission_fixtures import cleanup_text_runners
    from .provider import Reply
    c, s = context_receipts, context_receipts.scope
    no_model_marker=provider_peer.register(Reply(content='Unexpected fictional model dispatch'),
        marker='Fictional concurrent context source')
    locator = c.receive(human_text(s, 'accept_class_race_' + s.marker,
        no_model_marker))[0]
    release = threading.Event()
    delayed_human = StateReply({'errcode': 0, 'service_state': 3}, release=release)
    c.platform.states[:] = [delayed_human, StateReply()]
    api = KfSourceApi(service_processes, c.platform,
        provider_environment=provider_peer.environment)
    repository = ContextRepository(s.database.connect)

    async def classify():
        worker = ContextWorker(c.config.wecom_kf, repository=repository,
            client_factory=c.original_client)
        try:
            return await worker.run_once()
        finally:
            await worker.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(lambda: asyncio.run(classify()))
            try:
                assert delayed_human.arrived.wait(5)
                # This actual HTTP service is the only acceptance owner. No
                # classification/winner/Runner row is manufactured by the test.
                response = api.call('POST', '/v1/source-inputs',
                    json={**locator.value(), 'client_request_id': locator.stable_key})
                assert response.status_code == 202
                accepted = response.json()
                assert accepted['success'] is True and accepted['created'] is True
                assert accepted['accepted_runner_id'] == accepted['current_runner_id']
                assert c.receipt(locator)['accepted_input_ref'] == accepted['input_ref']
                release.set()
                result = pending.result(timeout=12)
                assert result == {'disposition': 'accepted_source'}
            finally:
                release.set()
        facts = s.rows('SELECT input_ref,phase,provenance FROM agent_runner_inputs '
            'WHERE input_ref=%s', (accepted['input_ref'],))
        assert len(facts) == 1 and facts[0]['phase'] == 'accepted'
        assert facts[0]['provenance']['route_id'] == c.receipt(locator)['route_id']
        assert len(c.stored_history()) == 1
        assert s.rows('SELECT 1 FROM wecom_kf_context_consumptions WHERE account_id=%s',
            (c.account['account_id'],)) == []
        assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',
            (c.account['account_id'],)) == []
        # Genuine new acceptance installs AI classification; the delayed human
        # observation cannot overwrite it or create a second domain history.
        fixed = s.rows('SELECT classification FROM wecom_kf_receipt_classifications '
            'WHERE account_id=%s', (c.account['account_id'],))
        assert fixed == [{'classification': 'ai'}]
        assert len(c.platform.calls) == 2 and not c.platform.errors
        assert provider_peer.requests(no_model_marker) == []
        # Conversely, a fixed human classification is authoritative even when
        # the later original API SDK returns state1. It must not append input.
        human = c.receive(human_text(s,'human_winner_'+s.marker,
            'Fictional fixed human winner'))[0]
        c.platform.states.extend([StateReply({'errcode':0,'service_state':3}),StateReply()])
        original_human = c.receipt(human)
        projected = asyncio.run(classify())
        assert projected['disposition']=='pending_history'  # earlier real accepted Runner is still queued
        before_history = c.stored_history()
        later = api.call('POST','/v1/source-inputs',
            json={**human.value(),'client_request_id':human.stable_key})
        assert later.status_code==409 and later.json()['error']=='SOURCE_BINDING_CHANGED'
        assert c.receipt(human)==original_human and c.receipt(human)['accepted_input_ref'] is None
        assert c.stored_history()==before_history
        assert s.rows("SELECT input_ref FROM agent_runner_inputs WHERE provenance->>'config_id'=%s",
            (s.config_id,)) == [{'input_ref':accepted['input_ref']}]
        assert len(s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',
            (c.account['account_id'],)))==0
        assert len(c.platform.calls)==4 and not c.platform.errors
    finally:
        release.set()
        api.close()
        cleanup_text_runners(s)
