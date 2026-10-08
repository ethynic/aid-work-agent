"""Prepared zero-cost known Voice semantics, original local HTTP/PG only."""
import asyncio
from decimal import Decimal

import pytest

from .kf_context_voice_fixtures import context_voice_receipts, context_receipts, voice_price, kf_scope
from .kf_admission_peer import StateReply
from .kf_voice_peer import AsrReply

pytestmark = pytest.mark.integration


def test_trusted_recognition_and_explicit_known_failure_keep_zero_billable_asr_calls(
        context_voice_receipts, voice_price):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_voice_repository import ContextVoiceRepository
    from src.channels.wecom_kf.context_worker import ContextWorker

    v, c, s = context_voice_receipts, context_voice_receipts.context, context_voice_receipts.scope
    recognition_text = 'Fictional trusted page recognition with a complete sentence'
    null_price_text = 'Fictional successfully recognized speech with a NULL configured price'
    recognition, _ = v.receive_voice(recognition=recognition_text, send_time=100)
    rejected, _ = v.receive_voice(employee=True, send_time=101)
    no_price, _ = v.receive_voice(send_time=102)
    c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3}) for _ in range(3)]
    v.peer.replies[:] = [AsrReply(payload={'status': 40000001, 'message': 'fictional known rejection'}),
        AsrReply(payload={'status': 20000000, 'result': null_price_text})]
    initial_price = s.rows('SELECT asr_price_per_call FROM token_cost_prices WHERE model_name=%s',
        (voice_price['model'],))[0]['asr_price_per_call']
    initial_balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
        (s.tenant_id,))[0]['credit_balance']
    repository = ContextRepository(s.database.connect)

    async def consume():
        worker = ContextWorker(c.config.wecom_kf, repository=repository,
            client_factory=v.original_client)
        try:
            recognized = await worker.run_once()
            assert v.peer.calls == []
            failed = await worker.run_once()
            assert v.post_count() == 1
            # Real original price DAL/freeze_price must represent a valid NULL
            # ASR price, not a fabricated pricing function result.
            s.rows('UPDATE token_cost_prices SET asr_price_per_call=NULL WHERE model_name=%s',
                (voice_price['model'],))
            succeeded = await worker.run_once()
            assert await worker.run_once() is None
            return [recognized, failed, succeeded]
        finally:
            await worker.close()
            assert not worker.tasks and not worker.voice.tasks

    try:
        results = asyncio.run(consume())
        assert [row['disposition'] for row in results] == ['persisted'] * 3
        preps = {}
        voice_repo = ContextVoiceRepository(s.database.connect)
        for locator in (recognition, rejected, no_price):
            preps[locator.message_id] = voice_repo.read(locator)[4]
        recognized, failed, succeeded = [preps[l.message_id] for l in (recognition, rejected, no_price)]
        assert recognized['phase'] == 'known' and recognized['result_kind'] == 'recognition'
        assert recognized['success'] is True and recognized['transcript'] == recognition_text
        assert recognized['artifact'] is None and recognized['authorized_epoch'] == 0
        assert failed['phase'] == 'known' and failed['result_kind'] == 'asr'
        assert failed['success'] is False and failed['transcript'] == '' and failed['provider_status'] == 40000001
        assert succeeded['phase'] == 'known' and succeeded['result_kind'] == 'asr'
        assert succeeded['success'] is True and succeeded['transcript'] == null_price_text
        assert succeeded['provider_status'] == 20000000
        assert succeeded['price_snapshot']['price']['asr_price_per_call'] is None
        assert all(row['cost'] == Decimal('0') and row['finance_pending'] is False
            and row['history_projection'] == 'projected' for row in preps.values())
        records = s.rows('SELECT record_id,status,asr_calls,credit_cost,source_type FROM chat_records '
            'WHERE session_id=%s ORDER BY record_id', (s.legacy_sid,))
        assert len(records) == 2
        by_id = {row['record_id']: row for row in records}
        assert recognized['record_id'] not in by_id
        assert by_id[failed['record_id']]['status'] == 'failed'
        assert by_id[failed['record_id']]['asr_calls'] == 0
        assert by_id[succeeded['record_id']]['status'] == 'completed'
        assert by_id[succeeded['record_id']]['asr_calls'] == 1
        assert all(row['credit_cost'] == Decimal('0') and row['source_type'] == 'wecom_kf_human_asr'
            for row in records)
        histories = {row['message_id']: row for row in c.stored_history()}
        expected = ['[ASR识别结果] ' + recognition_text, '[人工客服] [语音消息]',
            '[ASR识别结果] ' + null_price_text]
        for result, text in zip(results, expected):
            row = histories[result['history_id']]
            assert row['content'] == text and row['metadata']['pending_asr'] is False
            assert row['metadata']['asr_phase'] == 'known'
        assert v.post_count() == 2 and sum(row['kind'] == 'media' for row in v.peer.calls) == 2
        assert not v.peer.errors and not c.platform.errors and len(c.platform.calls) == 3
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (s.tenant_id,)) == [{'credit_balance': initial_balance}]
        assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
        # Durable known results reproject through their original repository;
        # no caller says known again and no second physical dispatch occurs.
        for locator, result in zip((recognition, rejected, no_price), results):
            assert voice_repo.project(locator)['history_id'] == result['history_id']
        assert v.post_count() == 2
        assert {row['record_id'] for row in s.rows('SELECT record_id FROM chat_records '
            'WHERE session_id=%s', (s.legacy_sid,))} == set(by_id)
    finally:
        ids = s.rows('SELECT record_id FROM wecom_kf_context_voice_preparations '
            'WHERE account_id=%s AND tenant_id=%s', (c.account['account_id'], s.tenant_id))
        for row in ids:
            s.rows('DELETE FROM chat_records WHERE record_id=%s AND tenant_id=%s', (row['record_id'], s.tenant_id))
        s.rows('DELETE FROM wecom_kf_context_voice_preparations WHERE account_id=%s AND tenant_id=%s',
            (c.account['account_id'], s.tenant_id))
        s.rows('UPDATE token_cost_prices SET asr_price_per_call=%s WHERE model_name=%s',
            (initial_price, voice_price['model']))
        s.rows('UPDATE tenants SET credit_balance=%s WHERE tenant_id=%s', (initial_balance, s.tenant_id))
