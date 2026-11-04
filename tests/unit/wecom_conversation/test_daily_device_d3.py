"""A v2 daily Runner uses its accepted device for the first local read."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.local_tools import overlay_flow
from src.local_tools.proxy_tool import WecomReadSessionTool
from src.services.agent_runner.runtime import wecom_business_flow as flow
from src.tools.context import ToolExecutionContext, tool_execution_scope


@pytest.mark.asyncio
async def test_first_v2_daily_read_chooses_accepted_unselected_device():
    """A newly selected B must not steal the first invocation accepted for A."""
    device_a = {'id': 'device-a', 'selected': False, 'status': 'active',
                'last_seen_at': datetime.now(timezone.utc),
                'capabilities_json': {'provider_id': 'ai.aidwork.wecom'}}
    device_b = {**device_a, 'id': 'device-b', 'selected': True}
    identity = SimpleNamespace(tenant_id='tenant-a', user_id='user-a',
                               session_id='session-a', source='chat', session_kind='web')
    owner = SimpleNamespace(state=SimpleNamespace(identity=identity),
                            saved_phase=AsyncMock(return_value=None))
    operation = SimpleNamespace(owner=owner, branch='wecom.main', ordinal=0)
    context = ToolExecutionContext(
        tenant_id='tenant-a', user_id='user-a', session_id='session-a',
        request_data={'wecom_task_device': 'device-a',
                      'wecom_conversation_business': {
                          'schema_version': 2, 'kind': 'daily', 'device': 'device-a'}})
    proxy = WecomReadSessionTool()
    with tool_execution_scope(context), \
         patch.object(proxy, '_validate_args', return_value=None), \
         patch.object(proxy, '_dispatch_and_wait', new_callable=AsyncMock,
                      return_value={'success': True, 'data': {}}) as dispatch, \
         patch('src.local_tools.overlay_flow._known', side_effect=lambda result, _: result), \
         patch('src.local_tools.repository.list_devices', return_value=[device_b, device_a]):
        result = await overlay_flow.poll_operation(
            proxy, operation, {'session': 'ws1.one'}, tenant_id='tenant-a',
            user_id='user-a', session_id='session-a', progress_queue=None)
    assert result['success'] is True
    assert dispatch.await_args.args[3]['id'] == 'device-a'


@pytest.mark.asyncio
async def test_without_v2_daily_descriptor_keeps_selected_device_rule():
    """The new exception must not change ordinary WeCom selection."""
    device_b = {'id': 'device-b', 'selected': True, 'status': 'active'}
    identity = SimpleNamespace(tenant_id='tenant-a', user_id='user-a',
                               session_id='session-a', source='chat', session_kind='web')
    owner = SimpleNamespace(state=SimpleNamespace(identity=identity),
                            saved_phase=AsyncMock(return_value=None))
    operation = SimpleNamespace(owner=owner, branch='wecom.main', ordinal=0)
    proxy = WecomReadSessionTool()
    with tool_execution_scope(ToolExecutionContext(
            tenant_id='tenant-a', user_id='user-a', session_id='session-a')), \
         patch.object(proxy, '_validate_args', return_value=None), \
         patch.object(proxy, '_find_ready_device', new_callable=AsyncMock,
                      return_value=(device_b, None)) as choose, \
         patch.object(proxy, '_dispatch_and_wait', new_callable=AsyncMock,
                      return_value={'success': True, 'data': {}}) as dispatch, \
         patch('src.local_tools.overlay_flow._known', side_effect=lambda result, _: result):
        await overlay_flow.poll_operation(
            proxy, operation, {'session': 'ws1.one'}, tenant_id='tenant-a',
            user_id='user-a', session_id='session-a', progress_queue=None)
    choose.assert_awaited_once_with('tenant-a', 'user-a')
    assert dispatch.await_args.args[3]['id'] == 'device-b'


@pytest.mark.asyncio
async def test_daily_preflight_accepts_healthy_original_after_selection_moves():
    """The Runner preflight checks A's ownership and readiness, not current selection."""
    identity = SimpleNamespace(tenant_id='tenant-a', user_id='user-a',
                               session_id='session-a', source='chat', session_kind='web')
    root_identity = {key: getattr(identity, key) for key in
                     ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')}
    control = SimpleNamespace(durable_owner=True, attempt=SimpleNamespace(runner_id='run-a'),
                              envelope={'execution': {'execution_id': 'run-a',
                                                      'identity': root_identity,
                                                      'resources': {}}})
    execution = SimpleNamespace(identity=identity, control=control)
    device_a = {'id': 'device-a', 'selected': False, 'status': 'active',
                'last_seen_at': datetime.now(timezone.utc),
                'capabilities_json': {'provider_id': 'ai.aidwork.wecom'}}
    device_b = {**device_a, 'id': 'device-b', 'selected': True}
    with patch('src.local_tools.repository.list_devices', return_value=[device_b, device_a]):
        await flow._verify_device_before_daily(execution, {'device': 'device-a'})
