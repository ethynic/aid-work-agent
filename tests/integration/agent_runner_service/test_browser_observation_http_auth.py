"""Real HTTP authentication precedes native/legacy/configuration disclosure.

One real parked native Browser is reused. The additional legacy-NULL row is an
explicit isolated legacy catalog fixture, not claimed an actual legacy runtime.
No auth/Manager/owner/API result is mocked.
"""
import uuid

import pytest

from .browser_io import browser_page, browser_redis
from .browser_observation_fixture import parked_observation_runtime, observation_api, observation_configuration
from .test_worker import workers, api_pair, prices, runner

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_actual_http_ticket_auth_precedes_disabled_native_legacy_and_missing_run_state(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    actor = actors['a']
    client, prefix, redis_environment = browser_redis
    with parked_observation_runtime(workers, actor, service_database, browser_page,
            redis_environment, observation_configuration['worker']) as original:
        legacy_id, missing_id = 'fixture-legacy-' + uuid.uuid4().hex, 'fixture-missing-' + uuid.uuid4().hex
        service_database.rows('''INSERT INTO bs_browser_runs(run_id,tenant_id,user_id,session_id,state)
            VALUES (%s,%s,%s,%s,'WAITING_HUMAN')''',
            (legacy_id, actor.tenant_id, actor.user_id, actor.session_id))
        environment = {**workers.environment, **observation_configuration['gateway'],
                       'AGENT_RUNNER_BROWSER_VIEW_ENABLED': 'false'}
        try:
            with observation_api(workers.processes, environment) as api:
                paths = [f'/api/browser/runs/{identifier}/view_ticket'
                         for identifier in (original['run_id'], legacy_id, missing_id)]
                for path in paths:
                    assert api.call('POST', path).status_code == 401
                # An authenticated foreign user is not told the native feature
                # is disabled before its own original-session authorization.
                assert api.call('POST', paths[0], actor=actors['a_other']).status_code in {403,404,409}
                assert api.call('POST', paths[0], actor=actor).status_code == 503
                from src.core.cache_utils import CacheKeys
                if client.get(f'{prefix}:{CacheKeys.TOKEN}:{actor.token}') is None:
                    raise AssertionError('Original opaque bearer cache was not actually warmed')
                service_database.rows('DELETE FROM tokens WHERE token=%s', (actor.token,))
                for path in paths:
                    assert api.call('POST', path, actor=actor).status_code == 401
                assert original['worker'].poll() is None
                assert runner(service_database, original['runner_id'])['status'] == 'waiting'
                assert len(workers.provider.requests(original['marker'])) == 2
        finally:
            service_database.rows('DELETE FROM bs_browser_runs WHERE run_id=%s', (legacy_id,))
