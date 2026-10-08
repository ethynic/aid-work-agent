"""Real PG completion locks against declared native storage-contract shapes.

The fixture binds original root/child calls through the production repository.
It does not claim an actual Browser activation/sampler or runtime completion.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
import json
import queue

import pytest

from .conftest import wait_for
from .test_storage import storage
from .test_browser_binding_storage import browser_owner, bind, activate, assistance

pytestmark = pytest.mark.integration


def prepared_wait(values, *, expires_at=None):
    from src.services.agent_runner.browser_completion import BrowserCompletionRepository
    bound = bind(values)
    activate(values)
    request = assistance(values)
    if expires_at is not None:
        request['expires_at'] = expires_at
    waited = values['owner'].bind_wait(values['attempt'], bound['revision'],
        execution_id=values['root_id'], call_id=values['call_id'], assistance=request,
        worker_boot=values['boot'], browser_epoch=values['epoch'])
    ref = waited['checkpoint']['execution']['resources']['browser_runs'][values['call_id']]
    binding = {**{key: ref[key] for key in ('runner_id', 'run_id', 'runner_execution_id', 'runner_tool_call_id',
        'owner_worker_id', 'owner_boot_id', 'browser_epoch', 'owner_endpoint')},
        **{key: waited[key] for key in ('tenant_id', 'user_id', 'session_id')}}
    return BrowserCompletionRepository(values['repository'].connection_factory), waited, request, binding


def prepared_completion(values, database):
    """Declared native waiting CP, genuine ports/fact/bridge/claim throughout."""
    repository, waited, request, binding = prepared_wait(values)
    fact = repository.record_fact(binding, request['assistance_id'], step_index=0, completed_by_human=True)
    actual = database.rows('SELECT runner_wait_id FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                          (request['assistance_id'],))[0]
    checkpoint = copy.deepcopy(waited['checkpoint'])
    root = checkpoint['execution']
    root['outcome'] = 'waiting'
    root['tools'][values['call_id']]['phase'] = 'waiting'
    root['waiting'] = dict(kind='human_assistance', tool_call_id=values['call_id'],
        assistance_id=request['assistance_id'], wait_id=actual['runner_wait_id'], target_execution_id=values['root_id'])
    parked = values['execution'].park(values['attempt'], waited['revision'], 'waiting', checkpoint, {})
    control_id = repository.bridge_parked(binding, request['assistance_id'])
    assert control_id is not None
    parked = values['repository'].get(parked['runner_id'])
    prepared = copy.deepcopy(parked['checkpoint'])
    prepared['applied_control_id'] = control_id
    prepared['execution']['resources']['browser_completions'] = {values['call_id']: fact['completion_ref']}
    prepared['execution']['resources']['browser_waits'] = {values['call_id']: copy.deepcopy(root['waiting'])}
    return repository, parked, request, binding, fact, prepared


def test_completion_bridge_typed_checkpoint_guard_preserves_original_fact_claim_and_control_history(
        browser_owner, service_database):
    from src.services.agent_runner.browser_completion import BrowserRecoveryUnavailable
    repository, parked, request, binding, fact, _ = prepared_completion(browser_owner, service_database)
    bad = copy.deepcopy(parked['checkpoint'])
    bad['execution']['resources'] = []
    service_database.rows('UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s',
                          (json.dumps(bad), parked['runner_id']))
    before = browser_owner['repository'].get(parked['runner_id'])
    with pytest.raises(BrowserRecoveryUnavailable) as failure:
        repository.bridge_parked(binding, request['assistance_id'])
    assert failure.value.code == 'BROWSER_COMPLETION_OWNER_MISMATCH'
    after = browser_owner['repository'].get(parked['runner_id'])
    assert after['checkpoint'] == before['checkpoint'] and after['revision'] == before['revision']
    assert after['resume_control_id'] == before['resume_control_id']
    assert repository.read_fact(parked['runner_id'], fact['completion_ref'])['continuation']['phase'] == 'observed'
    assert service_database.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE runner_id=%s',
        (parked['runner_id'],)) == [{'status': 'accepted', 'consumed_attempt': None}]
    assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
        (parked['runner_id'],)) == [{'owner_runner_id': parked['runner_id']}]


def test_completion_recovery_wait_lock_crossing_browser_lease_cannot_consume_control_or_new_attempt(
        browser_owner, service_database):
    from src.services.agent_runner.browser_completion import BrowserRecoveryUnavailable
    from src.services.agent_runner.browser_recovery import BrowserRecoveryProof
    from src.services.agent_runner.recovery_repository import RecoveryRepository
    values = browser_owner
    repository, parked, request, binding, fact, prepared = prepared_completion(values, service_database)
    service_database.rows("UPDATE bs_browser_runs SET owner_lease_until=clock_timestamp()+interval '2 seconds' WHERE run_id=%s",
                          (request['run_id'],))
    backend = queue.Queue()

    @contextmanager
    def delayed_connection():
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid() AS pid')
            backend.put(cursor.fetchone()['pid'])
            yield connection

    delayed = RecoveryRepository(delayed_connection)
    # Actual PG claim proof only; native boot is a declared storage fixture,
    # not an in-memory Browser activation or a fake authorization result.
    proof = BrowserRecoveryProof({'boot': values['boot']}, service_database.connect)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_database.connect() as blocker, blocker.cursor() as cursor:
            cursor.execute('SELECT assistance_id FROM bs_browser_assistance_requests WHERE assistance_id=%s FOR UPDATE',
                           (request['assistance_id'],))
            future = pool.submit(delayed.claim_resume, parked['runner_id'], values['attempt'].worker_id, 60,
                revision=parked['revision'], control_id=parked['resume_control_id'], checkpoint=prepared,
                recovery_port=proof)
            try:
                pid = backend.get(timeout=2)
                wait_for(lambda: service_database.rows('''SELECT a.wait_event_type='Lock' AND
                    clock_timestamp()<b.owner_lease_until AS blocked FROM pg_stat_activity a
                    CROSS JOIN bs_browser_runs b WHERE a.pid=%s AND b.run_id=%s''',
                    (pid, request['run_id']))[0]['blocked'], timeout=1)
                wait_for(lambda: service_database.rows('SELECT clock_timestamp()>=owner_lease_until AS expired FROM bs_browser_runs WHERE run_id=%s',
                    (request['run_id'],))[0]['expired'], timeout=3)
            finally:
                blocker.rollback()
        with pytest.raises(BrowserRecoveryUnavailable):
            future.result(timeout=4)
    current = values['repository'].get(parked['runner_id'])
    assert current['checkpoint'] == parked['checkpoint'] and current['revision'] == parked['revision']
    assert current['attempt'] == parked['attempt'] and current['worker_id'] is None and current['lease_until'] is None
    assert current['resume_control_id'] == parked['resume_control_id']
    assert service_database.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE runner_id=%s',
        (parked['runner_id'],)) == [{'status': 'accepted', 'consumed_attempt': None}]
    assert repository.read_fact(parked['runner_id'], fact['completion_ref'])['continuation']['phase'] == 'observed'
    assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',
        (parked['runner_id'],)) == [{'owner_runner_id': parked['runner_id']}]
    assert service_database.rows('SELECT 1 FROM agent_runner_usage_receipts WHERE runner_id=%s', (parked['runner_id'],)) == []


def test_completion_wait_lock_crossing_real_expiry_cannot_commit_stale_clock_projection(
        browser_owner, service_database):
    from src.services.agent_runner.browser_completion import BrowserCompletionRepository, BrowserRecoveryUnavailable
    values = browser_owner
    expiry = service_database.rows("SELECT clock_timestamp()::timestamp+interval '2 seconds' AS expiry")[0]['expiry']
    _, waited, request, binding = prepared_wait(values, expires_at=expiry)
    backend = queue.Queue()

    @contextmanager
    def connection_factory():
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid() AS pid')
            backend.put(cursor.fetchone()['pid'])
            yield connection

    delayed = BrowserCompletionRepository(connection_factory)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_database.connect() as blocker, blocker.cursor() as cursor:
            cursor.execute('SELECT assistance_id FROM bs_browser_assistance_requests WHERE assistance_id=%s FOR UPDATE',
                           (request['assistance_id'],))
            future = pool.submit(delayed.record_fact, binding, request['assistance_id'],
                                 step_index=0, completed_by_human=True)
            try:
                pid = backend.get(timeout=2)
                wait_for(lambda: service_database.rows('''SELECT a.wait_event_type='Lock' AND
                    clock_timestamp()<w.expires_at AS before_expiry FROM pg_stat_activity a
                    CROSS JOIN bs_browser_assistance_requests w WHERE a.pid=%s AND w.assistance_id=%s''',
                    (pid, request['assistance_id']))[0]['before_expiry'], timeout=1)
                wait_for(lambda: service_database.rows('''SELECT clock_timestamp()>=expires_at AS expired
                    FROM bs_browser_assistance_requests WHERE assistance_id=%s''',
                    (request['assistance_id'],))[0]['expired'], timeout=3)
                assert service_database.rows('''SELECT owner_lease_until>clock_timestamp() AS live
                    FROM bs_browser_runs WHERE run_id=%s''', (request['run_id'],)) == [{'live': True}]
            finally:
                blocker.rollback()
        try:
            with pytest.raises(BrowserRecoveryUnavailable) as failure:
                future.result(timeout=4)
            assert failure.value.code == 'BROWSER_COMPLETION_WAIT_EXPIRED'
        except BaseException:
            row = service_database.rows('''SELECT completion_ref,completion_fact FROM bs_browser_assistance_requests
                WHERE assistance_id=%s''', (request['assistance_id'],))[0]
            print('SAFE_COMPLETION_LOCK_DIAG=' + str({'completion_ref_present': bool(row['completion_ref']),
                'phase': ((row['completion_fact'] or {}).get('continuation') or {}).get('phase'),
                'browser_lease_still_live': service_database.rows('SELECT owner_lease_until>clock_timestamp() AS live FROM bs_browser_runs WHERE run_id=%s',
                    (request['run_id'],))[0]['live']}))
            raise
    assert service_database.rows('SELECT completion_ref,completion_fact FROM bs_browser_assistance_requests WHERE assistance_id=%s',
        (request['assistance_id'],)) == [{'completion_ref': None, 'completion_fact': None}]
    current = values['repository'].get(waited['runner_id'])
    assert current['revision'] == waited['revision'] and current['checkpoint'] == waited['checkpoint']
    assert service_database.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s', (waited['runner_id'],)) == []
