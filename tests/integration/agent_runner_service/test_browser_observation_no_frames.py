"""Real no-frame WS revocation with only capture scheduling timing DI."""
import json
from pathlib import Path
import uuid

import pytest

from .browser_io import browser_page, browser_redis
from .browser_observation_fixture import observation_api, observation_configuration
from .browser_observation_io import observation_socket, ObservationTransportFailure
from .test_browser_observation_authority import wait_closed
from .test_browser_observation import issue_ticket
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_worker import workers, api_pair, prices, accept, runner, decoded
from .test_browser_owner_boundaries import delete_browser_facts

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.asyncio
async def test_real_ws_revokes_without_waiting_for_original_hub_first_frame(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    _, _, redis_environment = browser_redis
    workers.environment.update(redis_environment)
    workers.environment.update(observation_configuration['worker'])
    initial = tool_reply('browser_automation', {}, call_id='no-frame-original-browser-call')
    marker = workers.provider.register(initial, Reply(content=json.dumps(
        {'action': 'ask_user', 'reason': 'Fictional no-frame observation'})))
    initial.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' observe fictional local page', 'url': browser_page.url, 'headless': True})
    accepted = accept(workers.api, actors['a'], marker)
    identifier = accepted['runner_id']
    gate, report = workers.root / 'capture-release', workers.root / 'capture-observed.json'
    worker_id = 'observation-capture-' + uuid.uuid4().hex
    script = Path(__file__).with_name('browser_observation_capture_probe.py')
    child = workers.processes.start([str(script), str(gate), str(report), '--worker-id', worker_id],
        environment=workers.environment, private_working_directory=True)
    workers.children.append((child, worker_id))
    try:
        wait_for(lambda: runner(service_database, identifier)['status'] == 'waiting', timeout=40)
        root = decoded(runner(service_database, identifier)['checkpoint'])['execution']
        run_id = root['resources']['browser_runs']['no-frame-original-browser-call']['run_id']
        with observation_api(workers.processes,
                {**workers.environment, **observation_configuration['gateway']}) as api:
            ticket = issue_ticket(api, actors['a'], run_id)
            async with observation_socket(api.url, run_id, ticket) as wire:
                # The original monitor captures only after a real Hub subscriber exists.
                wait_for(report.is_file, timeout=3)
                assert json.loads(report.read_text()) == {'original_capture_waiting': True}
                # No JPEG is invented: the original capture has not executed.
                try:
                    message = await wire.receive(timeout=.25)
                    assert isinstance(message, str) and json.loads(message).get('type') == 'heartbeat'
                except ObservationTransportFailure as error:
                    assert error.kind == 'TimeoutError'
                before = service_database.rows('SELECT owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                                               (run_id,))[0]['owner_lease_until']
                wait_for(lambda: service_database.rows('SELECT owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                    (run_id,))[0]['owner_lease_until'] > before, timeout=15)
                service_database.rows('DELETE FROM tokens WHERE token=%s', (actors['a'].token,))
                assert await wait_closed(wire, timeout=5) in {4401,4403,4404,4409}
            assert runner(service_database, identifier)['status'] == 'waiting'
            assert not runner(service_database, identifier)['cancel_requested']
            assert child.poll() is None
            assert service_database.rows('SELECT runtime_state FROM bs_browser_runs WHERE run_id=%s',
                                        (run_id,)) == [{'runtime_state': 'live'}]
            assert len(workers.provider.requests(marker)) == 2
    finally:
        gate.touch()
        workers.processes.stop(child)
        delete_browser_facts(service_database, identifier)
