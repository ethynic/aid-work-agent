"""Actual network handshake/redirect bounds and revocation during blocked send.

Explicit external transport timing/response DI only; original claimed Runner,
Runtime, Browser, auth, gateway, sidecar, receipts and frame Hub remain real.
"""
import asyncio
import time
from urllib.parse import urlsplit, urlunsplit, quote

import aiohttp
import pytest

from .browser_io import browser_page, browser_redis
from .browser_observation_fixture import observation_api, observation_configuration
from .browser_observation_transport_fixture import fault_runtime
from .browser_observation_io import observation_socket
from .test_browser_observation import issue_ticket
from .test_browser_observation_authority import wait_closed
from .conftest import wait_for
from .test_worker import workers, api_pair, prices, runner

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['slow_handshake','redirect'])
async def test_real_gateway_rejects_stalled_handshake_or_redirect_without_following_it(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration, fault):
    _, _, environment = browser_redis
    with fault_runtime(workers, actors['a'], service_database, browser_page, environment,
                       observation_configuration, fault) as original:
        with observation_api(workers.processes,
                {**workers.environment, **observation_configuration['gateway']}) as api:
            ticket = issue_ticket(api, actors['a'], original['run_id'])
            parts = urlsplit(api.url)
            uri = urlunsplit(parts._replace(scheme='ws',
                path='/api/browser/runs/'+quote(original['run_id'],safe='')+'/view_ws',
                query='ticket='+quote(ticket,safe='')))
            before = time.monotonic()
            async with aiohttp.ClientSession(trust_env=False) as session:
                accepted = False
                try:
                    connection = await asyncio.wait_for(session.ws_connect(uri),timeout=9)
                except aiohttp.WSServerHandshakeError as error:
                    assert error.status in {401,403,404,409}
                else:
                    accepted = True
                    await connection.close()
                assert not accepted, 'External handshake/redirect fault was admitted'
            elapsed = time.monotonic()-before
            assert elapsed < 8
            if fault == 'slow_handshake':
                assert elapsed >= 4, 'Held TCP handshake did not reach the original 5s bound'
            assert original['report'].is_file(), 'Actual original endpoint was not reached'
            assert '/forbidden-observation-redirect' not in browser_page.requests
            assert not browser_page.unexpected
            row = runner(service_database, original['runner_id'])
            assert row['status'] == 'waiting' and not row['cancel_requested']
            assert original['child'].poll() is None
            assert len(workers.provider.requests(original['marker'])) == 2
            assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                (original['runner_id'],)) == [{'owner_runner_id':original['runner_id']}]
            assert service_database.rows('SELECT runtime_state,owner_lease_until > clock_timestamp() AS lease_live FROM bs_browser_runs WHERE run_id=%s',
                (original['run_id'],)) == [{'runtime_state':'live','lease_live':True}]


@pytest.mark.asyncio
async def test_real_observer_reauthorization_closes_during_blocked_original_jpeg_send(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    _, _, environment = browser_redis
    with fault_runtime(workers, actors['a'], service_database, browser_page, environment,
                       observation_configuration, 'slow_send') as original:
        with observation_api(workers.processes,
                {**workers.environment, **observation_configuration['gateway']}) as api:
            ticket = issue_ticket(api, actors['a'], original['run_id'])
            async with observation_socket(api.url, original['run_id'], ticket) as wire:
                await asyncio.to_thread(wait_for, original['report'].is_file, timeout=4)
                # Original metadata/real captured JPEG send is held, not invented.
                # Timer authorization must not await its send_lock or new frames.
                service_database.rows('DELETE FROM tokens WHERE token=%s', (actors['a'].token,))
                assert await wait_closed(wire, timeout=5) in {4401,4403,4404,4409}
            row = runner(service_database, original['runner_id'])
            assert row['status'] == 'waiting' and not row['cancel_requested']
            assert original['child'].poll() is None
            assert service_database.rows('SELECT runtime_state FROM bs_browser_runs WHERE run_id=%s',
                (original['run_id'],)) == [{'runtime_state':'live'}]
            assert len(workers.provider.requests(original['marker'])) == 2
