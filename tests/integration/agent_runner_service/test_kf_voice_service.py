"""Prepared AI voice business node. No mutable production import at collection.

Original Crypto/received-page/HTTP/Worker/Runtime/Engine/PG/Usage are the subject;
model, media and ASR network peers are fictional localhost. Token-getter only is
explicit external IO DI. This node is not prepared proof of a successful run.
"""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import secrets
import threading

import pytest

from .conftest import wait_for
from .kf_ingress_fixtures import kf_scope
from .kf_voice_fixtures import voice_scope, voice_price, original_timestamped_input
from .kf_voice_peer import AsrReply, wav_bytes
from .kf_admission_service import verify_original_resources, safe_source_execution_diagnostic
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, decoded, runner, terminal

pytestmark = pytest.mark.integration


def test_actual_voice_receipt_http_accept_then_original_worker_asr_model_record_history_once(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    v, s = voice_scope, voice_scope.scope
    asr_release, model_release = threading.Event(), threading.Event()
    model_reply = Reply(content='Fictional AI voice final output', release=model_release)
    marker = provider_peer.register(model_reply)
    transcript = marker + ' fictional recognized long customer sentence'
    history_text = '[ASR识别结果] ' + transcript
    asr_reply = AsrReply(payload={'status': 20000000, 'result': transcript}, release=asr_release)
    v.peer.replies.append(asr_reply)
    audio = wav_bytes()
    locator, _media_id = v.receive_voice(audio=audio)
    accepted = v.text.accept(locator)
    identifier, input_ref = accepted['current_runner_id'], accepted['input_ref']
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    phase = 'accepted_before_preparation'
    try:
        verify_original_resources(fleet, v.api)
        assert accepted['created'] and identifier == accepted['accepted_runner_id']
        # Receipt acceptance itself is not paid ASR or a media download.
        assert v.peer.calls == []
        assert s.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == []
        assert s.rows('SELECT 1 FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,)) == []
        immutable_input = runner(s.database, identifier)['input']
        environment = {**fleet.environment, **v.api.environment, **v.asr_environment(),
            'QWEN_MODEL_CODE': fleet.models[0]}
        worker_id = 'voice_original_worker_' + secrets.token_hex(10)
        probe = Path(__file__).with_name('kf_voice_process.py')
        child = service_processes.start([str(probe), 'worker', '--worker-id', worker_id, '--max-tasks', '1'],
            environment=environment, private_working_directory=True)
        fleet.children.append((child, worker_id))
        assert asr_reply.arrived.wait(15)
        phase = 'actual_asr_post_started'
        preparation = s.rows('SELECT * FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))
        assert len(preparation) == 1
        preparation_ref = 'voice_' + hashlib.sha256((input_ref + ':1').encode()).hexdigest()
        assert preparation[0]['preparation_ref'] == preparation_ref
        assert preparation[0]['operation_version'] == 1 and preparation[0]['phase'] == 'started'
        assert preparation[0]['fee_owner_runner_id'] == identifier and preparation[0]['authorized_attempt'] == 1
        asr = s.rows("SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s AND owner='asr'", (identifier,))
        assert len(asr) == 1 and asr[0]['phase'] == 'started'
        assert asr[0]['call_id'] == preparation_ref + ':asr'
        assert asr[0]['provider'] == 'aliyun' and asr[0]['model'] == voice_price['model']
        assert asr[0]['billing_boundary'] == 'main' and asr[0]['authorized_attempt'] == 1
        assert Decimal(str(asr[0]['price_snapshot']['price']['asr_price_per_call'])) == voice_price['price']
        assert asr[0]['price_snapshot']['usage_factor'] == voice_price['factor']
        assert not provider_peer.requests(marker)
        posts = [r for r in v.peer.calls if r['kind'] == 'asr']
        assert len(posts) == 1 and posts[0]['credential_valid']
        assert posts[0]['sha256'] == hashlib.sha256(audio).hexdigest()
        assert posts[0]['format'] == ['wav'] and posts[0]['sample_rate'] == ['16000']
        asr_release.set()
        assert model_reply.arrived.wait(15)
        phase = 'actual_model_only_text'
        running = runner(s.database, identifier)
        assert running['input'] == immutable_input and running['attempt'] == 1
        state = decoded(running['checkpoint'])['execution']
        incoming = [m for m in state['messages'] if m.get('role') == 'user'
            and (m.get('metadata') or {}).get('input_ref') == input_ref]
        assert len(incoming) == 1
        model_input = original_timestamped_input(incoming[0]['content'], transcript)
        requests = provider_peer.requests(marker)
        assert len(requests) == 1
        model_users = [m['content'] for m in requests[0]['messages'] if m.get('role') == 'user']
        assert model_users.count(model_input) == 1
        assert '[ASR识别结果] ' not in model_input
        assert all(isinstance(content, str) for content in model_users)  # No audio/base64 multimodal part.
        model_release.set()
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(child)
        phase = 'terminal_facts'
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        assert finished['result']['output'] == model_reply.content and finished['input'] == immutable_input
        receipts = s.rows('SELECT * FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY owner', (identifier,))
        assert len(receipts) == 2 and {r['owner'] for r in receipts} == {'asr', 'llm'}
        assert all(r['phase'] == 'observed' and r['applied'] and r['record_id'] == finished['record_id'] for r in receipts)
        assert next(r for r in receipts if r['owner'] == 'asr')['usage'] == {'calls': 1}
        assert len([r for r in v.peer.calls if r['kind'] == 'asr']) == 1
        assert len(provider_peer.requests(marker)) == 1
        records = s.rows('SELECT * FROM chat_records WHERE session_id=%s', (s.legacy_sid,))
        assert len(records) == 1 and records[0]['record_id'] == finished['record_id']
        assert records[0]['user_message'] == history_text
        assert records[0]['asr_calls'] == 1 and records[0]['total_token_count'] == 18
        assert records[0]['credit_cost'] == voice_price['successful_call_credit'] + Decimal('0.01')
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == [{'credit_balance': Decimal('999.89')}]
        history = s.rows("SELECT message_id,content,metadata FROM channel_messages WHERE session_id=%s AND role='user' AND metadata->>'input_ref'=%s", (s.legacy_sid, input_ref))
        assert len(history) == 1 and history[0]['message_id'] == input_ref + ':user'
        assert history[0]['content'] == history_text
        metadata = decoded(history[0]['metadata'])
        assert metadata['input_ref'] == input_ref
        attachments = metadata['attachments']
        assert len(attachments) == 1 and attachments[0]['type'] == 'voice'
        assert attachments[0]['file_id'] and 'content' not in attachments[0] and 'base64' not in attachments[0]
        assert s.rows('SELECT phase FROM agent_runner_inputs WHERE input_ref=%s', (input_ref,)) == [{'phase': 'applied'}]
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == [{'owner_runner_id': identifier, 'gate': 'delivery'}]
        repeat = v.text.accept(locator)
        assert not repeat['created'] and repeat['input_ref'] == input_ref and repeat['current_runner_id'] == identifier
        assert len([r for r in v.peer.calls if r['kind'] == 'asr']) == 1
        assert not v.peer.errors and not v.state_peer.errors and not s.peer.errors and not provider_peer.errors
    except BaseException as error:
        diagnostic = {'phase': phase, 'exception_class': type(error).__name__,
            'source': safe_source_execution_diagnostic(s, identifier, service_processes),
            'asr_post_count': sum(c['kind'] == 'asr' for c in v.peer.calls)}
        print('SAFE_VOICE_DIAGNOSTIC=' + json.dumps(diagnostic, sort_keys=True))
        raise
    finally:
        asr_release.set()
        model_release.set()
        fleet.close()
