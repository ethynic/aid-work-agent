"""Real domain/Worker/Web read model; external model and page are loopback peers.

Prepared for the card slice. No production tests run until its source freezes.
This file does not claim browser UI end-to-end coverage.
"""
from datetime import datetime
import json
import threading

import pytest

from .browser_human_page import browser_human_page
from .browser_human_service_fixture import human_resident, take, complete, assert_finished
from .browser_io import browser_redis
from .browser_observation_fixture import observation_configuration
from .conftest import wait_for
from .provider import Reply
from .test_api import start_api_pair, require_status
from .test_worker import workers, prices, runner, decoded

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.fixture(scope='module')
def api_pair(service_processes):
    return start_api_pair(service_processes, extra_environment={
        'AGENT_RUNNER_WEB_SERVICE_ID': 'runner-test', 'REDIS_ENABLED': 'false'})


def public_read(value, database):
    """Real HTTP query and paginated list must agree with the committed PG row."""
    identifier, api, actor = value['identifier'], value['api'], value['actor']
    queried = require_status(api.call('GET', f'/api/chat/runners/{identifier}', actor=actor), 200)['runner']
    page = require_status(api.call('GET', f'/api/chat/sessions/{actor.session_id}/runners', actor=actor), 200)
    listed = [row for row in page['runners'] + page['active_runners'] if row['runner_id'] == identifier]
    assert listed and all(row == queried for row in listed)
    stored = runner(database, identifier)
    assert queried['view_revision'] == stored['view_revision']
    assert queried['snapshot'] == decoded(stored['public_snapshot'])
    assert queried['revision'] == stored['revision']
    assert not any(key in queried for key in ('checkpoint', 'owner_boot_id', 'browser_epoch', 'input'))
    return queried, stored


def test_actual_domain_card_updates_and_original_worker_terminal_history_share_authoritative_public_view(
        workers, actors, service_database, browser_human_page, browser_redis, observation_configuration):
    actor = actors['a']
    _, _, environment = browser_redis
    workers.environment.update({
        'AGENT_RUNNER_WEB_ENABLED': 'true', 'AGENT_RUNNER_API_URL': workers.api.urls[0],
        'AGENT_RUNNER_WEB_SERVICE_ID': workers.api.service_id,
        'AGENT_RUNNER_WEB_SERVICE_TOKEN': workers.api._service_token,
    })
    decision_gate = threading.Event()
    decision = Reply(content=json.dumps({'action': 'done', 'reason': 'Original page finished'}), release=decision_gate)
    try:
        with human_resident(workers, actor, service_database, browser_human_page, environment,
                observation_configuration, decision=decision,
                probe='tests.integration.agent_runner_service.browser_card_probe') as value:
            original, original_row = public_read(value, service_database)
            original_card = original['snapshot']['browserAssistance']
            assert original_card['assistance_id'] == value['wait']['assistance_id']
            assert original_card['run_id'] == value['browser']['run_id']
            assert original_card['state'] == 'pending' and original_card['view_available'] is True
            assert take(value) == {'success': True, 'state': 'controlling'}
            taken, taken_row = public_read(value, service_database)
            assert taken['snapshot']['browserAssistance']['state'] == 'controlling'
            assert taken['view_revision'] == original['view_revision'] + 1
            assert taken_row['revision'] == original_row['revision']
            assert decoded(taken_row['checkpoint']) == decoded(original_row['checkpoint'])

            path = f"/api/browser/runs/{value['browser']['run_id']}/assistance/{value['wait']['assistance_id']}/extend"
            extended = require_status(value['api'].call('POST', path, actor=actor), 200)
            first, first_row = public_read(value, service_database)
            assert datetime.fromisoformat(first['snapshot']['browserAssistance']['expires_at']) == datetime.fromisoformat(extended['expires_at'])
            assert first['view_revision'] == taken['view_revision'] + 1
            assert first_row['revision'] == taken_row['revision']
            assert decoded(first_row['checkpoint']) == decoded(taken_row['checkpoint'])
            repeated = require_status(value['api'].call('POST', path, actor=actor), 200)
            again, again_row = public_read(value, service_database)
            assert repeated['expires_at'] == extended['expires_at']
            assert again == first and again_row['revision'] == first_row['revision']

            complete(value)
            wait_for(lambda: decision.arrived.is_set(), timeout=20)
            queued, queued_row = public_read(value, service_database)
            assert queued_row['attempt'] == 2
            assert queued['snapshot']['browserAssistance']['assistance_id'] == original_card['assistance_id']
            assert queued['snapshot']['browserAssistance']['state'] == 'resume_queued'
            assert queued['snapshot']['browserAssistance']['completion_status'] in {'observed', 'started'}
            assert queued['snapshot']['browserAssistance']['view_available'] is False
            assert queued['view_revision'] > first['view_revision']
            decision_gate.set()
            assert_finished(workers, service_database, value, browser_human_page)
            terminal, terminal_row = public_read(value, service_database)
            terminal_card = terminal['snapshot']['browserAssistance']
            assert terminal['status'] == 'completed' and terminal_card['state'] == 'resumed'
            assert terminal_card['completion_status'] == 'closed' and terminal_card['view_available'] is False
            intent = json.loads(value['report'].with_name(value['report'].name + '.intent.json').read_text())
            assert intent['intent_present'] and intent['before_digest'] == intent['after_digest']
            assert 'assistant_metadata' not in terminal['result']
            messages = service_database.rows('SELECT role,metadata FROM chat_messages WHERE session_id=%s', (actor.session_id,))
            assistant = [message for message in messages if message['role'] == 'assistant'
                         and decoded(message['metadata'] or {}).get('runner_id') == value['identifier']]
            assert assistant
            assert decoded(assistant[-1]['metadata'])['browserAssistance'] == terminal_card
    finally:
        decision_gate.set()
