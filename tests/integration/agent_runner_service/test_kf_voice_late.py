"""Prepared real late Voice/ChildRecovery and declared one-round budget DI.

First delegate ordinary wait is the accepted 5s timing harness. No completed
parent/child checkpoint, recovery result, permission or source receipt is made
up. Budget uses the existing original profile get_max_iterations=1 parameter.
"""
from decimal import Decimal
import json
from pathlib import Path
import threading
import uuid

import pytest

from .conftest import wait_for
from .kf_ingress_fixtures import kf_scope
from .kf_voice_fixtures import voice_scope, voice_price
from .kf_voice_peer import AsrReply
from .kf_admission_service import verify_original_resources
from .provider import Reply, tool_reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, terminal, decoded
from .test_kf_voice_results import start_voice_worker, post_count, safe_voice_error

pytestmark = pytest.mark.integration


def test_actual_last_model_budget_defers_only_unstarted_voice_and_original_delivery_claim_blocks_preparation(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    v, s = voice_scope, voice_scope.scope
    release = threading.Event()
    first_reply = Reply(content='Actual final allowed one-round text result', release=release)
    marker = provider_peer.register(first_reply)
    initial = v.text.accept(v.text.text('voice_budget_initial_', marker))
    identifier = initial['current_runner_id']
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    try:
        verify_original_resources(fleet, v.api)
        child = start_voice_worker(fleet, v, one_round=True)
        assert first_reply.arrived.wait(15)
        prior = runner(s.database, identifier)
        state = decoded(prior['checkpoint'])['execution']
        assert state['max_iterations'] == state['iteration'] == 1
        locator, _ = v.receive_voice()
        voice = v.text.accept(locator)
        assert voice['accepted_runner_id'] == voice['current_runner_id'] == identifier
        release.set()
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(child)
        assert finished['status'] == 'completed' and finished['result']['error_code'] is None
        assert finished['result']['output'] == first_reply.content
        actual = decoded(finished['checkpoint'])['execution']
        assert actual['outcome'] == 'completed' and actual['iteration'] == actual['max_iterations'] == 1
        facts = v.text.facts()
        assert len(facts) == 2 and facts[0]['phase'] == 'applied'
        pending = facts[1]
        assert pending['input_ref'] == voice['input_ref'] and pending['phase'] == 'deferred'
        new_id = pending['current_runner_id']
        assert new_id != identifier and pending['accepted_runner_id'] == identifier
        assert pending['deferred_to_runner'] == new_id
        queued = runner(s.database, new_id)
        assert queued['status'] == 'queued' and queued['attempt'] == 0
        assert decoded(queued['checkpoint'])['source_initial_ref'] == voice['input_ref']
        assert all((m.get('metadata') or {}).get('input_ref') != voice['input_ref']
            for m in actual['messages'] + actual['followup_messages'])
        assert s.rows('SELECT 1 FROM wecom_kf_input_preparations WHERE input_ref=%s', (voice['input_ref'],)) == []
        assert v.peer.calls == [] and post_count(v) == 0
        claim = [{'owner_runner_id': identifier, 'gate': 'delivery'}]
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == claim
        contender = start_voice_worker(fleet, v, once=True)
        fleet.assert_clean_exit(contender)
        assert runner(s.database, new_id)['attempt'] == 0
        assert s.rows('SELECT 1 FROM wecom_kf_input_preparations WHERE input_ref=%s', (voice['input_ref'],)) == []
        assert v.peer.calls == [] and len(provider_peer.requests(marker)) == 1
        assert s.rows('SELECT user_message,asr_calls,total_token_count,credit_cost FROM chat_records WHERE record_id=%s', (finished['record_id'],)) == [
            {'user_message': marker, 'asr_calls': 0, 'total_token_count': 18, 'credit_cost': Decimal('0.01')}]
        repeat = v.text.accept(locator)
        assert not repeat['created'] and repeat['input_ref'] == voice['input_ref']
        assert repeat['accepted_runner_id'] == identifier and repeat['current_runner_id'] == new_id
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'budget_unstarted_voice', error)
        raise
    finally:
        release.set()
        fleet.close()


def test_actual_completed_parent_original_child_reply_recovery_prepares_late_voice_then_one_parent_model_without_redelegate(
        voice_scope, service_processes, provider_peer, prices, voice_price):
    v, s = voice_scope, voice_scope.scope
    profile = 'customer-followup'
    subscription = 'voice_actual_child_subscription_' + s.marker
    s.rows("INSERT INTO subscriptions(subscription_id,tenant_id,subagent_type,status,payment_status) VALUES(%s,%s,%s,'active','paid')",
        (subscription, s.tenant_id, profile))
    gates = [threading.Event() for _ in range(3)]
    clarify = tool_reply('clarify', {'question': 'Which fictional customer?', 'missing_info': ['customer']}, call_id='voice_original_child_clarify')
    clarify.release = gates[0]
    child_final = Reply(content='Original voice child completed once', release=gates[2])
    child_marker = provider_peer.register(clarify, child_final)
    parent_final = Reply(content='Original parent completed before child recovery', release=gates[1])
    delegate = tool_reply('delegate_to_subagent', {'subagent_name': profile, 'task_description': child_marker}, call_id='voice_original_delegate')
    parent_marker = provider_peer.register(delegate, parent_final)
    new_reply = Reply(content='Actual late Voice reached original parent model')
    voice_marker = provider_peer.register(new_reply)
    transcript = voice_marker + ' actual late voice recognition after child reply'
    v.peer.replies.append(AsrReply(payload={'status': 20000000, 'result': transcript}))
    initial = v.text.accept(v.text.text('voice_child_initial_', parent_marker))
    identifier = initial['current_runner_id']
    fleet = Workers(service_processes, v.api, provider_peer, prices)
    try:
        verify_original_resources(fleet, v.api)
        report = fleet.root / 'actual-voice-original-child.json'
        environment = {**fleet.environment, **v.api.environment, 'QWEN_MODEL_CODE': fleet.models[0],
            'RUNNER_TEST_PARKED_TASK_REPORT': str(report)}
        first = service_processes.start([str(Path(__file__).with_name('kf_admission_delegate_process.py'))],
            environment=environment, private_working_directory=True)
        fleet.children.append((first, 'fixture-delegate-timeout-owner'))
        assert clarify.arrived.wait(20) and parent_final.arrived.wait(20)
        gates[0].set()
        observed = wait_for(lambda: json.loads(report.read_text()) if report.is_file() else None, timeout=15)
        assert observed['all_owned_tasks_done'] is True
        prior = decoded(runner(s.database, identifier)['checkpoint'])['execution']
        original_child = prior['children']['voice_original_delegate']
        child_id = original_child['execution_id']
        assert child_id in observed['execution_ids'] and original_child['checkpoint']['outcome'] == 'waiting'
        gates[1].set()
        fleet.assert_clean_exit(first)
        parked = runner(s.database, identifier)
        root = decoded(parked['checkpoint'])['execution']
        assert parked['status'] == 'waiting' and parked['attempt'] == 1
        assert root['resources']['root_execution_outcome'] == 'completed'
        wait = root['children']['voice_original_delegate']['checkpoint']['waiting']
        response = v.api.call('POST', '/v1/runners/' + identifier + '/controls',
            headers=v.api.headers(scope=s, input_ref=initial['input_ref']),
            json={'client_request_id': 'voice_child_reply_' + uuid.uuid4().hex, 'action': 'reply',
                'target_execution_id': child_id, 'wait_id': wait['wait_id'], 'answer': 'Fictional exact child answer'})
        assert response.status_code == 202
        control_id = response.json()['control']['control_id']
        second = start_voice_worker(fleet, v)
        assert child_final.arrived.wait(20)
        restoring = runner(s.database, identifier)
        assert restoring['status'] == 'running' and restoring['attempt'] == 2
        locator, _ = v.receive_voice()
        voice = v.text.accept(locator)
        assert voice['accepted_runner_id'] == voice['current_runner_id'] == identifier
        assert post_count(v) == 0
        gates[2].set()
        finished = terminal(s.database, identifier, timeout=35)
        fleet.assert_clean_exit(second)
        assert finished['status'] == 'completed' and finished['settlement_status'] == 'settled'
        final = decoded(finished['checkpoint'])['execution']
        assert final['max_iterations'] == root['max_iterations'] and final['iteration'] == root['iteration'] + 1
        assert final['output'].startswith(parent_final.content) and final['output'].endswith(new_reply.content)
        actual_child = final['children']['voice_original_delegate']
        assert actual_child['execution_id'] == child_id
        assert actual_child['checkpoint']['outcome'] == 'completed' and actual_child['checkpoint']['output'] == child_final.content
        assert len(provider_peer.requests(parent_marker)) == len(provider_peer.requests(child_marker)) == 2
        assert len(provider_peer.requests(voice_marker)) == post_count(v) == 1
        context = provider_peer.requests(voice_marker)[0]['messages']
        assert sum(str(m.get('content')) == transcript for m in context if m.get('role') == 'user') == 1
        assert sum(call.get('id') == 'voice_original_delegate' for m in context for call in m.get('tool_calls', [])) == 1
        prep = s.rows('SELECT phase,transcript,fee_owner_runner_id,authorized_attempt FROM wecom_kf_input_preparations WHERE input_ref=%s', (voice['input_ref'],))
        assert prep == [{'phase': 'known', 'transcript': transcript, 'fee_owner_runner_id': identifier, 'authorized_attempt': 2}]
        receipts = s.rows('SELECT owner,phase,applied,execution_id,authorized_attempt FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert len(receipts) == 6 and all(r['phase'] == 'observed' and r['applied'] for r in receipts)
        assert sum(r['execution_id'] == child_id for r in receipts) == 2
        assert next(r for r in receipts if r['owner'] == 'asr')['authorized_attempt'] == 2
        assert s.rows('SELECT total_token_count,asr_calls,credit_cost FROM chat_records WHERE record_id=%s', (finished['record_id'],)) == [
            {'total_token_count': 90, 'asr_calls': 1, 'credit_cost': Decimal('0.12')}]
        assert s.rows('SELECT content FROM channel_messages WHERE message_id=%s', (voice['input_ref'] + ':user',)) == [{'content': '[ASR识别结果] ' + transcript}]
        assert [fact['phase'] for fact in v.text.facts()] == ['applied', 'applied']
        assert s.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s', (control_id,)) == [{'status': 'consumed', 'consumed_attempt': 2}]
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == [{'owner_runner_id': identifier, 'gate': 'delivery'}]
        assert not v.peer.errors and not provider_peer.errors
    except BaseException as error:
        safe_voice_error(v, identifier, service_processes, 'actual_child_late_voice', error)
        raise
    finally:
        for event in gates:
            event.set()
        fleet.close()
        s.rows('DELETE FROM subscriptions WHERE subscription_id=%s', (subscription,))
