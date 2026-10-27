"""Unknown sender recovery uses original facts, never a guessed identity."""

import copy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.agent_engine.contracts import CheckpointFailure, Identity, ToolCall, ToolFact
from src.local_tools import wecom_inbound as inbound


def payload(*, baseline=False, platform='unknown'):
    message = {'msg_id':7,'sender_id':'customer','sender_platform':platform,'side':'peer','text':'original question'}
    return {'success':True,'data':{'session':'stable-session','baseline':baseline,'cursor':'main-cursor',
        'messages':[message],'pending_sessions':[{'session':'stable-session','unknown_messages':[
            {'msg_id':7,'sender_id':'customer','recovery_cursor':'native-single-cursor','baseline':baseline}]}]}}


def context():
    identity = Identity('tenant','user','chat')
    owner = SimpleNamespace(state=SimpleNamespace(identity=identity,execution_id='root',resources={}),
        root=SimpleNamespace(attempt=SimpleNamespace(runner_id='root')),wecom_device_id=lambda:'device',
        authorize_operation=AsyncMock())
    return owner


def original(owner, identifier=None):
    return {'id':identifier or str(uuid4()),'tenant_id':'tenant','user_id':'user','session_id':'chat',
        'provider_key':'wecom','business_kind':'agent_runner','tool_name':'wecom_read_session',
        'business_ref':{'runner_id':'root','execution_id':'root'},'device_id':'device','state':'succeeded',
        'created_at':datetime(2026,10,27),'result_json':{**payload(),inbound.INDEX:{'version':1,'pending':[0]}}}


@pytest.mark.asyncio
async def test_unknown_reads_are_bounded_and_rotate_past_unresolved_contacts(monkeypatch):
    owner = context()
    rows = [original(owner) for _ in range(8)]
    rows.sort(key=lambda row:row['id'])
    monkeypatch.setattr(inbound,'_rows',lambda owner,scan=None:[row for row in rows
        if not scan or row['id'] >= scan['invocation_id']][:5])
    reread = AsyncMock(return_value={'success':False,'code':'LOCALDB_UNAVAILABLE'})
    first = await inbound.before_read(owner,reread)
    second = await inbound.before_read(owner,reread)
    assert len(first) == len(second) == 4
    assert {item['invocation_id'] for item in first}.isdisjoint(item['invocation_id'] for item in second)
    assert reread.await_count == 8 and len(owner.state.resources) == 1
    assert set(owner.state.resources[inbound.SCAN]) == {'created_at','invocation_id','offset'}
    assert all(inbound._pending(row) == [0] for row in rows)


@pytest.mark.asyncio
async def test_refresh_must_match_the_original_message_sender_and_explicit_platform(monkeypatch):
    owner = context()
    row = original(owner)
    monkeypatch.setattr(inbound,'_rows',lambda *args:[row])
    for changed in ({'msg_id':8},{'sender_id':'other'},{'sender_platform':'unknown'},{'side':'self'}):
        fresh = payload(platform='wechat')
        fresh['data']['messages'][0].update(changed)
        result = await inbound.before_read(owner,AsyncMock(return_value=fresh))
        assert result[0].get('message') is None
    resolved = await inbound.before_read(owner,AsyncMock(return_value=payload(platform='wecom')))
    assert resolved[0]['message']['sender_platform'] == 'wecom'


def test_private_phase_proof_survives_consumed_index_but_not_scope_or_cursor_change():
    owner = context()
    row = original(owner)
    row['result_json'][inbound.INDEX]['pending'] = []
    state = owner.state
    fact = ToolFact(ToolCall('current-call','wecom_read_sessions',{}))
    ref = {'branch':'wecom.inbound.'+row['id']+'.7','ordinal':0,'request':{
        'device_id':'device','provider_key':'wecom','tool_name':'wecom_read_session',
        'arguments':{'session':'stable-session','cursor':'native-single-cursor','limit':1}}}
    root = {'runner_id':'root','tenant_id':'tenant'}
    invocation = {'device_id':'device'}
    load = lambda *args:row
    assert inbound.validate_read_phase(root,state,fact,ref,invocation,load_original=load)
    for change in ('cursor','user','device','execution'):
        bad, changed_ref = copy.deepcopy(row), copy.deepcopy(ref)
        if change == 'cursor': changed_ref['request']['arguments']['cursor'] = 'made-up-cursor'
        elif change == 'user': bad['user_id'] = 'other-user'
        elif change == 'device': bad['device_id'] = 'other-device'
        else: bad['business_ref']['execution_id'] = 'other-execution'
        with pytest.raises(CheckpointFailure,match='WECOM_INBOUND_OWNER_MISMATCH'):
            inbound.validate_read_phase(root,state,fact,changed_ref,invocation,load_original=lambda *args:bad)


def test_item_history_overrides_mixed_batch_baseline():
    result = payload(baseline=False)
    result['data']['pending_sessions'][0]['unknown_messages'][0]['baseline'] = True
    assert inbound._unknowns(result)[0][1] is True


@pytest.mark.asyncio
async def test_first_read_without_a_device_skips_refresh_and_uses_the_normal_main_bind(monkeypatch):
    owner = context()
    owner.wecom_device_id = lambda:None
    def unexpected(*args):
        raise AssertionError('An unbound first read must not query a guessed device')
    monkeypatch.setattr(inbound,'_rows',unexpected)
    assert await inbound.before_read(owner,AsyncMock()) == []
    assert owner.state.resources == {}


def test_only_trusted_late_success_is_an_owned_read_and_private_index_stays_on_raw_receipt():
    owner = context()
    row = original(owner)
    late = {**row['result_json'],'effect':'none'}
    row.update(state='unknown',effect='unknown',result_json={
        '_late_result':late,inbound.INDEX:{'version':1,'pending':[0]}})
    assert inbound._owned(row,owner)
    assert inbound._unknowns(inbound._payload(row)) == inbound._unknowns(late)
    assert inbound._pending(row) == [0]
    row['result_json']['_late_result']['effect'] = 'unknown'
    assert not inbound._owned(row,owner)
