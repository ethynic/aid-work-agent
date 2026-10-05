"""Ticket IO failure is not allowed to produce a usable native ticket."""
import pytest

from .browser_io import browser_page, browser_redis
from .browser_observation_fixture import parked_observation_runtime, observation_configuration
from .web_gateway import WebGateway
from .test_worker import workers, api_pair, prices, runner

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_real_native_ticket_write_failure_returns_no_usable_ticket_or_owner_change(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    client, prefix, environment = browser_redis
    with parked_observation_runtime(workers, actors['a'], service_database, browser_page,
            environment, observation_configuration['worker']) as original:
        api = WebGateway(workers.processes,
            environment={**workers.environment, **observation_configuration['gateway']},
            factory='tests.integration.agent_runner_service.browser_observation_ticket_fault_api:create_app',
            access_log=False)
        try:
            response = api.call('POST', f'/api/browser/runs/{original["run_id"]}/view_ticket', actor=actors['a'])
            assert response.status_code == 503
            body = response.json()
            assert 'ticket' not in body
            assert body.get('detail', {}).get('error_code') == 'WEB_PRESENCE_REQUIRED'
            assert list(client.scan_iter(match=f'{prefix}:browser_view_ticket:*')) == []
            assert runner(service_database, original['runner_id'])['status'] == 'waiting'
            assert not runner(service_database, original['runner_id'])['cancel_requested']
            assert original['worker'].poll() is None
            assert len(workers.provider.requests(original['marker'])) == 2
        finally:
            api.close()
