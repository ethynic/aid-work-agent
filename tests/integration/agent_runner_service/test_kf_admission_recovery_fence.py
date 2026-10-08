"""Actual uncommitted recovery lease expiry behind original event SQL lock.

The one-second recovery argument is explicit timing DI, not a default lease or
fake clock/Attempt. Actual original API/Worker/source proof/SQL/rollback/hold run.
"""
from datetime import datetime
import json
from pathlib import Path
import uuid

import pytest

from .conftest import wait_for
from .kf_admission_fixtures import text_scope, kf_scope
from .kf_admission_service import start_original_worker, verify_original_resources, safe_source_execution_diagnostic
from .provider import Reply
from .test_usage_storage import prices
from .test_worker import Workers, runner, decoded

pytestmark = pytest.mark.integration


def test_actual_recovery_event_sql_lock_crosses_new_short_lease_and_rolls_back_attempt_control_and_checkpoint(text_scope, service_processes, provider_peer, prices):
    t, s = text_scope, text_scope.scope
    marker = provider_peer.register(Reply(status=409))
    accepted = t.accept(t.text('recover_last_fence_', marker))
    identifier, ref = accepted['current_runner_id'], accepted['input_ref']
    headers = t.api.headers(scope=s, input_ref=ref)
    path = '/v1/runners/' + identifier + '/controls'
    fleet = Workers(service_processes, t.api, provider_peer, prices)
    report = fleet.root / 'original-recovery-tx-clock.json'
    probe = Path(__file__).with_name('kf_admission_recovery_lock_process.py')
    try:
        verify_original_resources(fleet, t.api)
        pause = t.api.call('POST', path, headers=headers,
            json={'action': 'pause', 'client_request_id': uuid.uuid4().hex})
        assert pause.status_code == 202
        acknowledger, _ = start_original_worker(fleet, t.api, maximum=1)
        fleet.assert_clean_exit(acknowledger)
        assert runner(s.database, identifier)['status'] == 'paused'
        resume = t.api.call('POST', path, headers=headers,
            json={'action': 'resume', 'client_request_id': uuid.uuid4().hex})
        assert resume.status_code == 202
        control_id = resume.json()['control']['control_id']
        before = runner(s.database, identifier)
        assert before['attempt'] == 1 and before['resume_control_id'] == control_id
        assert decoded(before['checkpoint'])['unstarted'] is True
        assert before['worker_id'] is None and before['lease_until'] is None
        prior_claim = s.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,))
        prior_inputs = t.facts()
        with s.database.connect() as blocking, blocking.cursor() as cursor:
            cursor.execute('LOCK TABLE agent_runner_events IN ACCESS EXCLUSIVE MODE')
            environment = {**fleet.environment, **t.api.environment, 'QWEN_MODEL_CODE': fleet.models[0],
                'KF_RECOVERY_LOCK_RUNNER': identifier, 'KF_RECOVERY_LOCK_REPORT': str(report)}
            worker_id = 'native-recovery-short-lease-' + uuid.uuid4().hex
            child = service_processes.start([str(probe), 'worker', '--worker-id', worker_id, '--once'],
                environment=environment, private_working_directory=True)
            fleet.children.append((child, worker_id))
            try:
                observed = wait_for(lambda: json.loads(report.read_text()) if report.is_file() else None, timeout=15)
                assert observed['notify_entered'] and observed['lease_argument_seconds'] == 1
                assert observed['previous_attempt'] == 1 and observed['new_attempt'] == 2
                deadline = datetime.fromisoformat(observed['actual_lease_until'])
                prepared_at = datetime.fromisoformat(observed['actual_prepared_observed_at'])
                assert datetime.fromisoformat(observed['database_now']) < deadline
                pid = observed['backend_pid']
                wait_for(lambda: s.rows("SELECT wait_event_type='Lock' AS blocked FROM pg_stat_activity WHERE pid=%s", (pid,))[0]['blocked'], timeout=2)
                # The uncommitted Attempt2 is known only from the original return
                # row observation; another connection still sees the old Attempt1.
                assert runner(s.database, identifier)['attempt'] == 1
                def actual_clock_crossed():
                    cursor.execute('SELECT clock_timestamp() AS now')
                    value = cursor.fetchone()['now']
                    return value if value >= deadline else None
                released_at = wait_for(actual_clock_crossed, timeout=2)
                assert 0 <= (released_at - prepared_at).total_seconds() < 10
                cursor.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s', (pid,))
                assert cursor.fetchone()['wait_event_type'] == 'Lock'
                blocking.commit()  # Release the actual SQL lock; no thread/caller result is fabricated.
                fleet.assert_clean_exit(child)
            finally:
                blocking.rollback()  # Always release our own lock before process cleanup.
        observed = json.loads(report.read_text())
        assert observed['claim_exception_class'] == 'SourceUnavailable'
        assert observed['claim_error_code'] == 'SOURCE_RECOVERY_ATTEMPT_UNAVAILABLE'
        after = runner(s.database, identifier)
        assert after['status'] == before['status'] == 'paused'
        assert after['attempt'] == before['attempt'] == 1
        assert after['revision'] == before['revision']
        assert after['checkpoint'] == before['checkpoint']
        assert after['resume_control_id'] == control_id and not after['cancel_requested']
        assert after['worker_id'] is None and after['lease_until'] is None
        assert t.facts() == prior_inputs
        assert s.rows('SELECT * FROM agent_runner_session_claims WHERE owner_runner_id=%s', (identifier,)) == prior_claim
        assert s.rows('SELECT status,consumed_attempt,consumed_at FROM agent_runner_controls WHERE control_id=%s', (control_id,)) == [{'status': 'accepted', 'consumed_attempt': None, 'consumed_at': None}]
        assert provider_peer.requests(marker) == [] and not provider_peer.errors
        assert s.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s', (identifier,)) == []
        assert s.rows('SELECT 1 FROM chat_records WHERE session_id=%s', (s.legacy_sid,)) == []
        # A real public verification/hold event may advance view/event, but the
        # rolled-back running/Attempt2 event must never be published.
        events = s.rows('SELECT payload FROM agent_runner_events WHERE runner_id=%s AND seq>%s ORDER BY seq', (identifier, before['event_seq']))
        assert len(events) == 1 and events[0]['payload']['attempt'] == 1
        assert events[0]['payload']['status'] == 'paused'
        assert after['event_seq'] == before['event_seq'] + 1
        assert isinstance(after['public_snapshot']['waiting'], dict)
        assert after['public_snapshot']['waiting']['kind'] == 'verification'
        assert not t.platform.errors and not s.peer.errors
    except BaseException:
        print(json.dumps(safe_source_execution_diagnostic(s, identifier, service_processes), sort_keys=True))
        raise
    finally:
        fleet.close()
