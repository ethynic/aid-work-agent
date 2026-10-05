"""Prepared source-authority socket risks, no mutable-source execution yet."""
from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
import uuid

import pytest

from .kf_admission_fixtures import text_scope, kf_scope
from .kf_admission_peer import StateReply
from .kf_admission_service import KfSourceApi
from .test_worker import runner, decoded

pytestmark = pytest.mark.integration


def rejection(response, status, code):
    assert response.status_code == status
    value = response.json()
    assert value['success'] is False and value['error'] == code


def test_actual_native_receipt_read_authority_and_legacy_receiptless_read_cancel_do_not_upgrade(text_scope, service_processes):
    t, s = text_scope, text_scope.scope
    intent = {'client_request_id': uuid.uuid4().hex, 'session': {'kind': 'channel', 'session_id': s.legacy_sid},
        'source': 'wecom_kf', 'channel_user_id': s.actor_id, 'channel_chat_id': s.open_kfid,
        'profile_id': 'main', 'text': 'Receiptless native must be refused'}
    rejection(t.api.call('POST', '/v1/runners', headers=t.api.headers(scope=s), json=intent), 403, 'SOURCE_RECEIPT_REQUIRED')
    assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
    locator = t.text('authority_')
    absent = locator.__class__('wecom_kf', locator.account_id, 'sync', 'not_received_' + s.marker)
    rejection(t.post(absent), 404, 'SOURCE_RECEIPT_NOT_FOUND')
    wrong_key = t.api.call('POST', '/v1/source-inputs', json={**locator.value(), 'client_request_id': 'untrusted_client_key'})
    rejection(wrong_key, 422, 'INVALID_SOURCE_RECEIPT')
    rejection(t.post(locator, headers={}), 401, 'SERVICE_UNAUTHORIZED')
    original_inbox = s.rows('SELECT payload,payload_digest FROM wecom_kf_inbox WHERE account_id=%s AND message_id=%s', (locator.account_id, locator.message_id))[0]
    altered = {**original_inbox['payload'], 'text': {'content': 'Fictional tampered stored source'}}
    try:
        s.rows('UPDATE wecom_kf_inbox SET payload=%s::jsonb WHERE account_id=%s AND message_id=%s',
            (json.dumps(altered), locator.account_id, locator.message_id))
        rejection(t.post(locator), 409, 'SOURCE_RECEIPT_CONFLICT')
        assert not t.facts()
    finally:
        s.rows('UPDATE wecom_kf_inbox SET payload=%s::jsonb,payload_digest=%s WHERE account_id=%s AND message_id=%s',
            (json.dumps(original_inbox['payload']), original_inbox['payload_digest'], locator.account_id, locator.message_id))
    # Two independent socket requests compete for the same real receipt/root.
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(t.post, (locator, locator)))
    assert all(response.status_code == 202 for response in responses)
    values = [response.json() for response in responses]
    assert sorted(value['created'] for value in values) == [False, True]
    assert len({(value['input_ref'], value['accepted_runner_id'], value['current_runner_id']) for value in values}) == 1
    accepted = values[0]
    identifier, ref = accepted['current_runner_id'], accepted['input_ref']
    good = t.api.headers(scope=s, input_ref=ref)
    assert t.api.call('GET', '/v1/runners/' + identifier, headers=good).status_code == 200
    before = runner(s.database, identifier)
    for headers in (t.api.headers(scope=s), {**good, 'X-AgentRunner-Channel-User': 'different_full_actor'},
                    {**good, 'X-AgentRunner-Source-Input': 'source_foreign'}):
        assert t.api.call('GET', '/v1/runners/' + identifier, headers=headers).status_code in (403, 404)
    assert runner(s.database, identifier) == before
    assert s.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s AND message_id=%s',
        (locator.account_id, locator.message_id)) == [{'accepted_input_ref': ref}]
    # A real server legacy-policy acceptance, made with the supported gate OFF
    # and a nonnative trusted peer, is not converted into a native source proof.
    legacy = KfSourceApi(service_processes, t.platform, source_id='kf_legacy_fixture', native_enabled=False)
    try:
        old = legacy.call('POST', '/v1/runners', headers=legacy.headers(scope=s), json={**intent,
            'client_request_id': uuid.uuid4().hex, 'text': 'Already accepted legacy input'})
        assert old.status_code == 202
        old_id = old.json()['runner']['runner_id']
        original = runner(s.database, old_id)
        assert not decoded(original['checkpoint']).get('source_initial_ref')
        # Current native-enabled server still recognizes the actual old row as
        # read/cancel authority through caller actor + the original ownedSession.
        assert t.api.call('GET', '/v1/runners/' + old_id, headers=t.api.headers(scope=s)).status_code == 200
        cancelled = t.api.call('POST', '/v1/runners/' + old_id + '/cancel', headers=t.api.headers(scope=s))
        assert cancelled.status_code == 200
        after = runner(s.database, old_id)
        assert after['cancel_requested'] and not decoded(after['checkpoint']).get('source_initial_ref')
        assert len(t.facts()) == 1
    finally:
        legacy.close()


def test_actual_sdk_state_and_config_changed_during_original_http_preparation_cannot_accept(text_scope):
    t, s = text_scope, text_scope.scope
    locator = t.text('state_')
    # Strict original SDK responses: state !=1 or bool-as-int is not admission.
    t.platform.states[:] = [StateReply({'errcode': 0, 'service_state': 3}),
        StateReply({'errcode': 0, 'service_state': True}), StateReply({'errcode': True, 'service_state': 1})]
    for _ in range(3):
        rejection(t.post(locator), 409, 'SOURCE_STATE_UNAVAILABLE')
        assert not t.facts()
    release = threading.Event()
    held = StateReply(release=release)
    t.platform.states.append(held)
    original = s.rows('SELECT config,updated_at FROM tenant_channel_configs WHERE config_id=%s', (s.config_id,))[0]
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(t.post, locator)
            try:
                assert held.arrived.wait(5)
                # Genuine current SQL version change while no application SQL
                # lock is held across the original SDK network request.
                s.rows('UPDATE tenant_channel_configs SET updated_at=clock_timestamp() WHERE config_id=%s', (s.config_id,))
                release.set()
                rejection(pending.result(timeout=15), 409, 'SOURCE_STATE_UNAVAILABLE')
            finally:
                release.set()
        assert t.facts() == []
        assert s.rows('SELECT accepted_input_ref FROM wecom_kf_inbox WHERE account_id=%s AND message_id=%s',
            (locator.account_id, locator.message_id)) == [{'accepted_input_ref': None}]
        assert s.rows('SELECT 1 FROM agent_runners WHERE session_id=%s', (s.legacy_sid,)) == []
    finally:
        release.set()
        # This original column is TEXT; restore its stored bytes exactly.
        s.rows('UPDATE tenant_channel_configs SET config=%s,updated_at=%s WHERE config_id=%s',
            (original['config'], original['updated_at'], s.config_id))
    assert s.rows('SELECT config,updated_at FROM tenant_channel_configs WHERE config_id=%s',
        (s.config_id,)) == [original]
    t.platform.states.append(StateReply())
    response = t.post(locator)
    # Preserve only the stable code on a future failure, never the raw body.
    error_code = response.json().get('error') if response.headers.get('content-type', '').startswith('application/json') else None
    safe_code = error_code if isinstance(error_code, str) and re.fullmatch(r'(?:SOURCE|SERVICE|RUNNER|KF)_[A-Z_]+', error_code) else 'UNLISTED_RESPONSE'
    assert response.status_code == 202, {
        'status': response.status_code,
        'error_code': safe_code,
    }
    accepted = response.json()
    assert accepted['created'] is True
    assert len(t.facts()) == 1 and len(t.platform.calls) == 5
    assert not t.platform.errors
