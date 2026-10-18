"""Independent shared recovery acceptance at the real proxy/durable boundaries."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config.settings import settings
from src.local_tools.boss_visual_recovery import recover_operation, recovery_context
from src.local_tools.durable_flow import LocalContinuationRequired
from src.local_tools.overlay_flow import _known, run_base
from src.local_tools.lifecycle import LocalOperationScope, current_local_operation
from src.local_tools.proxy_tool import BossFilterTool, BossOpenDetailTool, LocalToolProxyTool

pytestmark = pytest.mark.unit
BROWSER_REF = '1af9aa74-becd-41c6-ac57-d338b69a8ed6'


class FutureReadAdapter(BossOpenDetailTool):
    """A future fourth adapter must need no shared-flow selector changes."""
    name = 'boss_independent_preview'
    visual_recovery_policy = 'readonly'


class FutureWriteAdapter(FutureReadAdapter):
    name = 'boss_independent_send'
    visual_recovery_policy = 'observe_only'


def failure(proxy):
    external = proxy.visual_recovery_policy == 'observe_only'
    return {'success': False, 'code': 'EXECUTION_UNKNOWN', 'effect': 'unknown',
        'invocation_id': 'original-id', 'data': {'completed': ['preserved'], 'operation_recovery': {
            'schema_version': 1, 'operation_class': 'external_effect' if external else proxy.visual_recovery_policy,
            'operation': {'name': proxy.name, 'goal': '原始目标', 'expected': '稳定目标证据',
                'target_label': '原始候选人', 'result_kind': 'business_result' if external else 'page_state'},
            'execution': {'stage': 'input', 'input_status': 'unknown', 'attempts_used': 2, 'ordinary_retries_used': 1},
            'verification': {'status': 'inconclusive', 'evidence': ['调用已返回，业务结果未确认']}}}}


def observation(*, obstruction='present', goal='not_satisfied', action=True, receipt='diag'):
    return {'success': True, 'data': {'recovery_ref': f'ref-{receipt}' if action else None, 'visual': {
        'http_status': 200, 'state': 'completed', 'receipt': {'request': receipt, 'factor': 20},
        'diagnosis': {'obstruction': obstruction, 'goal_state': goal,
            'proposed_action': {'kind': 'dismiss'} if action else None}}}}


def restored():
    return observation(obstruction='absent', goal='satisfied', action=False, receipt='post')


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(settings.boss_tool_billing, 'overlay_heal_enabled', True)
    monkeypatch.setattr(settings.boss_tool_billing, 'visual_recovery_max_steps', 3)


@pytest.mark.parametrize('bound', [False, True])
@pytest.mark.parametrize('proxy_type', [FutureReadAdapter, FutureWriteAdapter, BossFilterTool])
async def test_real_direct_proxy_uses_server_policy_and_keeps_original_device(monkeypatch, proxy_type, bound):
    proxy = proxy_type(); source = failure(proxy)
    business_args = {'experience': '5-10年'} if proxy_type is BossFilterTool else {'name': '原始候选人'}
    if bound:
        source['data']['browser_target_ref'] = BROWSER_REF
        source['data']['operation_recovery']['browser_target_ref'] = BROWSER_REF
    replies = iter([source, observation(), {'success': True}, restored(),
        {'success': True, 'data': {'stable': True}}])
    calls = []
    monkeypatch.setattr(LocalToolProxyTool, '_find_ready_device', AsyncMock(return_value=({'id': 'fixed-device'}, None)))
    monkeypatch.setattr(LocalToolProxyTool, '_tool_credit_price', lambda _: 0)
    legacy = AsyncMock(side_effect=AssertionError('Modern recovery must not stack legacy healing/fees'))
    monkeypatch.setattr(LocalToolProxyTool, '_heal_overlay', legacy)
    async def dispatch(self, tenant, user, session, device, arguments, progress):
        calls.append((self.name, device['id'], deepcopy(arguments)))
        return next(replies)
    monkeypatch.setattr(LocalToolProxyTool, '_dispatch_and_wait', dispatch)
    result = await proxy.execute(**business_args, _trusted_tenant_id='tenant', _trusted_user_id='user')
    assert all(device == 'fixed-device' for _, device, _ in calls)
    assert len(result['data']['visual_recovery']['receipts']) == 2
    if bound:
        assert all(arguments['_boss_target_ref'] == BROWSER_REF for _, _, arguments in calls[1:])
        assert all('browser_target_ref' not in arguments.get('visual_context', {}) for _, _, arguments in calls)
    if proxy_type is not FutureWriteAdapter:
        expected_args = {**business_args, **({'_boss_target_ref': BROWSER_REF} if bound else {})}
        assert result['success'] and calls[-1] == (proxy.name, 'fixed-device', expected_args)
    else:
        assert result['code'] == 'EXECUTION_UNKNOWN' and result['effect'] == 'unknown'
        assert result['data']['completed'] == ['preserved']
        assert sum(name == proxy.name for name, _, _ in calls) == 1
    legacy.assert_not_awaited()


@pytest.mark.parametrize('bound', [False, True])
@pytest.mark.parametrize('proxy_type', [FutureReadAdapter, FutureWriteAdapter, BossFilterTool])
async def test_real_durable_flow_keeps_unknown_close_read_only_and_never_replays_external(monkeypatch, proxy_type, bound):
    proxy = proxy_type(); source = failure(proxy)
    args = {'experience': '5-10年'} if proxy_type is BossFilterTool else {'name': '原始候选人'}
    if bound:
        source['data']['browser_target_ref'] = BROWSER_REF
        source['data']['operation_recovery']['browser_target_ref'] = BROWSER_REF
    phase = {'request': {'tool_name': proxy.name, 'arguments': args, 'device_id': 'fixed-device'},
        'invocation': {'id': source['invocation_id'], 'deadline_at': datetime.now(timezone.utc)}}
    owner = SimpleNamespace(saved_phase=AsyncMock(side_effect=lambda **kw: phase if kw['branch'] == 'main' else None))
    monkeypatch.setattr(LocalToolProxyTool, '_poll_invocation', AsyncMock(return_value=source))
    monkeypatch.setattr(LocalToolProxyTool, '_find_ready_device', AsyncMock(return_value=({'id': 'fixed-device'}, None)))
    monkeypatch.setattr(LocalToolProxyTool, '_tool_credit_price', lambda _: 0)
    replies = iter([observation(), {'success': False, 'code': 'EXECUTION_UNKNOWN', 'effect': 'unknown'},
        restored(), {'success': True, 'data': {'stable': True}}])
    calls = []
    async def dispatch(self, tenant, user, session, device, arguments, progress):
        calls.append((self.name, current_local_operation().branch, device['id'], deepcopy(arguments)))
        return next(replies)
    monkeypatch.setattr(LocalToolProxyTool, '_dispatch_and_wait', dispatch)
    result = await run_base(proxy, LocalOperationScope(owner, 'main', 0),
        {**args, '_trusted_tenant_id': 'tenant', '_trusted_user_id': 'user'})
    assert all(device == 'fixed-device' for _, _, device, _ in calls)
    assert calls[1][1] == 'heal.visual.step0.dismiss'
    assert calls[2][1] == 'heal.visual.step0.postcheck'
    assert calls[2][3]['visual_context']['execution']['input_status'] == 'unknown'
    if bound:
        assert all(arguments['_boss_target_ref'] == BROWSER_REF for _, _, _, arguments in calls)
        assert all('browser_target_ref' not in arguments.get('visual_context', {}) for _, _, _, arguments in calls)
    if proxy_type is not FutureWriteAdapter:
        assert result['success'] and calls[-1][0] == proxy.name
        assert calls[-1][3] == {**args, **({'_boss_target_ref': BROWSER_REF} if bound else {})}
    else:
        assert not result['success'] and all(name != proxy.name for name, *_ in calls)
        assert result['data']['visual_recovery']['state'] == 'page_restored_business_unconfirmed'


@pytest.mark.parametrize('change', [
    lambda facts: facts.update(schema_version=True),
    lambda facts: facts.update(operation_class='page_state'),
    lambda facts: facts['operation'].update(name='boss_other'),
    lambda facts: facts['operation'].update(result_kind='business_result'),
    lambda facts: facts['execution'].update(ordinary_retries_used=True),
    lambda facts: facts['execution'].update(ordinary_retries_used=2),
    lambda facts: facts['verification'].update(status='satisfied', evidence=[]),
    lambda facts: facts['verification'].update(evidence=['x' * 501]),
])
def test_bad_new_adapter_facts_never_authorize_unknown_replay(change):
    proxy = FutureReadAdapter(); source = failure(proxy)
    change(source['data']['operation_recovery'])
    assert recovery_context(proxy, source) is None
    with pytest.raises(LocalContinuationRequired):
        _known(source, proxy)


@pytest.mark.parametrize('root_ref,fact_ref', [('bad-ref', None), (BROWSER_REF, '1af9aa74-becd-41c6-ac57-d338b69a8ed7'),
    (None, BROWSER_REF), (True, BROWSER_REF)])
def test_corrupt_browser_binding_cannot_authorize_recovery(root_ref, fact_ref):
    proxy = FutureReadAdapter(); source = failure(proxy)
    source['data']['browser_target_ref'] = root_ref
    source['data']['operation_recovery']['browser_target_ref'] = fact_ref
    assert recovery_context(proxy, source) is None


async def test_page_postcheck_capture_failure_uses_window_without_replaying_close():
    proxy = FutureReadAdapter(); source = failure(proxy)
    calls = []
    async def dispatch(name, branch, args):
        calls.append((name, branch, args))
        if branch == 'step0.diagnose':
            return observation()
        if branch == 'step0.dismiss':
            return {'success': True}
        if branch == 'step0.postcheck':
            return {'success': False, 'code': 'CHROME_UNAVAILABLE'}
        if branch == 'step0.postcheck.window':
            return restored()
        if branch == 'resume':
            return {'success': True, 'data': {'stable': True}}
        raise AssertionError(branch)
    result = await recover_operation(proxy, source, dispatch)
    assert result['success'], 'A blocked page observation must use bound window readback before giving up'
    assert sum(name == 'boss_overlay_dismiss' for name, _, _ in calls) == 1


async def test_underlying_goal_ready_cannot_hide_native_obstruction_after_direct_input_failure():
    proxy = FutureReadAdapter(); source = failure(proxy)
    dispatch = AsyncMock(side_effect=[restored(), observation(receipt='window-diag'),
        {'success': True}, restored(), {'success': True, 'data': {'stable': True}}])
    result = await recover_operation(proxy, source, dispatch)
    assert result['success']
    calls = dispatch.await_args_list
    assert [(c.args[0], c.args[1]) for c in calls] == [('boss_overlay_inspect', 'step0.diagnose'),
        ('boss_overlay_inspect', 'step0.diagnose.window'), ('boss_overlay_dismiss', 'step0.dismiss'),
        ('boss_overlay_inspect', 'step0.postcheck'), (proxy.name, 'resume')]
    assert calls[1].args[2]['observation_mode'] == 'window'
    assert calls[3].args[2]['observation_mode'] == 'window'


@pytest.mark.parametrize('window_obstruction', ['present', 'unknown'])
async def test_external_unknown_does_not_claim_page_restored_from_a_native_blind_page_image(window_obstruction):
    proxy = FutureWriteAdapter(); source = failure(proxy)
    dispatch = AsyncMock(side_effect=[observation(obstruction='absent', goal='inconclusive', action=False),
        observation(obstruction=window_obstruction, goal='inconclusive', action=False)])
    result = await recover_operation(proxy, source, dispatch)
    assert result['code'] == 'EXECUTION_UNKNOWN' and result['effect'] == 'unknown'
    assert not result['data']['visual_recovery'].get('page_restored')
    assert all(call.args[0] == 'boss_overlay_inspect' for call in dispatch.await_args_list)
    assert dispatch.await_count == 2
    assert dispatch.await_args_list[-1].args[2]['observation_mode'] == 'window'


async def test_next_diagnosis_explains_the_actual_prior_escape_and_unknown_input_instead_of_replaying_it():
    proxy = FutureReadAdapter(); source = failure(proxy)
    first = observation(receipt='escape')
    first['data']['visual']['diagnosis']['proposed_action'] = {'kind': 'press_escape'}
    dispatch = AsyncMock(side_effect=[first, {'success': False, 'code': 'EXECUTION_UNKNOWN'},
        observation(action=False, receipt='still-present'), observation(receipt='cancel'),
        {'success': True}, restored(), {'success': True}])
    result = await recover_operation(proxy, source, dispatch)
    assert result['success']
    second_context = dispatch.await_args_list[3].args[2]['visual_context']
    assert any('press_escape' in text and 'unknown' in text for text in second_context['verification']['evidence'])
    assert dispatch.await_args_list[4].args[2]['recovery_ref'] == 'ref-cancel'


async def test_dismiss_postcheck_has_its_own_goal_while_next_diagnosis_and_resume_keep_the_original_candidate():
    proxy = BossOpenDetailTool(); source = failure(proxy)
    operation = source['data']['operation_recovery']['operation']
    operation.update(goal='打开王欣详情', expected='简历画布、右侧打招呼和经历概览可见', target_label='王欣')
    saved_source = deepcopy(source)
    dispatch = AsyncMock(side_effect=[observation(receipt='first'), {'success': False, 'code': 'EXECUTION_UNKNOWN'},
        observation(action=False, receipt='still-present'), observation(receipt='second'),
        {'success': True}, restored(), {'success': True, 'data': {'opened': True}}])
    result = await recover_operation(proxy, source, dispatch)
    assert result['success'] and result['data']['opened']
    calls = dispatch.await_args_list
    for index in [2, 5]:
        context = calls[index].args[2]['visual_context']
        assert context['purpose'] == 'verify' and context['available_actions'] == []
        assert context['operation']['name'] == 'boss_overlay_dismiss'
        assert context['operation']['result_kind'] == 'page_state'
        assert 'target_label' not in context['operation']
        assert '王欣' not in str(context['operation'])
        assert '经历概览' not in str(context['operation'])
    assert calls[3].args[2]['visual_context']['operation'] == operation
    assert calls[-1].args == ('boss_open_detail', 'resume', None)
    assert source == saved_source


async def test_pending_readback_is_bounded_same_request_and_preserves_pending_evidence():
    proxy = FutureReadAdapter(); source = failure(proxy)
    pending = {'success': True, 'data': {'visual': {'http_status': 202, 'state': 'pending'}}}
    dispatch = AsyncMock(return_value=pending)
    result = await recover_operation(proxy, source, dispatch)
    assert dispatch.await_count == 3
    args = [call.args[2] for call in dispatch.await_args_list]
    assert len({item['request_id'] for item in args}) == 1
    assert all(item.get('retrieve_only') is True and 'visual_context' not in item for item in args[1:])
    assert result['data']['visual_recovery']['observations'][0]['state'] == 'pending'
    assert not result['success'] and not result['data']['visual_recovery']['steps']


async def test_already_consumed_reference_is_read_back_without_replaying_its_input():
    proxy = FutureReadAdapter(); source = failure(proxy)
    dispatch = AsyncMock(side_effect=[observation(), {'success': False, 'code': 'UI_CHANGED',
        'data': {'recovery_action_error': 'ALREADY_CONSUMED', 'input_status': 'not_started'}}, restored(),
        {'success': True, 'data': {'stable': True}}])
    result = await recover_operation(proxy, source, dispatch)
    assert result['success']
    assert sum(call.args[0] == 'boss_overlay_dismiss' for call in dispatch.await_args_list) == 1
    post = dispatch.await_args_list[2].args[2]['visual_context']
    assert post['purpose'] == 'verify' and post['available_actions'] == []
    assert post['execution']['input_status'] == 'unknown'


async def test_step_limit_is_independent_of_used_ordinary_retry_and_unknown_close_is_not_replayed(monkeypatch):
    monkeypatch.setattr(settings.boss_tool_billing, 'visual_recovery_max_steps', 2)
    proxy = FutureReadAdapter(); source = failure(proxy)
    dispatch = AsyncMock(side_effect=[observation(receipt='diag1'), {'success': False, 'code': 'EXECUTION_UNKNOWN'},
        observation(action=False, receipt='post1'), observation(receipt='diag2'), {'success': True},
        observation(action=False, receipt='post2')])
    result = await recover_operation(proxy, source, dispatch)
    recovery = result['data']['visual_recovery']
    assert recovery['state'] == 'recovery_limit_reached' and recovery['max_steps'] == 2
    assert len(recovery['steps']) == 2 and len(recovery['receipts']) == 4
    assert recovery['steps'][0]['input_status'] == 'unknown'
    assert source['data']['operation_recovery']['execution']['ordinary_retries_used'] == 1
    refs = [c.args[2]['recovery_ref'] for c in dispatch.await_args_list if c.args[0] == 'boss_overlay_dismiss']
    assert refs == ['ref-diag1', 'ref-diag2']
