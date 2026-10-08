"""Finite original-channel compatibility, actual Admission/Worker/PG.

HEAD channel_routes turns ASR parse/network errors into [语音消息] and continues;
account expiry/credit_limit sends the original fixed notice without a new AI
record. External HTTP/token retrieval is localhost IO; no permission, usage,
clock, checkpoint, accepted reference or platform ACK is manufactured.
"""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
import secrets
import time

import pytest

from .kf_completion_fixtures import completion_scope, kf_scope, fresh_customer_text
from .kf_completion_peer import CompletionWireReply
from .kf_completion_orchestration import (original_admission,
    accept_through_original_admission, deliver_through_original_admission)
from .kf_admission_service import KfSourceApi, start_original_worker, verify_original_resources
from .kf_admission_fixtures import cleanup_text_runners
from .kf_voice_peer import KfVoicePeer, AsrReply, wav_bytes
from .kf_voice_fixtures import voice_price, original_timestamped_input
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, decoded, terminal

pytestmark = pytest.mark.integration


def _script_customer_and_state(c):
    c.platform.script('/cgi-bin/kf/customer/batchget', *(CompletionWireReply(payload={
        'errcode': 0, 'customer_list': [{'external_userid': c.scope.actor_id,
            'nickname': 'Fictional original customer'}]}) for _ in range(8)))
    c.platform.script('/cgi-bin/kf/service_state/get',
        *(CompletionWireReply(payload={'errcode': 0, 'service_state': 1}) for _ in range(96)))


@pytest.mark.parametrize('failure', ['bad_json', 'lost_response'])
def test_actual_unknown_asr_uses_original_placeholder_and_next_customer_message_does_not_repost(
        completion_scope, service_processes, provider_peer, prices, voice_price, failure):
    c, s = completion_scope, completion_scope.scope
    _script_customer_and_state(c)
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply(), CompletionWireReply())
    peer = KfVoicePeer(c.platform)
    peer.replies.append(AsrReply(raw_body=b'{invalid-json') if failure == 'bad_json'
        else AsrReply(lose_response=True))
    media_id = 'compat_original_voice_' + s.marker
    peer.media[media_id] = (wav_bytes(), 'audio/wav')
    placeholder_marker = provider_peer.register(Reply(content='Fictional response to original voice placeholder'),
        marker='[语音消息]')
    next_marker = provider_peer.register(Reply(content='Fictional response to next customer message'))
    api = fleet = admission = None
    try:
        api = KfSourceApi(service_processes, peer, provider_environment=provider_peer.environment)
        c.config.api_url = api.url
        admission = original_admission(c, api)
        fleet = Workers(service_processes, api, provider_peer, prices)
        verify_original_resources(fleet, api)
        voice = c.receive({'msgid': 'compat_voice_' + s.marker, 'external_userid': s.actor_id,
            'open_kfid': s.open_kfid, 'origin': 3, 'send_time': int(time.time()),
            'msgtype': 'voice', 'voice': {'media_id': media_id}})[0]

        async def accept_voice():
            for _ in range(16):
                value = await admission.run_once()
                if value is not None:
                    assert value['success'] and value['input_ref'] == voice.stable_key
                    return value
                await asyncio.sleep(c.config.wecom_kf.poll_seconds)
            raise AssertionError('ORIGINAL_ADMISSION_DID_NOT_ACCEPT_VOICE')
        accepted = asyncio.run(accept_voice())
        identifier = accepted['current_runner_id']
        worker_id = 'compat_voice_worker_' + secrets.token_hex(8)
        environment = {**fleet.environment, **api.environment, 'QWEN_MODEL_CODE': fleet.models[0],
            'ALIYUN_ASR_ACCESS_KEY_ID': 'fictional_voice_key',
            'ALIYUN_ASR_ACCESS_KEY_SECRET': 'fictional_voice_secret',
            'ALIYUN_ASR_APPKEY': peer._appkey, 'KF_VOICE_ASR_FIXTURE_URL': peer.base_url,
            'KF_VOICE_ASR_FIXTURE_TOKEN': peer._asr_token}
        first = service_processes.start([str(Path(__file__).with_name('kf_voice_process.py')),
            'worker', '--worker-id', worker_id, '--max-tasks', '1'],
            environment=environment, private_working_directory=True)
        fleet.children.append((first, worker_id))
        c.record_resources('compat_voice_actual_worker', [api.child, first])
        finished = terminal(s.database, identifier, timeout=40)
        fleet.assert_clean_exit(first)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'pending'
        actual = provider_peer.requests(placeholder_marker)
        assert len(actual) == 1
        cp = decoded(finished['checkpoint'])['execution']
        incoming = [m for m in cp['messages'] if m.get('role') == 'user'
            and (m.get('metadata') or {}).get('input_ref') == voice.stable_key]
        assert len(incoming) == 1
        text = original_timestamped_input(incoming[0]['content'], '[语音消息]')
        assert [m.get('content') for m in actual[0]['messages'] if m.get('role') == 'user'].count(text) == 1
        assert s.rows('SELECT content FROM channel_messages WHERE message_id=%s',
            (voice.stable_key + ':user',)) == [{'content': '[语音消息]'}]
        prep = s.rows('SELECT phase,receipt_id,fee_owner_runner_id FROM wecom_kf_input_preparations '
            'WHERE input_ref=%s', (voice.stable_key,))
        assert len(prep) == 1 and prep[0]['phase'] == 'unknown'
        assert prep[0]['fee_owner_runner_id'] == identifier
        asr = s.rows("SELECT phase,usage,applied FROM agent_runner_usage_receipts WHERE receipt_id=%s",
            (prep[0]['receipt_id'],))
        assert asr == [{'phase': 'unknown', 'usage': None, 'applied': False}]
        assert sum(call['kind'] == 'asr' for call in peer.calls) == 1
        asyncio.run(deliver_through_original_admission(c, admission, voice, identifier))
        # Another genuine Crypto message must be processed normally. There is
        # no KF resume button, hand-redelivered tuple or fake known ASR result.
        next_locators = c.receive(fresh_customer_text(s, 'after_unknown_voice_' + s.marker, next_marker))
        next_acceptance = asyncio.run(accept_through_original_admission(c, admission, next_locators))
        next_id = next_acceptance['current_runner_id']
        second, _ = start_original_worker(fleet, api, maximum=1)
        c.record_resources('compat_next_customer_worker', [api.child, first, second])
        assert terminal(s.database, next_id)['status'] == 'completed'
        fleet.assert_clean_exit(second)
        asyncio.run(deliver_through_original_admission(c, admission, next_locators[0], next_id))
        assert len(provider_peer.requests(next_marker)) == 1
        assert sum(call['kind'] == 'asr' for call in peer.calls) == 1
        assert s.rows('SELECT phase,receipt_id,fee_owner_runner_id FROM wecom_kf_input_preparations '
            'WHERE input_ref=%s', (voice.stable_key,)) == prep
        assert s.rows('SELECT phase,usage,applied FROM agent_runner_usage_receipts WHERE receipt_id=%s',
            (prep[0]['receipt_id'],)) == asr
        records = s.rows('SELECT record_id,credit_cost FROM chat_records WHERE session_id=%s', (s.legacy_sid,))
        assert len(records) == len({r['record_id'] for r in records}) == 2
        assert sum(r['credit_cost'] for r in records) == Decimal('0.02')
        assert c.platform.count('/cgi-bin/kf/send_msg') == 2
        assert not peer.errors and not c.platform.errors and not provider_peer.errors
    finally:
        if admission is not None:
            asyncio.run(admission.close())
        if fleet is not None:
            fleet.close()
        if api is not None:
            api.close()
        peer.close()
        cleanup_text_runners(s)
        c.record_resources('compat_unknown_voice_finally')


@pytest.mark.parametrize('restriction', ['expired', 'credit_exhausted'])
def test_actual_account_expiry_or_account_credit_limit_sends_original_notice_without_new_ai_record(
        completion_scope, provider_peer, restriction):
    from src.db.models import ChatRecordDB
    from src.channels.wecom_kf.prompts import MSG_EXPIRED, MSG_CREDIT_EXHAUSTED
    c, s = completion_scope, completion_scope.scope
    _script_customer_and_state(c)
    c.platform.script('/cgi-bin/kf/send_msg', CompletionWireReply())
    old = s.rows('SELECT config FROM tenant_channel_configs WHERE config_id=%s', (s.config_id,))[0]['config']
    payload = json.loads(json.dumps(decoded(old)))
    if restriction == 'expired':
        payload['kf_account'][0]['expire_at'] = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    else:
        payload['kf_account'][0]['credit_limit'] = 1
        # Owned PG historical billing fixture, not this round's model or debit.
        # The original account aggregate must read the real channel/session join.
        with s.database.connect() as conn, conn.cursor() as cursor:
            ChatRecordDB.create_in_tx(cursor, 'prior_account_credit_' + s.marker,
                tenant_id=s.tenant_id, session_id=s.legacy_sid, credit_cost=Decimal('1'),
                user_message='Fictional previous billed round', assistant_message='Fictional previous reply')
            conn.commit()
    s.rows('UPDATE tenant_channel_configs SET config=%s,updated_at=clock_timestamp() WHERE config_id=%s',
        (json.dumps(payload), s.config_id))
    marker = provider_peer.register(Reply(content='This restricted receipt must not call the model'))
    api = admission = None
    try:
        before_records = s.rows('SELECT record_id,credit_cost FROM chat_records WHERE session_id=%s', (s.legacy_sid,))
        before_balance = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))
        locator = c.receive(fresh_customer_text(s, 'account_limit_' + s.marker, marker))[0]
        api = KfSourceApi(c.processes, c.platform)
        c.config.api_url = api.url
        admission = original_admission(c, api)
        c.record_resources('account_restriction_actual_api', [api.child])
        for _ in range(6):
            assert asyncio.run(admission.run_once()) is None
        expected = MSG_EXPIRED if restriction == 'expired' else MSG_CREDIT_EXHAUSTED
        history = s.rows('SELECT role,content FROM channel_messages WHERE tenant_id=%s AND session_id=%s '
            'AND content=%s', (s.tenant_id, s.legacy_sid, expected))
        assert history == [{'role': 'assistant', 'content': expected}]
        assert c.platform.count('/cgi-bin/kf/send_msg') == 1
        assert provider_peer.requests(marker) == []
        assert s.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s '
            'AND message_id=%s', (locator.account_id, locator.message_id)) == [{'accepted_input_ref': None}]
        assert s.rows('SELECT 1 FROM agent_runners WHERE tenant_id=%s AND session_id=%s',
            (s.tenant_id, s.legacy_sid)) == []
        assert s.rows('SELECT record_id,credit_cost FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == before_records
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,)) == before_balance
        assert not c.platform.errors and not provider_peer.errors
    finally:
        if admission is not None:
            asyncio.run(admission.close())
        if api is not None:
            api.close()
        cleanup_text_runners(s)
        s.rows('UPDATE tenant_channel_configs SET config=%s,updated_at=clock_timestamp() WHERE config_id=%s',
            (json.dumps(decoded(old)), s.config_id))
        c.record_resources('account_restriction_finally')
