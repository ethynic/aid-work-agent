"""Real PG sleep/claim transactions; no timer queue, credentials or business I/O."""

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest

from src.core.agent_engine.contracts import ExecutionState,Identity,AgentMode,Outcome,ToolCall,ToolFact
from src.services.agent_runner.control_contracts import RunnerControl
from src.services.agent_runner.control_repository import ControlRepository
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.durable_sleep import complete_sleep
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner.runtime_manifest import configuration_fingerprint
from src.services.agent_runner.timer_repository import TimerRepository
from src.services.agent_runner.wait_identity import owned_wait_id
from .test_storage import storage,attempt

pytestmark=pytest.mark.integration


@pytest.fixture(autouse=True)
def enable_sleep(monkeypatch):
    from src.config.settings import settings
    monkeypatch.setattr(settings.agent_runner,'durable_sleep_enabled',True)


def park(storage,actor):
    repository,execution,submit=storage
    accepted,principal=submit(actor)
    owned=execution.acquire('sleep-owner',30)
    call=ToolCall('sleep-call','agent_sleep',{'seconds':10})
    state=ExecutionState(principal.identity,owned['runner_id'],AgentMode.MASTER,'prompt',[],
        profile_id='main',iteration=1,outcome=Outcome.WAITING,pending=[call],
        tools={call.id:ToolFact(call,phase='waiting')},
        resources={'business_iterations':1,'round_tool_ids':[call.id],
            'configuration_fingerprint':configuration_fingerprint('fixture-profile'),'durable_sleep_version':1})
    state.waiting={'kind':'timer','version':1,'seconds':10,'tool_call_id':call.id,
        'target_execution_id':state.execution_id}
    state.waiting['wait_id']=owned_wait_id(state.execution_id,state.execution_id,state.waiting)
    parked=execution.park(attempt(owned),owned['revision'],'waiting',{'execution':state.checkpoint()},
                          {'waiting':copy.deepcopy(state.waiting)})
    return parked,principal


def due(service_database,row):
    # Move both authoritative projections consistently; this is a test clock
    # fixture only. The production code always reads clock_timestamp().
    deadline=datetime.now(timezone.utc)-timedelta(seconds=1)
    checkpoint=copy.deepcopy(row['checkpoint'])
    checkpoint['durable_sleep']['wake_at']=deadline.isoformat()
    checkpoint['execution']['waiting']['wake_at']=deadline.isoformat()
    from psycopg2.extras import Json
    service_database.rows('UPDATE agent_runners SET wake_at=%s,checkpoint=%s WHERE runner_id=%s',
        (deadline,Json(checkpoint),row['runner_id']))
    return {**row,'wake_at':deadline,'checkpoint':checkpoint}


def prepare(row,principal):
    authorizer=SimpleNamespace(authorize_persisted=lambda *a,**k:principal,assert_credit=lambda p:None)
    profiles=SimpleNamespace(resolve=lambda *a:(None,'fixture-profile'))
    return RecoveryCoordinator(authorizer,profiles).prepare(row,timer_trigger=True).checkpoint


def test_committed_sleep_uses_database_deadline_releases_attempt_and_preserves_claim(storage,actors,service_database):
    row,principal=park(storage,actors['a'])
    assert row['status']=='waiting' and row['worker_id'] is None and row['lease_until'] is None
    assert row['wake_at'] is not None
    assert row['checkpoint']['execution']['resources']['business_iterations']==0
    assert row['checkpoint']['execution']['iteration']==1
    timers=TimerRepository(service_database.connect)
    assert timers.next_candidates()==[]
    assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE session_id=%s',
        (actors['a'].session_id,))[0]['owner_runner_id']==row['runner_id']


def test_competing_timer_claims_complete_same_call_once_and_do_not_create_user_controls(storage,actors,service_database):
    row,principal=park(storage,actors['a'])
    row=due(service_database,row)
    prepared=prepare(row,principal)
    timers=TimerRepository(service_database.connect)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda worker:timers.claim(row,worker,30,prepared),('timer-a','timer-b')))
    claimed=[result for result in results if result is not None]
    assert len(claimed)==1
    claimed=claimed[0]
    assert claimed['runner_id']==row['runner_id'] and claimed['record_id']==row['record_id']
    assert claimed['attempt']==row['attempt']+1 and claimed['wake_at'] is None
    fact=claimed['checkpoint']['execution']['tools']['sleep-call']
    assert fact['phase']=='completed' and fact['result']['trigger']=='timer' and not fact['result_recorded']
    assert fact['result']['woke_at']
    assert service_database.rows('SELECT * FROM agent_runner_controls WHERE runner_id=%s',(row['runner_id'],))==[]


def test_pause_and_accepted_resume_win_over_prepared_timer_claim(storage,actors,service_database):
    row,principal=park(storage,actors['a'])
    row=due(service_database,row)
    prepared=prepare(row,principal)
    controls=ControlRepository(service_database.connect)
    controls.submit(principal,row['runner_id'],RunnerControl(client_request_id='pause',action='pause'))
    timers=TimerRepository(service_database.connect)
    assert timers.claim(row,'late-timer',30,prepared) is None
    assert timers.next_candidates()==[]
    current=storage[0].get(row['runner_id'])
    assert current['wake_at']==row['wake_at']
    wait=current['checkpoint']['execution']['waiting']
    controls.submit(principal,row['runner_id'],RunnerControl(client_request_id='resume',action='resume',
        wait_id=wait['wait_id'],target_execution_id=row['runner_id']))
    assert timers.claim(current,'still-late',30,prepared) is None


def test_timer_immediate_resume_requires_exact_original_wait(storage,actors,service_database):
    row,principal=park(storage,actors['a'])
    controls=ControlRepository(service_database.connect)
    with pytest.raises(RunnerError,match='CONTROL_WAIT_STALE'):
        controls.submit(principal,row['runner_id'],RunnerControl(client_request_id='stale',action='resume',
            wait_id='old-wait',target_execution_id=row['runner_id']))
    assert storage[0].get(row['runner_id'])['resume_control_id'] is None


def test_claim_before_action_crash_reaps_and_restores_same_activation_without_new_credit(storage,actors,service_database):
    row,principal=park(storage,actors['a'])
    row=due(service_database,row)
    timers=TimerRepository(service_database.connect)
    claimed=timers.claim(row,'crashed-after-claim',30,prepare(row,principal))
    service_database.rows("UPDATE agent_runners SET lease_until=clock_timestamp()-interval '1 second' WHERE runner_id=%s",
        (row['runner_id'],))
    assert storage[1].reap_expired()==[row['runner_id']]
    interrupted=timers.next_candidates()[0]
    replayed=timers.claim(interrupted,'replacement',30,prepare(interrupted,principal))
    assert replayed['checkpoint']['execution']['resources']['business_iterations']==0
    assert replayed['checkpoint']['execution']['tools']['sleep-call']['result']['woke_at']==claimed['checkpoint']['execution']['tools']['sleep-call']['result']['woke_at']
    assert replayed['attempt']==claimed['attempt']+1


def test_cancel_clears_due_projection_and_never_revives_runner(storage,actors,service_database):
    row,principal=park(storage,actors['a'])
    row=due(service_database,row)
    prepared=prepare(row,principal)
    cancelled=storage[0].cancel(principal,row['runner_id'])
    assert cancelled['wake_at'] is None
    assert TimerRepository(service_database.connect).claim(row,'cancel-loser',30,prepared) is None


def test_preflight_failure_holds_timer_with_original_deadline_for_user_retry(storage,actors,service_database):
    row,principal=park(storage,actors['a'])
    row=due(service_database,row)
    timers=TimerRepository(service_database.connect)
    assert timers.hold(row,'RECOVERY_CONFIGURATION_CHANGED')
    assert timers.next_candidates()==[]
    held=storage[0].get(row['runner_id'])
    assert held['wake_at'] is None
    assert held['checkpoint']['durable_sleep']==row['checkpoint']['durable_sleep']
