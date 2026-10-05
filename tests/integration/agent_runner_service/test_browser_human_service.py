"""Prepared real Web actions; execute only in a captured stable source window."""
from decimal import Decimal
import json
import uuid

import pytest

from .browser_human_page import browser_human_page
from .browser_io import browser_redis, owned_descendants, process_is_live
from .browser_diagnostics import browser_diagnostics, _codes
from .browser_observation_fixture import observation_configuration, observation_api
from .browser_observation_io import observation_socket
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_api import require_status
from .test_browser_observation import issue_ticket
from .test_browser_owner_boundaries import delete_browser_facts
from .test_worker import workers, api_pair, prices, accept, runner, decoded

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


async def input_denial(view):
    """Read an actual rejection, draining original metadata/JPEG pairs."""
    while True:
        raw = await view.receive(timeout=5)
        assert isinstance(raw, str), 'Input response had an unpaired frame'
        value = json.loads(raw)
        if value.get('type') == 'heartbeat':
            continue
        if value.get('type') == 'frame':
            jpeg = await view.receive(timeout=5)
            assert isinstance(jpeg, bytes) and jpeg.startswith(b'\xff\xd8')
            continue
        assert value.get('type') == 'input_rejected'
        return value.get('error_code')


@pytest.mark.asyncio
async def test_actual_web_take_pointer_keyboard_manual_complete_continues_original_call(
        workers, actors, service_database, browser_human_page, browser_redis,
        observation_configuration):
    actor = actors['a']
    _, _, environment = browser_redis
    workers.environment.update(environment)
    workers.environment.update(observation_configuration['worker'])
    initial = tool_reply('browser_automation', {}, call_id='human-service-original-browser-call')
    marker = workers.provider.register(initial,
        Reply(content=json.dumps({'action': 'ask_user', 'reason': 'Fictional manual confirmation'})),
        Reply(content=json.dumps({'action': 'done', 'reason': 'Original fictional page finished'})),
        Reply(content='Original human Browser completed'))
    initial.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' inspect original fictional page', 'url': browser_human_page.url, 'headless': True})
    accepted = accept(workers.api, actor, marker)
    identifier = accepted['runner_id']
    worker_id = 'human-service-' + uuid.uuid4().hex
    report = workers.processes.root / ('human-service-report-' + uuid.uuid4().hex + '.json')
    process = None
    descendants = set()
    try:
        process = workers.processes.start(['-m', 'tests.integration.agent_runner_service.browser_human_service_probe',
            str(report), '--worker-id', worker_id, '--max-tasks', '2'],
            environment=workers.environment, private_working_directory=True)
        workers.children.append((process, worker_id))
        wait_for(lambda: runner(service_database, identifier)['status'] == 'waiting', timeout=40)
        before = runner(service_database, identifier)
        assert before['attempt'] == 1 and before['worker_id'] is None and before['lease_until'] is None
        native = service_database.rows('SELECT * FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
        assert len(native) == 1 and native[0]['runtime_state'] == 'live'
        original = native[0]
        waits = service_database.rows('SELECT assistance_id,runner_wait_id,agent_execution_id,tool_call_id,state '
            'FROM bs_browser_assistance_requests WHERE runner_id=%s', (identifier,))
        assert len(waits) == 1 and waits[0]['state'] == 'pending'
        wait = waits[0]
        assert wait['agent_execution_id'] == identifier
        assert wait['tool_call_id'] == 'human-service-original-browser-call'
        descendants = owned_descendants(process.pid)
        with observation_api(workers.processes, {**workers.environment, **observation_configuration['gateway']}) as api:
            assert api.child.pid != process.pid
            ticket = issue_ticket(api, actor, original['run_id'])
            async with observation_socket(api.url, original['run_id'], ticket) as view:
                _, jpeg = await view.frame()
                assert len(jpeg) > 100
                # This same already-connected view must become controlling only
                # after the real HTTP take. The rejected input must not burn seq.
                await view.connection.send(json.dumps({'type': 'keyboard', 'key': 'G'}))
                assert await input_denial(view) == 'HUMAN_CONTROL_REQUIRED'
                assert browser_human_page.observed_events() == []
                taken = require_status(api.call('POST', f"/api/browser/runs/{original['run_id']}/take_control",
                    actor=actor, params={'assistance_id': wait['assistance_id']}), 200)
                assert taken == {'success': True, 'state': 'controlling'}
                assert service_database.rows('SELECT state FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                    (wait['assistance_id'],)) == [{'state': 'controlling'}]
                await view.connection.send(json.dumps({'type': 'pointer', 'action': 'click', 'x': 140, 'y': 120}))
                await view.connection.send(json.dumps({'type': 'keyboard', 'key': 'F'}))
                wait_for(lambda: browser_human_page.observed_events() == [{'kind': 'input', 'value': 'F'}], timeout=10)
                await view.connection.send(json.dumps({'type': 'pointer', 'action': 'click', 'x': 440, 'y': 120}))
                wait_for(lambda: browser_human_page.observed_events() == [
                    {'kind': 'input', 'value': 'F'}, {'kind': 'ready', 'value': True}], timeout=10)
                complete = require_status(api.call('POST',
                    f"/api/browser/runs/{original['run_id']}/assistance/{wait['assistance_id']}/complete", actor=actor), 200)
                assert complete['success'] and complete['state'] == 'resume_queued'
                assert isinstance(complete['continuation_id'], str) and complete['continuation_id']
        wait_for(lambda: runner(service_database, identifier)['status'] in {'completed', 'failed', 'cancelled'}, timeout=50)
        workers.assert_clean_exit(process)
        observations = json.loads(report.read_text())
        assert observations['input_calls'] == 4 and observations['input_accepted'] == 3
        assert observations['input_denied'] == 1 and observations['denied_without_fields'] == [True]
        assert observations['input_fields'] == 3 and observations['sample_fields'] >= 1
        assert observations['complete_calls'] == observations['resume_calls'] == 1
        assert observations['same_orchestrator'] and observations['same_page_ops'] and observations['same_raw_executor']
        assert observations['confirmation_added'] == 1 and observations['resume_success']
        assert observations['resume_seq_after'] > observations['resume_seq_before']
        row = runner(service_database, identifier)
        assert row['status'] == 'completed' and row['attempt'] == 2
        assert decoded(row['result'])['output'] == 'Original human Browser completed'
        tool = decoded(row['checkpoint'])['execution']['tools']['human-service-original-browser-call']
        assert tool['result_recorded'] and tool['result']['success']
        after = service_database.rows('SELECT run_id,owner_boot_id,browser_epoch,runtime_state,owner_lease_until,closed_at '
            'FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
        assert len(after) == 1 and after[0]['run_id'] == original['run_id']
        assert after[0]['owner_boot_id'] == original['owner_boot_id'] and after[0]['browser_epoch'] == original['browser_epoch']
        assert after[0]['runtime_state'] == 'closed' and after[0]['owner_lease_until'] is None and after[0]['closed_at'] is not None
        assert service_database.rows('SELECT action,status,consumed_attempt FROM agent_runner_controls WHERE runner_id=%s',
            (identifier,)) == [{'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 2}]
        assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == []
        assert browser_human_page.requests.count(browser_human_page.path) == 1 and not browser_human_page.unexpected
        assert not any(process_is_live(pid) for pid in descendants)
        assert len(workers.provider.requests(marker)) == 4 and not workers.provider.errors
        receipts = service_database.rows('SELECT phase,applied,usage FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 4 and all(item['phase'] == 'observed' and item['applied'] for item in receipts)
        assert sum(decoded(item['usage'])['prompt_tokens'] + decoded(item['usage'])['completion_tokens'] for item in receipts) == 72
        assert service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
            (actor.session_id,)) == [{'total_token_count': 72, 'credit_cost': Decimal('.01')}]
    except Exception:
        diagnosis = browser_diagnostics(workers, service_database, identifier, marker, browser_human_page, process)
        diagnosis['controls'] = [{'action': item['action'], 'status': item['status'],
            'consumed_attempt': item['consumed_attempt'], 'codes': _codes(item['error_code'])}
            for item in service_database.rows('SELECT action,status,consumed_attempt,error_code FROM agent_runner_controls WHERE runner_id=%s', (identifier,))]
        if report.exists():
            diagnosis['original_method_observations'] = json.loads(report.read_text())
        diagnosis['fictional_page_input_count'] = len(browser_human_page.observed_events())
        print('SAFE_BROWSER_HUMAN_DIAG=' + json.dumps(diagnosis))
        raise
    finally:
        if process is not None:
            workers.processes.stop(process)
        delete_browser_facts(service_database, identifier)
