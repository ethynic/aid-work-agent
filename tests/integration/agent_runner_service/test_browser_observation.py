"""Prepared real cross-process observation acceptance; run after source freeze.

``observation_configuration`` uses the writer's exact worker and gateway
environment contract. This file has not been executed against mutable
production code. Frames/auth/tickets/Hub/sidecar/owner are never fabricated.
"""
import pytest

from .browser_io import browser_page, browser_redis, redis_run_view
from .browser_observation_fixture import parked_observation_runtime, observation_api, observation_configuration
from .browser_observation_io import observation_socket
from .conftest import wait_for
from .test_worker import workers, api_pair, prices, runner
from .test_api import require_status

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def issue_ticket(api, actor, run_id):
    response = api.call('POST', f'/api/browser/runs/{run_id}/view_ticket', actor=actor)
    data = require_status(response, 200)
    ticket = data.get('ticket')
    if not isinstance(ticket, str) or not ticket:
        raise AssertionError('Original Web route did not return an observation ticket')
    return ticket


@pytest.mark.asyncio
async def test_actual_parked_view_crosses_api_sidecar_original_hub_and_detach_keeps_owner(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    client, prefix, redis_environment = browser_redis
    actor = actors['a']
    with parked_observation_runtime(workers, actor, service_database, browser_page,
            redis_environment, observation_configuration['worker']) as original:
        identifier, run_id = original['runner_id'], original['run_id']
        gateway_environment = {**workers.environment, **observation_configuration['gateway']}
        with observation_api(workers.processes, gateway_environment) as api:
            assert api.child.pid != original['worker'].pid
            first = issue_ticket(api, actor, run_id)
            second = issue_ticket(api, actor, run_id)
            async with observation_socket(api.url, run_id, first) as view_a:
                _, jpeg_a = await view_a.frame()
                assert len(jpeg_a) > 100
                async with observation_socket(api.url, run_id, second) as view_b:
                    metadata, jpeg_b = await view_b.frame()
                    assert len(jpeg_b) > 100
                # Closing one real subscriber cannot clear the run's Hub or
                # stop the other subscriber's original capture/runtime lease.
                next_metadata, _ = await view_a.frame(after_seq=metadata['seq'])
                assert next_metadata['seq'] > metadata['seq']
            row = runner(service_database, identifier)
            assert row['status'] == 'waiting' and not row['cancel_requested']
            assert row['result'] is None and row['finished_at'] is None
            assert original['worker'].poll() is None
            owner_key = f'{prefix}:browser_owner:{actor.tenant_id}:{run_id}'
            run_key = f'{prefix}:browser_run:{actor.tenant_id}:{run_id}'
            assert redis_run_view(client, run_key, owner_key) == {
                'run_id': run_id, 'state': 'WAITING_HUMAN', 'lease_live': True}
            before = service_database.rows('SELECT owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                                           (run_id,))[0]['owner_lease_until']
            wait_for(lambda: service_database.rows('SELECT owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                (run_id,))[0]['owner_lease_until'] > before, timeout=15)
            assert len(workers.provider.requests(original['marker'])) == 2
            assert browser_page.path in browser_page.requests and not browser_page.unexpected
            assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                (identifier,)) == [{'owner_runner_id': identifier}]
            assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s',
                                         (actor.session_id,)) == []
            # Real worker shutdown closes its original resource and listener;
            # the parked Runner is still awaiting the later completion bridge.
            workers.processes.stop(original['worker'])
            workers.assert_clean_exit(original['worker'])
            assert service_database.rows('SELECT runtime_state,owner_lease_until FROM bs_browser_runs WHERE run_id=%s',
                (run_id,)) == [{'runtime_state': 'closed', 'owner_lease_until': None}]
            assert runner(service_database, identifier)['status'] == 'waiting'
