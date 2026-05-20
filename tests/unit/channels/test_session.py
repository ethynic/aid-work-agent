"""
渠道会话管理单元测试

测试 metadata / context_data / attachments 的 JSON 序列化 round-trip。
验证租户隔离：session_id 包含 tenant_id，查询带租户过滤。
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from src.channels.session import ChannelSessionManager
from src.core.cache_utils import CacheKeys, delete_cached_pattern

TEST_TENANT = "test_tenant"


@pytest.fixture(autouse=True)
def _clear_channel_cache():
    """每个测试前清除渠道会话缓存，避免跨测试缓存污染"""
    delete_cached_pattern(CacheKeys.CHANNEL_SESSION, "")


@pytest.fixture
def session_manager():
    """创建 ChannelSessionManager 实例（已 mock 数据库）"""
    manager = ChannelSessionManager()
    manager._initialized = False
    return manager


@pytest.fixture
def mock_db():
    """模拟 PostgreSQL 内存数据库"""
    memory_store = {
        "sessions": {},  # session_id -> dict
        "messages": [],  # list of dict
    }

    class MockRow(dict):
        """模拟 psycopg2 RealDictCursor 返回的行"""
        pass

    def _make_connection():
        conn = MagicMock()
        cursor = MagicMock()
        cursor._fetch_rows = []
        cursor._fetch_idx = 0

        def execute(sql, params=None):
            sql_lower = sql.lower()
            params = params or ()
            cursor._fetch_rows = []
            cursor._fetch_idx = 0

            if "insert into channel_sessions" in sql_lower:
                row = MockRow(
                    session_id=params[0],
                    tenant_id=params[1],
                    channel_type=params[2],
                    channel_user_id=params[3],
                    channel_chat_id=params[4],
                    user_id=params[5],
                    username=params[6],
                    title=params[7],
                    context_data=params[8],
                    created_at=params[9],
                    updated_at=params[10],
                    last_message_at=params[11],
                    metadata=params[12],
                )
                memory_store["sessions"][params[0]] = row
                cursor.rowcount = 1

            elif "update channel_sessions" in sql_lower:
                sid = params[-1]
                if sid in memory_store["sessions"]:
                    row = memory_store["sessions"][sid]
                    if len(params) >= 3:
                        row["updated_at"] = params[0]
                        if "title" in sql_lower and "context_data" not in sql_lower and "metadata" not in sql_lower:
                            row["title"] = params[1]
                        elif "context_data" in sql_lower and "metadata" not in sql_lower:
                            row["context_data"] = params[1]
                        elif "metadata" in sql_lower and "context_data" not in sql_lower:
                            row["metadata"] = params[1]
                        elif "context_data" in sql_lower and "metadata" in sql_lower:
                            row["context_data"] = params[1]
                            row["metadata"] = params[2]
                    cursor.rowcount = 1
                else:
                    cursor.rowcount = 0

            elif "select * from channel_sessions where session_id" in sql_lower:
                sid = params[0]
                if sid in memory_store["sessions"]:
                    cursor._fetch_rows = [memory_store["sessions"][sid]]
                cursor.rowcount = 1 if cursor._fetch_rows else 0

            elif "select tenant_id, channel_type, channel_user_id from channel_sessions" in sql_lower:
                # update_session 清除缓存时查询租户信息
                sid = params[0]
                if sid in memory_store["sessions"]:
                    row = memory_store["sessions"][sid]
                    cursor._fetch_rows = [(row["tenant_id"], row["channel_type"], row["channel_user_id"])]
                cursor.rowcount = 1 if cursor._fetch_rows else 0

            elif "select * from channel_sessions where tenant_id" in sql_lower:
                tid, ctype, cuid = params[0], params[1], params[2]
                for row in memory_store["sessions"].values():
                    if (row.get("tenant_id") == tid and
                        row["channel_type"] == ctype and
                        row["channel_user_id"] == cuid):
                        cursor._fetch_rows = [row]
                        break

            elif "select * from channel_sessions" in sql_lower and "where" in sql_lower:
                # Generic fallback for list queries
                pass

            elif "insert into channel_messages" in sql_lower:
                row = MockRow(
                    message_id=params[0],
                    session_id=params[1],
                    tenant_id=params[2],
                    role=params[3],
                    content=params[4],
                    message_type=params[5],
                    attachments=params[6],
                    metadata=params[7],
                    created_at=params[8],
                )
                memory_store["messages"].append(row)
                cursor.rowcount = 1

            elif "select * from channel_messages" in sql_lower:
                sid = params[0]
                rows = [r for r in memory_store["messages"] if r["session_id"] == sid]
                cursor._fetch_rows = list(reversed(rows))

            elif "delete from" in sql_lower:
                if "channel_messages" in sql_lower:
                    sid = params[0]
                    memory_store["messages"] = [m for m in memory_store["messages"] if m["session_id"] != sid]
                elif "channel_sessions" in sql_lower:
                    sid = params[0]
                    if sid in memory_store["sessions"]:
                        del memory_store["sessions"][sid]
                cursor.rowcount = 1

            elif "create table" in sql_lower or "create index" in sql_lower:
                cursor.rowcount = 0

        def fetchone():
            rows = cursor._fetch_rows
            if rows:
                return rows[0]
            return None

        def fetchall():
            return cursor._fetch_rows

        cursor.execute = execute
        cursor.fetchone = fetchone
        cursor.fetchall = fetchall
        conn.cursor.return_value = cursor
        return conn

    with patch("src.channels.session.get_db_connection") as mock_get_conn:
        mock_conn = _make_connection()
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        yield memory_store


class TestMetadataRoundTrip:
    """Metadata 序列化 round-trip 测试"""

    def test_session_metadata_roundtrip(self, session_manager, mock_db):
        """会话 metadata 写入和读取 round-trip"""
        metadata = {"message_type": "text", "source": "wecom", "extra": {"key": "value"}}
        session = session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
            tenant_id=TEST_TENANT,
            metadata=metadata,
        )
        sid = session["session_id"]
        assert TEST_TENANT in sid

        # 读取会话，验证 metadata 正确反序列化
        result = session_manager.get_session("wecom", "user123", tenant_id=TEST_TENANT)
        assert result is not None
        assert result["metadata"] == metadata
        assert result["tenant_id"] == TEST_TENANT

    def test_session_context_data_roundtrip(self, session_manager, mock_db):
        """会话 context_data 更新和读取 round-trip（直接验证 DB 数据）"""
        session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
            tenant_id=TEST_TENANT,
        )

        context_data = {"step": 3, "data": {"items": [1, 2, 3]}, "flag": True}
        sid = session_manager._generate_session_id(TEST_TENANT, "wecom", "user123")

        # 直接在 mock_db 中验证初始 context_data
        assert sid in mock_db["sessions"]
        assert mock_db["sessions"][sid]["context_data"] == "{}"

        # 更新 context_data
        updated = session_manager.update_session(
            session_id=sid,
            context_data=context_data,
        )
        assert updated is True

        # 验证 mock_db 中已更新
        import json
        assert json.loads(mock_db["sessions"][sid]["context_data"]) == context_data

    def test_message_metadata_roundtrip(self, session_manager, mock_db):
        """消息 metadata 和 attachments 写入和读取 round-trip"""
        session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
            tenant_id=TEST_TENANT,
        )

        metadata = {"raw_xml": "<xml>...</xml>", "msg_id": "msg_001"}
        attachments = [
            {"file_name": "test.pdf", "url": "https://example.com/test.pdf"},
            {"file_name": "image.png", "url": "https://example.com/image.png"},
        ]

        sid = session_manager._generate_session_id(TEST_TENANT, "wecom", "user123")
        session_manager.add_message(
            session_id=sid,
            role="user",
            content="测试消息",
            attachments=attachments,
            metadata=metadata,
            tenant_id=TEST_TENANT,
        )

        messages = session_manager.get_messages(sid)
        assert len(messages) == 1
        msg = messages[0]
        assert msg["metadata"] == metadata
        assert msg["attachments"] == attachments
        assert msg["tenant_id"] == TEST_TENANT

    def test_legacy_str_dict_fallback(self, session_manager, mock_db):
        """兼容旧的 str(dict) 格式：fallback 到原样返回"""
        old_metadata_str = "{'source': 'wecom', 'old': True}"
        old_attachments_str = "[{'file': 'old.pdf'}]"
        legacy_sid = f"{TEST_TENANT}_wecom_legacy_user"
        row = dict(
            session_id=legacy_sid,
            tenant_id=TEST_TENANT,
            channel_type="wecom",
            channel_user_id="legacy_user",
            channel_chat_id=None,
            user_id=None,
            username=None,
            title="Legacy Session",
            context_data="{'step': 1}",
            created_at="2026-01-01 00:00:00",
            updated_at="2026-01-01 00:00:00",
            last_message_at="2026-01-01 00:00:00",
            metadata=old_metadata_str,
        )
        mock_db["sessions"][legacy_sid] = row
        mock_db["messages"].append(dict(
            message_id="msg_legacy",
            session_id=legacy_sid,
            tenant_id=TEST_TENANT,
            role="user",
            content="legacy",
            message_type="text",
            attachments=old_attachments_str,
            metadata=old_metadata_str,
            created_at="2026-01-01 00:00:00",
        ))

        result = session_manager.get_session("wecom", "legacy_user", tenant_id=TEST_TENANT)
        assert result["metadata"] == old_metadata_str

        messages = session_manager.get_messages(legacy_sid)
        assert messages[0]["attachments"] == old_attachments_str


class TestTenantIsolation:
    """租户隔离测试"""

    def test_different_tenants_get_different_sessions(self, session_manager, mock_db):
        """不同租户的同名用户获得不同会话"""
        s1 = session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="ZhangSan",
            tenant_id="tenant_a",
        )
        s2 = session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="ZhangSan",
            tenant_id="tenant_b",
        )
        assert s1["session_id"] != s2["session_id"]
        assert "tenant_a" in s1["session_id"]
        assert "tenant_b" in s2["session_id"]

    def test_session_id_format(self, session_manager, mock_db):
        """验证 session_id 格式包含租户信息"""
        sid = session_manager._generate_session_id("mytenant", "wecom", "user001")
        assert sid == "mytenant_wecom_user001"

    def test_empty_tenant_session_id_format(self, session_manager, mock_db):
        """空 tenant_id 时 session_id 格式"""
        sid = session_manager._generate_session_id("", "wecom", "user001")
        assert sid == "_wecom_user001"