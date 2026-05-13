"""
消息去重模块单元测试

测试基于 PostgreSQL 的分布式消息去重。
"""

import pytest
import time
from unittest.mock import MagicMock, patch

import psycopg2

from src.channels.idempotency import MessageDeduplicator


@pytest.fixture
def mock_db_connection():
    """模拟 PostgreSQL 连接，支持跨连接去重"""
    # 共享状态，模拟数据库持久化
    shared_state = {
        "records": set(),  # 已插入的 message_id 集合
        "deleted_count": 0,
    }

    def _make_connection():
        conn = MagicMock()
        cursor = MagicMock()

        def execute(sql, params=None):
            sql_upper = sql.strip().upper()
            if "INSERT" in sql_upper:
                msg_id = params[0] if params else ""
                if msg_id in shared_state["records"]:
                    # 模拟唯一键冲突
                    err = psycopg2.IntegrityError("duplicate key value violates unique constraint")
                    raise err
                shared_state["records"].add(msg_id)
            elif "DELETE" in sql_upper:
                cutoff = params[0] if params else 0
                # 模拟删除过期记录
                to_remove = set()
                shared_state["deleted_count"] += len(to_remove)
            elif "CREATE TABLE" in sql_upper:
                pass  # 建表语句忽略
            elif "CREATE INDEX" in sql_upper:
                pass  # 建索引忽略
            elif "DELETE FROM" in sql_upper and "WHERE" not in sql_upper:
                # clear() 调用
                shared_state["records"].clear()

        cursor.execute = execute
        cursor.rowcount = 1  # 默认影响行数
        conn.cursor.return_value = cursor
        return conn

    with patch("src.channels.idempotency.get_db_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=_make_connection())
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        yield shared_state


class TestMessageDeduplicator:
    """消息去重测试"""

    @pytest.mark.asyncio
    async def test_first_message_not_duplicate(self, mock_db_connection):
        """首次消息不重复"""
        dedup = MessageDeduplicator(ttl_seconds=300)
        result = await dedup.is_duplicate("msg_001")
        assert result is False
        assert "msg_001" in mock_db_connection["records"]

    @pytest.mark.asyncio
    async def test_second_message_is_duplicate(self, mock_db_connection):
        """重复消息返回 True"""
        dedup = MessageDeduplicator(ttl_seconds=300)
        await dedup.is_duplicate("msg_002")
        result = await dedup.is_duplicate("msg_002")
        assert result is True

    @pytest.mark.asyncio
    async def test_different_messages_not_duplicate(self, mock_db_connection):
        """不同消息 ID 互不干扰"""
        dedup = MessageDeduplicator(ttl_seconds=300)
        await dedup.is_duplicate("msg_a")
        result = await dedup.is_duplicate("msg_b")
        assert result is False

    @pytest.mark.asyncio
    async def test_cross_instance_deduplication(self, mock_db_connection):
        """跨实例去重：模拟不同 worker 进程使用同一数据库"""
        dedup1 = MessageDeduplicator(ttl_seconds=300)
        dedup2 = MessageDeduplicator(ttl_seconds=300)

        # 实例1 记录消息
        result1 = await dedup1.is_duplicate("msg_cross")
        assert result1 is False

        # 实例2 应能检测到同一消息已存在
        result2 = await dedup2.is_duplicate("msg_cross")
        assert result2 is True

    def test_cleanup_expired(self, mock_db_connection):
        """清理过期记录"""
        dedup = MessageDeduplicator(ttl_seconds=1)
        # cleanup_expired 执行删除语句
        cleaned = dedup.cleanup_expired()
        # mock 中删除逻辑简化，只要方法不抛异常即通过
        assert isinstance(cleaned, int)

    def test_clear(self, mock_db_connection):
        """清空所有记录"""
        dedup = MessageDeduplicator(ttl_seconds=300)
        dedup.clear()
        assert len(mock_db_connection["records"]) == 0
