"""KeyPool 跨事件循环与并发限流测试

背景：KeyPool 是进程级单例，被主 HTTP 事件循环与 APScheduler 后台线程的
独立事件循环（asyncio.new_event_loop）交替使用。Python 3.10+ 的
asyncio.Semaphore/Lock 首次 await 即绑定所在循环，跨循环复用会抛
"is bound to a different event loop"（2026-08-19 生产 02:30 复盘任务故障）。
本测试验证修复后的跨循环安全与并发限流语义。
"""

import asyncio
import threading

import pytest

from src.llm.key_pool import KeyPool

pytestmark = pytest.mark.unit


@pytest.fixture
def pool() -> KeyPool:
    return KeyPool(
        keys=["key_a", "key_b"],
        max_concurrent_per_key=2,
        queue_timeout=2.0,
    )


async def _use_pool(pool: KeyPool) -> str:
    """单次获取并释放一个 Key"""
    async with pool.acquire() as key:
        return key


def test_acquire_release_basic(pool):
    """基本获取/释放：能取回有效 Key，且多次使用不耗竭"""
    async def _run():
        keys = {await _use_pool(pool) for _ in range(6)}
        return keys
    keys = asyncio.run(_run())
    assert keys == {"key_a", "key_b"}


def test_concurrency_limit_enforced():
    """并发限流：max_concurrent=1 时第二个并发获取会排队直至超时"""
    pool2 = KeyPool(keys=["only_key"], max_concurrent_per_key=1, queue_timeout=0.2)

    async def _run():
        # 先占住唯一槽位
        held = pool2.acquire()
        await held.__aenter__()
        try:
            # 槽位被占，第二个获取应等待 queue_timeout 后抛 TimeoutError
            with pytest.raises(TimeoutError):
                await pool2.acquire().__aenter__()
        finally:
            await held.__aexit__(None, None, None)

    asyncio.run(_run())


def test_cross_event_loop_reuse(pool):
    """跨事件循环复用：主循环与后台线程新循环交替使用同一 KeyPool 不报错"""
    results: list = []

    def _background_worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            r = loop.run_until_complete(_use_pool(pool))
            results.append(("new_loop", r))
        finally:
            loop.close()

    async def _main_phase():
        return await _use_pool(pool)

    # 主循环
    r1 = asyncio.run(_main_phase())
    assert r1 in ("key_a", "key_b")

    # 后台线程新循环（修复前此处抛 RuntimeError: bound to a different event loop）
    t = threading.Thread(target=_background_worker)
    t.start()
    t.join()
    assert len(results) == 1
    assert results[0][0] == "new_loop"

    # 主循环再使用（验证重绑回主循环）
    r3 = asyncio.run(_main_phase())
    assert r3 in ("key_a", "key_b")
