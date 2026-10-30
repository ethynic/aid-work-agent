"""Recovery admission, call generations and bounded artifact retirement regressions."""
import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.core.agent_engine.contracts import (AgentMode,CheckpointFailure,ExecutionState,Identity,
    Outcome,ToolCall,ToolFact)
from src.services.agent_runner import continuous_history as history
from src.services.agent_runner.continuous_context import retained_context
from src.services.agent_runner.contracts import RunnerError,canonical_json
from src.services.agent_runner.wait_identity import owned_wait_id
from tests.unit.test_agent_engine_acceptance import Model,Control,Tools,call,completion,run,state


@contextmanager
def connect():
    yield SimpleNamespace(cursor=lambda:None,commit=lambda:None)


def history_control(monkeypatch,tmp_path=None):
    archive={}
    monkeypatch.setattr(history,'lock_runner',lambda *args:{'status':'running'})
    def write(cursor,row,result):
        for message in result['messages']:
            archive.setdefault(message['_history_id'],copy.deepcopy(message))
    monkeypatch.setattr(history,'write_history',write)
    root=ExecutionState(Identity('t','u','s'),'r',AgentMode.MASTER,'system',[])
    control=SimpleNamespace(attempt=SimpleNamespace(runner_id='r'),
        repository=SimpleNamespace(connection_factory=connect),envelope={},snapshot={})
    if tmp_path is not None: control.resource_directory=tmp_path
    return root,control,archive


@pytest.mark.asyncio
async def test_fresh_model_generations_cannot_reuse_a_retired_sleep_wait():
    from tests.unit.services.agent_runner.test_durable_sleep_independent import TimerTools,consume_committed_sleep
    from src.services.agent_runner.control_repository import ControlRepository
    from src.services.agent_runner.local_invocations import LocalPhase
    current=state(max_iterations=50)
    current.resources['tool_identity_version']=1
    model=Model(completion('',[call('provider-reused','agent_sleep',{'seconds':1})]),
        *[completion('',[call('provider-reused')]) for _ in range(40)],
        completion('',[call('provider-reused','agent_sleep',{'seconds':1})]))
    await run(current,model,TimerTools())
    old=copy.deepcopy(current.waiting)
    current,_=consume_committed_sleep(current)
    await run(current,model,TimerTools())
    history.compact_facts(current)
    assert old['tool_call_id'] not in current.tools
    assert current.waiting['wait_id']!=old['wait_id']
    assert LocalPhase(current.execution_id,old['tool_call_id'],'main',0).key('r') != LocalPhase(
        current.execution_id,current.waiting['tool_call_id'],'main',0).key('r')
    with pytest.raises(RunnerError,match='CONTROL_WAIT_STALE'):
        ControlRepository.assert_wait({'checkpoint':{'execution':current.checkpoint()}},
            SimpleNamespace(action='resume',wait_id=old['wait_id'],target_execution_id=current.execution_id))


@pytest.mark.asyncio
async def test_saved_unapplied_response_uses_original_generation_without_provider_replay():
    class CrashAfterResponse(Control):
        async def save(self,current,boundary):
            await super().save(current,boundary)
            if boundary=='model_usage': raise CheckpointFailure('simulated crash')
    current=state()
    current.resources['tool_identity_version']=1
    owner=CrashAfterResponse()
    with pytest.raises(CheckpointFailure):
        await run(current,Model(completion('',[call('reused')])),control=owner)
    restored=ExecutionState.restore(next(data for boundary,data in owner.saved if boundary=='model_usage'))
    restored_again=ExecutionState.restore(restored.checkpoint())
    model1,model2=Model(completion('done')),Model(completion('done'))
    _,tools1,_,_=await run(restored,model1)
    _,tools2,_,_=await run(restored_again,model2)
    assert tools1.dispatched[0][1].id==tools2.dispatched[0][1].id
    assert tools1.dispatched[0][1].id!='reused'
    assert len(model1.requests)==len(model2.requests)==1


def test_namespace_still_rejects_duplicate_provider_ids_in_one_response():
    from src.core.agent_engine.engine import normalize_calls
    with pytest.raises(ValueError,match='DUPLICATE_TOOL_CALL_ID'):
        normalize_calls([call('duplicate'),call('duplicate')],namespace='original-model')
    assert normalize_calls([call('legacy')])[0].id=='legacy'


def test_original_large_tool_results_and_retired_facts_preserve_latest_cursor_per_target():
    current=state()
    for index,target in enumerate(['group-a','group-b','group-a']):
        item=ToolCall(str(index),'read',{'target':target,'cursor':'input-cursor'})
        result={'messages':'x'*20000,'next_cursor':'exact-opaque-'+str(index)}
        current.tools[item.id]=ToolFact(item,phase='completed',result=result,result_recorded=True)
        from src.core.agent_events import _truncate_tool_content
        current.messages.extend([{'role':'assistant','tool_calls':[item.message_call()]},
            {'role':'tool','tool_call_id':item.id,'content':_truncate_tool_content(json.dumps(result))}])
    instructions,cursors=retained_context(current,current.messages[current.initial_len:])
    assert cursors[canonical_json(['read',{'target':'group-a'}])]['/next_cursor']=='exact-opaque-2'
    assert cursors[canonical_json(['read',{'target':'group-b'}])]['/next_cursor']=='exact-opaque-1'
    for index in range(40):
        item=ToolCall('tail-'+str(index),'other',{})
        current.tools[item.id]=ToolFact(item,phase='completed',result={},result_recorded=True)
    history.compact_facts(current)
    assert '0' not in current.tools and '2' not in current.tools
    current.messages=[]
    assert retained_context(current,[])[1]==cursors


def test_artifact_window_archives_every_reference_and_retries_same_ids(monkeypatch):
    current,control,archive=history_control(monkeypatch)
    original=None
    for turn in range(200):
        current.images.extend({'file_id':f'image-{turn}-{index}','download_url':f'/images/{turn}/{index}'} for index in range(50))
        control.envelope.setdefault('presentation',{}).setdefault('downloadableFiles',[]).extend(
            {'file_id':f'file-{turn}-{index}','download_url':f'/files/{turn}/{index}'} for index in range(50))
        if turn==0: original=(copy.deepcopy(current),copy.deepcopy(control.envelope))
        history.archive_and_compact(control,current)
        assert len(current.images)<=48 and len(control.snapshot['downloadableFiles'])<=48
    metadata=[message['metadata'] for message in archive.values()]
    image_ids={image['file_id'] for item in metadata for image in item['images']} | {image['file_id'] for image in current.images}
    file_ids={file['file_id'] for item in metadata for file in item['downloadableFiles']} | {file['file_id'] for file in control.snapshot['downloadableFiles']}
    assert len(image_ids)==len(file_ids)==10000
    assert len(canonical_json(control.snapshot).encode())<32000
    count=len(archive)
    retry,control.envelope=original
    history.archive_and_compact(control,retry)
    assert len(archive)==count


def completed_child(current,tmp_path):
    from src.services.agent_runner.resource_paths import workspace_directory
    child=ExecutionState(current.identity,'child',AgentMode.SUBAGENT,'child',[],outcome=Outcome.COMPLETED)
    directory=workspace_directory(tmp_path,current.identity,'child')
    workspace=directory/'skill_ws_owned'; workspace.mkdir(parents=True)
    (workspace/'input.txt').write_text('original input')
    image=directory/'image_owned.png';image.write_bytes(b'image')
    child.resources={'workspace':str(workspace),'reply_workspaces':[str(workspace)],
        'image_artifacts':[{'path':str(image)}]}
    item=ToolCall('delegate','delegate_to_subagent',{})
    current.tools[item.id]=ToolFact(item,phase='completed',result_recorded=True)
    current.children[item.id]={'execution_id':'child','status':'completed','checkpoint':child.checkpoint()}
    for index in range(33):
        item=ToolCall('new-'+str(index),'read',{})
        current.tools[item.id]=ToolFact(item,phase='completed',result_recorded=True)
    return workspace,image


def test_closed_child_owner_is_cleaned_before_its_only_resource_record_is_removed(monkeypatch,tmp_path):
    current,control,_=history_control(monkeypatch,tmp_path)
    workspace,image=completed_child(current,tmp_path)
    history.archive_and_compact(control,current)
    assert 'delegate' not in current.children
    assert not workspace.exists() and not image.exists()


def test_cleanup_failure_preserves_closed_child_for_idempotent_retry(monkeypatch,tmp_path):
    from pathlib import Path
    current,control,_=history_control(monkeypatch,tmp_path)
    workspace,image=completed_child(current,tmp_path)
    original=Path.unlink
    def denied(path,*args,**kwargs):
        if path==image: raise PermissionError('busy')
        return original(path,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'unlink',denied)
        history.archive_and_compact(control,current)
        assert 'delegate' in current.children and workspace.exists()
    history.archive_and_compact(control,current)
    assert 'delegate' not in current.children and not workspace.exists() and not image.exists()


def test_child_workspace_still_used_by_parent_is_not_retired(monkeypatch,tmp_path):
    current,control,_=history_control(monkeypatch,tmp_path)
    workspace,image=completed_child(current,tmp_path)
    current.resources['continuous_summary']='Continue reading '+str(workspace/'input.txt')
    history.archive_and_compact(control,current)
    assert 'delegate' in current.children and workspace.exists() and image.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('code',['CREDIT_BLOCKED','USER_UNAUTHORIZED','TENANT_EXPIRED','SUBSCRIPTION_REQUIRED','PROFILE_FORBIDDEN'])
async def test_post_claim_admission_denial_keeps_consumed_sleep_and_tree_paused(monkeypatch,tmp_path,code):
    from src.config.settings import AgentRunnerConfig
    from src.services.agent_runner.worker import RunnerWorker
    from tests.unit.services.agent_runner.test_worker_execution import MemoryExecutions,_channel_row
    row=_channel_row('feishu')
    row.update(source='chat',session_kind='web',attempt=2)
    current=ExecutionState(Identity(row['tenant_id'],row['user_id'],row['session_id']),row['runner_id'],AgentMode.MASTER,'',[])
    current.tools['sleep']=ToolFact(ToolCall('sleep','agent_sleep',{'seconds':1}),phase='completed',result={'success':True})
    current.pending=[current.tools['sleep'].call]
    row['checkpoint']={'execution':current.checkpoint(),'durable_sleep':{'version':1,'phase':'consumed','wait_id':'w'},
        'timer_activation':{'version':1,'attempt':2,'wait_id':'w','action_started':False}}
    class Executions(MemoryExecutions):
        def park(self,attempt,revision,status,checkpoint,snapshot):
            self.row.update(status=status,checkpoint=copy.deepcopy(checkpoint),public_snapshot=snapshot,
                revision=revision+1,worker_id=None,lease_until=None)
            return copy.deepcopy(self.row)
    executions=Executions(row)
    finalizer=SimpleNamespace(finalize=Mock())
    factory=SimpleNamespace(resource_directory=tmp_path,create=AsyncMock())
    authorizer=SimpleNamespace(authorize_persisted=Mock(side_effect=RunnerError(code,403)),assert_credit=Mock())
    worker=RunnerWorker(AgentRunnerConfig(enabled=True),worker_id='w',execution_repository=executions,
        repository=SimpleNamespace(get=lambda _:executions.row),usage_repository=SimpleNamespace(),
        runtime_factory=factory,authorizer=authorizer,finalizer=finalizer)
    monkeypatch.setattr(worker,'_persist_trace',lambda *args:None)
    await worker.execute(row)
    assert executions.row['status']=='paused'
    assert executions.row['checkpoint']['durable_sleep']['phase']=='consumed'
    assert executions.row['checkpoint']['execution']['tools']['sleep']['phase']=='completed'
    assert executions.row['checkpoint']['execution']['resources']['admission_blocked']==code
    assert executions.row['checkpoint']['timer_activation']['action_started'] is False
    finalizer.finalize.assert_not_called()
    factory.create.assert_not_called()


def test_workspace_replaced_by_a_file_keeps_its_owner_record(monkeypatch,tmp_path):
    import shutil
    current,control,_=history_control(monkeypatch,tmp_path)
    workspace,image=completed_child(current,tmp_path)
    shutil.rmtree(workspace);workspace.write_text('unexpected file')
    history.archive_and_compact(control,current)
    assert 'delegate' in current.children and workspace.is_file()


def test_legacy_bridge_continues_diffs_after_bounded_windows_replace_prefixes():
    from src.services.agent_runner.legacy_stream_bridge import _LegacyFrameState
    owner=_LegacyFrameState()
    def row(start,end):
        return {'status':'running','snapshot':{'history_version':1,
            'images':[{'file_id':str(index)} for index in range(start,end)],
            'progressMessages':[{'type':'tool_start','toolCallId':str(index)} for index in range(start,end)]}}
    first,_=owner.project(row(0,50))
    assert len(next(event['images'] for event in first if event['type']=='images'))==50
    assert owner.project(row(2,50))[0]==[]
    next_frames,_=owner.project(row(3,51))
    assert next(event['images'] for event in next_frames if event['type']=='images')==[{'file_id':'50'}]
    assert len([event for event in next_frames if event['type']=='tool_start'])==1
    assert len(owner.images_tail)==len(owner.progress_tail)==48
    assert owner.project(row(3,51))[0]==[]


def test_legacy_cursor_without_order_is_preserved_until_a_proven_new_generation():
    current=state()
    key=canonical_json(['read',{'group':'one'}])
    current.resources['continuous_cursors']={key:{'/next_cursor':'already-consumed-2'}}
    old=ToolCall('protected-old','read',{'group':'one'})
    current.tools[old.id]=ToolFact(old,phase='completed',result={'next_cursor':'old-1'},result_recorded=True)
    assert retained_context(current,[])[1][key]['/next_cursor']=='already-consumed-2'
    current=ExecutionState.restore(current.checkpoint())
    newer=ToolCall('new-generation','read',{'group':'one'})
    current.tools[newer.id]=ToolFact(newer,phase='completed',result={'next_cursor':'new-3'},result_recorded=True)
    current.resources['tool_cursor_orders']={newer.id:[3,0]}
    current.resources['continuous_cursors']=retained_context(current,[])[1]
    assert current.resources['continuous_cursors'][key]['/next_cursor']=='new-3'
    current.tools.pop(newer.id)
    current.resources['tool_cursor_orders'].pop(newer.id)
    assert retained_context(ExecutionState.restore(current.checkpoint()),[])[1][key]['/next_cursor']=='new-3'


def test_unknown_file_in_closed_owner_namespace_keeps_owner_for_safe_retry(monkeypatch,tmp_path):
    current,control,_=history_control(monkeypatch,tmp_path)
    workspace,image=completed_child(current,tmp_path)
    unknown=workspace.parent/'unknown-business-file';unknown.write_text('do not recursively remove unknown files')
    history.archive_and_compact(control,current)
    assert 'delegate' in current.children and unknown.exists()
    unknown.unlink()
    history.archive_and_compact(control,current)
    assert 'delegate' not in current.children and not workspace.parent.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('unapplied',[False,True])
async def test_newly_paired_legacy_calls_advance_cursor_without_renaming_after_upgrade(unapplied):
    from src.core.agent_engine.contracts import DispatchResult
    current=state(max_iterations=5)
    current.iteration=4
    current.resources.update(tool_identity_version=1,business_iterations=4)
    key=canonical_json(['read',{'group':'one'}])
    current.resources['continuous_cursors']={key:{'/next_cursor':'cursor-9'}}
    calls=[ToolCall('old-raw-'+str(index),'read',{'group':'one'}) for index in range(2)]
    if unapplied:
        current.model_calls=[{'call_id':'old-receipt','execution_id':current.execution_id,'iteration':4,
            'phase':'completed','applied':False,'response':completion('',[call(item.id,item.name,item.arguments) for item in calls])}]
    else:
        current.pending=list(calls)
        current.tools={item.id:ToolFact(item) for item in calls}
    class ProgressTools(Tools):
        async def dispatch(self,current,item):
            yield DispatchResult({'next_cursor':'cursor-'+str(10+int(item.id[-1]))})
    await run(current,Model(completion('done')),ProgressTools())
    assert set(item.id for item in calls)<=set(current.tools)
    assert retained_context(current,[])[1][key]['/next_cursor']=='cursor-11'
    current.resources['continuous_cursors']=retained_context(current,[])[1]
    restored=ExecutionState.restore(current.checkpoint())
    assert retained_context(restored,[])[1][key]['/next_cursor']=='cursor-11'
    assert restored.resources['tool_cursor_orders'][calls[0].id]<restored.resources['tool_cursor_orders'][calls[1].id]


@pytest.mark.asyncio
async def test_legacy_first_pair_checkpoint_restart_keeps_order_and_does_not_redispatch_old_result():
    from src.core.agent_engine.contracts import DispatchResult
    current=state(max_iterations=5);current.iteration=4
    current.resources.update(tool_identity_version=1,business_iterations=4)
    calls=[ToolCall('old-'+str(index),'read',{'group':'one'}) for index in range(2)]
    current.pending=list(calls);current.tools={item.id:ToolFact(item) for item in calls}
    key=canonical_json(['read',{'group':'one'}]);current.resources['continuous_cursors']={key:{'/next_cursor':'cursor-9'}}
    class CrashAfterPair(Control):
        async def save(self,current,boundary):
            await super().save(current,boundary)
            if boundary=='tool_completed': raise CheckpointFailure('crash after committed first pair')
    class ProgressTools(Tools):
        async def dispatch(self,current,item):
            self.dispatched.append(item.id)
            yield DispatchResult({'next_cursor':'cursor-'+str(10+int(item.id[-1]))})
    owner=CrashAfterPair();first=ProgressTools()
    with pytest.raises(CheckpointFailure): await run(current,Model(),first,owner)
    saved=next(data for boundary,data in owner.saved if boundary=='tool_completed')
    restored=ExecutionState.restore(saved)
    assert retained_context(restored,[])[1][key]['/next_cursor']=='cursor-10'
    second=ProgressTools();await run(restored,Model(completion('done')),second)
    assert first.dispatched==['old-0'] and second.dispatched==['old-1']
    assert retained_context(restored,[])[1][key]['/next_cursor']=='cursor-11'
    assert restored.resources['tool_cursor_orders']['old-0']==saved['resources']['tool_cursor_orders']['old-0']


@pytest.mark.asyncio
@pytest.mark.parametrize('version,accepted',[('missing',True),(1,True),(True,False),('1',False),(2,False),(None,False)])
async def test_runtime_identity_version_gate_accepts_only_exact_supported_integer(monkeypatch,version,accepted):
    from src.services.agent_runner.runtime.executor import RuntimeExecution
    from src.services.agent_runner.profiles import MainProfileCatalog
    current=state()
    if version!='missing': current.resources['tool_identity_version']=version
    runtime=RuntimeExecution.__new__(RuntimeExecution)
    runtime.assembler=SimpleNamespace(prepare=AsyncMock(return_value=(current,[])))
    runtime.resources=SimpleNamespace(execution_id=current.execution_id,subagent_config=None)
    runtime.initial_followup_inputs=[]
    runtime.control=SimpleNamespace(durable_owner=True,envelope={'business_round_policy':{}},intent={})
    runtime.supports_sleep=True
    def passed(*args): raise RuntimeError('IDENTITY_GATE_PASSED')
    monkeypatch.setattr(MainProfileCatalog,'resolve',passed)
    error,match=(RuntimeError,'IDENTITY_GATE_PASSED') if accepted else (
        ValueError,'RECOVERY_TOOL_IDENTITY_VERSION_UNSUPPORTED')
    with pytest.raises(error,match=match):
        async for _ in runtime.run('test'): pass


@pytest.mark.parametrize('versioned_position',[False,True])
def test_legacy_recorded_pairs_survive_retirement_without_overriding_new_versioned_progress(versioned_position):
    from src.core.agent_events import _truncate_tool_content
    current=state();current.iteration=10
    key=canonical_json(['read',{'target':'group'}])
    current.resources.update(tool_identity_version=1,
        continuous_cursors={key:{'/next_cursor':'versioned-12' if versioned_position else 'archived-9'}})
    if versioned_position: current.resources['continuous_cursor_orders']={key:[12,0]}
    for index in range(2):
        item=ToolCall('legacy-'+str(index),'read',{'target':'group','cursor':'archived-9'})
        result={'data':'x'*20000,'next_cursor':'completed-'+str(10+index)}
        current.tools[item.id]=ToolFact(item,phase='completed',result=result,result_recorded=True)
        current.messages.extend([{'role':'assistant','tool_calls':[item.message_call()]},
            {'role':'tool','tool_call_id':item.id,'content':_truncate_tool_content(json.dumps(result))}])
    orphan=ToolCall('protected-orphan','read',{'target':'group'})
    current.tools[orphan.id]=ToolFact(orphan,phase='completed',result={'next_cursor':'orphan-1'},result_recorded=True)
    current.resources['round_tool_ids']=[orphan.id]
    for index in range(40):
        item=ToolCall('other-'+str(index),'other',{})
        current.tools[item.id]=ToolFact(item,phase='completed',result={},result_recorded=True)
    history.compact_facts(current)
    expected='versioned-12' if versioned_position else 'completed-11'
    assert current.resources['continuous_cursors'][key]['/next_cursor']==expected
    assert 'legacy-0' not in current.tools and 'legacy-1' not in current.tools
    restored=ExecutionState.restore(current.checkpoint())
    history.compact_facts(restored)
    assert restored.resources['continuous_cursors'][key]['/next_cursor']==expected
    assert orphan.id in restored.tools


@pytest.mark.parametrize('proof',['current-pair','covered-prefix','missing-declaration','non-assistant-declaration'])
def test_legacy_current_pair_proof_excludes_already_summarized_and_unpaired_results(proof):
    current=state();key=canonical_json(['read',{'target':'group'}])
    current.resources.update(history_offset=8,continuous_cursors={key:{'/next_cursor':'saved-9'}})
    item=ToolCall('legacy-completed','read',{'target':'group'})
    current.tools[item.id]=ToolFact(item,phase='completed',result={'next_cursor':'result-10'},result_recorded=True)
    if proof!='missing-declaration':
        current.messages.append({'role':'user' if proof=='non-assistant-declaration' else 'assistant',
            'tool_calls':[item.message_call()]})
    current.messages.append({'role':'tool','tool_call_id':item.id,'content':'truncated invalid JSON'})
    if proof=='covered-prefix': current.resources['continuous_summary_through']=10
    value=retained_context(current,current.messages[current.initial_len:])[1][key]['/next_cursor']
    assert value==('result-10' if proof=='current-pair' else 'saved-9')
