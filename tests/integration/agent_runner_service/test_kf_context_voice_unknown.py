"""Prepared unknown/no replay and real late return preserving recall/clear."""
import asyncio
import threading

import pytest

from .kf_context_voice_fixtures import context_voice_receipts, context_receipts, voice_price, kf_scope
from .kf_context_fixtures import recall_event
from .kf_admission_peer import StateReply
from .kf_voice_peer import AsrReply

pytestmark = pytest.mark.integration


def test_original_started_unknown_visible_history_and_late_known_cas_respect_same_id_clear_and_recall(
        context_voice_receipts, voice_price, monkeypatch):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_voice_repository import ContextVoiceRepository
    from src.channels.wecom_kf.context_worker import ContextWorker

    v, c, s = context_voice_receipts, context_voice_receipts.context, context_voice_receipts.scope
    unknown, _ = v.receive_voice(send_time=100)
    late = None
    c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3}) for _ in range(2)]
    v.peer.replies[:] = [AsrReply(raw_body=b'{fictional-bad-json'),
        AsrReply(payload={'status': 20000000, 'result': 'Fictional real late response must respect recalled history'})]
    ready, release = threading.Event(), threading.Event()
    original_known = ContextVoiceRepository.known
    balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))[0]['credit_balance']
    voice_repo = ContextVoiceRepository(s.database.connect)

    def hold_late_original_result(self, operation, **result):
        if late is not None and operation['message_id'] == late.message_id:
            ready.set()
            assert release.wait(15), 'OWN_LATE_KNOWN_GATE_NOT_RELEASED'
        return original_known(self, operation, **result)

    monkeypatch.setattr(ContextVoiceRepository, 'known', hold_late_original_result)

    async def execute():
        worker = ContextWorker(c.config.wecom_kf, repository=ContextRepository(s.database.connect),
            client_factory=v.original_client)
        replay = ContextWorker(c.config.wecom_kf, repository=ContextRepository(s.database.connect),
            client_factory=v.original_client)
        active = None
        try:
            first = await worker.run_once()
            assert first['disposition'] == 'pending_asr'
            prep = (await asyncio.to_thread(voice_repo.read, unknown))[4]
            assert prep['phase'] == 'unknown' and prep['cost'] is None and prep['finance_pending'] is True
            assert s.rows('SELECT 1 FROM chat_records WHERE record_id=%s', (prep['record_id'],)) == []
            assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s', (c.account['account_id'],)) == []
            first_history = next(r for r in c.stored_history() if r['message_id'] == first['history_id'])
            assert first_history['content'] == '[语音消息]' and first_history['metadata']['pending_asr'] is True
            assert first_history['metadata']['asr_phase'] == 'unknown'
            # Projected unknown is not an unfinished selector candidate. A real
            # new worker must find no work, retain it and never repost. Only
            # after this check is the independent late receipt received.
            assert await replay.run_once() is None and v.post_count() == 1
            assert (await asyncio.to_thread(voice_repo.read, unknown))[4] == prep
            nonlocal late
            late, _ = await asyncio.to_thread(v.receive_voice, employee=True, send_time=101)
            active = asyncio.create_task(worker.run_once())
            assert await asyncio.to_thread(ready.wait, 10)
            pending = await replay.run_once()
            assert pending['disposition'] == 'pending_asr'
            placeholder = next(r for r in c.stored_history() if r['message_id'] == pending['history_id'])
            assert placeholder['content'] == '[人工客服] [语音消息]'
            assert placeholder['metadata']['pending_asr'] is True and placeholder['metadata']['asr_phase'] == 'started'
            assert v.post_count() == 2
            # Original encrypted/pulled user_recall_msg and real domain commit,
            # not a fabricated recalled flag or replacement history writer.
            recall, = await asyncio.to_thread(c.receive, recall_event(s, 'context_voice_recall_' + s.marker, late.message_id, send_time=102))
            recalled = await asyncio.to_thread(ContextRepository(s.database.connect).commit, recall)
            assert recalled['disposition'] == 'recalled'
            row = next(r for r in c.stored_history() if r['message_id'] == pending['history_id'])
            assert row['is_recalled'] is True
            release.set()
            await asyncio.wait_for(active, 15)
            known = (await asyncio.to_thread(voice_repo.read, late))[4]
            assert known['phase'] == 'known' and known['success'] is True
            assert known['history_projection'] == 'recalled'
            assert known['cost'] == voice_price['successful_call_credit'] and known['finance_pending'] is False
            recalled_after = next(r for r in c.stored_history() if r['message_id'] == pending['history_id'])
            assert recalled_after == row  # No overwrite or recall flag removal.
            assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s', (c.account['account_id'],)) == []
            # Explicit owned PG clear fixture, not a public clear API. Reuse
            # the persisted known result/record; do not re-deliver known again.
            s.rows('DELETE FROM channel_messages WHERE message_id=%s AND tenant_id=%s',
                (pending['history_id'], s.tenant_id))
            await asyncio.to_thread(voice_repo.project, late)
            cleared = (await asyncio.to_thread(voice_repo.read, late))[4]
            assert cleared['phase'] == 'known' and cleared['history_projection'] == 'cleared'
            assert cleared['record_id'] == known['record_id'] and cleared['cost'] == known['cost']
            assert s.rows('SELECT 1 FROM channel_messages WHERE message_id=%s', (pending['history_id'],)) == []
            assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s', (c.account['account_id'],)) == []
            assert (await asyncio.to_thread(voice_repo.read, unknown))[4] == prep
            return known, prep
        finally:
            release.set()
            if active is not None:
                await asyncio.gather(active, return_exceptions=True)
            await worker.close()
            await replay.close()
            assert not worker.tasks and not worker.voice.tasks and not replay.tasks and not replay.voice.tasks

    try:
        known, unknown_fact = asyncio.run(execute())
        records = s.rows('SELECT record_id,status,asr_calls,credit_cost FROM chat_records WHERE session_id=%s', (s.legacy_sid,))
        assert records == [{'record_id': known['record_id'], 'status': 'completed', 'asr_calls': 1,
            'credit_cost': voice_price['successful_call_credit']}]
        assert unknown_fact['record_id'] != known['record_id']
        assert v.post_count() == 2 and len(c.platform.calls) == 2
        assert not v.peer.errors and not c.platform.errors
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [
            {'credit_balance': balance - voice_price['successful_call_credit']}]
        assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
    finally:
        release.set()
        ids = s.rows('SELECT record_id FROM wecom_kf_context_voice_preparations WHERE account_id=%s AND tenant_id=%s',
            (c.account['account_id'], s.tenant_id))
        for row in ids:
            s.rows('DELETE FROM chat_records WHERE record_id=%s AND tenant_id=%s', (row['record_id'], s.tenant_id))
        s.rows('DELETE FROM wecom_kf_context_voice_preparations WHERE account_id=%s AND tenant_id=%s',
            (c.account['account_id'], s.tenant_id))
        s.rows('UPDATE tenants SET credit_balance=%s WHERE tenant_id=%s', (balance, s.tenant_id))
