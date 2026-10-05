"""Prepared internal owner completion; not public human-control acceptance.

The Worker remains real and resident. Its test-only same-process trigger calls
the trusted owner; the original HumanControl sampler and Browser orchestrator
must perform the real work. Await stable production handoff before running.
"""
from decimal import Decimal
import json
import uuid

import pytest

from .browser_io import browser_page, browser_redis, owned_descendants, process_is_live
from .browser_diagnostics import browser_diagnostics, _codes
from .browser_observation_fixture import observation_configuration
from .conftest import wait_for
from .provider import Reply, tool_reply
from .test_browser_owner_boundaries import delete_browser_facts
from .test_worker import workers, api_pair, prices, accept, runner, decoded

pytestmark = [pytest.mark.integration, pytest.mark.real_browser]


def test_actual_resident_original_browser_completion_rebinds_new_attempt_same_page_to_terminal(
        workers, actors, service_database, browser_page, browser_redis,
        observation_configuration):
    actor = actors['a']
    _, _, redis_environment = browser_redis
    workers.environment.update(redis_environment)
    workers.environment.update(observation_configuration['worker'])
    initial = tool_reply('browser_automation', {}, call_id='continuation-original-browser-call')
    marker = workers.provider.register(initial,
        Reply(content=json.dumps({'action': 'ask_user', 'reason': 'Fictional confirmation required'})),
        Reply(content=json.dumps({'action': 'done', 'reason': 'Fictional original page completed'})),
        Reply(content='Original Browser continuation completed'))
    initial.tool_calls[0]['function']['arguments'] = json.dumps(
        {'task': marker + ' inspect fictional original page', 'url': browser_page.url, 'headless': True})
    accepted = accept(workers.api, actor, marker)
    identifier = accepted['runner_id']
    command = workers.processes.root / ('continuation-command-' + uuid.uuid4().hex + '.json')
    report = workers.processes.root / ('continuation-report-' + uuid.uuid4().hex + '.json')
    process = None
    descendants = set()
    try:
        worker_id = 'continuation-worker-' + uuid.uuid4().hex
        process = workers.processes.start(['-m', 'tests.integration.agent_runner_service.browser_continuation_probe',
            str(command), str(report), '--worker-id', worker_id, '--max-tasks', '2'],
            environment=workers.environment, private_working_directory=True)
        workers.children.append((process, worker_id))
        wait_for(lambda: runner(service_database, identifier)['status'] == 'waiting', timeout=40)
        before = runner(service_database, identifier)
        original = service_database.rows('SELECT * FROM bs_browser_runs WHERE runner_id=%s', (identifier,))
        assert len(original) == 1 and original[0]['runtime_state'] == 'live'
        browser = original[0]
        waits = service_database.rows('SELECT assistance_id,runner_wait_id,agent_execution_id,tool_call_id FROM bs_browser_assistance_requests WHERE runner_id=%s',
                                      (identifier,))
        assert len(waits) == 1 and waits[0]['agent_execution_id'] == identifier
        assert waits[0]['tool_call_id'] == 'continuation-original-browser-call'
        assert before['attempt'] == 1 and before['worker_id'] is None
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
            (identifier,)) == [{'owner_runner_id': identifier}]
        assert len(workers.provider.requests(marker)) == 2
        descendants = owned_descendants(process.pid)
        command.write_text(json.dumps({'runner_id': identifier, 'run_id': browser['run_id'],
                                       'assistance_id': waits[0]['assistance_id']}))
        wait_for(lambda: runner(service_database, identifier)['status'] in {'completed', 'failed', 'cancelled'}, timeout=50)
        workers.assert_clean_exit(process)
        observations = json.loads(report.read_text())
        assert observations['completion_calls'] == observations['resume_calls'] == 1
        assert observations['completion_returned'] and observations['completion_ref_present']
        assert observations['completion_missing_count'] == 0
        assert observations['sample_seq_after'] > observations['sample_seq_before']
        assert observations['same_original_orchestrator']
        assert observations['same_original_page_ops'] and observations['same_original_executor']
        assert observations['resume_seq_after'] > observations['resume_seq_before']
        assert observations['confirmation_count_after'] == observations['confirmation_count_before'] + 1
        assert observations['resume_result_success']
        row = runner(service_database, identifier)
        assert row['status'] == 'completed' and row['attempt'] == 2
        assert decoded(row['result'])['output'] == 'Original Browser continuation completed'
        fact = decoded(row['checkpoint'])['execution']['tools']['continuation-original-browser-call']
        assert fact['result_recorded'] and fact['result']['success']
        after = service_database.rows('SELECT run_id,owner_boot_id,browser_epoch,runtime_state,owner_lease_until,closed_at FROM bs_browser_runs WHERE runner_id=%s',
                                     (identifier,))
        assert len(after) == 1 and after[0]['run_id'] == browser['run_id']
        assert after[0]['owner_boot_id'] == browser['owner_boot_id'] and after[0]['browser_epoch'] == browser['browser_epoch']
        assert after[0]['runtime_state'] == 'closed' and after[0]['owner_lease_until'] is None and after[0]['closed_at'] is not None
        controls = service_database.rows("SELECT action,status,consumed_attempt FROM agent_runner_controls WHERE runner_id=%s", (identifier,))
        assert controls == [{'action': 'browser_complete', 'status': 'consumed', 'consumed_attempt': 2}]
        assert browser_page.requests.count(browser_page.path) == 1 and not browser_page.unexpected
        assert not any(process_is_live(pid) for pid in descendants)
        assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == []
        assert len(workers.provider.requests(marker)) == 4 and not workers.provider.errors
        receipts = service_database.rows('SELECT phase,applied,usage FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 4 and all(item['phase'] == 'observed' and item['applied'] for item in receipts)
        assert sum(decoded(item['usage'])['prompt_tokens'] + decoded(item['usage'])['completion_tokens'] for item in receipts) == 72
        assert service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s',
            (actor.session_id,)) == [{'total_token_count': 72, 'credit_cost': Decimal('0.01')}]
    except Exception:
        # Preserve allowlisted actual facts before fixture cleanup. No page
        # text, model body, tool arguments, URLs or private binding is printed.
        diagnosis = browser_diagnostics(workers, service_database, identifier, marker, browser_page, process)
        diagnosis['controls'] = [{'action': item['action'], 'status': item['status'],
            'consumed_attempt': item['consumed_attempt'], 'codes': _codes(item['error_code'])}
            for item in service_database.rows('SELECT action,status,consumed_attempt,error_code FROM agent_runner_controls WHERE runner_id=%s',
                                               (identifier,))]
        diagnosis['completion_facts'] = [{'state': item['state'],
            'reference_present': bool(item['completion_ref']),
            'continuation_phase': ((decoded(item['completion_fact']) or {}).get('continuation') or {}).get('phase')}
            for item in service_database.rows('SELECT state,completion_ref,completion_fact FROM bs_browser_assistance_requests WHERE runner_id=%s',
                                               (identifier,))]
        if report.exists():
            diagnosis['original_method_observations'] = json.loads(report.read_text())
        print('SAFE_BROWSER_CONTINUATION_DIAG=' + json.dumps(diagnosis))
        raise
    finally:
        if process is not None:
            workers.processes.stop(process)
        delete_browser_facts(service_database, identifier)
