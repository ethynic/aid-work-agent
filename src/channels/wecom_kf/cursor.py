"""微信客服消息同步游标管理器

使用 Redis 存储每个客服账号的消息同步游标，支持多 worker 共享。
TTL 默认 3 天，与微信客服消息保留期一致。
"""
from typing import Optional

from loguru import logger

from src.core.redis_client import RedisClient


class CursorManager:
    """管理每个客服账号的消息同步游标"""

    CURSOR_TTL = 259200  # 3 天（秒）
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
