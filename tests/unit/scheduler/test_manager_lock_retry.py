"""ScheduledTaskManager 分布式锁重试接管单元测试（全 mock，不触真实 Redis/DB）。

背景（生产故障）：docker 强杀旧容器后 Redis 单副本锁（TTL 300s）残留为幽灵锁，
start() 抢锁失败即永久放弃，调度器停摆不自愈。修复后抢锁失败改为起后台线程，
锁 TTL 过期后重试接管并补启动；Redis 异常分支保持既有 fail-open 行为不变。
"""

from unittest.mock import MagicMock

import pytest

import src.scheduler.manager as manager_mod
from src.scheduler.manager import ScheduledTaskManager


@pytest.fixture()
def fast_retry(monkeypatch):
    """缩短重试间隔，让后台线程在测试内快速循环。"""
    monkeypatch.setattr(manager_mod, "_LOCK_RETRY_SECONDS", 0.01)


@pytest.fixture()
def patched_startup(monkeypatch):
    """打桩启动主体依赖：APScheduler / DB 加载 / 系统任务注册。"""
    monkeypatch.setattr(manager_mod, "BackgroundScheduler", lambda **kw: MagicMock())
    monkeypatch.setattr(manager_mod.ScheduledTaskDB, "list_active", lambda: [])
    monkeypatch.setattr(ScheduledTaskManager, "_register_system_jobs", lambda self: None)


@pytest.mark.unit
class TestManagerLockRetry:
    def test_start_lock_held_spawns_retry_thread_then_takes_over(
        self, monkeypatch, fast_retry, patched_startup
    ):
        """抢锁失败 → 重试线程启动并返回（不放弃）；锁释放后线程接管完成启动。"""
        m = ScheduledTaskManager()
        calls = {"n": 0}

        def fake_acquire(key, value, ex=None):
            calls["n"] += 1
            return calls["n"] > 1  # 首次被占（幽灵锁），之后可抢到

        monkeypatch.setattr(manager_mod.redis_client, "acquire_lock", fake_acquire)

        m.start()
        thread = m._retry_thread
        assert thread is not None and thread.is_alive()
        assert m._running is False  # start() 未放弃但也未启动主体

        thread.join(timeout=5)
        assert not thread.is_alive()
        # 接管成功：调度器主体已启动，锁值保持接管时使用的值
        assert m._running is True
        assert m._lock_value is not None
        assert m._scheduler.start.call_count == 1
        assert calls["n"] == 2  # start() 1 次 + 重试线程 1 次

    def test_repeat_start_no_second_retry_thread(
        self, monkeypatch, fast_retry, patched_startup
    ):
        """重复调用 start()（锁仍被占）→ 复用同一重试线程，不起第二个。"""
        m = ScheduledTaskManager()
        monkeypatch.setattr(
            manager_mod.redis_client, "acquire_lock", lambda k, v, ex=None: False
        )

        m.start()
        thread = m._retry_thread
        assert thread is not None and thread.is_alive()
        m.start()  # 再次抢锁仍失败
        assert m._retry_thread is thread

        # 清理：置 running 让重试线程按循环条件退出
        m._running = True
        thread.join(timeout=5)
        assert not thread.is_alive()

    def test_redis_error_fail_open_unchanged(self, monkeypatch, fast_retry, patched_startup):
        """Redis 异常分支保持既有 fail-open：直接启动调度器，不起重试线程。"""
        m = ScheduledTaskManager()

        def boom(key, value, ex=None):
            raise RuntimeError("redis down")

        monkeypatch.setattr(manager_mod.redis_client, "acquire_lock", boom)

        m.start()
        assert m._running is True
        assert m._lock_value is None
        assert m._retry_thread is None
        assert m._scheduler.start.call_count == 1

    def test_direct_acquire_success_no_retry_thread(
        self, monkeypatch, fast_retry, patched_startup
    ):
        """正常路径：首次抢锁即成功 → 直接启动，不起重试线程。"""
        m = ScheduledTaskManager()
        monkeypatch.setattr(
            manager_mod.redis_client, "acquire_lock", lambda k, v, ex=None: True
        )

        m.start()
        assert m._running is True
        assert m._lock_value is not None
        assert m._retry_thread is None
        assert m._scheduler.start.call_count == 1
