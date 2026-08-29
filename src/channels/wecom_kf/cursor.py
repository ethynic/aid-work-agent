"""微信客服消息同步游标管理器

使用 Redis 存储每个客服账号的消息同步游标，支持多 worker 共享。
TTL 默认 30 天。本地增量游标仅记录"上次同步到哪"，与微信消息保留期无耦合，
TTL 必须远大于微信 3 天保留期，否则 cursor 先于保留期到期会触发 3 天窗口全量重放。
"""
from typing import Optional

from loguru import logger

from src.core.redis_client import RedisClient


class CursorManager:
    """管理每个客服账号的消息同步游标"""

    CURSOR_TTL = 2592000  # 30 天（本地增量游标，与微信消息保留期无关，应远大于 3 天）
    KEY_PREFIX = "wecom_kf_cursor:"

    def __init__(self, redis_client: RedisClient):
        self._redis = redis_client

    def _key(self, open_kfid: str) -> str:
        return f"{self.KEY_PREFIX}{open_kfid}"

    def get_cursor(self, open_kfid: str) -> str:
        """获取上次同步的 cursor，无则返回空字符串"""
        try:
            cursor = self._redis.get(self._key(open_kfid))
            return cursor or ""
        except Exception as e:
            logger.warning(f"[CursorManager] 获取 cursor 失败: {e}")
            return ""

    def set_cursor(self, open_kfid: str, cursor: str, ttl: Optional[int] = None):
        """更新 cursor（带 TTL）"""
        if not cursor:
            return
        ttl = ttl or self.CURSOR_TTL
        try:
            self._redis.set(self._key(open_kfid), cursor, ex=ttl)
        except Exception as e:
            logger.warning(f"[CursorManager] 保存 cursor 失败: {e}")

    def delete_cursor(self, open_kfid: str):
        """删除 cursor（会话结束时调用）"""
        try:
            self._redis.delete(self._key(open_kfid))
        except Exception as e:
            logger.warning(f"[CursorManager] 删除 cursor 失败: {e}")
