"""Independent negative controls for phase 9 admission narrowing."""
import copy
import json
import uuid
from datetime import datetime
import pytest
from .test_storage import storage
from .test_local_wait_recovery import lost_local
from src.core.agent_engine.contracts import ExecutionState
from src.services.agent_runner.local_recovery import LocalRecoveryProof
from src.services.agent_runner.local_wait import end_waiting
from src.services.agent_runner.local_invocations import LocalPhase
from src.services.agent_runner.contracts import RunnerError
from src.local_tools import repository as devices

pytestmark=pytest.mark.integration


@pytest.mark.parametrize('code,legacy_stopped,completed', [
    ('WRONG_PAGE',False,True), ('INVALID_ARGUMENT',False,True),
    ('CANCELLED',True,False), ('INTERNAL_ERROR',True,False),
])
def test_stored_legacy_late_boolean_is_not_a_resource_stop_authority(lost_local,service_database,code,legacy_stopped,completed):
    repo,_,principal,row,device,current,claim=lost_local
    end_waiting(repo,principal,row['runner_id'])
    devices.write_result(current,row['tenant_id'],claim,False,code=code,effect='none',device_id=str(device['id']))
    service_database.rows("UPDATE local_tool_invocations SET result_json=jsonb_set(result_json,'{_late_result,stopped}',%s::jsonb) WHERE id=%s",
        (json.dumps(legacy_stopped),current))
    raw=devices.get_invocation(current,row['tenant_id'])
    assert raw['state']==raw['effect']=='unknown'
    assert devices.operation_completion_known(raw) is completed
    if completed:
        assert devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'})
    else:
        with pytest.raises(devices.DeviceOperationUnresolved):
            devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'})

@pytest.mark.parametrize('code',['CANCELLED','INTERNAL_ERROR','EXECUTION_UNKNOWN'])
def test_pending_late_transport_failure_keeps_precise_original_hold(lost_local,code):
    repo,_,principal,row,device,current,claim=lost_local
    detached=end_waiting(repo,principal,row['runner_id'])
    assert detached['checkpoint']['detached_local_invocations']==[current]
    devices.write_result(current,row['tenant_id'],claim,False,code=code,effect='none',device_id=str(device['id']))
    assert not devices.operation_completion_known(devices.get_invocation(current,row['tenant_id']))
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'})
    assert end_waiting(repo,principal,row['runner_id'])['checkpoint']['detached_local_invocations']==[current]

def test_authorized_new_device_cannot_rewrite_original_runner_recovery_binding(lost_local):
    _,_,_,row,device,current,claim=lost_local
    another=devices.create_device(row['tenant_id'],row['user_id'],uuid.uuid4().hex,machine_fingerprint_hash='independent-'+uuid.uuid4().hex)
    devices.write_result(current,row['tenant_id'],claim,True,effect='none',device_id=str(device['id']))
    state=ExecutionState.restore(row['checkpoint']['execution'])
    assert LocalRecoveryProof().can_restore(row,state,state.tools['original-call'])
    changed=copy.deepcopy(row)
    ref=next(iter(changed['checkpoint']['execution']['resources']['local_invocations'].values()))
    ref['request']['device_id']=str(another['id'])
    wrong=ExecutionState.restore(changed['checkpoint']['execution'])
    with pytest.raises(RunnerError):
        LocalRecoveryProof().can_restore(changed,wrong,wrong.tools['original-call'])

def test_legacy_detached_marker_classifies_owned_receipts_without_historical_failure_quarantine(lost_local,service_database):
    repo,_,principal,row,device,current,claim=lost_local
    end_waiting(repo,principal,row['runner_id'])
    service_database.rows("UPDATE agent_runners SET checkpoint=checkpoint-'detached_local_invocations' WHERE runner_id=%s",(row['runner_id'],))
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'})
    devices.write_result(current,row['tenant_id'],claim,False,code='WRONG_PAGE',effect='none',device_id=str(device['id']))
    assert devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'})

@pytest.mark.parametrize('code',['CANCELLED','INTERNAL_ERROR'])
def test_completed_engine_fact_does_not_prove_transport_failed_provider_is_stopped(lost_local,service_database,code):
    # Engine may finish a tool after a false/none transport result. Its phase
    # alone is not a stop witness for the external process tree.
    repo,_,principal,row,device,current,claim=lost_local
    checkpoint=copy.deepcopy(row['checkpoint'])
    root=checkpoint['execution']
    ref=copy.deepcopy(next(iter(root['resources']['local_invocations'].values())))
    phase=LocalPhase(row['runner_id'],'transport-failed-call','main',0)
    ref['tool_call_id']=phase.tool_call_id
    binding=dict(runner_id=row['runner_id'],execution_id=row['runner_id'],tool_call_id=phase.tool_call_id,branch='main',ordinal=0)
    history=devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'},
        row['session_id'],business_kind='agent_runner',business_ref=binding,dedupe_key=phase.key(row['runner_id']),
        deadline_at=datetime.fromisoformat(ref['request']['deadline_at']))
    history_claim=uuid.uuid4().hex
    devices.claim_next(str(device['id']),row['tenant_id'],history_claim,60,expected_invocation_id=history)
    devices.write_result(history,row['tenant_id'],history_claim,False,code=code,effect='none')
    assert not devices.operation_completion_known(devices.get_invocation(history,row['tenant_id']))
    ref['invocation_id']=history
    root['resources']['local_invocations'][phase.key(row['runner_id'])]=ref
    root['tools'][phase.tool_call_id]={**copy.deepcopy(root['tools']['original-call']),'phase':'completed','success':False,'result':code}
    root['tools'][phase.tool_call_id]['call']['id']=phase.tool_call_id
    service_database.rows('UPDATE agent_runners SET checkpoint=%s::jsonb WHERE runner_id=%s',(json.dumps(checkpoint),row['runner_id']))
    end_waiting(repo,principal,row['runner_id'])
    devices.write_result(current,row['tenant_id'],claim,True,effect='none',device_id=str(device['id']))
    with pytest.raises(devices.DeviceOperationUnresolved):
        devices.create_invocation(row['tenant_id'],row['user_id'],str(device['id']),'boss_goto',{'target':'recommend'})
