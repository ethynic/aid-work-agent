"""Real ticket consume race and finite dedicated-peer/proof rejection.

Negative assertions deliberately corrupt only a task-owned Redis ticket after
real issuance. They never manufacture permission or a Browser runtime.
"""
import asyncio
import json
from urllib.parse import urlsplit, urlunsplit

import aiohttp
import pytest

from .browser_io import browser_page, browser_redis
from .browser_observation_fixture import parked_observation_runtime, observation_api, observation_configuration
from .browser_observation_io import observation_socket, ObservationTransportFailure
from .test_browser_observation import issue_ticket
from .test_browser_observation_authority import refused_socket
from .test_worker import workers, api_pair, prices, runner

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def stored_ticket(client, prefix, ticket):
    from src.core.cache_utils import CacheKeys
    key = f'{prefix}:{CacheKeys.BROWSER_VIEW_TICKET}:{ticket.split(".",1)[0]}'
    raw = client.get(key)
    if raw is None:
        raise AssertionError('Original single-use ticket not stored in Redis')
    return key, json.loads(raw)


@pytest.mark.asyncio
async def test_one_original_redis_ticket_has_exactly_one_real_competing_ws_winner(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    client, prefix, environment = browser_redis
    with parked_observation_runtime(workers, actors['a'], service_database, browser_page,
            environment, observation_configuration['worker']) as original:
        with observation_api(workers.processes,
                {**workers.environment, **observation_configuration['gateway']}) as api:
            ticket = issue_ticket(api, actors['a'], original['run_id'])
            key, _ = stored_ticket(client, prefix, ticket)
            ready = asyncio.Event()

            async def compete():
                await ready.wait()
                try:
                    async with observation_socket(api.url, original['run_id'], ticket) as wire:
                        _, jpeg = await wire.frame()
                        assert len(jpeg) > 100
                        return 'winner'
                except ObservationTransportFailure as error:
                    assert error.status in {401,403,404,409,4401,4403,4404,4409}
                    return 'denied'

            contenders = [asyncio.create_task(compete()) for _ in range(2)]
            ready.set()
            assert sorted(await asyncio.gather(*contenders)) == ['denied','winner']
            assert client.get(key) is None
            await refused_socket(api, original['run_id'], ticket)
            assert runner(service_database, original['runner_id'])['status'] == 'waiting'
            assert original['worker'].poll() is None
            assert len(workers.provider.requests(original['marker'])) == 2


@pytest.mark.asyncio
async def test_actual_sidecar_requires_dedicated_peer_and_original_complete_private_proof(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    client, prefix, environment = browser_redis
    with parked_observation_runtime(workers, actors['a'], service_database, browser_page,
            environment, observation_configuration['worker']) as original:
        with observation_api(workers.processes,
                {**workers.environment, **observation_configuration['gateway']}) as api:
            ticket = issue_ticket(api, actors['a'], original['run_id'])
            _, stored = stored_ticket(client, prefix, ticket)
            endpoint = urlsplit(observation_configuration['worker']['AGENT_RUNNER_BROWSER_OWNER_ENDPOINT'])
            uri = urlunsplit(endpoint._replace(scheme='ws', path=endpoint.path+'/view'))
            # The real existing Runner peer credential has no Browser authority.
            headers = {'X-Browser-Gateway-Service': workers.api.service_id,
                'X-Browser-Gateway-Token': workers.api._service_token,
                'X-Browser-View-Assertion': json.dumps(stored['assertion'])}
            async with aiohttp.ClientSession(trust_env=False) as session:
                accepted = False
                try:
                    connection = await session.ws_connect(uri, headers=headers, timeout=2)
                except aiohttp.WSServerHandshakeError as error:
                    assert error.status in {401,403}
                else:
                    accepted = True
                    await connection.close()
                assert not accepted, 'Existing Runner peer gained dedicated Browser authority'
            # Real Redis negative proof corruption cannot select a legacy Hub.
            for field in ('owner_boot_id','browser_epoch','version'):
                bad = issue_ticket(api, actors['a'], original['run_id'])
                key, data = stored_ticket(client, prefix, bad)
                if field == 'version':
                    data['assertion'].pop(field)
                else:
                    data['assertion'][field] = 'foreign-fixture-proof'
                assert client.set(key, json.dumps(data), ex=60, xx=True)
                await refused_socket(api, original['run_id'], bad)
            # The unmodified dedicated gateway still observes the real owner.
            async with observation_socket(api.url, original['run_id'], ticket) as wire:
                await wire.frame()
            row = runner(service_database, original['runner_id'])
            assert row['status'] == 'waiting' and not row['cancel_requested']
            assert original['worker'].poll() is None
