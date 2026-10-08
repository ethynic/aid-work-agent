"""Original real resident Browser lifecycle for bounded human risk shapes."""
from contextlib import contextmanager
from decimal import Decimal
import json
import uuid

from .browser_diagnostics import browser_diagnostics
from .browser_io import owned_descendants, process_is_live
from .browser_observation_fixture import observation_api
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_api import require_status
from .test_browser_owner_boundaries import delete_browser_facts
from .test_worker import accept, runner, decoded


@contextmanager
def human_resident(workers, actor, database, page, redis_environment, configuration,
                   *, decision=None, ask=None, probe=None, probe_arguments=(), max_tasks=2):
    workers.environment.update(redis_environment)
    workers.environment.update(configuration['worker'])
    original = tool_reply('browser_automation', {}, call_id='human-risk-original-call')
    marker = workers.provider.register(original,
        ask or Reply(content=json.dumps({'action': 'ask_user', 'reason': 'Fictional human risk wait'})),
        decision or Reply(content=json.dumps({'action': 'done', 'reason': 'Original page completed'})),
        Reply(content='Human risk original Browser completed'))
    original.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' original fictional page', 'url': page.url, 'headless': True})
    accepted = accept(workers.api, actor, marker)
    identifier = accepted['runner_id']
    worker_id = 'human-risk-' + uuid.uuid4().hex
    report = workers.processes.root / ('human-risk-report-' + uuid.uuid4().hex + '.json')
    module = probe or 'tests.integration.agent_runner_service.browser_human_service_probe'
    process = workers.processes.start(['-m', module, str(report), *map(str, probe_arguments),
        '--worker-id', worker_id, '--max-tasks', str(max_tasks)],
        environment=workers.environment, private_working_directory=True)
    workers.children.append((process, worker_id))
    try:
        wait_for(lambda: runner(database, identifier)['status'] == 'waiting', timeout=40)
        before = runner(database, identifier)
        assert before['attempt'] == 1 and before['worker_id'] is None and before['lease_until'] is None
        native = database.rows('SELECT * FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
        assert len(native) == 1 and native[0]['runtime_state'] == 'live'
        waits = database.rows('SELECT * FROM bs_browser_assistance_requests WHERE runner_id=%s', (identifier,))
        assert len(waits) == 1 and waits[0]['state'] == 'pending'
        with observation_api(workers.processes, {**workers.environment, **configuration['gateway']}) as api:
            assert api.child.pid != process.pid
            yield dict(identifier=identifier, marker=marker, process=process, report=report, actor=actor,
                       api=api, browser=native[0], wait=waits[0], before=before,
                       descendants=owned_descendants(process.pid))
    except Exception:
        diagnosis = browser_diagnostics(workers, database, identifier, marker, page, process)
        if report.exists():
            diagnosis['original_method_observations'] = json.loads(report.read_text())
        diagnosis['human_waits'] = [{'state': w['state'], 'extended': w['extended_at'] is not None,
            'fact_present': w['completion_ref'] is not None}
            for w in database.rows('SELECT state,extended_at,completion_ref FROM bs_browser_assistance_requests WHERE runner_id=%s', (identifier,))]
        print('SAFE_BROWSER_HUMAN_RISK_DIAG=' + json.dumps(diagnosis))
        raise
    finally:
        workers.processes.stop(process)
        delete_browser_facts(database, identifier)


def take(value):
    return require_status(value['api'].call('POST', f"/api/browser/runs/{value['browser']['run_id']}/take_control",
        actor=value['actor'], params={'assistance_id': value['wait']['assistance_id']}), 200)


def complete(value):
    response = require_status(value['api'].call('POST',
        f"/api/browser/runs/{value['browser']['run_id']}/assistance/{value['wait']['assistance_id']}/complete",
        actor=value['actor']), 200)
    assert response['success'] and response['state'] == 'resume_queued'
    assert isinstance(response['continuation_id'], str) and response['continuation_id']
    return response


def assert_owned(workers, database, value):
    row = runner(database, value['identifier'])
    assert row['attempt'] == 1 and row['status'] == 'waiting' and row['result'] is None
    assert row['worker_id'] is None and row['lease_until'] is None and value['process'].poll() is None
    assert database.rows('SELECT runtime_state,owner_lease_until>clock_timestamp() AS live FROM bs_browser_runs WHERE run_id=%s',
        (value['browser']['run_id'],)) == [{'runtime_state': 'live', 'live': True}]
    assert database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
        (value['identifier'],)) == [{'owner_runner_id': value['identifier']}]
    assert len(workers.provider.requests(value['marker'])) == 2 and not workers.provider.errors
    assert database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (value['actor'].session_id,)) == []


def assert_finished(workers, database, value, page):
    wait_for(lambda: runner(database, value['identifier'])['status'] in {'completed', 'failed', 'cancelled'}, timeout=50)
    workers.assert_clean_exit(value['process'])
    row = runner(database, value['identifier'])
    assert row['status'] == 'completed' and row['attempt'] == 2
    assert decoded(row['result'])['output'] == 'Human risk original Browser completed'
    fact = decoded(row['checkpoint'])['execution']['tools']['human-risk-original-call']
    assert fact['result_recorded'] and fact['result']['success']
    native = database.rows('SELECT * FROM bs_browser_runs WHERE runner_id=%s', (value['identifier'],))
    assert len(native) == 1 and native[0]['run_id'] == value['browser']['run_id']
    assert native[0]['owner_boot_id'] == value['browser']['owner_boot_id']
    assert native[0]['browser_epoch'] == value['browser']['browser_epoch']
    assert native[0]['runtime_state'] == 'closed' and native[0]['owner_lease_until'] is None and native[0]['closed_at']
    assert database.rows('SELECT action,status,consumed_attempt FROM agent_runner_controls WHERE runner_id=%s',
        (value['identifier'],)) == [{'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 2}]
    assert database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (value['identifier'],)) == []
    assert not any(process_is_live(pid) for pid in value['descendants'])
    assert page.requests.count(page.path) == 1 and not page.unexpected
    assert len(workers.provider.requests(value['marker'])) == 4 and not workers.provider.errors
    receipts = database.rows('SELECT phase,applied,usage FROM agent_runner_usage_receipts WHERE runner_id=%s', (value['identifier'],))
    assert len(receipts) == 4 and all(item['phase'] == 'observed' and item['applied'] for item in receipts)
    assert sum(decoded(item['usage'])['prompt_tokens'] + decoded(item['usage'])['completion_tokens'] for item in receipts) == 72
    assert database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
        (value['actor'].session_id,)) == [{'total_token_count': 72, 'credit_cost': Decimal('.01')}]
