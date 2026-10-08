"""Prepared real cross-process token and native/legacy boundary scenarios.

Only isolated fixture SQL changes the principal or original proof. Browser
runtime, cached bearer verification, ticket storage, gateway, sidecar and Hub
stay real. These scenarios await the final production freeze before execution.
"""
import asyncio
from dataclasses import replace
import json

import pytest

from .browser_io import browser_page, browser_redis
from .browser_observation_fixture import parked_observation_runtime, observation_api, observation_configuration
from .browser_observation_io import observation_socket, ObservationTransportFailure
from .test_browser_observation import issue_ticket
from .test_worker import workers, api_pair, prices, runner

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


async def refused_socket(api, run_id, ticket):
    try:
        async with observation_socket(api.url, run_id, ticket) as wire:
            await wire.receive(timeout=5)
    except ObservationTransportFailure as error:
        assert error.status in {401, 403, 404, 409, 4401, 4403, 4404, 4409}
        return
    raise AssertionError('Unauthorized original Browser observation was accepted')


async def wait_closed(wire, timeout=5):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        assert remaining > 0, 'Fresh authorization did not close the observer before deadline'
        try:
            await wire.receive(timeout=remaining)
        except ObservationTransportFailure as error:
            assert error.kind == 'ConnectionClosed'
            return error.status


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['revoke', 'rebind', 'expire'])
async def test_real_single_use_ticket_rechecks_original_token_id_despite_warmed_cache(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration, change):
    actor = actors['a']
    client, prefix, redis_environment = browser_redis
    with parked_observation_runtime(workers, actor, service_database, browser_page,
            redis_environment, observation_configuration['worker']) as original:
        with observation_api(workers.processes,
                {**workers.environment, **observation_configuration['gateway']}) as api:
            ticket = issue_ticket(api, actor, original['run_id'])
            from src.core.cache_utils import CacheKeys
            cached = client.get(f'{prefix}:{CacheKeys.TOKEN}:{actor.token}')
            if cached is None:
                raise AssertionError('Original opaque bearer cache was not actually warmed')
            assert json.loads(cached)['user_id'] == actor.user_id
            token_row = service_database.rows('SELECT id FROM tokens WHERE token=%s', (actor.token,))[0]
            private = client.get(f'{prefix}:{CacheKeys.BROWSER_VIEW_TICKET}:{ticket.split(".", 1)[0]}')
            if private is None:
                raise AssertionError('Native view ticket was not stored in the real Redis helper')
            assert json.loads(private)['assertion']['token_row_id'] == token_row['id']
            if change == 'revoke':
                service_database.rows('DELETE FROM tokens WHERE id=%s', (token_row['id'],))
            elif change == 'rebind':
                service_database.rows('UPDATE tokens SET user_id=%s WHERE id=%s',
                                      (actors['a_other'].user_id, token_row['id']))
            else:
                service_database.rows("UPDATE tokens SET expires_at=clock_timestamp()-interval '1 minute' WHERE id=%s",
                                      (token_row['id'],))
            # A new native ticket must not use an old cached token->user result;
            # the already issued single-use ticket must fail by the same DB ID.
            assert api.call('POST', f'/api/browser/runs/{original["run_id"]}/view_ticket', actor=actor).status_code == 401
            await refused_socket(api, original['run_id'], ticket)
            row = runner(service_database, original['runner_id'])
            assert row['status'] == 'waiting' and not row['cancel_requested']
            assert original['worker'].poll() is None
            assert len(workers.provider.requests(original['marker'])) == 2


@pytest.mark.asyncio
async def test_null_tenant_platform_admin_owns_real_tenant_session_then_role_change_revokes_view(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    _, _, redis_environment = browser_redis
    original_admin = actors['global']
    tenant = actors['a'].tenant_id
    # The user retains its actual NULL tenant and platform_admin role; only its
    # own real session is scoped to the actual tenant selected at Runner accept.
    service_database.rows('UPDATE chat_sessions SET tenant_id=%s WHERE session_id=%s AND user_id=%s',
                          (tenant, original_admin.session_id, original_admin.user_id))
    actor = replace(original_admin, tenant_id=tenant)
    headers = {**workers.api.headers(original_admin), 'X-Tenant-Id': tenant}
    with parked_observation_runtime(workers, actor, service_database, browser_page,
            redis_environment, observation_configuration['worker'], submission_headers=headers) as original:
        with observation_api(workers.processes,
                {**workers.environment, **observation_configuration['gateway']}) as api:
            ticket = issue_ticket(api, original_admin, original['run_id'])
            async with observation_socket(api.url, original['run_id'], ticket) as wire:
                await wire.frame()
                for foreign in (actors['a'], actors['a_other'], actors['b']):
                    assert api.call('POST', f'/api/browser/runs/{original["run_id"]}/view_ticket', actor=foreign).status_code in {403,404,409}
                service_database.rows("UPDATE users SET role='user' WHERE user_id=%s", (original_admin.user_id,))
                assert await wait_closed(wire) in {4401,4403,4404,4409}
            assert api.call('POST', f'/api/browser/runs/{original["run_id"]}/view_ticket', actor=original_admin).status_code == 400
            assert runner(service_database, original['runner_id'])['status'] == 'waiting'
            assert original['worker'].poll() is None


@pytest.mark.asyncio
async def test_actual_native_pg_disappears_but_original_redis_hub_does_not_allow_legacy_observation(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    client, prefix, redis_environment = browser_redis
    actor = actors['a']
    with parked_observation_runtime(workers, actor, service_database, browser_page,
            redis_environment, observation_configuration['worker']) as original:
        with observation_api(workers.processes,
                {**workers.environment, **observation_configuration['gateway']}) as api:
            ticket = issue_ticket(api, actor, original['run_id'])
            async with observation_socket(api.url, original['run_id'], ticket) as wire:
                await wire.frame()
            old_ticket = issue_ticket(api, actor, original['run_id'])
            # Real missing authoritative row, not a mocked is_native boolean.
            service_database.rows('DELETE FROM bs_browser_runs WHERE run_id=%s', (original['run_id'],))
            assert client.get(f'{prefix}:browser_run:{actor.tenant_id}:{original["run_id"]}') is not None
            assert api.call('POST', f'/api/browser/runs/{original["run_id"]}/view_ticket', actor=actor).status_code in {403,404,409}
            await refused_socket(api, original['run_id'], old_ticket)
            assert not runner(service_database, original['runner_id'])['cancel_requested']
