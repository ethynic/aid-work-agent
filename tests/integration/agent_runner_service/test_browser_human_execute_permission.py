"""Real current task execution policy differs from an owned read/cancel right."""
from decimal import Decimal
import json

import pytest

from .browser_human_page import browser_human_page
from .browser_io import browser_redis, process_is_live
from .browser_observation_fixture import observation_configuration
from .browser_observation_io import observation_socket
from .browser_human_service_fixture import human_resident, take, assert_owned
from .test_browser_human_service import input_denial
from .test_browser_observation import issue_ticket
from .test_api import require_status
from .test_worker import workers, api_pair, prices, runner, decoded
from .conftest import wait_for

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.asyncio
async def test_actual_owned_observation_survives_execute_withdrawal_but_new_input_and_complete_do_not_write_and_read_cancel_closes(
        workers, actors, service_database, browser_human_page, browser_redis, observation_configuration):
    actor = actors['a']
    _, _, environment = browser_redis
    with human_resident(workers, actor, service_database, browser_human_page,
            environment, observation_configuration,
            probe='tests.integration.agent_runner_service.browser_human_cancel_probe') as value:
        api, run_id, wait_id = value['api'], value['browser']['run_id'], value['wait']['assistance_id']
        tenant = service_database.rows('SELECT status,credit_balance FROM tenants WHERE tenant_id=%s',
            (actor.tenant_id,))[0]
        ticket = issue_ticket(api, actor, run_id)
        async with observation_socket(api.url, run_id, ticket) as view:
            metadata, jpeg = await view.frame()
            assert len(jpeg) > 100
            assert take(value) == {'success': True, 'state': 'controlling'}
            try:
                # Existing RunnerAuthorizer._tenant executes this actual PG
                # policy. The read scope still owns the same session and user.
                service_database.rows("UPDATE tenants SET status='suspended',credit_balance=0 WHERE tenant_id=%s",
                    (actor.tenant_id,))
                assert isinstance(issue_ticket(api, actor, run_id), str)
                require_status(workers.api.call('GET', '/v1/runners/' + value['identifier'], actor=actor), 200)
                denied = require_status(api.call('POST', f'/api/browser/runs/{run_id}/take_control',
                    actor=actor, params={'assistance_id': wait_id}), 403)
                assert denied['detail']['error_code'] == 'TENANT_UNAVAILABLE'
                denied = require_status(api.call('POST', f'/api/browser/runs/{run_id}/assistance/{wait_id}/complete',
                    actor=actor), 403)
                assert denied['detail']['error_code'] == 'TENANT_UNAVAILABLE'
                await view.connection.send(json.dumps({'type': 'keyboard', 'key': 'G'}))
                assert await input_denial(view) == 'TENANT_UNAVAILABLE'
                metadata, jpeg = await view.frame(after_seq=metadata['seq'])
                assert len(jpeg) > 100  # real read/WS permission remains valid
                assert_owned(workers, service_database, value)
                assert browser_human_page.observed_events() == []
                observations = json.loads(value['report'].read_text())
                assert observations['input_denied'] == 1 and observations['denied_without_fields'] == [True]
                assert observations['input_fields'] == observations['sample_fields'] == 0
                assert observations['complete_calls'] == observations['resume_calls'] == 0
                assert service_database.rows('SELECT completion_ref FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                    (wait_id,)) == [{'completion_ref': None}]
                # An owner may stop the task with its read authority, even with
                # execution withdrawn and balance zero; this is not a new IO.
                assert require_status(api.call('POST', f'/api/browser/runs/{run_id}/cancel', actor=actor,
                    params={'assistance_id': wait_id}), 200) == {'success': True}
                try:
                    wait_for(lambda: runner(service_database, value['identifier'])['status'] in {'completed', 'failed', 'cancelled'},
                        timeout=30)
                except Exception:
                    report = value['report'].with_suffix('.cancel.json')
                    print('SAFE_ORIGINAL_CANCEL_PHASES=' + (report.read_text() if report.exists() else '{"missing":true}'))
                    raise
                workers.assert_clean_exit(value['process'])
                row = runner(service_database, value['identifier'])
                assert row['status'] == 'cancelled' and decoded(row['result'])['status'] == 'cancelled'
                assert service_database.rows('SELECT runtime_state,owner_lease_until,closed_at IS NOT NULL AS closed FROM bs_browser_runs WHERE run_id=%s',
                    (run_id,)) == [{'runtime_state': 'closed', 'owner_lease_until': None, 'closed': True}]
                assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s',
                    (value['identifier'],)) == []
                assert not any(process_is_live(pid) for pid in value['descendants'])
                assert len(workers.provider.requests(value['marker'])) == 2 and not workers.provider.errors
                assert service_database.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s',
                    (value['identifier'],)) == []
                receipts = service_database.rows('SELECT phase,applied,usage FROM agent_runner_usage_receipts WHERE runner_id=%s',
                    (value['identifier'],))
                assert len(receipts) == 2 and all(r['phase'] == 'observed' and r['applied'] for r in receipts)
                assert sum(decoded(r['usage'])['prompt_tokens'] + decoded(r['usage'])['completion_tokens'] for r in receipts) == 36
                assert service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
                    (actor.session_id,)) == [{'total_token_count': 36, 'credit_cost': Decimal('.01')}]
            finally:
                service_database.rows('UPDATE tenants SET status=%s,credit_balance=%s WHERE tenant_id=%s',
                    (tenant['status'], tenant['credit_balance'], actor.tenant_id))
        assert browser_human_page.requests.count(browser_human_page.path) == 1 and not browser_human_page.unexpected
