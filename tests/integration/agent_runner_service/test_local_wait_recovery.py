"""Real PG recovery: retain original facts while freeing ordinary conversation."""

from datetime import datetime, timedelta, timezone
import json
import uuid

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact
from src.local_tools import repository as devices
from src.services.agent_runner.local_invocations import LocalInvocationRepository, LocalPhase
from src.services.agent_runner.local_recovery import LocalRecoveryProof, cancel_owned_invocations, cancel_completion_status
from src.services.agent_runner.local_wait import end_waiting
from src.services.agent_runner.finalizer import RunnerFinalizer
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.worker import execution_result
from .test_storage import storage, attempt

pytestmark = pytest.mark.integration


def replay_completed_receipt_as_late(database, identifier):
    """Domain return-window fixture: retain the old fee but reclassify its receipt.

    The ordinary worker test covers real lease-loss-before-receipt. Domain
    checkpoints are gated after completion; reclassification lets those real
    workers prove that their later audit/model/cursor consumers agree as well.
    """
    original = database.rows('SELECT * FROM local_tool_invocations WHERE id=%s', (identifier,))[0]
    assert original['state'] == 'succeeded'
    result = original['result_json']
    database.rows("UPDATE local_tool_invocations SET state='unknown',effect='unknown',result_json=NULL WHERE id=%s", (identifier,))
    late = devices.write_result(identifier, original['tenant_id'], original['claim_token_hash'],
        result['success'], code=result.get('code'), message=result.get('message'),
        effect=original['effect'], data=result.get('data'), device_id=str(original['device_id']))
    assert late['state'] == 'unknown' and late['credit_cost'] == original['credit_cost']


@pytest.fixture
def lost_local(storage, actors, service_database):
    repository, executions, submit = storage
    actor = actors['a']
    accepted, principal = submit(actor)
    owned = executions.acquire('old-worker', 60)
    identity = Identity(*(owned[key] for key in ('tenant_id','user_id','session_id','source','session_kind')))
    state = ExecutionState(identity, owned['runner_id'], AgentMode.MASTER, 'Fixture', [])
    call = ToolCall('original-call', 'boss_goto', {'target':'recommend'})
    state.tools[call.id] = ToolFact(call, phase='dispatching')
    state.pending = [call]
    row = executions.save_checkpoint(attempt(owned), owned['revision'], {'execution': state.checkpoint()})
    device = devices.create_device(actor.tenant_id, actor.user_id, uuid.uuid4().hex,
        machine_fingerprint_hash='fixture-machine-' + uuid.uuid4().hex)
    try:
        phase = LocalPhase(state.execution_id, call.id, 'main', 0)
        row = LocalInvocationRepository(service_database.connect).bind(attempt(row), row['revision'], phase,
            device_id=str(device['id']), tool_name=call.name, arguments=call.arguments,
            deadline_at=datetime.now(timezone.utc)+timedelta(seconds=180))
        invocation = row['checkpoint']['execution']['resources']['local_invocations'][phase.key(row['runner_id'])]['invocation_id']
        claim = uuid.uuid4().hex
        assert devices.claim_next(str(device['id']), actor.tenant_id, claim, 60,
            expected_invocation_id=invocation)
        assert devices.mark_started(invocation, actor.tenant_id, claim)['state'] == 'running'
        # Simulate process loss, never a user cancellation or a replacement call.
        service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (row['runner_id'],))
        executions.reap_expired()
        service_database.rows("UPDATE local_tool_invocations SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=%s", (invocation,))
        devices.expire_stale_claims()
        yield repository, executions, principal, repository.get(row['runner_id']), device, invocation, claim
    finally:
        for table in ('local_tool_events','local_tool_invocations','local_tool_devices'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s', (actor.tenant_id,))


def test_replacement_worker_consumes_late_original_result_without_redispatch(lost_local, service_database):
    repository, _, _, row, device, invocation, claim = lost_local
    state = ExecutionState.restore(row['checkpoint']['execution'])
    assert not LocalRecoveryProof().can_restore(row, state, state.tools['original-call'])
    assert devices.write_result(invocation, row['tenant_id'], claim, True, effect='none',
        data={'page':'jobs'}, device_id=str(device['id']))['state'] == 'unknown'
    stored = devices.get_invocation(invocation, row['tenant_id'])
    assert stored['state'] == stored['effect'] == 'unknown'
    assert stored['result_json']['_late_result']['stopped'] is True
    assert LocalRecoveryProof().can_restore(row, state, state.tools['original-call'])
    assert service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE tenant_id=%s', (row['tenant_id'],)) == [{'n':1}]
    assert repository.get(row['runner_id'])['attempt'] == row['attempt']


def test_end_wait_uses_cancel_finalizer_and_holds_gui_until_known_receipt(lost_local, actors, service_database, storage):
    repository, executions, principal, row, device, invocation, claim = lost_local
    with pytest.raises(RunnerError):
        end_waiting(repository, storage[2](actors['a_other'])[1], row['runner_id'])
    detached = end_waiting(repository, principal, row['runner_id'])
    assert detached['checkpoint']['detached_local_wait'] is True and detached['cancel_requested']
    assert end_waiting(repository, principal, row['runner_id'])['revision'] == detached['revision']
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {})
    cancellation = executions.acquire('new-worker', 60)
    assert cancellation['runner_id'] == row['runner_id'] and cancellation['cancel_only']
    facts = cancel_owned_invocations(service_database.connect, attempt(cancellation))
    assert not cancel_completion_status(facts)[0]
    assert cancel_completion_status(facts, detached_local_wait=True)[0]
    state = ExecutionState.restore(cancellation['checkpoint']['execution'])
    result = execution_result(state, 'cancelled')
    executions.stage_finalization(attempt(cancellation), cancellation['revision'],
        cancellation['checkpoint'], result, cancellation['public_snapshot'])
    finalized = RunnerFinalizer(service_database.connect).finalize(attempt(cancellation))
    assert finalized['status'] == 'cancelled'
    assert service_database.rows('SELECT 1 FROM agent_runner_session_claims WHERE owner_runner_id=%s', (row['runner_id'],)) == []
    assert devices.get_invocation(invocation, row['tenant_id'])['state'] == 'unknown'
    # Returning an abort/unknown does not prove Provider children stopped.
    devices.write_result(invocation, row['tenant_id'], claim, False, code='EXECUTION_UNKNOWN',
        effect='unknown', device_id=str(device['id']))
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {})


def test_known_late_result_releases_same_machine_hold_and_new_login_attempt_can_choose_device(lost_local, service_database):
    repository, _, principal, row, device, invocation, claim = lost_local
    end_waiting(repository, principal, row['runner_id'])
    original = service_database.rows('SELECT machine_fingerprint_hash FROM local_tool_devices WHERE id=%s', (device['id'],))[0]
    another = devices.create_device(row['tenant_id'], row['user_id'], uuid.uuid4().hex,
        machine_fingerprint_hash=original['machine_fingerprint_hash'])
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'], row['user_id'], str(another['id']), 'boss_goto', {})
    devices.write_result(invocation, row['tenant_id'], claim, True, effect='none', device_id=str(device['id']))
    fresh = devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {}, row['session_id'])
    assert fresh != invocation
    assert devices.claim_next(str(device['id']), row['tenant_id'], 'new-claim', 60, expected_invocation_id=fresh)
    devices.write_result(fresh, row['tenant_id'], 'new-claim', False, code='NOT_LOGGED_IN', effect='none')
    assert devices.create_invocation(row['tenant_id'], row['user_id'], str(another['id']), 'boss_goto', {}, row['session_id'])
    assert devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {}, row['session_id'])


@pytest.mark.parametrize('capable', [True, False])
def test_runtime_completion_evidence_releases_hold_only_with_declared_capability(
        lost_local, service_database, capable):
    """契约 2.20 候选「正常结束的事实」真实 PG：租约过期 unknown 无回执保持占用，
    带证据迟到回执仅在设备声明 runtime_boss_completion_v1 时解除；原 unknown
    事实（state/effect/error_code）与业务 data 零改写，state 仍不自动重发。"""
    repository, _, principal, row, device, invocation, claim = lost_local
    end_waiting(repository, principal, row['runner_id'])
    # 租约过期 → unknown 无回执：completion_known 仍 false，占用保持（证据不误释放）
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']), 'boss_goto', {})
    devices.touch_device_seen(str(device['id']), capabilities={'capabilities': ['local_v2'] +
        (['runtime_boss_completion_v1'] if capable else [])})
    written = devices.write_result(invocation, row['tenant_id'], claim, True, effect='unknown',
        data={'filter': 1, '_runtime_completion': {'version': 1}}, device_id=str(device['id']))
    assert written['state'] == written['effect'] == 'unknown', '原 unknown 事实零改写'
    late = written['result_json']['_late_result']
    assert late['stopped'] is capable and late['success'] is True
    expected_data = {'filter': 1, '_runtime_completion': {'version': 1}} if capable else {'filter': 1}
    assert late['data'] == expected_data
    if capable:
        fresh = devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']),
            'boss_goto', {}, row['session_id'])
        assert fresh != invocation, '证据解除占用后新调用放行'
    else:
        with pytest.raises(devices.DeviceOperationUnresolved):
            devices.create_invocation(row['tenant_id'], row['user_id'], str(device['id']),
                'boss_goto', {})
        assert written['effect'] == 'unknown', '效果未知语义不动（不自动重发）'


@pytest.mark.parametrize('capable', [True, False])
def test_r8_settled_unknown_effect_restores_but_evidenceless_unknown_stays_verification(
        lost_local, service_database, capable):
    """R8 修复1（契约「停止与接续」）：迟到可信回执 effect=unknown 且带
    _runtime_completion 证据 → 完成判定成立，恢复检查放行接续（未知效果保留为
    事实，不重发：invocation 数不增）；无证据 unknown（真未知）保持现状卡核验，
    租约过期无回执保持占用。"""
    repository, _, principal, row, device, invocation, claim = lost_local
    state = ExecutionState.restore(row['checkpoint']['execution'])
    assert not LocalRecoveryProof().can_restore(row, state, state.tools['original-call'])
    devices.touch_device_seen(str(device['id']), capabilities={'capabilities':
        ['runtime_boss_completion_v1'] if capable else ['local_v2']})
    devices.write_result(invocation, row['tenant_id'], claim, True, effect='unknown',
        data={'page': 'jobs', '_runtime_completion': {'version': 1}} if capable else {'page': 'jobs'},
        device_id=str(device['id']))
    stored = devices.get_invocation(invocation, row['tenant_id'])
    assert stored['state'] == stored['effect'] == 'unknown', '原 unknown 事实零改写'
    assert stored['result_json']['_late_result']['stopped'] is capable
    assert LocalRecoveryProof().can_restore(row, state, state.tools['original-call']) is capable
    assert service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE tenant_id=%s',
        (row['tenant_id'],)) == [{'n': 1}], '停稳接续不产生新派发（效果未知不重放）'
    if capable:
        # recovery preflight 全链路：resume 控制的 prepare 不再 RECOVERY_TOOL_VERIFICATION_REQUIRED，
        # 原调用保持 pending 等待由 worker 重新绑定原 invocation（未知结果作为事实交回）。
        from types import SimpleNamespace
        from src.services.agent_runner.recovery import RecoveryCoordinator
        from src.services.agent_runner.runtime_manifest import configuration_fingerprint
        service_database.rows("""UPDATE agent_runners SET profile_fingerprint='fixture-profile',
            checkpoint=jsonb_set(jsonb_set(checkpoint,'{execution,resources,configuration_fingerprint}',%s::jsonb),
                '{execution,profile_id}','"main"')
            WHERE runner_id=%s""", (json.dumps(configuration_fingerprint('fixture-profile')), row['runner_id']))
        row = repository.get(row['runner_id'])
        restored = ExecutionState.restore(row['checkpoint']['execution'])
        assert LocalRecoveryProof().can_restore(row, restored, restored.tools['original-call'])
        authority = SimpleNamespace(authorize_persisted=lambda *a, **k: principal,
            assert_credit=lambda *a: None, authorize_child_profile=lambda *a: None)
        control = {'action': 'resume', 'control_id': 'r8-resume-control', 'payload': {},
                   'accepted_at': datetime.now(timezone.utc)}
        prepared = RecoveryCoordinator(authority, SimpleNamespace(
            resolve=lambda *a: (None, 'fixture-profile')),
            tool_recovery=LocalRecoveryProof()).prepare(row, control)
        tree = ExecutionState.restore(prepared.checkpoint['execution'])
        assert [call.id for call in tree.pending] == ['original-call']
        assert restored.tools['original-call'].phase == 'dispatching'


@pytest.mark.parametrize('late', [False, True])
def test_next_none_login_failure_allows_new_attempt_on_original_device(lost_local, service_database, late):
    _, _, _, row, device, original_id, original_claim = lost_local
    tenant, user, session, device_id = row['tenant_id'], row['user_id'], row['session_id'], str(device['id'])
    devices.write_result(original_id, tenant, original_claim, True, effect='none')
    parent = devices.create_invocation(tenant, user, device_id, 'boss_resume_batch', {'limit':1}, session)
    assert devices.claim_next(device_id, tenant, 'parent-claim', 60, expected_invocation_id=parent)
    devices.write_result(parent, tenant, 'parent-claim', True, effect='none')
    arguments = {'continue_from':parent, 'limit':3}
    failed = devices.create_invocation(tenant, user, device_id, 'boss_resume_next', arguments, session)
    assert devices.claim_next(device_id, tenant, 'failed-claim', 60, expected_invocation_id=failed)
    if late:
        service_database.rows("UPDATE local_tool_invocations SET state='unknown',effect='unknown',result_json=NULL WHERE id=%s", (failed,))
    devices.write_result(failed, tenant, 'failed-claim', False, code='NOT_LOGGED_IN', effect='none')
    retried = devices.create_invocation(tenant, user, device_id, 'boss_resume_next', arguments, session)
    assert retried != failed
    # An unresolved successor still excludes another fork, even after safe login failures.
    with pytest.raises(devices.ResumeContinuationConflict):
        devices.create_invocation(tenant, user, device_id, 'boss_resume_next', arguments, session)
