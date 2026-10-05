"""Real resident Browser preparation; caller supplies the frozen sidecar config.

No environment names or internal routes are inferred here. The existing public
BrowserAutomationTool and original default worker perform every state change.
"""
from contextlib import contextmanager
import json
import secrets
import socket

import bcrypt
import pytest

from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_worker import accept, decoded, runner
from .test_api import body, require_status
from .test_browser_owner_boundaries import delete_browser_facts
from .web_gateway import WebGateway


class ObservationConfiguration:
    """Only environment plumbing; credentials never appear in fixture repr."""
    def __init__(self):
        with socket.socket() as reserve:
            reserve.bind(('127.0.0.1', 0))
            port = reserve.getsockname()[1]
        token = secrets.token_urlsafe(32)
        endpoint = f'http://127.0.0.1:{port}/internal/runner-browser'
        worker = {
            'AGENT_RUNNER_BROWSER_OWNER_ENABLED': 'true',
            'AGENT_RUNNER_BROWSER_VIEW_ENABLED': 'true',
            'AGENT_RUNNER_BROWSER_OWNER_ENDPOINT': endpoint,
            'AGENT_RUNNER_BROWSER_ALLOWED_ENDPOINTS': endpoint,
            'AGENT_RUNNER_BROWSER_BIND_HOST': '127.0.0.1',
            'AGENT_RUNNER_BROWSER_BIND_PORT': str(port),
            'AGENT_RUNNER_BROWSER_GATEWAY_SERVICE_ID': 'browser-web',
            'AGENT_RUNNER_BROWSER_GATEWAY_TOKEN_HASH': bcrypt.hashpw(
                token.encode(), bcrypt.gensalt(rounds=4)).decode(),
        }
        self._environment = {'worker': worker,
            'gateway': {**worker, 'AGENT_RUNNER_BROWSER_GATEWAY_TOKEN': token}}

    def __getitem__(self, key):
        return self._environment[key]

    def __repr__(self):
        return '<ObservationConfiguration task-owned Browser peer>'


@pytest.fixture
def observation_configuration():
    return ObservationConfiguration()


@contextmanager
def parked_observation_runtime(workers, actor, database, page, redis_environment,
                               sidecar_environment, *, submission_headers=None):
    workers.environment.update(redis_environment)
    workers.environment.update(sidecar_environment)
    workers.environment['AGENT_RUNNER_BROWSER_OWNER_ENABLED'] = 'true'
    original = tool_reply('browser_automation', {}, call_id='observation-original-browser-call')
    marker = workers.provider.register(original, Reply(content=json.dumps(
        {'action': 'ask_user', 'reason': 'Fictional browser observation wait'})))
    original.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' observe fictional local page', 'url': page.url, 'headless': True})
    if submission_headers is None:
        accepted = accept(workers.api, actor, marker)
    else:
        # A NULL-tenant platform administrator may explicitly own a real-tenant
        # session. The real service validates its current DB role and headers.
        accepted = require_status(workers.api.call('POST', '/v1/runners',
            headers=submission_headers, json=body(actor, text=marker)), 202)['runner']
    identifier = accepted['runner_id']
    child = None
    try:
        child, worker_id = workers.start(once=False)
        wait_for(lambda: runner(database, identifier)['status'] == 'waiting', timeout=40)
        row = runner(database, identifier)
        original_fact = decoded(row['checkpoint'])['execution']['resources']['browser_runs'][
            'observation-original-browser-call']
        browser = database.rows('SELECT * FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
        assert len(browser) == 1 and browser[0]['run_id'] == original_fact['run_id']
        assert browser[0]['runtime_state'] == 'live' and browser[0]['state'] == 'WAITING_HUMAN'
        assert browser[0]['owner_worker_id'] == worker_id
        assert row['worker_id'] is None and row['lease_until'] is None and row['result'] is None
        assert len(workers.provider.requests(marker)) == 2 and not workers.provider.errors
        yield {'runner_id': identifier, 'run_id': browser[0]['run_id'],
               'worker': child, 'worker_id': worker_id, 'marker': marker,
               'browser': browser[0], 'checkpoint_ref': original_fact}
    finally:
        if child is not None:
            workers.processes.stop(child)
        delete_browser_facts(database, identifier)


@contextmanager
def observation_api(processes, environment):
    api = WebGateway(processes, environment=environment,
        factory='tests.integration.agent_runner_service.browser_observation_api:create_app',
        access_log=False)
    try:
        yield api
    finally:
        api.close()
