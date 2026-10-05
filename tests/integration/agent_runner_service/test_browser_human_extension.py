"""PG's once-only expiry survives real Redis failure and a stale projection race."""
from datetime import datetime
import json
import threading
import uuid

import pytest

from .browser_human_page import browser_human_page
from .browser_io import browser_redis
from .browser_observation_fixture import observation_configuration
from .browser_human_service_fixture import human_resident, take, complete, assert_owned, assert_finished
from .provider import Reply
from .test_api import require_status
from .test_worker import workers, api_pair, prices, runner
from .conftest import wait_for

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_actual_extend_once_pg_commit_redis_failure_reprojects_same_expiry_and_delayed_projection_does_not_revert_queued(
        workers, actors, service_database, browser_human_page, browser_redis, observation_configuration):
    actor = actors['a']
    client, prefix, environment = browser_redis
    entered = workers.processes.root / ('original-project-entered-' + uuid.uuid4().hex)
    release = workers.processes.root / ('original-project-release-' + uuid.uuid4().hex)
    decision_gate = threading.Event()
    decision = Reply(content=json.dumps({'action': 'done', 'reason': 'Original page completed'}), release=decision_gate)
    with human_resident(workers, actor, service_database, browser_human_page, environment,
            observation_configuration, decision=decision,
            probe='tests.integration.agent_runner_service.browser_human_projection_probe',
            probe_arguments=(entered, release)) as value:
        api, run_id, wait_id = value['api'], value['browser']['run_id'], value['wait']['assistance_id']
        path = f'/api/browser/runs/{run_id}/assistance/{wait_id}/extend'
        assistance_key = f'{prefix}:browser_assistance:{actor.tenant_id}:{wait_id}'
        lock_key, lock_value = assistance_key + ':cas', 'fixture-owned-' + uuid.uuid4().hex
        assert take(value) == {'success': True, 'state': 'controlling'}
        cached_before = json.loads(client.get(assistance_key))
        assert client.set(lock_key, lock_value, nx=True, ex=20)
        try:
            denied = require_status(api.call('POST', path, actor=actor), 409)
            assert denied['detail']['error_code'] == 'HUMAN_STATE_PROJECTION_UNAVAILABLE'
            committed = service_database.rows('SELECT extended_at,expires_at::timestamptz AS expiry_instant '
                'FROM bs_browser_assistance_requests WHERE assistance_id=%s', (wait_id,))[0]
            assert committed['extended_at'] is not None and committed['expiry_instant'].tzinfo is not None
            assert json.loads(client.get(assistance_key))['expires_at'] == cached_before['expires_at']
            assert_owned(workers, service_database, value)
        finally:
            if client.get(lock_key) == lock_value:
                client.delete(lock_key)

        responses, errors = [], []
        def delayed_request():
            try:
                responses.append(api.call('POST', path, actor=actor))
            except Exception as error:
                errors.append(type(error).__name__)
        thread = threading.Thread(target=delayed_request, daemon=False)
        thread.start()
        try:
            wait_for(entered.exists, timeout=5)
            projected = require_status(api.call('POST', path, actor=actor), 200)
            assert projected['success']
            assert datetime.fromisoformat(projected['expires_at']) == committed['expiry_instant']
            assert service_database.rows('SELECT extended_at,expires_at::timestamptz AS expiry_instant '
                'FROM bs_browser_assistance_requests WHERE assistance_id=%s', (wait_id,)) == [committed]
            complete(value)
            # Native completion's authority is PG, not a legacy Redis state
            # publication. Release the old projection after actual consumption.
            wait_for(lambda: service_database.rows('SELECT consumed_attempt FROM agent_runner_controls '
                'WHERE runner_id=%s AND action=\'browser_complete\'', (value['identifier'],))
                == [{'consumed_attempt': 2}], timeout=5)
            assert runner(service_database, value['identifier'])['attempt'] == 2
            completion_before = service_database.rows('SELECT completion_ref,completion_fact '
                'FROM bs_browser_assistance_requests WHERE assistance_id=%s', (wait_id,))[0]
            assert completion_before['completion_ref']
            before_release = json.loads(client.get(assistance_key))
        except BaseException:
            decision_gate.set()
            raise
        finally:
            release.touch()
            thread.join(timeout=12)
        try:
            assert not thread.is_alive() and errors == [] and len(responses) == 1
            delayed = require_status(responses[0], 200)
            assert delayed['expires_at'] == projected['expires_at']
            cached = json.loads(client.get(assistance_key))
            assert not (before_release['state'] not in {'pending', 'controlling'}
                and cached['state'] in {'pending', 'controlling'})
            assert cached['extended'] and cached['expires_at'] == committed['expiry_instant'].timestamp()
            completion_after = service_database.rows('SELECT completion_ref,completion_fact '
                'FROM bs_browser_assistance_requests WHERE assistance_id=%s', (wait_id,))[0]
            assert completion_after['completion_ref'] == completion_before['completion_ref']
            phases = ['observed', 'started', 'completed']
            assert phases.index(completion_after['completion_fact']['continuation']['phase']) >= phases.index(
                completion_before['completion_fact']['continuation']['phase'])
            assert service_database.rows('SELECT extended_at,expires_at::timestamptz AS expiry_instant '
                'FROM bs_browser_assistance_requests WHERE assistance_id=%s', (wait_id,)) == [committed]
            assert decision.arrived.wait(timeout=15)
        finally:
            decision_gate.set()
        assert_finished(workers, service_database, value, browser_human_page)
