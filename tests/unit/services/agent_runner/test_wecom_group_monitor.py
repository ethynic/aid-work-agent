"""Monitor authority lives in the original root, never in group/model data."""

import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from src.local_tools.proxy_tool import WecomGroupPollInput, WecomGroupPollTool, WecomMessageSendInput, WecomMessageSendTool
from src.local_tools.lifecycle import local_operation_scope
from src.local_tools.repository import DeviceOperationUnresolved
from src.services.agent_runner.ownership import LeaseLost
from src.services.agent_runner.wecom_device_binding import RESOURCE, bound_device, bind_or_validate


def task():
    row = dict(runner_id='root-a', tenant_id='tenant', user_id='user', session_id='session',
               source='chat', session_kind='web')
    identity = {key: row[key] for key in ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')}
    root = {'execution_id': row['runner_id'], 'identity': identity, 'resources': {}}
    return row, {'execution': root}


def pin(row, checkpoint):
    assert not bind_or_validate(checkpoint, row, 'wecom_group_poll', 'wecom', 'device-a', {'target_name': 'Synthetic'})


def test_poll_schema_has_one_target_and_no_shared_or_model_supplied_watermark():
    assert WecomGroupPollInput(target_name='Synthetic').model_dump(exclude_none=True) == {'target_name': 'Synthetic'}
    for values in ({}, {'target_name': ''}, {'target_name': 'Synthetic', 'cursor': ''}):
        with pytest.raises(ValidationError):
            WecomGroupPollInput(**values)
    for extra in ({'limit': 3}, {'device_id': 'other'}):
        assert WecomGroupPollTool()._validate_args({'target_name': 'Synthetic', **extra})
    assert WecomGroupPollInput.model_json_schema()['additionalProperties'] is False
    assert WecomMessageSendInput(target_name='Synthetic', text='generated', group_cursor='opaque').group_cursor == 'opaque'


@pytest.mark.asyncio
@pytest.mark.parametrize('tool_class, public, field', [
    (WecomGroupPollTool, {'target_name': 'Synthetic'}, 'cursor'),
    (WecomMessageSendTool, {'target_name': 'Synthetic', 'text': 'generated'}, 'group_cursor'),
])
async def test_explicit_null_survives_real_executor_normalization_but_never_enqueues(tool_class, public, field):
    from src.tools.executor import ToolExecutor, normalize_provided_parameters
    from src.tools.registry import ToolRegistry
    tool = tool_class()
    registry = ToolRegistry()
    registry.register(tool)
    normalized = []
    def normalize(current, arguments):
        value = normalize_provided_parameters(current, arguments)
        normalized.append(value)
        return value
    with patch('src.tools.executor.normalize_provided_parameters', side_effect=normalize), \
            patch.object(tool, '_find_ready_device', new_callable=AsyncMock) as choose, \
            patch('src.local_tools.service.LocalInvocationService.enqueue') as enqueue:
        result = await ToolExecutor(registry).execute(tool.name, {**public, field: None,
            '_trusted_tenant_id': 'tenant', '_trusted_user_id': 'user', '_session_id': 'session'})
    assert normalized and normalized[-1][field] is None
    assert result['code'] == 'INVALID_ARGS' and not result['success']
    choose.assert_not_awaited()
    enqueue.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('tool_class, public, field', [
    (WecomGroupPollTool, {'target_name': 'Synthetic'}, 'cursor'),
    (WecomMessageSendTool, {'target_name': 'Synthetic', 'text': 'generated'}, 'group_cursor'),
])
async def test_omitted_new_cursor_stays_absent_through_executor_legacy_path(tool_class, public, field):
    from src.tools.executor import ToolExecutor
    from src.tools.registry import ToolRegistry
    tool = tool_class()
    registry = ToolRegistry()
    registry.register(tool)
    with patch.object(tool, '_find_ready_device', AsyncMock(return_value=({'id': 'device'}, None))), \
            patch.object(tool, '_dispatch_and_wait', AsyncMock(return_value={'success': True, 'effect': 'none'})) as dispatch:
        result = await ToolExecutor(registry).execute(tool.name, {**public,
            '_trusted_tenant_id': 'tenant', '_trusted_user_id': 'user', '_session_id': 'session'})
    assert result['success']
    assert field not in dispatch.await_args.args[4]


@pytest.mark.parametrize('change', ['runner_id', 'identity', 'version', 'device_id'])
def test_marker_cannot_be_borrowed_between_roots_or_identities(change):
    row, checkpoint = task()
    pin(row, checkpoint)
    marker = checkpoint['execution']['resources'][RESOURCE]
    marker[change] = {'runner_id': 'other', 'identity': {}, 'version': True, 'device_id': ''}[change]
    with pytest.raises(LeaseLost):
        bound_device(checkpoint['execution'], row['runner_id'], checkpoint['execution']['identity'])


def test_pin_is_root_only_and_wecom_scope_cannot_fall_back_to_name_or_media():
    row, checkpoint = task()
    pin(row, checkpoint)
    original = copy.deepcopy(checkpoint)
    for tool, device, args in [('wecom_probe', 'device-b', {}),
                               ('wecom_message_send', 'device-a', {'target_name': 'Synthetic', 'text': 'x'}),
                               ('wecom_send_image', 'device-a', {}), ('wecom_send_file', 'device-a', {})]:
        with pytest.raises(DeviceOperationUnresolved):
            bind_or_validate(checkpoint, row, tool, 'wecom', device, args)
    assert bind_or_validate(checkpoint, row, 'wecom_message_send', 'wecom', 'device-a',
                            {'target_name': 'Synthetic', 'text': 'generated', 'group_cursor': 'opaque'})
    assert checkpoint == original
    assert not bind_or_validate(checkpoint, row, 'boss_goto', None, 'other', {})


@pytest.mark.asyncio
async def test_monitor_uses_original_nonselected_device_but_never_migrates_when_offline():
    tool = WecomGroupPollTool()
    owner = SimpleNamespace(wecom_device_id=lambda: 'original')
    original = dict(id='original', selected=False, status='active', last_seen_at=datetime.now(timezone.utc),
                    capabilities_json={'provider_id': 'ai.aidwork.wecom'})
    other = {**original, 'id': 'selected-other', 'selected': True}
    with local_operation_scope(owner), patch('src.local_tools.proxy_tool.repository.list_devices', return_value=[other, original]):
        chosen, error = await tool._find_ready_device('tenant', 'user')
        assert chosen is original and error is None
        original['status'] = 'revoked'
        chosen, error = await tool._find_ready_device('tenant', 'user')
        assert chosen is None and '未改用其他设备' in error


def test_poll_price_is_zero_even_when_unlisted_tool_default_is_paid(monkeypatch):
    from src.local_tools import pricing
    monkeypatch.setattr(pricing.settings.boss_tool_billing, 'enabled', True)
    monkeypatch.setattr(pricing.settings.boss_tool_billing, 'default_credit_price', 99)
    assert pricing.tool_credit_price('wecom_group_poll') == 0


def test_bounded_poll_page_is_preserved_as_parseable_json_beyond_generic_tail_cut():
    data = {'context_messages': [], 'new_messages': [{'text': '匿名'*5000}], 'cursor': 'opaque-tail'}
    result = WecomGroupPollTool()._map_terminal({'id': 'inv', 'state': 'succeeded', 'effect': 'none',
        'result_json': {'success': True, 'data': data}})
    assert result['_no_truncate'] is True and result['data']['cursor'] == 'opaque-tail'


def test_repeated_fact_retirement_keeps_five_latest_group_cursors_and_one_root_pin_bounded():
    from src.core.agent_engine.contracts import AgentMode, ExecutionState, Identity, ToolCall, ToolFact
    from src.services.agent_runner.continuous_history import compact_facts
    from src.services.agent_runner.contracts import canonical_json
    row, checkpoint = task()
    pin(row, checkpoint)
    state = ExecutionState(Identity('tenant', 'user', 'session'), row['runner_id'], AgentMode.STANDALONE, 'prompt', [])
    marker = copy.deepcopy(checkpoint['execution']['resources'][RESOURCE])
    state.resources[RESOURCE] = marker
    for generation in range(100):
        for ordinal in range(50):
            group = 'Synthetic-'+str(ordinal % 5)
            call = ToolCall(
                f'{generation}-{ordinal}', 'wecom_group_poll', {'target_name': group, 'cursor': 'previous'})
            state.tools[call.id] = ToolFact(call, phase='completed', result_recorded=True,
                result={'data': {'cursor': f'cursor-{generation}-{ordinal}'}})
            state.resources.setdefault('tool_cursor_orders', {})[call.id] = [generation, ordinal]
        compact_facts(state)
        assert len(state.tools) <= 32
        assert len(state.resources['continuous_cursors']) == 5
        assert len(state.resources['tool_cursor_orders']) <= 32
        assert state.resources[RESOURCE] == marker
    for group in range(5):
        key = canonical_json(['wecom_group_poll', {'target_name': 'Synthetic-'+str(group)}])
        assert state.resources['continuous_cursors'][key]['/data/cursor'] == f'cursor-99-{45+group}'


@pytest.mark.asyncio
async def test_admin_metadata_adds_only_trusted_wecom_choices_without_mutating_registry(monkeypatch):
    from src.api import agent_definitions
    from src.core import agent
    from src.local_tools.catalog import allowed_tools
    registry = SimpleNamespace(_tools={'read': SimpleNamespace(display_name='Read', description='read')})
    monkeypatch.setattr(agent_definitions, '_require_admin', lambda request: None)
    monkeypatch.setattr(agent, 'master_agent', SimpleNamespace(tool_registry=registry))
    result = await agent_definitions.list_tools_meta(SimpleNamespace())
    names = {item['id'] for item in json.loads(result.body)['data']}
    assert names == {'read', *allowed_tools('wecom')}
    assert list(registry._tools) == ['read']
    assert not any(name.startswith('boss_') for name in names)
