"""Prepared actual late input state boundaries; external token/provider gates.

No execution state or phase is fabricated. The first original model establishes
a real Runtime, then the original before_model preparation sees a late Voice.
"""
from decimal import Decimal
from pathlib import Path
import secrets
import threading

import pytest

from .conftest import wait_for
from .kf_ingress_fixtures import kf_scope
from .kf_voice_fixtures import voice_scope, voice_price
from .kf_voice_peer import AsrReply
from .kf_admission_service import verify_original_resources
from .provider import Reply, tool_reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, terminal, decoded
from .test_kf_voice_results import start_voice_worker, post_count, safe_voice_error

pytestmark = pytest.mark.integration


@pytest.mark.skip(reason='Retired M6a product expectation: KF has no pause/resume UI; ASR network/parse unknown uses the original placeholder and continues chat. Historical passing evidence is not new compatibility evidence.')
def test_actual_late_voice_prepost_pause_saves_not_started_model_then_resume_uses_original_owner_once(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    v, s = voice_scope, voice_scope.scope
    release = threading.Event()
    first_reply = Reply(content='First original text model before late Voice', release=release)
    first_marker = provider_peer.register(first_reply)
    next_reply = Reply(content='Original resumed second model recognized Voice')
    next_marker = provider_peer.register(next_reply)
    transcript = next_marker + ' actual paused late voice transcript'
    v.peer.replies.append(AsrReply(payload={'status': 20000000, 'result': transcript}))
    initial = v.text.accept(v.text.text('late_pause_first_', first_marker))
    identifier = initial['current_runner_id']
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    ready, token_release = fleet.root / 'voice-token-ready', fleet.root / 'voice-token-release'
    read_ready, read_release = fleet.root / 'voice-read-ready', fleet.root / 'voice-read-release'
    read_input = fleet.root / 'voice-read-input.txt'
    read_input.write_text('voice-original-read-success')
    read_call_id = 'voice-late-pause-original-read'
    first_reply.tool_calls = tool_reply('read', {'file_path': str(read_input)}, call_id=read_call_id).tool_calls
    try:
        verify_original_resources(fleet, v.api)
        worker_id = 'late_voice_pause_worker_' + secrets.token_hex(10)
        environment = {**fleet.environment, **v.api.environment, **v.asr_environment(),
            'QWEN_MODEL_CODE': fleet.models[0], 'KF_VOICE_TOKEN_READY': str(ready),
            'KF_VOICE_TOKEN_RELEASE': str(token_release), 'KF_VOICE_READ_READY': str(read_ready),
            'KF_VOICE_READ_RELEASE': str(read_release), 'KF_VOICE_READ_INPUT': str(read_input)}
        first = service_processes.start([str(Path(__file__).with_name('kf_voice_late_pause_process.py')),
            'worker', '--worker-id', worker_id, '--max-tasks', '1'],
            environment=environment, private_working_directory=True)
        fleet.children.append((first, worker_id))
        assert first_reply.arrived.wait(15)
        release.set()
        wait_for(lambda: read_ready.is_file(), timeout=15)
        import json
        assert json.loads(read_ready.read_text()) == {'read_calls': 1, 'success': True}
        locator, _ = v.receive_voice()
        voice = v.text.accept(locator)
        assert voice['accepted_runner_id'] == voice['current_runner_id'] == identifier
        read_release.write_text('release')
        wait_for(lambda: ready.is_file(), timeout=15)
        assert json.loads(ready.read_text()) == {'boundary': 'before_model', 'root': True,
            'read_calls': 1, 'read_success': True}
        before = s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (voice['input_ref'],))[0]
        assert before['phase'] == 'media_ready' and before['receipt_id'] is None
        assert post_count(v) == 0 and not provider_peer.requests(next_marker)
        paused = v.api.call('POST', '/v1/runners/' + identifier + '/controls',
            headers=v.api.headers(scope=s, input_ref=initial['input_ref']),
            json={'action': 'pause', 'client_request_id': 'late_voice_pause_' + secrets.token_hex(10)})
        assert paused.status_code == 202
        token_release.write_text('release')
        row = wait_for(lambda: current if (current := runner(s.database, identifier))['status'] == 'paused' else None, timeout=25)
        fleet.assert_clean_exit(first)
        state = decoded(row['checkpoint'])['execution']
        assert state['outcome'] == 'paused'
        assert state['model_calls'][-1]['phase'] == 'not_started'
        read_fact = state['tools'][read_call_id]
        assert read_fact['phase'] == 'completed' and read_fact['result_recorded'] is True
        assert read_fact['result'] == {'content': '1\tvoice-original-read-success', 'total_lines': 1, 'read_lines': 1}
        assert sum(call.get('id') == read_call_id for message in state['messages']
            for call in message.get('tool_calls', [])) == 1
        assert sum(message.get('tool_call_id') == read_call_id for message in state['messages']
            if message.get('role') == 'tool') == 1
        assert s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (voice['input_ref'],)) == [before]
        assert post_count(v) == 0 and len(provider_peer.requests(first_marker)) == 1
        assert not provider_peer.requests(next_marker)
        receipts_before = s.rows('SELECT owner,phase,authorized_attempt FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert receipts_before == [{'owner': 'llm', 'phase': 'observed', 'authorized_attempt': 1}]
        assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
        resumed = v.api.call('POST', '/v1/runners/' + identifier + '/controls',
            headers=v.api.headers(scope=s, input_ref=initial['input_ref']),
            json={'action': 'resume', 'client_request_id': 'late_voice_resume_' + secrets.token_hex(10)})
        assert resumed.status_code == 202
        second = start_voice_worker(fleet, v)
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(second)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        assert finished['attempt'] == 2 and post_count(v) == 1
        assert len(provider_peer.requests(first_marker)) == len(provider_peer.requests(next_marker)) == 1
        assert json.loads(read_ready.read_text()) == {'read_calls': 1, 'success': True}
        assert decoded(finished['checkpoint'])['execution']['tools'][read_call_id] == read_fact
        prep = s.rows('SELECT phase,transcript,fee_owner_runner_id,authorized_attempt,artifact FROM wecom_kf_input_preparations WHERE input_ref=%s', (voice['input_ref'],))[0]
        assert prep['phase'] == 'known' and prep['transcript'] == transcript
        assert prep['artifact'] == before['artifact']
        assert prep['fee_owner_runner_id'] == identifier and prep['authorized_attempt'] == 2
        receipts = s.rows('SELECT owner,phase,authorized_attempt,applied FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 3 and all(item['phase'] == 'observed' and item['applied'] for item in receipts)
        assert sorted(item['authorized_attempt'] for item in receipts if item['owner'] == 'llm') == [1, 2]
        assert next(item for item in receipts if item['owner'] == 'asr')['authorized_attempt'] == 2
        # Real fixture price: 2*(11*1+7*2)/1e6*100 + 1*.001*100
        # = .105, cumulative original billing rounds up to .11 once.
        assert s.rows('SELECT total_token_count,asr_calls,credit_cost FROM chat_records WHERE record_id=%s', (finished['record_id'],)) == [
            {'total_token_count': 36, 'asr_calls': 1, 'credit_cost': Decimal('0.11')}]
        assert s.rows('SELECT content FROM channel_messages WHERE message_id=%s', (voice['input_ref'] + ':user',)) == [{'content': '[ASR识别结果] ' + transcript}]
        actual_input = [message for message in decoded(finished['checkpoint'])['execution']['messages']
            if message.get('role') == 'user' and (message.get('metadata') or {}).get('input_ref') == voice['input_ref']]
        assert len(actual_input) == 1 and actual_input[0]['content'] == transcript
        assert [message.get('content') for message in provider_peer.requests(next_marker)[0]['messages']
            if message.get('role') == 'user'].count(transcript) == 1
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [{'credit_balance': Decimal('999.89')}]
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == [{'owner_runner_id': identifier, 'gate': 'delivery'}]
        assert [fact['phase'] for fact in v.text.facts()] == ['applied', 'applied']
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'late_pause_prepost', error)
        raise
    finally:
        release.set()
        read_release.write_text('release')
        token_release.write_text('release')
        fleet.close()


@pytest.mark.skip(reason='Retired M6a product expectation: KF has no pause/resume UI; ASR network/parse unknown uses the original placeholder and continues chat. Historical passing evidence is not new compatibility evidence.')
def test_actual_late_voice_unknown_with_original_runtime_state_cancellation_holds_input_claim_and_never_reposts(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    v, s = voice_scope, voice_scope.scope
    release = threading.Event()
    first_reply = Reply(content='Known first text response before late unknown ASR', release=release)
    marker = provider_peer.register(first_reply)
    v.peer.replies.append(AsrReply(raw_body=b'{actual-invalid-json'))
    initial = v.text.accept(v.text.text('late_unknown_initial_', marker))
    identifier = initial['current_runner_id']
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    try:
        verify_original_resources(fleet, v.api)
        first = start_voice_worker(fleet, v)
        assert first_reply.arrived.wait(15)
        locator, _ = v.receive_voice()
        voice = v.text.accept(locator)
        assert voice['accepted_runner_id'] == voice['current_runner_id'] == identifier
        release.set()
        held = wait_for(lambda: row if (row := runner(s.database, identifier))['status'] == 'interrupted' else None, timeout=25)
        fleet.assert_clean_exit(first)
        state = decoded(held['checkpoint'])['execution']
        assert state['messages'] and state['output'] == first_reply.content
        assert len(provider_peer.requests(marker)) == 1 and post_count(v) == 1
        prep = s.rows('SELECT phase,receipt_id,fee_owner_runner_id,authorized_attempt FROM wecom_kf_input_preparations WHERE input_ref=%s', (voice['input_ref'],))[0]
        assert prep['phase'] == 'unknown' and prep['fee_owner_runner_id'] == identifier and prep['authorized_attempt'] == 1
        receipts = s.rows('SELECT receipt_id,owner,phase,authorized_attempt,applied FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY owner', (identifier,))
        assert len(receipts) == 2 and all(not item['applied'] for item in receipts)
        assert next(item for item in receipts if item['owner'] == 'asr')['phase'] == 'unknown'
        assert next(item for item in receipts if item['owner'] == 'llm')['phase'] == 'observed'
        claim = s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,))
        assert len(claim) == 1 and claim[0]['owner_runner_id'] == identifier
        cancelled = v.api.call('POST', '/v1/runners/' + identifier + '/cancel',
            headers=v.api.headers(scope=s, input_ref=initial['input_ref']))
        assert cancelled.status_code == 200
        second = start_voice_worker(fleet, v, once=True)
        fleet.assert_clean_exit(second)
        after = runner(s.database, identifier)
        assert after['cancel_requested'] is True
        assert after['status'] == 'finalizing'
        cancel_checkpoint = decoded(after['checkpoint'])
        assert cancel_checkpoint['finalization_intent'] == 'cancel_only'
        assert not cancel_checkpoint.get('pending_finalization')
        assert after['settlement_status'] == 'pending' and after['record_id'] == held['record_id']
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == claim
        assert post_count(v) == 1 and len(provider_peer.requests(marker)) == 1
        assert s.rows('SELECT phase,receipt_id,fee_owner_runner_id,authorized_attempt FROM wecom_kf_input_preparations WHERE input_ref=%s', (voice['input_ref'],)) == [prep]
        assert s.rows('SELECT receipt_id,owner,phase,authorized_attempt,applied FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY owner', (identifier,)) == receipts
        assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [{'credit_balance': Decimal('1000')}]
        assert s.rows('SELECT 1 FROM channel_messages WHERE message_id=%s', (voice['input_ref'] + ':user',)) == []
        facts = v.text.facts()
        assert facts[1]['input_ref'] == voice['input_ref'] and facts[1]['phase'] in {'accepted', 'attached'}
        third = start_voice_worker(fleet, v, once=True)
        fleet.assert_clean_exit(third)
        retried = runner(s.database, identifier)
        assert retried['status'] == 'finalizing'
        assert decoded(retried['checkpoint'])['finalization_intent'] == 'cancel_only'
        assert not decoded(retried['checkpoint']).get('pending_finalization')
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == claim
        assert s.rows('SELECT receipt_id,owner,phase,authorized_attempt,applied FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY owner', (identifier,)) == receipts
        assert s.rows('SELECT phase,receipt_id,fee_owner_runner_id,authorized_attempt FROM wecom_kf_input_preparations WHERE input_ref=%s', (voice['input_ref'],)) == [prep]
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [{'credit_balance': Decimal('1000')}]
        assert post_count(v) == 1 and len(provider_peer.requests(marker)) == 1
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'late_unknown_cancel', error)
        raise
    finally:
        release.set()
        fleet.close()
