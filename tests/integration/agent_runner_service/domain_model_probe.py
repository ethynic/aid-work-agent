"""Narrow real-PG domain-model authorization; no Engine conversation claimed.

Initial native execution/tool contracts are declared fixtures. Lifecycle,
dispatch fence, UsageScope, Gateway/provider HTTP and pricing are production.
No completed ToolFact/provider result is fabricated. Domain receipt mismatches
reuse actual responses from a separately completed physical call.
"""
import asyncio
import copy
from decimal import Decimal
import json
from pathlib import Path
import sys
import time
import uuid


def main():
    from src.db.database import init_postgres_pool, close_postgres_pool
    from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact, Outcome, CheckpointFailure
    from src.core.execution_usage import execution_token_usage
    from src.services.agent_runner.repository import RunnerRepository
    from src.services.agent_runner.execution_repository import ExecutionRepository
    from src.services.agent_runner.recovery_repository import RecoveryRepository
    from src.services.agent_runner.durable_control import DurableControl
    from src.services.agent_runner.local_owner import RunnerLocalLifecycle
    from src.services.agent_runner.local_invocations import LocalPhase
    from src.services.agent_runner.ownership import Attempt, StopRequested
    from src.services.agent_runner.usage_repository import UsageRepository
    from src.services.agent_runner.usage_pricing import grouped_cost
    from src.services.agent_runner.usage_context import UsageScope, runner_usage_scope
    from src.services.agent_runner.domain_usage import domain_model_permit, physical_authorization
    from src.services.agent_runner.authorization import RunnerAuthorizer
    from src.config.settings import settings
    from src.llm.gateway import llm_gateway
    from src.llm.call_observer import provider_call_purpose
    from src.tools.context import ExecutionContextFactory, tool_execution_scope

    mode, identifier, marker, model, directory = sys.argv[1:6]
    gate = Path(directory)
    assert gate.is_absolute() and gate.is_dir()
    init_postgres_pool()
    repository, execution, usage = RunnerRepository(), ExecutionRepository(), UsageRepository()

    def receipts_for(target):
        with repository.connection_factory() as connection:
            return usage.facts_in_tx(connection.cursor(),target)

    def prepare(target, tool='boss_select_job', call_id='domain-model-call'):
        owned = execution.acquire('fixture-domain-model-'+uuid.uuid4().hex,120)
        assert owned['runner_id'] == target
        identity = Identity(*(owned[key] for key in ('tenant_id','user_id','session_id','source','session_kind')))
        state = ExecutionState(identity, target, AgentMode.STANDALONE, 'Declared owner contract fixture', [], profile_id=owned['profile_id'])
        state.tools[call_id] = ToolFact(ToolCall(call_id, tool, {}), phase='dispatching')
        attempt = Attempt(target,owned['worker_id'],owned['attempt'])
        owned = execution.save_checkpoint(attempt,owned['revision'],{'execution':state.checkpoint()})
        return attach(owned,state,call_id)

    def attach(owned,state,call_id):
        attempt = Attempt(owned['runner_id'],owned['worker_id'],owned['attempt'])
        control = DurableControl(execution,attempt,owned)
        control.state = state
        async def authorize(profile_id=None):
            await asyncio.to_thread(RunnerAuthorizer(settings.agent_runner).authorize_persisted,repository.get(owned['runner_id']),execute=True)
        control.authorization_check = authorize
        scope = UsageScope(attempt,usage,state.execution_id,tool_call_id=call_id)
        control.usage_scope = scope
        context = ExecutionContextFactory.for_agent_call(tenant_id=state.identity.tenant_id,
            user_id=state.identity.user_id,session_id=state.identity.session_id,
            agent_execution_id=state.execution_id,tool_call_id=call_id,env_vars={},infer_legacy_identity=False)
        return control,state,RunnerLocalLifecycle(control,state,call_id),scope,context

    async def wait_file(name):
        deadline = time.monotonic()+30
        while not (gate/name).exists():
            assert time.monotonic()<deadline, 'DOMAIN_MODEL_PROBE_GATE_TIMEOUT'
            await asyncio.sleep(.025)

    async def physical(text=marker):
        return await llm_gateway.chat_lite(messages=[{'role':'user','content':text}],temperature=0)

    async def run():
        tool = {'covered-detail':'boss_resume_detail','covered-batch':'boss_resume_batch'}.get(mode,'boss_select_job')
        control,state,owner,scope,context = prepare(identifier,tool)
        branch = 'resume.evaluate' if mode.startswith('covered-') else 'heal.choice'
        purpose = 'resume_recognition_covered' if mode.startswith('covered-') else 'llm'
        intent = {'fixture_input':marker}
        observed_error = None
        with runner_usage_scope(scope), tool_execution_scope(context):
            if mode.startswith('covered-') or mode=='cancel-response':
                await owner.model_phase(branch=branch,ordinal=0,intent=intent,invoke=physical,purpose=purpose)
                if mode=='cancel-response':
                    try:
                        await owner.model_phase(branch=branch,ordinal=1,intent={'new_phase':True},invoke=physical)
                    except StopRequested as error:
                        observed_error = str(error)
                    assert observed_error=='RUNNER_CANCEL_REQUESTED'
            elif mode in {'hint-purpose','hint-skill'}:
                scope.skill_boundary = mode=='hint-skill'
                try:
                    with provider_call_purpose('resume_recognition_covered'):
                        await physical()
                except CheckpointFailure as error:
                    observed_error = str(error)
                assert observed_error=='COVERED_USAGE_OWNER_REQUIRED'
            elif mode=='client-hint':
                await physical(marker+' '+json.dumps({'purpose':'resume_recognition_covered','covered':True}))
            elif mode in {'foreign-receipt','sibling-receipt'}:
                if mode=='foreign-receipt':
                    other_id = sys.argv[6]
                    other = prepare(other_id,call_id='other-domain-model-call')
                    other_control,other_state,other_owner,other_scope,other_context = other
                    with runner_usage_scope(other_scope),tool_execution_scope(other_context):
                        response = await other_owner.model_phase(branch='heal.choice',ordinal=0,
                            intent=intent,invoke=physical)
                    target_ordinal=0
                else:
                    response = await owner.model_phase(branch='heal.choice',ordinal=0,intent=intent,invoke=physical)
                    target_ordinal=1
                async def previous_actual_response():
                    return response # Genuine original HTTP response, unchanged.
                try:
                    await owner.model_phase(branch='heal.choice',ordinal=target_ordinal,
                        intent={'different_phase':True},invoke=previous_actual_response)
                except CheckpointFailure as error:
                    observed_error = str(error)
                assert observed_error=='LOCAL_MODEL_RECEIPT_OWNER_MISMATCH'
            elif mode=='rearm-none':
                async def pause_before_real_receipt_start():
                    (gate/'before-io').touch()
                    await wait_file('release-before-io')
                    return await physical()
                try:
                    await owner.model_phase(branch=branch,ordinal=0,intent=intent,invoke=pause_before_real_receipt_start)
                except StopRequested as error:
                    assert error.command=='pause'
                else:
                    raise AssertionError('REAL_RECEIPT_START_WAS_NOT_BLOCKED')
                assert receipts_for(identifier)==[]
                # Native safe-checkpoint fixture: actual receipt.start refused
                # dispatch, hence no physical effect exists. No Engine pause
                # behavior is inferred from this contract integration.
                state.outcome=Outcome.PAUSED
                state.tools[context.tool_call_id].phase='waiting'
                await control.save(state,'contract_zero_io_safe_pause')
                parked=RecoveryRepository().acknowledge_pause(control.attempt,control.revision,
                    control.envelope,control.snapshot)
                (gate/'paused.json').write_text(json.dumps({'runner_id':identifier,'receipt_count':0}))
                await wait_file('resume-ready')
                parked=repository.get(identifier)
                restored=copy.deepcopy(parked['checkpoint'])
                restored['applied_control_id']=parked['resume_control_id']
                resumed=RecoveryRepository().claim_resume(identifier,'fixture-domain-rearm-owner',120,
                    revision=parked['revision'],control_id=parked['resume_control_id'],checkpoint=restored)
                assert resumed is not None
                state=ExecutionState.restore(resumed['checkpoint']['execution'])
                control,state,owner,scope,context=attach(resumed,state,context.tool_call_id)
                with runner_usage_scope(scope),tool_execution_scope(context):
                    first=await owner.model_phase(branch=branch,ordinal=0,intent=intent,invoke=physical)
                    replay=await owner.model_phase(branch=branch,ordinal=0,intent=intent,invoke=physical)
                    assert first==replay
            elif mode.startswith('rearm-'):
                phase=LocalPhase(state.execution_id,context.tool_call_id,branch,0)
                start_intent={**intent,'model_purpose':'llm','model_phase_version':1}
                await owner.start_domain(branch=branch,ordinal=0,intent=start_intent)
                receipt_phase=mode.removeprefix('rearm-')
                with domain_model_permit(control.attempt,phase,'llm'),provider_call_purpose('llm'):
                    if receipt_phase in {'started','old-attempt'}:
                        call_id='declared-physical-start-'+uuid.uuid4().hex
                        auth=physical_authorization(control.attempt,state.execution_id,context.tool_call_id,'llm',call_id)
                        usage.start(control.attempt,call_id=call_id,execution_id=state.execution_id,
                            tool_call_id=context.tool_call_id,provider='qwen',model=model,
                            purpose='llm',domain_authorization=auth)
                    else:
                        await physical() # True HTTP; reported/unknown usage is external IO.
                if receipt_phase=='old-attempt':
                    # Deliberate epoch replacement contract fixture. Not a
                    # public resume/preflight success or Engine recovery claim.
                    with repository.connection_factory() as connection:
                        cursor=connection.cursor()
                        cursor.execute("UPDATE agent_runners SET worker_id='fixture-replacement-model-owner',attempt=attempt+1 WHERE runner_id=%s RETURNING *",(identifier,))
                        from src.services.agent_runner.repository import decoded
                        replacement=decoded(cursor.fetchone());connection.commit()
                    control,state,owner,scope,context=attach(replacement,state,context.tool_call_id)
                try:
                    await owner.model_phase(branch=branch,ordinal=0,intent=intent,invoke=physical)
                except Exception as error:
                    observed_error=str(error)
                assert observed_error=='LOCAL_MODEL_RESPONSE_VERIFICATION_REQUIRED'
            else:
                raise ValueError('UNKNOWN_DOMAIN_MODEL_PROBE_MODE')
        row=repository.get(identifier)
        receipts=receipts_for(identifier)
        cost,totals,_=grouped_cost(receipts)
        report={'mode':mode,'error_code':observed_error,'attempt':row['attempt'],
            'cost':str(cost),'totals':totals,'display':execution_token_usage(row['checkpoint']['execution']),
            'model_calls':len(row['checkpoint']['execution'].get('model_calls',[])),
            'domain':[{'branch':f['branch'],'ordinal':f['ordinal'],'phase':f['phase'],
                       'authorized_attempt':f.get('authorized_attempt')} for f in
                      row['checkpoint']['execution'].get('resources',{}).get('local_domain_phases',{}).values()],
            'receipts':[{'phase':f['phase'],'purpose':f['purpose'],'authorized_attempt':f['authorized_attempt'],
                        'price_covered':f['price_snapshot'].get('covered_cost')} for f in receipts]}
        (gate/'report.json').write_text(json.dumps(report))

    try:
        asyncio.run(run())
    finally:
        close_postgres_pool()


if __name__=='__main__':
    main()
