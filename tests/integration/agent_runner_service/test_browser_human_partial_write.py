"""Possibly-written human IPC is unknown: no zero-IO proof or released claim."""
import json

import pytest

from .browser_human_page import browser_human_page
from .browser_io import browser_redis, process_is_live
from .browser_observation_fixture import observation_configuration
from .browser_observation_io import observation_socket
from .browser_human_service_fixture import human_resident, take
from .test_browser_observation import issue_ticket
from .provider import Reply
from .test_api import require_status
from .test_worker import workers, api_pair, prices, runner, decoded, accept
from .conftest import wait_for

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


@pytest.mark.asyncio
async def test_actual_partial_ipc_write_unknown_cancel_preserves_original_claim_and_blocks_next_session_job(
        workers, actors, service_database, browser_human_page, browser_redis, observation_configuration):
    actor = actors['a']
    _, _, environment = browser_redis
    with human_resident(workers, actor, service_database, browser_human_page, environment,
            observation_configuration, probe='tests.integration.agent_runner_service.browser_human_shutdown_probe') as value:
        api, run_id = value['api'], value['browser']['run_id']
        original_balance = service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s',
            (actor.tenant_id,))
        async with observation_socket(api.url, run_id, issue_ticket(api, actor, run_id)) as view:
            await view.frame()
            take(value)
            await view.connection.send(json.dumps({'type': 'keyboard', 'key': 'P'}))
            partial_report = value['report'].with_suffix('.partial.json')
            wait_for(partial_report.exists, timeout=5)
            assert json.loads(partial_report.read_text()) == {
                'actual_written_bytes': 2, 'write_started': True, 'frame_written': False}
            wait_for(lambda: value['report'].exists() and json.loads(value['report'].read_text()).get('input_fields') == 1,
                timeout=10)
        observed = json.loads(value['report'].read_text())
        assert observed['input_calls'] == 1 and observed['input_fields'] == 1
        assert observed['input_accepted'] == observed['input_denied'] == 0
        assert observed['sample_fields'] == observed['resume_calls'] == 0
        assert service_database.rows('SELECT completion_ref FROM bs_browser_assistance_requests WHERE runner_id=%s',
            (value['identifier'],)) == [{'completion_ref': None}]
        assert require_status(api.call('POST', f'/api/browser/runs/{run_id}/cancel', actor=actor,
            params={'assistance_id': value['wait']['assistance_id']}), 200) == {'success': True}
        wait_for(lambda: decoded(runner(service_database, value['identifier'])['checkpoint']).get('cancel_completion_blocked') is not None,
            timeout=20)
        # Original fail-closed shutdown reports its unconfirmed native Close.
        # This error must not skip the sidecar and original pool cleanup.
        assert value['process'].wait(timeout=25) == 1
        shutdown = json.loads(value['report'].with_suffix('.shutdown.json').read_text())
        assert shutdown == {'factory_exception': 'BrowserOwnerFailure',
            'factory_code': 'BROWSER_CLOSE_VERIFICATION_REQUIRED',
            'sidecar_close_returned': True, 'sidecar_task_done': True, 'sidecar_socket_closed': True,
            'logs_pool_close_returned': True, 'postgres_pool_close_returned': True}
        assert not any(process_is_live(pid) for pid in value['descendants'])
        row = runner(service_database, value['identifier'])
        assert row['status'] == 'waiting' and row['cancel_requested'] and row['result'] is None
        assert row['worker_id'] is None and row['lease_until'] is None
        cp = decoded(row['checkpoint'])
        assert cp['cancel_completion_blocked']['error_code'] == 'LOCAL_CANCEL_EFFECT_VERIFICATION_REQUIRED'
        assert not cp['cancel_completion_blocked']['ready']
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
            (value['identifier'],)) == [{'owner_runner_id': value['identifier']}]
        assert service_database.rows('SELECT runtime_state,closed_at FROM bs_browser_runs WHERE run_id=%s',
            (run_id,)) == [{'runtime_state': 'live', 'closed_at': None}]
        assert len(workers.provider.requests(value['marker'])) == 2
        assert service_database.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s', (value['identifier'],)) == []
        assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (actor.session_id,)) == []
        marker = workers.provider.register(Reply(content='Fictional blocked follower must not execute'))
        next_id = accept(workers.api, actor, marker)['runner_id']
        child, _ = workers.start(once=True)
        workers.assert_clean_exit(child)
        assert runner(service_database, next_id)['status'] == 'queued'
        assert workers.provider.requests(marker) == []
        assert service_database.rows('SELECT COUNT(*) AS n FROM agent_runner_usage_receipts WHERE runner_id=%s',
            (value['identifier'],)) == [{'n': 2}]
        receipts = service_database.rows('SELECT phase,usage FROM agent_runner_usage_receipts WHERE runner_id=%s',
            (value['identifier'],))
        assert all(item['phase'] == 'observed' for item in receipts)
        assert sum(decoded(item['usage'])['prompt_tokens'] + decoded(item['usage'])['completion_tokens']
            for item in receipts) == 36
        assert service_database.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (actor.tenant_id,)) == original_balance
        assert not browser_human_page.unexpected
