"""Native resume SDK/fresh identity/idempotency/accepted gate-off drain.

Original socket API, receipt/current SQL authority and default CLI are actual.
Only external provider/current-service-state HTTP responses are fictional.
"""
from decimal import Decimal
import json
import uuid

import pytest

from .conftest import wait_for
from .kf_admission_fixtures import text_scope, kf_scope
from .kf_admission_peer import StateReply
from .kf_admission_service import KfSourceApi, start_original_worker, verify_original_resources, safe_source_execution_diagnostic
from .provider import Reply
from .test_kf_admission_authority import rejection
from .test_usage_storage import prices
from .test_worker import Workers, runner, terminal, decoded

pytestmark = pytest.mark.integration


def test_actual_native_resume_requires_current_sdk_state_but_duplicate_and_gate_off_keep_original_receipt(text_scope, service_processes, provider_peer, prices):
    t, s = text_scope, text_scope.scope
    marker = provider_peer.register(Reply(content='Original native receipt resumed exactly once'))
    accepted = t.accept(t.text('native_controls_', marker))
    identifier, ref = accepted['current_runner_id'], accepted['input_ref']
    path = '/v1/runners/' + identifier + '/controls'
    headers = t.api.headers(scope=s, input_ref=ref)
    original_credit = s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))[0]['credit_balance']
    fleet = Workers(service_processes, t.api, provider_peer, prices)
    off = None
    try:
        verify_original_resources(fleet, t.api)
        paused = t.api.call('POST', path, headers=headers,
            json={'action': 'pause', 'client_request_id': uuid.uuid4().hex})
        assert paused.status_code == 202
        pause_id = paused.json()['control']['control_id']
        first, _ = start_original_worker(fleet, t.api, maximum=1)
        fleet.assert_clean_exit(first)
        parked = wait_for(lambda: (r if (r := runner(s.database, identifier))['status'] == 'paused' else None), timeout=10)
        assert parked['attempt'] == 1 and decoded(parked['checkpoint'])['unstarted'] is True
        assert parked['worker_id'] is None and parked['lease_until'] is None
        assert provider_peer.requests(marker) == []
        assert s.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == []
        assert t.api.call('GET', '/v1/runners/' + identifier, headers=headers).status_code == 200
        # This is an external SDK reply only, never a fake application authority.
        t.platform.states[len(t.platform.calls)] = StateReply({'errcode': 0, 'service_state': 3})
        unavailable = t.api.call('POST', path, headers=headers,
            json={'action': 'resume', 'client_request_id': uuid.uuid4().hex})
        rejection(unavailable, 409, 'SOURCE_STATE_UNAVAILABLE')
        assert runner(s.database, identifier) == parked
        assert s.rows('SELECT control_id FROM agent_runner_controls WHERE runner_id=%s', (identifier,)) == [{'control_id': pause_id}]
        # Actual new API configuration disables new native admission. Its same
        # exact peer must still process the already accepted original receipt.
        off = KfSourceApi(service_processes, t.platform, provider_environment=provider_peer.environment, native_enabled=False)
        off_headers = off.headers(scope=s, input_ref=ref)
        t.platform.states[len(t.platform.calls)] = StateReply()
        s.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s', (s.tenant_id,))
        denied = off.call('POST', path, headers=off_headers,
            json={'action': 'resume', 'client_request_id': uuid.uuid4().hex})
        rejection(denied, 402, 'CREDIT_BLOCKED')
        assert runner(s.database, identifier) == parked
        s.rows('UPDATE tenants SET credit_balance=%s WHERE tenant_id=%s', (original_credit, s.tenant_id))
        request = {'action': 'resume', 'client_request_id': uuid.uuid4().hex}
        resumed = off.call('POST', path, headers=off_headers, json=request)
        assert resumed.status_code == 202 and resumed.json()['created'] is True
        control_id = resumed.json()['control']['control_id']
        before_duplicate = runner(s.database, identifier)
        count = len(t.platform.calls)
        t.platform.states[count] = StateReply({'errcode': 0, 'service_state': 3})
        s.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s', (s.tenant_id,))
        duplicate = off.call('POST', path, headers=off_headers, json=request)
        assert duplicate.status_code == 202 and duplicate.json()['created'] is False
        assert duplicate.json()['control']['control_id'] == control_id
        assert len(t.platform.calls) == count  # An existing fact needs read authority, not new SDK dispatch.
        assert runner(s.database, identifier) == before_duplicate
        foreign = off.call('POST', path,
            headers={**off_headers, 'X-AgentRunner-Channel-User': 'not_the_original_current_actor'}, json=request)
        rejection(foreign, 403, 'SOURCE_SERVICE_FORBIDDEN')
        assert len(t.platform.calls) == count
        assert runner(s.database, identifier) == before_duplicate
        assert off.call('GET', '/v1/runners/' + identifier, headers=off_headers).status_code == 200
        s.rows('UPDATE tenants SET credit_balance=%s WHERE tenant_id=%s', (original_credit, s.tenant_id))
        # Original default --once must safely leave an accepted native control
        # unconsumed while its current external state is unavailable. This is a
        # bounded CLI exit/PG contract, not a claim of resident FIFO fairness.
        before_hold = runner(s.database, identifier)
        claim = s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,))
        holding, _ = start_original_worker(fleet, off, once=True)
        fleet.assert_clean_exit(holding)
        held = runner(s.database, identifier)
        assert held['status'] == before_hold['status'] == 'paused'
        assert held['attempt'] == before_hold['attempt'] == 1
        assert held['resume_control_id'] == control_id
        assert held['worker_id'] is None and held['lease_until'] is None
        assert held['checkpoint'] == before_hold['checkpoint']
        assert s.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s', (control_id,)) == [{'status': 'accepted', 'consumed_attempt': None}]
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == claim
        assert t.facts()[0]['phase'] == 'accepted'
        assert provider_peer.requests(marker) == []
        assert s.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == []
        t.platform.states[len(t.platform.calls)] = StateReply()
        restoring, _ = start_original_worker(fleet, off, maximum=1)
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(restoring)
        assert finished['status'] == 'completed' and finished['attempt'] == 2
        assert finished['settlement_status'] == 'settled' and finished['resume_control_id'] is None
        assert finished['worker_id'] is None and finished['lease_until'] is None
        assert len(provider_peer.requests(marker)) == 1 and not provider_peer.errors
        assert t.facts()[0]['phase'] == 'applied' and t.facts()[0]['input_ref'] == ref
        assert len(t.facts()) == 1
        assert s.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s', (control_id,)) == [{'status': 'consumed', 'consumed_attempt': 2}]
        receipts = s.rows('SELECT phase,applied FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,))
        assert receipts == [{'phase': 'observed', 'applied': True}]
        assert s.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE record_id=%s', (finished['record_id'],)) == [{'total_token_count': 18, 'credit_cost': Decimal('0.01')}]
        history = s.rows('SELECT message_id,role,metadata FROM channel_messages WHERE session_id=%s ORDER BY created_at', (s.legacy_sid,))
        assert [m['role'] for m in history] == ['user', 'user', 'assistant']
        assert sum(m['message_id'] == ref + ':user' for m in history) == 1
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == [{'owner_runner_id': identifier, 'gate': 'delivery'}]
        assert not t.platform.errors and not s.peer.errors
    except BaseException:
        print(json.dumps(safe_source_execution_diagnostic(s, identifier, service_processes), sort_keys=True))
        raise
    finally:
        try:
            s.rows('UPDATE tenants SET credit_balance=%s WHERE tenant_id=%s', (original_credit, s.tenant_id))
        finally:
            try:
                fleet.close()
            finally:
                if off is not None:
                    off.close()
