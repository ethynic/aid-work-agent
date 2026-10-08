"""Actual original model/CAS/cutoff risks; declared budget/CP timing DI below."""
from decimal import Decimal
import json
from pathlib import Path
import threading
import uuid

import pytest

from .kf_admission_fixtures import text_scope, kf_scope
from .kf_admission_service import start_original_worker, verify_original_resources, safe_source_execution_diagnostic
from .conftest import wait_for
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, terminal, decoded

pytestmark = pytest.mark.integration


def marked_users(checkpoint):
    return [m for m in decoded(checkpoint)['execution']['messages']
        if m.get('role') == 'user' and (m.get('metadata') or {}).get('input_ref')]


def test_actual_followup_entered_failed_model_then_explicit_checkpoint_interruption_empty_resume_does_not_replay(text_scope, service_processes, provider_peer, prices):
    """Real model failure; original CP/interrupt ports are explicit timing DI.

The default restoring Runtime/ChildRecovery must preserve that saved failure,
not turn an already-used source continuation into permission for a new model.
"""
    t, s = text_scope, text_scope.scope
    release = threading.Event()
    first_reply = Reply(content='Original successful first response', release=release)
    first_marker = provider_peer.register(first_reply)
    failed_reply = Reply(status=400)
    later_marker = provider_peer.register(failed_reply)
    fleet = Workers(service_processes, t.api, provider_peer, prices)
    identifier = None
    try:
        verify_original_resources(fleet, t.api)
        initial = t.accept(t.text('failure_initial_', first_marker))
        identifier = initial['current_runner_id']
        report = fleet.root / 'actual-failed-interrupted.json'
        probe = Path(__file__).with_name('kf_admission_failed_process.py')
        environment = {**fleet.environment, **t.api.environment,
            'QWEN_MODEL_CODE': fleet.models[0], 'KF_ADMISSION_FAILED_REPORT': str(report)}
        first = service_processes.start([str(probe), 'worker', '--worker-id',
            'fixture-failed-before-stage', '--max-tasks', '1'], environment=environment,
            private_working_directory=True)
        fleet.children.append((first, 'fixture-failed-before-stage'))
        assert first_reply.arrived.wait(20)
        later = t.accept(t.text('failure_followup_', later_marker))
        assert later['current_runner_id'] == identifier
        release.set()
        observed = wait_for(lambda: json.loads(report.read_text()) if report.is_file() else None, timeout=20)
        assert observed == {'outcome': 'failed', 'last_model_phase': 'failed', 'status': 'interrupted', 'attempt': 1}
        fleet.assert_clean_exit(first)
        interrupted = runner(s.database, identifier)
        saved = decoded(interrupted['checkpoint'])['execution']
        assert saved['outcome'] == 'failed' and saved['error_code'] == 'EXECUTION_FAILED'
        assert saved['model_calls'][-1]['phase'] == 'failed'
        assert saved['followup_messages'] == []
        assert saved['resources'].get('root_continuation_requested') is not True
        assert 'source_continuation_intent' not in saved['resources']
        assert [m['metadata']['input_ref'] for m in marked_users(interrupted['checkpoint'])] == [initial['input_ref'], later['input_ref']]
        assert [f['phase'] for f in t.facts()] == ['appended', 'appended']
        assert len(provider_peer.requests(first_marker)) == len(provider_peer.requests(later_marker)) == 1
        prior_receipts = s.rows('SELECT receipt_id,phase FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY receipt_id', (identifier,))
        assert len(prior_receipts) == 2
        assert sorted(r['phase'] for r in prior_receipts) == ['observed', 'unknown']
        assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
        resumed_control = t.api.call('POST', '/v1/runners/' + identifier + '/controls',
            headers=t.api.headers(scope=s, input_ref=initial['input_ref']),
            json={'action': 'resume', 'client_request_id': 'empty_failed_resume_' + uuid.uuid4().hex})
        assert resumed_control.status_code == 202
        control_id = resumed_control.json()['control']['control_id']
        resumed, _ = start_original_worker(fleet, t.api, maximum=1)
        finished = terminal(s.database, identifier, timeout=25)
        fleet.assert_clean_exit(resumed)
        final = decoded(finished['checkpoint'])['execution']
        assert finished['status'] == 'failed' and finished['attempt'] == 2
        assert finished['settlement_status'] == 'pending'  # Failed external response has unknown usage, not zero fabricated cost.
        assert final['outcome'] == 'failed' and final['error_code'] == saved['error_code']
        assert final['iteration'] == saved['iteration'] and final['max_iterations'] == saved['max_iterations']
        assert final['model_calls'] == saved['model_calls']
        assert final['messages'] == saved['messages']
        assert len(provider_peer.requests(first_marker)) == len(provider_peer.requests(later_marker)) == 1
        assert not provider_peer.errors
        assert s.rows('SELECT receipt_id,phase FROM agent_runner_usage_receipts WHERE runner_id=%s ORDER BY receipt_id', (identifier,)) == prior_receipts
        assert s.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s', (control_id,)) == [{'status': 'consumed', 'consumed_attempt': 2}]
        assert [f['phase'] for f in t.facts()] == ['applied', 'applied']
        assert s.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == [{'total_token_count': 18, 'credit_cost': Decimal('0.01')}]
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == [{'owner_runner_id': identifier, 'gate': 'delivery'}]
    except BaseException:
        print(json.dumps(safe_source_execution_diagnostic(s, identifier, service_processes), sort_keys=True))
        raise
    finally:
        release.set()
        fleet.close()


def test_actual_ordered_inputs_append_at_original_model_boundary_and_continue_cancel_stops_further_dispatch(text_scope, service_processes, provider_peer, prices):
    t, s = text_scope, text_scope.scope
    releases = [threading.Event(), threading.Event()]
    first_reply = Reply(content='First response before followup', release=releases[0])
    final_reply = Reply(content='Known response concurrent with actual cancel', release=releases[1])
    markers = [provider_peer.register(first_reply), provider_peer.register(Reply(content='Must not separately dispatch middle input')),
        provider_peer.register(final_reply)]
    locators = [t.text('ordered_' + str(i) + '_', marker) for i, marker in enumerate(markers)]
    first = t.accept(locators[0])
    identifier = first['current_runner_id']
    fleet = Workers(service_processes, t.api, provider_peer, prices)
    try:
        verify_original_resources(fleet, t.api)
        child, _ = start_original_worker(fleet, t.api, maximum=1)
        assert first_reply.arrived.wait(15)
        accepted = [first, t.accept(locators[1]), t.accept(locators[2])]
        assert all(a['current_runner_id'] == identifier for a in accepted)
        assert len({a['input_ref'] for a in accepted}) == 3
        before = runner(s.database, identifier)
        assert [m['metadata']['input_ref'] for m in marked_users(before['checkpoint'])] == [first['input_ref']]
        releases[0].set()
        assert final_reply.arrived.wait(15)
        boundary = runner(s.database, identifier)
        assert boundary['attempt'] == 1 and boundary['record_id'] == before['record_id']
        assert [m['metadata']['input_ref'] for m in marked_users(boundary['checkpoint'])] == [a['input_ref'] for a in accepted]
        assert [f['phase'] for f in t.facts()] == ['appended'] * 3
        messages = provider_peer.requests(markers[2])[0]['messages']
        contents = [str(m.get('content')) for m in messages if m.get('role') == 'user']
        assert all(sum(marker in value for value in contents) == 1 for marker in markers)
        assert not provider_peer.requests(markers[1])
        cancelled = t.api.call('POST', '/v1/runners/' + identifier + '/cancel',
            headers=t.api.headers(scope=s, input_ref=first['input_ref']))
        assert cancelled.status_code == 200 and runner(s.database, identifier)['cancel_requested']
        releases[1].set()
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(child)
        assert finished['status'] == 'cancelled' and finished['settlement_status'] == 'settled'
        assert [f['phase'] for f in t.facts()] == ['applied'] * 3
        assert len(provider_peer.requests(markers[0])) == len(provider_peer.requests(markers[2])) == 1
        assert not provider_peer.requests(markers[1]) and not provider_peer.errors
        receipts = s.rows('SELECT phase,applied,record_id FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 2 and all(r['phase'] == 'observed' and r['applied'] and r['record_id'] == finished['record_id'] for r in receipts)
        records = s.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s', (s.legacy_sid,))
        assert records == [{'total_token_count': 36, 'credit_cost': Decimal('0.01')}]
        refs = s.rows("SELECT message_id FROM channel_messages WHERE session_id=%s AND role='user' AND metadata->>'input_ref' IS NOT NULL ORDER BY created_at", (s.legacy_sid,))
        assert [r['message_id'] for r in refs] == [a['input_ref'] + ':user' for a in accepted]
    finally:
        for release in releases:
            release.set()
        fleet.close()


def test_actual_one_round_profile_budget_defers_only_unappended_inputs_and_original_delivery_claim_blocks_new_runner(text_scope, service_processes, provider_peer, prices):
    t, s = text_scope, text_scope.scope
    release = threading.Event()
    reply = Reply(content='Actual last allowed original model response', release=release)
    first_marker = provider_peer.register(reply)
    next_marker = provider_peer.register(Reply(content='Deferred input must not dispatch behind delivery'))
    locators = [t.text('budget_first_', first_marker), t.text('budget_next_', next_marker)]
    first = t.accept(locators[0])
    identifier = first['current_runner_id']
    fleet = Workers(service_processes, t.api, provider_peer, prices)
    try:
        verify_original_resources(fleet, t.api)
        child, _ = start_original_worker(fleet, t.api, maximum=1, one_round_profile=True)
        assert reply.arrived.wait(15)
        running = runner(s.database, identifier)
        state = decoded(running['checkpoint'])['execution']
        assert state['max_iterations'] == 1 and state['iteration'] == 1
        second = t.accept(locators[1])
        assert second['accepted_runner_id'] == second['current_runner_id'] == identifier
        assert [m['metadata']['input_ref'] for m in marked_users(running['checkpoint'])] == [first['input_ref']]
        release.set()
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(child)
        # The original Engine exhausts its one-round budget while the next
        # source input is queued. Worker exposes ITERATION_LIMIT as failed.
        assert finished['status'] == 'failed'
        assert finished['result']['error_code'] == 'ITERATION_LIMIT'
        assert finished['result']['output'] == reply.content
        facts = t.facts()
        assert facts[0]['phase'] == 'applied' and facts[0]['current_runner_id'] == identifier
        pending = facts[1]
        assert pending['phase'] == 'deferred' and pending['accepted_runner_id'] == identifier
        next_id = pending['current_runner_id']
        assert next_id != identifier and pending['deferred_to_runner'] == next_id
        old_state = decoded(finished['checkpoint'])['execution']
        assert old_state['outcome'] == 'iteration_limit'
        assert old_state['iteration'] == old_state['max_iterations'] == 1
        assert [m['metadata']['input_ref'] for m in marked_users(finished['checkpoint'])] == [first['input_ref']]
        assert all((m.get('metadata') or {}).get('input_ref') != second['input_ref'] for m in old_state['followup_messages'])
        assert second['input_ref'] not in old_state['resources'].get('continuation_inputs', {})
        queued = runner(s.database, next_id)
        assert queued['status'] == 'queued' and queued['attempt'] == 0
        assert decoded(queued['checkpoint'])['source_initial_ref'] == second['input_ref']
        assert decoded(queued['input'])['text'] == next_marker
        claim = s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,))
        assert claim == [{'owner_runner_id': identifier, 'gate': 'delivery'}]
        contender, _ = start_original_worker(fleet, t.api, once=True)
        fleet.assert_clean_exit(contender)
        assert runner(s.database, next_id)['attempt'] == 0 and not provider_peer.requests(next_marker)
        repeat = t.accept(locators[1])
        assert not repeat['created'] and repeat['accepted_runner_id'] == identifier and repeat['current_runner_id'] == next_id
        assert repeat['input_ref'] == second['input_ref']
        assert len(provider_peer.requests(first_marker)) == 1 and not provider_peer.errors
        receipts = s.rows('SELECT phase,applied,record_id FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert receipts == [{'phase': 'observed', 'applied': True, 'record_id': finished['record_id']}]
        assert s.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == [{'total_token_count': 18, 'credit_cost': Decimal('0.01')}]
        assert s.rows("SELECT message_id FROM channel_messages WHERE session_id=%s AND role='user' AND metadata->>'input_ref' IS NOT NULL", (s.legacy_sid,)) == [{'message_id': first['input_ref'] + ':user'}]
    finally:
        release.set()
        fleet.close()
