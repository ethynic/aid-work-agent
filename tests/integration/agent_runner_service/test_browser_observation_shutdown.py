"""Real in-flight sidecar handshake stopped before original auth returns.

Only auth scheduling and invocation of original stop_observations use DI.
This does not pretend to be full OS worker shutdown: the original Browser
stays parked/live so a late accept would be a genuine admission failure.
"""
import asyncio
import json
from pathlib import Path
import uuid

import pytest

from .browser_io import browser_page, browser_redis
from .browser_observation_fixture import observation_api, observation_configuration
from .browser_observation_io import observation_socket, ObservationTransportFailure
from .test_browser_observation import issue_ticket
from .test_browser_owner_boundaries import delete_browser_facts
from .provider import Reply, tool_reply
from .conftest import wait_for
from .test_worker import workers, api_pair, prices, accept, runner, decoded

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.asyncio
async def test_real_inflight_sidecar_handshake_cannot_accept_after_observation_stop(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    _, _, redis_environment = browser_redis
    workers.environment.update(redis_environment)
    workers.environment.update(observation_configuration['worker'])
    initial = tool_reply('browser_automation', {}, call_id='stop-handshake-original-call')
    marker = workers.provider.register(initial, Reply(content=json.dumps(
        {'action': 'ask_user', 'reason': 'Fictional original Browser wait'})))
    initial.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' observe local fixture', 'url': browser_page.url, 'headless': True})
    identifier = accept(workers.api, actors['a'], marker)['runner_id']
    paths = [workers.root / name for name in ('stop-observations', 'stop-observed',
                                              'handshake-entered', 'auth-release')]
    stop, stopped, entered, release = paths
    worker_id = 'handshake-timing-' + uuid.uuid4().hex
    script = Path(__file__).with_name('browser_observation_handshake_probe.py')
    child = workers.processes.start([str(script), *map(str, paths), '--worker-id', worker_id],
        environment=workers.environment, private_working_directory=True)
    workers.children.append((child, worker_id))
    observation = None
    try:
        await asyncio.to_thread(wait_for, lambda: runner(service_database, identifier)['status'] == 'waiting', timeout=40)
        node = decoded(runner(service_database, identifier)['checkpoint'])['execution']
        run_id = node['resources']['browser_runs']['stop-handshake-original-call']['run_id']
        with observation_api(workers.processes,
                {**workers.environment, **observation_configuration['gateway']}) as api:
            ticket = issue_ticket(api, actors['a'], run_id)

            async def connect_original():
                try:
                    async with observation_socket(api.url, run_id, ticket) as wire:
                        await wire.frame(timeout=5)
                except ObservationTransportFailure as error:
                    assert error.status in {401,403,404,409,4401,4403,4404,4409}
                    return 'denied'
                return 'accepted'

            observation = asyncio.create_task(connect_original())
            await asyncio.to_thread(wait_for, entered.is_file, timeout=4)
            stop.touch()
            await asyncio.to_thread(wait_for, stopped.is_file, timeout=2)
            release.touch()
            assert await asyncio.wait_for(observation, timeout=6) == 'denied'
            row = runner(service_database, identifier)
            assert row['status'] == 'waiting' and not row['cancel_requested']
            assert child.poll() is None
            assert service_database.rows('SELECT runtime_state FROM bs_browser_runs WHERE run_id=%s',
                                        (run_id,)) == [{'runtime_state': 'live'}]
            assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                                        (identifier,)) == [{'owner_runner_id': identifier}]
            assert len(workers.provider.requests(marker)) == 2
    finally:
        release.touch()
        stop.touch()
        if observation is not None:
            await asyncio.gather(observation, return_exceptions=True)
        workers.processes.stop(child)
        delete_browser_facts(service_database, identifier)
