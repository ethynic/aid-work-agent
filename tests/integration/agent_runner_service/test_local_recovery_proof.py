"""Narrow original invocation proof with real PG and original device-result DAL.

No Wecom ingress or physical client is introduced. The claimed execution and
phase checkpoint are real contract fixtures, not an actual Engine conversation.
"""
from datetime import datetime, timedelta, timezone
import uuid

import pytest

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact
from src.local_tools import repository as devices
from src.services.agent_runner.local_invocations import LocalInvocationRepository, LocalPhase
from src.services.agent_runner.local_recovery import LocalRecoveryProof
from .test_storage import storage, attempt

pytestmark=pytest.mark.integration


@pytest.mark.parametrize('tool,code,allowed',[
    ('wecom_probe','UI_CHANGED',True),
    ('boss_select_job','UI_CHANGED',False),
    ('wecom_probe','EXECUTION_UNKNOWN',False),
])
def test_known_noheal_failure_reuses_original_but_healing_or_unknown_remains_verification(
        storage,actors,service_database,tool,code,allowed):
    _,execution,submit=storage
    actor=actors['a']
    accepted,_=submit(actor)
    owned=execution.acquire('local-proof-owner',60)
    assert owned['runner_id']==accepted['runner_id']
    identity=Identity(*(owned[key] for key in ('tenant_id','user_id','session_id','source','session_kind')))
    state=ExecutionState(identity,owned['runner_id'],AgentMode.MASTER,'Fixture',[])
    call=ToolCall('proof-local-call',tool,{})
    state.tools[call.id]=ToolFact(call,phase='dispatching')
    row=execution.save_checkpoint(attempt(owned),owned['revision'],{'execution':state.checkpoint()})
    device=devices.create_device(actor.tenant_id,actor.user_id,'fixture-local-proof-'+uuid.uuid4().hex)
    try:
        phase=LocalPhase(state.execution_id,call.id,'main',0)
        row=LocalInvocationRepository(service_database.connect).bind(attempt(row),row['revision'],phase,
            device_id=str(device['id']),tool_name=tool,arguments={},deadline_at=datetime.now(timezone.utc)+timedelta(seconds=60))
        original=row['checkpoint']['execution']['resources']['local_invocations'][phase.key(row['runner_id'])]['invocation_id']
        claim='fixture-claim-hash-'+uuid.uuid4().hex
        invocation=devices.claim_next(str(device['id']),actor.tenant_id,claim,60,expected_invocation_id=original)
        assert str(invocation['id'])==original
        assert devices.mark_started(original,actor.tenant_id,claim)['state']=='running'
        terminal=devices.write_result(original,actor.tenant_id,claim,False,code=code,
            effect='unknown' if code=='EXECUTION_UNKNOWN' else 'not_applied',message='Fixture device result')
        assert terminal['state']==('unknown' if code=='EXECUTION_UNKNOWN' else 'failed')
        restored=ExecutionState.restore(row['checkpoint']['execution'])
        assert LocalRecoveryProof().can_restore(row,restored,restored.tools[call.id]) is allowed
        assert service_database.rows('SELECT count(*) AS n FROM local_tool_invocations WHERE tenant_id=%s',(actor.tenant_id,))==[{'n':1}]
        assert service_database.rows('SELECT 1 FROM client_usage_logs WHERE tenant_id=%s',(actor.tenant_id,))==[]
    finally:
        for table in ('local_tool_events','local_tool_invocations','local_tool_devices'):
            service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s',(actor.tenant_id,))
