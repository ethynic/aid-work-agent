"""Actual default worker readiness rejects before any Runner acquisition."""
import socket
from urllib.parse import urlsplit, urlunsplit

import pytest

from .browser_io import browser_redis
from .browser_observation_fixture import observation_configuration
from .test_worker import workers, api_pair, prices, accept, runner
from .provider import Reply

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.parametrize('fault', ['occupied_listener','https','wrong_advertised_port'])
def test_actual_factory_readiness_failure_does_not_acquire_or_touch_foreign_listener(
        workers, actors, service_database, browser_redis, observation_configuration, fault):
    _, _, environment = browser_redis
    workers.environment.update(environment)
    workers.environment.update(observation_configuration['worker'])
    endpoint = urlsplit(workers.environment['AGENT_RUNNER_BROWSER_OWNER_ENDPOINT'])
    foreign = None
    if fault == 'occupied_listener':
        foreign = socket.socket()
        foreign.bind(('127.0.0.1', endpoint.port))
        foreign.listen(8)
    else:
        if fault == 'https':
            wrong = urlunsplit(endpoint._replace(scheme='https'))
        else:
            with socket.socket() as reserve:
                reserve.bind(('127.0.0.1', 0))
                port = reserve.getsockname()[1]
            assert port != endpoint.port
            wrong = urlunsplit(endpoint._replace(netloc='127.0.0.1:'+str(port)))
        workers.environment['AGENT_RUNNER_BROWSER_OWNER_ENDPOINT'] = wrong
        workers.environment['AGENT_RUNNER_BROWSER_ALLOWED_ENDPOINTS'] = wrong
    marker = workers.provider.register(Reply(content='Must not dispatch before listener ready'))
    identifier = accept(workers.api, actors['a'], marker)['runner_id']
    child = None
    try:
        child, _ = workers.start()
        assert child.wait(timeout=15) != 0, 'Invalid Browser listener admitted a worker'
        row = runner(service_database, identifier)
        assert row['status'] == 'queued' and row['attempt'] == 0
        assert row['worker_id'] is None and row['lease_until'] is None
        assert row['result'] is None
        assert service_database.rows('SELECT 1 FROM chat_records WHERE record_id=%s', (row['record_id'],)) == []
        assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                     (identifier,)) == []
        assert service_database.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                     (identifier,)) == []
        assert service_database.rows('SELECT 1 FROM bs_browser_runs WHERE runner_id=%s', (identifier,)) == []
        assert len(workers.provider.requests(marker)) == 0
        if foreign is not None:
            # Real socket is still the original fixture's, never closed by Factory.
            with socket.create_connection(('127.0.0.1', endpoint.port), timeout=1):
                pass
            assert foreign.fileno() >= 0
        else:
            with socket.socket() as empty:
                empty.settimeout(.2)
                assert empty.connect_ex(('127.0.0.1', endpoint.port)) != 0
    finally:
        if child is not None:
            workers.processes.stop(child)
        if foreign is not None:
            foreign.close()
