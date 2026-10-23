"""Independent red tests for the four reported recovery counterexamples."""
import copy
import json
import uuid
from datetime import datetime
import pytest
from .test_storage import storage
from .test_local_wait_recovery import lost_local
from src.local_tools import repository as devices
from src.services.agent_runner.local_invocations import LocalPhase
from src.services.agent_runner.local_wait import end_waiting

pytestmark=pytest.mark.integration

@pytest.mark.parametrize('code',['WRONG_PAGE','INVALID_ARGUMENT'])
def test_completed_historical_none_failure_cannot_quarantine_after_current_is_settled(lost_local,service_database,code):
    repo,_,principal,row,device,current,claim=lost_local
    checkpoint=copy.deepcopy(row['checkpoint'])
    root=checkpoint['execution']
    refs=root['resources']['local_invocations']
    original=next(iter(refs.values()))
    phase=LocalPhase(row['runner_id'],'completed-historical-call','main',0)
    ref={k:v for k,v in original.items()}
    ref['tool_call_id']=phase.tool_call_id
    request=ref['request']
    binding=dict(runner_id=row['runner_id'],execution_id=row['runner_id'],tool_call_id=phase.tool_call_id,branch='main',ordinal=0)
    history=devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),request['tool_name'],request['arguments'],row['session_id'],
        business_kind='agent_runner',business_ref=binding,dedupe_key=phase.key(row['runner_id']),
        deadline_at=datetime.fromisoformat(request['deadline_at']))
    history_claim=uuid.uuid4().hex
    devices.claim_next(str(device['id']),row['tenant_id'],history_claim,60,expected_invocation_id=history)
    devices.mark_started(history,row['tenant_id'],history_claim)
    assert devices.write_result(history,row['tenant_id'],history_claim,False,code=code,effect='none')['state']=='failed'
    ref['invocation_id']=history
    refs[phase.key(row['runner_id'])]=ref
    root['tools'][phase.tool_call_id]={**copy.deepcopy(root['tools']['original-call']),'phase':'completed'}
    root['tools'][phase.tool_call_id]['call']['id']=phase.tool_call_id
    service_database.rows('UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s',(json.dumps(checkpoint),row['runner_id']))
    end_waiting(repo,principal,row['runner_id'])
    devices.write_result(current,row['tenant_id'],claim,True,effect='none',device_id=str(device['id']))
    # Only the now-settled current unknown was a hold; history must not trap it.
    assert devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'})

@pytest.mark.parametrize('code',['WRONG_PAGE','INVALID_ARGUMENT'])
def test_late_known_none_failure_is_observed_as_failure_and_releases_execution_hold(lost_local,code):
    repo,_,principal,row,device,current,claim=lost_local
    end_waiting(repo,principal,row['runner_id'])
    devices.write_result(current,row['tenant_id'],claim,False,code=code,effect='none',device_id=str(device['id']))
    raw=devices.get_invocation(current,row['tenant_id'])
    assert raw['state']=='unknown' # Preserve original lease-loss audit fact.
    observed=devices.observed_invocation(raw)
    assert observed['state']=='failed' and observed['effect']=='none' and observed['error_code']==code
    assert devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'})

@pytest.mark.parametrize('missing',['original','target','both'])
def test_missing_fingerprint_does_not_prove_independent_device_is_same_machine(lost_local,service_database,missing):
    repo,_,principal,row,device,current,claim=lost_local
    if missing in {'original','both'}:
        service_database.rows('UPDATE local_tool_devices SET machine_fingerprint_hash=NULL WHERE id=%s',(device['id'],))
    other=devices.create_device(row['tenant_id'],row['user_id'],uuid.uuid4().hex,
        machine_fingerprint_hash=None if missing in {'target','both'} else 'independent-machine-'+uuid.uuid4().hex)
    end_waiting(repo,principal,row['runner_id'])
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'})
    assert devices.create_invocation(row['tenant_id'],row['user_id'],str(other['id']),'boss_goto',{'target':'recommend'})

def test_new_authorized_runner_can_change_device_after_session_history_login_failure(lost_local,storage,actors):
    _,_,_,row,device,_,_=lost_local
    failed=devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'},row['session_id'])
    claim=uuid.uuid4().hex
    devices.claim_next(str(device['id']),row['tenant_id'],claim,60,expected_invocation_id=failed)
    devices.write_result(failed,row['tenant_id'],claim,False,code='NOT_LOGGED_IN',effect='none')
    another=devices.create_device(row['tenant_id'],row['user_id'],uuid.uuid4().hex,machine_fingerprint_hash='another-machine-'+uuid.uuid4().hex)
    new_runner,_=storage[2](actors['a'])
    assert new_runner['runner_id']!=row['runner_id']
    assert devices.create_invocation(row['tenant_id'],row['user_id'],str(another['id']),'boss_goto',{'target':'recommend'},row['session_id'],
        business_kind='agent_runner',business_ref={'runner_id':new_runner['runner_id']},dedupe_key=uuid.uuid4().hex)
