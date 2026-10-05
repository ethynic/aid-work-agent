"""Current DB subject/session changes revoke an actual native observer."""
import pytest

from .browser_io import browser_page, browser_redis
from .browser_observation_fixture import parked_observation_runtime, observation_api, observation_configuration
from .browser_observation_io import observation_socket
from .test_browser_observation import issue_ticket
from .test_browser_observation_authority import wait_closed
from .test_worker import workers, api_pair, prices, runner

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['inactive_user','moved_tenant','rebound_session'])
async def test_real_native_observer_checks_current_subject_and_original_owned_session(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration, change):
    _, _, environment = browser_redis
    actor = actors['a']
    with parked_observation_runtime(workers, actor, service_database, browser_page,
            environment, observation_configuration['worker']) as original:
        with observation_api(workers.processes,
                {**workers.environment, **observation_configuration['gateway']}) as api:
            ticket = issue_ticket(api, actor, original['run_id'])
            async with observation_socket(api.url, original['run_id'], ticket) as wire:
                await wire.frame()
                if change == 'inactive_user':
                    service_database.rows("UPDATE users SET status='inactive' WHERE user_id=%s",(actor.user_id,))
                elif change == 'moved_tenant':
                    service_database.rows('UPDATE users SET tenant_id=%s WHERE user_id=%s',
                        (actors['b'].tenant_id,actor.user_id))
                else:
                    service_database.rows('UPDATE chat_sessions SET user_id=%s WHERE session_id=%s',
                        (actors['a_other'].user_id,actor.session_id))
                assert await wait_closed(wire,timeout=5) in {4401,4403,4404,4409}
            expected = {'inactive_user':401,'moved_tenant':403,'rebound_session':404}[change]
            assert api.call('POST',f'/api/browser/runs/{original["run_id"]}/view_ticket',actor=actor).status_code == expected
            assert not runner(service_database, original['runner_id'])['cancel_requested']
            assert len(workers.provider.requests(original['marker'])) == 2
            assert original['worker'].poll() is None
