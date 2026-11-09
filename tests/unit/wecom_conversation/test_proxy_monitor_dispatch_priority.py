"""派发抢锁失败后，监控只给同设备的待派发批次让一次执行机会。"""

import asyncio
from types import SimpleNamespace

import pytest

from src.wecom_conversation import proxy_dispatcher, proxy_monitor


def _group(device_id, *sessions):
    return [{'tenant_id': 'tenant', 'device_id': device_id,
             'wecom_account_id': 'account', 'session': session}
            for session in sessions]


@pytest.mark.asyncio
async def test_lock_collision_yields_only_its_own_device(monkeypatch):
    """真实抢锁失败后 A 让位，B 设备仍可按自己的组锁读取。"""
    key = ('tenant', 'device-a', 'account')
    lock = proxy_monitor.group_lock(key)
    read_devices = []

    async def task_device(*_args):
        return object(), None

    async def agent_allowed(_batch):
        return True

    async def read_group(items):
        read_devices.append(items[0]['device_id'])

    monkeypatch.setattr(proxy_dispatcher, '_proxy_api',
                        lambda: SimpleNamespace(_task_device=task_device))
    monkeypatch.setattr(proxy_dispatcher, '_agent_allowed', agent_allowed)
    monkeypatch.setattr(proxy_monitor, '_group_has_pending',
                        lambda _tenant, device, _account: device == 'device-a')
    monkeypatch.setattr(proxy_monitor, '_run_group_locked', read_group)
    batch = {'tenant_id': 'tenant', 'device_id': 'device-a',
             'wecom_account_id': 'account', 'user_id': 'user', 'id': 1}

    try:
        lock.acquire()
        assert await proxy_dispatcher._dispatch_batch(batch, {}) is False
        assert proxy_monitor.dispatch_turn_requested(key)
    finally:
        lock.release()
    try:
        await asyncio.gather(proxy_monitor._run_group(_group('device-a', 'session-a')),
                             proxy_monitor._run_group(_group('device-b', 'session-b')))
        assert read_devices == ['device-b']
        monkeypatch.setattr(proxy_monitor, '_group_has_pending', lambda *_: False)
        await proxy_monitor._run_group(_group('device-a', 'session-a'))
        assert read_devices == ['device-b', 'device-a']
        assert not proxy_monitor.dispatch_turn_requested(key)
    finally:
        proxy_monitor.clear_dispatch_turn(key)


@pytest.mark.asyncio
async def test_other_session_reads_until_dispatcher_actually_collides(monkeypatch):
    """等待 Runner 释放后 B 可先读；A 派发真的撞锁才让下一轮。"""
    key = ('tenant', 'device-c', 'account')
    reads = []

    async def read_group(items):
        reads.append([item['session'] for item in items])

    monkeypatch.setattr(proxy_monitor, '_group_has_pending', lambda *_: True)
    monkeypatch.setattr(proxy_monitor, '_run_group_locked', read_group)
    try:
        await proxy_monitor._run_group(_group('device-c', 'session-a', 'session-b'))
        assert reads == [['session-a', 'session-b']]
        proxy_monitor.request_dispatch_turn(key)
        await proxy_monitor._run_group(_group('device-c', 'session-a', 'session-b'))
        assert reads == [['session-a', 'session-b']]
    finally:
        proxy_monitor.clear_dispatch_turn(key)


@pytest.mark.asyncio
async def test_dispatcher_clears_turn_after_acquiring_lock(monkeypatch):
    """派发已拿到本设备锁后清掉让位标记，后续监控不被旧标记压住。"""
    key = ('tenant', 'device-d', 'account')

    async def task_device(*_args):
        return object(), None

    async def agent_allowed(_batch):
        return True

    monkeypatch.setattr(proxy_dispatcher, '_proxy_api',
                        lambda: SimpleNamespace(_task_device=task_device))
    monkeypatch.setattr(proxy_dispatcher, '_agent_allowed', agent_allowed)
    monkeypatch.setattr(proxy_dispatcher, '_group_has_running', lambda *_: True)
    batch = {'tenant_id': 'tenant', 'device_id': 'device-d',
             'wecom_account_id': 'account', 'user_id': 'user', 'id': 2}
    proxy_monitor.request_dispatch_turn(key)
    try:
        assert await proxy_dispatcher._dispatch_batch(batch, {}) is False
        assert not proxy_monitor.dispatch_turn_requested(key)
    finally:
        proxy_monitor.clear_dispatch_turn(key)


@pytest.mark.asyncio
async def test_device_unavailable_clears_old_dispatch_turn(monkeypatch):
    """设备派发条件失效后，监控不应被上次抢锁标记一直压住。"""
    key = ('tenant', 'device-e', 'account')

    async def task_device(*_args):
        return None, {'code': 'DEVICE_OFFLINE', 'message': 'offline'}

    monkeypatch.setattr(proxy_dispatcher, '_proxy_api',
                        lambda: SimpleNamespace(_task_device=task_device))
    batch = {'tenant_id': 'tenant', 'device_id': 'device-e',
             'wecom_account_id': 'account', 'user_id': 'user', 'id': 3}
    proxy_monitor.request_dispatch_turn(key)
    try:
        assert await proxy_dispatcher._dispatch_batch(batch, {}) is False
        assert not proxy_monitor.dispatch_turn_requested(key)
    finally:
        proxy_monitor.clear_dispatch_turn(key)
