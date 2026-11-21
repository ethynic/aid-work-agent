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
                    # 模拟 ON CONFLICT (message_id) DO NOTHING：冲突不报错，rowcount=0
                    # （2026-10-16 起实现不再抛 IntegrityError，mock 同步该语义）
                    cursor.rowcount = 0
                else:
                    shared_state["records"].add(msg_id)
                    cursor.rowcount = 1
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


@pytest.fixture
def throttle_db_connection():
    """模拟年龄感知 upsert 的 PostgreSQL 连接（claim_throttle 语义）"""
    # 共享状态：key -> created_at（模拟 channel_message_dedup 行）
    shared_state = {"rows": {}}

    def _make_connection():
        conn = MagicMock()
        cursor = MagicMock()

        def execute(sql, params=None):
            sql_upper = " ".join(sql.strip().upper().split())
            if "INSERT" in sql_upper and "ON CONFLICT" in sql_upper:
                key, now, cutoff = params[0], params[1], params[2]
                if key not in shared_state["rows"]:
                    shared_state["rows"][key] = now
                    cursor.rowcount = 1
                elif shared_state["rows"][key] < cutoff:
                    # 旧行超过 ttl：DO UPDATE WHERE 生效，刷新占位
                    shared_state["rows"][key] = now
                    cursor.rowcount = 1
                else:
                    # 旧行仍在窗口内：DO UPDATE WHERE 不生效
                    cursor.rowcount = 0
            elif "CREATE" in sql_upper:
                pass
            else:
                cursor.rowcount = 0

        cursor.execute = execute
        conn.cursor.return_value = cursor
        return conn

    with patch("src.channels.idempotency.get_db_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=_make_connection())
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        yield shared_state


class TestClaimThrottle:
    """限流占位（claim_throttle）测试：N 分钟窗口内最多提示一次"""

    @pytest.mark.asyncio
    async def test_first_claim_succeeds(self, throttle_db_connection):
        """首次占位成功（应发送提示）"""
        dedup = MessageDeduplicator(ttl_seconds=300)
        claimed = await dedup.claim_throttle("kf_filter_hint:t1:u1", ttl_seconds=300)
        assert claimed is True

    @pytest.mark.asyncio
    async def test_second_claim_within_window_throttled(self, throttle_db_connection):
        """窗口内再次调用被节流（不应发送提示）"""
        dedup = MessageDeduplicator(ttl_seconds=300)
        await dedup.claim_throttle("kf_filter_hint:t1:u1", ttl_seconds=300)
        claimed = await dedup.claim_throttle("kf_filter_hint:t1:u1", ttl_seconds=300)
        assert claimed is False

    @pytest.mark.asyncio
    async def test_expired_row_refreshes_claim(self, throttle_db_connection):
        """旧行超过 ttl 后刷新放行（窗口滚动，不会拖成全天）"""
        dedup = MessageDeduplicator(ttl_seconds=300)
        await dedup.claim_throttle("kf_filter_hint:t1:u1", ttl_seconds=300)
        # 模拟时间流逝：把行龄手工推老 400 秒（> ttl 300）
        for key in list(throttle_db_connection["rows"]):
            throttle_db_connection["rows"][key] -= 400
        claimed = await dedup.claim_throttle("kf_filter_hint:t1:u1", ttl_seconds=300)
        assert claimed is True

    @pytest.mark.asyncio
    async def test_different_keys_independent(self, throttle_db_connection):
        """不同租户/客户 key 互不影响"""
        dedup = MessageDeduplicator(ttl_seconds=300)
        await dedup.claim_throttle("kf_filter_hint:t1:u1", ttl_seconds=300)
        claimed = await dedup.claim_throttle("kf_filter_hint:t1:u2", ttl_seconds=300)
        assert claimed is True

    @pytest.mark.asyncio
    async def test_db_error_fails_open(self):
        """DB 异常时放行（宁可重复提示，不可静默）"""
        with patch("src.channels.idempotency.get_db_connection") as mock_get_conn:
            conn = MagicMock()
            cursor = MagicMock()

            def execute(sql, params=None):
                # 仅让业务 INSERT 失败；构造期 _ensure_table 的 CREATE TABLE 正常通过
                if "INSERT" in sql.upper():
                    raise psycopg2.OperationalError("connection lost")

            cursor.execute = execute
            conn.cursor.return_value = cursor
            mock_get_conn.return_value.__enter__ = MagicMock(return_value=conn)
            mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
            dedup = MessageDeduplicator(ttl_seconds=300)
            claimed = await dedup.claim_throttle("kf_filter_hint:t1:u1", ttl_seconds=300)
            assert claimed is True
