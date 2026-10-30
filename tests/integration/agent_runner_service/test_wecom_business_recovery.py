"""Actual PostgreSQL recovery/cancel ownership; no desktop input or messages."""

import copy
import asyncio
import threading
import time
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from psycopg2.extras import Json

from src.core.agent_engine.contracts import AgentMode, ExecutionState, ToolCall, ToolFact
from src.local_tools import repository as devices
from src.local_tools.wecom_send_status import original_scope
from src.services.agent_runner.local_recovery import LocalRecoveryProof, cancel_owned_invocations, cancel_completion_status
from src.services.agent_runner.local_verification import verify_local
from .test_storage import storage, attempt
from .test_wecom_send_status_scope import send_scope

pytestmark = pytest.mark.integration
CANONICAL = 'ws1.original-conversation'


@pytest.fixture
def named_scope(storage,actors,service_database,monkeypatch):
    from src.local_tools.proxy_tool import LocalToolProxyTool
    from src.services.agent_runner.durable_control import DurableControl
    from src.services.agent_runner.local_owner import RunnerLocalLifecycle
    from src.services.agent_runner.runtime_manifest import configuration_fingerprint
    from src.config.settings import settings
    monkeypatch.setattr(settings.agent_runner,'durable_sleep_enabled',True)
    repository,executions,submit = storage
    accepted,principal = submit(actors['a'])
    row = executions.acquire('original-name-owner',60)
    state = ExecutionState(principal.identity,row['runner_id'],AgentMode.MASTER,'accepted role',[],profile_id='main')
    state.resources['configuration_fingerprint'] = configuration_fingerprint('fixture-profile')
    call = ToolCall('named-original','wecom_message_send',{'session':'Complete group name','text':'first reply'})
    state.tools[call.id] = ToolFact(call,phase='dispatching')
    state.pending = [call]
    row = executions.save_checkpoint(attempt(row),row['revision'],{
        'continuous':{'version':1,'enabled':True},'execution':state.checkpoint()})
    device = devices.create_device(actors['a'].tenant_id,actors['a'].user_id,uuid.uuid4().hex)
    identifier = str(device['id'])
    service_database.rows('UPDATE local_tool_devices SET selected=TRUE,last_seen_at=NOW(),capabilities_json=%s WHERE id=%s',
        (Json({'providers':['wecom']}),identifier))
    writes,reads = [],[]
    async def local_result(tool,invocation,tenant_id,progress,*,durable=False):
        raw = devices.get_invocation(str(invocation['id']),tenant_id)
        if raw['state'] == 'queued':
            token = uuid.uuid4().hex
            assert devices.claim_next(identifier,tenant_id,token,60,['wecom'],expected_invocation_id=str(raw['id']))
            assert devices.mark_started(str(raw['id']),tenant_id,token)
            if tool.name == 'wecom_message_send':
                writes.append(dict(raw))
                unknown = raw['arguments_json']['text'] == 'first reply'
                devices.write_result(str(raw['id']),tenant_id,token,not unknown,
                    code='EXECUTION_UNKNOWN' if unknown else 'OK',effect='unknown' if unknown else 'applied',
                    data=None if unknown else {'session':raw['arguments_json']['session']},device_id=identifier)
            else:
                reads.append(dict(raw))
                data = ({'session':CANONICAL,'resolve_only':True} if raw['arguments_json'].get('resolve_only')
                    else {'sessions':[{'session':CANONICAL,'messages':[]}],'cursor':'same-native-cursor','baseline':False})
                devices.write_result(str(raw['id']),tenant_id,token,True,code='OK',effect='none',data=data,device_id=identifier)
            raw = devices.get_invocation(str(raw['id']),tenant_id)
        return tool._map_terminal(raw)
    monkeypatch.setattr(LocalToolProxyTool,'_poll_invocation',local_result)
    def owner(row,state,call_id):
        control = DurableControl(executions,attempt(row),row)
        control.state = state
        return RunnerLocalLifecycle(control,state,call_id)
    source = SimpleNamespace(repository=repository,executions=executions,principal=principal,
        row=row,state=state,owner=owner(row,state,call.id),new_owner=owner,writes=writes,reads=reads,device=identifier)
    try:
        yield source
    finally:
        service_database.rows('DELETE FROM local_tool_events WHERE invocation_id IN (SELECT id FROM local_tool_invocations WHERE device_id=%s)',(identifier,))
        service_database.rows('DELETE FROM local_tool_invocations WHERE device_id=%s',(identifier,))
        service_database.rows('DELETE FROM local_tool_devices WHERE id=%s',(identifier,))


async def business_call(source,name,arguments,*,new_call=False):
    from src.local_tools import wecom_flow
    from src.local_tools.lifecycle import LocalOperationScope,local_operation_scope
    from src.local_tools.proxy_tool import WecomMessageSendTool,WecomReadSessionsTool
    if new_call:
        old = source.state.tools[source.owner.call_id]
        old.phase,old.result_recorded = 'completed',True
        source.state.pending = []
        call = ToolCall(uuid.uuid4().hex,name,arguments)
        source.state.tools[call.id] = ToolFact(call,phase='dispatching')
        source.state.pending = [call]
        await source.owner.control.save(source.state,'after_tool')
        source.owner = source.new_owner(source.repository.get(source.row['runner_id']),source.state,call.id)
    tool = WecomMessageSendTool() if name == 'wecom_message_send' else WecomReadSessionsTool()
    with local_operation_scope(source.owner):
        return await wecom_flow.run(tool,LocalOperationScope(source.owner,'main',0),arguments)


@pytest.mark.asyncio
async def test_named_unknown_without_returned_target_is_blocked_after_a_read_exposes_stable_id_and_other_target_runs(named_scope):
    source = named_scope
    first = await business_call(source,'wecom_message_send',source.state.pending[0].arguments)
    assert first['effect'] == 'unknown' and not first.get('data')
    assert source.reads[0]['arguments_json'] == {'session':'Complete group name','limit':1,'resolve_only':True}
    assert source.writes[0]['arguments_json']['session'] == CANONICAL
    read = await business_call(source,'wecom_read_sessions',{},new_call=True)
    stable = read['data']['sessions'][0]['session']
    duplicate = await business_call(source,'wecom_message_send',{'session':stable,'text':'duplicate'},new_call=True)
    assert duplicate['code'] == 'WECOM_SEND_PENDING' and duplicate['effect'] == 'none'
    assert len(source.writes) == 1
    other = await business_call(source,'wecom_message_send',{'session':'ws1.other','text':'independent'},new_call=True)
    assert other['success'] and len(source.writes) == 2
    assert len(source.reads) == 2, 'Stable sends must not add another resolution read'


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary',['resolved_before_main','main_unknown'])
async def test_fresh_attempt_reuses_owned_resolution_without_changing_model_intent(named_scope,service_database,monkeypatch,boundary):
    from src.local_tools import overlay_flow,wecom_flow
    from src.services.agent_runner.recovery import RecoveryCoordinator
    from src.services.agent_runner.recovery_repository import RecoveryRepository
    source = named_scope
    original_poll = overlay_flow.poll_operation
    async def interrupted(tool,operation,arguments,**kwargs):
        if tool.name == 'wecom_message_send':
            raise InterruptedError('fixture server publication before main bind')
        return await original_poll(tool,operation,arguments,**kwargs)
    if boundary == 'resolved_before_main':
        monkeypatch.setattr(overlay_flow,'poll_operation',interrupted)
        with pytest.raises(InterruptedError):
            await business_call(source,'wecom_message_send',source.state.pending[0].arguments)
        monkeypatch.setattr(overlay_flow,'poll_operation',original_poll)
    else:
        await business_call(source,'wecom_message_send',source.state.pending[0].arguments)
    row = source.executions.interrupt_attempt(source.owner.root.attempt)
    proof = LocalRecoveryProof(service_database.connect)
    authorizer = SimpleNamespace(authorize_persisted=lambda *a,**k:source.principal,assert_credit=lambda p:None)
    profiles = SimpleNamespace(resolve=lambda *a:(None,'fixture-profile'))
    prepared = RecoveryCoordinator(authorizer,profiles,tool_recovery=proof).prepare(row,continuous_trigger=True)
    resumed = RecoveryRepository(service_database.connect).claim_continuous(row,'fresh-name-owner',60,prepared.checkpoint)
    assert resumed and resumed['attempt'] == row['attempt']+1
    source.state = ExecutionState.restore(resumed['checkpoint']['execution'])
    source.owner = source.new_owner(resumed,source.state,'named-original')
    result = await business_call(source,'wecom_message_send',source.state.pending[0].arguments)
    assert result['effect'] == 'unknown' and len(source.reads) == len(source.writes) == 1
    assert source.state.tools['named-original'].call.arguments['session'] == 'Complete group name'
    assert set(source.state.resources['wecom_pending_sends']) == {CANONICAL}
    invocations = service_database.rows('SELECT tool_name,arguments_json FROM local_tool_invocations WHERE device_id=%s',
        (source.device,))
    assert len(invocations) == 2
    assert next(row for row in invocations if row['tool_name']=='wecom_message_send')['arguments_json']['session'] == CANONICAL


def modern_cancel(source, database):
    checkpoint = copy.deepcopy(source['row']['checkpoint'])
    checkpoint.pop('detached_local_wait', None)
    checkpoint.pop('detached_local_invocations', None)
    root = checkpoint['execution']
    root.setdefault('resources', {})['wecom_task_device'] = {'version': 2,
        'runner_id': source['row']['runner_id'], 'identity': root['identity'], 'device_id': source['device']}
    database.rows('UPDATE agent_runners SET checkpoint=%s WHERE runner_id=%s',
        (Json(checkpoint), source['row']['runner_id']))
    return source['executions'].acquire('modern-cancel-owner', 60)


def test_interrupted_before_main_binding_can_resume_the_same_modern_read(storage, actors):
    repository, executions, submit = storage
    row, principal = submit(actors['a'])
    owned = executions.acquire('pre-read-owner', 60)
    state = ExecutionState(principal.identity, row['runner_id'], AgentMode.MASTER, 'accepted role', [], profile_id='main')
    call = ToolCall('same-read', 'wecom_read_sessions', {})
    state.tools[call.id] = ToolFact(call, phase='dispatching')
    state.pending = [call]
    executions.save_checkpoint(attempt(owned), owned['revision'], {'execution':state.checkpoint()})
    interrupted = executions.interrupt_attempt(attempt(owned))
    assert LocalRecoveryProof(repository.connection_factory).can_restore(interrupted, state, state.tools[call.id])
    assert not state.resources.get('local_invocations'), 'preflight must not fabricate a main invocation'


def test_user_stop_ends_modern_cloud_wait_but_preserves_unknown_physical_hold(send_scope, service_database):
    source = send_scope
    owned = modern_cancel(source, service_database)
    facts = cancel_owned_invocations(service_database.connect, attempt(owned))
    assert facts.row['checkpoint']['detached_local_invocations'] == [source['original']]
    assert cancel_completion_status(facts, detached_local_wait=True) == (True, None)
    raw = devices.get_invocation(source['original'], source['actor'].tenant_id)
    assert raw['state'] == raw['effect'] == 'unknown'
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(source['actor'].tenant_id, source['actor'].user_id, source['device'],
            'wecom_message_send', {'target_name':'Other', 'text':'must not dispatch'}, provider_key='wecom')


def test_wecom_resource_receipt_releases_only_hold_not_original_business_unknown(send_scope, service_database):
    source = send_scope
    from src.services.agent_runner.finalizer import RunnerFinalizer
    from src.services.agent_runner.worker import execution_result
    cancellation = source['executions'].acquire('end-original-wait', 60)
    facts = cancel_owned_invocations(service_database.connect, attempt(cancellation))
    old_state = ExecutionState.restore(facts.row['checkpoint']['execution'])
    source['executions'].stage_finalization(attempt(cancellation), facts.row['revision'], facts.row['checkpoint'],
        execution_result(old_state, 'cancelled'), facts.row['public_snapshot'])
    RunnerFinalizer(service_database.connect).finalize(attempt(cancellation))
    capabilities = {'capabilities':['runtime_recovery_observe_v2', 'runtime_recovery_stop_v1', 'runtime_recovery_wecom_v1']}
    service_database.rows('UPDATE local_tool_devices SET capabilities_json=%s WHERE id=%s',
        (Json(capabilities), source['device']))
    row = verify_local(source['repository'], source['principal'], source['row']['runner_id'], 'wecom-stop', stop_resources=True)
    identifier = row['checkpoint']['local_verification']['invocation_id']
    token = uuid.uuid4().hex
    assert devices.claim_next(source['device'], source['actor'].tenant_id, token, 60,
        ['runtime-recovery'], expected_invocation_id=identifier)
    args = devices.get_invocation(identifier, source['actor'].tenant_id)['arguments_json']
    assert args['provider_key'] == 'wecom'
    observation = {'version':args['version'], 'scope_id':args['scope_id'], 'device_id':source['device'],
        'original_invocation_ids':args['original_invocation_ids'], 'provider_scope':'wecom',
        'target_relation':'not_applicable', 'browser_target_ref':None,
        'page_summary':{'observed':False, 'page_kind':'not_applicable', 'result':'unknown'},
        'resource_stopped':True, 'stop_evidence':'supervised_device_boundary',
        'resource_scope':'managed_provider_pool', 'runtime_generation':str(uuid.uuid4())}
    result = devices.write_runtime_result(identifier, source['actor'].tenant_id, token, True,
        code='OK', effect='none', data={'recovery_observation':observation}, device_id=source['device'])
    assert result['state'] == 'succeeded'
    verified = source['repository'].get(source['row']['runner_id'])['checkpoint']
    assert verified['local_verification']['resource_stopped'] is True
    assert verified['local_verification']['page_observed'] is False
    assert source['original'] in verified['verified_local_invocations']
    assert devices.get_invocation(source['original'], source['actor'].tenant_id)['state'] == 'unknown'


def test_cancel_and_status_admission_follow_same_lock_order(send_scope, service_database):
    source = send_scope
    owned = modern_cancel(source, service_database)
    locked, proceed = threading.Event(), threading.Event()
    label = 'wecom-cancel-' + uuid.uuid4().hex

    def status_claim():
        with service_database.connect() as connection:
            cursor = connection.cursor()
            devices.lock_operation_admission(cursor, source['actor'].tenant_id)
            locked.set()
            assert proceed.wait(10)
            assert original_scope(cursor, source['actor'].tenant_id, source['actor'].user_id,
                source['actor'].session_id, source['original'], device_id=source['device'])

    @contextmanager
    def cancellation_connection():
        with service_database.connect() as connection:
            connection.cursor().execute('SET application_name=%s', (label,))
            yield connection

    with ThreadPoolExecutor(max_workers=2) as pool:
        query = pool.submit(status_claim)
        assert locked.wait(10)
        cancellation = pool.submit(cancel_owned_invocations, cancellation_connection, attempt(owned))
        try:
            until = time.monotonic() + 10
            while time.monotonic() < until:
                waiting = service_database.rows("SELECT wait_event FROM pg_stat_activity WHERE application_name=%s", (label,))
                if waiting and waiting[0]['wait_event'] == 'advisory':
                    break
                time.sleep(.02)
            else:
                pytest.fail('cancel never reached the shared admission lock')
        finally:
            proceed.set()
        query.result(timeout=10)
        assert cancellation.result(timeout=10).row['checkpoint']['detached_local_wait'] is True
