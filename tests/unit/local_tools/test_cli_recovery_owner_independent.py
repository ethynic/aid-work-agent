"""Independent boundary tests: a current CLI result must never start server repair."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.local_tools.boss_visual_recovery import cli_owns_recovery, is_visual_failure, recovery_context
from src.local_tools.durable_flow import LocalContinuationRequired
from src.local_tools.lifecycle import LocalOperationScope
from src.local_tools.overlay_flow import _known, run_base
from src.local_tools.proxy_tool import BossOpenDetailTool, LocalToolProxyTool

pytestmark = pytest.mark.unit


def local_result(state='diagnosis_unavailable', *, unknown=False):
    return {'success': False, 'code': 'EXECUTION_UNKNOWN' if unknown else 'UI_CHANGED',
        'effect': 'unknown' if unknown else 'none', 'invocation_id': 'original-local-call',
        'data': {'completed': ['original-result'], 'visual_recovery': {
            'schema_version': 1, 'owner': 'cli', 'state': state,
            'receipts': [{'request_id': 'already-paid', 'factor': 20}]}}}


@pytest.mark.parametrize('state', ['not_needed', 'unconfigured', 'diagnosis_unavailable',
    'postcheck_inconclusive', 'recovery_limit_reached', 'recovered'])
@pytest.mark.parametrize('unknown', [False, True])
async def test_direct_proxy_returns_local_outcome_without_second_repair(monkeypatch, state, unknown):
    source = local_result(state, unknown=unknown)
    before = deepcopy(source)
    dispatch = AsyncMock(return_value=source)
    forbidden = AsyncMock(side_effect=AssertionError('server must not diagnose, repair, or replay a local-owned call'))
    monkeypatch.setattr(LocalToolProxyTool, '_find_ready_device', AsyncMock(return_value=({'id': 'original-device'}, None)))
    monkeypatch.setattr(LocalToolProxyTool, '_tool_credit_price', lambda _: 0)
    monkeypatch.setattr(LocalToolProxyTool, '_dispatch_and_wait', dispatch)
    monkeypatch.setattr(LocalToolProxyTool, '_heal_overlay', forbidden)
    monkeypatch.setattr('src.local_tools.boss_visual_recovery.recover_operation', forbidden)
    result = await BossOpenDetailTool().execute(name='原候选人', _trusted_tenant_id='tenant', _trusted_user_id='user')
    assert result is source and result == before, 'Local failures and paid receipts must remain intact, not rewritten into server outcomes'
    assert dispatch.await_count == 1, 'Only the original CLI invocation is dispatched'
    forbidden.assert_not_awaited()


@pytest.mark.parametrize('saved', [False, True])
@pytest.mark.parametrize('unknown', [False, True])
async def test_durable_new_and_saved_invocations_stop_at_local_owner(monkeypatch, saved, unknown):
    source = local_result(unknown=unknown)
    args = {'name': '原候选人'}
    phase = {'request': {'tool_name': 'boss_open_detail', 'arguments': args, 'device_id': 'original-device'},
        'invocation': {'id': source['invocation_id'], 'deadline_at': datetime.now(timezone.utc)}}
    owner = SimpleNamespace(saved_phase=AsyncMock(return_value=phase if saved else None))
    dispatch = AsyncMock(return_value=source)
    poll = AsyncMock(return_value=source)
    forbidden = AsyncMock(side_effect=AssertionError('durable continuation may not add server recovery phases'))
    monkeypatch.setattr(LocalToolProxyTool, '_find_ready_device', AsyncMock(return_value=({'id': 'original-device'}, None)))
    monkeypatch.setattr(LocalToolProxyTool, '_tool_credit_price', lambda _: 0)
    monkeypatch.setattr(LocalToolProxyTool, '_dispatch_and_wait', dispatch)
    monkeypatch.setattr(LocalToolProxyTool, '_poll_invocation', poll)
    monkeypatch.setattr('src.local_tools.boss_visual_recovery.recover_operation', forbidden)
    result = await run_base(BossOpenDetailTool(), LocalOperationScope(owner, 'main', 0),
        {**args, '_trusted_tenant_id': 'tenant', '_trusted_user_id': 'user'})
    assert result is source
    assert owner.saved_phase.await_count == 1, 'No policy/diagnostic/fee continuation should be appended'
    assert dispatch.await_count == (0 if saved else 1)
    assert poll.await_count == (1 if saved else 0)
    forbidden.assert_not_awaited()


def test_local_unknown_is_not_lost_to_server_continuation_exception():
    source = local_result(unknown=True)
    assert _known(source, BossOpenDetailTool()) is source
    assert not is_visual_failure(BossOpenDetailTool(), source)
    assert source['effect'] == 'unknown' and source['data']['completed'] == ['original-result']


@pytest.mark.parametrize('marker', [None, {}, {'schema_version': True, 'owner': 'cli'},
    {'schema_version': '1', 'owner': 'cli'}, {'schema_version': 2, 'owner': 'cli'},
    {'schema_version': 1, 'owner': 'server'}, {'owner': 'cli'}])
def test_only_explicit_versioned_local_ownership_bypasses_legacy_unknown_guard(marker):
    source = local_result(unknown=True)
    source['data']['visual_recovery'] = marker
    assert not cli_owns_recovery(source)
    with pytest.raises(LocalContinuationRequired):
        _known(source, BossOpenDetailTool())


def test_missing_local_marker_keeps_existing_client_compatibility():
    source = local_result(unknown=True)
    del source['data']['visual_recovery']
    assert not cli_owns_recovery(source)
    with pytest.raises(LocalContinuationRequired):
        _known(source, BossOpenDetailTool())


@pytest.mark.parametrize('policy,operation_class,result_kind', [
    ('page_state', 'page_state', 'page_state'),
    ('readonly', 'readonly', 'page_state'),
    ('observe_only', 'external_effect', 'business_result'),
])
def test_public_v2_facts_cannot_be_re_admitted_by_legacy_server_policy(policy, operation_class, result_kind):
    """Old admission reads schema v1; local v2 projection must stop both modern/legacy fallbacks."""
    source = local_result(unknown=True)
    del source['data']['visual_recovery']  # Simulate a consumer that does not know this new owner marker.
    facts = {'schema_version': 1, 'operation_class': operation_class,
        'operation': {'name': 'boss_open_detail', 'goal': '原目标', 'expected': '领域后态', 'result_kind': result_kind},
        'execution': {'stage': 'input', 'input_status': 'unknown', 'attempts_used': 1, 'ordinary_retries_used': 0},
        'verification': {'status': 'inconclusive', 'evidence': ['尚未确认']}}
    proxy = SimpleNamespace(name='boss_open_detail', heal_eligible=True, visual_recovery_policy=policy)
    source['data']['operation_recovery'] = facts
    assert recovery_context(proxy, source) is not None, 'The fixture must be otherwise eligible for old server repair'
    source['data']['operation_recovery'] = {**facts, 'schema_version': 2, 'owner': 'cli'}
    source['data']['page_operation'] = {**facts, 'schema_version': 2, 'owner': 'cli'}
    assert recovery_context(proxy, source) is None
    assert not is_visual_failure(proxy, source)
