"""Native two-sample predicate completion belongs to task permission, not page login."""
import json

import pytest

from .browser_human_auto_page import browser_human_auto_page
from .browser_io import browser_redis
from .browser_observation_fixture import observation_configuration
from .browser_observation_io import observation_socket
from .browser_human_service_fixture import human_resident, take, assert_owned, assert_finished
from .test_browser_observation import issue_ticket
from .test_worker import workers, api_pair, prices
from .conftest import wait_for

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.asyncio
async def test_actual_same_owner_automatic_structural_predicate_two_samples_continue_after_page_token_is_revoked(
        workers, actors, service_database, browser_human_auto_page, browser_redis, observation_configuration):
    actor = actors['a']
    _, _, environment = browser_redis
    with human_resident(workers, actor, service_database, browser_human_auto_page, environment,
            observation_configuration, probe='tests.integration.agent_runner_service.browser_human_auto_probe') as value:
        wait_id, api, run_id = value['wait']['assistance_id'], value['api'], value['browser']['run_id']
        token = service_database.rows('SELECT id,expires_at FROM tokens WHERE token=%s', (actor.token,))[0]
        _, prefix, _ = browser_redis
        cached = json.loads(browser_redis[0].get(f'{prefix}:browser_assistance:{actor.tenant_id}:{wait_id}'))
        assert cached['completion_mode'] == 'auto_or_confirm'
        assert cached['predicates'] == [{'type': 'challenge_iframe_absent', 'value': 'absent'}]
        async with observation_socket(api.url, run_id, issue_ticket(api, actor, run_id)) as view:
            await view.frame()
            take(value)
            # Until the real DOM's marker is removed, original automatic
            # sampling cannot manufacture completion from an LLM done reply.
            report = value['report'].with_suffix('.auto.json')
            wait_for(lambda: report.exists() and json.loads(report.read_text()).get('sample_success', 0) >= 1, timeout=5)
            assert_owned(workers, service_database, value)
            assert service_database.rows('SELECT completion_ref FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                (wait_id,)) == [{'completion_ref': None}]
            tenant = service_database.rows('SELECT status FROM tenants WHERE tenant_id=%s', (actor.tenant_id,))[0]
            try:
                service_database.rows("UPDATE tenants SET status='suspended' WHERE tenant_id=%s", (actor.tenant_id,))
                wait_for(lambda: json.loads(report.read_text()).get('guard_denied', 0) >= 1, timeout=5)
                denied = json.loads(report.read_text())
                wait_for(lambda: json.loads(report.read_text()).get('guard_denied', 0) >= denied['guard_denied'] + 2, timeout=5)
                still_denied = json.loads(report.read_text())
                assert still_denied['sample_calls'] == denied['sample_calls'] == still_denied['denied_sample_calls']
                assert service_database.rows('SELECT completion_ref FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                    (wait_id,)) == [{'completion_ref': None}]
                assert service_database.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s', (value['identifier'],)) == []
                assert still_denied['fields_calls'] == denied['fields_calls'] == still_denied['denied_fields_calls']
                assert_owned(workers, service_database, value)
            finally:
                service_database.rows('UPDATE tenants SET status=%s WHERE tenant_id=%s', (tenant['status'], actor.tenant_id))
            await view.connection.send(json.dumps({'type': 'pointer', 'action': 'click', 'x': 440, 'y': 120}))
            wait_for(lambda: browser_human_auto_page.observed_events() == [{'kind': 'ready', 'value': True}], timeout=5)
            try:
                service_database.rows("UPDATE tokens SET expires_at=TIMESTAMP '2000-01-01' WHERE id=%s", (token['id'],))
                assert api.call('POST', f'/api/browser/runs/{run_id}/view_ticket', actor=actor).status_code == 401
                assert_finished(workers, service_database, value, browser_human_auto_page)
                observed = json.loads(report.read_text())
                assert observed['sample_success'] >= 3 and observed['guard_calls'] >= 3 and observed['same_page_ops']
                fact = service_database.rows('SELECT completion_fact FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                    (wait_id,))[0]['completion_fact']
                assert fact['completed_by_human'] is False and fact['continuation']['phase'] == 'completed'
                calls = json.loads(value['report'].read_text())
                assert calls['resume_calls'] == 1 and calls['confirmation_added'] == 0 and calls['resume_success']
            finally:
                service_database.rows('UPDATE tokens SET expires_at=%s WHERE id=%s', (token['expires_at'], token['id']))
