"""
渠道会话管理单元测试

测试 metadata / context_data / attachments 的 JSON 序列化 round-trip。
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from src.channels.session import ChannelSessionManager


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
                    channel_type=params[1],
                    channel_user_id=params[2],
                    channel_chat_id=params[3],
                    user_id=params[4],
                    username=params[5],
                    title=params[6],
                    context_data=params[7],
                    created_at=params[8],
                    updated_at=params[9],
                    last_message_at=params[10],
                    metadata=params[11],
                )
                memory_store["sessions"][params[0]] = row
                cursor.rowcount = 1

            elif "update channel_sessions" in sql_lower:
                sid = params[-1]
                if sid in memory_store["sessions"]:
                    row = memory_store["sessions"][sid]
                    # 简单解析 set 字段（按 params 顺序）
                    if len(params) >= 3:
                        row["updated_at"] = params[0]
                        # params[1] 是 title/context_data/metadata 等
                        # 根据 SQL 中的字段名判断
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

            elif "select * from channel_sessions where channel_type" in sql_lower:
                ctype, cuid = params[0], params[1]
                for row in memory_store["sessions"].values():
                    if row["channel_type"] == ctype and row["channel_user_id"] == cuid:
                        cursor._fetch_rows = [row]
                        break

            elif "insert into channel_messages" in sql_lower:
                row = MockRow(
                    message_id=params[0],
                    session_id=params[1],
                    role=params[2],
                    content=params[3],
                    message_type=params[4],
                    attachments=params[5],
                    metadata=params[6],
                    created_at=params[7],
                )
                memory_store["messages"].append(row)
                cursor.rowcount = 1

            elif "select * from channel_messages" in sql_lower:
                sid = params[0]
                rows = [r for r in memory_store["messages"] if r["session_id"] == sid]
                # 默认升序，反转后取最新
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
            metadata=metadata,
        )
        sid = session["session_id"]

        # 读取会话，验证 metadata 正确反序列化
        result = session_manager.get_session("wecom", "user123")
        assert result is not None
        assert result["metadata"] == metadata

    def test_session_context_data_roundtrip(self, session_manager, mock_db):
        """会话 context_data 更新和读取 round-trip"""
        session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
        )

        context_data = {"step": 3, "data": {"items": [1, 2, 3]}, "flag": True}
        updated = session_manager.update_session(
            session_id="wecom_user123",
            context_data=context_data,
        )
        assert updated is True

        result = session_manager.get_session("wecom", "user123")
        assert result["context_data"] == context_data

    def test_message_metadata_roundtrip(self, session_manager, mock_db):
        """消息 metadata 和 attachments 写入和读取 round-trip"""
        session_manager.get_or_create_session(
            channel_type="wecom",
            channel_user_id="user123",
        )

        metadata = {"raw_xml": "<xml>...</xml>", "msg_id": "msg_001"}
        attachments = [
            {"file_name": "test.pdf", "url": "https://example.com/test.pdf"},
            {"file_name": "image.png", "url": "https://example.com/image.png"},
        ]

        session_manager.add_message(
            session_id="wecom_user123",
            role="user",
            content="测试消息",
            attachments=attachments,
            metadata=metadata,
        )

        messages = session_manager.get_messages("wecom_user123")
        assert len(messages) == 1
        msg = messages[0]
        assert msg["metadata"] == metadata
        assert msg["attachments"] == attachments

    def test_legacy_str_dict_fallback(self, session_manager, mock_db):
        """兼容旧的 str(dict) 格式：fallback 到原样返回"""
        # 手动插入一条旧格式的记录（str(dict) 格式，使用单引号）
        # session_id 必须是 channel_type + _ + channel_user_id
        old_metadata_str = "{'source': 'wecom', 'old': True}"
        old_attachments_str = "[{'file': 'old.pdf'}]"
        legacy_sid = "wecom_legacy_user"
        row = dict(
            session_id=legacy_sid,
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
            role="user",
            content="legacy",
            message_type="text",
            attachments=old_attachments_str,
            metadata=old_metadata_str,
            created_at="2026-01-01 00:00:00",
        ))

        result = session_manager.get_session("wecom", "legacy_user")
        # 旧格式无法 json.loads，fallback 到原样返回字符串
        assert result["metadata"] == old_metadata_str

        messages = session_manager.get_messages(legacy_sid)
        assert messages[0]["attachments"] == old_attachments_str
