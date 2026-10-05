"""Prepared real SQL fault/retained original observer result contract.

An observation wrapper records the exact original Speech parser call and then
executes the original repository method unchanged. It never fabricates a result
or permits another POST; the retry is an explicitly labelled typed observer
storage re-delivery, not a natural process resurrection guarantee.
"""
import asyncio
from copy import deepcopy
import secrets
import threading

import pytest

from .kf_context_voice_fixtures import context_voice_receipts, context_receipts, voice_price, kf_scope
from .kf_admission_peer import StateReply
from .kf_voice_peer import AsrReply

pytestmark = pytest.mark.integration


def test_original_known_result_and_history_fee_sql_rollback_reproject_exact_observation_without_second_post_or_debit(
        context_voice_receipts, voice_price, monkeypatch):
    from psycopg2 import InternalError, sql
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_voice_repository import ContextVoiceRepository
    from src.channels.wecom_kf.context_worker import ContextWorker

    v, c, s = context_voice_receipts, context_voice_receipts.context, context_voice_receipts.scope
    locator, _ = v.receive_voice(employee=True)
    c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3})]
    transcript = 'Fictional original Speech result retained through authoritative storage rollback'
    v.peer.replies[:] = [AsrReply(payload={'status': 20000000, 'result': transcript})]
    balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))[0]['credit_balance']
    received = c.receipt(locator)
    retained = []
    guard = threading.Lock()
    original_known = ContextVoiceRepository.known

    def observe_original_known(self, operation, **result):
        with guard:
            retained.append((deepcopy(operation), deepcopy(result)))
        return original_known(self, operation, **result)

    monkeypatch.setattr(ContextVoiceRepository, 'known', observe_original_known)
    fee_name, history_name = 'cv_fee_' + secrets.token_hex(8), 'cv_history_' + secrets.token_hex(8)
    # Real original debit UPDATE executes inside the known/record transaction.
    # The trigger is scoped to this fixture's tenant and removed in finally.
    def create_fault(name, table, condition):
        s.rows(sql.SQL('CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN '
            "IF {} THEN RAISE EXCEPTION 'FICTIONAL_OWN_CONTEXT_VOICE_STORAGE_FAULT'; END IF; RETURN NEW; END $$").format(
                sql.Identifier(name), condition))
        s.rows(sql.SQL('CREATE TRIGGER {} BEFORE INSERT OR UPDATE ON {} FOR EACH ROW EXECUTE FUNCTION {}()').format(
            sql.Identifier(name), sql.Identifier(table), sql.Identifier(name)))

    def remove_fault(name, table):
        s.rows(sql.SQL('DROP TRIGGER IF EXISTS {} ON {}').format(sql.Identifier(name), sql.Identifier(table)))
        s.rows(sql.SQL('DROP FUNCTION IF EXISTS {}()').format(sql.Identifier(name)))

    create_fault(fee_name, 'tenants', sql.SQL('NEW.tenant_id={} AND NEW.credit_balance<OLD.credit_balance').format(
        sql.Literal(s.tenant_id)))
    prep = None
    try:
        async def initial():
            worker = ContextWorker(c.config.wecom_kf, repository=ContextRepository(s.database.connect),
                client_factory=v.original_client)
            try:
                return await worker.run_once()
            finally:
                await worker.close()
                assert not worker.tasks and not worker.voice.tasks

        pending = asyncio.run(initial())
        assert pending['disposition'] == 'pending_asr' and pending['history_id'] is not None
        assert len(retained) == 1 and retained[0][1] == {
            'success': True, 'text': transcript, 'status': 20000000}
        operation, result = retained[0]
        voice_repo = ContextVoiceRepository(s.database.connect)
        prep = voice_repo.read(locator)[4]
        assert prep['phase'] == 'unknown' and prep['cost'] is None and prep['finance_pending'] is True
        assert prep['operation_ref'] == operation['operation_ref'] and prep['authorized_epoch'] == operation['authorized_epoch']
        assert v.post_count() == 1 and len(c.platform.calls) == 1
        assert s.rows('SELECT 1 FROM chat_records WHERE record_id=%s', (prep['record_id'],)) == []
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [{'credit_balance': balance}]
        assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s', (c.account['account_id'],)) == []
        unknown_history = next(row for row in c.stored_history() if row['message_id'] == pending['history_id'])
        assert unknown_history['content'] == '[人工客服] [语音消息]'
        assert unknown_history['metadata']['pending_asr'] is True and unknown_history['metadata']['asr_phase'] == 'unknown'
        remove_fault(fee_name, 'tenants')
        # Re-deliver only the exact already observed original parser return.
        # Subsequent history retry reads the persisted known fact, no new known.
        known = original_known(voice_repo, operation, **result)
        assert known['phase'] == 'known' and known['cost'] == voice_price['successful_call_credit']
        record = s.rows('SELECT * FROM chat_records WHERE record_id=%s', (known['record_id'],))
        assert len(record) == 1 and record[0]['asr_calls'] == 1
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [
            {'credit_balance': balance - voice_price['successful_call_credit']}]
        prior_consumption = s.rows('SELECT * FROM wecom_kf_context_consumptions WHERE account_id=%s', (c.account['account_id'],))
        create_fault(history_name, 'channel_messages', sql.SQL('NEW.message_id={}').format(sql.Literal(pending['history_id'])))
        with pytest.raises(InternalError):
            voice_repo.project(locator)
        assert s.rows('SELECT * FROM chat_records WHERE record_id=%s', (known['record_id'],)) == record
        assert s.rows('SELECT * FROM wecom_kf_context_consumptions WHERE account_id=%s', (c.account['account_id'],)) == prior_consumption
        assert next(row for row in c.stored_history() if row['message_id'] == pending['history_id']) == unknown_history
        assert s.rows('SELECT 1 FROM wecom_kf_context_task_intents WHERE account_id=%s', (c.account['account_id'],)) == []
        remove_fault(history_name, 'channel_messages')
        # Persisted fact projection, never test fabricated repeat known result.
        complete = voice_repo.project(locator)
        assert complete['disposition'] == 'persisted' and complete['history_id'] == pending['history_id']
        finished = next(row for row in c.stored_history() if row['message_id'] == pending['history_id'])
        assert finished['content'] == '[人工客服] [ASR识别结果] ' + transcript
        assert finished['metadata']['pending_asr'] is False and finished['metadata']['asr_phase'] == 'known'
        intents = s.rows('SELECT * FROM wecom_kf_context_task_intents WHERE account_id=%s', (c.account['account_id'],))
        assert len(intents) == 2 and all(row['state'] == 'pending_adapter' for row in intents)
        assert voice_repo.project(locator)['history_id'] == complete['history_id']
        assert s.rows('SELECT * FROM chat_records WHERE record_id=%s', (known['record_id'],)) == record
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [
            {'credit_balance': balance - voice_price['successful_call_credit']}]
        assert c.receipt(locator) == received
        assert v.post_count() == 1 and len(retained) == 1 and len(c.platform.calls) == 1
        assert not v.peer.errors and not c.platform.errors
        assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
    finally:
        remove_fault(fee_name, 'tenants')
        remove_fault(history_name, 'channel_messages')
        ids = s.rows('SELECT record_id FROM wecom_kf_context_voice_preparations WHERE account_id=%s AND tenant_id=%s',
            (c.account['account_id'], s.tenant_id))
        for row in ids:
            s.rows('DELETE FROM chat_records WHERE record_id=%s AND tenant_id=%s', (row['record_id'], s.tenant_id))
        s.rows('DELETE FROM wecom_kf_context_voice_preparations WHERE account_id=%s AND tenant_id=%s',
            (c.account['account_id'], s.tenant_id))
        s.rows('UPDATE tenants SET credit_balance=%s WHERE tenant_id=%s', (balance, s.tenant_id))
