"""
消息去重模块

防止 WeCom 回调重试导致的重复处理。
基于 PostgreSQL 的分布式去重，支持多 worker 部署。
"""

import time
from typing import Optional

import psycopg2
from loguru import logger

from src.db.database import get_db_connection


class MessageDeduplicator:
    """
    基于 PostgreSQL 的分布式消息去重器

    WeCom 在未收到 5 秒内响应时会重试回调（最多 3 次）。
    此模块通过 message_id 去重，避免同一消息被多次处理。
    使用 PostgreSQL 实现，支持多 worker / 多进程部署场景。
    """

    def __init__(self, ttl_seconds: int = 300):
        """
        Args:
            ttl_seconds: 去重记录的 TTL（秒），默认 5 分钟
        """
        self._ttl = ttl_seconds
        self._ensure_table()

    def _ensure_table(self):
        """确保去重表存在"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_message_dedup (
                    message_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_dedup_created_at
                ON channel_message_dedup(created_at)
            """)
            conn.commit()

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

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO channel_message_dedup (message_id, created_at)
                    VALUES (%s, %s)
                """, (message_id, now))
                conn.commit()
                return False
            except psycopg2.IntegrityError:
                conn.rollback()
                return True
            except Exception as e:
                conn.rollback()
                logger.error(f"去重检查异常: {e}")
                # 异常时保守处理，视为重复以避免重复处理
                return True

    def cleanup_expired(self) -> int:
        """
        清理过期的去重记录

        Returns:
            清理的记录数
        """
        cutoff = time.time() - self._ttl
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    DELETE FROM channel_message_dedup
                    WHERE created_at < %s
                """, (cutoff,))
                conn.commit()
                cleaned = cursor.rowcount
                if cleaned > 0:
                    logger.debug(f"清理过期去重记录: {cleaned} 条")
                return cleaned
            except psycopg2.OperationalError as e:
                conn.rollback()
                logger.warning(f"清理过期去重记录失败（连接问题）: {e}")
                return 0
            except Exception as e:
                conn.rollback()
                logger.error(f"清理过期去重记录异常: {e}")
                return 0

    def clear(self):
        """清空所有去重记录"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM channel_message_dedup")
            conn.commit()
            logger.info("去重记录已清空")
