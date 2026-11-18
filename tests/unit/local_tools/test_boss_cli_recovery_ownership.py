"""A locally completed command must never acquire a second server repair/replay."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.local_tools.boss_visual_recovery import cli_owns_recovery, is_visual_failure, recover_operation
from src.local_tools.lifecycle import LocalOperationScope
from src.local_tools.overlay_flow import _known, run_base
from src.local_tools.proxy_tool import BossFilterTool, LocalToolProxyTool

pytestmark = pytest.mark.unit


def local_result(state='not_configured'):
    return {'success': False, 'code': 'EXECUTION_UNKNOWN', 'effect': 'unknown',
            'invocation_id': 'original-id', 'data': {'confirmed_items': ['preserved'],
                'visual_recovery': {'schema_version': 1, 'owner': 'cli', 'state': state}}}


@pytest.mark.parametrize('state', ['not_needed', 'not_configured', 'recovered',
                                 'diagnosis_unavailable', 'recovery_limit_reached'])
async def test_direct_proxy_returns_local_receipt_without_second_dispatch(monkeypatch, state):
    original = local_result(state)
    dispatch = AsyncMock(return_value=original)
    legacy = AsyncMock(side_effect=AssertionError('Local recovery must not invoke server healing'))
    monkeypatch.setattr(LocalToolProxyTool, '_find_ready_device', AsyncMock(return_value=({'id': 'device'}, None)))
    monkeypatch.setattr(LocalToolProxyTool, '_tool_credit_price', lambda _: 0)
    monkeypatch.setattr(LocalToolProxyTool, '_dispatch_and_wait', dispatch)
    monkeypatch.setattr(LocalToolProxyTool, '_heal_overlay', legacy)
    result = await BossFilterTool().execute(experience='5-10年', _trusted_tenant_id='tenant', _trusted_user_id='user')
    assert result is original
    assert result['effect'] == 'unknown' and result['data']['confirmed_items'] == ['preserved']
    dispatch.assert_awaited_once()
    legacy.assert_not_awaited()


@pytest.mark.parametrize('effect,known', [('none', 'applied'), ('unknown', 'applied')])
async def test_receipt_effect_conflict_keeps_original_without_legacy_replay(monkeypatch, effect, known):
    original = {'success': False, 'code': 'UI_CHANGED', 'effect': effect,
                'invocation_id': 'original-id', 'data': {'receipt_version': '1.1', 'known_effect': known}}
    dispatch = AsyncMock(return_value=original)
    legacy = AsyncMock(side_effect=AssertionError('Inconsistent evidence cannot authorize a replay'))
    monkeypatch.setattr(LocalToolProxyTool, '_find_ready_device', AsyncMock(return_value=({'id': 'device'}, None)))
    monkeypatch.setattr(LocalToolProxyTool, '_tool_credit_price', lambda _: 0)
    monkeypatch.setattr(LocalToolProxyTool, '_dispatch_and_wait', dispatch)
    monkeypatch.setattr(LocalToolProxyTool, '_heal_overlay', legacy)
    result = await BossFilterTool().execute(experience='5-10年', _trusted_tenant_id='tenant', _trusted_user_id='user')
    assert result is original
    dispatch.assert_awaited_once()
    legacy.assert_not_awaited()

    poll = AsyncMock(return_value=original)
    monkeypatch.setattr('src.local_tools.overlay_flow.poll_operation', poll)
    owner = SimpleNamespace(saved_phase=AsyncMock(side_effect=AssertionError('No healing phase may be created')))
    assert await run_base(BossFilterTool(), LocalOperationScope(owner, 'main', 0),
                          {'experience': '5-10年', '_trusted_tenant_id': 'tenant', '_trusted_user_id': 'user'}) is original
    poll.assert_awaited_once()
    owner.saved_phase.assert_not_awaited()


async def test_durable_flow_preserves_unknown_local_result_without_new_recovery_phase(monkeypatch):
    original = local_result('page_restored_business_unconfirmed')
    poll = AsyncMock(return_value=original)
    monkeypatch.setattr('src.local_tools.overlay_flow.poll_operation', poll)
    owner = SimpleNamespace(saved_phase=AsyncMock(side_effect=AssertionError('No healing phase may be created')))
    result = await run_base(BossFilterTool(), LocalOperationScope(owner, 'main', 0),
                            {'experience': '5-10年', '_trusted_tenant_id': 'tenant', '_trusted_user_id': 'user'})
    assert result is original
    poll.assert_awaited_once()
    assert _known(original, BossFilterTool()) is original


async def test_even_direct_legacy_recovery_entry_does_not_repair_local_receipt():
    original = local_result()
    dispatch = AsyncMock(side_effect=AssertionError('No second screenshot, model call, or business replay'))
    assert not is_visual_failure(BossFilterTool(), original)
    assert await recover_operation(BossFilterTool(), original, dispatch) is original
    dispatch.assert_not_awaited()


@pytest.mark.parametrize('report', [None, {}, {'owner': 'cli'}, {'schema_version': True, 'owner': 'cli'},
                                    {'schema_version': 2, 'owner': 'cli'}, {'schema_version': 1, 'owner': 'server'}])
def test_only_explicit_current_cli_ownership_suppresses_server_compatibility(report):
    original = deepcopy(local_result())
    original['data']['visual_recovery'] = report
    assert not cli_owns_recovery(original)


# ---- R8 接续语义（契约「停止与接续」）：停稳已证明的未知效果作为事实交回 ----


def settled_unknown(**data):
    """迟到可信回执经 observed_invocation 投影后的工具结果形态（效果未知、证据在 data）。"""
    return {'success': True, 'code': None, 'effect': 'unknown', 'invocation_id': 'inv-1',
            'data': {'filter': 1, **data}}


async def test_proven_steady_unknown_effect_is_returned_as_fact_without_new_wait():
    result = settled_unknown(_runtime_completion={'version': 1})
    assert _known(result, BossFilterTool()) is result, '停稳已证明：未知效果交回原任务，不再挂核验'


@pytest.mark.parametrize('data', [
    {'other': 1}, {'_runtime_completion': {'version': 2}}, {'_runtime_completion': '1'}, {},
])
async def test_unproven_unknown_effect_keeps_the_verification_hold(data):
    import pytest as _pytest
    from src.local_tools.durable_flow import LocalContinuationRequired
    result = settled_unknown(**data)
    with _pytest.raises(LocalContinuationRequired):
        _known(result, BossFilterTool())


async def test_visual_failure_branch_still_wins_over_evidence_fact(monkeypatch):
    """带视觉恢复策略的工具保持既有观察修复链（R8 不改其语义）。"""
    from src.local_tools.boss_visual_recovery import recovery_context
    from unittest.mock import patch
    result = settled_unknown(_runtime_completion={'version': 1})
    with patch('src.local_tools.boss_visual_recovery.recovery_context',
               return_value=({'error': {'code': 'EXECUTION_UNKNOWN'}}, 'preserve_result', None)):
        assert is_visual_failure(BossFilterTool(), result) is True
        assert _known(result, BossFilterTool()) is result


async def test_r8_unknown_greet_with_proven_completion_never_dispatches_a_new_greet(monkeypatch):
    """未知招呼不自动重发：停稳已证明也只把未知结果交回，轮询原调用、零新派发。"""
    from src.local_tools.lifecycle import LocalOperationScope
    from src.local_tools.overlay_flow import poll_operation
    from src.local_tools.proxy_tool import BossGreetTool
    arguments = {'names': ['张三'], 'matched_names': None, 'limit': 1}
    phase = {'request': {'tool_name': 'boss_greet', 'arguments': arguments, 'device_id': 'device-1'},
             'invocation': {'id': 'greet-1', 'deadline_at': None}}
    owner = SimpleNamespace(saved_phase=AsyncMock(return_value=phase))
    poll = AsyncMock(return_value=settled_unknown(_runtime_completion={'version': 1}))
    monkeypatch.setattr(BossGreetTool, '_poll_invocation', poll)
    dispatch = AsyncMock(side_effect=AssertionError('招呼效果未知不重放：不允许新 greet 派发'))
    monkeypatch.setattr(BossGreetTool, '_dispatch_and_wait', dispatch)
    find_device = AsyncMock(side_effect=AssertionError('停稳接续不重新选设备'))
    monkeypatch.setattr(BossGreetTool, '_find_ready_device', find_device)
    result = await poll_operation(BossGreetTool(), LocalOperationScope(owner, 'main', 0), arguments,
        tenant_id='tenant', user_id='user', session_id='session', progress_queue=None)
    assert result['effect'] == 'unknown' and result['invocation_id'] == 'inv-1'
    poll.assert_awaited_once()
    dispatch.assert_not_awaited()
    find_device.assert_not_awaited()
