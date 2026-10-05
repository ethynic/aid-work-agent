"""Actual selected tenant authorizes tickets before configuration or Redis NX.

One NULL-tenant administrator owns two real tenant sessions. Only the original
tenant-B session launches a real parked Browser; no owner or auth is replaced.
"""
from dataclasses import replace
import uuid

import pytest

from .browser_io import browser_page, browser_redis
from .browser_observation_fixture import parked_observation_runtime, observation_api, observation_configuration
from .browser_observation_io import observation_socket
from .conftest import wait_for
from .test_browser_observation import issue_ticket
from .test_worker import workers, api_pair, prices, runner

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.asyncio
async def test_selected_tenant_matches_actual_native_owner_before_disabled_or_ticket_write(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    from src.core.cache_utils import CacheKeys

    client, prefix, redis_environment = browser_redis
    admin = actors['global']
    tenant_a, tenant_b = actors['a'].tenant_id, actors['b'].tenant_id
    session_a = str(uuid.uuid4())
    service_database.rows('UPDATE chat_sessions SET tenant_id=%s WHERE session_id=%s AND user_id=%s',
                          (tenant_b, admin.session_id, admin.user_id))
    service_database.rows("INSERT INTO chat_sessions(session_id,user_id,tenant_id,title) VALUES (%s,%s,%s,'Fictional selected tenant boundary')",
                          (session_a, admin.user_id, tenant_a))
    try:
        owned_sessions = service_database.rows('SELECT tenant_id FROM chat_sessions WHERE user_id=%s AND session_id IN (%s,%s)',
                                               (admin.user_id, session_a, admin.session_id))
        assert {item['tenant_id'] for item in owned_sessions} == {tenant_a, tenant_b}
        assert service_database.rows('SELECT tenant_id,role FROM users WHERE user_id=%s',
                                     (admin.user_id,)) == [{'tenant_id': None, 'role': 'platform_admin'}]
        selected_actor = replace(admin, tenant_id=tenant_b)
        submission_headers = {**workers.api.headers(admin), 'X-Tenant-Id': tenant_b}
        with parked_observation_runtime(workers, selected_actor, service_database, browser_page,
                redis_environment, observation_configuration['worker'],
                submission_headers=submission_headers) as original:
            path = f'/api/browser/runs/{original["run_id"]}/view_ticket'
            environment = {**workers.environment, **observation_configuration['gateway']}

            def tickets():
                # Key bytes stay in memory; neither bearer nor private tickets
                # are included in assertion messages or evidence outputs.
                return set(client.scan_iter(match=f'{prefix}:{CacheKeys.BROWSER_VIEW_TICKET}:*'))

            with observation_api(workers.processes, environment) as api:
                no_header = issue_ticket(api, admin, original['run_id'])
                async with observation_socket(api.url, original['run_id'], no_header) as wire:
                    await wire.frame()
                response = api.call('POST', path,
                    headers={**api.headers(admin), 'X-Tenant-Id': tenant_b})
                assert response.status_code == 200
                matched = response.json().get('ticket')
                assert isinstance(matched, str) and bool(matched)
                async with observation_socket(api.url, original['run_id'], matched) as wire:
                    await wire.frame()
                before = tickets()
                wrong = api.call('POST', path,
                    headers={**api.headers(admin), 'X-Tenant-Id': tenant_a})
                assert wrong.status_code == 403
                assert wrong.json()['detail']['error_code'] == 'TENANT_FORBIDDEN'
                assert 'ticket' not in wrong.json()
                assert tickets() == before

            with observation_api(workers.processes,
                    {**environment, 'AGENT_RUNNER_BROWSER_VIEW_ENABLED': 'false'}) as disabled:
                before = tickets()
                wrong = disabled.call('POST', path,
                    headers={**disabled.headers(admin), 'X-Tenant-Id': tenant_a})
                assert wrong.status_code == 403
                assert wrong.json()['detail']['error_code'] == 'TENANT_FORBIDDEN'
                assert 'ticket' not in wrong.json()
                assert tickets() == before
                for headers in (disabled.headers(admin),
                        {**disabled.headers(admin), 'X-Tenant-Id': tenant_b}):
                    response = disabled.call('POST', path, headers=headers)
                    assert response.status_code == 503
                    assert response.json()['detail']['error_code'] == 'BROWSER_VIEW_NOT_AVAILABLE'
                assert tickets() == before

            row = runner(service_database, original['runner_id'])
            assert row['status'] == 'waiting' and not row['cancel_requested']
            assert row['result'] is None and row['finished_at'] is None
            assert original['worker'].poll() is None
            assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                (original['runner_id'],)) == [{'owner_runner_id': original['runner_id']}]
            browser = service_database.rows('SELECT runtime_state,owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                                            (original['run_id'],))[0]
            assert browser['runtime_state'] == 'live'
            assert service_database.rows('SELECT owner_lease_until>clock_timestamp() AS live FROM bs_browser_runs WHERE run_id=%s',
                                        (original['run_id'],)) == [{'live': True}]
            wait_for(lambda: service_database.rows('SELECT owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                (original['run_id'],))[0]['owner_lease_until'] > browser['owner_lease_until'], timeout=15)
            assert len(workers.provider.requests(original['marker'])) == 2
    finally:
        service_database.rows('DELETE FROM chat_sessions WHERE session_id=%s AND user_id=%s',
                              (session_a, admin.user_id))
