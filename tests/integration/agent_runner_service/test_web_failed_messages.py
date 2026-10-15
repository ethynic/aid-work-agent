"""Failed-message preferences persist without removing execution/accounting facts.

Exercise real HTTP auth, PostgreSQL row locks and separate gateway processes.
Only provider IO uses the existing local fixture; no model execution is needed.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import json
import threading
import uuid

import pytest

from .test_api import require_status
from .test_web_gateway import api_pair, gateways, submission, submit
from .test_worker import decoded, prices, workers

pytestmark = pytest.mark.integration


def failed(gateway, actor, database):
    row = submit(gateway, actor, submission(actor))
    database.rows("""UPDATE agent_runners SET status='failed', settlement_status='settled',
        result='{"status":"failed","output":"","error_code":"EXECUTION_FAILED"}'::jsonb
        WHERE runner_id=%s""", (row['runner_id'],))
    return row['runner_id']


def path(actor, runner_id):
    return f'/api/sessions/{actor.session_id}/failed-messages/{runner_id}'


def preferences(gateway, actor):
    return require_status(gateway.call('GET', f'/api/sessions/{actor.session_id}/messages', actor=actor), 200)


def metadata(database, actor):
    return decoded(database.rows('SELECT metadata FROM chat_sessions WHERE session_id=%s',
                                 (actor.session_id,))[0]['metadata']) or {}


def test_preferences_survive_another_gateway_and_preserve_messages_records_and_metadata(gateways, actors, service_database):
    actor = actors['a']
    gateway = gateways.start()
    runner_id = failed(gateway, actor, service_database)
    service_database.rows('UPDATE chat_sessions SET metadata=%s::jsonb WHERE session_id=%s',
                         (json.dumps({'unrelated': {'keep': True}, 'chat_failed_message_ui': {'future': 7}}), actor.session_id))
    for role, content in [('user', 'original input'), ('assistant', 'partial output')]:
        service_database.rows('''INSERT INTO chat_messages(message_id, session_id, role, content, metadata)
            VALUES (%s,%s,%s,%s,%s)''',
            (runner_id+':'+role, actor.session_id, role, content, json.dumps({'runner_id': runner_id})))
    service_database.rows('''INSERT INTO chat_records(record_id,session_id,tenant_id,user_id,status,credit_cost)
        VALUES (%s,%s,%s,%s,'failed',0.01)''',
        ('record-'+uuid.uuid4().hex, actor.session_id, actor.tenant_id, actor.user_id))
    facts = service_database.rows('SELECT * FROM agent_runners WHERE runner_id=%s', (runner_id,))
    records = service_database.rows('SELECT * FROM chat_records WHERE session_id=%s', (actor.session_id,))
    messages = preferences(gateway, actor)['messages']
    require_status(gateway.call('POST', path(actor, runner_id), actor=actor, json={'action': 'hide'}), 200)
    require_status(gateway.call('POST', path(actor, runner_id), actor=actor,
                               json={'action': 'retry', 'client_request_id': 'fresh-attempt'}), 200)
    # A new process cannot obtain the result from the original process cache.
    refreshed = preferences(gateways.start(), actor)
    assert refreshed['failed_message_ui'] == {'future': 7, 'hidden_failed': {runner_id: True},
                                             'retry_sources': {'fresh-attempt': runner_id}}
    assert refreshed['messages'] == messages
    assert metadata(service_database, actor)['unrelated'] == {'keep': True}
    assert service_database.rows('SELECT * FROM agent_runners WHERE runner_id=%s', (runner_id,)) == facts
    assert service_database.rows('SELECT * FROM chat_records WHERE session_id=%s', (actor.session_id,)) == records


def test_concurrent_hides_and_retry_links_do_not_overwrite_each_other(gateways, actors, service_database):
    actor = actors['a']
    gateways_pair = [gateways.start(), gateways.start()]
    ids = [failed(gateways_pair[0], actor, service_database) for _ in range(4)]
    service_database.rows('UPDATE chat_sessions SET metadata=%s::jsonb WHERE session_id=%s',
                         (json.dumps({'preserved': [1, 2, 3]}), actor.session_id))
    barrier = threading.Barrier(8)

    def update(item):
        index, action = item
        barrier.wait(timeout=10)
        body = {'action': action}
        if action == 'retry':
            body['client_request_id'] = 'retry-'+str(index)
        response = gateways_pair[index % 2].call('POST', path(actor, ids[index]), actor=actor, json=body)
        require_status(response, 200)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, [(index, action) for index in range(4) for action in ('hide', 'retry')]))
    saved = metadata(service_database, actor)
    assert saved == {'preserved': [1, 2, 3], 'chat_failed_message_ui': {
        'hidden_failed': dict.fromkeys(ids, True), 'retry_sources': {'retry-'+str(i): rid for i, rid in enumerate(ids)}}}


def test_repeating_an_action_is_idempotent_but_retry_source_cannot_be_rebound(gateways, actors, service_database):
    actor = actors['a']
    gateway = gateways.start()
    first, second = [failed(gateway, actor, service_database) for _ in range(2)]
    for _ in range(2):
        require_status(gateway.call('POST', path(actor, first), actor=actor, json={'action': 'hide'}), 200)
        require_status(gateway.call('POST', path(actor, first), actor=actor,
                                   json={'action': 'retry', 'client_request_id': 'immutable-key'}), 200)
    response = require_status(gateway.call('POST', path(actor, second), actor=actor,
        json={'action': 'retry', 'client_request_id': 'immutable-key'}), 409)
    assert response['detail'] == 'RETRY_SOURCE_CONFLICT'
    assert preferences(gateway, actor)['failed_message_ui'] == {
        'hidden_failed': {first: True}, 'retry_sources': {'immutable-key': first}}


@pytest.mark.parametrize('section', ['hidden_failed', 'retry_sources'])
def test_each_map_limit_rejects_growth_but_keeps_existing_keys_idempotent(gateways, actors, service_database, section):
    actor = actors['a']
    gateway = gateways.start()
    first, second = [failed(gateway, actor, service_database) for _ in range(2)]
    existing_key = first if section == 'hidden_failed' else 'existing-retry'
    entries = {str(index): True if section == 'hidden_failed' else first for index in range(1999)}
    entries[existing_key] = True if section == 'hidden_failed' else first
    service_database.rows('UPDATE chat_sessions SET metadata=%s::jsonb WHERE session_id=%s',
                         (json.dumps({'chat_failed_message_ui': {section: entries}}), actor.session_id))
    action = {'action': 'hide'} if section == 'hidden_failed' else {'action': 'retry', 'client_request_id': existing_key}
    require_status(gateway.call('POST', path(actor, first), actor=actor, json=action), 200)
    new_action = {'action': 'hide'} if section == 'hidden_failed' else {'action': 'retry', 'client_request_id': 'new-retry'}
    response = require_status(gateway.call('POST', path(actor, second), actor=actor, json=new_action), 409)
    assert response['detail'] == 'FAILED_MESSAGE_PREFERENCES_FULL'
    assert metadata(service_database, actor)['chat_failed_message_ui'][section] == entries


def test_current_owner_tenant_and_null_scope_are_checked_for_reads_and_writes(gateways, actors, service_database):
    gateway = gateways.start()
    owner = actors['a']
    runner_id = failed(gateway, owner, service_database)
    for foreign in [actors['a_other'], actors['b'], actors['global_other']]:
        require_status(gateway.call('GET', f'/api/sessions/{owner.session_id}/messages', actor=foreign), 404)
        require_status(gateway.call('POST', path(owner, runner_id), actor=foreign, json={'action': 'hide'}), 404)
    headers = {**gateway.headers(owner), 'X-Tenant-Id': actors['b'].tenant_id}
    require_status(gateway.call('GET', f'/api/sessions/{owner.session_id}/messages', headers=headers), 403)
    require_status(gateway.call('POST', path(owner, runner_id), headers=headers, json={'action': 'hide'}), 403)
    admin = actors['global']
    admin_runner = failed(gateway, admin, service_database)
    require_status(gateway.call('POST', path(admin, admin_runner), actor=admin, json={'action': 'hide'}), 200)
    assert preferences(gateway, admin)['failed_message_ui']['hidden_failed'] == {admin_runner: True}
    assert metadata(service_database, owner).get('chat_failed_message_ui') is None


@pytest.mark.parametrize('mutation', ['expired', 'revoked', 'inactive'])
def test_cached_success_cannot_authorize_after_fresh_identity_is_invalidated(gateways, actors, service_database, mutation):
    gateway = gateways.start()
    actor = actors['a']
    runner_id = failed(gateway, actor, service_database)
    preferences(gateway, actor)
    require_status(gateway.call('POST', path(actor, runner_id), actor=actor, json={'action': 'hide'}), 200)
    before = metadata(service_database, actor)
    if mutation == 'expired':
        service_database.rows('UPDATE tokens SET expires_at=%s WHERE token=%s',
                              (datetime.now()-timedelta(seconds=1), actor.token))
    elif mutation == 'revoked':
        service_database.rows('DELETE FROM tokens WHERE token=%s', (actor.token,))
    else:
        service_database.rows("UPDATE users SET status='inactive' WHERE user_id=%s", (actor.user_id,))
    require_status(gateway.call('GET', f'/api/sessions/{actor.session_id}/messages', actor=actor), 401)
    require_status(gateway.call('POST', path(actor, runner_id), actor=actor,
                               json={'action': 'retry', 'client_request_id': 'late-key'}), 401)
    assert metadata(service_database, actor) == before


def test_wrong_runner_scope_status_and_missing_runner_cannot_change_preferences(gateways, actors, service_database):
    gateway = gateways.start()
    actor = actors['a']
    queued = submit(gateway, actor, submission(actor))['runner_id']
    foreign = failed(gateway, actors['a_other'], service_database)
    other_session = 'second-session-'+uuid.uuid4().hex
    service_database.rows('INSERT INTO chat_sessions(session_id,user_id,tenant_id,title) VALUES (%s,%s,%s,%s)',
                          (other_session, actor.user_id, actor.tenant_id, 'Second owned session'))
    other = failed(gateway, replace(actor, session_id=other_session), service_database)
    wrong_kind = failed(gateway, actor, service_database)
    wrong_source = failed(gateway, actor, service_database)
    service_database.rows("UPDATE agent_runners SET session_kind='channel' WHERE runner_id=%s", (wrong_kind,))
    service_database.rows("UPDATE agent_runners SET source='feishu' WHERE runner_id=%s", (wrong_source,))
    for runner_id in [queued, foreign, other, wrong_kind, wrong_source, 'missing-runner']:
        for body in [{'action': 'hide'}, {'action': 'retry', 'client_request_id': 'rejected-key'}]:
            response = require_status(gateway.call('POST', path(actor, runner_id), actor=actor, json=body), 409)
            assert response['detail'] == 'FAILED_MESSAGE_NOT_FOUND'
    assert metadata(service_database, actor).get('chat_failed_message_ui') is None


def test_invalid_request_shapes_and_unauthed_calls_never_write_preferences(gateways, actors, service_database):
    gateway = gateways.start()
    actor = actors['a']
    runner_id = failed(gateway, actor, service_database)
    invalid = [{}, {'action': 'delete'}, {'action': 'retry'}, {'action': 'hide', 'client_request_id': 'extra-key'},
        {'action': 'retry', 'client_request_id': ''}, {'action': 'retry', 'client_request_id': 'x'*129},
        {'action': 'hide', 'extra': True}, {'action': 'retry', 'client_request_id': 4}]
    for body in invalid:
        require_status(gateway.call('POST', path(actor, runner_id), actor=actor, json=body), 422)
    require_status(gateway.call('POST', path(actor, 'x'*129), actor=actor, json={'action': 'hide'}), 422)
    require_status(gateway.call('POST', path(actor, runner_id), json={'action': 'hide'}), 401)
    require_status(gateway.call('GET', f'/api/sessions/{actor.session_id}/messages'), 401)
    assert metadata(service_database, actor).get('chat_failed_message_ui') is None
