"""
Session API 端到端测试
测试数据库交互层：会话创建、查询、更新、删除、消息管理
"""

import pytest
import uuid
from datetime import datetime


class TestSessionCRUD:
    """会话 CRUD 测试"""

    @pytest.fixture
    def test_user_for_session(self):
        """创建测试用户"""
        from src.db.models import UserDB, hash_password

        user_id = f"session_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "session_test",
            "phone": phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)

        yield user_id

        # 清理
        try:
            UserDB.delete(user_id)
        except Exception:
            pass

    def test_create_session(self, test_user_for_session):
        """测试会话创建"""
        from src.db.models import SessionDB

        user_id = test_user_for_session
        session = SessionDB.create(user_id, title="Test Session")

        assert session is not None
        assert "session_id" in session
        assert session["user_id"] == user_id

    def test_get_session_by_id(self, test_user_for_session):
        """测试根据 ID 获取会话"""
        from src.db.models import SessionDB

        user_id = test_user_for_session
        session = SessionDB.create(user_id, title="Test Session")
        session_id = session["session_id"]

        retrieved = SessionDB.get_by_id(session_id)

        assert retrieved is not None
        assert retrieved["session_id"] == session_id

    def test_list_sessions_by_user(self, test_user_for_session):
        """测试列出用户的所有会话"""
        from src.db.models import SessionDB

        user_id = test_user_for_session

        # 创建多个会话
        for i in range(3):
            SessionDB.create(user_id, title=f"Session {i}")

        sessions = SessionDB.list_by_user(user_id, limit=10)

        assert len(sessions) >= 3

    def test_update_session_title(self, test_user_for_session):
        """测试更新会话标题"""
        from src.db.models import SessionDB

        user_id = test_user_for_session
        session = SessionDB.create(user_id, title="Original Title")
        session_id = session["session_id"]

        result = SessionDB.update_title(session_id, "Updated Title")

        assert result is True

        updated = SessionDB.get_by_id(session_id)
        assert updated["title"] == "Updated Title"

    def test_update_session_context(self, test_user_for_session):
        """测试更新会话上下文"""
        from src.db.models import SessionDB

        user_id = test_user_for_session
        session = SessionDB.create(user_id, title="Test Session")
        session_id = session["session_id"]

        new_context = {"key": "value", "count": 42}
        result = SessionDB.update_context(session_id, new_context)

        assert result is True

        updated = SessionDB.get_by_id(session_id)
        import json
        context = json.loads(updated["context_data"])
        assert context["key"] == "value"

    def test_delete_session(self, test_user_for_session):
        """测试删除会话"""
        from src.db.models import SessionDB

        user_id = test_user_for_session
        session = SessionDB.create(user_id, title="Test Session")
        session_id = session["session_id"]

        result = SessionDB.delete(session_id)

        assert result is True

        # 验证删除成功
        retrieved = SessionDB.get_by_id(session_id)
        assert retrieved is None

    def test_delete_session_preserves_chat_records(self, test_user_for_session):
        """删除会话时必须保留 chat_records（计费/审计数据）"""
        from src.db.models import SessionDB, MessageDB, ChatRecordDB
        from src.db.database import get_db_connection

        user_id = test_user_for_session
        session = SessionDB.create(user_id, title="Billing Test Session")
        session_id = session["session_id"]

        # 写入一条消息和一条计费记录
        MessageDB.add(session_id, "user", "测试消息")
        record = ChatRecordDB.create(
            session_id=session_id,
            user_id=user_id,
            user_message="测试消息",
            assistant_message="测试回复",
            total_token_count=100,
            prompt_tokens=40,
            completion_tokens=60,
            model="qwen-test",
            provider="qwen",
        )
        assert record is not None
        record_id = record["record_id"]

        # 删除会话
        assert SessionDB.delete(session_id) is True

        # 会话和消息应已删除
        assert SessionDB.get_by_id(session_id) is None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS c FROM chat_messages WHERE session_id = %s", (session_id,))
            assert cursor.fetchone()["c"] == 0

            # chat_records 必须仍然存在，用于计费聚合
            cursor.execute("SELECT record_id, total_token_count FROM chat_records WHERE record_id = %s", (record_id,))
            row = cursor.fetchone()
            assert row is not None
            assert row["total_token_count"] == 100


class TestSessionMessages:
    """会话消息测试"""

    @pytest.fixture
    def test_user_and_session(self):
        """创建测试用户和会话"""
        from src.db.models import UserDB, SessionDB, hash_password

        user_id = f"msg_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "msg_test",
            "phone": phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)
        session = SessionDB.create(user_id, title="Message Test")

        yield user_id, session["session_id"]

        # 清理
        try:
            SessionDB.delete(session["session_id"])
        except Exception:
            pass
        try:
            UserDB.delete(user_id)
        except Exception:
            pass

    def test_create_message(self, test_user_and_session):
        """测试消息创建"""
        from src.db.models import MessageDB

        user_id, session_id = test_user_and_session

        message = MessageDB.create(
            session_id=session_id,
            role="user",
            content="Hello, AI!"
        )

        assert message is not None
        assert "message_id" in message
        assert message["session_id"] == session_id

    def test_get_message_by_id(self, test_user_and_session):
        """测试根据 ID 获取消息"""
        from src.db.models import MessageDB

        user_id, session_id = test_user_and_session

        message = MessageDB.create(
            session_id=session_id,
            role="user",
            content="Test message"
        )
        message_id = message["message_id"]

        retrieved = MessageDB.get_by_id(message_id)

        assert retrieved is not None
        assert retrieved["message_id"] == message_id

    def test_list_messages_by_session(self, test_user_and_session):
        """测试列出会话的所有消息"""
        from src.db.models import MessageDB

        user_id, session_id = test_user_and_session

        # 创建多条消息
        for i in range(3):
            MessageDB.create(
                session_id=session_id,
                role="user",
                content=f"Message {i}"
            )

        messages = MessageDB.list_by_session(session_id, limit=10)

        assert len(messages) >= 3

    def test_delete_message(self, test_user_and_session):
        """测试删除消息"""
        from src.db.models import MessageDB

        user_id, session_id = test_user_and_session

        message = MessageDB.create(
            session_id=session_id,
            role="user",
            content="To be deleted"
        )
        message_id = message["message_id"]

        result = MessageDB.delete(message_id)

        assert result is True

        # 验证删除成功
        retrieved = MessageDB.get_by_id(message_id)
        assert retrieved is None


class TestSessionRecords:
    """会话记录（对话历史）测试"""

    @pytest.fixture
    def test_user_and_session_for_records(self):
        """创建测试用户和会话"""
        from src.db.models import UserDB, SessionDB, hash_password

        user_id = f"record_test_{uuid.uuid4().hex[:8]}"
        phone = f"138{uuid.uuid4().hex[:8]}"

        user_data = {
            "user_id": user_id,
            "username": "record_test",
            "phone": phone,
            "password_hash": hash_password("Test123456"),
            "status": "active"
        }

        UserDB.create(user_data)
        session = SessionDB.create(user_id, title="Record Test")

        yield user_id, session["session_id"]

        # 清理
        # SessionDB.delete 有意保留 chat_records（计费审计表），测试产生的
        # chat_records 必须由 fixture 自行清理，否则会以 tenant_id NULL 残留
        try:
            from src.db.database import get_db_connection

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM chat_records WHERE user_id = %s", (user_id,))
                conn.commit()
        except Exception:
            pass
        try:
            SessionDB.delete(session["session_id"])
        except Exception:
            pass
        try:
            UserDB.delete(user_id)
        except Exception:
            pass

    def test_create_record(self, test_user_and_session_for_records):
        """测试创建会话记录"""
        from src.db.models import ChatRecordDB

        user_id, session_id = test_user_and_session_for_records

        record = ChatRecordDB.create(
            session_id=session_id,
            user_id=user_id,
            user_message="Hello",
            assistant_message="Hi there!",
            total_token_count=100,
            model="qwen-turbo"
        )

        assert record is not None
        assert "record_id" in record

    def test_get_record_by_id(self, test_user_and_session_for_records):
        """测试根据 ID 获取会话记录"""
        from src.db.models import ChatRecordDB

        user_id, session_id = test_user_and_session_for_records

        record = ChatRecordDB.create(
            session_id=session_id,
            user_id=user_id,
            user_message="Hello",
            assistant_message="Hi there!"
        )
        record_id = record["record_id"]

        retrieved = ChatRecordDB.get_by_id(record_id)

        assert retrieved is not None
        assert retrieved["record_id"] == record_id

    def test_list_records_by_session(self, test_user_and_session_for_records):
        """测试列出会话的所有记录"""
        from src.db.models import ChatRecordDB

        user_id, session_id = test_user_and_session_for_records

        # 创建多条记录
        for i in range(3):
            ChatRecordDB.create(
                session_id=session_id,
                user_id=user_id,
                user_message=f"User message {i}",
                assistant_message=f"AI response {i}",
                total_token_count=100 + i
            )

        records = ChatRecordDB.list_by_session(session_id, limit=10)

        assert len(records) >= 3

    def test_list_records_by_user(self, test_user_and_session_for_records):
        """测试列出用户的所有会话记录"""
        from src.db.models import ChatRecordDB

        user_id, session_id = test_user_and_session_for_records

        # 创建记录
        ChatRecordDB.create(
            session_id=session_id,
            user_id=user_id,
            user_message="Test",
            assistant_message="Result"
        )

        records = ChatRecordDB.list_by_user(user_id, limit=10)

        assert len(records) >= 1

    def test_delete_record(self, test_user_and_session_for_records):
        """测试删除会话记录"""
        from src.db.models import ChatRecordDB

        user_id, session_id = test_user_and_session_for_records

        record = ChatRecordDB.create(
            session_id=session_id,
            user_id=user_id,
            user_message="To be deleted",
            assistant_message="Response"
        )
        record_id = record["record_id"]

        result = ChatRecordDB.delete(record_id)

        assert result is True

        # 验证删除成功
        retrieved = ChatRecordDB.get_by_id(record_id)
        assert retrieved is None