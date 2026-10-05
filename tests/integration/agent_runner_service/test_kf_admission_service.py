"""Prepared native text developer normal, not a passed/frozen behavior record.

Real platform crypto/SDK/inbox -> actual internal HTTP -> original resident
Worker/Runtime/Engine/usage/finalizer. Only external platform/model IO is fictional.
Schema-specific cleanup/ledger oracles will be checked against actual handoff
before this sole developer node is allowed to run.
"""
import asyncio
import json
from decimal import Decimal
import threading

import pytest

from .conftest import wait_for
from .kf_ingress_fixtures import kf_scope, seed_intent, text_message
from .kf_ingress_peer import PullPage
from .kf_admission_peer import KfAdmissionPeer, StateReply
from .kf_admission_service import KfSourceApi, start_original_worker, verify_original_resources, safe_original_child_diagnostic
from .kf_admission_fixtures import cleanup_text_runners
from .provider import Reply
from .test_api import require_status
from .test_usage_storage import prices
from .test_worker import Workers, decoded, runner, terminal

pytestmark = pytest.mark.integration


def test_actual_trusted_text_source_http_accepts_and_resident_original_runner_finishes_once(
        kf_scope, service_processes, provider_peer, prices):
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_worker import KfIngressWorker
    from src.channels.wecom_kf.admission_worker import KfAdmissionWorker
    from src.config.settings import settings
    from src.services.agent_runner.source_client import SourceClient
    from src.services.agent_runner.source_receipts import SourceLocator

    scope = kf_scope
    release = threading.Event()
    reply = Reply(content='Fictional trusted text final output', release=release)
    marker = provider_peer.register(reply)
    message_id = 'trusted_text_' + scope.marker
    repository = KfIngressRepository(scope.database.connect)
    account = seed_intent(scope, repository)
    scope.peer.script(PullPage({'errcode': 0, 'has_more': 0, 'next_cursor': 'trusted_text_received',
        'msg_list': [text_message(scope, message_id, content=marker)]}))
    config = settings.agent_runner.model_copy(deep=True)
    config.enabled = True
    config.wecom_kf.enabled = True

    async def receive():
        ingress = KfIngressWorker(config.wecom_kf, repository, client_factory=scope.original_client)
        try:
            return await ingress.run_once()
        finally:
            await ingress.close()
    assert asyncio.run(receive()) == {'received': 1, 'has_more': False}
    received = scope.rows('SELECT * FROM wecom_kf_inbox WHERE config_id=%s', (scope.config_id,))
    assert len(received) == 1 and received[0]['message_id'] == message_id
    assert received[0]['namespace'] == 'sync' and received[0]['actor_id'] == scope.actor_id
    assert scope.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (scope.legacy_sid,)) == []
    platform = KfAdmissionPeer(scope.peer, scope.actor_id)
    # Real external state reads remain exact state1; observed request counts are
    # asserted below. This finite script does not fabricate any application proof.
    platform.states.extend(StateReply() for _ in range(16))
    api, fleet = None, None
    phase = 'received'
    try:
        api = KfSourceApi(service_processes, platform, provider_environment=provider_peer.environment)
        phase = 'service_ready'
        config.api_url = api.url
        fleet = Workers(service_processes, api, provider_peer, prices)
        verify_original_resources(fleet, api)
        locator = SourceLocator('wecom_kf', account['account_id'], 'sync', message_id)

        async def accept_original_source():
            client = SourceClient(config, token=api._credential)
            consumer = KfAdmissionWorker(config, connection_factory=scope.database.connect, client=client)
            try:
                first = await consumer.run_once()
                repeat = await client.accept(locator)
                return first, repeat
            finally:
                await consumer.close()
                await client.close()
        first, repeat = asyncio.run(accept_original_source())
        phase = 'accepted'
        assert first is not None and first['success'] is True and first['created'] is True
        assert repeat['success'] is True and repeat['created'] is False
        for key in ('input_ref', 'accepted_runner_id', 'current_runner_id'):
            assert isinstance(first[key], str) and first[key] == repeat[key]
        identifier = first['accepted_runner_id']
        assert identifier == first['current_runner_id']
        stored = runner(scope.database, identifier)
        assert stored['session_kind'] == 'channel' and stored['session_id'] == scope.legacy_sid
        assert stored['tenant_id'] == scope.tenant_id and stored['user_id'] is None
        intent = decoded(stored['input'])
        assert stored['source'] == 'wecom_kf' and intent['channel_user_id'] == scope.actor_id
        assert intent['channel_chat_id'] == scope.open_kfid and stored['profile_id'] == 'main'
        assert decoded(stored['actor_id']) == [scope.actor_id, scope.open_kfid]
        assert stored['status'] == 'queued' and stored['attempt'] == 0 and stored['worker_id'] is None
        assert decoded(stored['checkpoint'])['source_initial_ref'] == first['input_ref']
        source_fact = scope.rows('SELECT input_ref,source_key,accepted_runner_id,current_runner_id,phase FROM agent_runner_inputs WHERE input_ref=%s', (first['input_ref'],))
        assert source_fact == [{'input_ref': first['input_ref'], 'source_key': locator.stable_key,
            'accepted_runner_id': identifier, 'current_runner_id': identifier, 'phase': 'accepted'}]
        assert scope.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s',
            (locator.account_id, locator.namespace, locator.message_id)) == [{'accepted_input_ref': first['input_ref']}]
        headers = api.headers(scope=scope, input_ref=first['input_ref'])
        queued = require_status(api.call('GET', '/v1/runners/' + identifier, headers=headers), 200)['runner']
        assert queued['status'] == 'queued'
        phase = 'queued_query'
        child, _ = start_original_worker(fleet, api, maximum=1)
        assert reply.arrived.wait(15), 'Original resident worker did not dispatch the fictional model'
        phase = 'model_arrived'
        running = runner(scope.database, identifier)
        assert running['status'] == 'running' and running['attempt'] == 1
        # The server-only initial ref identifies the actual newly appended user
        # input, never an arbitrary old user in this same legacy session.
        execution = decoded(running['checkpoint'])['execution']
        initial = [(index, message) for index, message in enumerate(execution['messages'])
            if message.get('role') == 'user' and (message.get('metadata') or {}).get('input_ref') == first['input_ref']]
        assert len(initial) == 1 and initial[0][0] < execution['initial_len']
        assert marker in str(initial[0][1]['content'])
        assert initial[0][1]['metadata']['submitted_text'] == marker
        assert all(not (message.get('metadata') or {}).get('input_ref')
            for message in execution['messages'] if message.get('role') == 'user'
            and message.get('content') == 'Original fictional history')
        assert len(provider_peer.requests(marker)) == 1
        release.set()
        finished = terminal(scope.database, identifier)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        phase = 'terminal'
        fleet.assert_clean_exit(child)
        assert decoded(finished['result'])['output'] == reply.content
        result = require_status(api.call('GET', '/v1/runners/' + identifier, headers=headers), 200)['runner']
        assert result['status'] == 'completed' and result['result']['output'] == reply.content
        assert len(provider_peer.requests(marker)) == 1 and not provider_peer.errors
        phase = 'terminal_query'
        records = scope.rows('SELECT * FROM chat_records WHERE session_id=%s', (scope.legacy_sid,))
        assert len(records) == 1 and records[0]['record_id'] == finished['record_id']
        assert records[0]['prompt_tokens'] == 11 and records[0]['completion_tokens'] == 7
        assert records[0]['total_token_count'] == 18 and records[0]['credit_cost'] == Decimal('0.01')
        receipts = scope.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 1 and receipts[0]['phase'] == 'observed' and receipts[0]['applied']
        assert receipts[0]['record_id'] == finished['record_id']
        assert scope.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (scope.tenant_id,))[0]['credit_balance'] == Decimal('999.99')
        phase = 'fees_and_receipts'
        history = scope.rows('SELECT message_id,role,content,metadata FROM channel_messages WHERE session_id=%s ORDER BY created_at', (scope.legacy_sid,))
        assert len(history) == 3 and history[0]['content'] == 'Original fictional history'
        assert not (decoded(history[0]['metadata']) or {}).get('input_ref')
        assert history[1]['message_id'] == first['input_ref'] + ':user'
        assert history[1]['role'] == 'user' and history[1]['content'] == marker
        assert decoded(history[1]['metadata'])['input_ref'] == first['input_ref']
        assert scope.rows('SELECT phase FROM agent_runner_inputs WHERE input_ref=%s', (first['input_ref'],)) == [{'phase': 'applied'}]
        assert history[2]['role'] == 'assistant' and history[2]['content'] == reply.content
        assert all(decoded(row['metadata'])['runner_id'] == identifier for row in history[1:])
        final_execution = decoded(finished['checkpoint'])['execution']
        assert len([message for message in final_execution['messages']
            if message.get('role') == 'user' and (message.get('metadata') or {}).get('input_ref') == first['input_ref']]) == 1
        claim = scope.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,))
        assert len(claim) == 1 and claim[0]['gate'] == 'delivery' and claim[0]['session_id'] == scope.legacy_sid
        assert scope.rows('SELECT user_id FROM channel_sessions WHERE session_id=%s', (scope.legacy_sid,)) == [{'user_id': None}]
        assert platform.calls and all(call['credential_valid'] and call['account_actor_match'] for call in platform.calls)
        assert not platform.errors and not scope.peer.errors
        assert not any(call['path'] in ('/cgi-bin/kf/service_state/trans', '/cgi-bin/kf/send_msg') for call in platform.calls + scope.peer.calls)
    except BaseException as error:
        print('SAFE_KF_SOURCE_DIAGNOSTIC=' + json.dumps({'phase': phase,
            'exception_class': type(error).__name__,
            'original_children': safe_original_child_diagnostic(service_processes),
            'source_state_request_count': len(platform.calls),
            'model_request_count': len(provider_peer.requests(marker)),
            'own_runner_states': scope.rows('SELECT status,attempt,revision,settlement_status FROM agent_runners WHERE tenant_id=%s AND session_id=%s', (scope.tenant_id, scope.legacy_sid))}, sort_keys=True))
        raise
    finally:
        release.set()
        if fleet is not None:
            fleet.close()
        if api is not None:
            api.close()
        platform.close()
        # Original channel fixture closes platform threads and removes its exact
        # history/SID/config. Original wrapper finally drops this owned DB.
        cleanup_text_runners(scope)
