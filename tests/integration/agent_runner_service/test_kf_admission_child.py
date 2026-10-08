"""Real completed parent/owned parked child, default recovery, then source input.

Only the first delegate's ordinary wait duration is timing DI (5s). Original
child tasks/Engine/checkpoints/controls/source API/model receipts remain real.
"""
from decimal import Decimal
import json
from pathlib import Path
import threading
import uuid

import pytest

from .conftest import wait_for
from .kf_admission_fixtures import text_scope, kf_scope
from .test_kf_admission_authority import rejection
from .kf_admission_service import start_original_worker, verify_original_resources, safe_source_execution_diagnostic
from .provider import Reply, tool_reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, decoded, terminal

pytestmark = pytest.mark.integration


def test_actual_new_source_during_original_child_reply_recovery_reopens_parent_model_without_redelegating_child(text_scope, service_processes, provider_peer, prices):
    t, s = text_scope, text_scope.scope
    subscription = 'native_child_subscription_' + s.marker
    profile = 'customer-followup'
    s.rows("INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status) VALUES(%s,%s,%s,'active','paid')", (subscription, s.tenant_id, profile))
    releases = [threading.Event() for _ in range(3)]
    clarify = tool_reply('clarify', {'question': 'Which fictional customer?', 'missing_info': ['customer']}, call_id='native_original_child_clarify')
    clarify.release = releases[0]
    child_final = Reply(content='Original child completed once after its exact reply', release=releases[2])
    child_marker = provider_peer.register(clarify, child_final)
    parent_final = Reply(content='Already completed original parent output', release=releases[1])
    delegate = tool_reply('delegate_to_subagent', {'subagent_name': profile, 'task_description': child_marker}, call_id='native_original_delegate')
    parent_marker = provider_peer.register(delegate, parent_final)
    new_reply = Reply(content='New source really reached the resumed parent model')
    new_marker = provider_peer.register(new_reply)
    fleet = Workers(service_processes, t.api, provider_peer, prices)
    identifier = None
    try:
        verify_original_resources(fleet, t.api)
        initial_locator = t.text('actual_child_initial_', parent_marker)
        accepted = t.accept(initial_locator)
        identifier = accepted['current_runner_id']
        report = fleet.root / 'original-owned-child-done.json'
        environment = {**fleet.environment, **t.api.environment, 'QWEN_MODEL_CODE': fleet.models[0],
            'RUNNER_TEST_PARKED_TASK_REPORT': str(report)}
        probe = Path(__file__).with_name('kf_admission_delegate_process.py')
        first = service_processes.start([str(probe)], environment=environment, private_working_directory=True)
        fleet.children.append((first, 'fixture-delegate-timeout-owner'))
        assert clarify.arrived.wait(20) and parent_final.arrived.wait(20)
        releases[0].set()
        observed = wait_for(lambda: json.loads(report.read_text()) if report.is_file() else None, timeout=15)
        assert observed['all_owned_tasks_done'] is True
        before = decoded(runner(s.database, identifier)['checkpoint'])['execution']
        original_child = before['children']['native_original_delegate']
        child_id = original_child['execution_id']
        assert child_id in observed['execution_ids']
        assert original_child['checkpoint']['outcome'] == 'waiting'
        releases[1].set()
        fleet.assert_clean_exit(first)
        parked = runner(s.database, identifier)
        root = decoded(parked['checkpoint'])['execution']
        assert parked['status'] == 'waiting' and parked['attempt'] == 1
        assert root['output'] == parent_final.content
        assert root['resources']['root_execution_outcome'] == 'completed'
        assert root['tools']['native_original_delegate']['phase'] == 'completed'
        original_budget = root['max_iterations']
        original_iteration = root['iteration']
        wait = root['children']['native_original_delegate']['checkpoint']['waiting']
        locator = t.text('child_resume_new_source_', new_marker)
        rejection(t.post(locator), 409, 'SOURCE_WAITING_INPUT_NOT_SUPPORTED')
        assert runner(s.database, identifier)['checkpoint'] == parked['checkpoint']
        reply = t.api.call('POST', '/v1/runners/' + identifier + '/controls',
            headers=t.api.headers(scope=s, input_ref=accepted['input_ref']),
            json={'client_request_id': 'native_child_reply_' + uuid.uuid4().hex, 'action': 'reply',
                'target_execution_id': child_id, 'wait_id': wait['wait_id'], 'answer': 'Fictional exact child answer'})
        assert reply.status_code == 202
        control_id = reply.json()['control']['control_id']
        resumed, _ = start_original_worker(fleet, t.api, maximum=1)
        assert child_final.arrived.wait(20)
        restoring = runner(s.database, identifier)
        assert restoring['status'] == 'running' and restoring['attempt'] == 2
        incoming = t.accept(locator)
        assert incoming['accepted_runner_id'] == incoming['current_runner_id'] == identifier
        assert incoming['input_ref'] != accepted['input_ref']
        releases[2].set()
        finished = terminal(s.database, identifier, timeout=30)
        fleet.assert_clean_exit(resumed)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        final = decoded(finished['checkpoint'])['execution']
        assert final['max_iterations'] == original_budget and final['iteration'] == original_iteration + 1
        assert final['output'].startswith(parent_final.content) and final['output'].endswith(new_reply.content)
        child = final['children']['native_original_delegate']
        assert child['execution_id'] == child_id and child['task_record']['task_id'] == original_child['task_record']['task_id']
        assert child['checkpoint']['outcome'] == 'completed' and child['checkpoint']['output'] == child_final.content
        assert len(provider_peer.requests(parent_marker)) == 2 and len(provider_peer.requests(child_marker)) == 2
        assert len(provider_peer.requests(new_marker)) == 1 and not provider_peer.errors
        new_context = provider_peer.requests(new_marker)[0]['messages']
        assert sum(new_marker in str(m.get('content')) for m in new_context if m.get('role') == 'user') == 1
        assert sum(c.get('id') == 'native_original_delegate' for m in new_context for c in m.get('tool_calls', [])) == 1
        assert s.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s', (control_id,)) == [{'status': 'consumed', 'consumed_attempt': 2}]
        assert [f['phase'] for f in t.facts()] == ['applied', 'applied']
        receipts = s.rows('SELECT phase,applied,execution_id FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 5 and all(r['phase'] == 'observed' and r['applied'] for r in receipts)
        assert sum(r['execution_id'] == child_id for r in receipts) == 2
        assert s.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == [{'total_token_count': 90, 'credit_cost': Decimal('0.02')}]
        history = s.rows('SELECT message_id FROM channel_messages WHERE session_id=%s', (s.legacy_sid,))
        ids = [h['message_id'] for h in history]
        assert ids.count(accepted['input_ref'] + ':user') == ids.count(incoming['input_ref'] + ':user') == ids.count(control_id + ':user') == 1
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == [{'owner_runner_id': identifier, 'gate': 'delivery'}]
    except BaseException:
        print(json.dumps(safe_source_execution_diagnostic(s, identifier, service_processes), sort_keys=True))
        raise
    finally:
        for event in releases:
            event.set()
        fleet.close()
        s.rows('DELETE FROM subscriptions WHERE subscription_id=%s', (subscription,))
