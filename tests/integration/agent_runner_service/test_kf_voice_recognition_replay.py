"""Prepared real PG/page replay contract plus one original ASR producer.

The original media-only normalized fact is inserted by the current original
page port with absent Recognition, matching the legacy stored representation.
This is not a claim of executing the old binary; it exercises current migration
compatibility without rewriting an accepted payload/digest or making a fake
recognized result. Changed existing Recognition and media remain conflicts.
"""
from copy import deepcopy
from decimal import Decimal
import secrets

import pytest

from .kf_ingress_fixtures import kf_scope, text_message
from .kf_voice_fixtures import voice_scope, voice_price
from .kf_voice_peer import AsrReply, wav_bytes
from .kf_admission_service import verify_original_resources
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, terminal
from .test_kf_voice_results import start_voice_worker, post_count, safe_voice_error

pytestmark = pytest.mark.integration


def original_replay_page(voice, message):
    from src.channels.wecom_kf.ingress_auth import bounded_page
    text, scope = voice.text, voice.scope
    text.serial += 1
    query, body = scope.material.encrypted_callback(scope.open_kfid)
    text.repository.accept_callback(scope.tenant_id, scope.config_id, query, body)
    lease, _ = text.repository.claim('voice_replay_setup_' + scope.marker)
    assert lease is not None
    try:
        return text.repository.commit_page(lease, bounded_page({'errcode': 0,
            'has_more': 0, 'next_cursor': 'text_scope_' + str(text.serial),
            'msg_list': [message]}, lease.proof))
    finally:
        text.repository.release(lease)


def test_actual_media_only_original_fact_recognition_replay_advances_cursor_without_rewriting_then_uses_asr(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    from src.channels.wecom_kf.ingress_auth import KfIngressError
    v, s = voice_scope, voice_scope.scope
    media_id = 'old_media_' + secrets.token_hex(12)
    v.peer.media[media_id] = (wav_bytes(), 'audio/wav')
    original = text_message(s, 'old_voice_' + secrets.token_hex(12))
    original.pop('text')
    original['msgtype'], original['voice'] = 'voice', {'media_id': media_id}
    locator = v.text.receive(original)[0]
    accepted = v.text.accept(locator)
    identifier, input_ref = accepted['current_runner_id'], accepted['input_ref']
    before = s.rows('SELECT * FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s',
        (locator.account_id, locator.namespace, locator.message_id))[0]
    assert 'recognition' not in before['payload']['voice']
    assert before['accepted_input_ref'] == input_ref
    replay = deepcopy(original)
    replay['voice']['recognition'] = 'New replay text must not silently replace original fact'
    assert original_replay_page(v, replay) == {'received': 0, 'has_more': False}
    after = s.rows('SELECT * FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s',
        (locator.account_id, locator.namespace, locator.message_id))[0]
    assert after == before
    assert s.rows('SELECT cursor FROM wecom_kf_account_sync WHERE account_id=%s',
        (locator.account_id,)) == [{'cursor': 'text_scope_' + str(v.text.serial)}]

    # Narrow PG/page conflict proofs, not additional paid producer matrices.
    bad_media = deepcopy(replay)
    bad_media['voice']['media_id'] = 'different_untrusted_media'
    with pytest.raises(KfIngressError):
        original_replay_page(v, bad_media)
    strict = deepcopy(original)
    strict['msgid'] = 'known_recognition_' + secrets.token_hex(12)
    strict['voice']['recognition'] = 'Original trusted Recognition'
    strict_locator = v.text.receive(strict)[0]
    strict_before = s.rows('SELECT payload,payload_digest FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s',
        (strict_locator.account_id, strict_locator.namespace, strict_locator.message_id))
    altered = deepcopy(strict)
    altered['voice']['recognition'] = 'Changed Recognition must conflict'
    with pytest.raises(KfIngressError):
        original_replay_page(v, altered)
    assert s.rows('SELECT payload,payload_digest FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s',
        (strict_locator.account_id, strict_locator.namespace, strict_locator.message_id)) == strict_before

    marker = provider_peer.register(Reply(content='Original media-only fact actual ASR final output'))
    transcript = marker + ' actual original paid ASR result'
    v.peer.replies.append(AsrReply(payload={'status': 20000000, 'result': transcript}))
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    try:
        verify_original_resources(fleet, v.api)
        child = start_voice_worker(fleet, v)
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(child)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        assert post_count(v) == 1 and len(provider_peer.requests(marker)) == 1
        prep = s.rows('SELECT phase,result_kind,transcript,fee_owner_runner_id FROM wecom_kf_input_preparations WHERE input_ref=%s', (input_ref,))
        assert prep == [{'phase': 'known', 'result_kind': 'provider', 'transcript': transcript,
            'fee_owner_runner_id': identifier}]
        assert s.rows('SELECT payload,payload_digest,accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s AND namespace=%s AND message_id=%s',
            (locator.account_id, locator.namespace, locator.message_id)) == [{key: before[key]
                for key in ('payload', 'payload_digest', 'accepted_input_ref')}]
        assert s.rows('SELECT user_message,asr_calls,credit_cost FROM chat_records WHERE record_id=%s', (finished['record_id'],)) == [
            {'user_message': '[ASR识别结果] ' + transcript, 'asr_calls': 1,
                'credit_cost': Decimal('0.11')}]
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'old_fact_replay_asr', error)
        raise
    finally:
        fleet.close()
