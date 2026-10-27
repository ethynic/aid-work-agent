"""Sleep is a committed cloud fact, not a local invocation or a free round."""

import copy
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.core.agent_engine import AgentEngine,AgentMode,DispatchResult,ExecutionState,Outcome
from src.core.agent_engine.contracts import Identity,ToolCall,ToolFact
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.durable_sleep import arm,complete_sleep,recover_uncommitted,sleep_leaf,validate_armed,replayable_activation
from src.services.agent_runner.wait_identity import owned_wait_id
from src.tools.agent.sleep_tool import SleepInput


def sleeper(*,mixed=False,continuous=False):
    state=ExecutionState(Identity('tenant','user','session'),'runner',AgentMode.MASTER,'prompt',[],
        iteration=1,profile_id='main',outcome=Outcome.WAITING)
    call=ToolCall('sleep-1','agent_sleep',{'seconds':10})
    state.pending=[call]
    state.tools[call.id]=ToolFact(call,phase='waiting')
    state.waiting={'kind':'timer','version':1,'seconds':10,'tool_call_id':call.id,
                   'target_execution_id':state.execution_id}
    state.waiting['wait_id']=owned_wait_id(state.execution_id,state.execution_id,state.waiting)
    state.resources={'business_iterations':1,'round_tool_ids':['read',call.id] if mixed else [call.id]}
    if continuous:
        state.resources.update(continuous_enabled=True,continuous_segment_start=0)
    return state


@pytest.mark.parametrize('seconds',[True,False,0,-1,604801,1.5,'10',None])
def test_invalid_seconds_never_create_a_free_wait(seconds):
    with pytest.raises(ValidationError):
        SleepInput.model_validate({'seconds':seconds})


def test_park_clock_and_credit_are_idempotent_and_keep_physical_iterations():
    state=sleeper()
    checkpoint={'execution':state.checkpoint()}
    now=datetime.now(timezone.utc)
    deadline=arm(checkpoint,now)
    assert deadline==now+timedelta(seconds=10)
    assert checkpoint['execution']['iteration']==1
    assert checkpoint['execution']['resources']['business_iterations']==0
    assert arm(checkpoint,now+timedelta(hours=1),existing=checkpoint['durable_sleep'])==deadline
    assert checkpoint['execution']['resources']['business_iterations']==0


def test_mixed_read_sleep_keeps_business_charge_and_consumption_starts_next_segment():
    checkpoint={'execution':sleeper(mixed=True,continuous=True).checkpoint()}
    arm(checkpoint,datetime.now(timezone.utc))
    resources=checkpoint['execution']['resources']
    assert resources['business_iterations']==1
    assert resources['continuous_segment_start']==0
    state=ExecutionState.restore(checkpoint['execution'])
    row={'checkpoint':checkpoint,'wake_at':datetime.fromisoformat(checkpoint['durable_sleep']['wake_at'])}
    complete_sleep(row,{state.execution_id:state},checkpoint,trigger='timer')
    assert state.resources['continuous_segment_start']==1


def test_consumer_completes_fact_without_adding_a_duplicate_tool_message():
    state=sleeper()
    checkpoint={'execution':state.checkpoint()}
    deadline=arm(checkpoint,datetime.now(timezone.utc))
    state=ExecutionState.restore(checkpoint['execution'])
    row={'checkpoint':checkpoint,'wake_at':deadline}
    complete_sleep(row,{state.execution_id:state},checkpoint,trigger='timer')
    fact=state.tools['sleep-1']
    assert fact.phase=='completed' and fact.success and not fact.result_recorded
    assert state.pending[0].id=='sleep-1' and not state.messages
    assert checkpoint['durable_sleep']['phase']=='consumed'


@pytest.mark.parametrize('mutation',['deadline','call','identity','seconds'])
def test_armed_projection_mismatch_is_never_guessed(mutation):
    checkpoint={'execution':sleeper().checkpoint()}
    deadline=arm(checkpoint,datetime.now(timezone.utc))
    row={'checkpoint':checkpoint,'wake_at':deadline}
    if mutation=='deadline':row['wake_at']=deadline+timedelta(seconds=1)
    elif mutation=='call':checkpoint['durable_sleep']['tool_call_id']='other'
    elif mutation=='identity':checkpoint['execution']['waiting']['wait_id']='other'
    else:checkpoint['execution']['waiting']['seconds']=100
    with pytest.raises(RunnerError):validate_armed(row)


def test_sleep_never_hides_unknown_sibling_effect():
    state=sleeper()
    call=ToolCall('uncertain','send',{})
    state.tools[call.id]=ToolFact(call,phase='dispatching')
    with pytest.raises(RunnerError,match='SLEEP_UNSAFE_BOUNDARY'):
        sleep_leaf({'execution':state.checkpoint()},safe=True)


def test_multiple_sleep_calls_in_same_model_round_only_credit_once():
    state=sleeper()
    second=ToolCall('sleep-2','agent_sleep',{'seconds':5})
    state.pending.append(second)
    state.tools[second.id]=ToolFact(second)
    state.resources['round_tool_ids'].append(second.id)
    checkpoint={'execution':state.checkpoint()}
    now=datetime.now(timezone.utc)
    arm(checkpoint,now)
    assert checkpoint['execution']['resources']['business_iterations']==0
    state=ExecutionState.restore(checkpoint['execution'])
    complete_sleep({'checkpoint':checkpoint,'wake_at':now+timedelta(seconds=10)},
        {state.execution_id:state},checkpoint,trigger='timer')
    state.pending.pop(0)
    state.tools[second.id].phase='waiting'
    state.waiting={'kind':'timer','version':1,'seconds':5,'tool_call_id':second.id,
        'target_execution_id':state.execution_id}
    state.waiting['wait_id']=owned_wait_id(state.execution_id,state.execution_id,state.waiting)
    checkpoint['execution']=state.checkpoint()
    arm(checkpoint,now)
    assert checkpoint['execution']['resources']['business_iterations']==0


@pytest.mark.asyncio
async def test_acquisition_sources_get_finite_turns_under_continuous_load():
    import time
    from src.services.agent_runner.worker import RunnerWorker
    worker=object.__new__(RunnerWorker)
    worker.config=SimpleNamespace(durable_sleep_enabled=True)
    worker.factory=SimpleNamespace()
    worker.executions=SimpleNamespace()
    worker._cancel_ready_scan_at=time.monotonic()+100
    worker._acquisition_turn=0
    async def controls():return 'controls'
    async def timers():return 'timers'
    async def continuous():return 'continuous'
    async def queued():return 'queued'
    worker._acquire_controls=controls
    worker._acquire_timers=timers
    worker._acquire_continuous=continuous
    worker._acquire_queued=queued
    picked=[await worker._acquire_next() for _ in range(12)]
    assert picked==['controls','timers','continuous','queued']*3


def test_explicit_recovery_of_uncommitted_sleep_is_a_failure_not_replay():
    state=sleeper()
    fact=state.tools['sleep-1']
    assert recover_uncommitted(state,fact)
    assert fact.phase=='completed' and not fact.success and state.waiting is None
    assert fact.result['error_code']=='SLEEP_NOT_COMMITTED'
    fact.invocation_id='not-a-cloud-control'
    assert not recover_uncommitted(state,fact)


def test_reap_only_allows_latest_unstarted_timer_activation():
    state=sleeper()
    state.tools['sleep-1'].phase='completed'
    state.tools['sleep-1'].result={'wait_id':'w','trigger':'timer'}
    checkpoint={'execution':state.checkpoint(),
        'durable_sleep':{'version':1,'phase':'consumed','trigger':'timer','wait_id':'w',
            'execution_id':'runner','tool_call_id':'sleep-1'},
        'timer_activation':{'version':1,'attempt':3,'wait_id':'w','action_started':False}}
    row={'attempt':3,'checkpoint':checkpoint}
    assert replayable_activation(row)
    checkpoint['timer_activation']['action_started']=True
    assert not replayable_activation(row)
    checkpoint['timer_activation']['action_started']=False
    row['attempt']=4
    assert not replayable_activation(row)


class Control:
    def command(self):return None
    async def save(self,*args):pass


class Observer:
    async def event(self,*args):pass
    async def model_usage(self,*args):pass


class Tools:
    def definitions(self):return []
    def display_name(self,call):return call.name
    async def dispatch(self,state,call):yield DispatchResult({'success':True})


class Model:
    def __init__(self):self.calls=0
    async def complete(self,*args):
        self.calls+=1
        return {'content':'one observation','usage':{},'tool_calls':[]}


@pytest.mark.asyncio
async def test_continuous_root_guard_pauses_while_delegated_no_tool_answer_returns():
    root=ExecutionState(Identity('t','u','s'),'r',AgentMode.MASTER,'p',[],max_iterations=2,
        resources={'continuous_enabled':True})
    model=Model()
    async for _ in AgentEngine().run(root,model,Tools(),Control(),Observer()):pass
    assert model.calls==2 and root.outcome==Outcome.PAUSED
    assert root.waiting['kind']=='continuous_guard'
    assert root.resources['business_iterations']==2
    child=ExecutionState(Identity('t','u','s'),'c',AgentMode.SUBAGENT,'p',[],max_iterations=2,
        resources={'continuous_enabled':True})
    model=Model()
    async for _ in AgentEngine().run(child,model,Tools(),Control(),Observer()):pass
    assert model.calls==1 and child.outcome==Outcome.COMPLETED


@pytest.mark.asyncio
async def test_ordinary_round_limit_survives_many_physical_sleep_iterations():
    state=ExecutionState(Identity('t','u','s'),'r',AgentMode.MASTER,'p',[],max_iterations=2,
        iteration=70,resources={'business_iterations':1})
    model=Model()
    async for _ in AgentEngine().run(state,model,Tools(),Control(),Observer()):pass
    assert model.calls==1 and state.iteration==71 and state.resources['business_iterations']==2


def test_duration_checkpoint_has_constant_size_across_attempts():
    from src.services.agent_runner.durable_control import DurableControl
    from src.services.agent_runner.ownership import Attempt
    envelope={'attempt_durations_ms':{'1':100,'2':200}}
    control=DurableControl(None,Attempt('r','w',3),{'revision':0,'public_snapshot':{},
        'cancel_requested':False,'checkpoint':envelope,'status':'running'})
    assert control.capture_duration()>=300
    assert 'attempt_durations_ms' not in control.envelope
    previous=control.envelope['execution_duration_ms']
    control=DurableControl(None,Attempt('r','w',4),{'revision':0,'public_snapshot':{},
        'cancel_requested':False,'checkpoint':control.envelope,'status':'running'})
    assert control.capture_duration()>=previous
    assert len(control.envelope)==3


def test_old_attempt_trace_finally_does_not_project_a_new_attempt():
    from unittest.mock import MagicMock
    from src.services.agent_runner.worker import RunnerWorker
    worker=object.__new__(RunnerWorker)
    worker.repository=SimpleNamespace(get=lambda identifier:{'attempt':4})
    collector=MagicMock()
    worker._persist_trace(collector,'runner',SimpleNamespace(attempt=SimpleNamespace(number=3)))
    collector.on_complete.assert_not_called()


def test_attempt_trace_uses_only_local_spans_and_never_repeats_cumulative_cost():
    from unittest.mock import MagicMock
    from src.services.agent_runner.worker import RunnerWorker
    worker=object.__new__(RunnerWorker)
    worker.repository=SimpleNamespace(get=lambda identifier:{'attempt':3,'status':'waiting',
        'record_id':'stable-record','checkpoint':{'execution_duration_ms':999999,
            'execution':{'iteration':900}},'result':None})
    trace=SimpleNamespace(trace_id='tr_runner:3',spans=[SimpleNamespace(name='llm_call',
        usage={'prompt_tokens':4,'completion_tokens':2})],metadata={})
    collector=SimpleNamespace(trace=trace,on_complete=MagicMock())
    control=SimpleNamespace(attempt=SimpleNamespace(number=3),snapshot={})
    worker._persist_trace(collector,'runner',control)
    assert trace.total_tokens==6 and trace.agent_iterations==1 and trace.total_cost==0
    assert trace.metadata['record_id']=='stable-record' and 'runner_duration_ms' not in trace.metadata
    collector.on_complete.assert_called_once_with(None,persist_sync=True)
