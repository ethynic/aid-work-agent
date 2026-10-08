"""Fresh public action authorization and a consumed original completion read."""
import json
import threading

import pytest

from .browser_human_page import browser_human_page
from .browser_io import browser_redis
from .browser_observation_fixture import observation_configuration
from .browser_human_service_fixture import human_resident, take, complete, assert_owned, assert_finished
from .test_browser_observation import issue_ticket
from .provider import Reply
from .test_worker import workers, api_pair, prices, runner
from .conftest import wait_for

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_actual_fresh_human_role_selected_tenant_token_and_old_complete_fact_do_not_dispatch_new_io(
        workers, actors, service_database, browser_human_page, browser_redis, observation_configuration):
    actor = actors['a']
    _, _, environment = browser_redis
    gate = threading.Event()
    decision = Reply(content=json.dumps({'action': 'done', 'reason': 'Original page completed'}), release=gate)
    with human_resident(workers, actor, service_database, browser_human_page,
            environment, observation_configuration, decision=decision) as value:
        api, run_id, wait_id = value['api'], value['browser']['run_id'], value['wait']['assistance_id']
        path = f'/api/browser/runs/{run_id}/take_control'
        query = {'assistance_id': wait_id}
        # Warm existing user/cache paths, then use real current DB changes.
        issue_ticket(api, actor, run_id)
        assert api.call('POST', path, actor=actors['b'], params=query).status_code == 409
        assert api.call('POST', path, actor=actor, params={'assistance_id': 'fictional-other-wait'}).status_code == 403
        token = service_database.rows('SELECT id,expires_at FROM tokens WHERE token=%s', (actor.token,))[0]
        try:
            service_database.rows("UPDATE tokens SET expires_at=TIMESTAMP '2000-01-01' WHERE id=%s", (token['id'],))
            assert api.call('POST', path, actor=actor, params=query).status_code == 401
        finally:
            service_database.rows('UPDATE tokens SET expires_at=%s WHERE id=%s', (token['expires_at'], token['id']))
        try:
            # A real NULL-tenant administrator still owns this genuine A
            # session; an explicitly selected B scope grants no A action.
            service_database.rows("UPDATE users SET role='platform_admin',tenant_id=NULL WHERE user_id=%s", (actor.user_id,))
            assert api.call('POST', path, actor=actor, params=query,
                headers={**api.headers(actor), 'X-Tenant-Id': actors['b'].tenant_id}).status_code == 403
            assert take(value) == {'success': True, 'state': 'controlling'}
            service_database.rows("UPDATE users SET role='user' WHERE user_id=%s", (actor.user_id,))
            # Existing HTTP tenant middleware rejects this NULL user before
            # native execution; do not pretend it is a different owner error.
            assert api.call('POST', path, actor=actor, params=query).status_code == 400
        finally:
            service_database.rows("UPDATE users SET role='user',tenant_id=%s WHERE user_id=%s", (actor.tenant_id, actor.user_id))
        assert_owned(workers, service_database, value)
        assert browser_human_page.observed_events() == []
        assert service_database.rows('SELECT completion_ref FROM bs_browser_assistance_requests WHERE assistance_id=%s',
            (wait_id,)) == [{'completion_ref': None}]
        try:
            first = complete(value)
            assert decision.arrived.wait(timeout=15)
            assert runner(service_database, value['identifier'])['attempt'] == 2
            wait_for(lambda: json.loads(value['report'].read_text()).get('resume_calls') == 1, timeout=5)
            before = json.loads(value['report'].read_text())
            second = complete(value)
            assert second == first
            after = json.loads(value['report'].read_text())
            assert after['sample_fields'] == before['sample_fields'] >= 1
            assert after['complete_calls'] == before['complete_calls'] == 1
            assert after['resume_calls'] == before['resume_calls'] == 1
            assert service_database.rows('SELECT action,status,consumed_attempt FROM agent_runner_controls WHERE runner_id=%s',
                (value['identifier'],)) == [{'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 2}]
            assert len(workers.provider.requests(value['marker'])) == 3 and not workers.provider.errors
        finally:
            gate.set()
        assert_finished(workers, service_database, value, browser_human_page)
        final = json.loads(value['report'].read_text())
        assert final['resume_calls'] == 1 and final['confirmation_added'] == 1 and final['resume_success']
