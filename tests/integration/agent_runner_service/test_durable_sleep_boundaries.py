"""Real PostgreSQL coverage for post-claim admission and long-lived counters."""
import copy
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
import uuid

import pytest
import yaml

from src.config.settings import AgentRunnerConfig
from src.core.agent_engine.contracts import AgentMode,ExecutionState,Outcome,ToolCall,ToolFact
from src.services.agent_runner.contracts import RunnerError
from src.services.agent_runner.control_contracts import RunnerControl
from src.services.agent_runner.control_repository import ControlRepository
from src.services.agent_runner.continuous_history import archive_and_compact
from src.services.agent_runner.durable_control import DurableControl
from src.services.agent_runner.recovery import RecoveryCoordinator
from src.services.agent_runner.recovery_repository import RecoveryRepository
from src.services.agent_runner.runtime_manifest import configuration_fingerprint
from src.services.agent_runner.timer_repository import TimerRepository
from src.services.agent_runner.wait_identity import owned_wait_id
from src.services.agent_runner.worker import RunnerWorker
from .test_storage import storage,attempt
from .test_durable_sleep_independent import sleeping_checkpoint

pytestmark=pytest.mark.integration


def claimed_timer(storage,actor,database,child):
    repository,execution,submit=storage
    _,principal=submit(actor)
    owned=execution.acquire('original-before-admission',30)
    leaf=ExecutionState.restore(sleeping_checkpoint(owned)['execution'])
    leaf.resources['configuration_fingerprint']=configuration_fingerprint('fixture-profile')
    leaf.role,leaf.profile_id=AgentMode.MASTER,'main'
    root=leaf
    if child:
        leaf.execution_id,leaf.role,leaf.profile_id='original-child',AgentMode.SUBAGENT,'fixture-child'
        leaf.waiting['target_execution_id']=leaf.execution_id
        leaf.waiting['wait_id']=owned_wait_id(owned['runner_id'],leaf.execution_id,leaf.waiting)
        root=ExecutionState(principal.identity,owned['runner_id'],AgentMode.MASTER,'',[],
            profile_id='main',outcome=Outcome.WAITING,
            resources={'configuration_fingerprint':configuration_fingerprint('fixture-profile')})
        call=ToolCall('delegate','delegate_to_subagent',{'subagent_name':'fixture-child'})
        root.pending=[call];root.tools[call.id]=ToolFact(call,phase='waiting')
        root.children[call.id]={'execution_id':leaf.execution_id,'status':'waiting',
            'profile_id':leaf.profile_id,'profile_fingerprint':'fixture-profile',
            'task_record':{'execution_id':leaf.execution_id,'task_id':'original-child-task','subagent_name':'fixture-child'},
            'checkpoint':leaf.checkpoint()}
        root.waiting={'kind':'child_wait','tool_call_id':call.id,'execution_id':leaf.execution_id,
            'child_wait':copy.deepcopy(leaf.waiting)}
    parked=execution.park(attempt(owned),owned['revision'],'waiting',{'execution':root.checkpoint()},{})
    checkpoint=copy.deepcopy(parked['checkpoint'])
    deadline=datetime.now(timezone.utc)-timedelta(seconds=1)
    checkpoint['durable_sleep']['wake_at']=deadline.isoformat()
    target=checkpoint['execution']['children']['delegate']['checkpoint'] if child else checkpoint['execution']
    target['waiting']['wake_at']=deadline.isoformat()
    database.rows('UPDATE agent_runners SET wake_at=%s,checkpoint=%s::jsonb WHERE runner_id=%s',
        (deadline,json.dumps(checkpoint),owned['runner_id']))
    due=repository.get(owned['runner_id'])
    authority=SimpleNamespace(authorize_persisted=lambda *a,**k:principal,assert_credit=lambda *a:None,
        authorize_child_profile=lambda *a:None)
    coordinator=RecoveryCoordinator(authority,SimpleNamespace(resolve=lambda *a:(None,'fixture-profile')))
    prepared=coordinator.prepare(due,timer_trigger=True).checkpoint
    return TimerRepository(database.connect).claim(due,'claimed-before-admission',30,prepared),principal,coordinator


@pytest.mark.asyncio
@pytest.mark.parametrize('child',[False,True])
@pytest.mark.parametrize('next_action',['resume','cancel'])
async def test_claimed_sleep_credit_change_pauses_original_tree_then_explicit_resume_or_cancel(
        storage,actors,service_database,monkeypatch,tmp_path,child,next_action):
    from src.config.settings import settings
    monkeypatch.setattr(settings.agent_runner,'durable_sleep_enabled',True)
    row,principal,coordinator=claimed_timer(storage,actors['a'],service_database,child)
    finalizer=SimpleNamespace(finalize=Mock())
    factory=SimpleNamespace(resource_directory=tmp_path,create=AsyncMock())
    authority=SimpleNamespace(authorize_persisted=lambda *a,**k:principal,
        assert_credit=Mock(side_effect=RunnerError('CREDIT_BLOCKED',402)))
    worker=RunnerWorker(AgentRunnerConfig(enabled=True),worker_id=row['worker_id'],
        execution_repository=storage[1],repository=storage[0],usage_repository=SimpleNamespace(),
        runtime_factory=factory,authorizer=authority,finalizer=finalizer)
    monkeypatch.setattr(worker,'_persist_trace',lambda *args:None)
    await worker.execute(row)
    paused=storage[0].get(row['runner_id'])
    assert paused['status']=='paused' and paused['wake_at'] is None and paused['worker_id'] is None
    assert paused['checkpoint']['durable_sleep']['phase']=='consumed'
    leaf=paused['checkpoint']['execution']['children']['delegate']['checkpoint'] if child else paused['checkpoint']['execution']
    assert leaf['tools']['independent-sleep-1']['phase']=='completed'
    assert not leaf['tools']['independent-sleep-1']['result_recorded']
    assert service_database.rows('SELECT owner_runner_id FROM agent_runner_session_claims WHERE owner_runner_id=%s',(row['runner_id'],))
    assert not TimerRepository(service_database.connect).next_candidates()
    factory.create.assert_not_called();finalizer.finalize.assert_not_called()
    if next_action=='cancel':
        storage[0].cancel(principal,row['runner_id'])
        closing=storage[1].acquire('explicit-cancel-after-denial',30)
        assert closing['runner_id']==row['runner_id'] and closing['status']=='finalizing' and closing['cancel_only']
    else:
        control,_=ControlRepository(service_database.connect).submit(principal,row['runner_id'],
            RunnerControl(client_request_id=uuid.uuid4().hex,action='resume'))
        candidate=storage[0].get(row['runner_id'])
        restored=coordinator.prepare(candidate,control).checkpoint
        resumed=RecoveryRepository(service_database.connect).claim_resume(row['runner_id'],'funded-resume',30,
            revision=candidate['revision'],control_id=control['control_id'],checkpoint=restored)
        assert resumed['status']=='running' and resumed['attempt']==row['attempt']+1
        after=resumed['checkpoint']['execution']['children']['delegate']['checkpoint'] if child else resumed['checkpoint']['execution']
        assert after['execution_id']==leaf['execution_id'] and after['pending']==leaf['pending']
        assert after['tools']['independent-sleep-1']==leaf['tools']['independent-sleep-1']


def test_legacy_int_counters_migrate_twice_and_real_settlement_accepts_values_above_int32(storage,actors,service_database):
    from src.services.agent_runner.transaction_repository import settle_record
    _,_,submit=storage
    row,_=submit(actors['a'])
    columns=['total_token_count','prompt_tokens','completion_tokens','cached_input_tokens',
        'agent_iterations','duration_ms','embedding_tokens','asr_calls']
    root=Path(__file__).resolve().parents[3]
    source=(root/'deploy/init-postgres.sql').read_text(encoding='utf8')
    start=source.index('CREATE TABLE IF NOT EXISTS chat_records (')
    schema=source[start:source.index('\n);',start)+3]
    migration=next(item['statements'] for item in yaml.safe_load((root/'deploy/db_update.yaml').read_text(encoding='utf8'))
        if item['datetime']=='2026-10-22 23:10:00')
    # Only this connection's temp table is shadowed. The real tenant and Runner
    # remain in the disposable database to exercise settle_record unchanged.
    with service_database.connect() as connection:
        cursor=connection.cursor()
        cursor.execute(schema.replace('CREATE TABLE IF NOT EXISTS chat_records','CREATE TEMP TABLE chat_records').replace('BIGINT','INTEGER'))
        cursor.execute('INSERT INTO chat_records(record_id,tenant_id,session_id,user_message,credit_cost) VALUES(%s,%s,%s,%s,0)',
            (row['record_id'],row['tenant_id'],row['session_id'],'legacy value retained'))
        cursor.execute(migration);cursor.execute(migration)
        cursor.execute("SELECT data_type FROM information_schema.columns WHERE table_name='chat_records' AND table_schema LIKE 'pg_temp%%' AND column_name=ANY(%s)",(columns,))
        types=cursor.fetchall()
        assert len(types)==len(columns) and all(item['data_type']=='bigint' for item in types)
        large=2**31+17
        totals={column:large for column in columns if column not in {'agent_iterations','duration_ms'}}
        runner={**row,'checkpoint':{'execution':{'iteration':large}}}
        assert settle_record(cursor,runner,{'status':'running','duration_ms':large},Decimal('0'),totals,{'groups':[]})==0
        assert settle_record(cursor,runner,{'status':'running','duration_ms':large},Decimal('0'),totals,{'groups':[]})==0
        cursor.execute('SELECT * FROM chat_records WHERE record_id=%s',(row['record_id'],))
        saved=cursor.fetchone()
        assert all(saved[column]==large for column in columns)
        assert saved['user_message']=='legacy value retained'
        cursor.execute('DROP TABLE pg_temp.chat_records')
    for relative in ['deploy/init-postgres.sql','deploy/nuodu/init-postgres.sql']:
        text=(root/relative).read_text(encoding='utf8');part=text[text.index('CREATE TABLE IF NOT EXISTS chat_records ('):]
        part=part[:part.index('\n);')]
        assert all(column+' BIGINT' in part for column in columns)


def test_artifact_archive_survives_crash_retry_and_projects_monotone_history_revision(storage,actors,service_database):
    from src.services.agent_runner.public_view import public_runner
    repository,execution,submit=storage
    _,principal=submit(actors['a'])
    row=execution.acquire('artifact-owner',30)
    control=DurableControl(execution,attempt(row),row)
    current=ExecutionState(principal.identity,row['runner_id'],AgentMode.MASTER,'',[])
    current.images=[{'file_id':f'image-{i}','download_url':f'/images/{i}'} for i in range(50)]
    control.envelope['presentation']={'downloadableFiles':[{'file_id':f'file-{i}','download_url':f'/files/{i}'} for i in range(50)]}
    original,original_envelope=copy.deepcopy(current),copy.deepcopy(control.envelope)
    archive_and_compact(control,current)
    control._capture(current)
    first=control.snapshot['history_revision']
    assert first>0
    retry=DurableControl(execution,attempt(row),row);retry.envelope=original_envelope
    archive_and_compact(retry,original);retry._capture(original)
    assert retry.snapshot['history_revision']==first
    archived=service_database.rows('SELECT metadata FROM chat_messages WHERE message_id=%s',
        (row['runner_id']+':history:artifacts:0',))
    assert len(archived)==1
    metadata=json.loads(archived[0]['metadata'])
    assert [item['file_id'] for item in metadata['images']]==['image-0','image-1']
    assert [item['file_id'] for item in metadata['downloadableFiles']]==['file-0','file-1']
    assert public_runner({**row,'public_snapshot':control.snapshot})['snapshot']['history_revision']==first
    current.images.extend({'file_id':f'image-next-{i}'} for i in range(50))
    archive_and_compact(control,current);control._capture(current)
    assert control.snapshot['history_revision']>first


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel',[False,True])
async def test_admission_pause_atomically_acknowledges_concurrent_pause_and_cancellation_wins(
        storage,actors,service_database,monkeypatch,tmp_path,cancel):
    from src.config.settings import settings
    monkeypatch.setattr(settings.agent_runner,'durable_sleep_enabled',True)
    row,principal,_=claimed_timer(storage,actors['a'],service_database,True)
    controls=ControlRepository(service_database.connect)
    accepted=[]
    def deny(_):
        control,_=controls.submit(principal,row['runner_id'],RunnerControl(client_request_id=uuid.uuid4().hex,action='pause'))
        accepted.append(control)
        if cancel: storage[0].cancel(principal,row['runner_id'])
        raise RunnerError('CREDIT_BLOCKED',402)
    worker=RunnerWorker(AgentRunnerConfig(enabled=True),worker_id=row['worker_id'],
        execution_repository=storage[1],repository=storage[0],usage_repository=SimpleNamespace(),
        runtime_factory=SimpleNamespace(resource_directory=tmp_path,create=AsyncMock()),
        authorizer=SimpleNamespace(authorize_persisted=lambda *a,**k:principal,assert_credit=deny),
        finalizer=SimpleNamespace(finalize=Mock()))
    monkeypatch.setattr(worker,'_persist_trace',lambda *args:None)
    await worker.execute(row)
    actual=service_database.rows('SELECT status,consumed_attempt FROM agent_runner_controls WHERE control_id=%s',
        (accepted[0]['control_id'],))[0]
    assert actual['status']==('rejected' if cancel else 'consumed')
    if not cancel: assert actual['consumed_attempt']==row['attempt']
    else:
        closing=storage[1].acquire('cancel-priority',30)
        assert closing['runner_id']==row['runner_id'] and closing['cancel_only']


def test_fresh_recent_history_and_earlier_pages_cover_controls_despite_old_cache(
        storage,actors,service_database):
    from src.api.message_projection import public_conversation_messages
    from src.db.models import MessageDB
    from src.services.agent_runner.ownership import lock_runner
    from src.services.agent_runner.transaction_repository import write_history
    _,execution,submit=storage
    row,_=submit(actors['a']);owned=execution.acquire('paged-history-owner',30)
    cached=MessageDB.list_by_session(row['session_id'],100)
    assert cached==[]
    messages=[{'role':'assistant','content':'','tool_calls':[{'id':str(index),'function':{'name':'read'}}],
        '_history_id':row['runner_id']+':history:private:'+str(index)} for index in range(150)]
    messages.append({'role':'user','content':'original rule','metadata':{'control_id':'control-history-user'},
        '_history_id':'ignored-control-id'})
    messages.extend({'role':'assistant','content':'','_history_id':row['runner_id']+':history:artifacts:'+str(index),
        'metadata':{'downloadableFiles':[{'file_id':'artifact-'+str(index)}]}} for index in range(130))
    def write(values):
        with service_database.connect() as connection:
            cursor=connection.cursor();current=lock_runner(cursor,row['runner_id'],attempt(owned))
            write_history(cursor,current,{'status':'running','archival':True,'messages':values})
    write(messages)
    assert MessageDB.list_by_session(row['session_id'],100)==cached
    page=MessageDB.list_runner_history_page(row['session_id'],row['runner_id'],limit=100)
    assert len(page['messages'])==100 and page['has_more']
    visible=[];raw=[]
    late_id=row['runner_id']+':history:late'
    write([{'role':'assistant','content':'after newest page','_history_id':late_id}])
    while True:
        raw.extend(page['messages']);visible.extend(public_conversation_messages(page['messages']))
        if not page['has_more']: break
        before=int(page['next_cursor'])
        page=MessageDB.list_runner_history_page(row['session_id'],row['runner_id'],before_id=before,limit=100)
        assert int(page['next_cursor'])<before
    assert len(raw)==len(visible)==132
    assert 'control-history-user:user' in {item['message_id'] for item in visible}
    assert all(item['message_id']!=late_id for item in raw)
    latest=MessageDB.list_runner_history_page(row['session_id'],row['runner_id'],limit=100)
    assert len(latest['messages'])==100 and latest['messages'][-1]['message_id']==late_id
