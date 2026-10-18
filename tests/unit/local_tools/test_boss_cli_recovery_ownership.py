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
