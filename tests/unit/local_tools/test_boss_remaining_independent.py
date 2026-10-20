"""Independent acceptance: repair UI without losing an outcome or replaying an unknown send."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config.settings import settings
from src.local_tools.boss_visual_recovery import (
    recover_operation, recovery_arguments, recovery_context, public_recovery_metadata,
)
from src.local_tools.lifecycle import LocalOperationScope
from src.local_tools.overlay_flow import run_base
from src.local_tools.proxy_tool import (
    BossFilterTool, BossReadChatTool, BossResumeBatchTool, BossSendCurrentTool,
    LocalToolProxyTool,
)

pytestmark = pytest.mark.unit
TARGET = '1af9aa74-becd-41c6-ac57-d338b69a8ed6'
CONTACT = 'c' * 64


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings.boss_tool_billing, 'overlay_heal_enabled', True)
    monkeypatch.setattr(settings.boss_tool_billing, 'visual_recovery_max_steps', 3)


def original(proxy, *, retained=False):
    external = proxy.visual_recovery_policy == 'observe_only'
    facts = {
        'schema_version': 1, 'operation_class': 'external_effect' if external else proxy.visual_recovery_policy,
        'operation': {'name': proxy.name, 'goal': '关闭未收起的详情，保留原读取成果',
            'expected': '详情画布及右栏消失，推荐列表恢复可读', 'target_label': '原目标甲',
            'result_kind': 'business_result' if retained or external else 'page_state'},
        'execution': {'stage': 'postcheck', 'input_status': 'attempted', 'attempts_used': 2, 'ordinary_retries_used': 1},
        'verification': {'status': 'inconclusive', 'evidence': ['收尾后态仍不能确认']},
        'error': {'code': 'UI_CHANGED', 'source': 'postcheck', 'summary': '收尾界面没有关闭'},
        'browser_target_ref': TARGET,
    }
    if external:
        facts.update(business_effect={'status': 'not_started', 'completed_count': 0}, recovery_contact_ref=CONTACT)
    data = {'operation_recovery': facts, 'browser_target_ref': TARGET}
    if retained:
        data.update(resumes=[{'candidate_name': '已读取甲', 'images': [{'base64': 'private-image'}]}],
                    failures=[{'name': '未读取乙', 'error': '原错误'}], processed_names=['已读取甲'])
    return {'success': retained, 'code': 'OK' if retained else 'UI_CHANGED', 'effect': 'none',
            'invocation_id': 'independent-original', 'data': data}


def visual(*, present=True, goal=None, reference='stored-action'):
    return {'success': True, 'data': {'recovery_ref': reference if present else None, 'visual': {
        'http_status': 200, 'state': 'completed', 'receipt': {'factor': 20},
        'diagnosis': {'obstruction': 'present' if present else 'absent',
            'goal_state': goal or ('not_satisfied' if present else 'satisfied'),
            'proposed_action': {'kind': 'dismiss'} if present else None}}}}


@pytest.mark.parametrize('durable', [False, True])
@pytest.mark.parametrize('canonical', [None, {}, {'schema_version': True}])
async def test_bad_canonical_receipt_cannot_fall_through_to_legacy_click_or_fee(monkeypatch, durable, canonical):
    """A partially upgraded CLI receipt must never accidentally acquire the old blind retry."""
    proxy = BossFilterTool()
    source = original(proxy)
    source['data']['page_operation'] = deepcopy(source['data']['operation_recovery'])
    source['data']['operation_recovery'] = canonical
    monkeypatch.setattr(LocalToolProxyTool, '_tool_credit_price', lambda _: 0)
    monkeypatch.setattr(LocalToolProxyTool, '_find_ready_device', AsyncMock(return_value=({'id': 'device-a'}, None)))
    dispatch = AsyncMock(return_value=source)
    monkeypatch.setattr(LocalToolProxyTool, '_dispatch_and_wait', dispatch)
    legacy = AsyncMock(side_effect=AssertionError('invalid canonical facts must not invoke legacy healing'))
    monkeypatch.setattr(LocalToolProxyTool, '_heal_overlay', legacy)
    if durable:
        owner = SimpleNamespace(saved_phase=AsyncMock(return_value=None))
        result = await run_base(proxy, LocalOperationScope(owner, 'main', 0), {
            'experience': '5-10年', '_trusted_tenant_id': 'tenant-a', '_trusted_user_id': 'user-a'})
    else:
        result = await proxy.execute(experience='5-10年', _trusted_tenant_id='tenant-a', _trusted_user_id='user-a')
    assert result == source
    assert dispatch.await_count == 1
    legacy.assert_not_awaited()


@pytest.mark.parametrize('obstruction,goal,expected_state', [
    (False, 'satisfied', 'page_restored_result_preserved'),
    (False, 'not_satisfied', 'no_supported_action'),
    (False, 'inconclusive', 'no_supported_action'),
])
async def test_completed_read_is_not_itself_proof_that_cleanup_is_finished(obstruction, goal, expected_state):
    """A screenshot model must assess the pending close goal, not re-prove available image bytes."""
    proxy = BossResumeBatchTool()
    source = original(proxy, retained=True)
    saved = deepcopy(source)
    response = visual(present=obstruction, goal=goal)
    dispatch = AsyncMock(return_value=response)
    result = await recover_operation(proxy, source, dispatch)
    context = dispatch.await_args_list[0].args[2]['visual_context']
    assert context['operation']['result_kind'] == 'page_state'
    assert context['operation']['goal'] == '关闭未收起的详情，保留原读取成果'
    assert 'resumes' not in str(context) and 'private-image' not in str(context)
    assert all(call.args[0] == 'boss_overlay_inspect' for call in dispatch.await_args_list)
    assert result['success'] is True
    assert result['data']['resumes'] == source['data']['resumes']
    assert result['data']['failures'] == source['data']['failures']
    assert result['data']['visual_recovery']['state'] == expected_state
    assert source == saved
    assert 'resumes' not in public_recovery_metadata(result)


@pytest.mark.parametrize('change', [
    lambda result: result.update(effect='partial'),
    lambda result: result.update(effect='applied'),
    lambda result: result.update(effect='unknown'),
    lambda result: result.update(code='EXECUTION_UNKNOWN'),
    lambda result: result['data']['operation_recovery']['business_effect'].update(status='confirmed', completed_count=1),
    lambda result: result['data']['operation_recovery']['business_effect'].update(status='unknown', completed_count=0),
    lambda result: result['data']['operation_recovery'].pop('recovery_contact_ref'),
])
async def test_original_effect_evidence_is_required_even_when_model_declares_page_ready(change):
    """Page readiness can never turn an unknown/completed business action into permission to resend."""
    proxy = BossSendCurrentTool()
    source = original(proxy)
    change(source)
    dispatch = AsyncMock(return_value=visual(present=False))
    result = await recover_operation(proxy, source, dispatch)
    assert all(call.args[0] == 'boss_overlay_inspect' for call in dispatch.await_args_list)
    assert result['success'] == source['success'] and result['effect'] == source['effect']
    assert result['data']['visual_recovery']['page_restored'] is True


async def test_unsubmitted_bound_current_send_resumes_only_exact_original_contact_and_body():
    """A known zero-submit failure may continue; do not apply a blanket external-action stop."""
    proxy = BossSendCurrentTool()
    source = original(proxy)
    dispatch = AsyncMock(side_effect=[visual(), {'success': True}, visual(present=False),
        {'success': True, 'effect': 'applied', 'data': {'sent': True}}])
    result = await recover_operation(proxy, source, dispatch)
    assert result['success'] and result['data']['sent']
    assert dispatch.await_args_list[-1].args == ('boss_send_current', 'resume', None)
    intent = {'message': '原始全文', 'dry_run': False}
    saved = deepcopy(intent)
    assert recovery_arguments(source, intent, None) == {
        **intent, '_boss_target_ref': TARGET, '_boss_expected_contact_ref': CONTACT}
    assert intent == saved
    for call in dispatch.await_args_list[:-1]:
        if call.args[2] and 'visual_context' in call.args[2]:
            context = str(call.args[2]['visual_context'])
            assert CONTACT not in context and TARGET not in context


async def test_first_implicit_read_identity_remains_pinned_across_recovery():
    """The model may close a panel, but must not silently change whose conversation gets read."""
    proxy = BossReadChatTool()
    source = original(proxy)
    args = {}
    assert recovery_context(proxy, source) is not None
    restored = recovery_arguments(source, args, None)
    assert restored['contact'] == '原目标甲' and restored['_boss_target_ref'] == TARGET
    assert args == {}
    phase = recovery_arguments(source, args, {'request_id': 'readback', 'retrieve_only': True})
    assert phase == {'request_id': 'readback', 'retrieve_only': True, '_boss_target_ref': TARGET}


async def test_unknown_close_and_failed_cleanup_keep_read_payload_once_without_dispatching_batch_again():
    """A paid batch with useful images survives even when its last close cannot be confirmed."""
    proxy = BossResumeBatchTool()
    source = original(proxy, retained=True)
    dispatch = AsyncMock(side_effect=[visual(), {'success': False, 'code': 'EXECUTION_UNKNOWN'},
        {'success': True, 'data': {'visual': {'http_status': 409, 'state': 'model_unknown'}}}])
    result = await recover_operation(proxy, source, dispatch)
    assert result['success'] and result['data']['resumes'] == source['data']['resumes']
    assert result['data']['visual_recovery']['state'] == 'postcheck_inconclusive'
    assert not result['data']['visual_recovery'].get('page_restored')
    assert [call.args[0] for call in dispatch.await_args_list] == [
        'boss_overlay_inspect', 'boss_overlay_dismiss', 'boss_overlay_inspect']
    assert dispatch.await_args_list[-1].args[2]['visual_context']['available_actions'] == []
