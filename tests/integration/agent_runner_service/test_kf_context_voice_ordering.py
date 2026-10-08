"""Real source service/current pointer gates both known and unknown history.

The original Context keyset cursor is positioned after the earlier AI receipt
(scheduling DI); original source acceptance, SDK state, resident Worker/model,
public GET and terminal cache remain actual. Provider and ASR use localhost.
"""
import asyncio
from decimal import Decimal
import threading

import pytest

from .kf_context_voice_fixtures import context_voice_receipts, context_receipts, voice_price, kf_scope
from .kf_context_fixtures import human_text
from .kf_admission_peer import StateReply
from .kf_voice_peer import AsrReply
from .kf_admission_service import KfSourceApi, start_original_worker, verify_original_resources
from .kf_admission_fixtures import cleanup_text_runners
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, terminal

pytestmark=pytest.mark.integration


def test_original_public_source_terminal_gate_orders_each_voice_history_projection_without_losing_known_owner(
        context_voice_receipts, voice_price, service_processes, provider_peer, prices):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_voice_repository import ContextVoiceRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.services.agent_runner.source_client import SourceClient
    v,c,s=context_voice_receipts,context_voice_receipts.context,context_voice_receipts.scope
    release=threading.Event()
    reply=Reply(content='Fictional earlier source completes before later voice history',release=release)
    marker=provider_peer.register(reply)
    first=c.receive(human_text(s,'voice_earlier_source_'+s.marker,marker,send_time=100))[0]
    known,_=v.receive_voice(employee=True,send_time=101)
    unknown,_=v.receive_voice(send_time=102)
    c.platform.states[:]=[StateReply({'errcode':0,'service_state':1}),
        StateReply({'errcode':0,'service_state':3}),StateReply({'errcode':0,'service_state':3})]
    c.platform.states.extend(StateReply() for _ in range(20))
    transcript='Fictional known voice must wait for the original public terminal source'
    v.peer.replies[:]=[AsrReply(payload={'status':20000000,'result':transcript}),AsrReply(raw_body=b'{fictional-bad-json')]
    api=KfSourceApi(service_processes,c.platform,provider_environment=provider_peer.environment)
    c.config.api_url=api.url
    client=SourceClient(c.config,token=api._credential)
    repository=ContextRepository(s.database.connect)
    voice_repo=ContextVoiceRepository(s.database.connect)
    reads=[]
    original_read=client.read

    async def count_original_read(locator):
        result=await original_read(locator)
        reads.append((locator.stable_key,result['runner']['status']))
        return result

    client.read=count_original_read
    balance=s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',(s.tenant_id,))[0]['credit_balance']
    fleet=None

    async def poll(*,after=0):
        worker=ContextWorker(c.config.wecom_kf,repository=repository,
            client_factory=v.original_client,source_client=client)
        worker.after=after
        try:
            return await worker.run_once()
        finally:
            await worker.close()
            assert not worker.tasks and not worker.voice.tasks

    try:
        response=api.call('POST','/v1/source-inputs',json={**first.value(),'client_request_id':first.stable_key})
        assert response.status_code==202
        accepted=response.json(); identifier,ref=accepted['current_runner_id'],accepted['input_ref']
        assert accepted['created'] is True and runner(s.database,identifier)['status']=='queued'
        after=c.receipt(first)['receipt_order']
        before=c.stored_history()
        # Both actual result kinds are retained even though the earlier queued
        # source prevents their history (including unknown placeholders).
        known_pending=asyncio.run(poll(after=after))
        unknown_pending=asyncio.run(poll(after=c.receipt(known)['receipt_order']))
        assert known_pending['disposition']==unknown_pending['disposition']=='pending_history'
        assert known_pending['history_id'] is unknown_pending['history_id'] is None
        assert c.stored_history()==before
        known_fact=voice_repo.read(known)[4]; unknown_fact=voice_repo.read(unknown)[4]
        assert known_fact['phase']=='known' and known_fact['transcript']==transcript
        assert known_fact['cost']==voice_price['successful_call_credit'] and known_fact['finance_pending'] is False
        assert unknown_fact['phase']=='unknown' and unknown_fact['cost'] is None and unknown_fact['finance_pending'] is True
        assert v.post_count()==2 and all(status=='queued' for _,status in reads)
        assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',(c.account['account_id'],))==[]
        fleet=Workers(service_processes,api,provider_peer,prices)
        verify_original_resources(fleet,api)
        child,_=start_original_worker(fleet,api,maximum=1)
        assert reply.arrived.wait(15)
        requests=provider_peer.requests(marker)
        assert len(requests)==1
        users=[m['content'] for m in requests[0]['messages'] if m.get('role')=='user']
        assert all(transcript not in text and '[语音消息]' not in text for text in users)
        assert c.stored_history()==before
        release.set()
        finished=terminal(s.database,identifier);fleet.assert_clean_exit(child)
        assert finished['status']=='completed' and finished['settlement_status']=='settled'
        projected=asyncio.run(poll(after=after))
        assert projected['disposition']=='persisted'
        unknown_projected=asyncio.run(poll(after=c.receipt(known)['receipt_order']))
        assert unknown_projected['disposition']=='pending_asr'
        rows={row['message_id']:row for row in c.stored_history()}
        assert rows[projected['history_id']]['content']=='[人工客服] [ASR识别结果] '+transcript
        assert rows[unknown_projected['history_id']]['content']=='[语音消息]'
        assert rows[unknown_projected['history_id']]['metadata']['pending_asr'] is True
        assert reads[-1]==(first.stable_key,'completed')
        cache=s.rows('SELECT * FROM wecom_kf_context_consumptions WHERE account_id=%s AND message_id=%s',
            (first.account_id,first.message_id))
        assert len(cache)==1 and cache[0]['disposition']=='source_terminal'
        assert cache[0]['accepted_input_ref']==ref
        assert cache[0]['completion_observation']['current_runner_id']==identifier
        assert v.post_count()==2 and voice_repo.read(unknown)[4]['phase']=='unknown'
        assert voice_repo.read(known)[4]['record_id']==known_fact['record_id']
        assert s.rows('SELECT record_id,asr_calls,credit_cost FROM chat_records WHERE source_type=%s AND session_id=%s',
            ('wecom_kf_human_asr',s.legacy_sid))==[{'record_id':known_fact['record_id'],'asr_calls':1,'credit_cost':voice_price['successful_call_credit']}]
        assert s.rows('SELECT credit_cost,total_token_count FROM chat_records WHERE record_id=%s',
            (finished['record_id'],))==[{'credit_cost':Decimal('.01'),'total_token_count':18}]
        count=len(reads)
        # Owned SQL clear DI: completion cache stays independent of chat rows;
        # persistent known result may not create a replacement history.
        s.rows('DELETE FROM channel_messages WHERE tenant_id=%s AND session_id=%s',(s.tenant_id,s.legacy_sid))
        cleared=voice_repo.project(known)  # Persisted known read/project port, not a fake selector candidate.
        assert cleared['history_id']==projected['history_id']
        assert voice_repo.read(known)[4]['history_projection']=='cleared'
        assert c.stored_history()==[] and len(reads)==count and v.post_count()==2
        assert s.rows('SELECT * FROM wecom_kf_context_consumptions WHERE account_id=%s AND message_id=%s',
            (first.account_id,first.message_id))==cache
        assert len(provider_peer.requests(marker))==1 and not provider_peer.errors and not v.peer.errors and not c.platform.errors
        assert s.rows('SELECT gate,owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s',
            (s.legacy_sid,))==[{'gate':'delivery','owner_runner_id':identifier}]
    finally:
        release.set()
        if fleet is not None: fleet.close()
        asyncio.run(client.close());api.close()
        cleanup_text_runners(s)
        records=s.rows('SELECT record_id FROM wecom_kf_context_voice_preparations WHERE account_id=%s',(c.account['account_id'],))
        for row in records:
            s.rows('DELETE FROM chat_records WHERE record_id=%s AND tenant_id=%s',(row['record_id'],s.tenant_id))
        s.rows('DELETE FROM wecom_kf_context_voice_preparations WHERE account_id=%s',(c.account['account_id'],))
        s.rows('UPDATE tenants SET credit_balance=%s WHERE tenant_id=%s',(balance,s.tenant_id))
        v.persist_resource_observation('ordering_body_finally')
