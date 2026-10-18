"""Shared flow intent: new adapters share repair without gaining replay permission."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config.settings import settings
from src.local_tools.boss_visual_recovery import recover_operation, recovery_context, recovery_arguments

pytestmark = pytest.mark.unit


def source(name='boss_read_chat', policy='readonly'):
    proxy = SimpleNamespace(name=name, heal_eligible=True, visual_recovery_policy=policy)
    facts = {'schema_version': 1, 'operation_class': 'external_effect' if policy == 'observe_only' else policy,
        'operation': {'name': name, 'goal': '完成原授权目标', 'expected': '原目标的稳定后态', 'target_label': '原目标',
            'result_kind': 'business_result' if policy == 'observe_only' else 'page_state'},
        'execution': {'stage': 'input', 'input_status': 'unknown', 'attempts_used': 3, 'ordinary_retries_used': 1},
        'verification': {'status': 'inconclusive', 'evidence': ['缺少完整后态']}}
    return proxy, {'success': False, 'code': 'EXECUTION_UNKNOWN', 'effect': 'unknown', 'invocation_id': 'original-id',
        'data': {'operation_recovery': facts, 'completed_items': ['already-done']}}


def vision(obstruction='present', goal='not_satisfied', action='dismiss', ref='11111111-1111-4111-8111-111111111111', **extra):
    return {'success': True, 'data': {'recovery_ref': ref if action else None,
        'visual': {'http_status': 200, 'state': 'completed', 'receipt': {'factor': 20},
            'diagnosis': {'obstruction': obstruction, 'goal_state': goal,
                'proposed_action': {'kind': action} if action else None}, **extra}}}


def clear():
    return vision('absent', 'satisfied', action=None)


async def test_fourth_adapter_uses_the_same_flow_without_adding_a_tool_name_to_it():
    proxy, original = source()
    dispatch = AsyncMock(side_effect=[vision(), {'success': True}, clear(), {'success': True, 'data': {'messages': []}}])
    result = await recover_operation(proxy, original, dispatch)
    assert result['success'] and result['data']['messages'] == []
    assert dispatch.await_args_list[-1].args == ('boss_read_chat', 'resume', None)
    assert dispatch.await_args_list[0].args[2]['observation_mode'] == 'page'
    assert dispatch.await_args_list[0].args[2]['visual_context']['available_actions'] == ['dismiss_click', 'press_escape']


async def test_external_unknown_can_repair_the_page_without_replaying_the_business_action():
    proxy, original = source('boss_send_current', 'observe_only')
    dispatch = AsyncMock(side_effect=[vision(), {'success': True}, clear()])
    result = await recover_operation(proxy, original, dispatch)
    assert result['code'] == 'EXECUTION_UNKNOWN' and result['effect'] == 'unknown'
    assert result['data']['completed_items'] == ['already-done']
    assert result['data']['visual_recovery']['state'] == 'page_restored_business_unconfirmed'
    assert all(call.args[0] != 'boss_send_current' for call in dispatch.await_args_list)
    post = dispatch.await_args_list[-1].args[2]['visual_context']
    assert post['operation']['result_kind'] == 'page_state'
    assert post['operation']['name'] == 'boss_overlay_dismiss'
    assert 'target_label' not in post['operation']
    assert post['available_actions'] == []


async def test_two_obstructions_are_rediagnosed_with_fresh_references_before_resuming():
    proxy, original = source()
    dispatch = AsyncMock(side_effect=[vision(), {'success': True}, vision(action=None),
        vision(action='press_escape', ref='22222222-2222-4222-8222-222222222222'), {'success': True}, clear(), {'success': True}])
    result = await recover_operation(proxy, original, dispatch)
    assert result['success']
    steps = result['data']['visual_recovery']['steps']
    assert [step['action_kind'] for step in steps] == ['dismiss', 'press_escape']
    close = [call.args[2]['recovery_ref'] for call in dispatch.await_args_list if call.args[0] == 'boss_overlay_dismiss']
    assert len(set(close)) == 2
    assert len(result['data']['visual_recovery']['receipts']) == 4


async def test_stale_guard_is_zero_input_and_recaptures_instead_of_stopping():
    proxy, original = source()
    dispatch = AsyncMock(side_effect=[vision(), {'success': False, 'code': 'UI_CHANGED', 'data': {'recovery_action_error': 'STALE_OBSERVATION'}},
        vision(ref='22222222-2222-4222-8222-222222222222'), {'success': True}, clear(), {'success': True}])
    result = await recover_operation(proxy, original, dispatch)
    assert result['success']
    assert result['data']['visual_recovery']['steps'][0]['input_status'] == 'not_started'
    assert [call.args[1] for call in dispatch.await_args_list] == ['step0.diagnose', 'step0.dismiss', 'step1.diagnose', 'step1.dismiss', 'step1.postcheck', 'resume']


@pytest.mark.parametrize('page_result', [vision('absent', 'not_satisfied', action=None),
    {'success': False, 'code': 'CHROME_UNAVAILABLE'}])
async def test_page_cdp_failure_or_unexplained_input_failure_uses_window_observation(page_result):
    proxy, original = source()
    dispatch = AsyncMock(side_effect=[page_result, vision(), {'success': True}, clear(), {'success': True}])
    result = await recover_operation(proxy, original, dispatch)
    assert result['success']
    observations = [call.args[2]['observation_mode'] for call in dispatch.await_args_list if call.args[0] == 'boss_overlay_inspect']
    assert observations == ['page', 'window', 'window']


async def test_unknown_input_is_observed_without_replaying_its_reference():
    proxy, original = source()
    dispatch = AsyncMock(side_effect=[vision(), {'success': False, 'code': 'EXECUTION_UNKNOWN'}, clear(), {'success': True}])
    result = await recover_operation(proxy, original, dispatch)
    assert result['success']
    assert sum(call.args[0] == 'boss_overlay_dismiss' for call in dispatch.await_args_list) == 1
    assert dispatch.await_args_list[2].args[2]['visual_context']['execution']['input_status'] == 'unknown'


async def test_configured_limit_reports_current_obstruction_without_claiming_recovery(monkeypatch):
    monkeypatch.setattr(settings.boss_tool_billing, 'visual_recovery_max_steps', 2)
    proxy, original = source()
    dispatch = AsyncMock(side_effect=[vision(), {'success': True}, vision(action=None)] * 2)
    result = await recover_operation(proxy, original, dispatch)
    assert not result['success']
    assert result['data']['visual_recovery']['state'] == 'recovery_limit_reached'
    assert result['data']['visual_recovery']['last_state'] == 'obstruction_remains'
    assert dispatch.await_count == 6


async def test_pending_request_is_only_retrieved_with_the_same_id_and_no_new_image():
    proxy, original = source()
    pending = {'success': True, 'data': {'visual': {'state': 'pending', 'http_status': 202}}}
    dispatch = AsyncMock(side_effect=[pending, pending, pending])
    result = await recover_operation(proxy, original, dispatch)
    calls = dispatch.await_args_list
    assert len({call.args[2]['request_id'] for call in calls}) == 1
    assert all(call.args[2].get('retrieve_only') for call in calls[1:])
    assert result['data']['visual_recovery']['state'] == 'diagnosis_unavailable'


async def test_invalid_actions_keep_assessment_even_when_no_window_action_is_available():
    proxy, original = source()
    response = vision(action=None, action_error='INVALID_MODEL_ACTION')
    dispatch = AsyncMock(side_effect=[response, response])
    result = await recover_operation(proxy, original, dispatch)
    observations = result['data']['visual_recovery']['observations']
    assert len(observations) == 2
    assert all(o['diagnosis']['obstruction'] == 'present' and o['action_error'] == 'INVALID_MODEL_ACTION' for o in observations)


def test_client_page_claim_cannot_upgrade_external_server_policy_to_replay():
    proxy, original = source('boss_send_current', 'observe_only')
    original['data']['operation_recovery']['operation_class'] = 'page_state'
    assert recovery_context(proxy, original) is None


def test_corrupt_canonical_context_cannot_fall_back_to_valid_legacy_claim():
    proxy, original = source('boss_filter', 'page_state')
    original['data']['page_operation'] = deepcopy(original['data']['operation_recovery'])
    original['data']['operation_recovery']['schema_version'] = True
    assert recovery_context(proxy, original) is None


async def test_window_clear_can_confirm_repair_without_resolving_business_unknown():
    proxy, original = source('boss_send_current', 'observe_only')
    dispatch = AsyncMock(side_effect=[vision('unknown', 'inconclusive', action=None),
        vision('absent', 'inconclusive', action=None)])
    result = await recover_operation(proxy, original, dispatch)
    assert not result['success'] and result['effect'] == 'unknown'
    assert result['data']['visual_recovery']['page_restored'] is True
    assert dispatch.await_count == 2


async def test_definite_obstruction_is_rediagnosed_even_when_goal_readiness_is_unknown():
    proxy, original = source()
    dispatch = AsyncMock(side_effect=[vision(), {'success': True}, vision('present', 'inconclusive', action=None),
        vision(), {'success': True}, clear(), {'success': True}])
    result = await recover_operation(proxy, original, dispatch)
    assert result['success'] and len(result['data']['visual_recovery']['steps']) == 2


async def test_a_page_ready_without_window_visibility_does_not_prove_direct_native_input_can_resume():
    # A native obstruction is invisible in a page image even when the underlying state is ready.
    proxy, original = source()
    dispatch = AsyncMock(side_effect=[clear(), clear(), {'success': False, 'code': 'UI_CHANGED'}])
    result = await recover_operation(proxy, original, dispatch)
    assert not result['success']
    assert result['data']['visual_recovery']['state'] == 'original_goal_unconfirmed'
    assert dispatch.await_args_list[1].args[2]['observation_mode'] == 'window'


def test_original_tab_reference_is_local_and_added_to_every_phase_without_losing_intent():
    proxy, original = source('boss_independent_read', 'readonly')
    ref = '11111111-1111-4111-8111-111111111111'
    original['data']['browser_target_ref'] = ref
    original['data']['operation_recovery']['browser_target_ref'] = ref
    admitted = recovery_context(proxy, original)
    assert admitted and 'browser_target_ref' not in admitted[0]
    args = {'name': '原目标'}
    assert recovery_arguments(original, args, None) == {**args, '_boss_target_ref': ref}
    assert recovery_arguments(original, args, {'request_id': 'fresh'}) == {'request_id': 'fresh', '_boss_target_ref': ref}
    assert args == {'name': '原目标'}
    original['data']['operation_recovery']['browser_target_ref'] = '22222222-2222-4222-8222-222222222222'
    assert recovery_context(proxy, original) is None
