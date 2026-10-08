"""Prepared real human/employee Voice normal; not executable proof before freeze.

Crypto/SDK/current classification, ContextWorker/Speech observer/POST and PG
history/independent debit are original. Only external HTTP replies and original
Speech token retrieval are fictional localhost IO. No Runner/AI permission seam.
"""
import asyncio

import pytest

from .kf_context_voice_fixtures import context_voice_receipts, context_receipts, voice_price, kf_scope
from .kf_context_fixtures import assert_full_history_binding
from .kf_admission_peer import StateReply
from .kf_voice_peer import AsrReply

pytestmark = pytest.mark.integration


def test_actual_customer_and_servicer_voice_at_nonpositive_balance_have_original_history_and_one_stable_debit_each(
        context_voice_receipts, voice_price, monkeypatch):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.channels.wecom_kf.context_voice_repository import ContextVoiceRepository
    from src.services.recap import runner as old_recap
    import redis

    v, c, s = context_voice_receipts, context_voice_receipts.context, context_voice_receipts.scope
    transcripts = ['Fictional human customer speech with a complete long sentence',
        'Fictional employee speech with a complete long response']
    v.peer.replies[:] = [AsrReply(payload={'status': 20000000, 'result': text})
        for text in transcripts]
    c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3}) for _ in transcripts]
    customer, _ = v.receive_voice(send_time=100)
    employee, _ = v.receive_voice(employee=True, send_time=101)
    locators = [customer, employee]
    before = [c.receipt(locator) for locator in locators]
    original_balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
        (s.tenant_id,))[0]['credit_balance']
    s.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s', (s.tenant_id,))
    forbidden = {'recap': 0, 'redis': 0}

    def forbid_recap(*args, **kwargs):
        forbidden['recap'] += 1
        raise AssertionError('OLD_VOID_RECAP_MUST_NOT_EXECUTE')

    def unavailable_redis(*args, **kwargs):
        forbidden['redis'] += 1
        raise ConnectionError('FICTIONAL_CONTEXT_VOICE_REDIS_UNAVAILABLE')

    monkeypatch.setattr(old_recap, 'enqueue_human_period_tasks', forbid_recap)
    monkeypatch.setattr(redis.Redis, 'execute_command', unavailable_redis)
    repository = ContextRepository(s.database.connect)

    async def consume():
        worker = ContextWorker(c.config.wecom_kf, repository=repository,
            client_factory=v.original_client)
        try:
            first = await worker.run_once()
            assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
                (s.tenant_id,)) == [{'credit_balance': -voice_price['successful_call_credit']}]
            second = await worker.run_once()
            assert await worker.run_once() is None
            assert not worker.tasks and not worker.voice.tasks
            return [first, second]
        finally:
            await worker.close()
            assert not worker.tasks and not worker.voice.tasks

    record_ids = []
    try:
        results = asyncio.run(consume())
        assert [r['disposition'] for r in results] == ['persisted', 'persisted']
        history_ids = [r['history_id'] for r in results]
        assert len(set(history_ids)) == 2
        preparations = s.rows('SELECT * FROM wecom_kf_context_voice_preparations '
            'WHERE account_id=%s ORDER BY message_id', (c.account['account_id'],))
        assert len(preparations) == 2
        by_message = {row['message_id']: row for row in preparations}
        history = c.stored_history()
        current = [row for row in history if row['message_id'] in history_ids]
        assert len(history) == 3 and len(current) == 2
        for index, (locator, receipt, text) in enumerate(zip(locators, before, transcripts)):
            prep = by_message[locator.message_id]
            row = next(row for row in current if row['message_id'] == history_ids[index])
            expected = ('[人工客服] ' if index else '') + '[ASR识别结果] ' + text
            assert row['role'] == 'user' and row['content'] == expected and not row['is_recalled']
            metadata = assert_full_history_binding(row, receipt, s)
            assert metadata['source'] == ('servicer' if index else 'customer_human')
            assert metadata['namespace'] == 'sync' and metadata['actor_id'] == s.actor_id
            assert metadata['pending_asr'] is False and metadata['asr_phase'] == 'known'
            assert metadata['operation_ref'] == prep['operation_ref'] and metadata['operation_version'] == 1
            assert prep['phase'] == 'known' and prep['result_kind'] == 'asr'
            assert prep['success'] is True and prep['transcript'] == text
            assert prep['provider_status'] == 20000000 and prep['finance_pending'] is False
            assert prep['payload_digest'] == receipt['payload_digest']
            assert prep['scope']['route_id'] == receipt['route_id']
            assert prep['scope']['tenant_id'] == s.tenant_id and prep['scope']['session_id'] == s.legacy_sid
            assert prep['scope']['user_id'] is None and prep['authorized_epoch'] == 1
            assert prep['cost'] == voice_price['successful_call_credit']
            assert prep['history_projection'] == 'projected'
            assert prep['artifact']['file_size'] > 0 and len(prep['artifact']['sha256']) == 64
            assert 'base64' not in prep['artifact'] and 'content' not in prep['artifact']
            record_ids.append(prep['record_id'])
        assert len(set(record_ids)) == 2
        records = s.rows('SELECT record_id,source_type,user_message,model,provider,status,asr_calls,'
            'total_token_count,credit_cost FROM chat_records WHERE session_id=%s ORDER BY record_id',
            (s.legacy_sid,))
        assert len(records) == 2 and {r['record_id'] for r in records} == set(record_ids)
        assert all(r['source_type'] == 'wecom_kf_human_asr' and r['user_message'] == '[人工期语音识别]'
            and r['model'] == 'aliyun-nls-asr' and r['provider'] == 'aliyun'
            and r['status'] == 'completed' and r['asr_calls'] == 1 and r['total_token_count'] == 0
            and r['credit_cost'] == voice_price['successful_call_credit'] for r in records)
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [
            {'credit_balance': -2 * voice_price['successful_call_credit']}]
        assert v.post_count() == 2 and sum(call['kind'] == 'media' for call in v.peer.calls) == 2
        assert all(call['credential_valid'] for call in v.peer.calls)
        assert not v.peer.errors and not c.platform.errors and len(c.platform.calls) == 2
        for read in (c.original_history, c.runtime_history):
            roles = [(r['role'], r['content']) for r in read()]
            assert ('user', '[ASR识别结果] ' + transcripts[0]) in roles
            assert ('assistant', '[人工客服] [ASR识别结果] ' + transcripts[1]) in roles
            assert ('user', '[人工客服] [ASR识别结果] ' + transcripts[1]) not in roles
        intents = s.rows('SELECT * FROM wecom_kf_context_task_intents WHERE account_id=%s ORDER BY task_id',
            (c.account['account_id'],))
        assert len(intents) == 4 and {r['task_name'] for r in intents} == {'lead_refresh', 'external_push_human'}
        assert all(r['state'] == 'pending_adapter' and r['history_id'] in history_ids for r in intents)
        for locator in locators:
            repeat = ContextVoiceRepository(s.database.connect).project(locator)
            assert repeat['disposition'] == 'persisted' and repeat['history_id'] in history_ids
        assert c.stored_history() == history
        assert s.rows('SELECT * FROM wecom_kf_context_voice_preparations WHERE account_id=%s ORDER BY message_id',
            (c.account['account_id'],)) == preparations
        assert s.rows('SELECT * FROM wecom_kf_context_task_intents WHERE account_id=%s ORDER BY task_id',
            (c.account['account_id'],)) == intents
        assert [c.receipt(locator) for locator in locators] == before
        assert v.post_count() == 2 and forbidden == {'recap': 0, 'redis': 0}
        assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
        assert s.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE tenant_id=%s', (s.tenant_id,)) == []
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [
            {'credit_balance': -2 * voice_price['successful_call_credit']}]
    finally:
        # Exact owned preparation/record cleanup, never a shared tenant sweep.
        if not record_ids:
            record_ids = [r['record_id'] for r in s.rows('SELECT record_id FROM '
                'wecom_kf_context_voice_preparations WHERE account_id=%s AND tenant_id=%s',
                (c.account['account_id'], s.tenant_id))]
        for record_id in record_ids:
            s.rows('DELETE FROM chat_records WHERE record_id=%s AND tenant_id=%s', (record_id, s.tenant_id))
        s.rows('DELETE FROM wecom_kf_context_voice_preparations WHERE account_id=%s AND tenant_id=%s',
            (c.account['account_id'], s.tenant_id))
        s.rows('UPDATE tenants SET credit_balance=%s WHERE tenant_id=%s', (original_balance, s.tenant_id))
