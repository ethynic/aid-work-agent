"""Prepared two original consumers, one real localhost POST operation."""
import asyncio
import threading

import pytest

from .kf_context_voice_fixtures import context_voice_receipts, context_receipts, voice_price, kf_scope
from .kf_admission_peer import StateReply
from .kf_voice_peer import AsrReply

pytestmark = pytest.mark.integration


def test_two_original_context_consumers_share_one_first_voice_operation_post_record_and_history(
        context_voice_receipts, voice_price):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_voice_repository import ContextVoiceRepository
    from src.channels.wecom_kf.context_worker import ContextWorker

    v, c, s = context_voice_receipts, context_voice_receipts.context, context_voice_receipts.scope
    locator, _ = v.receive_voice(employee=True)
    c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3})]
    release = threading.Event()
    transcript = 'Fictional one physical recognition observed by two original context consumers'
    reply = AsrReply(payload={'status': 20000000, 'result': transcript}, release=release)
    v.peer.replies[:] = [reply]
    balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))[0]['credit_balance']
    voice_repo = ContextVoiceRepository(s.database.connect)

    async def race():
        first = ContextWorker(c.config.wecom_kf, repository=ContextRepository(s.database.connect),
            client_factory=v.original_client)
        second = ContextWorker(c.config.wecom_kf, repository=ContextRepository(s.database.connect),
            client_factory=v.original_client)
        active = None
        try:
            active = asyncio.create_task(first.run_once())
            assert await asyncio.to_thread(reply.arrived.wait, 10)
            dispatched = await asyncio.to_thread(voice_repo.read, locator)
            operation = dispatched[4]
            assert operation['phase'] == 'started' and operation['authorized_epoch'] == 1
            assert operation['finance_pending'] is True and operation['cost'] is None
            # Actual first consumer is inside original Speech HTTP, no PG locks
            # held across this gate. The second uses its own real repository.
            duplicate = await asyncio.wait_for(second.run_once(), 10)
            assert duplicate['disposition'] == 'pending_asr' and duplicate['history_id'] is not None
            assert v.post_count() == 1
            while_held = await asyncio.to_thread(voice_repo.read, locator)
            assert while_held[4]['operation_ref'] == operation['operation_ref']
            assert while_held[4]['record_id'] == operation['record_id']
            assert while_held[4]['authorized_epoch'] == 1 and while_held[4]['phase'] == 'started'
            assert s.rows('SELECT 1 FROM chat_records WHERE record_id=%s', (operation['record_id'],)) == []
            assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s',
                (c.account['account_id'],)) == []
            placeholder = next(row for row in c.stored_history() if row['message_id'] == duplicate['history_id'])
            assert placeholder['content'] == '[人工客服] [语音消息]'
            assert placeholder['metadata']['pending_asr'] is True
            release.set()
            completed = await asyncio.wait_for(active, 15)
            assert completed['disposition'] == 'persisted' and completed['history_id'] == duplicate['history_id']
            assert await second.run_once() is None
            return completed, operation
        finally:
            release.set()
            # Original strong scopes must actually finish before fixture cleanup.
            if active is not None:
                await asyncio.gather(active, return_exceptions=True)
            await first.close()
            await second.close()
            assert not first.tasks and not first.voice.tasks and not second.tasks and not second.voice.tasks

    try:
        complete, operation = asyncio.run(race())
        prep = voice_repo.read(locator)[4]
        assert prep['phase'] == 'known' and prep['success'] is True and prep['transcript'] == transcript
        assert prep['operation_ref'] == operation['operation_ref'] and prep['record_id'] == operation['record_id']
        assert prep['authorized_epoch'] == 1 and prep['cost'] == voice_price['successful_call_credit']
        assert prep['finance_pending'] is False
        records = s.rows('SELECT record_id,status,asr_calls,credit_cost FROM chat_records WHERE session_id=%s', (s.legacy_sid,))
        assert records == [{'record_id': prep['record_id'], 'status': 'completed', 'asr_calls': 1,
            'credit_cost': voice_price['successful_call_credit']}]
        rows = [row for row in c.stored_history() if row['message_id'] == complete['history_id']]
        assert len(rows) == 1 and rows[0]['content'] == '[人工客服] [ASR识别结果] ' + transcript
        assert rows[0]['metadata']['pending_asr'] is False and rows[0]['metadata']['operation_ref'] == prep['operation_ref']
        intents = s.rows('SELECT * FROM wecom_kf_context_task_intents WHERE account_id=%s', (c.account['account_id'],))
        assert len(intents) == 2 and all(row['state'] == 'pending_adapter' for row in intents)
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [
            {'credit_balance': balance - voice_price['successful_call_credit']}]
        assert v.post_count() == 1 and len(c.platform.calls) == 1
        assert not v.peer.errors and not c.platform.errors
        assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
        assert voice_repo.project(locator)['history_id'] == complete['history_id'] and v.post_count() == 1
    finally:
        release.set()
        ids = s.rows('SELECT record_id FROM wecom_kf_context_voice_preparations WHERE account_id=%s AND tenant_id=%s',
            (c.account['account_id'], s.tenant_id))
        for row in ids:
            s.rows('DELETE FROM chat_records WHERE record_id=%s AND tenant_id=%s', (row['record_id'], s.tenant_id))
        s.rows('DELETE FROM wecom_kf_context_voice_preparations WHERE account_id=%s AND tenant_id=%s',
            (c.account['account_id'], s.tenant_id))
        s.rows('UPDATE tenants SET credit_balance=%s WHERE tenant_id=%s', (balance, s.tenant_id))
