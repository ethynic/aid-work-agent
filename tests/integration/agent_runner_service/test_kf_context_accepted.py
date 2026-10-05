"""Prepared compatibility of authoritative accepted facts lacking new tables.

The old-data shape is an explicit PG legacy fixture: acceptance is first made
by the real source HTTP/SDK owner, then only this new classification row is
removed. No Runner/provenance/claim/execution/fee fact is manufactured. This is
not claimed as execution of archived old application code.
"""
import asyncio
from datetime import datetime
from decimal import Decimal
import threading

import pytest

from .kf_context_fixtures import context_receipts, kf_scope, human_text, recall_event
from .kf_context_resources import record_context_resources
from .kf_admission_fixtures import cleanup_text_runners
from .kf_admission_peer import StateReply
from .kf_admission_service import KfSourceApi, start_original_worker, verify_original_resources
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, decoded, terminal

pytestmark = pytest.mark.integration


def test_original_accepted_text_without_new_classification_keeps_execution_read_cancel_and_provenance(
        context_receipts, service_processes, provider_peer, prices):
    from src.services.agent_runner.source_client import SourceClient
    from src.services.agent_runner.source_receipts import SourceLocator
    c, s = context_receipts, context_receipts.scope
    node='test_original_accepted_text_without_new_classification_keeps_execution_read_cancel_and_provenance'
    record_context_resources(s,service_processes,node,stage='allocated')
    release = threading.Event()
    reply = Reply(content='Fictional accepted context compatibility result', release=release)
    marker = provider_peer.register(reply)
    locator = c.receive(human_text(s, 'old_accepted_text_' + s.marker, marker))[0]
    c.platform.states[:] = [StateReply() for _ in range(16)]
    api = KfSourceApi(service_processes, c.platform, provider_environment=provider_peer.environment)
    readonly, fleet = None, None
    try:
        response = api.call('POST', '/v1/source-inputs',
            json={**locator.value(), 'client_request_id': locator.stable_key})
        assert response.status_code == 202
        accepted = response.json()
        identifier, ref = accepted['current_runner_id'], accepted['input_ref']
        assert accepted['created'] is True and identifier == accepted['accepted_runner_id']
        # Precise pre-Context classification absence; accepted authoritative
        # provenance and its hash/link remain the original service's values.
        s.rows('DELETE FROM wecom_kf_receipt_classifications WHERE account_id=%s '
            'AND namespace=%s AND message_id=%s',
            (locator.account_id, locator.namespace, locator.message_id))
        inbox_before = c.receipt(locator)
        input_before = s.rows('SELECT * FROM agent_runner_inputs WHERE input_ref=%s', (ref,))[0]
        owner_before = runner(s.database, identifier)
        assert input_before['phase'] == 'accepted'
        s.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s', (s.tenant_id,))
        readonly = KfSourceApi(service_processes, c.platform,
            provider_environment=provider_peer.environment, native_enabled=False)
        c.config.api_url = readonly.url

        async def read_original():
            client = SourceClient(c.config, token=readonly._credential)
            try:
                return await client.read(locator)
            finally:
                await client.close()
        sdk_before = len(c.platform.calls)
        observed = asyncio.run(read_original())
        assert observed['success'] is True and observed['created'] is False
        assert observed['locator'] == locator.value()
        assert observed['client_request_id'] == locator.stable_key
        assert observed['input_ref'] == ref and observed['current_runner_id'] == identifier
        assert observed['runner']['status'] == 'queued'
        assert observed['runner']['session'] == {'kind': 'channel', 'session_id': s.legacy_sid}
        assert datetime.fromisoformat(observed['observed_at']).tzinfo is not None
        assert set(observed['runner']) == {'runner_id','session','profile_id','status',
            'settlement_status','revision','view_revision','control_revision','resume_requested'}
        for params in (list(locator.value().items())+[('unexpected','scope')],
                list(locator.value().items())+[('message_id',locator.message_id)]):
            invalid=readonly.call('GET','/v1/source-inputs',params=params)
            assert invalid.status_code==422
            assert invalid.json()['error']=='SOURCE_LOCATOR_QUERY_INVALID'
        assert readonly.call('GET', '/v1/source-inputs', headers={},
            params=locator.value()).status_code == 401
        missing = SourceLocator('wecom_kf', locator.account_id, 'sync', 'missing_' + s.marker)
        assert readonly.call('GET', '/v1/source-inputs', params=missing.value()).status_code == 404
        assert c.receipt(locator) == inbox_before
        assert s.rows('SELECT * FROM agent_runner_inputs WHERE input_ref=%s', (ref,)) == [input_before]
        assert runner(s.database, identifier) == owner_before
        assert s.rows('SELECT 1 FROM wecom_kf_receipt_classifications WHERE account_id=%s',
            (locator.account_id,)) == []
        assert len(c.platform.calls) == sdk_before  # read has no execute SDK/credit gate
        s.rows('UPDATE tenants SET credit_balance=1000 WHERE tenant_id=%s', (s.tenant_id,))
        fleet = Workers(service_processes, api, provider_peer, prices)
        verify_original_resources(fleet, api)
        child, _ = start_original_worker(fleet, api, maximum=1)
        assert reply.arrived.wait(15)
        current = runner(s.database, identifier)
        assert current['status'] == 'running' and current['attempt'] == 1
        sdk_at_dispatch = len(c.platform.calls)
        headers = api.headers(scope=s, input_ref=ref)
        assert api.call('GET', '/v1/runners/' + identifier, headers=headers).status_code == 200
        cancelled = api.call('POST', '/v1/runners/' + identifier + '/cancel', headers=headers)
        assert cancelled.status_code == 200
        assert runner(s.database, identifier)['cancel_requested'] is True
        assert len(c.platform.calls) == sdk_at_dispatch
        release.set()
        finished = terminal(s.database, identifier)
        assert finished['status'] == 'cancelled' and finished['settlement_status'] == 'settled'
        fleet.assert_clean_exit(child)
        after = s.rows('SELECT * FROM agent_runner_inputs WHERE input_ref=%s', (ref,))[0]
        for key in ('provenance','intent','intent_digest','source_key','input_ref','locator'):
            assert after[key] == input_before[key]
        assert c.receipt(locator)['payload_digest'] == inbox_before['payload_digest']
        assert c.receipt(locator)['accepted_input_ref'] == ref
        assert s.rows('SELECT 1 FROM wecom_kf_receipt_classifications WHERE account_id=%s',
            (locator.account_id,)) == []
        assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',
            (locator.account_id,)) == []
        receipts = s.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 1 and receipts[0]['phase'] == 'observed' and receipts[0]['applied']
        records = s.rows('SELECT * FROM chat_records WHERE record_id=%s', (finished['record_id'],))
        assert len(records) == 1 and records[0]['total_token_count'] == 18
        assert records[0]['credit_cost'] == Decimal('0.01')
        assert len(provider_peer.requests(marker)) == 1 and not provider_peer.errors
        initial = [row for row in decoded(finished['checkpoint'])['execution']['messages']
            if row.get('role') == 'user' and (row.get('metadata') or {}).get('input_ref') == ref]
        assert len(initial) == 1
        # Actual recall of an already accepted/physically executed target is
        # a domain observation, never authority to alter execution or fee facts.
        from src.channels.wecom_kf.context_repository import ContextRepository
        owner_snapshot=runner(s.database,identifier)
        receipt_snapshot=s.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s',(identifier,))
        input_snapshot=s.rows('SELECT * FROM agent_runner_inputs WHERE input_ref=%s',(ref,))
        history_snapshot=c.stored_history()
        recall=c.receive(recall_event(s,'accepted_recall_'+s.marker,locator.message_id))[0]
        observed_recall=ContextRepository(s.database.connect).commit(recall)
        assert observed_recall['disposition']=='accepted_target'
        assert observed_recall['target_message_id']==locator.message_id
        assert runner(s.database,identifier)==owner_snapshot
        assert s.rows('SELECT * FROM agent_runner_inputs WHERE input_ref=%s',(ref,))==input_snapshot
        assert s.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s',(identifier,))==receipt_snapshot
        assert c.stored_history()==history_snapshot and len(provider_peer.requests(marker))==1
    finally:
        release.set()
        if fleet is not None:
            fleet.close()
        if readonly is not None:
            readonly.close()
        api.close()
        cleanup_text_runners(s)
        record_context_resources(s,service_processes,node,stage='body_finally')


# These accepted fixtures are reused byte-for-byte; all new assertions remain in
# this Context-only module. The old shape is absence of the new classification,
# while known ASR and its authorized fee owner were produced by original IO.
from .kf_voice_fixtures import voice_scope, voice_price, original_timestamped_input
from .kf_voice_peer import AsrReply
from pathlib import Path
import secrets


def test_original_known_voice_without_new_classification_keeps_same_preparation_owner_and_no_second_asr(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    v, s = voice_scope, voice_scope.scope
    node='test_original_known_voice_without_new_classification_keeps_same_preparation_owner_and_no_second_asr'
    record_context_resources(s,service_processes,node,stage='allocated')
    release = threading.Event()
    reply = Reply(content='Fictional legacy known voice final response', release=release)
    marker = provider_peer.register(reply)
    transcript = marker+' fictional recognized legacy voice compatibility sentence'
    v.peer.replies.append(AsrReply(payload={'status':20000000,'result':transcript}))
    locator, _ = v.receive_voice()
    accepted = v.text.accept(locator)
    identifier, ref = accepted['current_runner_id'], accepted['input_ref']
    original_input = s.rows('SELECT * FROM agent_runner_inputs WHERE input_ref=%s',(ref,))[0]
    fleet = Workers(service_processes,v.api,provider_peer,prices)
    try:
        verify_original_resources(fleet,v.api)
        worker_id='context_known_voice_worker_'+secrets.token_hex(8)
        child=service_processes.start([str(Path(__file__).with_name('kf_voice_process.py')),
            'worker','--worker-id',worker_id,'--max-tasks','1'],
            environment={**fleet.environment,**v.api.environment,**v.asr_environment(),
                'QWEN_MODEL_CODE':fleet.models[0]},private_working_directory=True)
        fleet.children.append((child,worker_id))
        assert reply.arrived.wait(15)
        known=s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s',(ref,))
        assert len(known)==1 and known[0]['phase']=='known'
        assert known[0]['fee_owner_runner_id']==identifier and known[0]['authorized_attempt']==1
        assert len([row for row in v.peer.calls if row['kind']=='asr'])==1
        # Explicit legacy schema-shape DI after real known Speech completion;
        # no preparation, price, Runner, source digest or provenance is changed.
        s.rows('DELETE FROM wecom_kf_receipt_classifications WHERE account_id=%s AND namespace=%s AND message_id=%s',
            (locator.account_id,locator.namespace,locator.message_id))
        sdk_before=len(v.state_peer.calls)
        s.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s',(s.tenant_id,))
        read=v.api.call('GET','/v1/source-inputs',params=locator.value())
        assert read.status_code==200 and read.json()['current_runner_id']==identifier
        assert read.json()['runner']['status']=='running'
        headers=v.api.headers(scope=s,input_ref=ref)
        assert v.api.call('GET','/v1/runners/'+identifier,headers=headers).status_code==200
        cancelled=v.api.call('POST','/v1/runners/'+identifier+'/cancel',headers=headers)
        assert cancelled.status_code==200 and runner(s.database,identifier)['cancel_requested']
        assert len(v.state_peer.calls)==sdk_before  # read/cancel never demand paid execute permission
        s.rows('UPDATE tenants SET credit_balance=1000 WHERE tenant_id=%s',(s.tenant_id,))
        release.set()
        finished=terminal(s.database,identifier)
        fleet.assert_clean_exit(child)
        assert finished['status']=='cancelled' and finished['settlement_status']=='settled'
        assert s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s',(ref,))==known
        after=s.rows('SELECT * FROM agent_runner_inputs WHERE input_ref=%s',(ref,))[0]
        for key in ('provenance','intent','intent_digest','source_key','input_ref','locator'):
            assert after[key]==original_input[key]
        receipt=s.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s',(identifier,))
        assert len(receipt)==2 and {row['owner'] for row in receipt}=={'asr','llm'}
        assert all(row['phase']=='observed' and row['applied'] for row in receipt)
        records=s.rows('SELECT * FROM chat_records WHERE record_id=%s',(finished['record_id'],))
        assert len(records)==1 and records[0]['user_message']=='[ASR识别结果] '+transcript
        assert records[0]['asr_calls']==1 and records[0]['total_token_count']==18
        assert records[0]['credit_cost']==voice_price['successful_call_credit']+Decimal('0.01')
        incoming=[m for m in decoded(finished['checkpoint'])['execution']['messages']
            if m.get('role')=='user' and (m.get('metadata') or {}).get('input_ref')==ref]
        assert len(incoming)==1
        original_timestamped_input(incoming[0]['content'],transcript)
        repeat=v.text.accept(locator)
        assert repeat['created'] is False and repeat['input_ref']==ref
        assert repeat['current_runner_id']==identifier
        assert s.rows('SELECT 1 FROM wecom_kf_receipt_classifications WHERE account_id=%s',
            (locator.account_id,))==[]
        assert len([row for row in v.peer.calls if row['kind']=='asr'])==1
        assert len(provider_peer.requests(marker))==1 and not provider_peer.errors
        assert not v.peer.errors and not v.state_peer.errors
    finally:
        release.set()
        fleet.close()
        for table in ('wecom_kf_context_task_intents','wecom_kf_context_consumptions','wecom_kf_receipt_classifications'):
            s.rows('DELETE FROM '+table+' WHERE account_id=%s AND tenant_id=%s',
                (locator.account_id,s.tenant_id))
        record_context_resources(s,service_processes,node,stage='body_finally')
