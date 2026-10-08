"""Additional real PG Browser lease/owner race contracts, not runtime tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import queue

import pytest

from .conftest import wait_for
from .test_browser_binding_storage import browser_owner, bind, activate, assistance
from .test_storage import storage

pytestmark = pytest.mark.integration


def test_browser_wait_blocked_on_original_assistance_unique_key_cannot_commit_after_browser_lease_expiry(
        browser_owner, service_database):
    from src.services.agent_runner.browser_binding import BrowserOwnerRepository
    from src.services.agent_runner.ownership import LeaseLost
    values = browser_owner
    before = bind(values)
    activate(values)
    request = assistance(values)
    service_database.rows("UPDATE bs_browser_runs SET owner_lease_until=clock_timestamp()+interval '2 seconds' WHERE run_id=%s", (values['record']['run_id'],))
    backend = queue.Queue()
    @contextmanager
    def connection_factory():
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid() AS pid')
            backend.put(cursor.fetchone()['pid'])
            yield connection
    delayed = BrowserOwnerRepository(connection_factory)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_database.connect() as blocker, blocker.cursor() as cursor:
            # This uncommitted legacy-compatible audit row is invisible to
            # SELECT but holds the actual unique-index key during INSERT.
            cursor.execute('INSERT INTO bs_browser_assistance_requests(assistance_id,run_id) VALUES (%s,%s)',
                           (request['assistance_id'], request['run_id']))
            future = pool.submit(delayed.bind_wait, values['attempt'], before['revision'],
                execution_id=values['root_id'], call_id=values['call_id'], assistance=request, worker_boot=values['boot'], browser_epoch=values['epoch'])
            try:
                pid = backend.get(timeout=2)
                wait_for(lambda: service_database.rows('''SELECT a.wait_event_type='Lock' AND
                    a.xact_start<r.owner_lease_until AND clock_timestamp()<r.owner_lease_until AS blocked
                    FROM pg_stat_activity a CROSS JOIN bs_browser_runs r WHERE a.pid=%s AND r.run_id=%s''',
                    (pid, request['run_id']))[0]['blocked'], timeout=1)
                wait_for(lambda: service_database.rows('SELECT clock_timestamp()>=owner_lease_until AS expired FROM bs_browser_runs WHERE run_id=%s', (request['run_id'],))[0]['expired'], timeout=3)
            finally:
                # Release the real key and join the real thread before fixture
                # teardown/drop, even when observation assertions fail.
                blocker.rollback()
        with pytest.raises(LeaseLost):
            future.result(timeout=3)
    current = values['repository'].get(before['runner_id'])
    assert current['revision'] == before['revision'] and current['checkpoint'] == before['checkpoint']
    assert service_database.rows('SELECT 1 FROM bs_browser_assistance_requests WHERE assistance_id=%s', (request['assistance_id'],)) == []


def test_new_runner_attempt_worker_cannot_bind_wait_to_another_original_live_browser_owner(
        browser_owner, service_database):
    from src.services.agent_runner.ownership import Attempt, LeaseLost
    values = browser_owner
    before = bind(values)
    activate(values)
    replacement = service_database.rows('''UPDATE agent_runners SET attempt=attempt+1,worker_id=%s,
        lease_until=clock_timestamp()+interval '120 seconds' WHERE runner_id=%s RETURNING *''',
        ('fixture-different-browser-worker', before['runner_id']))[0]
    changed = Attempt(replacement['runner_id'], replacement['worker_id'], replacement['attempt'])
    request = assistance(values)
    with pytest.raises(LeaseLost):
        values['owner'].bind_wait(changed, replacement['revision'], execution_id=values['root_id'],
            call_id=values['call_id'], assistance=request, worker_boot=values['boot'], browser_epoch=values['epoch'])
    current = values['repository'].get(before['runner_id'])
    assert current['checkpoint'] == replacement['checkpoint'] and current['revision'] == replacement['revision']
    assert service_database.rows('SELECT 1 FROM bs_browser_assistance_requests WHERE assistance_id=%s', (request['assistance_id'],)) == []


def test_browser_activation_and_wait_reject_run_row_with_foreign_original_user_binding(
        browser_owner, service_database):
    from src.services.agent_runner.ownership import LeaseLost
    values = browser_owner
    before = bind(values)
    service_database.rows('UPDATE bs_browser_runs SET user_id=%s WHERE run_id=%s',
                          ('foreign-fictional-browser-user', values['record']['run_id']))
    with pytest.raises(LeaseLost):
        activate(values)
    assert service_database.rows('SELECT runtime_state FROM bs_browser_runs WHERE run_id=%s',
                                  (values['record']['run_id'],)) == [{'runtime_state': 'starting'}]
    # Independently validate wait's same identity gate under a native live
    # database state; this is not an actual executor proof.
    service_database.rows("UPDATE bs_browser_runs SET runtime_state='live' WHERE run_id=%s", (values['record']['run_id'],))
    request = assistance(values)
    with pytest.raises(LeaseLost):
        values['owner'].bind_wait(values['attempt'], before['revision'], execution_id=values['root_id'],
            call_id=values['call_id'], assistance=request, worker_boot=values['boot'], browser_epoch=values['epoch'])
    assert values['repository'].get(before['runner_id'])['checkpoint'] == before['checkpoint']
    assert service_database.rows('SELECT 1 FROM bs_browser_assistance_requests WHERE assistance_id=%s', (request['assistance_id'],)) == []


def test_current_worker_wrong_boot_or_epoch_cannot_use_stored_checkpoint_as_live_owner_proof(
        browser_owner, service_database):
    from src.services.agent_runner.ownership import LeaseLost
    values = browser_owner
    before = bind(values)
    activate(values)
    request = assistance(values)
    for field in ('worker_boot', 'browser_epoch'):
        proof = {'worker_boot': values['boot'], 'browser_epoch': values['epoch']}
        proof[field] = 'foreign-native-runtime-proof'
        with pytest.raises(LeaseLost):
            values['owner'].bind_wait(values['attempt'], before['revision'], execution_id=values['root_id'],
                call_id=values['call_id'], assistance=request, **proof)
    current = values['repository'].get(before['runner_id'])
    assert current['revision'] == before['revision'] and current['checkpoint'] == before['checkpoint']
    assert service_database.rows('SELECT 1 FROM bs_browser_assistance_requests WHERE assistance_id=%s', (request['assistance_id'],)) == []
