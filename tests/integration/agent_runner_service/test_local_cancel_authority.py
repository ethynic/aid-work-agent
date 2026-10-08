"""Real Worker/Adapter/Lifecycle cancellation and locked-device policy ports.

These are narrow claimed-state integration cases, not default CLI conversations.
No owner, tool result, fence or database method is replaced.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact
from src.local_tools import repository as devices
from src.local_tools.proxy_tool import BossSendCurrentTool
from src.services.agent_runner.durable_control import DurableControl
from src.services.agent_runner.contracts import Principal
from src.services.agent_runner.local_invocations import LocalInvocationRepository, LocalPhase
from src.services.agent_runner.local_owner import RunnerLocalLifecycle
from src.services.agent_runner.ownership import LeaseLost
from src.services.agent_runner.runtime.local_tools import LocalToolAdapter
from src.services.agent_runner.worker import RunnerWorker
from src.tools.context import ExecutionContextFactory
from src.tools.executor import ToolExecutor
from src.tools.registry import ToolRegistry

from .conftest import wait_for
from .test_storage import storage, attempt

pytestmark=pytest.mark.integration


@pytest.fixture
def claimed_local(storage, actors, service_database):
    repository, execution, submit=storage
    actor=actors['a']
    accepted,_=submit(actor)
    row=execution.acquire('captured-local-owner',60)
    assert row['runner_id']==accepted['runner_id']
    identity=Identity(*(row[key] for key in ('tenant_id','user_id','session_id','source','session_kind')))
    device=devices.create_device(actor.tenant_id,actor.user_id,'fictional-cancel-'+uuid.uuid4().hex,
        capabilities={'provider_id':'ai.aidwork.boss-recruiting'})
    assert devices.select_device(actor.tenant_id,actor.user_id,str(device['id']))
    assert devices.touch_device_seen(str(device['id']))
    def prepare(name='boss_select_job',arguments=None):
        state=ExecutionState(identity,row['runner_id'],AgentMode.MASTER,'Fixture',[])
        call=ToolCall('captured-local-call',name,arguments or {})
        state.tools[call.id]=ToolFact(call,phase='dispatching')
        saved=execution.save_checkpoint(attempt(row),row['revision'],{'execution':state.checkpoint()})
        control=DurableControl(execution,attempt(saved),saved)
        control.state=state
        return saved,state,control,call
    try:
        yield repository,execution,actor,device,prepare
    finally:
        for table in ('local_tool_events','local_tool_invocations','local_tool_devices','client_usage_logs'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s',(actor.tenant_id,))


@pytest.mark.asyncio
async def test_actual_worker_cancel_cannot_borrow_replacement_attempt(claimed_local,service_database):
    repository,execution,actor,device,prepare=claimed_local
    row,state,_,call=prepare()
    phase=LocalPhase(state.execution_id,call.id,'main',0)
    row=LocalInvocationRepository(service_database.connect).bind(attempt(row),row['revision'],phase,
        device_id=str(device['id']),tool_name=call.name,arguments={},
        deadline_at=datetime.now(timezone.utc)+timedelta(seconds=60))
    original=row['checkpoint']['execution']['resources']['local_invocations'][phase.key(row['runner_id'])]['invocation_id']
    captured=attempt(row)
    worker=RunnerWorker(SimpleNamespace(),worker_id=captured.worker_id,execution_repository=execution,repository=repository)
    # An actual persisted replacement epoch, not a forged Attempt passed to DAL.
    service_database.rows("UPDATE agent_runners SET worker_id='replacement-owner',attempt=attempt+1,cancel_requested=true WHERE runner_id=%s",(row['runner_id'],))
    with pytest.raises(LeaseLost):
        await worker._cancel_saved_invocations(captured)
    assert devices.get_invocation(original,actor.tenant_id)['state']=='queued'
    current=repository.get(row['runner_id'])
    replacement=RunnerWorker(SimpleNamespace(),worker_id=current['worker_id'],execution_repository=execution,repository=repository)
    await replacement._cancel_saved_invocations(attempt(current))
    assert devices.get_invocation(original,actor.tenant_id)['state']=='cancelled'
    assert repository.get(row['runner_id'])['checkpoint']==row['checkpoint']
    assert service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE session_id=%s',(actor.session_id,))==[{'n':1}]


@pytest.mark.asyncio
@pytest.mark.parametrize('lost_epoch',[False,True])
async def test_special_first_dispatch_live_cancel_uses_durable_authority(claimed_local,service_database,lost_epoch):
    repository,execution,actor,_,prepare=claimed_local
    arguments={'message':'Fictional dry-run message','dry_run':True}
    row,state,control,call=prepare('boss_send_current',arguments)
    registry=ToolRegistry();registry.register(BossSendCurrentTool())
    adapter=LocalToolAdapter(session_id=actor.session_id,execution_id=state.execution_id,
        subagent_config=None,llm=None,tool_executor=ToolExecutor(registry),
        cancel_on_detach=False,control=control,state=state)
    context=ExecutionContextFactory.for_agent_call(tenant_id=actor.tenant_id,user_id=actor.user_id,
        session_id=actor.session_id,agent_execution_id=state.execution_id,tool_call_id=call.id,
        infer_legacy_identity=False,env_vars={})
    invocation=None
    async def consume():
        nonlocal invocation
        async for kind,payload in adapter._run_local_required_tool(call.name,arguments,actor.tenant_id,actor.user_id,
                context=context,cancel_check=lambda:control.cancel_requested):
            if kind=='invocation':
                invocation=payload
                state.tools[call.id].invocation_id=payload
                await control.save(state,'tool_invocation')
                if lost_epoch:
                    service_database.rows("UPDATE agent_runners SET worker_id='replacement-special-owner',attempt=attempt+1,cancel_requested=true WHERE runner_id=%s",(row['runner_id'],))
                else:
                    repository.cancel(Principal(state.identity,'user',actor.user_id,row['service_id']),row['runner_id'])
                control.cancel_requested=True
    if lost_epoch:
        with pytest.raises(LeaseLost): await asyncio.wait_for(consume(),timeout=10)
    else:
        await asyncio.wait_for(consume(),timeout=10)
    assert invocation is not None
    assert devices.get_invocation(invocation,actor.tenant_id)['state']==('queued' if lost_epoch else 'cancelled')
    saved=repository.get(row['runner_id'])['checkpoint']['execution']
    assert saved['tools'][call.id]['invocation_id']==invocation
    assert service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE session_id=%s',(actor.session_id,))==[{'n':1}]
    assert service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s',(actor.tenant_id,))==[]


@pytest.mark.parametrize('revocation',['selection','catalog'])
def test_actual_lifecycle_rechecks_device_policy_after_lock_wait(claimed_local,service_database,revocation):
    repository,execution,actor,device,prepare=claimed_local
    row,state,control,call=prepare()
    owner=RunnerLocalLifecycle(control,state,call.id)
    def bind():
        return asyncio.run(owner.bind_invocation(branch='main',ordinal=0,tool_name=call.name,
            device=device,arguments={},deadline_at=datetime.now(timezone.utc)+timedelta(seconds=60)))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with service_database.connect() as connection,connection.cursor() as cursor:
            cursor.execute('SELECT id FROM local_tool_devices WHERE id=%s FOR UPDATE',(str(device['id']),))
            if revocation=='selection': cursor.execute('UPDATE local_tool_devices SET selected=false WHERE id=%s',(str(device['id']),))
            else: cursor.execute("UPDATE local_tool_devices SET capabilities_json='{}'::jsonb WHERE id=%s",(str(device['id']),))
            future=pool.submit(bind)
            wait_for(lambda:service_database.rows("SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND wait_event_type='Lock' AND query LIKE '%%local_tool_devices%%'"),timeout=5)
            assert not future.done()
            connection.commit()
        with pytest.raises(LeaseLost,match='LOCAL_DEVICE_NOT_READY'): future.result(timeout=5)
    saved=repository.get(row['runner_id'])
    assert saved['revision']==row['revision']+1 # only local_before_bind committed
    assert not saved['checkpoint']['execution']['resources'].get('local_invocations')
    assert service_database.rows('SELECT 1 FROM local_tool_invocations WHERE session_id=%s',(actor.session_id,))==[]
