"""Continuous attachment reads must not accumulate owner copies after history compaction."""
import json
from types import SimpleNamespace

from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact
from src.services.agent_runner.continuous_history import retire_attachment_resources
from src.services.agent_runner.resource_paths import workspace_directory


def fixture(tmp_path):
    state = ExecutionState(Identity('tenant','user','session'),'root',AgentMode.MASTER,'system',[],initial_len=0)
    control = SimpleNamespace(resource_directory=tmp_path)
    owner = workspace_directory(tmp_path,state.identity,state.execution_id)
    owner.mkdir(parents=True)
    return state,control,owner


def artifact(owner, key, *, attachment=True):
    path = owner / ('image_'+key+'.png')
    path.write_bytes(b'anonymous owner resource')
    return {'path':str(path),'sha256':key,'mime_type':'image/png','size_bytes':path.stat().st_size,
            **({'source':'local_attachment','image_ref':{'file_id':key}} if attachment else {})}


def test_retired_attachment_copy_removed_but_live_result_and_initial_image_retained(tmp_path):
    state,control,owner = fixture(tmp_path)
    retired,live,initial = [artifact(owner,key,attachment=key!='initial') for key in ('retired','live','initial')]
    state.resources['image_artifacts'] = [retired,live,initial]
    state.messages = [{'role':'tool','content':json.dumps({'data':{'attachment':{'image':{'file_id':'live'}}}})}]
    retire_attachment_resources(control,state)
    assert [item.get('sha256') for item in state.resources['image_artifacts']] == ['live','initial']
    assert not (owner/'image_retired.png').exists()
    assert (owner/'image_live.png').exists() and (owner/'image_initial.png').exists()


def test_unrecorded_tool_result_still_keeps_recovery_image_and_shared_owner_file(tmp_path):
    state,control,owner = fixture(tmp_path)
    live = artifact(owner,'shared')
    old = {**live,'image_ref':{'file_id':'old'}}
    state.resources['image_artifacts'] = [old,live]
    call = ToolCall('pending','wecom_read_session',{'session':'fixture','attachment':{'message_id':1}})
    state.tools[call.id] = ToolFact(call,phase='completed',result={'data':{'attachment':{'image':{'file_id':'shared'}}}})
    retire_attachment_resources(control,state)
    assert state.resources['image_artifacts'] == [live]
    assert (owner/'image_shared.png').exists(), 'retired reference must not delete another live reference to same bytes'
