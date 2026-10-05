"""Committed cancel-blocked release versus forged proof, real PG/heartbeat."""
import asyncio
from datetime import datetime,timedelta,timezone
import threading
from types import SimpleNamespace
import uuid

import pytest

from src.local_tools import repository as devices
from src.services.agent_runner.contracts import Principal
from src.services.agent_runner.local_invocations import LocalInvocationRepository,LocalPhase
from src.services.agent_runner.worker import RunnerWorker

from .test_local_cancel_authority import claimed_local
from .test_storage import storage,attempt

pytestmark=pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize('forged',[False,True])
async def test_real_blocked_cancel_commit_return_window_has_exact_release_proof(claimed_local,service_database,forged):
    repository,execution,actor,device,prepare=claimed_local
    row,state,control,call=prepare()
    phase=LocalPhase(state.execution_id,call.id,'main',0)
    row=LocalInvocationRepository(service_database.connect).bind(attempt(row),row['revision'],phase,
        device_id=str(device['id']),tool_name=call.name,arguments={},deadline_at=datetime.now(timezone.utc)+timedelta(seconds=60))
    original=row['checkpoint']['execution']['resources']['local_invocations'][phase.key(row['runner_id'])]['invocation_id']
    claim='fictional-block-heartbeat-'+uuid.uuid4().hex
    claimed=devices.claim_next(str(device['id']),actor.tenant_id,claim,60,expected_invocation_id=original)
    assert str(claimed['id'])==original
    assert devices.mark_started(original,actor.tenant_id,claim)['state']=='running'
    repository.cancel(Principal(state.identity,'user',actor.user_id,row['service_id']),row['runner_id'])
    worker=RunnerWorker(SimpleNamespace(heartbeat_seconds=.03,lease_seconds=30),worker_id=row['worker_id'],
        execution_repository=execution,repository=repository)
    facts=await worker._cancel_saved_invocations(control.attempt)
    assert facts[0]['state']=='cancel_requested'
    entered,release,finished=threading.Event(),threading.Event(),threading.Event()
    def operation():
        saved=execution.block_cancel(control.attempt,'LOCAL_CANCEL_ACK_PENDING')
        entered.set()
        try:
            assert release.wait(5)
            return saved
        finally: finished.set()
    owner=asyncio.create_task(asyncio.to_thread(operation))
    heartbeat=None
    try:
        assert await asyncio.to_thread(entered.wait,3)
        if forged:
            service_database.rows("UPDATE agent_runners SET checkpoint=jsonb_set(checkpoint,'{cancel_completion_blocked,worker_id}',to_jsonb('foreign-proof-owner'::text)) WHERE runner_id=%s",(row['runner_id'],))
        heartbeat=asyncio.create_task(worker._heartbeat(control.attempt,control,owner))
        await asyncio.wait_for(heartbeat,2)
        assert control.stopped is forged
        if forged:
            with pytest.raises(asyncio.CancelledError): await owner
        else:
            assert not owner.done()
            release.set()
            returned=await asyncio.wait_for(owner,3)
            assert returned['status']=='waiting' and returned['worker_id'] is None and returned['lease_until'] is None
        assert repository.get(row['runner_id'])['status']=='waiting'
        assert execution.acquire('cannot-hot-reclaim',30) is None
        assert devices.get_invocation(original,actor.tenant_id)['state']=='cancel_requested'
        assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',(row['runner_id'],))==[{'owner_runner_id':row['runner_id']}]
        assert service_database.rows('SELECT 1 FROM chat_records WHERE session_id=%s',(actor.session_id,))==[]
    finally:
        release.set()
        if heartbeat is not None: heartbeat.cancel()
        await asyncio.gather(*(item for item in (owner,heartbeat) if item is not None),return_exceptions=True)
        assert await asyncio.to_thread(finished.wait,3)


@pytest.mark.asyncio
async def test_late_cancel_observation_does_not_wait_for_invocation_lock_or_block_other_session(
        claimed_local,storage,actors,service_database):
    repository,execution,actor,device,prepare=claimed_local
    row,state,control,call=prepare()
    phase=LocalPhase(state.execution_id,call.id,'main',0)
    row=LocalInvocationRepository(service_database.connect).bind(attempt(row),row['revision'],phase,
        device_id=str(device['id']),tool_name=call.name,arguments={},deadline_at=datetime.now(timezone.utc)+timedelta(seconds=60))
    original=row['checkpoint']['execution']['resources']['local_invocations'][phase.key(row['runner_id'])]['invocation_id']
    claim='fictional-late-read-'+uuid.uuid4().hex
    assert devices.claim_next(str(device['id']),actor.tenant_id,claim,60,expected_invocation_id=original)
    assert devices.mark_started(original,actor.tenant_id,claim)['state']=='running'
    repository.cancel(Principal(state.identity,'user',actor.user_id,row['service_id']),row['runner_id'])
    worker=RunnerWorker(SimpleNamespace(lease_seconds=30),worker_id='unrelated-ready-worker',
        execution_repository=execution,repository=repository)
    await worker._cancel_saved_invocations(control.attempt)
    blocked=execution.block_cancel(control.attempt,'LOCAL_CANCEL_ACK_PENDING')
    other,_=storage[2](actors['b'])
    task=None
    try:
        with service_database.connect() as connection,connection.cursor() as cursor:
            cursor.execute('SELECT id FROM local_tool_invocations WHERE id=%s FOR UPDATE',(original,))
            task=asyncio.create_task(worker._acquire_next())
            claimed=await asyncio.wait_for(asyncio.shield(task),2)
            assert claimed['runner_id']==other['runner_id'] and claimed['status']=='running'
            assert repository.get(row['runner_id'])['attempt']==blocked['attempt']
            assert repository.get(row['runner_id'])['checkpoint']==blocked['checkpoint']
    finally:
        # asyncio timeout cannot terminate the actual PG thread; release its
        # original row lock first, then let that owned SQL operation finish.
        if task is not None: await asyncio.wait_for(task,5)
    assert devices.get_invocation(original,actor.tenant_id)['state']=='cancel_requested'
    assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',(row['runner_id'],))==[{'owner_runner_id':row['runner_id']}]
