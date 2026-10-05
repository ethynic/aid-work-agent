"""Actual resident bridge isolates a declared private checkpoint corruption."""
import copy
from decimal import Decimal
import json
import uuid

import pytest

from .browser_io import browser_page, browser_redis
from .browser_diagnostics import browser_diagnostics
from .browser_observation_fixture import observation_configuration
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_browser_continuation_races import ask, done, native_wait, complete_original, request_control, completion_fact
from .test_browser_owner_boundaries import delete_browser_facts
from .test_worker import workers, api_pair, prices, accept, runner, decoded

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_actual_bad_checkpoint_completion_bridge_keeps_worker_alive_for_another_legal_session(
        workers, actors, service_database, browser_page, browser_redis, observation_configuration):
    _, _, environment = browser_redis
    workers.environment.update(environment)
    workers.environment.update(observation_configuration['worker'])
    initial = tool_reply('browser_automation', {}, call_id='race-original-browser-call')
    marker = workers.provider.register(initial, ask(), done(), Reply(content='Must not run broken Browser'))
    initial.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' original private scope', 'url': browser_page.url, 'headless': True})
    own = accept(workers.api, actors['a'], marker)
    suffix = uuid.uuid4().hex
    command, report, entered, release, bridge = [workers.processes.root / (prefix + suffix + '.json') for prefix in
        ('isolation-command-', 'isolation-report-', 'isolation-entered-', 'isolation-release-', 'isolation-bridge-')]
    worker_id = 'isolated-owner-' + suffix
    process = workers.processes.start(['-m', 'tests.integration.agent_runner_service.browser_continuation_isolation_probe',
        str(command), str(report), str(entered), str(release), 'pending', str(bridge),
        '--worker-id', worker_id, '--max-tasks', '3'], environment=workers.environment, private_working_directory=True)
    workers.children.append((process, worker_id))
    value = dict(identifier=own['runner_id'], marker=marker, process=process, command=command, report=report,
                 entered=entered, release=release, sequence=0, descendants=set())
    try:
        wait_for(entered.exists, timeout=40)
        wait = native_wait(service_database, value)
        resume = request_control(workers, actors['a'], value, 'resume')
        observed = complete_original(value, wait)
        assert observed['sampled'] and observed['ref_present'] and not observed['bridged']
        original = completion_fact(service_database, wait)
        before = runner(service_database, own['runner_id'])
        assert before['status'] == 'waiting' and before['attempt'] == 1
        assert before['worker_id'] is None and before['lease_until'] is None
        assert service_database.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s',
            (resume['control_id'],)) == [{'status': 'accepted', 'consumed_attempt': None}]
        bad = copy.deepcopy(decoded(before['checkpoint']))
        bad['execution']['resources'] = []
        # Deliberate private PG corruption in this fictional runner only. It is
        # not a naturally produced checkpoint or a substituted Browser result.
        service_database.rows('UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s',
                              (json.dumps(bad), own['runner_id']))
        next_marker = workers.provider.register(Reply(content='Other legal session completes'))
        other = accept(workers.api, actors['b'], next_marker)
        release.touch()
        wait_for(lambda: bridge.exists() and json.loads(bridge.read_text())['typed_scope_failures'] > 0, timeout=20)
        wait_for(lambda: runner(service_database, other['runner_id'])['status'] == 'completed', timeout=30)
        assert process.poll() is None, 'A bad owned resource stopped the actual resident worker'
        assert json.loads(bridge.read_text())['original_bridge_returned'] > 0
        after = runner(service_database, own['runner_id'])
        assert after['attempt'] == before['attempt'] == 1 and after['result'] is None
        assert decoded(after['checkpoint']) == bad
        assert completion_fact(service_database, wait) == original
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
            (own['runner_id'],)) == [{'owner_runner_id': own['runner_id']}]
        assert service_database.rows('SELECT status,consumed_attempt,error_code FROM agent_runner_controls WHERE control_id=%s',
            (resume['control_id'],)) == [{'status': 'rejected', 'consumed_attempt': None, 'error_code': 'CHECKPOINT_TREE_INVALID'}]
        assert len(workers.provider.requests(marker)) == 2 and len(workers.provider.requests(next_marker)) == 1
        assert not workers.provider.errors and browser_page.requests.count(browser_page.path) == 1
        assert json.loads(report.read_text())['resume_calls'] == 0
        assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actors['a'].session_id,)) == []
        assert service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
            (actors['b'].session_id,)) == [{'total_token_count': 18, 'credit_cost': Decimal('.01')}]
        assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (other['runner_id'],)) == []
    except Exception:
        diagnosis = browser_diagnostics(workers, service_database, own['runner_id'], marker, browser_page, process)
        if bridge.exists():
            diagnosis['original_bridge_observations'] = json.loads(bridge.read_text())
        print('SAFE_BROWSER_CONTINUATION_ISOLATION_DIAG=' + json.dumps(diagnosis))
        raise
    finally:
        release.touch()
        # Corrupt private CP remains unchanged; cleanup does not heal it to
        # produce another bridge/action. Stop exact owned process first.
        workers.processes.stop(process)
        delete_browser_facts(service_database, own['runner_id'])
