"""Prepared ordinary pause before POST must preserve reusable media_ready.

Only token-fetch external IO timing is held. The phase, actual API pause ACK,
resume, original dispatch guard/POST, result/fees and model are never faked.
"""
from decimal import Decimal
from pathlib import Path
import secrets

import pytest

from .conftest import wait_for
from .kf_ingress_fixtures import kf_scope
from .kf_voice_fixtures import voice_scope, voice_price
from .kf_voice_peer import AsrReply
from .kf_admission_service import verify_original_resources
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, terminal, decoded
from .test_kf_voice_results import start_voice_worker, post_count, safe_voice_error

pytestmark = pytest.mark.integration


@pytest.mark.skip(reason='Retired M6a product expectation: KF has no pause/resume UI; ASR network/parse unknown uses the original placeholder and continues chat. Historical passing evidence is not new compatibility evidence.')
def test_actual_prepost_pause_keeps_original_media_ready_then_resume_performs_one_original_asr_and_model(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    v, s = voice_scope, voice_scope.scope
    marker = provider_peer.register(Reply(content='Original paused Voice recognized after one paid dispatch'))
    transcript = marker + ' original resumed long speech text'
    v.peer.replies.append(AsrReply(payload={'status': 20000000, 'result': transcript}))
    locator, _ = v.receive_voice()
    accepted = v.text.accept(locator)
    identifier, input_ref = accepted['current_runner_id'], accepted['input_ref']
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    ready, release = fleet.root / 'voice-token-ready', fleet.root / 'voice-token-release'
    try:
        verify_original_resources(fleet, v.api)
        worker_id = 'voice_pause_worker_' + secrets.token_hex(10)
        environment = {**fleet.environment, **v.api.environment, **v.asr_environment(),
            'QWEN_MODEL_CODE': fleet.models[0], 'KF_VOICE_TOKEN_READY': str(ready),
            'KF_VOICE_TOKEN_RELEASE': str(release)}
        first = service_processes.start([str(Path(__file__).with_name('kf_voice_pause_process.py')),
            'worker', '--worker-id', worker_id, '--max-tasks', '1'],
            environment=environment, private_working_directory=True)
        fleet.children.append((first, worker_id))
        wait_for(lambda: ready.is_file(), timeout=15)
        before = s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))[0]
        assert before['phase'] == 'media_ready' and before['receipt_id'] is None
        assert before['observed_at'] is None and before['transcript'] is None
        assert before['success'] is False and before['result_kind'] is None
        assert post_count(v) == 0 and not provider_peer.requests(marker)
        paused = v.api.call('POST', '/v1/runners/' + identifier + '/controls',
            headers=v.api.headers(scope=s, input_ref=input_ref),
            json={'action': 'pause', 'client_request_id': 'voice_pause_' + secrets.token_hex(10)})
        assert paused.status_code == 202
        assert runner(s.database, identifier)['pause_requested']
        release.write_text('release')
        acknowledged = wait_for(lambda: row if (row := runner(s.database, identifier))['status'] == 'paused' else None, timeout=25)
        fleet.assert_clean_exit(first)
        retained = s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))[0]
        assert retained == before  # No ordinary pause rewritten as known failure.
        assert post_count(v) == 0 and not provider_peer.requests(marker)
        assert s.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == []
        assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
        assert decoded(acknowledged['checkpoint'])['unstarted'] is True
        claim = s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,))
        assert len(claim) == 1 and claim[0]['owner_runner_id'] == identifier
        resumed = v.api.call('POST', '/v1/runners/' + identifier + '/controls',
            headers=v.api.headers(scope=s, input_ref=input_ref),
            json={'action': 'resume', 'client_request_id': 'voice_resume_' + secrets.token_hex(10)})
        assert resumed.status_code == 202
        second = start_voice_worker(fleet, v)
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(second)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        assert finished['attempt'] == 2 and finished['record_id'] == acknowledged['record_id']
        assert post_count(v) == len(provider_peer.requests(marker)) == 1
        known = s.rows('SELECT phase,success,transcript,fee_owner_runner_id,authorized_attempt,artifact FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))[0]
        assert known['phase'] == 'known' and known['success'] is True and known['transcript'] == transcript
        assert known['fee_owner_runner_id'] == identifier and known['authorized_attempt'] == 2
        assert known['artifact'] == before['artifact']
        receipts = s.rows('SELECT owner,authorized_attempt,phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 2 and all(r['authorized_attempt'] == 2 and r['phase'] == 'observed' and r['applied'] for r in receipts)
        assert {r['owner'] for r in receipts} == {'llm', 'asr'}
        assert s.rows('SELECT user_message,asr_calls,total_token_count,credit_cost FROM chat_records WHERE record_id=%s', (finished['record_id'],)) == [
            {'user_message': '[ASR识别结果] ' + transcript, 'asr_calls': 1,
                'total_token_count': 18, 'credit_cost': Decimal('0.11')}]
        assert s.rows('SELECT phase FROM agent_runner_inputs WHERE input_ref=%s', (input_ref,)) == [{'phase': 'applied'}]
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'pause_before_post', error)
        raise
    finally:
        release.write_text('release')
        fleet.close()
