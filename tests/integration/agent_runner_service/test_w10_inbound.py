"""Real PG atomic handoff of old unknown incoming messages to a later read."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import psycopg2
from psycopg2.extras import Json
import pytest

from src.local_tools import wecom_inbound as inbound
from .test_storage import storage, attempt

pytestmark = pytest.mark.integration


@pytest.fixture
def owner(storage,actors,service_database):
    _, execution, submit = storage
    _, principal = submit(actors['a'])
    row = execution.acquire('inbound-owner',60)
    device = str(uuid4())
    identity = principal.identity
    root = SimpleNamespace(attempt=attempt(row),repository=execution,lock=asyncio.Lock(),_check_usage=lambda:None)
    owner = SimpleNamespace(root=root,state=SimpleNamespace(identity=identity,execution_id=row['runner_id'],resources={}),
        wecom_device_id=lambda:device,authorize_operation=AsyncMock())
    service_database.rows('INSERT INTO local_tool_devices(id,tenant_id,user_id,token_hash) VALUES(%s,%s,%s,%s)',
        (device,identity.tenant_id,identity.user_id,'synthetic-device-no-token'))
    try:
        yield owner
    finally:
        service_database.rows('DELETE FROM local_tool_invocations WHERE device_id=%s',(device,))
        service_database.rows('DELETE FROM local_tool_devices WHERE id=%s',(device,))


def result(identifier, *, unknown=False, baseline=False):
    data = {'session':'native-session','cursor':'unchanged-main-cursor','baseline':baseline,'messages':[]}
    if unknown:
        data['messages'] = [{'msg_id':7,'sender_id':'peer-id','sender_platform':'unknown','side':'peer','text':'original incoming question'}]
        data['pending_sessions'] = [{'session':'native-session','unknown_messages':[
            {'msg_id':7,'sender_id':'peer-id','recovery_cursor':'native-recovery-cursor','baseline':baseline}]}]
    return {'success':True,'invocation_id':identifier,'effect':'none','data':data}


def save(database,owner, *, unknown=False,baseline=False):
    identifier = str(uuid4())
    payload = result(identifier,unknown=unknown,baseline=baseline)
    identity = owner.state.identity
    database.rows('''INSERT INTO local_tool_invocations(id,tenant_id,user_id,session_id,device_id,
        tool_name,arguments_json,state,effect,result_json,provider_key,business_kind,business_ref)
        VALUES(%s,%s,%s,%s,%s,'wecom_read_session','{}','succeeded','none',%s,'wecom','agent_runner',%s)''',
        (identifier,identity.tenant_id,identity.user_id,identity.session_id,owner.wecom_device_id(),Json(payload),
         Json({'runner_id':owner.root.attempt.runner_id,'execution_id':owner.state.execution_id})))
    return payload


def stored(database,identifier):
    return database.rows('SELECT result_json FROM local_tool_invocations WHERE id=%s',(identifier,))[0]['result_json']


@pytest.mark.asyncio
@pytest.mark.parametrize('baseline',[False,True])
async def test_restart_recovers_original_body_and_history_once_without_rewinding_main_cursor(owner,service_database,baseline):
    old = save(service_database,owner,unknown=True,baseline=baseline)
    await inbound.after_read(owner,old,{})
    original_data = copy.deepcopy(old['data'])
    owner.state.resources.clear()  # A fresh runtime need not retain the original body.
    async def refreshed(item):
        assert item['source_invocation_id'] == old['invocation_id'] and item['msg_id'] == 7
        return {'success':True,'data':{'session':item['session'],'messages':[
            {'msg_id':7,'sender_id':'peer-id','sender_platform':'wechat','side':'peer','text':'different fresh body'}]}}
    recovery = await inbound.before_read(owner,refreshed)
    current = save(service_database,owner)
    output = await inbound.after_read(owner,current,{},recovery=recovery)
    candidate = output['data']['recovered_messages'][0]
    assert candidate['baseline'] is baseline and candidate['message']['text'] == 'original incoming question'
    assert candidate['message']['sender_platform'] == 'wechat'
    assert output['data']['cursor'] == 'unchanged-main-cursor'
    assert stored(service_database,old['invocation_id'])['data'] == original_data
    assert stored(service_database,current['invocation_id'])['data'] == current['data']
    assert stored(service_database,old['invocation_id'])[inbound.INDEX]['pending'] == []
    # Crash before the ToolFact save: the original invocation still reprojects
    # the candidate, without restoring an already-consumed old pending index.
    replayed = await inbound.after_read(owner,current,{})
    assert replayed['data']['recovered_messages'] == [candidate]
    assert await inbound.before_read(owner,refreshed) == []


@pytest.mark.asyncio
async def test_unchanged_unknown_remains_pending_and_another_session_can_keep_progressing(owner,service_database):
    old = save(service_database,owner,unknown=True)
    await inbound.after_read(owner,old,{})
    recovery = await inbound.before_read(owner,AsyncMock(return_value={'success':True,'data':{
        'session':'native-session','messages':old['data']['messages']}}))
    current = save(service_database,owner)
    output = await inbound.after_read(owner,current,{},recovery=recovery)
    assert 'recovered_messages' not in output['data']
    assert stored(service_database,old['invocation_id'])[inbound.INDEX]['pending'] == [0]
    assert output['success'] and output['data']['cursor'] == current['data']['cursor']


@pytest.mark.asyncio
async def test_database_failure_rolls_back_pending_consumption_and_candidate_together(owner,service_database):
    old = save(service_database,owner,unknown=True)
    await inbound.after_read(owner,old,{})
    fresh = {'success':True,'data':{'session':'native-session','messages':[
        {**old['data']['messages'][0],'sender_platform':'wechat'}]}}
    recovery = await inbound.before_read(owner,AsyncMock(return_value=fresh))
    current = save(service_database,owner)
    constraint = 'w10_inbound_failure_'+uuid4().hex
    # A real transactional constraint rejects only this fixture's new handoff.
    service_database.rows('ALTER TABLE local_tool_invocations ADD CONSTRAINT '+constraint+
        ' CHECK (id<>\''+current['invocation_id']+'\'::uuid OR NOT (result_json ? \''+inbound.RECOVERED+'\'))')
    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            await inbound.after_read(owner,current,{},recovery=recovery)
        assert stored(service_database,old['invocation_id'])[inbound.INDEX]['pending'] == [0]
        assert inbound.RECOVERED not in stored(service_database,current['invocation_id'])
    finally:
        service_database.rows('ALTER TABLE local_tool_invocations DROP CONSTRAINT '+constraint)


@pytest.mark.asyncio
async def test_unbound_first_read_does_not_cast_none_to_a_device_then_indexes_after_main_bind(owner,service_database):
    original_device = owner.wecom_device_id
    owner.wecom_device_id = lambda:None
    reread = AsyncMock()
    assert await inbound.before_read(owner,reread) == []
    reread.assert_not_awaited()
    owner.wecom_device_id = original_device  # The ordinary main read performed the binding.
    first = save(service_database,owner,unknown=True)
    await inbound.after_read(owner,first,{})
    assert stored(service_database,first['invocation_id'])[inbound.INDEX]['pending'] == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize('late_current',[False,True])
async def test_late_success_indexes_and_recovers_without_rewriting_original_unknown_receipt(owner,service_database,late_current):
    def make_late(payload):
        late = {**copy.deepcopy(payload),'effect':'none','stopped':True}
        service_database.rows("UPDATE local_tool_invocations SET state='unknown',effect='unknown',result_json=%s WHERE id=%s",
            (Json({'_late_result':late}),payload['invocation_id']))
        return late
    old = save(service_database,owner,unknown=True,baseline=True)
    original_late = make_late(old)
    await inbound.after_read(owner,old,{})
    async def refresh(item):
        return {'success':True,'data':{'session':item['session'],'messages':[
            {**original_late['data']['messages'][0],'sender_platform':'wechat'}]}}
    recovery = await inbound.before_read(owner,refresh)
    assert len(recovery) == 1
    current = save(service_database,owner)
    current_late = make_late(current) if late_current else None
    result = await inbound.after_read(owner,current,{},recovery=recovery)
    assert result['data']['recovered_messages'][0]['message']['sender_platform'] == 'wechat'
    assert result['data']['recovered_messages'][0]['baseline'] is True
    original = service_database.rows('SELECT state,effect,result_json FROM local_tool_invocations WHERE id=%s',
        (old['invocation_id'],))[0]
    assert original['state'] == 'unknown' and original['effect'] == 'unknown'
    assert original['result_json']['_late_result'] == original_late
    assert original['result_json'][inbound.INDEX]['pending'] == []
    if current_late:
        assert stored(service_database,current['invocation_id'])['_late_result'] == current_late
    replay = await inbound.after_read(owner,current,{})
    assert replay['data']['recovered_messages'] == result['data']['recovered_messages']


@pytest.mark.asyncio
async def test_successful_empty_read_does_not_write_an_empty_index_or_update_its_row(owner,service_database):
    current = save(service_database,owner)
    before = service_database.rows('SELECT xmin::text AS version FROM local_tool_invocations WHERE id=%s',
        (current['invocation_id'],))[0]
    assert await inbound.after_read(owner,current,{}) == current
    after = service_database.rows('SELECT xmin::text AS version FROM local_tool_invocations WHERE id=%s',
        (current['invocation_id'],))[0]
    assert before == after and stored(service_database,current['invocation_id']) == current


def test_repeated_compaction_retires_closed_unknown_db_read_views_without_erasing_audit_or_unknown_sends(owner,service_database):
    from src.core.agent_engine.contracts import AgentMode,ExecutionState,ToolCall,ToolFact
    from src.services.agent_runner.continuous_history import archive_and_compact,KEEP_FACTS
    from src.services.agent_runner.local_invocations import LocalPhase

    state = ExecutionState(owner.state.identity,owner.state.execution_id,AgentMode.MASTER,'role',[])
    refs = state.resources.setdefault('local_invocations',{})
    owner.root.envelope = {}
    owner.root.snapshot = {}
    def append(calls):
        records = []
        for identifier,name,args,closed in calls:
            invocation = str(uuid4())
            call = ToolCall(identifier,name,args)
            state.tools[identifier] = ToolFact(call,phase='completed' if closed else 'waiting',
                result={'success':False,'code':'DB_READ_INTERRUPTED'},result_recorded=closed)
            phase = LocalPhase(state.execution_id,identifier,'main',0)
            refs[phase.key(state.execution_id)] = {'tool_call_id':identifier,'invocation_id':invocation}
            if not closed:
                state.pending.append(call)
            identity = state.identity
            records.append((invocation,identity.tenant_id,identity.user_id,identity.session_id,owner.wecom_device_id(),
                name,Json(args),Json({'runner_id':state.execution_id,'execution_id':state.execution_id})))
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.executemany('''INSERT INTO local_tool_invocations(id,tenant_id,user_id,session_id,device_id,
                tool_name,arguments_json,state,effect,result_json,provider_key,business_kind,business_ref)
                VALUES(%s,%s,%s,%s,%s,%s,%s,'unknown','unknown',NULL,'wecom','agent_runner',%s)''',records)
    append([('unknown-send','wecom_message_send',{'session':'target','text':'possibly sent'},True),
        ('unknown-ui-read','wecom_read_session',{'channel':'ui','target_name':'target'},True),
        ('pending-db-read','wecom_read_sessions',{},False)])
    for round_number in range(4):
        append([(f'closed-db-{round_number}-{index}','wecom_read_sessions',{},True) for index in range(40)])
        archive_and_compact(owner.root,state)
        assert len(state.tools) <= KEEP_FACTS+3 and len(refs) <= KEEP_FACTS+3
        assert {'unknown-send','unknown-ui-read','pending-db-read'} <= set(state.tools)
    audit = service_database.rows('''SELECT state,effect,result_json FROM local_tool_invocations
        WHERE device_id=%s AND tool_name='wecom_read_sessions' ''',(owner.wecom_device_id(),))
    assert len(audit) == 161
    assert all(row['state']=='unknown' and row['effect']=='unknown' and row['result_json'] is None for row in audit)
