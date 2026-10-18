"""Same-name clarification uses exact card references; invalid input never dispatches."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from src.local_tools import catalog, overlay_flow
from src.local_tools.durable_flow import LocalContinuationRequired
from src.local_tools.proxy_tool import (
    BossLookupCandidatesTool, BossGreetSelectedTool, LocalToolProxyTool, LOCAL_PROXY_TOOL_NAMES,
)
from src.tools.executor import normalize_provided_parameters

pytestmark = pytest.mark.unit
REF = 'boss-card-v1:1791850000000:' + 'a' * 64


def test_tool_registration_and_strict_schema():
    for tool_class in [BossLookupCandidatesTool, BossGreetSelectedTool]:
        tool = tool_class()
        assert tool.name in LOCAL_PROXY_TOOL_NAMES
        assert tool.name in catalog.TRUSTED_PROVIDERS['boss-recruiting']['tools']
        assert type(tool).execute is LocalToolProxyTool.execute
        assert tool.heal_eligible is True
        assert tool.visual_recovery_policy == ('readonly' if tool_class is BossLookupCandidatesTool else 'observe_only')
    assert BossGreetSelectedTool.InputModel(name='张测试', candidate_ref=REF).candidate_ref == REF
    for args in [{'name': '张测试'}, {'name': '张测试', 'candidate_ref': REF + '\n'},
                 {'name': ['张测试'], 'candidate_ref': REF}, {'name': ' ', 'candidate_ref': REF},
                 {'name': '张测试', 'candidate_ref': REF, 'limit': 2},
                 {'name': '张测试', 'candidate_ref': 123}]:
        with pytest.raises(ValidationError):
            BossGreetSelectedTool.InputModel.model_validate(args)
    for names in [[], ['a', 'b', 'c', 'd'], [' '], [1], '张测试', ['张' * 31]]:
        with pytest.raises(ValidationError):
            BossLookupCandidatesTool.InputModel(names=names)


@pytest.mark.parametrize('path', ['legacy', 'owned'])
@pytest.mark.parametrize('args', [{}, {'name': '张测试'}, {'name': '张测试', 'candidate_ref': 'bad'},
    {'name': ' ', 'candidate_ref': REF}, {'name': '张测试', 'candidate_ref': REF, 'names': ['张测试']},
    {'name': '张测试', 'candidate_ref': REF + '\n'}])
@pytest.mark.asyncio
async def test_invalid_selected_never_selects_device_or_creates_invocation(monkeypatch, path, args):
    tool = BossGreetSelectedTool()
    gate, dispatch = AsyncMock(), AsyncMock()
    monkeypatch.setattr(tool, '_find_ready_device', gate)
    monkeypatch.setattr(tool, '_dispatch_and_wait', dispatch)
    if path == 'legacy':
        result = await tool.execute(**args, _trusted_tenant_id='tenant', _trusted_user_id='user')
    else:
        operation = SimpleNamespace(branch='device', ordinal=0,
            owner=SimpleNamespace(saved_phase=AsyncMock(return_value=None)))
        result = await overlay_flow.poll_operation(tool, operation, args,
            tenant_id='tenant', user_id='user', session_id='session', progress_queue=None)
    assert not result['success'] and result['code'] == 'INVALID_ARGS'
    gate.assert_not_called()
    dispatch.assert_not_called()


@pytest.mark.parametrize('tool_class,args', [
    (BossLookupCandidatesTool, {'names': ['张测试']}),
    (BossGreetSelectedTool, {'name': '张测试', 'candidate_ref': REF}),
])
@pytest.mark.asyncio
async def test_exact_reference_and_arguments_survive_shared_recovery_without_legacy_healing(monkeypatch, tool_class, args):
    from src.config.settings import settings
    monkeypatch.setattr(settings.boss_tool_billing, 'overlay_heal_enabled', True)
    tool = tool_class()
    monkeypatch.setattr(tool, '_find_ready_device', AsyncMock(return_value=({'id': 'device'}, None)))
    monkeypatch.setattr(tool, '_tool_credit_price', lambda: 0)
    external = tool_class is BossGreetSelectedTool
    facts = {'schema_version': 1, 'operation_class': 'external_effect' if external else 'readonly',
        'operation': {'name': tool.name, 'goal': '原始精确候选意图', 'expected': '原精确卡片的稳定后验',
            'result_kind': 'business_result' if external else 'page_state'},
        'execution': {'stage': 'prepared', 'input_status': 'not_started', 'attempts_used': 1, 'ordinary_retries_used': 0},
        'verification': {'status': 'inconclusive', 'evidence': ['准备页面受阻，未提交']}}
    if external:
        facts['business_effect'] = {'status': 'not_started', 'completed_count': 0}
    source = {'success': False, 'code': 'UI_CHANGED', 'effect': 'none', 'invocation_id': 'original-reference-call',
        'data': {'operation_recovery': facts}}
    obstructed = {'success': True, 'data': {'recovery_ref': 'stored-close', 'visual': {
        'http_status': 200, 'state': 'completed', 'diagnosis': {'obstruction': 'present',
            'goal_state': 'not_satisfied', 'proposed_action': {'kind': 'dismiss_click'}}}}}
    restored = {'success': True, 'data': {'visual': {'http_status': 200, 'state': 'completed',
        'diagnosis': {'obstruction': 'absent', 'goal_state': 'satisfied', 'proposed_action': None}}}}
    dispatch = AsyncMock(side_effect=[source, obstructed, {'success': True}, restored,
        {'success': True, 'data': {'original_target_verified': True}}])
    heal = AsyncMock()
    monkeypatch.setattr(LocalToolProxyTool, '_dispatch_and_wait', dispatch)
    monkeypatch.setattr(LocalToolProxyTool, '_heal_overlay', heal)
    result = await tool.execute(**args, _trusted_tenant_id='tenant', _trusted_user_id='user')
    assert result['success'] and result['data']['original_target_verified']
    assert dispatch.await_args_list[0].args[4] == args
    assert dispatch.await_args_list[-1].args[4] == args
    assert dispatch.await_args_list[1].args[4]['visual_context']['operation']['name'] == tool.name
    heal.assert_not_called()


@pytest.mark.parametrize('state', ['intent_recorded', 'unknown'])
@pytest.mark.asyncio
async def test_owned_selected_resumes_original_invocation_without_replaying(monkeypatch, state):
    tool = BossGreetSelectedTool()
    args = {'name': '张测试', 'candidate_ref': REF}
    saved = {'request': {'arguments': args, 'tool_name': tool.name, 'device_id': 'old-device'},
        'invocation': {'id': 'old-invocation', 'state': state,
            'deadline_at': datetime.now(timezone.utc) + timedelta(seconds=60)}}
    operation = SimpleNamespace(branch='device', ordinal=0,
        owner=SimpleNamespace(saved_phase=AsyncMock(return_value=saved)))
    gate, dispatch = AsyncMock(), AsyncMock()
    poll = AsyncMock(return_value={'success': False, 'effect': 'unknown', 'invocation_id': 'old-invocation'})
    monkeypatch.setattr(tool, '_find_ready_device', gate)
    monkeypatch.setattr(tool, '_dispatch_and_wait', dispatch)
    monkeypatch.setattr(tool, '_poll_invocation', poll)
    with pytest.raises(LocalContinuationRequired) as error:
        await overlay_flow.poll_operation(tool, operation, normalize_provided_parameters(tool, args),
            tenant_id='tenant', user_id='user', session_id='session', progress_queue=None)
    assert str(error.value) == 'LOCAL_EFFECT_VERIFICATION_REQUIRED'
    poll.assert_awaited_once()
    gate.assert_not_called()
    dispatch.assert_not_called()
