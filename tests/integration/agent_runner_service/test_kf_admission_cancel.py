"""Prepared real queued-pause/unstarted ACK/user-cancel regression.

No checkpoint or pause outcome is fabricated. Both acknowledgements are from
the original HTTP/Worker paths; cancellation owns the original finalizer.
"""
from decimal import Decimal
import json
import uuid

import pytest

from .conftest import wait_for
from .provider import Reply
from .kf_admission_fixtures import text_scope, kf_scope
from .kf_admission_service import start_original_worker, verify_original_resources, safe_source_execution_diagnostic
from .test_usage_storage import prices
from .test_worker import Workers, runner, decoded, terminal

pytestmark = pytest.mark.integration


def test_actual_queued_pause_unstarted_ack_then_read_authorized_cancel_finishes_without_model_or_false_application(text_scope, service_processes, provider_peer, prices):
    t, s = text_scope, text_scope.scope
    marker = provider_peer.register(Reply(status=409))
    locator = t.text('pause_before_start_', marker)
    accepted = t.accept(locator)
    identifier = accepted['current_runner_id']
    headers = t.api.headers(scope=s, input_ref=accepted['input_ref'])
    paused = t.api.call('POST', '/v1/runners/' + identifier + '/controls', headers=headers,
        json={'action': 'pause', 'client_request_id': 'pause_before_start_' + uuid.uuid4().hex})
    assert paused.status_code == 202
    control_id = paused.json()['control']['control_id']
    assert runner(s.database, identifier)['pause_requested']
    fleet = Workers(service_processes, t.api, provider_peer, prices)
    try:
        verify_original_resources(fleet, t.api)
        acknowledger, _ = start_original_worker(fleet, t.api, maximum=1)
        fleet.assert_clean_exit(acknowledger)
        original = wait_for(lambda: (r if (r := runner(s.database, identifier))['status'] == 'paused' else None), timeout=10)
        checkpoint = decoded(original['checkpoint'])
        assert original['attempt'] == 1 and original['worker_id'] is None and original['lease_until'] is None
        assert checkpoint['unstarted'] is True and 'execution' not in checkpoint
        assert original['record_id'] == runner(s.database, identifier)['record_id']
        assert not provider_peer.requests(marker) and not provider_peer.errors
        assert s.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == []
        assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
        fact = t.facts()[0]
        assert fact['phase'] == 'accepted' and fact['input_ref'] == accepted['input_ref']
        assert s.rows('SELECT status FROM agent_runner_controls WHERE control_id=%s', (control_id,)) == [{'status': 'consumed'}]
        claim = s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,))
        assert claim == [{'owner_runner_id': identifier, 'gate': 'execution'}]
        cancellation = t.api.call('POST', '/v1/runners/' + identifier + '/cancel', headers=headers)
        assert cancellation.status_code == 200 and runner(s.database, identifier)['cancel_requested']
        finisher, _ = start_original_worker(fleet, t.api, maximum=1)
        finished = terminal(s.database, identifier)
        fleet.assert_clean_exit(finisher)
        assert finished['status'] == 'cancelled' and finished['settlement_status'] == 'settled'
        assert finished['attempt'] == 2 and finished['record_id'] == original['record_id']
        assert finished['worker_id'] is None and finished['lease_until'] is None
        assert not provider_peer.requests(marker) and not provider_peer.errors
        assert t.facts()[0]['phase'] == 'cancelled'  # No model application is inferred from cancelled history.
        assert t.facts()[0]['accepted_runner_id'] == t.facts()[0]['current_runner_id'] == identifier
        assert s.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == []
        records = s.rows('SELECT record_id,total_token_count,credit_cost FROM chat_records WHERE session_id=%s', (s.legacy_sid,))
        assert records == [{'record_id': original['record_id'], 'total_token_count': 0, 'credit_cost': Decimal('0')}]
        assert s.rows('SELECT credit_balance FROM tenants WHERE tenant_id=%s', (s.tenant_id,))[0]['credit_balance'] == Decimal('1000')
        history = s.rows('SELECT message_id,role,content,metadata FROM channel_messages WHERE session_id=%s ORDER BY created_at', (s.legacy_sid,))
        assert len(history) == 3 and history[0]['content'] == 'Original fictional history'
        assert history[1]['message_id'] == accepted['input_ref'] + ':user' and history[1]['content'] == marker
        assert history[2]['role'] == 'assistant' and decoded(history[2]['metadata'])['cancelled'] is True
        assert s.rows('SELECT owner_runner_id,gate FROM agent_runner_session_claims WHERE session_id=%s', (s.legacy_sid,)) == [{'owner_runner_id': identifier, 'gate': 'delivery'}]
        repeat = t.api.call('POST', '/v1/runners/' + identifier + '/cancel', headers=headers)
        assert repeat.status_code == 200
        contender, _ = start_original_worker(fleet, t.api, once=True)
        fleet.assert_clean_exit(contender)
        assert s.rows('SELECT count(*) AS n FROM chat_records WHERE session_id=%s', (s.legacy_sid,))[0]['n'] == 1
        assert s.rows('SELECT count(*) AS n FROM channel_messages WHERE session_id=%s', (s.legacy_sid,))[0]['n'] == 3
    except BaseException:
        print(json.dumps(safe_source_execution_diagnostic(s, identifier, service_processes), sort_keys=True))
        raise
    finally:
        fleet.close()
