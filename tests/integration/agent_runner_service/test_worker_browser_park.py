"""Real parked Browser keeps its original independent runtime lease.

Narrow fresh-process observation wrappers call original Browser operations;
the actual worker/Engine/Manager/Playwright/Redis/PG remain unchanged. This does
not assert sidecar, human-control completion, or cross-process resume support.
"""
import json
from pathlib import Path
import uuid

import pytest

from .browser_io import browser_page, browser_redis, redis_run_view
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, runner, decoded

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_actual_wait_is_committed_before_suspension_and_browser_lease_survives_park(
        workers, actors, service_database, browser_page, browser_redis):
    actor = actors['a']
    client, prefix, environment = browser_redis
    workers.environment.update(environment)
    workers.environment['AGENT_RUNNER_BROWSER_OWNER_ENABLED'] = 'true'
    initial = tool_reply('browser_automation', {}, call_id='parked-original-browser-call')
    marker = workers.provider.register(initial, Reply(content=json.dumps({'action': 'ask_user', 'reason': 'Fictional manual page confirmation'})))
    initial.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' inspect fixture', 'url': browser_page.url, 'headless': True})
    accepted = accept(workers.api, actor, marker)
    identifier = accepted['runner_id']
    report = workers.root / 'browser-producer-observations.json'
    probe = Path(__file__).with_name('browser_producer_probe.py')
    worker_id = 'browser-resident-' + uuid.uuid4().hex
    child = workers.processes.start([str(probe), str(report), '--worker-id', worker_id],
                                    environment=workers.environment, private_working_directory=True)
    workers.children.append((child, worker_id))
    try:
        wait_for(lambda: runner(service_database, identifier)['status'] == 'waiting', timeout=30)
        row = runner(service_database, identifier)
        assert row['worker_id'] is None and row['lease_until'] is None
        assert row['result'] is None and row['finished_at'] is None
        root = decoded(row['checkpoint'])['execution']
        fact = root['resources']['browser_runs']['parked-original-browser-call']
        run_id = fact['run_id']
        runs = service_database.rows('SELECT * FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
        assert len(runs) == 1 and runs[0]['state'] == 'WAITING_HUMAN' and runs[0]['runtime_state'] == 'live'
        assert runs[0]['owner_worker_id'] == worker_id and runs[0]['owner_boot_id'] == fact['owner_boot_id']
        assists = service_database.rows('SELECT runner_id,runner_wait_id,assistance_id,agent_execution_id,tool_call_id FROM bs_browser_assistance_requests WHERE runner_id=%s', (identifier,))
        assert len(assists) == 1
        assistance = assists[0]
        assert assistance['agent_execution_id'] == identifier and assistance['tool_call_id'] == 'parked-original-browser-call'
        assert fact['waits'] == {assistance['runner_wait_id']: assistance['assistance_id']}
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                     (identifier,)) == [{'owner_runner_id': identifier}]
        events = json.loads(report.read_text())
        publish = next(event for event in events if event['boundary'] == 'before_redis_publish')
        assert publish['mandatory_pg_starting'] and publish['redis_absent'] and publish['distributed']
        start = next(event for event in events if event['boundary'] == 'actual_executor_start')
        assert start['result_ok'] and start['pid']
        boundaries = [event['boundary'] for event in events]
        assert boundaries.index('mandatory_wait_committed') < boundaries.index('before_suspension_return')
        exposed = next(event for event in events if event['boundary'] == 'before_suspension_return')
        assert exposed['mandatory_wait_exists'] and exposed['assistance_id'] == assistance['assistance_id']
        run_key = f'{prefix}:browser_run:{actor.tenant_id}:{run_id}'
        owner_key = f'{prefix}:browser_owner:{actor.tenant_id}:{run_id}'
        assert redis_run_view(client, run_key, owner_key) == {'run_id': run_id, 'state': 'WAITING_HUMAN', 'lease_live': True}
        before = runs[0]['owner_lease_until']
        wait_for(lambda: service_database.rows('SELECT owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                                               (run_id,))[0]['owner_lease_until'] > before, timeout=15)
        assert runner(service_database, identifier)['status'] == 'waiting' and child.poll() is None
        assert browser_page.path in browser_page.requests and not browser_page.unexpected
        assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors
        assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actor.session_id,)) == []
        # Gracefully stop only our resident worker; real Browser shutdown must
        # confirm physical resource closure, leaving the parked Runner claim.
        workers.processes.stop(child)
        workers.assert_clean_exit(child)
        events = json.loads(report.read_text())
        closed = [event for event in events if event['boundary'] == 'actual_executor_close']
        assert closed and closed[-1]['result_closed'] and closed[-1]['resource_confirmed']
        assert service_database.rows('SELECT runtime_state,owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                                     (run_id,)) == [{'runtime_state': 'closed', 'owner_lease_until': None}]
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                     (identifier,)) == [{'owner_runner_id': identifier}]
    finally:
        workers.processes.stop(child)
        service_database.rows('DELETE FROM bs_browser_resume_jobs WHERE run_id IN (SELECT run_id FROM bs_browser_runs WHERE runner_id=%s)', (identifier,))
        service_database.rows('DELETE FROM bs_browser_assistance_requests WHERE runner_id=%s', (identifier,))
        service_database.rows('DELETE FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
