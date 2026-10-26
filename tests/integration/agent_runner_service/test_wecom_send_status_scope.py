"""Real isolated PostgreSQL admission. No WeCom, UI, clipboard, network send or business DB."""
import copy
import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact
from src.local_tools import repository as devices
from src.local_tools import api
from src.local_tools.security import sha256_hex
from src.local_tools import wecom_send_status as status_scope
from src.local_tools.proxy_tool import WecomSendStatusTool
from src.local_tools.overlay_flow import poll_operation
from src.services.agent_runner.local_invocations import LocalInvocationRepository, LocalPhase
from src.services.agent_runner.local_recovery import LocalRecoveryProof, cancel_owned_invocations
from src.services.agent_runner.finalizer import RunnerFinalizer
from src.services.agent_runner.worker import execution_result
from src.services.agent_runner.local_wait import end_waiting
from .test_storage import storage, attempt

pytestmark = pytest.mark.integration
RECEIPT = 'send-2026-10-26T01-02-03-004Z'


@pytest.fixture
def send_scope(storage, actors, service_database, request):
    repository, executions, submit = storage
    actor = actors['a']
    accepted, principal = submit(actor)
    owned = executions.acquire('send-scope-fixture', 60)
    state = ExecutionState(Identity(actor.tenant_id, actor.user_id, actor.session_id),
        owned['runner_id'], AgentMode.MASTER, '', [])
    call = ToolCall('original-send', 'wecom_message_send', {'target_name': 'Anonymous group', 'text': 'Anonymous message'})
    state.tools[call.id] = ToolFact(call, phase='dispatching')
    state.pending = [call]
    row = executions.save_checkpoint(attempt(owned), owned['revision'], {'execution': state.checkpoint()})
    device = devices.create_device(actor.tenant_id, actor.user_id, uuid.uuid4().hex,
        machine_fingerprint_hash='send-status-fixture-' + uuid.uuid4().hex)
    try:
        phase = LocalPhase(state.execution_id, call.id, 'main', 0)
        row = LocalInvocationRepository(service_database.connect).bind(attempt(row), row['revision'], phase,
            device_id=str(device['id']), tool_name=call.name, arguments=call.arguments, provider_key='wecom')
        original = row['checkpoint']['execution']['resources']['local_invocations'][phase.key(row['runner_id'])]['invocation_id']
        claim = uuid.uuid4().hex
        assert devices.claim_next(str(device['id']), actor.tenant_id, claim, 60, ['wecom'], expected_invocation_id=original)
        assert devices.mark_started(original, actor.tenant_id, claim)
        if getattr(request, 'param', False):
            service_database.rows("UPDATE local_tool_invocations SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=%s", (original,))
            devices.expire_stale_claims()
        devices.write_result(original, actor.tenant_id, claim, False, code='EXECUTION_UNKNOWN', effect='unknown',
            data={'receipt_id': RECEIPT}, device_id=str(device['id']))
        service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (row['runner_id'],))
        executions.reap_expired()
        row = end_waiting(repository, principal, row['runner_id'])
        assert row['checkpoint']['detached_local_invocations'] == [original]
        yield dict(repository=repository, executions=executions, actor=actor, principal=principal,
            row=row, device=str(device['id']), original=original, claim=claim)
    finally:
        for table in ('local_tool_events', 'local_tool_invocations', 'local_tool_devices'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s', (actor.tenant_id,))


def enqueue(source, **overrides):
    args = dict(tenant_id=source['actor'].tenant_id, user_id=source['actor'].user_id,
        device_id=source['device'], tool_name='wecom_send_status', arguments={'receipt_id': RECEIPT},
        session_id=source['actor'].session_id, provider_key='wecom', business_kind='wecom_send_status',
        business_ref={'version': 1, 'send_invocation_id': source['original']})
    args.update(overrides)
    return devices.create_invocation(**args)


@pytest.mark.parametrize('send_scope', [False, True], indirect=True)
def test_original_and_late_receipts_allow_only_separate_read_without_releasing_hold(send_scope, service_database):
    source = send_scope
    before = copy.deepcopy(devices.get_invocation(source['original'], source['actor'].tenant_id))
    checkpoint = source['repository'].get(source['row']['runner_id'])['checkpoint']
    status = enqueue(source)
    claim = uuid.uuid4().hex
    assert devices.claim_next(source['device'], source['actor'].tenant_id, claim, 60, ['wecom'], expected_invocation_id=status)
    assert devices.mark_started(status, source['actor'].tenant_id, claim)['state'] == 'running'
    result = devices.write_result(status, source['actor'].tenant_id, claim, True, effect='none',
        data={'receipt_id': RECEIPT, 'send_status': 'unknown'}, device_id=source['device'])
    assert result['state'] == 'succeeded' and result['credit_cost'] == 0
    assert devices.get_invocation(source['original'], source['actor'].tenant_id) == before
    assert source['repository'].get(source['row']['runner_id'])['checkpoint'] == checkpoint
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(source['actor'].tenant_id, source['actor'].user_id, source['device'],
            'wecom_message_send', {'target_name': 'Anonymous group', 'text': 'Never send'}, source['actor'].session_id, provider_key='wecom')


@pytest.mark.parametrize('stage', ['enqueue', 'claim', 'started'])
@pytest.mark.parametrize('invalid', ['none', 'invalidation_failed'])
def test_source_is_revalidated_before_each_physical_dispatch_stage(send_scope, service_database, stage, invalid):
    source = send_scope
    tenant = source['actor'].tenant_id
    status = enqueue(source) if stage != 'enqueue' else None
    claim = uuid.uuid4().hex
    if stage == 'started':
        assert devices.claim_next(source['device'], tenant, sha256_hex(claim), 60, ['wecom'], expected_invocation_id=status)
    if invalid == 'none':
        service_database.rows("UPDATE local_tool_invocations SET effect='none' WHERE id=%s", (source['original'],))
    else:
        service_database.rows("UPDATE local_tool_invocations SET result_json=jsonb_set(result_json,'{data,receipt_invalidation_failed}','true') WHERE id=%s", (source['original'],))
    if stage == 'enqueue':
        with pytest.raises(devices.DeviceOperationUnresolved): enqueue(source)
    else:
        if stage == 'claim':
            assert devices.claim_next(source['device'], tenant, claim, 60, ['wecom'], expected_invocation_id=status) is None
        else:
            app = FastAPI()
            app.include_router(api.router)
            app.dependency_overrides[api._require_device] = lambda: {'id': source['device'], 'tenant_id': tenant}
            with TestClient(app) as client:
                response = client.post(f'/api/local-tools/runtime/invocations/{status}/started', json={'claim_token': claim})
            assert response.status_code == 404, 'a denied started acknowledgement cannot grant any provider execution'
    if status:
        terminal = devices.get_invocation(status, tenant)
        assert (terminal['state'], terminal['effect'], terminal['error_code']) == ('failed', 'none', 'WECOM_SEND_SCOPE_INVALID')
        assert terminal['started_at'] is None and terminal['finished_at'] is not None
        assert terminal['result_json']['retryable'] is False
        assert devices.list_events(status)[-1]['stage'] == 'failed'


@pytest.mark.parametrize('mismatch', ['tenant', 'user', 'session', 'device', 'receipt', 'tool', 'marker'])
def test_model_ids_or_wrong_scope_never_authorize_an_unrelated_read(send_scope, actors, mismatch):
    source = send_scope
    changes = {
        'tenant': {'tenant_id': actors['b'].tenant_id},
        'user': {'user_id': actors['a_other'].user_id},
        'session': {'session_id': actors['a_other'].session_id},
        'receipt': {'arguments': {'receipt_id': RECEIPT + '-2'}},
        'tool': {'tool_name': 'wecom_message_send'},
        'marker': {'business_kind': None, 'business_ref': None},
    }
    if mismatch == 'device':
        another = devices.create_device(source['actor'].tenant_id, source['actor'].user_id, uuid.uuid4().hex)
        changes['device'] = {'device_id': str(another['id'])}
    with pytest.raises(devices.DeviceOperationUnresolved):
        enqueue(source, **changes[mismatch])


def test_another_retained_unknown_on_same_device_still_blocks_this_narrow_read(send_scope, service_database):
    source = send_scope
    queued = enqueue(source)
    other = str(uuid.uuid4())
    row = source['repository'].get(source['row']['runner_id'])
    checkpoint = copy.deepcopy(row['checkpoint'])
    state = ExecutionState.restore(checkpoint['execution'])
    call = ToolCall('parallel-unknown', 'wecom_message_send', {'target_name': 'Other anonymous group', 'text': 'Another original intent'})
    state.tools[call.id] = ToolFact(call, phase='waiting', invocation_id=other)
    phase = LocalPhase(state.execution_id, call.id, 'main', 0)
    binding = dict(runner_id=row['runner_id'], execution_id=state.execution_id,
        tool_call_id=call.id, branch='main', ordinal=0)
    state.resources['local_invocations'][phase.key(row['runner_id'])] = dict(binding, invocation_id=other,
        request=dict(device_id=source['device'], tool_name=call.name, arguments=call.arguments,
            provider_key='wecom', deadline_at=None, authorization_epoch=None, execution_lane='standard'))
    checkpoint['execution'] = state.checkpoint()
    checkpoint['detached_local_invocations'].append(other)
    service_database.rows('''INSERT INTO local_tool_invocations
        (id,tenant_id,user_id,session_id,device_id,tool_name,arguments_json,provider_key,business_kind,business_ref,state,effect)
        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,'wecom','agent_runner',%s::jsonb,'unknown','unknown')''',
        (other, source['actor'].tenant_id, source['actor'].user_id, source['actor'].session_id,
         source['device'], call.name, json.dumps(call.arguments), json.dumps(binding)))
    service_database.rows('UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s',
        (json.dumps(checkpoint), row['runner_id']))
    with pytest.raises(devices.DeviceOperationUnresolved):
        enqueue(source)
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.claim_next(source['device'], source['actor'].tenant_id, uuid.uuid4().hex, 60, ['wecom'])
    assert devices.get_invocation(queued, source['actor'].tenant_id)['state'] == 'queued'
    assert source['repository'].get(row['runner_id'])['checkpoint'] == checkpoint


@pytest.mark.parametrize('invalid', ['none', 'invalidation_failed'])
def test_ordinary_claim_retires_invalid_status_head_then_claims_the_next_valid_read(actors, service_database, invalid):
    actor = actors['a']
    device = str(devices.create_device(actor.tenant_id, actor.user_id, uuid.uuid4().hex)['id'])
    try:
        sources = []
        for index in range(2):
            identifier = devices.create_invocation(actor.tenant_id, actor.user_id, device,
                'wecom_message_send', {'target_name': 'Anonymous group', 'text': 'Original intent'},
                actor.session_id, provider_key='wecom')
            claim = uuid.uuid4().hex
            assert devices.claim_next(device, actor.tenant_id, claim, 60, ['wecom'], expected_invocation_id=identifier)
            assert devices.mark_started(identifier, actor.tenant_id, claim)
            receipt = RECEIPT if index == 0 else RECEIPT + '-2'
            devices.write_result(identifier, actor.tenant_id, claim, True, effect='applied', data={'receipt_id': receipt})
            sources.append((identifier, receipt))
        statuses = [enqueue(dict(actor=actor, device=device, original=identifier), arguments={'receipt_id': receipt})
                    for identifier, receipt in sources]
        service_database.rows("UPDATE local_tool_invocations SET created_at=NOW()-INTERVAL '1 minute' WHERE id=%s", (statuses[0],))
        if invalid == 'none':
            service_database.rows("UPDATE local_tool_invocations SET effect='none' WHERE id=%s", (sources[0][0],))
        else:
            service_database.rows("UPDATE local_tool_invocations SET result_json=jsonb_set(result_json,'{data,receipt_invalidation_failed}','true') WHERE id=%s", (sources[0][0],))
        originals = [devices.get_invocation(identifier, actor.tenant_id) for identifier, _ in sources]
        assert devices.claim_next(device, actor.tenant_id, uuid.uuid4().hex, 60, ['wecom']) is None
        rejected = devices.get_invocation(statuses[0], actor.tenant_id)
        assert (rejected['state'], rejected['effect'], rejected['error_code']) == ('failed', 'none', 'WECOM_SEND_SCOPE_INVALID')
        assert devices.list_events(statuses[0])[-1]['stage'] == 'failed'
        actual = devices.claim_next(device, actor.tenant_id, uuid.uuid4().hex, 60, ['wecom'])
        assert str(actual['id']) == statuses[1]
        assert [devices.get_invocation(identifier, actor.tenant_id) for identifier, _ in sources] == originals
    finally:
        for table in ('local_tool_events', 'local_tool_invocations', 'local_tool_devices'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s', (actor.tenant_id,))


def _bind_new_durable_status(source, storage, service_database):
    repository, executions, submit = storage
    # Finish only the old cloud wait; the original unknown and its device hold remain.
    cancellation = executions.acquire('finish-old-wait', 60)
    assert cancellation['runner_id'] == source['row']['runner_id'] and cancellation['cancel_only']
    cancel_owned_invocations(service_database.connect, attempt(cancellation))
    old_state = ExecutionState.restore(cancellation['checkpoint']['execution'])
    executions.stage_finalization(attempt(cancellation), cancellation['revision'], cancellation['checkpoint'],
        execution_result(old_state, 'cancelled'), cancellation['public_snapshot'])
    RunnerFinalizer(service_database.connect).finalize(attempt(cancellation))
    before = devices.get_invocation(source['original'], source['actor'].tenant_id)
    accepted, principal = submit(source['actor'])
    owned = executions.acquire('read-only-check', 60)
    assert owned['runner_id'] == accepted['runner_id']
    state = ExecutionState(Identity(source['actor'].tenant_id, source['actor'].user_id, source['actor'].session_id),
        owned['runner_id'], AgentMode.MASTER, '', [])
    call = ToolCall('status-call', 'wecom_send_status', {'send_invocation_id': source['original']})
    state.tools[call.id] = ToolFact(call, phase='dispatching')
    state.pending = [call]
    row = executions.save_checkpoint(attempt(owned), owned['revision'], {'execution': state.checkpoint()})
    phase = LocalPhase(state.execution_id, call.id, 'main', 0)
    bound = LocalInvocationRepository(service_database.connect).bind(attempt(row), row['revision'], phase,
        device_id=source['device'], tool_name=call.name, arguments={'receipt_id': RECEIPT}, provider_key='wecom')
    status = bound['checkpoint']['execution']['resources']['local_invocations'][phase.key(bound['runner_id'])]['invocation_id']
    return repository, bound, principal, call, status, before


def test_new_durable_status_binds_native_args_and_restart_reuses_its_exact_phase(send_scope, storage, service_database):
    source = send_scope
    repository, bound, principal, call, status, before = _bind_new_durable_status(source, storage, service_database)
    claim = uuid.uuid4().hex
    assert devices.claim_next(source['device'], source['actor'].tenant_id, claim, 60, ['wecom'], expected_invocation_id=status)
    assert devices.mark_started(status, source['actor'].tenant_id, claim)
    devices.write_result(status, source['actor'].tenant_id, claim, True, effect='none',
        data={'receipt_id': RECEIPT, 'send_status': 'unknown'})
    restored = ExecutionState.restore(bound['checkpoint']['execution'])
    count = service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE tenant_id=%s', (source['actor'].tenant_id,))
    assert LocalRecoveryProof(service_database.connect).can_restore(bound, restored, restored.tools[call.id])
    assert service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE tenant_id=%s', (source['actor'].tenant_id,)) == count
    repository.cancel(principal, bound['runner_id'])
    cancel_owned_invocations(service_database.connect, attempt(bound))
    assert devices.get_invocation(status, source['actor'].tenant_id)['state'] == 'succeeded'
    assert devices.get_invocation(source['original'], source['actor'].tenant_id) == before


@pytest.mark.parametrize('point', ['claim', 'started'])
def test_rejected_status_restart_consumes_private_failure_without_source_io_or_new_invocation(send_scope, storage, service_database, point):
    source = send_scope
    repository, bound, principal, call, status, _ = _bind_new_durable_status(source, storage, service_database)
    tenant = source['actor'].tenant_id
    claim = uuid.uuid4().hex
    if point == 'started':
        assert devices.claim_next(source['device'], tenant, claim, 60, ['wecom'])
    service_database.rows("UPDATE local_tool_invocations SET result_json=jsonb_set(result_json,'{data,receipt_invalidation_failed}','true') WHERE id=%s", (source['original'],))
    original = devices.get_invocation(source['original'], tenant)
    held = source['repository'].get(source['row']['runner_id'])['checkpoint']
    if point == 'claim':
        assert devices.claim_next(source['device'], tenant, claim, 60, ['wecom']) is None
    else:
        assert devices.mark_started(status, tenant, claim) is None
    rejected = devices.get_invocation(status, tenant)
    assert rejected['result_json'][status_scope.NO_DISPATCH]['invocation_id'] == status
    restored = ExecutionState.restore(bound['checkpoint']['execution'])
    count = service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE tenant_id=%s', (tenant,))
    with patch.object(status_scope, 'original_scope', side_effect=AssertionError('no invalid source re-query')):
        assert LocalRecoveryProof(service_database.connect).can_restore(bound, restored, restored.tools[call.id])
    ref = next(iter(restored.resources['local_invocations'].values()))
    operation = SimpleNamespace(owner=SimpleNamespace(saved_phase=AsyncMock(return_value={
        'request': ref['request'], 'invocation': rejected})), branch='main', ordinal=0)
    tool = WecomSendStatusTool()
    with patch.object(tool, 'native_arguments', AsyncMock(side_effect=AssertionError('no source query'))), \
         patch.object(tool, '_dispatch_and_wait', AsyncMock(side_effect=AssertionError('no new invocation'))):
        result = asyncio.run(poll_operation(tool, operation, call.arguments, tenant_id=tenant,
            user_id=source['actor'].user_id, session_id=source['actor'].session_id, progress_queue=None))
    assert result['code'] == 'WECOM_SEND_SCOPE_INVALID' and result['effect'] == 'none'
    assert service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE tenant_id=%s', (tenant,)) == count
    assert devices.get_invocation(source['original'], tenant) == original
    assert source['repository'].get(source['row']['runner_id'])['checkpoint'] == held


def test_end_waiting_keeps_real_unknown_but_excludes_proven_undispatched_status(send_scope, storage, service_database):
    source = send_scope
    repository, bound, principal, _, rejected_id, original = _bind_new_durable_status(source, storage, service_database)
    tenant = source['actor'].tenant_id
    service_database.rows("UPDATE local_tool_invocations SET result_json=jsonb_set(result_json,'{data,receipt_invalidation_failed}','true') WHERE id=%s", (source['original'],))
    assert devices.claim_next(source['device'], tenant, uuid.uuid4().hex, 60, ['wecom']) is None
    rejected = devices.get_invocation(rejected_id, tenant)
    assert devices.operation_completion_known(rejected)
    # Fixture restores the source to seed a separate actual dispatch in the same Runner.
    service_database.rows("UPDATE local_tool_invocations SET result_json=result_json #- '{data,receipt_invalidation_failed}' WHERE id=%s", (source['original'],))
    state = ExecutionState.restore(bound['checkpoint']['execution'])
    second = ToolCall('second-actual-read', 'wecom_send_status', {'send_invocation_id': source['original']})
    state.tools[second.id] = ToolFact(second, phase='dispatching')
    state.pending = [second]
    row = storage[1].save_checkpoint(attempt(bound), bound['revision'], {'execution': state.checkpoint()})
    phase = LocalPhase(state.execution_id, second.id, 'main', 0)
    row = LocalInvocationRepository(service_database.connect).bind(attempt(row), row['revision'], phase,
        device_id=source['device'], tool_name=second.name, arguments={'receipt_id': RECEIPT}, provider_key='wecom')
    actual = row['checkpoint']['execution']['resources']['local_invocations'][phase.key(row['runner_id'])]['invocation_id']
    claim = uuid.uuid4().hex
    assert devices.claim_next(source['device'], tenant, claim, 60, ['wecom'], expected_invocation_id=actual)
    assert devices.mark_started(actual, tenant, claim)
    service_database.rows("UPDATE local_tool_invocations SET lease_expires_at=NOW()-INTERVAL '1 second' WHERE id=%s", (actual,))
    devices.expire_stale_claims()
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-INTERVAL '1 second' WHERE runner_id=%s", (row['runner_id'],))
    storage[1].reap_expired()
    ended = end_waiting(repository, principal, row['runner_id'])
    assert ended['checkpoint']['detached_local_invocations'] == [actual]
    assert devices.get_invocation(rejected_id, tenant) == rejected
    assert devices.get_invocation(source['original'], tenant) == original
    assert not devices.operation_completion_known(original)
    # An older saved hold can still include the rejected row. Query projection must
    # retain its exact private proof while refusing the genuinely unknown read.
    legacy = copy.deepcopy(ended['checkpoint'])
    legacy['detached_local_invocations'] = [rejected_id, actual]
    service_database.rows('UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s',
        (json.dumps(legacy), row['runner_id']))
    with pytest.raises(devices.DeviceOperationUnresolved):
        enqueue(source)
    # This is the first late result after transport loss, not a replacement of a
    # previously accepted business-unknown receipt.
    devices.write_result(actual, tenant, claim, True, effect='none', data={'send_status': 'unknown'})
    assert devices.operation_completion_known(devices.get_invocation(actual, tenant))
    assert enqueue(source), 'the authorized read can pass once only the actual read has a completion proof'
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(tenant, source['actor'].user_id, source['device'],
            'wecom_message_send', {'target_name': 'Anonymous group', 'text': 'Never send'}, source['actor'].session_id, provider_key='wecom')
    assert source['repository'].get(row['runner_id'])['checkpoint'] == legacy
    assert devices.get_invocation(source['original'], tenant) == original
