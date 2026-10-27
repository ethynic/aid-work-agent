"""Three business tools hide navigation, while failed sends never get replayed."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.local_tools.proxy_tool import (WecomReadSessionTool, WecomMessageSendTool,
    WecomReadSessionsTool, WecomGroupPollTool, WecomSendStatusTool)
from src.local_tools.wecom_business import BUSINESS_TOOLS, project_definitions
from src.local_tools import wecom_flow


def test_only_three_business_schemas_are_advertised_without_removing_legacy_execution():
    tools = [WecomReadSessionTool(), WecomMessageSendTool(), WecomReadSessionsTool(),
             WecomGroupPollTool(), WecomSendStatusTool()]
    definitions = project_definitions([tool.to_tool_definition() for tool in tools])
    assert {item['name'] for item in definitions} == BUSINESS_TOOLS
    for item in definitions:
        properties = item['input_schema']['properties']
        assert not {'target_ref', 'target_name', 'group_cursor', 'send_invocation_id','resolve_only'} & properties.keys()
    assert WecomMessageSendTool.InputModel(target_name='Old group', text='text', group_cursor='old').group_cursor == 'old'
    assert WecomReadSessionTool.InputModel(session='stable-session').session == 'stable-session'


def context(pending=None):
    state = SimpleNamespace(resources={'wecom_pending_sends': pending or {}},
        identity=SimpleNamespace(tenant_id='tenant', user_id='user', session_id='web-session'))
    owner = SimpleNamespace(state=state, authorize_operation=AsyncMock(), wecom_device_id=lambda: None,
        saved_phase=AsyncMock(return_value={
        'request': {'device_id': 'original-device'}}))
    return SimpleNamespace(owner=owner, branch='main', ordinal=0)


@pytest.mark.asyncio
async def test_db_read_loss_is_retryable_without_claiming_external_input_occurred(monkeypatch):
    operation = context()
    dispatch = AsyncMock(return_value={'success': False, 'code': 'EXECUTION_UNKNOWN',
        'effect': 'unknown', 'invocation_id': 'original-read'})
    monkeypatch.setattr('src.local_tools.overlay_flow.poll_operation', dispatch)
    result = await wecom_flow.run(WecomReadSessionTool(), operation, {'session': 'stable', 'cursor': 'original'})
    assert result['code'] == 'DB_READ_INTERRUPTED' and result['effect'] == 'none'
    assert result['invocation_id'] == 'original-read'
    assert dispatch.await_args.args[2]['cursor'] == 'original'


@pytest.mark.asyncio
async def test_unknown_send_is_saved_under_canonical_session_and_never_replayed(monkeypatch):
    operation = context()
    operation.owner.saved_phase.return_value['invocation'] = {
        'state':'succeeded','result_json':{'success':True,'data':{'session':'ws1.stable'}}}
    dispatch = AsyncMock(side_effect=[{'success':True,'data':{'session':'ws1.stable'}},
        {'success':False,'code':'EXECUTION_UNKNOWN','effect':'unknown','invocation_id':'original-send'}])
    monkeypatch.setattr('src.local_tools.overlay_flow.poll_operation', dispatch)
    await wecom_flow.run(WecomMessageSendTool(), operation, {'session': 'Name', 'text': 'reply'})
    assert operation.owner.state.resources['wecom_pending_sends']['ws1.stable']['invocation_id'] == 'original-send'
    assert dispatch.await_args_list[0].args[2] == {'session':'Name','limit':1,'resolve_only':True}
    assert dispatch.await_args_list[1].args[2] == {'session':'ws1.stable','text':'reply'}
    monkeypatch.setattr(wecom_flow, '_reconcile', AsyncMock(return_value=None))
    second = await wecom_flow.run(WecomMessageSendTool(), operation, {'session':'ws1.stable','text':'reply'})
    assert second['code'] == 'WECOM_SEND_PENDING' and second['effect'] == 'none'
    assert second['invocation_id'] is None
    assert dispatch.await_count == 2
    dispatch.side_effect = None
    dispatch.return_value = {'success':True,'effect':'applied'}
    assert (await wecom_flow.run(WecomMessageSendTool(),operation,{'session':'ws1.other','text':'independent'}))['success']
    assert dispatch.await_count == 3


@pytest.mark.asyncio
async def test_confirmed_reconciliation_clears_only_its_pending_and_other_reads_continue(monkeypatch):
    operation = context({'one': {'invocation_id': 'send-one', 'device_id': 'device'},
        'two': {'invocation_id': 'send-two', 'device_id': 'device'}})
    reconcile = AsyncMock(side_effect=[{'success': True, 'data': {'send_status': 'confirmed'}},
                                       {'success': True, 'data': {'send_status': 'unknown'}}])
    monkeypatch.setattr(wecom_flow, '_reconcile', reconcile)
    dispatch = AsyncMock(return_value={'success': True, 'effect': 'none', 'data': {'cursor': 'new'}})
    monkeypatch.setattr('src.local_tools.overlay_flow.poll_operation', dispatch)
    result = await wecom_flow.run(WecomReadSessionsTool(), operation, {'cursor': 'old'})
    assert set(operation.owner.state.resources['wecom_pending_sends']) == {'two'}
    assert result['data']['reconciled_sends'][0]['invocation_id'] == 'send-one'
    assert result['data']['cursor'] == 'new' and dispatch.await_count == 1


@pytest.mark.asyncio
async def test_confirmed_previous_send_does_not_turn_a_repeat_send_into_a_new_write(monkeypatch):
    operation = context({'ws1.one': {'invocation_id':'original','device_id':'device'}})
    monkeypatch.setattr(wecom_flow, '_reconcile', AsyncMock(return_value={
        'success': True, 'data': {'send_status': 'confirmed'}}))
    dispatch = AsyncMock()
    monkeypatch.setattr('src.local_tools.overlay_flow.poll_operation', dispatch)
    result = await wecom_flow.run(WecomMessageSendTool(),operation,{'session':'ws1.one','text':'again'})
    assert result['code'] == 'WECOM_SEND_RECONCILED' and not operation.owner.state.resources['wecom_pending_sends']
    dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_fifth_pending_target_is_not_starved_by_four_persistent_unknowns(monkeypatch):
    operation = context({str(number): {'invocation_id': str(number), 'device_id': 'device'} for number in range(5)})
    checked = []
    async def reconcile(owner, pending, identity, progress):
        checked.append(pending['invocation_id'])
    monkeypatch.setattr(wecom_flow, '_reconcile', reconcile)
    monkeypatch.setattr('src.local_tools.overlay_flow.poll_operation', AsyncMock(return_value={'success': True, 'data': {}}))
    await wecom_flow.run(WecomReadSessionsTool(), operation, {})
    await wecom_flow.run(WecomReadSessionsTool(), operation, {})
    assert checked[:5] == ['0', '1', '2', '3', '4']


def test_late_read_projects_server_recovered_candidates_without_exposing_private_index():
    candidates = [{'session': 'same', 'baseline': False, 'message': {'msg_id': 1}}]
    result = WecomReadSessionTool()._map_terminal({'id':'source', 'state':'unknown', 'effect':'unknown',
        'result_json': {'_late_result': {'success':True, 'code':'OK', 'effect':'none', 'data':{'channel':'local-db'}},
                        '_wecom_recovered':candidates, '_wecom_inbound':{'version':1,'pending':[]}}})
    assert result['success'] and result['data']['recovered_messages'] == candidates
    assert '_wecom_inbound' not in result and '_wecom_recovered' not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('code,effect,expected',[
    ('TARGET_AMBIGUOUS','none','TARGET_AMBIGUOUS'),('EXECUTION_UNKNOWN','unknown','DB_READ_INTERRUPTED')])
async def test_failed_name_resolution_never_binds_a_send_or_creates_a_pending_hold(monkeypatch,code,effect,expected):
    operation = context()
    dispatch = AsyncMock(return_value={'success':False,'code':code,'effect':effect})
    monkeypatch.setattr('src.local_tools.overlay_flow.poll_operation',dispatch)
    result = await wecom_flow.run(WecomMessageSendTool(),operation,{'session':'Same name','text':'reply'})
    assert result['code'] == expected and result['effect'] == 'none'
    assert dispatch.await_count == 1 and dispatch.await_args.args[0].name == 'wecom_read_session'
    assert not operation.owner.state.resources['wecom_pending_sends']


@pytest.mark.asyncio
async def test_stable_send_keeps_the_single_operation_fast_path(monkeypatch):
    dispatch = AsyncMock(return_value={'success':True,'effect':'applied'})
    monkeypatch.setattr('src.local_tools.overlay_flow.poll_operation',dispatch)
    assert (await wecom_flow.run(WecomMessageSendTool(),context(),{'session':'ws1.stable','text':'reply'}))['success']
    assert dispatch.await_count == 1 and dispatch.await_args.args[0].name == 'wecom_message_send'
