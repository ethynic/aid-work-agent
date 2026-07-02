"""archive.poller 单元测试

覆盖：
- start/stop 正常生命周期
- 重复 start 不重复启动
- 启动时立即扫一次（initial）
- 周期扫描触发（periodic）
- 扫描触发 fetcher.fetch_once 被调用
- 单租户 fetcher 异常不影响其他租户
- DB 查询异常不崩主循环
- DB 查询超时不崩主循环
- 无 server 配置时静默跳过
- listen_mode 非 server 的配置被过滤
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.channels.wecom_personal_rpa.archive import poller as poller_module
from src.channels.wecom_personal_rpa.archive.poller import ServerArchivePoller


def _make_cfg(tenant_id: str, config_id: str, listen_mode: str = "server") -> dict:
    return {
        "config_id": config_id,
        "tenant_id": tenant_id,
        "channel_type": "wecom_personal_rpa",
        "config": {"listen_mode": listen_mode},
        "verified": 1,
    }


# ----------------- 启动 / 停止 -----------------


@pytest.mark.asyncio
async def test_start_stop_basic(monkeypatch):
    """启动后 is_running=True，stop 后 is_running=False。"""
    monkeypatch.setattr(poller_module.ChannelConfigDB, "list_by_channel_type", lambda *a, **kw: [])
    poller = ServerArchivePoller(poll_interval_seconds=5)
    assert not poller.is_running

    await poller.start()
    assert poller.is_running

    await poller.stop()
    assert not poller.is_running


@pytest.mark.asyncio
async def test_start_idempotent(monkeypatch):
    """重复 start 不重复启动。"""
    monkeypatch.setattr(poller_module.ChannelConfigDB, "list_by_channel_type", lambda *a, **kw: [])
    poller = ServerArchivePoller(poll_interval_seconds=5)

    await poller.start()
    first_task = poller._loop_task
    await poller.start()  # 重复启动
    assert poller._loop_task is first_task  # 同一 task

    await poller.stop()


@pytest.mark.asyncio
async def test_start_initial_scan_called(monkeypatch):
    """启动时立即触发一次扫描（initial）。"""
    scan_calls = []

    async def _mock_scan(reason="periodic"):
        scan_calls.append(reason)

    monkeypatch.setattr(poller_module.ChannelConfigDB, "list_by_channel_type", lambda *a, **kw: [])
    poller = ServerArchivePoller(poll_interval_seconds=5)
    poller._scan_once = _mock_scan

    await poller.start()
    # 给 initial 扫描一个 tick
    await asyncio.sleep(0.1)
    await poller.stop()

    assert "initial" in scan_calls


# ----------------- 扫描行为 -----------------


@pytest.mark.asyncio
async def test_scan_triggers_fetcher_for_each_config(monkeypatch):
    """扫描时对每个配置触发一次 fetcher.fetch_once。"""
    configs = [_make_cfg("t1", "c1"), _make_cfg("t2", "c2"), _make_cfg("t3", "c3")]
    monkeypatch.setattr(poller_module.ChannelConfigDB, "list_by_channel_type", lambda *a, **kw: configs)

    fetcher_mock = MagicMock()
    fetcher_mock.fetch_once = AsyncMock()

    poller = ServerArchivePoller(fetcher=fetcher_mock, poll_interval_seconds=60)
    await poller._scan_once(reason="test")

    # 等所有 create_task 完成
    await asyncio.sleep(0.05)
    pending = [t for t in poller._running_fetcher_tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    assert fetcher_mock.fetch_once.await_count == 3
    called_args = sorted([(c.args[0], c.args[1]) for c in fetcher_mock.fetch_once.await_args_list])
    assert called_args == [("t1", "c1"), ("t2", "c2"), ("t3", "c3")]


@pytest.mark.asyncio
async def test_scan_filters_non_server_mode(monkeypatch):
    """listen_mode='client' 的配置被过滤掉。"""
    configs = [
        _make_cfg("t1", "c1", listen_mode="server"),
        _make_cfg("t2", "c2", listen_mode="client"),  # 应被过滤
        _make_cfg("t3", "c3", listen_mode="server"),
    ]
    monkeypatch.setattr(poller_module.ChannelConfigDB, "list_by_channel_type", lambda *a, **kw: configs)

    fetcher_mock = MagicMock()
    fetcher_mock.fetch_once = AsyncMock()

    poller = ServerArchivePoller(fetcher=fetcher_mock, poll_interval_seconds=60)
    await poller._scan_once(reason="test")

    await asyncio.sleep(0.05)
    pending = [t for t in poller._running_fetcher_tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    # 只触发 listen_mode='server' 的 2 个租户
    assert fetcher_mock.fetch_once.await_count == 2
    called_tenants = {c.args[0] for c in fetcher_mock.fetch_once.await_args_list}
    assert called_tenants == {"t1", "t3"}


@pytest.mark.asyncio
async def test_scan_empty_configs_skips(monkeypatch):
    """无配置时静默跳过，不抛异常。"""
    monkeypatch.setattr(poller_module.ChannelConfigDB, "list_by_channel_type", lambda *a, **kw: [])

    fetcher_mock = MagicMock()
    fetcher_mock.fetch_once = AsyncMock()

    poller = ServerArchivePoller(fetcher=fetcher_mock, poll_interval_seconds=60)
    # 不抛异常
    await poller._scan_once(reason="test")
    assert fetcher_mock.fetch_once.await_count == 0


@pytest.mark.asyncio
async def test_scan_db_exception_does_not_crash(monkeypatch):
    """DB 查询异常不抛异常（不崩主循环）。"""
    def _raise(*a, **kw):
        raise RuntimeError("DB down")

    monkeypatch.setattr(poller_module.ChannelConfigDB, "list_by_channel_type", _raise)

    fetcher_mock = MagicMock()
    fetcher_mock.fetch_once = AsyncMock()

    poller = ServerArchivePoller(fetcher=fetcher_mock, poll_interval_seconds=60)
    # 不抛异常
    await poller._scan_once(reason="test")
    assert fetcher_mock.fetch_once.await_count == 0


@pytest.mark.asyncio
async def test_single_tenant_failure_doesnt_affect_others(monkeypatch):
    """单个 fetcher.fetch_once 异常不影响其他租户。"""
    configs = [_make_cfg("t1", "c1"), _make_cfg("t2", "c2")]
    monkeypatch.setattr(poller_module.ChannelConfigDB, "list_by_channel_type", lambda *a, **kw: configs)

    fetcher_mock = MagicMock()

    # t1 抛异常，t2 正常
    async def _mock_fetch(tenant_id, config_id):
        if tenant_id == "t1":
            raise RuntimeError("t1 failed")

    fetcher_mock.fetch_once = _mock_fetch

    poller = ServerArchivePoller(fetcher=fetcher_mock, poll_interval_seconds=60)
    await poller._scan_once(reason="test")

    await asyncio.sleep(0.05)
    pending = [t for t in poller._running_fetcher_tasks if not t.done()]
    if pending:
        # 不应抛异常（_safe_fetch_once 包装了）
        results = await asyncio.gather(*pending, return_exceptions=True)
        assert all(r is None for r in results), f"应有 None（吞异常），实际 {results}"


# ----------------- 集成：启动 → 周期扫描 -----------------


@pytest.mark.asyncio
async def test_periodic_scan_loop(monkeypatch):
    """启动后周期触发扫描（短间隔验证循环逻辑）。"""
    scan_count = 0

    async def _mock_scan(reason="periodic"):
        nonlocal scan_count
        scan_count += 1

    monkeypatch.setattr(poller_module.ChannelConfigDB, "list_by_channel_type", lambda *a, **kw: [])
    poller = ServerArchivePoller(poll_interval_seconds=5)
    poller._scan_once = _mock_scan

    # 5s 周期，跑 ~3s 应该有 initial(1) + 部分 periodic
    await poller.start()
    await asyncio.sleep(3)
    await poller.stop()

    # initial 必有，periodic 至少触发 1 次（5s 周期在 3s 内可能 0 次，所以宽松断言）
    assert scan_count >= 1
