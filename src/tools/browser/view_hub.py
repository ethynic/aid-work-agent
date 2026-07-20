"""浏览器实时画面内存 Hub；帧永不进入日志、Redis 或数据库。"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BrowserFrame:
    run_id: str
    seq: int
    jpeg: bytes
    width: int
    height: int
    captured_at: float


class BrowserViewSubscription:
    def __init__(self, hub: "BrowserViewHub", key: tuple[str, str]) -> None:
        self._hub = hub
        self._key = key
        self._event = asyncio.Event()
        self._last_seq = 0
        self.closed = False

    async def next_frame(self, timeout: float = 30.0) -> BrowserFrame | None:
        while not self.closed:
            # 先清 event 再读 latest，避免 publish 落在“读帧→clear”窗口时丢唤醒。
            self._event.clear()
            frame = self._hub.latest(*self._key)
            if frame is not None and frame.seq > self._last_seq:
                self._last_seq = frame.seq
                return frame
            try:
                await asyncio.wait_for(self._event.wait(), timeout)
            except asyncio.TimeoutError:
                return None
        return None

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            await self._hub.unsubscribe(self)


class BrowserViewHub:
    """每个 run 仅保留最新帧，慢消费者自然丢弃旧帧。"""

    def __init__(self) -> None:
        self._frames: dict[tuple[str, str], BrowserFrame] = {}
        self._subscribers: dict[tuple[str, str], set[BrowserViewSubscription]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, tenant_id: str, frame: BrowserFrame) -> None:
        if not frame.jpeg or len(frame.jpeg) > 1024 * 1024:
            return
        key = (tenant_id, frame.run_id)
        async with self._lock:
            previous = self._frames.get(key)
            if previous is not None and frame.seq <= previous.seq:
                return
            self._frames[key] = frame
            subscribers = tuple(self._subscribers.get(key, ()))
        for subscriber in subscribers:
            subscriber._event.set()

    def latest(self, tenant_id: str, run_id: str) -> BrowserFrame | None:
        return self._frames.get((tenant_id, run_id))

    async def subscribe(self, tenant_id: str, run_id: str) -> BrowserViewSubscription:
        key = (tenant_id, run_id)
        subscription = BrowserViewSubscription(self, key)
        async with self._lock:
            self._subscribers.setdefault(key, set()).add(subscription)
        return subscription

    async def unsubscribe(self, subscription: BrowserViewSubscription) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(subscription._key)
            if subscribers:
                subscribers.discard(subscription)
                if not subscribers:
                    self._subscribers.pop(subscription._key, None)

    def observer_count(self, tenant_id: str, run_id: str) -> int:
        return len(self._subscribers.get((tenant_id, run_id), ()))

    async def clear(self, tenant_id: str, run_id: str) -> None:
        key = (tenant_id, run_id)
        async with self._lock:
            self._frames.pop(key, None)
            subscribers = self._subscribers.pop(key, set())
        for subscription in subscribers:
            subscription.closed = True
            subscription._event.set()


browser_view_hub = BrowserViewHub()
