"""Prepared real admission CLI SIGTERM + competing Context consumer.

The owned child calls original admission_worker.main/run_worker and its actual
signal handler. Original start SQL commits, then its unmodified return is held
before the original Speech callback can return/POST (callback timing DI).
"""
import asyncio
import json
import os
from pathlib import Path
import signal

import pytest

from .conftest import wait_for
from .kf_context_voice_fixtures import context_voice_receipts, context_receipts, voice_price, kf_scope
from .kf_admission_peer import StateReply
from .kf_voice_peer import AsrReply

pytestmark = pytest.mark.integration


def test_fixed_non_ai_voice_drains_with_native_off_and_proven_unwritten_stop_recovers_exactly_once(
        context_voice_receipts, voice_price, service_processes):
    from src.channels.wecom_kf.context_repository import ContextRepository
    from src.channels.wecom_kf.context_voice_repository import ContextVoiceRepository
    from src.channels.wecom_kf.context_worker import ContextWorker
    from src.channels.wecom_kf.ingress_auth import KfIngressError

    v, c, s = context_voice_receipts, context_voice_receipts.context, context_voice_receipts.scope
    locator, _ = v.receive_voice()
    c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3})]
    gate = service_processes.root / 'context-voice-original-before-post'
    ready, release = gate.with_suffix('.ready.json'), gate.with_suffix('.release')
    probe = Path(__file__).with_name('kf_context_voice_admission_process.py')
    child = service_processes.start([str(probe)], private_working_directory=True,
        environment={'AGENT_RUNNER_ENABLED': 'true', 'AGENT_RUNNER_WECOM_KF_ENABLED': 'true',
            'REDIS_ENABLED': 'false', 'CONTEXT_VOICE_PEER_URL': v.peer.base_url,
            'CONTEXT_VOICE_CALLBACK_GATE': str(gate),
            'CONTEXT_VOICE_FICTIONAL_TOKEN': v.peer._asr_token,
            'ALIYUN_ASR_ACCESS_KEY_ID': 'fictional_context_voice_key',
            'ALIYUN_ASR_ACCESS_KEY_SECRET': 'fictional_context_voice_secret',
            'ALIYUN_ASR_APPKEY': v.peer._appkey, 'ASR_USAGE_FACTOR': '100',
            'AGENT_RUNNER_STORAGE_ROOT': str(v.storage_root)})
    # Only the already allocated owned PID/birth is recorded; no argv/env.
    stat = Path('/proc') / str(child.pid) / 'stat'
    v.allocation['owned_child'] = {'pid': child.pid, 'start_ticks': stat.read_text().rsplit(')', 1)[1].split()[19],
        'entry': probe.name}
    v.persist_resource_observation('owned_original_cli_started')
    voice_repo = ContextVoiceRepository(s.database.connect)
    balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))[0]['credit_balance']
    original_operation = None

    async def second_consumer():
        worker = ContextWorker(c.config.wecom_kf, repository=ContextRepository(s.database.connect),
            client_factory=v.original_client)
        try:
            return await worker.run_once()
        finally:
            await worker.close()
            assert not worker.tasks and not worker.voice.tasks

    async def recover():
        # Fixed classification drain is distinct from fresh admission. No fake
        # after cursor or manual project call substitutes for original selection.
        config = c.config.wecom_kf.model_copy(deep=True)
        config.enabled = False
        worker = ContextWorker(config, repository=ContextRepository(s.database.connect),
            client_factory=v.original_client)
        try:
            result = await worker.run_once()
            assert await worker.run_once() is None
            return result
        finally:
            await worker.close()
            assert not worker.tasks and not worker.voice.tasks

    try:
        wait_for(lambda: ready.exists(), timeout=10)
        frozen = json.loads(ready.read_text())
        original_operation = voice_repo.read(locator)[4]
        assert original_operation['phase'] == 'started' and original_operation['authorized_epoch'] == frozen['authorized_epoch'] == 1
        assert original_operation['operation_ref'] == frozen['operation_ref']
        assert original_operation['record_id'] == frozen['record_id'] and v.post_count() == 0
        pending = asyncio.run(second_consumer())
        assert pending['disposition'] == 'pending_asr' and pending['history_id'] is not None
        placeholder = next(r for r in c.stored_history() if r['message_id'] == pending['history_id'])
        assert placeholder['content'] == '[语音消息]'
        assert placeholder['metadata']['pending_asr'] is True and placeholder['metadata']['asr_phase'] == 'started'
        assert v.post_count() == 0
        # Actual original child CLI handles SIGTERM. The callback still has not
        # returned, so this is a provably zero-POST cancellation window.
        os.kill(child.pid, signal.SIGTERM)
        import time
        time.sleep(.1)
        assert child.poll() is None and v.post_count() == 0
        release.touch()
        assert child.wait(timeout=10) == 0
        retained = voice_repo.read(locator)[4]
        assert retained['phase'] == 'media_ready' and retained['authorized_epoch'] == original_operation['authorized_epoch']
        assert retained['success'] is None and retained['result_kind'] is None
        assert retained['artifact'] == original_operation['artifact'] and retained['record_id'] == original_operation['record_id']
        assert retained['price_snapshot'] is None and retained['started_at'] is None
        assert v.post_count() == 0
        assert s.rows('SELECT 1 FROM chat_records WHERE record_id=%s', (retained['record_id'],)) == []
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [{'credit_balance': balance}]
        # Available state1 must not reclassify the fixed reliable3 receipt.
        c.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 1})]
        transcript = 'Fictional first and only ASR after an original CLI unwritten stop'
        v.peer.replies[:] = [AsrReply(payload={'status': 20000000, 'result': transcript})]
        complete = asyncio.run(recover())
        assert complete['disposition'] == 'persisted' and complete['history_id'] == pending['history_id']
        prep = voice_repo.read(locator)[4]
        assert prep['phase'] == 'known' and prep['success'] is True and prep['transcript'] == transcript
        assert prep['operation_ref'] == original_operation['operation_ref'] and prep['record_id'] == original_operation['record_id']
        assert prep['authorized_epoch'] == 2 and prep['cost'] == voice_price['successful_call_credit']
        assert prep['finance_pending'] is False
        # Negative old-owner port check, explicitly not a new provider result:
        # old epoch must reject before storage even for the exact actual text.
        with pytest.raises(KfIngressError) as stale:
            voice_repo.known(original_operation, success=True, text=transcript, status=20000000)
        assert stale.value.code == 'KF_CONTEXT_ASR_OWNER_CHANGED'
        assert v.post_count() == 1 and len(c.platform.calls) == 1 and not v.peer.errors and not c.platform.errors
        assert s.rows('SELECT classification,observed_state FROM wecom_kf_receipt_classifications '
            'WHERE account_id=%s AND message_id=%s', (c.account['account_id'], locator.message_id)) == [
            {'classification': 'human', 'observed_state': 3}]
        row = next(r for r in c.stored_history() if r['message_id'] == complete['history_id'])
        assert row['content'] == '[ASR识别结果] ' + transcript and row['metadata']['pending_asr'] is False
        assert s.rows('SELECT record_id,asr_calls,credit_cost FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == [
            {'record_id': prep['record_id'], 'asr_calls': 1, 'credit_cost': voice_price['successful_call_credit']}]
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [
            {'credit_balance': balance - voice_price['successful_call_credit']}]
        assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
    finally:
        release.touch()
        service_processes.stop(child)
        v.allocation['owned_child']['actual_exit'] = child.returncode
        v.persist_resource_observation('owned_original_cli_finished')
        ids = s.rows('SELECT record_id FROM wecom_kf_context_voice_preparations WHERE account_id=%s AND tenant_id=%s',
            (c.account['account_id'], s.tenant_id))
        for row in ids:
            s.rows('DELETE FROM chat_records WHERE record_id=%s AND tenant_id=%s', (row['record_id'], s.tenant_id))
        s.rows('DELETE FROM wecom_kf_context_voice_preparations WHERE account_id=%s AND tenant_id=%s',
            (c.account['account_id'], s.tenant_id))
        s.rows('UPDATE tenants SET credit_balance=%s WHERE tenant_id=%s', (balance, s.tenant_id))
