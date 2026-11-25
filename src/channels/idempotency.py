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

    def __init__(self, ttl_seconds: int = 3600):
        """
        Args:
            ttl_seconds: 去重记录的 TTL（秒），默认 1 小时
                （5 分钟窗口只能拦截回调重试，拦不住 cursor 到期触发的 3 天窗口全量重放）
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
            True 表示重复消息，应跳过处理；False 表示新消息或 DB 异常时放行（让上游业务幂等兜底）
        """
        now = time.time()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # ON CONFLICT DO NOTHING：冲突即重复（rowcount=0），
                # 与捕获 IntegrityError 等价，但不让 postgres 日志产生 ERROR 噪音
                # （2026-10-16 巡检：wecom_kf 回调重试高频冲突被误判为数据库故障）
                cursor.execute("""
                    INSERT INTO channel_message_dedup (message_id, created_at)
                    VALUES (%s, %s)
                    ON CONFLICT (message_id) DO NOTHING
                """, (message_id, now))
                conn.commit()
                return cursor.rowcount == 0
            except Exception as e:
                # DB 异常（连接抖动 / 死锁 / 不可达）时放行，
                # 避免静默丢弃用户消息。下游业务需自行幂等兜底。
                conn.rollback()
                logger.warning(
                    f"去重检查 DB 异常，放行消息 {message_id}: {e}"
                )
                return False

    async def claim_throttle(self, key: str, ttl_seconds: int) -> bool:
        """限流占位：key 在 ttl_seconds 窗口内首次调用占位成功返回 True，窗口内后续调用返回 False。

        与 is_duplicate（行存在即重复，语义由每日 03:00 的 cleanup_expired 兜底过期）
        不同，本方法按行龄判断：行不存在或已超过 ttl 时刷新 created_at 并放行。
        适用场景：拦截提示等「N 分钟最多提示一次」的节流（kf_filter_hint）。
        不能用 is_duplicate——其行要等到次日清理才会消失，窗口会膨胀到接近一天。

        Args:
            key: 节流键（如 kf_filter_hint:{tenant_id}:{user_id}）
            ttl_seconds: 节流窗口秒数

        Returns:
            True 表示占位成功（应发送提示）；False 表示窗口内已提示过（跳过）；
            DB 异常时放行（宁可重复提示，不可静默）。
        """
        now = time.time()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 年龄感知 upsert：冲突且旧行超过 ttl 时刷新（rowcount=1 占位成功），
                # 冲突且旧行仍在窗口内时 DO UPDATE 不生效（rowcount=0，节流命中）
                cursor.execute("""
                    INSERT INTO channel_message_dedup (message_id, created_at)
                    VALUES (%s, %s)
                    ON CONFLICT (message_id) DO UPDATE
                    SET created_at = EXCLUDED.created_at
                    WHERE channel_message_dedup.created_at < %s
                """, (key, now, now - ttl_seconds))
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                conn.rollback()
                # 占位失败视为窗口未占用，放行提示（宁可重复提示，不可静默吞掉）
                logger.warning(f"限流占位 DB 异常，放行 {key}: {e}")
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
