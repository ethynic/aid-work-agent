"""Actual wait/Browser/Redis/PG followed by narrow external Redis IO faults."""
import json
from pathlib import Path
import uuid

import pytest

from .browser_io import browser_page, browser_redis
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, runner, decoded

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.parametrize('boundary', ['get', 'bind'])
def test_postwait_redis_failure_preserves_mandatory_wait_and_runner_claim(
        workers, actors, service_database, browser_page, browser_redis, boundary):
    actor = actors['a']
    _, _, environment = browser_redis
    workers.environment.update(environment)
    workers.environment['AGENT_RUNNER_BROWSER_OWNER_ENABLED'] = 'true'
    initial = tool_reply('browser_automation', {}, call_id='postwait-browser-call')
    marker = workers.provider.register(initial, Reply(content=json.dumps(
        {'action': 'ask_user', 'reason': 'Fictional manual page confirmation'})))
    initial.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' inspect fixture', 'url': browser_page.url, 'headless': True})
    accepted = accept(workers.api, actor, marker)
    identifier = accepted['runner_id']
    report = workers.root / ('postwait-' + boundary + '.json')
    probe = Path(__file__).with_name('browser_postwait_fault_probe.py')
    worker_id = 'postwait-worker-' + uuid.uuid4().hex
    process = workers.processes.start([str(probe), boundary, str(report), '--worker-id', worker_id, '--once'],
        environment=workers.environment, private_working_directory=True)
    workers.children.append((process, worker_id))
    try:
        wait_for(lambda: runner(service_database, identifier)['status'] == 'interrupted', timeout=30)
        workers.assert_clean_exit(process)
        assert json.loads(report.read_text()) == {'boundary': boundary,
            'mandatory_wait_committed': True, 'original_redis_record_read': True,
            'exception_class': 'CheckpointFailure', 'authoritative_failure': True,
            'expected_stable_code': True}
        row = runner(service_database, identifier)
        assert row['result'] is None and row['finished_at'] is None
        root = decoded(row['checkpoint'])['execution']
        call = root['tools']['postwait-browser-call']
        assert call['phase'] == 'dispatching' and not call.get('result_recorded')
        fact = root['resources']['browser_runs']['postwait-browser-call']
        waits = service_database.rows('SELECT runner_wait_id,assistance_id FROM bs_browser_assistance_requests WHERE runner_id=%s',
                                      (identifier,))
        assert len(waits) == 1 and fact['waits'] == {waits[0]['runner_wait_id']: waits[0]['assistance_id']}
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                      (identifier,)) == [{'owner_runner_id': identifier}]
        assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actor.session_id,)) == []
        assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors
        assert browser_page.path in browser_page.requests and not browser_page.unexpected
        later = accept(workers.api, actor, marker + ' queued followup')
        from src.services.agent_runner.execution_repository import ExecutionRepository
        assert ExecutionRepository(service_database.connect).acquire('postwait-contender', 30) is None
        assert runner(service_database, later['runner_id'])['status'] == 'queued'
        assert len(workers.provider.requests(marker)) == 2
    finally:
        workers.processes.stop(process)
        service_database.rows('DELETE FROM bs_browser_resume_jobs WHERE run_id IN (SELECT run_id FROM bs_browser_runs WHERE runner_id=%s)',
                              (identifier,))
        service_database.rows('DELETE FROM bs_browser_assistance_requests WHERE runner_id=%s', (identifier,))
        service_database.rows('DELETE FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
