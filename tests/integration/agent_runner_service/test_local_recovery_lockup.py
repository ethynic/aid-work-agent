"""Real binding transactions and legacy no-invocation proof, without desktop IO."""
import copy
import hashlib
import json
from contextlib import contextmanager
import psycopg2
from uuid import uuid4

import pytest
from psycopg2.extras import Json

from src.core.agent_engine.contracts import ExecutionState, ToolCall, ToolFact
from src.local_tools import repository as local
from src.services.agent_runner.contracts import canonical_json, Principal
from src.services.agent_runner.local_invocations import LocalDispatchRejected, LocalInvocationRepository, LocalPhase
from src.services.agent_runner.local_recovery import LocalRecoveryProof
from src.services.agent_runner.local_wait import end_waiting
from src.services.agent_runner.unbound_local import resolve_next_in_tx

from .test_local_phase_storage import local_store, recruiting_schema
from .test_storage import storage, attempt

pytestmark = pytest.mark.integration


def seed_next(local_store, database):
    repo, execution, prepare, devices = local_store
    row, _ = prepare()
    parent = str(uuid4())
    result = {'success': True, 'data': {'job_name': 'Fixture job', 'stop_reason': 'limit_reached',
        'processed_names': ['already stored'], 'resumes': [{'candidate_name': 'already stored'}]}}
    database.rows('''INSERT INTO local_tool_invocations(id,tenant_id,user_id,session_id,device_id,
        tool_name,arguments_json,state,effect,result_json) VALUES(%s,%s,%s,%s,%s,
        'boss_resume_batch','{}','succeeded','none',%s)''',
        (parent, row['tenant_id'], row['user_id'], row['session_id'], devices['a'], Json(result)))
    cp = copy.deepcopy(row['checkpoint'])
    node = cp['execution']
    call = ToolCall('local-call', 'boss_resume_next', {'continue_from': parent, 'limit': 3})
    node['tools'][call.id] = ToolFact(call, phase='dispatching').__dict__
    # Use the public state codec, not a mock can_restore or a fabricated dispatch proof.
    state = ExecutionState.restore(row['checkpoint']['execution'])
    state.tools[call.id] = ToolFact(call, phase='dispatching')
    state.pending = [call]
    phase = LocalPhase(state.execution_id, call.id, 'resume.next.request', 0)
    canonical = {'continue_from': parent, 'limit': 3, 'exclude_names': ['already stored'],
        'expected_job_name': 'Fixture job', '_resume_expected_device_id': devices['a']}
    state.resources['local_domain_phases'] = {phase.key(row['runner_id']): {
        'runner_id': row['runner_id'], 'execution_id': state.execution_id, 'tool_call_id': call.id,
        'branch': phase.branch, 'ordinal': 0, 'phase': 'completed', 'request': call.arguments,
        'intent_digest': hashlib.sha256(canonical_json(call.arguments).encode()).hexdigest(), 'result': canonical}}
    cp['execution'] = state.checkpoint()
    row = execution.save_checkpoint(attempt(row), row['revision'], cp)
    main = LocalPhase(state.execution_id, call.id, 'main', 0)
    return repo, execution, row, main, canonical


def bind_next(store, row, phase, canonical):
    return store.bind(attempt(row), row['revision'], phase,
        device_id=canonical['_resume_expected_device_id'], tool_name='boss_resume_next',
        arguments={key: value for key, value in canonical.items() if not key.startswith('_')})


def test_stale_rejection_rolls_back_binding_without_poisoning_the_saved_checkpoint(local_store, service_database):
    repo, execution, row, phase, canonical = seed_next(local_store, service_database)
    args = {key: value for key, value in canonical.items() if not key.startswith('_')}
    service_database.rows("UPDATE local_tool_devices SET selected=TRUE,last_seen_at=NOW(),capabilities_json=%s WHERE id=%s",
        (Json({'provider_id': 'ai.aidwork.boss-recruiting'}), canonical['_resume_expected_device_id']))
    service_database.rows('''INSERT INTO local_tool_invocations(tenant_id,user_id,session_id,device_id,
        tool_name,arguments_json,state) VALUES(%s,%s,%s,%s,'boss_resume_next',%s,'queued')''',
        (row['tenant_id'], row['user_id'], row['session_id'], canonical['_resume_expected_device_id'], Json(args)))
    with pytest.raises(LocalDispatchRejected) as rejected:
        bind_next(LocalInvocationRepository(service_database.connect), row, phase, canonical)
    assert isinstance(rejected.value.reason, local.ResumeContinuationConflict)
    assert repo.get(row['runner_id'])['checkpoint'] == row['checkpoint']
    assert len(service_database.rows('SELECT id FROM local_tool_invocations WHERE session_id=%s', (row['session_id'],))) == 2


@pytest.mark.asyncio
async def test_owner_converges_stale_as_completed_business_failure_and_usage_scope_stays_usable(local_store, service_database):
    from src.services.agent_runner.durable_control import DurableControl
    from src.services.agent_runner.local_owner import RunnerLocalLifecycle
    from src.services.agent_runner.usage_context import UsageScope
    repo, execution, row, phase, canonical = seed_next(local_store, service_database)
    args = {key: value for key, value in canonical.items() if not key.startswith('_')}
    service_database.rows("UPDATE local_tool_devices SET selected=TRUE,last_seen_at=NOW(),capabilities_json=%s WHERE id=%s",
        (Json({'provider_id': 'ai.aidwork.boss-recruiting'}), canonical['_resume_expected_device_id']))
    service_database.rows('''INSERT INTO local_tool_invocations(tenant_id,user_id,session_id,device_id,
        tool_name,arguments_json,state) VALUES(%s,%s,%s,%s,'boss_resume_next',%s,'queued')''',
        (row['tenant_id'], row['user_id'], row['session_id'], canonical['_resume_expected_device_id'], Json(args)))
    control = DurableControl(execution, attempt(row), row)
    control.usage_scope = UsageScope(attempt(row), None, row['runner_id'])
    state = ExecutionState.restore(row['checkpoint']['execution'])
    owner = RunnerLocalLifecycle(control, state, phase.tool_call_id,
        invocations=LocalInvocationRepository(service_database.connect))
    with pytest.raises(local.ResumeContinuationConflict):
        await owner.bind_invocation(branch='main', ordinal=0, tool_name='boss_resume_next',
            device={'id': canonical['_resume_expected_device_id']}, arguments=args)
    control.usage_scope.check()
    saved = repo.get(row['runner_id'])['checkpoint']['execution']['tools'][phase.tool_call_id]
    assert saved['phase'] == 'completed' and saved['result']['code'] == 'STALE_CONTINUATION'
    assert saved['result']['effect'] == 'none' and saved['result']['invocation_id'] is None


def test_real_legacy_dispatching_without_invocation_can_resume_or_end_without_device_hold(local_store, service_database):
    repo, execution, row, phase, _ = seed_next(local_store, service_database)
    parked = execution.interrupt_attempt(attempt(row))
    state = ExecutionState.restore(parked['checkpoint']['execution'])
    assert LocalRecoveryProof(service_database.connect).can_restore(parked, state, state.tools[phase.tool_call_id])
    fact = state.tools[phase.tool_call_id]
    assert fact.phase == 'completed' and fact.result['code'] == 'LOCAL_NOT_DISPATCHED'
    assert state.resources.get('local_invocations', {}) == {}
    principal = Principal(state.identity, 'user', row['user_id'], 'storage-test')
    ended = end_waiting(repo, principal, row['runner_id'])
    assert ended['cancel_requested'] and ended['checkpoint']['ended_undispatched_wait']
    assert 'detached_local_invocations' not in ended['checkpoint']
    assert ended['checkpoint']['execution']['tools'][phase.tool_call_id]['phase'] == 'completed'


def test_missing_checkpoint_reference_with_committed_binding_adopts_original_id_only(local_store, service_database):
    repo, execution, row, phase, canonical = seed_next(local_store, service_database)
    saved = bind_next(LocalInvocationRepository(service_database.connect), row, phase, canonical)
    cp = copy.deepcopy(saved['checkpoint'])
    original = cp['execution']['resources'].pop('local_invocations')[phase.key(row['runner_id'])]['invocation_id']
    torn = execution.save_checkpoint(attempt(saved), saved['revision'], cp)
    with service_database.connect() as connection:
        from src.services.agent_runner.ownership import lock_runner
        cursor = connection.cursor()
        locked = lock_runner(cursor, row['runner_id'])
        node = locked['checkpoint']['execution']
        assert resolve_next_in_tx(cursor, locked, node, phase.tool_call_id)
        assert node['resources']['local_invocations'][phase.key(row['runner_id'])]['invocation_id'] == original
        assert node['tools'][phase.tool_call_id]['phase'] == 'dispatching'
    assert len(service_database.rows('SELECT id FROM local_tool_invocations WHERE session_id=%s', (row['session_id'],))) == 2


def test_zero_readonly_unknown_can_advance_but_unreported_or_external_writes_cannot():
    item = {'tool_name': 'boss_resume_next', 'state': 'unknown', 'effect': 'none',
        'result_json': {'success': False, 'code': 'EXECUTION_UNKNOWN', 'data': {
            'resumes': [], 'attempted': 0, 'stop_reason': 'page_operation_failed',
            'operation_recovery': {'operation_class': 'readonly', 'operation': {'name': 'boss_resume_next'}}}}}
    assert local.continuation_retry_safe(item)
    for change in ({'tool_name': 'boss_greet'}, {'effect': 'unknown'}, {'result_json': None}):
        assert not local.continuation_retry_safe({**item, **change})


def test_rollback_failure_is_not_a_no_dispatch_proof(local_store, service_database):
    _, _, row, phase, canonical = seed_next(local_store, service_database)
    args = {key: value for key, value in canonical.items() if not key.startswith('_')}
    service_database.rows('''INSERT INTO local_tool_invocations(tenant_id,user_id,session_id,device_id,
        tool_name,arguments_json,state) VALUES(%s,%s,%s,%s,'boss_resume_next',%s,'queued')''',
        (row['tenant_id'], row['user_id'], row['session_id'], canonical['_resume_expected_device_id'], Json(args)))
    class FailedRollback:
        def __init__(self, connection): self.connection = connection
        def cursor(self): return self.connection.cursor()
        def commit(self): return self.connection.commit()
        def rollback(self): raise psycopg2.OperationalError('fictional rollback connection lost')
    @contextmanager
    def connection():
        with service_database.connect() as actual:
            yield FailedRollback(actual)
    with pytest.raises(psycopg2.OperationalError):
        bind_next(LocalInvocationRepository(connection), row, phase, canonical)
