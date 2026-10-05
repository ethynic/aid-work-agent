"""Prepared real Voice response boundaries; no mutable production import.

Only external token retrieval, model/media/provider responses are fictional IO.
Every original Worker, SDK, Speech POST, permission, PG receipt and fee remains
the subject. These definitions are not behavior evidence until actually run.
"""
from decimal import Decimal
import json
from pathlib import Path
import secrets
import threading

import pytest

from .conftest import wait_for
from .kf_ingress_fixtures import kf_scope
from .kf_voice_fixtures import voice_scope, voice_price, original_timestamped_input
from .kf_voice_peer import AsrReply
from .kf_ingress_fixtures import text_message
from .kf_admission_service import verify_original_resources, safe_source_execution_diagnostic
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, terminal, decoded

pytestmark = pytest.mark.integration


def start_voice_worker(fleet, voice, *, once=False, maximum=1, one_round=False):
    worker_id = 'voice_result_worker_' + secrets.token_hex(10)
    arguments = [str(Path(__file__).with_name('kf_voice_process.py')),
        'worker', '--worker-id', worker_id]
    if once:
        arguments.append('--once')
    else:
        arguments.extend(['--max-tasks', str(maximum)])
    environment = {**fleet.environment, **voice.api.environment,
        **voice.asr_environment(), 'QWEN_MODEL_CODE': fleet.models[0]}
    if one_round:
        environment['KF_VOICE_ONE_ROUND'] = 'true'
    child = fleet.processes.start(arguments, environment=environment,
        private_working_directory=True)
    fleet.children.append((child, worker_id))
    return child


def post_count(voice):
    return sum(call['kind'] == 'asr' for call in voice.peer.calls)


def safe_voice_error(voice, identifier, processes, phase, error):
    print('SAFE_VOICE_DIAGNOSTIC=' + json.dumps({'phase': phase,
        'exception_class': type(error).__name__, 'asr_post_count': post_count(voice),
        'source': safe_source_execution_diagnostic(voice.scope, identifier, processes)},
        sort_keys=True))


def test_actual_trusted_page_recognition_uses_original_model_and_history_without_media_or_asr(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    from src.channels.wecom_kf.ingress_auth import KfIngressError
    v, s = voice_scope, voice_scope.scope
    # Invalid native Voice cannot durably enter a page or advance its cursor.
    # Optional Recognition cannot stand in for the mandatory media_id field.
    for voice_value in ({}, {'recognition': 'Recognition with missing media must reject'}):
        invalid = text_message(s, 'invalid_voice_' + secrets.token_hex(10))
        invalid.pop('text')
        invalid['msgtype'], invalid['voice'] = 'voice', voice_value
        cursor_before = s.rows('SELECT cursor FROM wecom_kf_account_sync WHERE account_id=%s', (v.text.account['account_id'],))
        inbox_before = s.rows('SELECT message_id,payload_digest FROM wecom_kf_inbox WHERE account_id=%s ORDER BY receipt_order', (v.text.account['account_id'],))
        with pytest.raises(KfIngressError):
            v.text.receive(invalid)
        assert s.rows('SELECT cursor FROM wecom_kf_account_sync WHERE account_id=%s', (v.text.account['account_id'],)) == cursor_before
        assert s.rows('SELECT message_id,payload_digest FROM wecom_kf_inbox WHERE account_id=%s ORDER BY receipt_order', (v.text.account['account_id'],)) == inbox_before
        assert v.text.facts() == [] and v.peer.calls == []
    reply = Reply(content='Trusted received Recognition final output')
    marker = provider_peer.register(reply)
    transcript = marker + ' trusted original received page recognition'
    locator, _ = v.receive_voice(recognition=transcript)
    accepted = v.text.accept(locator)
    identifier, input_ref = accepted['current_runner_id'], accepted['input_ref']
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    try:
        verify_original_resources(fleet, v.api)
        child = start_voice_worker(fleet, v)
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(child)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        assert v.peer.calls == []  # No media GET, token-paid dispatch, or ASR POST.
        preparations = s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))
        assert len(preparations) == 1
        prep = preparations[0]
        assert prep['phase'] == 'known' and prep['result_kind'] == 'recognition'
        assert prep['success'] is True and prep['transcript'] == transcript
        assert prep['receipt_id'] is None and prep['fee_owner_runner_id'] is None
        assert prep['artifact'] is None
        receipts = s.rows('SELECT owner,phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert receipts == [{'owner': 'llm', 'phase': 'observed', 'applied': True}]
        requests = provider_peer.requests(marker)
        assert len(requests) == 1
        incoming = [m for m in decoded(finished['checkpoint'])['execution']['messages']
            if m.get('role') == 'user' and (m.get('metadata') or {}).get('input_ref') == input_ref]
        assert len(incoming) == 1
        model_input = original_timestamped_input(incoming[0]['content'], transcript)
        assert [m.get('content') for m in requests[0]['messages'] if m.get('role') == 'user'].count(model_input) == 1
        history = s.rows("SELECT content,metadata FROM channel_messages WHERE message_id=%s", (input_ref + ':user',))
        assert len(history) == 1 and history[0]['content'] == '[ASR识别结果] ' + transcript
        assert decoded(history[0]['metadata'])['input_ref'] == input_ref
        assert s.rows('SELECT user_message,asr_calls,total_token_count,credit_cost FROM chat_records WHERE record_id=%s',
            (finished['record_id'],)) == [{'user_message': '[ASR识别结果] ' + transcript,
                'asr_calls': 0, 'total_token_count': 18, 'credit_cost': Decimal('0.01')}]
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [{'credit_balance': Decimal('999.99')}]
        assert s.rows('SELECT phase FROM agent_runner_inputs WHERE input_ref=%s', (input_ref,)) == [{'phase': 'applied'}]
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s',
            (s.legacy_sid,)) == [{'owner_runner_id': identifier, 'gate': 'delivery'}]
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'recognition_terminal', error)
        raise
    finally:
        fleet.close()


def test_actual_explicit_integer_provider_rejection_is_known_zero_asr_calls_and_one_model_placeholder(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    v, s = voice_scope, voice_scope.scope
    # Select the scripted external response using the exact native placeholder;
    # the original Assembler may remove an old unpaired history user message.
    marker = provider_peer.register(Reply(content='Known voice rejection final output'),
        marker='[语音消息]')
    v.peer.replies.append(AsrReply(payload={'status': 40000001, 'message': 'fictional known rejection'}))
    locator, _ = v.receive_voice()
    accepted = v.text.accept(locator)
    identifier, input_ref = accepted['current_runner_id'], accepted['input_ref']
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    try:
        verify_original_resources(fleet, v.api)
        child = start_voice_worker(fleet, v)
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(child)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        assert post_count(v) == 1
        preparation = s.rows('SELECT phase,success,transcript,provider_status,fee_owner_runner_id FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))
        assert preparation == [{'phase': 'known', 'success': False, 'transcript': '',
            'provider_status': 40000001, 'fee_owner_runner_id': identifier}]
        receipts = s.rows('SELECT owner,phase,usage,applied FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY owner', (identifier,))
        assert len(receipts) == 2 and all(r['phase'] == 'observed' and r['applied'] for r in receipts)
        assert next(r for r in receipts if r['owner'] == 'asr')['usage'] == {'calls': 0}
        model = provider_peer.requests(marker)
        assert len(model) == 1
        incoming = [m for m in decoded(finished['checkpoint'])['execution']['messages']
            if m.get('role') == 'user' and (m.get('metadata') or {}).get('input_ref') == input_ref]
        assert len(incoming) == 1
        model_input = original_timestamped_input(incoming[0]['content'], '[语音消息]')
        assert [m.get('content') for m in model[0]['messages'] if m.get('role') == 'user'].count(model_input) == 1
        assert s.rows('SELECT user_message,asr_calls,total_token_count,credit_cost FROM chat_records WHERE record_id=%s',
            (finished['record_id'],)) == [{'user_message': '[语音消息]', 'asr_calls': 0,
                'total_token_count': 18, 'credit_cost': Decimal('0.01')}]
        assert s.rows('SELECT content FROM channel_messages WHERE message_id=%s', (input_ref + ':user',)) == [{'content': '[语音消息]'}]
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'known_rejection_terminal', error)
        raise
    finally:
        fleet.close()


@pytest.mark.skip(reason='Retired M6a product expectation: KF has no pause/resume UI; ASR network/parse unknown uses the original placeholder and continues chat. Historical passing evidence is not new compatibility evidence.')
def test_actual_bad_json_asr_result_holds_original_claim_and_explicit_resume_does_not_post_again(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    v, s = voice_scope, voice_scope.scope
    marker = provider_peer.register(Reply(content='Unknown ASR must not dispatch model'))
    s.rows('UPDATE channel_messages SET content=%s WHERE message_id=%s',
        (marker, 'kf_history_' + s.marker))
    v.peer.replies.append(AsrReply(raw_body=b'{not-valid-json'))
    locator, _ = v.receive_voice()
    accepted = v.text.accept(locator)
    identifier, input_ref = accepted['current_runner_id'], accepted['input_ref']
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    try:
        verify_original_resources(fleet, v.api)
        first = start_voice_worker(fleet, v)
        interrupted = wait_for(lambda: row if (row := runner(s.database, identifier))['status'] == 'interrupted' else None, timeout=25)
        fleet.assert_clean_exit(first)
        assert post_count(v) == 1 and not provider_peer.requests(marker)
        prep = s.rows('SELECT phase,receipt_id,fee_owner_runner_id,authorized_attempt FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))
        assert len(prep) == 1 and prep[0]['phase'] == 'unknown'
        assert prep[0]['fee_owner_runner_id'] == identifier and prep[0]['authorized_attempt'] == 1
        receipt = s.rows('SELECT receipt_id,phase,usage,applied,authorized_attempt FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipt) == 1 and receipt[0]['receipt_id'] == prep[0]['receipt_id']
        assert receipt[0]['phase'] == 'unknown' and not receipt[0]['applied']
        assert receipt[0]['authorized_attempt'] == 1
        assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
        assert s.rows('SELECT 1 FROM channel_messages WHERE message_id=%s', (input_ref + ':user',)) == []
        prior_claim = s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,))
        assert len(prior_claim) == 1 and prior_claim[0]['owner_runner_id'] == identifier
        resume = v.api.call('POST', '/v1/runners/' + identifier + '/controls',
            headers=v.api.headers(scope=s, input_ref=input_ref),
            json={'action': 'resume', 'client_request_id': 'voice_unknown_resume_' + secrets.token_hex(10)})
        assert resume.status_code == 202
        second = start_voice_worker(fleet, v, once=True)
        fleet.assert_clean_exit(second)
        final = runner(s.database, identifier)
        assert final['status'] == 'interrupted' and final['record_id'] == interrupted['record_id']
        assert post_count(v) == 1 and not provider_peer.requests(marker)
        assert s.rows('SELECT receipt_id,phase,usage,applied,authorized_attempt FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == receipt
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == prior_claim
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [{'credit_balance': Decimal('1000')}]
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'unknown_restart', error)
        raise
    finally:
        fleet.close()


def test_actual_cancel_during_original_asr_post_saves_late_original_fee_and_never_dispatches_model(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    v, s = voice_scope, voice_scope.scope
    marker = provider_peer.register(Reply(content='Cancelled preparation must not dispatch model'))
    s.rows('UPDATE channel_messages SET content=%s WHERE message_id=%s',
        (marker, 'kf_history_' + s.marker))
    release = threading.Event()
    transcript = marker + ' actual late known recognition'
    reply = AsrReply(payload={'status': 20000000, 'result': transcript}, release=release)
    v.peer.replies.append(reply)
    locator, _ = v.receive_voice()
    accepted = v.text.accept(locator)
    identifier, input_ref = accepted['current_runner_id'], accepted['input_ref']
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    try:
        verify_original_resources(fleet, v.api)
        child = start_voice_worker(fleet, v)
        assert reply.arrived.wait(15)
        original = s.rows('SELECT fee_owner_runner_id,authorized_attempt,receipt_id FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))[0]
        assert original['fee_owner_runner_id'] == identifier and original['authorized_attempt'] == 1
        cancelled = v.api.call('POST', '/v1/runners/' + identifier + '/cancel',
            headers=v.api.headers(scope=s, input_ref=input_ref))
        assert cancelled.status_code == 200 and runner(s.database, identifier)['cancel_requested']
        release.set()
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(child)
        assert finished['status'] == 'cancelled' and finished['settlement_status'] == 'settled'
        assert post_count(v) == 1 and not provider_peer.requests(marker)
        prep = s.rows('SELECT phase,success,transcript,fee_owner_runner_id,authorized_attempt,receipt_id FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))
        assert prep == [{'phase': 'known', 'success': True, 'transcript': transcript, **original}]
        assert s.rows('SELECT phase,usage,applied,authorized_attempt,record_id FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == [
            {'phase': 'observed', 'usage': {'calls': 1}, 'applied': True,
                'authorized_attempt': 1, 'record_id': finished['record_id']}]
        assert s.rows('SELECT asr_calls,total_token_count,credit_cost FROM chat_records WHERE record_id=%s', (finished['record_id'],)) == [
            {'asr_calls': 1, 'total_token_count': 0, 'credit_cost': voice_price['successful_call_credit']}]
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [{'credit_balance': Decimal('999.90')}]
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == [{'owner_runner_id': identifier, 'gate': 'delivery'}]
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'late_cancel', error)
        raise
    finally:
        release.set()
        fleet.close()
