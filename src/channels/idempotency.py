"""
消息去重模块

防止 WeCom 回调重试导致的重复处理。
基于 TTL 的内存缓存，适合单进程部署。
"""

import asyncio
import time
from typing import Dict


class MessageDeduplicator:
    """
    基于 TTL 的消息去重器

    WeCom 在未收到 5 秒内响应时会重试回调（最多 3 次）。
    此模块通过 message_id 去重，避免同一消息被多次处理。
    """

    def __init__(self, ttl_seconds: int = 300):
        """
        Args:
            ttl_seconds: 去重记录的 TTL（秒），默认 5 分钟
        """
        self._cache: Dict[str, float] = {}  # msg_id -> timestamp
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()

    async def is_duplicate(self, message_id: str) -> bool:
        """
        检查消息是否重复

        首次见到的 message_id 会被记录，再次出现时返回 True。

        Args:
            message_id: 消息唯一 ID

        Returns:
            True 表示重复消息，应跳过处理
        """
        now = time.time()
        async with self._lock:
            # 清理过期条目
            expired = [k for k, v in self._cache.items() if now - v > self._ttl]
            for k in expired:
                del self._cache[k]

            if message_id in self._cache:
                return True

            self._cache[message_id] = now
            return False

    def clear(self):
        """清空缓存"""
        self._cache.clear()
