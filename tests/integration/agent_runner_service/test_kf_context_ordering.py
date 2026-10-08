"""Prepared actual service observation, two consumers and original first model.

A Context poll cursor starts just after C1 (explicit keyset scheduling DI).
Original source HTTP preparation is held at its fictional SDK response, not at
any SQL/Owner port. All classifications, public statuses and terminal caches
come from original repositories and the original resident Worker.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import threading

import pytest

from .kf_context_fixtures import context_receipts, kf_scope, human_text
from .kf_context_resources import record_context_resources
from .kf_admission_peer import StateReply
from .kf_admission_service import KfSourceApi, start_original_worker, verify_original_resources
from .kf_admission_fixtures import cleanup_text_runners
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, terminal

pytestmark = pytest.mark.integration


def test_real_earlier_unaccepted_source_defers_servicer_until_public_terminal_and_cache_survives_history_clear(
        context_receipts, service_processes, provider_peer, prices):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.services.agent_runner.source_client import SourceClient
    c, s = context_receipts, context_receipts.scope
    node='test_real_earlier_unaccepted_source_defers_servicer_until_public_terminal_and_cache_survives_history_clear'
    record_context_resources(s,service_processes,node,stage='allocated')
    source_release, model_release = threading.Event(), threading.Event()
    held_state = StateReply(release=source_release)
    reply=Reply(content='Fictional original earlier input terminal',release=model_release)
    marker=provider_peer.register(reply)
    c1, servicer=c.receive(human_text(s,'earlier_unaccepted_'+s.marker,marker),
        human_text(s,'later_servicer_'+s.marker,'Fictional later employee context',employee=True))
    c.platform.states[:] = [held_state,StateReply({'errcode':0,'service_state':3})]
    c.platform.states.extend(StateReply() for _ in range(20))
    api=KfSourceApi(service_processes,c.platform,provider_environment=provider_peer.environment)
    c.config.api_url=api.url
    client=SourceClient(c.config,token=api._credential)
    reads=[]
    original_read=client.read

    async def observed_original_read(locator):
        result=await original_read(locator)
        reads.append(locator.stable_key)
        return result
    client.read=observed_original_read  # transparent call counter, original values returned
    repository=ContextRepository(s.database.connect)
    fleet=None

    async def context_poll(*,after=0):
        worker=ContextWorker(c.config.wecom_kf,repository=repository,
            client_factory=c.original_client,source_client=client)
        worker.after=after
        try:
            return await worker.run_once()
        finally:
            await worker.close()
            assert not worker.tasks

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            accepted_future=pool.submit(api.call,'POST','/v1/source-inputs',
                json={**c1.value(),'client_request_id':c1.stable_key})
            try:
                assert held_state.arrived.wait(5)
                assert c.receipt(c1)['accepted_input_ref'] is None
                after=c.receipt(c1)['receipt_order']
                pending=asyncio.run(context_poll(after=after))
                assert pending['disposition']=='pending_history' and pending['history_id'] is None
                assert len(c.stored_history())==1 and reads==[]
                assert c.receipt(servicer)['accepted_input_ref'] is None
                assert s.rows('SELECT classification FROM wecom_kf_receipt_classifications WHERE account_id=%s AND message_id=%s',
                    (servicer.account_id,servicer.message_id))==[{'classification':'employee'}]
            finally:
                source_release.set()
            response=accepted_future.result(timeout=12)
        assert response.status_code==202
        acceptance=response.json()
        identifier,ref=acceptance['current_runner_id'],acceptance['input_ref']
        assert acceptance['created'] is True and runner(s.database,identifier)['status']=='queued'
        assert (asyncio.run(context_poll()))['disposition']=='pending_history'
        assert reads==[c1.stable_key] and len(c.stored_history())==1
        fleet=Workers(service_processes,api,provider_peer,prices)
        verify_original_resources(fleet,api)
        child,_=start_original_worker(fleet,api,maximum=1)
        assert reply.arrived.wait(15)
        requests=provider_peer.requests(marker)
        assert len(requests)==1
        users=[m['content'] for m in requests[0]['messages'] if m.get('role')=='user']
        assert all('Fictional later employee context' not in text for text in users)
        assert len(c.stored_history())==1  # no future S1 leaked into initial assembler history
        model_release.set()
        finished=terminal(s.database,identifier)
        fleet.assert_clean_exit(child)
        assert finished['status']=='completed' and finished['settlement_status']=='settled'
        assert s.rows('SELECT credit_cost,total_token_count FROM chat_records WHERE record_id=%s',
            (finished['record_id'],))==[{'credit_cost':Decimal('0.01'),'total_token_count':18}]
        persisted=asyncio.run(context_poll())
        assert persisted['disposition']=='persisted' and reads==[c1.stable_key,c1.stable_key]
        cache=s.rows('SELECT disposition,accepted_input_ref,completion_observation,payload_digest,route_id '
            'FROM wecom_kf_context_consumptions WHERE account_id=%s AND message_id=%s',
            (c1.account_id,c1.message_id))
        assert len(cache)==1 and cache[0]['disposition']=='source_terminal'
        assert cache[0]['accepted_input_ref']==ref
        assert cache[0]['completion_observation']['current_runner_id']==identifier
        assert cache[0]['completion_observation']['runner']['status']=='completed'
        # Exact own history-clear PG fixture, not a fake delivery ACK or receipt.
        # Domain terminal truth must remain independent of these history rows.
        s.rows('DELETE FROM channel_messages WHERE session_id=%s AND tenant_id=%s',
            (s.legacy_sid,s.tenant_id))
        next_servicer=c.receive(human_text(s,'after_clear_servicer_'+s.marker,
            'Fictional employee after history clear',employee=True))[0]
        next_poll=asyncio.run(context_poll())
        assert next_poll['disposition']=='persisted'
        assert len(c.stored_history())==1
        assert c.stored_history()[0]['metadata']['msgid']==next_servicer.message_id
        assert reads==[c1.stable_key,c1.stable_key]  # original immutable cache avoided another GET
        assert s.rows('SELECT disposition,accepted_input_ref,completion_observation,payload_digest,route_id '
            'FROM wecom_kf_context_consumptions WHERE account_id=%s AND message_id=%s',
            (c1.account_id,c1.message_id))==cache
        assert len(provider_peer.requests(marker))==1 and not provider_peer.errors
        assert s.rows('SELECT attempt,status FROM agent_runners WHERE runner_id=%s',
            (identifier,))==[{'attempt':1,'status':'completed'}]
        assert s.rows('SELECT gate,owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s',
            (s.legacy_sid,))==[{'gate':'delivery','owner_runner_id':identifier}]
        assert not c.platform.errors
    finally:
        source_release.set();model_release.set()
        if fleet is not None: fleet.close()
        asyncio.run(client.close())
        api.close()
        cleanup_text_runners(s)
        record_context_resources(s,service_processes,node,stage='body_finally')
