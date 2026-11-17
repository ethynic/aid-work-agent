"""R10 修复1（契约「停止与接续」）：停稳已核的 cancel_requested 原调用立即交回未知事实。

真实 PostgreSQL：原调用丢失业务回执 → 用户停止并继续 → Runtime 回传有效停稳
证明（v3 → checkpoint verified_local_invocations 合并，Worker 经 owner envelope
随领取载入）→ Worker 续跑轮询该行。修复前 durable 轮询分支对 cancel_requested
行继续等旧调用终态，直到租约过期才交回（生产领取租约 60s）；修复后确认停稳
已核即优先消费已有回执，无回执立即把「业务效果未知」事实交回原任务（不等
租约/deadline），下一条 BOSS 读取秒级可派发，不重放该调用、不改写原行；无停
稳证明保持原等租约收敛（回归锚）。
"""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from src.local_tools import repository as devices
from src.local_tools.lifecycle import local_operation_scope
from src.local_tools.overlay_flow import poll_operation
from src.local_tools.proxy_tool import BossFilterTool, BossGotoTool

pytestmark = pytest.mark.integration

LEASE_SECONDS = 20
# 修复前：cancel_requested 行要等 lease_expires_at 过期 + 清扫收敛才交回（≥ 租约剩余）；
# 修复后：停稳已核立即交回（秒级）。断言阈值取中，红绿分明。
IMMEDIATE_LIMIT_SECONDS = 8


@pytest.fixture
def stopped_original(actors, service_database):
    """真实设备 + 已领取执行的 boss_filter 原调用，协作取消后停在 cancel_requested（无回执、租约未过期）。"""
    actor = actors['a']
    device = devices.create_device(actor.tenant_id, actor.user_id, 'hash-' + uuid.uuid4().hex,
        name='fixture-runtime', platform='windows',
        machine_fingerprint_hash='fixture-machine-' + uuid.uuid4().hex)
    invocation = devices.create_invocation(actor.tenant_id, actor.user_id, str(device['id']),
        'boss_filter', {'keyword': 'python', 'filters': []}, actor.session_id)
    claim = uuid.uuid4().hex
    assert devices.claim_next(str(device['id']), actor.tenant_id, claim, LEASE_SECONDS,
        expected_invocation_id=invocation)
    assert devices.mark_started(invocation, actor.tenant_id, claim)['state'] == 'running'
    # 协作取消（受管停止目标）：行置 cancel_requested，回执未送达，租约保持未过期
    service_database.rows("UPDATE local_tool_invocations SET state='cancel_requested' WHERE id=%s",
        (invocation,))
    yield actor, device, invocation, claim
    for table in ('local_tool_events', 'local_tool_invocations', 'local_tool_devices'):
        service_database.rows(f'DELETE FROM {table} WHERE tenant_id=%s', (actor.tenant_id,))


def _covered_owner(actor, device, invocation, *, covered):
    """模拟续跑 Worker：saved_phase 绑定原调用；envelope 承载 checkpoint 停稳事实。"""
    row = devices.get_invocation(invocation, actor.tenant_id)
    return SimpleNamespace(
        root=SimpleNamespace(envelope={'verified_local_invocations': [invocation] if covered else []}),
        saved_phase=AsyncMock(return_value={
            'request': {'tool_name': 'boss_filter', 'arguments': {'keyword': 'python', 'filters': []},
                        'device_id': str(device['id']), 'deadline_at': None},
            'invocation': row,
        }))


async def _poll_original(actor, device, invocation, *, covered):
    owner = _covered_owner(actor, device, invocation, covered=covered)
    operation = SimpleNamespace(owner=owner, branch='main', ordinal=0)
    started = time.monotonic()
    with local_operation_scope(owner):
        result = await poll_operation(BossFilterTool(), operation,
            {'keyword': 'python', 'filters': []}, tenant_id=actor.tenant_id,
            user_id=actor.user_id, session_id=actor.session_id, progress_queue=None)
    return result, time.monotonic() - started


async def test_stop_verified_cancel_requested_hands_unknown_back_immediately(
        stopped_original, service_database):
    actor, device, invocation, _ = stopped_original
    # 停稳事实已合并进 Worker checkpoint（v3 停稳回执事务内合并，随领取载入 envelope）
    result, elapsed = await _poll_original(actor, device, invocation, covered=True)
    assert elapsed < IMMEDIATE_LIMIT_SECONDS, (
        f'停稳已核的 cancel_requested 行必须立即交回，实测等待 {elapsed:.1f}s（修复前等满租约）')
    assert result['success'] is False and result['code'] == 'EXECUTION_UNKNOWN'
    assert result['effect'] == 'unknown' and result['invocation_id'] == invocation
    assert '禁止重试' in result['message'], '效果未知事实必须带禁止重试语义交回'
    # 不重放、不改写：原行保持 cancel_requested 事实（审计不变），无第二次派发
    row = devices.get_invocation(invocation, actor.tenant_id)
    assert row['state'] == 'cancel_requested' and row['result_json'] is None
    assert service_database.rows(
        "SELECT COUNT(*) AS n FROM local_tool_invocations WHERE tenant_id=%s AND tool_name='boss_filter'",
        (actor.tenant_id,))[0]['n'] == 1
    # 下一条 BOSS 读取秒级启动：同一设备立即派发新读取，不被原调用空等阻塞
    next_read = devices.create_invocation(actor.tenant_id, actor.user_id, str(device['id']),
        'boss_goto', {'target': 'recommend'}, actor.session_id)
    assert next_read


async def test_stop_verified_consumes_backfilled_receipt(stopped_original):
    """停稳已核 + 回执已补传：按既有终态消费交回（不新增逻辑）。"""
    actor, device, invocation, claim = stopped_original
    assert devices.write_result(invocation, actor.tenant_id, claim, True, effect='applied',
        data={'filter_count': 3}, device_id=str(device['id']))['state'] == 'succeeded'
    result, elapsed = await _poll_original(actor, device, invocation, covered=True)
    assert elapsed < IMMEDIATE_LIMIT_SECONDS
    assert result['success'] is True and result['effect'] == 'applied'
    assert (result.get('data') or {}).get('filter_count') == 3


async def test_without_stop_proof_keeps_waiting_for_lease(stopped_original, service_database):
    """无停稳证明（回归锚）：保持现状等租约停续 → 清扫收敛 unknown → 按既有语义
    卡 LOCAL_EFFECT_VERIFICATION_REQUIRED（不交回未知事实，R9 兼容锚）。"""
    actor, device, invocation, _ = stopped_original
    service_database.rows(
        "UPDATE local_tool_invocations SET lease_expires_at=NOW()+INTERVAL '4 seconds' WHERE id=%s",
        (invocation,))
    from src.local_tools.durable_flow import LocalContinuationRequired
    started = time.monotonic()
    owner = _covered_owner(actor, device, invocation, covered=False)
    operation = SimpleNamespace(owner=owner, branch='main', ordinal=0)
    with local_operation_scope(owner), pytest.raises(LocalContinuationRequired) as raised:
        await poll_operation(BossFilterTool(), operation, {'keyword': 'python', 'filters': []},
            tenant_id=actor.tenant_id, user_id=actor.user_id, session_id=actor.session_id,
            progress_queue=None)
    elapsed = time.monotonic() - started
    assert raised.value.code == 'LOCAL_EFFECT_VERIFICATION_REQUIRED'
    assert elapsed >= 3, f'无停稳证明必须保持等租约收敛（回归锚），实测 {elapsed:.1f}s'
    row = devices.get_invocation(invocation, actor.tenant_id)
    assert row['state'] == 'unknown' and row['effect'] == 'unknown'
