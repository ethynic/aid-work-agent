"""Independent PostgreSQL timer fencing; no shared scheduler or external tools.

These tests use real repositories and child interpreters, but deliberately do
not claim that a repository-only resume proves a complete worker/model turn.
"""
import copy
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
import queue
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Outcome, ToolCall, ToolFact
from src.services.agent_runner.durable_sleep import complete_sleep
from src.services.agent_runner.timer_repository import TimerRepository
from src.services.agent_runner.wait_identity import owned_wait_id

from .test_storage import attempt, storage
from .test_usage_storage import prices
from .conftest import wait_for

pytestmark = pytest.mark.integration


def sleeping_checkpoint(row):
    from src.core.agent_engine.contracts import Identity
    identity = Identity(**{key: row[key] for key in ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')})
    state = ExecutionState(identity, row['runner_id'], AgentMode.STANDALONE, 'Timer acceptance',
                           [{'role': 'user', 'content': 'Read fictional updates'}], iteration=1,
                           outcome=Outcome.WAITING)
    call = ToolCall('independent-sleep-1', 'agent_sleep', {'seconds': 1})
    state.pending = [call]
    state.tools = {call.id: ToolFact(call, phase='waiting')}
    state.waiting = {'version': 1, 'kind': 'timer', 'seconds': 1,
                     'tool_call_id': call.id, 'target_execution_id': state.execution_id}
    state.waiting['wait_id'] = owned_wait_id(state.execution_id, state.execution_id, state.waiting)
    state.resources.update(business_iterations=1, round_tool_ids=[call.id])
    return {'execution': state.checkpoint()}


def park_timer(storage, actor):
    _, execution, submit = storage
    row, principal = submit(actor)
    acquired = execution.acquire('independent-original-owner', 30)
    assert acquired['runner_id'] == row['runner_id']
    parked = execution.park(attempt(acquired), acquired['revision'], 'waiting',
                            sleeping_checkpoint(acquired), {})
    return parked, principal


def prepared(row):
    checkpoint = copy.deepcopy(row['checkpoint'])
    state = ExecutionState.restore(checkpoint['execution'])
    complete_sleep(row, {state.execution_id: state}, checkpoint, trigger='timer')
    checkpoint['execution'] = state.checkpoint()
    return checkpoint


def expire_timer(database, row):
    # Move both authoritative facts together, rather than creating a mismatch.
    checkpoint = copy.deepcopy(row['checkpoint'])
    deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    checkpoint['durable_sleep']['wake_at'] = deadline.isoformat()
    checkpoint['execution']['waiting']['wake_at'] = deadline.isoformat()
    database.rows('UPDATE agent_runners SET wake_at=%s,checkpoint=%s::jsonb WHERE runner_id=%s',
                  (deadline, json.dumps(checkpoint), row['runner_id']))
    return storage_row(database, row['runner_id'])


def storage_row(database, runner_id):
    from src.services.agent_runner.repository import decoded
    return decoded(database.rows('SELECT * FROM agent_runners WHERE runner_id=%s', (runner_id,))[0])


def test_real_sleep_wait_releases_attempt_but_preserves_original_session_claim(storage, actors, service_database):
    row, _ = park_timer(storage, actors['a'])
    assert row['status'] == 'waiting' and row['worker_id'] is None and row['lease_until'] is None
    assert row['wake_at'] is not None
    assert row['checkpoint']['execution']['resources']['business_iterations'] == 0
    claims = service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s',
                                   (actors['a'].session_id,))
    assert [item['owner_runner_id'] for item in claims] == [row['runner_id']]
    assert TimerRepository(service_database.connect).claim(row, 'early-owner', 30, prepared(row)) is None


def test_two_fresh_processes_consume_one_timer_and_reaped_preaction_attempt_can_resume(
        storage, actors, service_database, tmp_path):
    row, _ = park_timer(storage, actors['a'])
    row = expire_timer(service_database, row)
    source = tmp_path / 'claim-input.json'
    # Row contains datetime objects; each child obtains its own fresh row.
    source.write_text(json.dumps({'runner_id': row['runner_id'], 'checkpoint': prepared(row)}))
    gate = tmp_path / 'start'
    script = r'''
import json, os, sys, time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
import psycopg2
from psycopg2.extras import RealDictCursor
from src.services.agent_runner.repository import decoded
from src.services.agent_runner.timer_repository import TimerRepository
assert urlsplit(os.environ['DATABASE_URL']).path.lstrip('/').startswith('aid_test_')
@contextmanager
def connect():
    connection=psycopg2.connect(os.environ['DATABASE_URL'],cursor_factory=RealDictCursor)
    try:
        with connection: yield connection
    finally: connection.close()
request=json.loads(Path(sys.argv[1]).read_text())
with connect() as connection,connection.cursor() as cursor:
    cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s',(request['runner_id'],))
    expected=decoded(cursor.fetchone())
Path(sys.argv[4]+'.ready').write_text('ready')
deadline=time.monotonic()+15
while not Path(sys.argv[2]).exists():
    if time.monotonic()>deadline: raise RuntimeError('TEST_GATE_TIMEOUT')
    time.sleep(.01)
won=TimerRepository(connect).claim(expected,sys.argv[3],30,request['checkpoint'])
Path(sys.argv[4]).write_text(json.dumps({'won':won is not None}))
'''
    children, outputs = [], []
    try:
        for index in range(2):
            output = tmp_path / f'result-{index}.json'
            outputs.append(output)
            children.append(subprocess.Popen([sys.executable, '-c', script, str(source), str(gate),
                                               f'independent-contender-{index}', str(output)],
                                              env=os.environ.copy(), stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL))
        deadline = time.monotonic() + 15
        while not all(Path(str(path)+'.ready').exists() for path in outputs):
            assert all(child.poll() is None for child in children), 'A timer contender exited before its gate'
            assert time.monotonic() < deadline, 'Timer contenders did not reach the shared gate'
            time.sleep(.01)
        gate.write_text('go')
        assert [child.wait(timeout=20) for child in children] == [0, 0]
        assert sum(json.loads(path.read_text())['won'] for path in outputs) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
    acquired = storage_row(service_database, row['runner_id'])
    assert acquired['attempt'] == row['attempt'] + 1 and acquired['wake_at'] is None
    assert acquired['record_id'] == row['record_id'] and acquired['input_digest'] == row['input_digest']
    assert service_database.rows('SELECT 1 FROM agent_runner_controls WHERE runner_id=%s', (row['runner_id'],)) == []
    fact = acquired['checkpoint']['execution']['tools']['independent-sleep-1']
    assert fact['phase'] == 'completed' and not fact['result_recorded']
    assert not any(message['role'] == 'tool' for message in acquired['checkpoint']['execution']['messages'])
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
                          (row['runner_id'],))
    assert storage[1].reap_expired() == [row['runner_id']]
    repository = TimerRepository(service_database.connect)
    recovered = repository.next_candidates()
    assert [item['runner_id'] for item in recovered] == [row['runner_id']]
    resumed = repository.claim(recovered[0], 'independent-post-crash-owner', 30, recovered[0]['checkpoint'])
    assert resumed['attempt'] == acquired['attempt'] + 1
    assert resumed['checkpoint']['durable_sleep']['woke_at'] == acquired['checkpoint']['durable_sleep']['woke_at']
    assert resumed['checkpoint']['execution']['tools']['independent-sleep-1'] == fact


@pytest.mark.parametrize('change', ['control_revision', 'pause', 'cancel', 'accepted_resume'])
def test_timer_claim_rechecks_user_control_after_preparation(storage, actors, service_database, change):
    row, _ = park_timer(storage, actors['a'])
    row = expire_timer(service_database, row)
    checkpoint = prepared(row)
    clauses = {'control_revision': 'control_revision=control_revision+1', 'pause': 'pause_requested=TRUE',
               'cancel': 'cancel_requested=TRUE', 'accepted_resume': "resume_control_id='accepted-by-user'"}
    service_database.rows('UPDATE agent_runners SET '+clauses[change]+' WHERE runner_id=%s', (row['runner_id'],))
    assert TimerRepository(service_database.connect).claim(row, 'stale-preparation', 30, checkpoint) is None
    fresh = storage_row(service_database, row['runner_id'])
    assert fresh['attempt'] == row['attempt'] and fresh['wake_at'] == row['wake_at']
    assert fresh['checkpoint']['durable_sleep']['phase'] == 'armed'


def test_started_action_after_timer_claim_prevents_automatic_crash_replay(storage, actors, service_database):
    row, _ = park_timer(storage, actors['a'])
    row = expire_timer(service_database, row)
    repository = TimerRepository(service_database.connect)
    acquired = repository.claim(row, 'independent-action-owner', 30, prepared(row))
    checkpoint = acquired['checkpoint']
    checkpoint['timer_activation']['action_started'] = True
    storage[1].save_checkpoint(attempt(acquired), acquired['revision'], checkpoint)
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
                          (row['runner_id'],))
    storage[1].reap_expired()
    assert repository.next_candidates() == []
    assert storage_row(service_database, row['runner_id'])['status'] == 'interrupted'


def test_trusted_nested_tree_parks_and_restores_original_timer_leaf(storage, actors, service_database, monkeypatch):
    """Public child catalog forbids delegation; this verifies an imported tree."""
    from src.config.settings import settings
    from src.services.agent_runner.recovery import RecoveryCoordinator
    from src.services.agent_runner.runtime_manifest import configuration_fingerprint
    monkeypatch.setattr(settings.agent_runner, 'durable_sleep_enabled', True)
    _, execution, submit = storage
    row, principal = submit(actors['a'])
    acquired = execution.acquire('nested-original-owner', 30)
    leaf = ExecutionState.restore(sleeping_checkpoint(acquired)['execution'])
    leaf.execution_id, leaf.profile_id, leaf.role = 'original-leaf', 'fictional-child', AgentMode.SUBAGENT
    leaf.waiting['target_execution_id'] = leaf.execution_id
    leaf.waiting['wait_id'] = owned_wait_id(row['runner_id'], leaf.execution_id, leaf.waiting)
    fingerprint = configuration_fingerprint('fixture-profile')
    leaf.resources['configuration_fingerprint'] = fingerprint
    middle = ExecutionState(principal.identity, 'original-middle', AgentMode.SUBAGENT, 'Middle', [],
                            profile_id='fictional-child', outcome=Outcome.WAITING,
                            resources={'configuration_fingerprint': fingerprint})
    root = ExecutionState(principal.identity, row['runner_id'], AgentMode.MASTER, 'Root', [],
                          profile_id='main', outcome=Outcome.WAITING,
                          resources={'configuration_fingerprint': fingerprint})
    for parent, child in ((middle, leaf), (root, middle)):
        call = ToolCall(parent.execution_id+'-delegate', 'delegate_to_subagent', {'subagent_name': 'fictional-child'})
        parent.pending = [call]
        parent.tools = {call.id: ToolFact(call, phase='waiting')}
        parent.children[call.id] = {'execution_id': child.execution_id, 'status': 'waiting',
            'profile_id': child.profile_id, 'profile_fingerprint': 'fixture-profile',
            'task_record': {'execution_id': child.execution_id, 'task_id': child.execution_id+'-task',
                            'subagent_name': 'fictional-child'}, 'checkpoint': child.checkpoint()}
        parent.waiting = {'kind': 'child_wait', 'tool_call_id': call.id,
                          'execution_id': child.execution_id, 'child_wait': copy.deepcopy(child.waiting)}
    parked = execution.park(attempt(acquired), acquired['revision'], 'waiting', {'execution': root.checkpoint()}, {})
    # Nested clock movement locates the timer leaf, preserving both ancestors.
    checkpoint = copy.deepcopy(parked['checkpoint'])
    deadline = datetime.now(timezone.utc)-timedelta(seconds=1)
    checkpoint['durable_sleep']['wake_at'] = deadline.isoformat()
    checkpoint['execution']['children'][root.pending[0].id]['checkpoint']['children'][middle.pending[0].id]['checkpoint']['waiting']['wake_at'] = deadline.isoformat()
    service_database.rows('UPDATE agent_runners SET wake_at=%s,checkpoint=%s::jsonb WHERE runner_id=%s',
                          (deadline, json.dumps(checkpoint), row['runner_id']))
    due = storage_row(service_database, row['runner_id'])
    authority = SimpleNamespace(authorize_persisted=lambda *a, **k: principal, assert_credit=lambda *a: None,
                                authorize_child_profile=lambda *a: None)
    coordinator = RecoveryCoordinator(authority, SimpleNamespace(resolve=lambda *a: (None, 'fixture-profile')))
    prepared_tree = coordinator.prepare(due, timer_trigger=True).checkpoint
    resumed = TimerRepository(service_database.connect).claim(due, 'nested-restored-owner', 30, prepared_tree)
    middle_after = resumed['checkpoint']['execution']['children'][root.pending[0].id]['checkpoint']
    leaf_after = middle_after['children'][middle.pending[0].id]['checkpoint']
    assert middle_after['execution_id'] == middle.execution_id and leaf_after['execution_id'] == leaf.execution_id
    assert leaf_after['tools']['independent-sleep-1']['phase'] == 'completed'
    assert not leaf_after['tools']['independent-sleep-1']['result_recorded']
    assert resumed['checkpoint']['execution']['tools'][root.pending[0].id]['phase'] == 'waiting'
    assert middle_after['tools'][middle.pending[0].id]['phase'] == 'waiting'


def test_five_sessions_keep_separate_sleep_ownership_and_cancelling_one_does_not_stop_others(
        storage, actors, service_database):
    rows = []
    for index in range(5):
        session_id = 'independent-monitor-session-'+uuid.uuid4().hex
        service_database.rows('INSERT INTO chat_sessions(session_id,user_id,tenant_id,title) VALUES (%s,%s,%s,%s)',
                              (session_id, actors['a'].user_id, actors['a'].tenant_id, f'Fictional target {index}'))
        row, principal = park_timer(storage, replace(actors['a'], session_id=session_id))
        rows.append((expire_timer(service_database, row), principal))
    repository = TimerRepository(service_database.connect)
    assert {row['runner_id'] for row in repository.next_candidates()} == {row['runner_id'] for row, _ in rows}
    storage[0].cancel(rows[0][1], rows[0][0]['runner_id'])
    candidates = repository.next_candidates()
    assert {row['runner_id'] for row in candidates} == {row['runner_id'] for row, _ in rows[1:]}
    claimed = [repository.claim(row, f'independent-session-owner-{index}', 30, prepared(row))
               for index, row in enumerate(candidates)]
    assert len({row['session_id'] for row in claimed}) == 4
    assert all(row['checkpoint']['execution']['execution_id'] == row['runner_id'] for row in claimed)
    assert storage_row(service_database, rows[0][0]['runner_id'])['cancel_requested']


def test_crash_before_park_is_not_automatically_armed_and_explicit_resume_pairs_a_failure(
        storage, actors, service_database, monkeypatch):
    from src.config.settings import settings
    from src.services.agent_runner.control_contracts import RunnerControl
    from src.services.agent_runner.control_repository import ControlRepository
    from src.services.agent_runner.recovery import RecoveryCoordinator
    from src.services.agent_runner.recovery_repository import RecoveryRepository
    from src.services.agent_runner.runtime_manifest import configuration_fingerprint
    monkeypatch.setattr(settings.agent_runner, 'durable_sleep_enabled', True)
    _, execution, submit = storage
    row, principal = submit(actors['a'])
    acquired = execution.acquire('crash-before-arm', 30)
    checkpoint = sleeping_checkpoint(acquired)
    checkpoint['execution'].update(role='master', profile_id='main')
    checkpoint['execution']['resources']['configuration_fingerprint'] = configuration_fingerprint('fixture-profile')
    saved = execution.save_checkpoint(attempt(acquired), acquired['revision'], checkpoint)
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
                          (row['runner_id'],))
    execution.reap_expired()
    assert TimerRepository(service_database.connect).next_candidates() == []
    interrupted = storage_row(service_database, row['runner_id'])
    assert interrupted['wake_at'] is None and 'durable_sleep' not in interrupted['checkpoint']
    wait = checkpoint['execution']['waiting']
    control, _ = ControlRepository(service_database.connect).submit(principal, row['runner_id'],
        RunnerControl(client_request_id='explicit-unarmed-resume', action='resume',
                      wait_id=wait['wait_id'], target_execution_id=wait['target_execution_id']))
    interrupted = storage_row(service_database, row['runner_id'])
    authority = SimpleNamespace(authorize_persisted=lambda *a, **k: principal, assert_credit=lambda *a: None)
    coordinator = RecoveryCoordinator(authority, SimpleNamespace(resolve=lambda *a: (None, 'fixture-profile')))
    prepared_tree = coordinator.prepare(interrupted, control).checkpoint
    resumed = RecoveryRepository(service_database.connect).claim_resume(row['runner_id'], 'explicit-recoverer', 30,
        revision=interrupted['revision'], control_id=control['control_id'], checkpoint=prepared_tree)
    fact = resumed['checkpoint']['execution']['tools']['independent-sleep-1']
    assert fact['phase'] == 'completed' and fact['success'] is False and not fact['result_recorded']
    assert fact['result']['error_code'] == 'SLEEP_NOT_COMMITTED'
    assert resumed['checkpoint']['execution']['resources']['business_iterations'] == 1
    assert resumed['wake_at'] is None


def test_unknown_started_sibling_prevents_park_and_timer_transaction_leaves_no_false_wait(
        storage, actors, service_database):
    from src.services.agent_runner.contracts import RunnerError
    _, execution, submit = storage
    row, _ = submit(actors['a'])
    acquired = execution.acquire('unsafe-write-owner', 30)
    checkpoint = sleeping_checkpoint(acquired)
    state = ExecutionState.restore(checkpoint['execution'])
    call = ToolCall('unknown-send', 'send_message', {'fictional': True})
    state.tools[call.id] = ToolFact(call, phase='dispatching')
    checkpoint['execution'] = state.checkpoint()
    with pytest.raises(RunnerError, match='SLEEP_UNSAFE_BOUNDARY'):
        execution.park(attempt(acquired), acquired['revision'], 'waiting', checkpoint, {})
    current = storage_row(service_database, row['runner_id'])
    assert current['status'] == 'running' and current['wake_at'] is None
    assert current['revision'] == acquired['revision'] and current['checkpoint'] == acquired['checkpoint']


def test_control_user_message_retained_then_archived_and_retried_has_one_database_identity(
        storage, actors, service_database):
    from src.services.agent_runner.continuous_history import archive_and_compact, compaction_cut
    from src.services.agent_runner.transaction_repository import write_history
    from src.services.agent_runner.worker import execution_result
    _, execution, submit = storage
    _, principal = submit(actors['a'])
    acquired = execution.acquire('independent-history-owner', 30)
    control_id = 'supplement-'+uuid.uuid4().hex
    generated = [{'role': 'assistant', 'content': f'Fictional observation {index}'} for index in range(120)]
    generated.insert(100, {'role': 'user', 'content': 'keep this accepted instruction',
                           'metadata': {'control_id': control_id, 'submitted_text': 'keep this accepted instruction'}})
    root = ExecutionState(principal.identity, acquired['runner_id'], AgentMode.MASTER, 'Archive',
                           [{'role': 'user', 'content': 'Initial request'}, *generated], initial_len=1)
    envelope = {'supplemental_inputs': [{'control_id': control_id, 'text': 'keep this accepted instruction'}]}
    port = SimpleNamespace(attempt=attempt(acquired), repository=execution, envelope=envelope, snapshot={})
    first_cut = compaction_cut(generated)
    root.resources.update(continuous_summary='Trusted fixture summary already produced',
                           continuous_summary_through=first_cut)
    archive_and_compact(port, root)
    assert service_database.rows("SELECT message_id FROM chat_messages WHERE session_id=%s AND metadata::jsonb->>'control_id'=%s",
                                 (actors['a'].session_id, control_id)) == []
    root.messages.extend({'role': 'assistant', 'content': f'Later observation {index}'} for index in range(96))
    next_cut = compaction_cut(root.messages[root.initial_len:])
    root.resources['continuous_summary_through'] = root.resources['history_offset']+next_cut
    before_checkpoint_commit = copy.deepcopy(root)
    archive_and_compact(port, root)
    archive_and_compact(port, before_checkpoint_commit)
    root.output = 'Finished fictional task'
    with service_database.connect() as connection, connection.cursor() as cursor:
        write_history(cursor, {**acquired, 'checkpoint': {'execution': root.checkpoint(), **envelope}},
                      execution_result(root, 'completed'))
    rows = service_database.rows("SELECT message_id FROM chat_messages WHERE session_id=%s AND metadata::jsonb->>'control_id'=%s",
                                  (actors['a'].session_id, control_id))
    assert rows == [{'message_id': control_id+':user'}]


@pytest.mark.parametrize('held_row', ['tenant', 'record'])
def test_physical_receipt_admission_waiting_on_billing_locks_rechecks_expired_lease(
        storage, actors, service_database, prices, held_row):
    from src.services.agent_runner.finalizer import RunnerFinalizer
    from src.services.agent_runner.ownership import LeaseLost
    from src.services.agent_runner.usage_repository import UsageRepository
    _, execution, submit = storage
    row, _ = submit(actors['a'])
    acquired = execution.acquire('billing-lock-race', 3)
    current = execution.save_checkpoint(attempt(acquired), acquired['revision'],
        {'continuous': {'version': 1, 'enabled': True}})
    RunnerFinalizer(service_database.connect).settle_active(attempt(current))
    backend = queue.Queue()
    @contextmanager
    def observed_connection():
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('SELECT pg_backend_pid() AS pid')
            backend.put(cursor.fetchone()['pid'])
            yield connection
    usage = UsageRepository(observed_connection)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_database.connect() as lock, lock.cursor() as cursor:
            if held_row == 'tenant':
                cursor.execute('SELECT tenant_id FROM tenants WHERE tenant_id=%s FOR UPDATE', (row['tenant_id'],))
            else:
                cursor.execute('SELECT record_id FROM chat_records WHERE record_id=%s FOR UPDATE', (row['record_id'],))
            pending = pool.submit(usage.start, attempt(current), call_id='blocked-physical-call',
                execution_id=row['runner_id'], provider='qwen', model=prices[0])
            pid = backend.get(timeout=5)
            wait_for(lambda: service_database.rows("SELECT pid FROM pg_stat_activity WHERE pid=%s AND wait_event_type='Lock'",
                                                   (pid,)), timeout=5)
            wait_for(lambda: service_database.rows('SELECT clock_timestamp()>=%s AS expired',
                                                   (current['lease_until'],))[0]['expired'], timeout=5)
        with pytest.raises(LeaseLost, match='RUNNER_ATTEMPT_EXPIRED'):
            pending.result(timeout=5)
    assert service_database.rows('SELECT receipt_id FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                 (row['runner_id'],)) == []


@pytest.mark.parametrize('status', ['waiting', 'paused'])
def test_large_history_at_zero_balance_can_park_without_a_new_paid_summary(
        storage, actors, service_database, monkeypatch, status):
    from src.memory.mid_term import ContextCompressionService
    from src.services.agent_runner.continuous_lifecycle import before_park
    from src.services.agent_runner.durable_control import DurableControl
    async def forbidden_summary(*args, **kwargs):
        raise AssertionError('Saving a safe wait must not require another provider call')
    monkeypatch.setattr(ContextCompressionService, '_call_summary_llm', forbidden_summary)
    _, execution, submit = storage
    row, _ = submit(actors['a'])
    acquired = execution.acquire('zero-balance-large-history', 30)
    checkpoint = sleeping_checkpoint(acquired)
    state = ExecutionState.restore(checkpoint['execution'])
    state.messages.extend({'role': 'assistant', 'content': f'Original observation {index}'} for index in range(160))
    state.resources['durable_sleep_version'] = 1
    if status == 'paused':
        state.outcome, state.waiting, state.pending, state.tools = Outcome.PAUSED, None, [], {}
    checkpoint['execution'] = state.checkpoint()
    saved = execution.save_checkpoint(attempt(acquired), acquired['revision'], checkpoint)
    service_database.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s', (row['tenant_id'],))
    control = DurableControl(execution, attempt(saved), saved)
    control.state = state
    original = copy.deepcopy(state.messages)
    asyncio.run(before_park(control, state))
    envelope, snapshot = control._capture(state)
    parked = execution.park(attempt(saved), control.revision, status, envelope, snapshot)
    assert parked['status'] == status and parked['checkpoint']['execution']['messages'] == original
    assert service_database.rows('SELECT receipt_id FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                 (row['runner_id'],)) == []


def test_inline_summary_receipt_fences_real_timer_activation_even_after_stale_checkpoint_save(
        storage, actors, service_database, prices):
    from src.services.agent_runner.usage_repository import UsageRepository
    row, _ = park_timer(storage, actors['a'])
    row = expire_timer(service_database, row)
    timers = TimerRepository(service_database.connect)
    acquired = timers.claim(row, 'summary-after-wake-owner', 30, prepared(row))
    stale = copy.deepcopy(acquired['checkpoint'])
    assert stale['timer_activation']['action_started'] is False
    usage = UsageRepository(service_database.connect)
    receipt = usage.start(attempt(acquired), call_id='compression-without-engine-model-fact',
                          execution_id=row['runner_id'], purpose='compression', provider='qwen', model=prices[0])
    usage.uncertain(attempt(acquired), receipt['receipt_id'])
    saved = storage[1].save_checkpoint(attempt(acquired), acquired['revision'], stale)
    assert saved['checkpoint']['timer_activation']['action_started'] is True
    assert saved['checkpoint']['execution']['model_calls'] == []
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s",
                          (row['runner_id'],))
    storage[1].reap_expired()
    interrupted = storage_row(service_database, row['runner_id'])
    assert timers.next_candidates() == []
    assert timers.claim(interrupted, 'must-not-auto-replay-summary', 30, interrupted['checkpoint']) is None
    assert service_database.rows('SELECT phase FROM agent_runner_usage_receipts WHERE runner_id=%s',
                                 (row['runner_id'],)) == [{'phase': 'unknown'}]


def test_pause_before_sleep_park_keeps_zero_business_projection_and_original_deadline_on_resume(
        storage, actors, service_database, monkeypatch):
    from src.config.settings import settings
    from src.services.agent_runner.control_contracts import RunnerControl
    from src.services.agent_runner.control_repository import ControlRepository
    from src.services.agent_runner.recovery import RecoveryCoordinator
    from src.services.agent_runner.recovery_repository import RecoveryRepository
    from src.services.agent_runner.runtime_manifest import configuration_fingerprint
    monkeypatch.setattr(settings.agent_runner, 'durable_sleep_enabled', True)
    _, execution, submit = storage
    row, principal = submit(actors['a'])
    acquired = execution.acquire('pause-before-sleep-park', 30)
    checkpoint = sleeping_checkpoint(acquired)
    checkpoint['execution'].update(role='master', profile_id='main', outcome='paused')
    checkpoint['execution']['resources']['configuration_fingerprint'] = configuration_fingerprint('fixture-profile')
    controls = ControlRepository(service_database.connect)
    controls.submit(principal, row['runner_id'], RunnerControl(client_request_id='pause-first', action='pause'))
    recovery = RecoveryRepository(service_database.connect)
    paused = recovery.acknowledge_pause(attempt(acquired), acquired['revision'], checkpoint,
                                         {'business_iterations': 1})
    assert paused['status'] == 'paused' and paused['public_snapshot']['business_iterations'] == 0
    assert paused['checkpoint']['execution']['resources']['business_iterations'] == 0
    deadline = paused['checkpoint']['durable_sleep']['wake_at']
    assert TimerRepository(service_database.connect).next_candidates() == []
    wait = paused['checkpoint']['execution']['waiting']
    command, _ = controls.submit(principal, row['runner_id'], RunnerControl(client_request_id='resume-original-sleep',
        action='resume', wait_id=wait['wait_id'], target_execution_id=wait['target_execution_id']))
    fresh = storage[0].get(row['runner_id'])
    authority = SimpleNamespace(authorize_persisted=lambda *a, **k: principal, assert_credit=lambda *a: None)
    prepared_tree = RecoveryCoordinator(authority, SimpleNamespace(resolve=lambda *a: (None, 'fixture-profile'))).prepare(
        fresh, command).checkpoint
    resumed = recovery.claim_resume(row['runner_id'], 'pause-resume-owner', 30, revision=fresh['revision'],
                                    control_id=command['control_id'], checkpoint=prepared_tree)
    assert resumed['checkpoint']['durable_sleep']['wake_at'] == deadline
    assert resumed['checkpoint']['execution']['tools']['independent-sleep-1']['result']['wake_at'] == deadline
    assert resumed['checkpoint']['execution']['resources']['business_iterations'] == 0
