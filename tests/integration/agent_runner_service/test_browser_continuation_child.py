"""Real original child Browser call and a completed sibling; internal owner only."""
import copy
from decimal import Decimal
import json
import uuid

import pytest

from .browser_io import browser_page, browser_redis, owned_descendants, process_is_live
from .browser_diagnostics import browser_diagnostics
from .browser_observation_fixture import observation_configuration
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_browser_owner_boundaries import delete_browser_facts
from .test_worker import workers, api_pair, prices, accept, runner, decoded
from .test_worker_controls import child_profile

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_actual_original_child_continuation_preserves_completed_sibling_and_parent_pairing(
        workers, actors, service_database, browser_page, browser_redis, child_profile, observation_configuration):
    _, _, environment = browser_redis
    workers.environment.update(environment)
    workers.environment.update(observation_configuration['worker'])
    first = Reply()
    sibling = Reply(content='Original completed sibling fact')
    browser_call = tool_reply('browser_automation', {}, call_id='continuation-child-browser-call')
    marker = workers.provider.register(first, sibling, browser_call,
        Reply(content=json.dumps({'action': 'ask_user', 'reason': 'Original child confirmation'})),
        Reply(content=json.dumps({'action': 'done', 'reason': 'Original child ready'})),
        Reply(content='Original Browser child result'), Reply(content='Original parent paired result'))
    first.tool_calls = tool_reply('delegate_to_subagent',
        {'subagent_name': child_profile[0], 'task_description': marker + ' completed sibling'},
        call_id='continuation-sibling-delegate').tool_calls + tool_reply('delegate_to_subagent',
        {'subagent_name': child_profile[0], 'task_description': marker + ' original Browser child'},
        call_id='continuation-child-delegate').tool_calls
    browser_call.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' fictional original page', 'url': browser_page.url, 'headless': True})
    accepted = accept(workers.api, actors['a'], marker)
    identifier = accepted['runner_id']
    command = workers.processes.root / ('child-continuation-command-' + uuid.uuid4().hex + '.json')
    report = workers.processes.root / ('child-continuation-report-' + uuid.uuid4().hex + '.json')
    process = None
    descendants = set()
    try:
        worker_id = 'child-continuation-' + uuid.uuid4().hex
        process = workers.processes.start(['-m', 'tests.integration.agent_runner_service.browser_continuation_probe',
            str(command), str(report), '--worker-id', worker_id, '--max-tasks', '2'],
            environment=workers.environment, private_working_directory=True)
        workers.children.append((process, worker_id))
        wait_for(lambda: runner(service_database, identifier)['status'] == 'waiting', timeout=65)
        before = decoded(runner(service_database, identifier)['checkpoint'])['execution']
        saved_sibling = copy.deepcopy(before['children']['continuation-sibling-delegate'])
        saved_child = copy.deepcopy(before['children']['continuation-child-delegate'])
        assert saved_sibling['checkpoint']['outcome'] == 'completed'
        assert saved_sibling['checkpoint']['output'] == sibling.content
        assert not before['resources'].get('browser_runs')
        assert not saved_sibling['checkpoint']['resources'].get('browser_runs')
        waits = service_database.rows('SELECT assistance_id,run_id,agent_execution_id,tool_call_id FROM bs_browser_assistance_requests WHERE runner_id=%s',
                                     (identifier,))
        assert len(waits) == 1 and waits[0]['agent_execution_id'] == saved_child['execution_id']
        assert waits[0]['tool_call_id'] == 'continuation-child-browser-call'
        assert len(workers.provider.requests(marker)) == 4
        descendants = owned_descendants(process.pid)
        command.write_text(json.dumps({'runner_id': identifier, 'run_id': waits[0]['run_id'],
                                       'assistance_id': waits[0]['assistance_id']}))
        wait_for(lambda: runner(service_database, identifier)['status'] in {'completed', 'failed', 'cancelled'}, timeout=65)
        workers.assert_clean_exit(process)
        row = runner(service_database, identifier)
        assert row['status'] == 'completed' and row['attempt'] == 2
        assert decoded(row['result'])['output'] == 'Original parent paired result'
        root = decoded(row['checkpoint'])['execution']
        current_sibling = root['children']['continuation-sibling-delegate']
        current_child = root['children']['continuation-child-delegate']
        assert current_sibling['execution_id'] == saved_sibling['execution_id']
        for key in ('model_calls', 'messages', 'tools', 'output', 'resources'):
            assert current_sibling['checkpoint'][key] == saved_sibling['checkpoint'][key]
        assert current_child['execution_id'] == saved_child['execution_id']
        assert current_child['task_record']['task_id'] == saved_child['task_record']['task_id']
        assert current_child['checkpoint']['outcome'] == 'completed'
        assert current_child['checkpoint']['tools']['continuation-child-browser-call']['result_recorded']
        assert root['tools']['continuation-child-delegate']['result_recorded']
        assert not root['resources'].get('browser_runs') and not current_sibling['checkpoint']['resources'].get('browser_runs')
        assert len(root['model_calls']) == 2 and len(current_sibling['checkpoint']['model_calls']) == 1
        assert len(current_child['checkpoint']['model_calls']) == 2
        native = service_database.rows('SELECT runner_execution_id,runner_tool_call_id,runtime_state,owner_lease_until FROM bs_browser_runs WHERE runner_id=%s',
                                      (identifier,))
        assert native == [{'runner_execution_id': saved_child['execution_id'],
                          'runner_tool_call_id': 'continuation-child-browser-call', 'runtime_state': 'closed', 'owner_lease_until': None}]
        observations = json.loads(report.read_text())
        assert observations['completion_calls'] == observations['resume_calls'] == 1
        assert observations['same_original_page_ops'] and observations['same_original_executor']
        assert observations['confirmation_count_after'] == observations['confirmation_count_before'] + 1
        receipts = service_database.rows('SELECT execution_id,phase,applied,usage FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 7 and all(item['phase'] == 'observed' and item['applied'] for item in receipts)
        assert sum(item['execution_id'] == identifier for item in receipts) == 2
        assert sum(item['execution_id'] == saved_sibling['execution_id'] for item in receipts) == 1
        assert sum(item['execution_id'] == saved_child['execution_id'] for item in receipts) == 4
        assert service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
            (actors['a'].session_id,)) == [{'total_token_count': 126, 'credit_cost': Decimal('0.02')}]
        assert service_database.rows('SELECT action,status,consumed_attempt FROM agent_runner_controls WHERE runner_id=%s',
            (identifier,)) == [{'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 2}]
        assert len(workers.provider.requests(marker)) == 7 and not workers.provider.errors
        assert browser_page.requests.count(browser_page.path) == 1 and not browser_page.unexpected
        assert not any(process_is_live(pid) for pid in descendants)
        assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == []
    except Exception:
        diagnosis = browser_diagnostics(workers, service_database, identifier, marker, browser_page, process)
        checkpoint = decoded(runner(service_database, identifier)['checkpoint']) or {}
        diagnosis['child_outcomes'] = [(fact.get('checkpoint') or {}).get('outcome') for fact in
            (checkpoint.get('execution') or {}).get('children', {}).values()]
        if report.exists():
            diagnosis['original_method_observations'] = json.loads(report.read_text())
        print('SAFE_BROWSER_CHILD_CONTINUATION_DIAG=' + json.dumps(diagnosis))
        raise
    finally:
        if process is not None:
            workers.processes.stop(process)
        delete_browser_facts(service_database, identifier)
