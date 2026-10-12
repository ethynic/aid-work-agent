"""微信客服拉取任务去重与限频重试单元测试（2026-10-11 45009 事故修复）

覆盖：
1. _try_start_kf_pull_task：
   - 锁空闲 -> 启动拉取任务并清理 pending
   - 锁被占 -> 不起新任务，仅置 pending 标记
2. _kf_pull_task_wrapper：
   - 任务结束后释放锁
   - pending 存在 -> 触发补偿拉取
   - pending 不存在 -> 不补偿
3. _compensate_kf_pull：按 open_kfid 反查 config_id 并重建 adapter 重启任务
4. _process_tenant_wecom_kf_messages 拉取循环：
   - 45009 限频 -> 指数退避重试后成功
   - 限频超过最大重试次数 -> 放弃且不更新 cursor
   - 非限频错误 -> 立即失败不重试
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.saas.api.channel_routes as cr_module


def _fake_redis(acquired=True, pending=None):
    """构造 redis_client 替身，pending 为 getdel 的返回队列"""
    redis = MagicMock()
    redis.acquire_lock.return_value = acquired
    redis.release_lock.return_value = True
    redis.set.return_value = None
    redis.delete.return_value = 1
    if pending is None:
        redis.getdel.return_value = None
    else:
        redis.getdel.side_effect = pending
    redis.make_key.side_effect = lambda prefix, rest: f"{prefix}:{rest}"
    return redis


def _kf_adapter():
    adapter = MagicMock()
    kf_config = {"subagent_type": "sales-assistant", "open_kfid": "kf1"}
    adapter.get_kf_config.return_value = kf_config
    adapter.cursor_manager.get_cursor.return_value = "cur0"
    return adapter


@pytest.fixture
def no_sleep():
    with patch("src.saas.api.channel_routes.asyncio.sleep", new=AsyncMock()) as m:
        yield m


@pytest.mark.asyncio
async def test_start_task_lock_acquired(monkeypatch):
    """锁空闲：启动拉取任务、清理 pending、不置 pending"""
    redis = _fake_redis(acquired=True)
    monkeypatch.setattr(cr_module, "redis_client", redis)
    started = []

    async def fake_wrapper(t, c, o, a, tok):
        started.append((t, o, tok))

    monkeypatch.setattr(cr_module, "_kf_pull_task_wrapper", fake_wrapper)
    adapter = _kf_adapter()

    cr_module._try_start_kf_pull_task("t1", "cfg1", "kf1", adapter)
    await asyncio.sleep(0)

    assert len(started) == 1
    assert started[0][0] == "t1" and started[0][1] == "kf1"
    redis.acquire_lock.assert_called_once()
    redis.delete.assert_called_once()  # 清理残留 pending
    redis.set.assert_not_called()  # 未置 pending


@pytest.mark.asyncio
async def test_start_task_lock_busy_sets_pending(monkeypatch):
    """锁被占：不起新任务，仅置 pending 标记"""
    redis = _fake_redis(acquired=False)
    monkeypatch.setattr(cr_module, "redis_client", redis)
    started = []

    async def fake_wrapper(t, c, o, a, tok):
        started.append((t, o, tok))

    monkeypatch.setattr(cr_module, "_kf_pull_task_wrapper", fake_wrapper)

    cr_module._try_start_kf_pull_task("t1", "cfg1", "kf1", MagicMock())
    await asyncio.sleep(0)

    assert started == []
    redis.set.assert_called_once()  # 置 pending
    redis.delete.assert_not_called()


@pytest.mark.asyncio
async def test_wrapper_releases_lock_and_compensates(monkeypatch):
    """任务结束：释放锁；pending 存在时触发补偿拉取"""
    redis = _fake_redis(pending=[True, None])
    monkeypatch.setattr(cr_module, "redis_client", redis)
    proc = AsyncMock()
    monkeypatch.setattr(cr_module, "_process_tenant_wecom_kf_messages", proc)
    compensate = AsyncMock()
    monkeypatch.setattr(cr_module, "_compensate_kf_pull", compensate)

    await cr_module._kf_pull_task_wrapper("t1", "cfg1", "kf1", MagicMock(), "tok1")

    proc.assert_awaited_once()
    redis.release_lock.assert_called_once()
    assert redis.release_lock.call_args[0][1] == "tok1"
    compensate.assert_awaited_once_with("t1", "kf1")


@pytest.mark.asyncio
async def test_wrapper_no_pending_no_compensate(monkeypatch):
    """任务结束：pending 不存在时不补偿"""
    redis = _fake_redis()
    monkeypatch.setattr(cr_module, "redis_client", redis)
    monkeypatch.setattr(cr_module, "_process_tenant_wecom_kf_messages", AsyncMock())
    compensate = AsyncMock()
    monkeypatch.setattr(cr_module, "_compensate_kf_pull", compensate)

    await cr_module._kf_pull_task_wrapper("t1", "cfg1", "kf1", MagicMock(), "tok1")

    compensate.assert_not_awaited()
    redis.release_lock.assert_called_once()


@pytest.mark.asyncio
async def test_compensate_resolves_config_and_restarts(monkeypatch):
    """补偿拉取：反查 config_id -> 重建 adapter -> 重新走锁竞争"""
    redis = _fake_redis()
    monkeypatch.setattr(cr_module, "redis_client", redis)
    monkeypatch.setattr(
        cr_module.ChannelConfigDB,
        "list_by_tenant",
        MagicMock(return_value=[{"config_id": "cfg9", "config": {"kf_account": [{"open_kfid": "kf1"}]}}]),
    )
    adapter = _kf_adapter()
    monkeypatch.setattr(
        cr_module.ChannelFactory,
        "create_from_tenant_config",
        AsyncMock(return_value=(adapter, "cfg9", None)),
    )
    start = MagicMock()
    monkeypatch.setattr(cr_module, "_try_start_kf_pull_task", start)

    await cr_module._compensate_kf_pull("t1", "kf1")

    start.assert_called_once_with("t1", "cfg9", "kf1", adapter)


@pytest.mark.asyncio
async def test_compensate_config_not_found(monkeypatch):
    """补偿拉取：反查不到配置时不重启任务"""
    redis = _fake_redis()
    monkeypatch.setattr(cr_module, "redis_client", redis)
    monkeypatch.setattr(
        cr_module.ChannelConfigDB,
        "list_by_tenant",
        MagicMock(return_value=[]),
    )
    factory = AsyncMock()
    monkeypatch.setattr(cr_module, "ChannelFactory", factory)
    start = MagicMock()
    monkeypatch.setattr(cr_module, "_try_start_kf_pull_task", start)

    await cr_module._compensate_kf_pull("t1", "kf1")

    factory.create_from_tenant_config.assert_not_awaited()
    start.assert_not_called()


@pytest.mark.asyncio
async def test_sync_retry_on_rate_limit_then_success(no_sleep, monkeypatch):
    """45009 限频：退避重试 2 次后成功拉取"""
    redis = _fake_redis()
    monkeypatch.setattr(cr_module, "redis_client", redis)
    adapter = _kf_adapter()
    ok = {"errcode": 0, "errmsg": "ok", "msg_list": [], "has_more": 0, "next_cursor": "cur9"}
    limited = {"errcode": 45009, "errmsg": "Too many requests."}
    adapter.api_client.sync_msg = AsyncMock(side_effect=[limited, limited, ok])

    await cr_module._process_tenant_wecom_kf_messages("t1", "cfg1", "kf1", adapter)

    assert adapter.api_client.sync_msg.await_count == 3
    # 退避 2s / 4s
    delays = [c.args[0] for c in no_sleep.call_args_list]
    assert delays == [2, 4]
    # 限频重试期间 cursor 必须保持初始值不变（限频响应不含 next_cursor，不能被污染为空）
    cursors = [c.kwargs["cursor"] for c in adapter.api_client.sync_msg.await_args_list]
    assert cursors == ["cur0", "cur0", "cur0"]
    adapter.cursor_manager.set_cursor.assert_called_once_with("kf1", "cur9")


@pytest.mark.asyncio
async def test_sync_retry_on_45011_then_success(no_sleep, monkeypatch):
    """45011（同类频率限制）：同样走指数退避重试"""
    redis = _fake_redis()
    monkeypatch.setattr(cr_module, "redis_client", redis)
    adapter = _kf_adapter()
    ok = {"errcode": 0, "errmsg": "ok", "msg_list": [], "has_more": 0, "next_cursor": "cur9"}
    limited = {"errcode": 45011, "errmsg": "api freq out of limit."}
    adapter.api_client.sync_msg = AsyncMock(side_effect=[limited, ok])

    await cr_module._process_tenant_wecom_kf_messages("t1", "cfg1", "kf1", adapter)

    assert adapter.api_client.sync_msg.await_count == 2
    cursors = [c.kwargs["cursor"] for c in adapter.api_client.sync_msg.await_args_list]
    assert cursors == ["cur0", "cur0"]
    adapter.cursor_manager.set_cursor.assert_called_once_with("kf1", "cur9")


@pytest.mark.asyncio
async def test_sync_retry_exhausted_gives_up(no_sleep, monkeypatch):
    """限频连续超过最大重试次数：放弃且不更新 cursor"""
    redis = _fake_redis()
    monkeypatch.setattr(cr_module, "redis_client", redis)
    adapter = _kf_adapter()
    limited = {"errcode": 45009, "errmsg": "Too many requests."}
    adapter.api_client.sync_msg = AsyncMock(return_value=limited)

    await cr_module._process_tenant_wecom_kf_messages("t1", "cfg1", "kf1", adapter)

    # 首次 + 3 次重试 = 4 次
    assert adapter.api_client.sync_msg.await_count == 1 + cr_module._KF_SYNC_MAX_RETRIES
    adapter.cursor_manager.set_cursor.assert_not_called()


@pytest.mark.asyncio
async def test_sync_non_retryable_error_fails_immediately(no_sleep, monkeypatch):
    """非限频错误：立即失败不重试"""
    redis = _fake_redis()
    monkeypatch.setattr(cr_module, "redis_client", redis)
    adapter = _kf_adapter()
    adapter.api_client.sync_msg = AsyncMock(return_value={"errcode": 40001, "errmsg": "invalid credential"})

    await cr_module._process_tenant_wecom_kf_messages("t1", "cfg1", "kf1", adapter)

    assert adapter.api_client.sync_msg.await_count == 1
    no_sleep.assert_not_awaited()
    adapter.cursor_manager.set_cursor.assert_not_called()
