"""Detail greetings require opt-in; resume recognition and manual approved lists stay separate."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from src.local_tools.proxy_tool import BossGreetDetailTool, BossGreetTool, LocalToolProxyTool
from src.local_tools import overlay_flow
from src.tools.executor import normalize_provided_parameters

pytestmark = pytest.mark.unit


def test_schema_defaults_off_without_injecting_defaults_into_old_intents():
    tool = BossGreetDetailTool()
    assert tool.InputModel(name='张测试').greet_in_detail is False
    assert normalize_provided_parameters(tool, {'name': '张测试'}) == {'name': '张测试'}
    assert type(tool).execute is LocalToolProxyTool.execute
    assert tool.InputModel(name='张测试', greet_in_detail=True).greet_in_detail is True
    for value in ['true', 'false', 1, 0, None, {}, []]:
        with pytest.raises(ValidationError):
            tool.InputModel(name='张测试', greet_in_detail=value)


@pytest.mark.parametrize('mode', [None, False, 'true', 'false', 1, 0])
@pytest.mark.parametrize('path', ['legacy', 'owned'])
@pytest.mark.asyncio
async def test_disabled_request_never_selects_device_or_dispatches(monkeypatch, mode, path):
    tool = BossGreetDetailTool()
    device, dispatch = AsyncMock(), AsyncMock()
    monkeypatch.setattr(tool, '_find_ready_device', device)
    monkeypatch.setattr(tool, '_dispatch_and_wait', dispatch)
    args = {'name': '张测试', 'dry_run': True, **({'greet_in_detail': mode} if mode is not None else {})}
    if path == 'legacy':
        result = await tool.execute(**args, _trusted_tenant_id='tenant', _trusted_user_id='user')
    else:
        operation = SimpleNamespace(branch='device', ordinal=0, owner=SimpleNamespace(saved_phase=AsyncMock(return_value=None)))
        result = await overlay_flow.poll_operation(tool, operation, args, tenant_id='tenant',
            user_id='user', session_id='session', progress_queue=None)
    assert result['success'] is False and result['code'] == 'INVALID_ARGS'
    assert '默认关闭' in result['message']
    device.assert_not_called()
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_true_keeps_explicit_mode_and_candidate_in_dispatch(monkeypatch):
    tool = BossGreetDetailTool()
    monkeypatch.setattr(tool, '_find_ready_device', AsyncMock(return_value=({'id': 'device'}, None)))
    monkeypatch.setattr(tool, '_tool_credit_price', lambda: 0)
    dispatch = AsyncMock(return_value={'success': True, 'effect': 'none', 'data': {'greeted': False}})
    monkeypatch.setattr(tool, '_dispatch_and_wait', dispatch)
    result = await tool.execute(name='张测试', greet_in_detail=True, dry_run=True,
        _trusted_tenant_id='tenant', _trusted_user_id='user')
    assert result['success'] is True
    assert dispatch.call_args.args[4] == {'name': '张测试', 'greet_in_detail': True, 'dry_run': True}


@pytest.mark.asyncio
async def test_saved_preupgrade_invocation_is_polled_not_replayed_or_rewritten(monkeypatch):
    tool = BossGreetDetailTool()
    args = {'name': '张测试'}
    saved = {'request': {'arguments': args, 'tool_name': tool.name, 'device_id': 'old-device'},
        'invocation': {'id': 'old-invocation', 'deadline_at': datetime.now(timezone.utc) + timedelta(seconds=60)}}
    operation = SimpleNamespace(branch='device', ordinal=0, owner=SimpleNamespace(saved_phase=AsyncMock(return_value=saved)))
    device, dispatch = AsyncMock(), AsyncMock()
    poll = AsyncMock(return_value={'success': True, 'effect': 'none', 'invocation_id': 'old-invocation'})
    monkeypatch.setattr(tool, '_find_ready_device', device)
    monkeypatch.setattr(tool, '_dispatch_and_wait', dispatch)
    monkeypatch.setattr(tool, '_poll_invocation', poll)
    result = await overlay_flow.poll_operation(tool, operation, normalize_provided_parameters(tool, args),
        tenant_id='tenant', user_id='user', session_id='session', progress_queue=None)
    assert result['invocation_id'] == 'old-invocation'
    poll.assert_awaited_once()
    device.assert_not_called()
    dispatch.assert_not_called()


def test_manual_list_tool_retains_exact_name_whitelist():
    assert BossGreetTool.InputModel(names=[' 张测试 ', '李测试', '张测试']).names == ['张测试', '李测试']
    assert '人工审核' in BossGreetTool.description
    assert '默认关闭' in BossGreetDetailTool.description or '默认false' in BossGreetDetailTool.description
