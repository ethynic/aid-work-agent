"""Page repair must keep completed work and only resume a proven unsubmitted effect."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config.settings import settings
from src.local_tools.boss_visual_recovery import (
    is_visual_failure, public_recovery_metadata, recover_operation, recovery_arguments, recovery_context,
)
from src.local_tools import proxy_tool as tools

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings.boss_tool_billing, 'overlay_heal_enabled', True)


def receipt(name='boss_send_current', policy='observe_only'):
    proxy = SimpleNamespace(name=name, heal_eligible=True, visual_recovery_policy=policy)
    facts = {'schema_version': 1, 'operation_class': 'external_effect' if policy == 'observe_only' else policy,
        'operation': {'name': name, 'goal': '原授权目标', 'expected': '原授权目标的后验',
            'target_label': '原联系人', 'result_kind': 'business_result' if policy == 'observe_only' else 'page_state'},
        'execution': {'stage': 'input', 'input_status': 'attempted', 'attempts_used': 1, 'ordinary_retries_used': 0},
        'verification': {'status': 'inconclusive', 'evidence': ['准备输入后未能继续']},
        'business_effect': {'status': 'not_started', 'completed_count': 0}, 'recovery_contact_ref': 'a' * 64}
    if policy != 'observe_only':
        facts.pop('business_effect')
    return proxy, {'success': False, 'code': 'UI_CHANGED', 'effect': 'none', 'invocation_id': 'original',
        'data': {'operation_recovery': facts}}


def image(obstructed=True):
    return {'success': True, 'data': {'recovery_ref': 'saved-reference' if obstructed else None,
        'visual': {'http_status': 200, 'state': 'completed', 'receipt': {'factor': 20}, 'diagnosis': {
            'obstruction': 'present' if obstructed else 'absent',
            'goal_state': 'not_satisfied' if obstructed else 'satisfied',
            'proposed_action': {'kind': 'dismiss'} if obstructed else None}}}}


async def test_preparation_error_can_repair_and_continue_without_a_blanket_external_stop():
    proxy, source = receipt()
    dispatch = AsyncMock(side_effect=[image(), {'success': True}, image(False), {'success': True, 'data': {'sent': True}}])
    result = await recover_operation(proxy, source, dispatch)
    assert result['success'] and result['data']['sent']
    assert dispatch.await_args_list[-1].args == (proxy.name, 'resume', None)
    args = recovery_arguments(source, {'message': '原文'}, None)
    assert args == {'message': '原文', '_boss_expected_contact_ref': 'a' * 64}
    assert 'recovery_contact_ref' not in dispatch.await_args_list[0].args[2]['visual_context']


@pytest.mark.parametrize('change', [
    lambda r: r.update(effect='unknown'),
    lambda r: r.update(code='EXECUTION_UNKNOWN'),
    lambda r: r['data']['operation_recovery'].pop('recovery_contact_ref'),
    lambda r: r['data']['operation_recovery']['business_effect'].update(status='unknown'),
    lambda r: r['data']['operation_recovery']['business_effect'].update(status='confirmed', completed_count=1),
])
async def test_unknown_completed_or_unbound_current_effect_is_never_reissued(change):
    proxy, source = receipt()
    change(source)
    dispatch = AsyncMock(side_effect=[image(), {'success': True}, image(False)])
    result = await recover_operation(proxy, source, dispatch)
    assert all(c.args[0] != proxy.name for c in dispatch.await_args_list)
    assert result['effect'] == source['effect']


@pytest.mark.parametrize('count', [True, -1, 1])
def test_not_started_does_not_admit_an_invalid_completed_count(count):
    proxy, source = receipt()
    source['data']['operation_recovery']['business_effect']['completed_count'] = count
    assert recovery_context(proxy, source) is None


async def test_read_payload_is_preserved_when_only_cleanup_needs_visual_repair():
    proxy, source = receipt('boss_resume_batch', 'readonly')
    source.update(success=True, code='OK')
    source['data'].update(resumes=[{'candidate_name': '已读者', 'image_base64': 'private'}], failures=[{'name': '未读者'}])
    facts = source['data']['operation_recovery']
    facts['operation']['result_kind'] = 'business_result'
    facts['verification'] = {'status': 'satisfied', 'evidence': ['资料已完整读取，关闭待确认']}
    facts['error'] = {'code': 'UI_CHANGED', 'source': 'postcheck', 'summary': '关闭详情未生效'}
    original = deepcopy(source)
    assert is_visual_failure(proxy, source)
    dispatch = AsyncMock(side_effect=[image(), {'success': True}, image(False)])
    result = await recover_operation(proxy, source, dispatch)
    assert result['data']['resumes'] == original['data']['resumes']
    assert result['data']['failures'] == original['data']['failures']
    assert result['data']['visual_recovery']['state'] == 'page_restored_result_preserved'
    assert all(c.args[0] != proxy.name for c in dispatch.await_args_list)
    assert source == original
    assert 'resumes' not in public_recovery_metadata(result)


async def test_current_read_needs_its_original_contact_and_does_not_switch_to_a_new_one():
    proxy, source = receipt('boss_read_chat', 'readonly')
    assert recovery_arguments(source, {}, None)['contact'] == '原联系人'
    source['data']['operation_recovery']['operation'].pop('target_label')
    dispatch = AsyncMock(side_effect=[image(), {'success': True}, image(False)])
    result = await recover_operation(proxy, source, dispatch)
    assert result['data']['visual_recovery']['state'] == 'page_restored_target_unbound'
    assert all(c.args[0] != proxy.name for c in dispatch.await_args_list)


@pytest.mark.parametrize('cls,policy', [
    (tools.BossGotoTool, 'page_state'), (tools.BossSelectJobTool, 'page_state'),
    (tools.BossOpenChatTool, 'page_state'), (tools.BossCloseDetailTool, 'page_state'),
    (tools.BossInterviewDemoTool, 'page_state'), (tools.BossFilterOptionsTool, 'readonly'),
    (tools.BossListJobsTool, 'readonly'), (tools.BossLookupCandidatesTool, 'readonly'),
    (tools.BossReadChatTool, 'readonly'), (tools.BossResumeDetailTool, 'readonly'),
    (tools.BossResumeBatchTool, 'readonly'), (tools.BossResumeNextTool, 'readonly'),
    (tools.BossGreetTool, 'observe_only'), (tools.BossGreetSelectedTool, 'observe_only'),
    (tools.BossGreetDetailTool, 'observe_only'), (tools.BossAcceptResumeTool, 'observe_only'),
    (tools.BossRejectCurrentTool, 'observe_only'), (tools.BossSendToTool, 'observe_only'),
    (tools.BossSendCurrentTool, 'observe_only'),
])
def test_all_remaining_page_adapters_declare_server_owned_recovery_policy(cls, policy):
    adapter = cls()
    assert adapter.heal_eligible and adapter.visual_recovery_policy == policy
