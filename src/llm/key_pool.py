"""
LLM API Key 池

管理多个 API Key，通过 Semaphore 控制每个 Key 的并发上限，
超出并发时自动排队等待，防止触发 API 速率限制。
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator, List, Optional

from loguru import logger


class _KeySlot:
    """单个 Key 的并发槽位"""

    def __init__(self, key: str, max_concurrent: int):
        self.key = key
        self.max_concurrent = max_concurrent
        # 信号量按事件循环惰性重绑：KeyPool 是进程级单例，主 HTTP 事件循环与
        # APScheduler 后台线程的独立循环（asyncio.new_event_loop）会交替使用，
        # Python 3.10+ 的 asyncio 原语首次 await 即绑定所在循环，跨循环复用会抛
        # "is bound to a different event loop"。_semaphore 为 None 表示未绑定。
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        # 统计用（可选）
        self._total_calls: int = 0
        self._active_calls: int = 0

    def _ensure_loop(self) -> asyncio.Semaphore:
        """返回绑定到当前运行循环的信号量（跨循环时重绑）"""
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop is not loop:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
            self._loop = loop
        return self._semaphore

    @property
    def available(self) -> bool:
        """当前 Key 是否有空闲并发槽"""
        if self._semaphore is None:
            return True
        return self._semaphore._value > 0  # type: ignore[attr-defined]

    async def acquire(self, timeout: float) -> bool:
        """尝试获取并发槽，超时返回 False"""
        sem = self._ensure_loop()
        try:
            await asyncio.wait_for(sem.acquire(), timeout=timeout)
            self._active_calls += 1
            self._total_calls += 1
            return True
        except asyncio.TimeoutError:
            return False

    def release(self) -> None:
        sem = self._ensure_loop()
        sem.release()
        self._active_calls -= 1


class KeyPool:
    """
    API Key 池

    支持轮询策略：优先选取有空闲槽位的 Key；
    若全部 Key 都被占满，则等待最早释放的槽位。

    用法（推荐通过 async with 上下文管理器）::

        async with key_pool.acquire() as api_key:
            # 使用 api_key 调用 LLM
            ...
        # 退出时自动释放槽位
    """

    def __init__(
        self,
        keys: List[str],
        max_concurrent_per_key: int = 2,
        queue_timeout: float = 30.0,
    ):
        """
        Args:
            keys: API Key 列表
            max_concurrent_per_key: 每个 Key 的最大并发数
            queue_timeout: 等待可用 Key 的超时秒数
        """
        if not keys:
            raise ValueError("KeyPool 初始化失败：keys 列表不能为空")

        self._slots: List[_KeySlot] = [
            _KeySlot(key, max_concurrent_per_key) for key in keys
        ]
        self._queue_timeout = queue_timeout
        self._round_robin_idx = 0
        # 锁按事件循环惰性重绑（原因同 _KeySlot._semaphore）
        self._lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        logger.info(
            f"KeyPool 初始化完成：{len(keys)} 个 Key，"
            f"每 Key 最大并发 {max_concurrent_per_key}，"
            f"总并发上限 {len(keys) * max_concurrent_per_key}"
        )

    @asynccontextmanager
    async def acquire(self) -> AsyncGenerator[str, None]:
        """
        获取一个可用 Key 的上下文管理器。

        优先使用有空闲槽位的 Key（轮询顺序），
        全部占满时等待任一 Key 释放（先到先得）。
        """
        slot = await self._get_slot()
        idx = next(
            i for i, s in enumerate(self._slots) if s is slot
        )
        logger.debug(
            f"[KeyPool] 开始使用 Key#{idx} (...{slot.key[-6:]})，"
            f"本 Key 累计调用: {slot._total_calls}，当前活跃: {slot._active_calls}"
        )
        try:
            yield slot.key
        finally:
            slot.release()
            logger.debug(
                f"[KeyPool] 释放 Key#{idx} (...{slot.key[-6:]})，"
                f"本 Key 累计调用: {slot._total_calls}，当前活跃: {slot._active_calls}"
            )

    def _ensure_loop(self) -> asyncio.Lock:
        """返回绑定到当前运行循环的锁（跨循环时重绑锁与各槽位信号量）"""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._lock = asyncio.Lock()
            self._loop = loop
            for slot in self._slots:
                slot._ensure_loop()
        return self._lock

    async def _get_slot(self) -> _KeySlot:
        """轮询找到可用 Key 槽位"""
        n = len(self._slots)
        lock = self._ensure_loop()

        # 快速路径：按轮询顺序找一个有空闲的 Key
        async with lock:
            start = self._round_robin_idx
            for i in range(n):
                idx = (start + i) % n
                slot = self._slots[idx]
                if slot.available:
                    # 预占：更新轮询起点
                    self._round_robin_idx = (idx + 1) % n
                    # 在锁外 acquire semaphore（避免死锁），这里直接 acquire，一定成功
                    break
            else:
                slot = None  # type: ignore

        if slot is not None:
            # 在锁外获取信号量（此时 _value > 0，不会阻塞）
            acquired = await slot.acquire(timeout=self._queue_timeout)
            if acquired:
                return slot

        # 慢路径：所有 Key 都满了，并发等待所有槽位
        logger.debug("[KeyPool] 所有 Key 并发槽已满，进入排队等待...")
        slot = await self._wait_any_slot()
        return slot

    async def _wait_any_slot(self) -> _KeySlot:
        """并发尝试所有 Key，取最先成功的"""
        tasks = []
        for slot in self._slots:
            task = asyncio.create_task(
                self._try_acquire_slot(slot)
            )
            tasks.append((task, slot))

        done, pending = await asyncio.wait(
            [t for t, _ in tasks],
            timeout=self._queue_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # 取消其他等待中的任务
        for task in pending:
            task.cancel()

        if not done:
            raise TimeoutError(
                f"等待可用 API Key 超时（{self._queue_timeout}s），"
                "请增加 Key 数量或提高每 Key 并发上限"
            )

        # 找到对应的 slot
        completed_task = next(iter(done))
        for task, slot in tasks:
            if task is completed_task:
                idx = next(
                    i for i, s in enumerate(self._slots) if s is slot
                )
                logger.debug(
                    f"[KeyPool] Key#{idx} (...{slot.key[-6:]}) 排队等待后获取成功"
                )
                return slot

        raise RuntimeError("KeyPool 内部错误：无法匹配已完成的任务")

    async def _try_acquire_slot(self, slot: _KeySlot) -> None:
        """尝试获取指定槽位（用于并发等待）"""
        sem = slot._ensure_loop()
        await sem.acquire()
        slot._active_calls += 1
        slot._total_calls += 1

    def stats(self) -> List[dict]:
        """返回各 Key 的使用统计（用于监控/调试）"""
        return [
            {
                "key_suffix": f"...{s.key[-6:]}",
                "active": s._active_calls,
                "total": s._total_calls,
                "max_concurrent": s.max_concurrent,
            }
            for s in self._slots
        ]
