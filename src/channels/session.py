"""
渠道会话管理

管理第三方渠道的会话信息
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.models.message import ChannelType


class ChannelSessionManager:
    """
    渠道会话管理器

    为每个渠道用户创建和管理独立的会话
    会话包含：聊天记录、用户信息、渠道信息
    """

    def __init__(self):
        """初始化会话管理器"""
        self._ensure_tables()

    def _ensure_tables(self):
        """确保数据库表存在"""
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 渠道会话表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_sessions (
                    session_id TEXT PRIMARY KEY,
                    channel_type TEXT NOT NULL,
                    channel_user_id TEXT NOT NULL,
                    channel_chat_id TEXT,
                    user_id TEXT,
                    username TEXT,
                    title TEXT,
                    context_data TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_message_at TEXT,
                    metadata TEXT
                )
            """)

            # 渠道消息表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_messages (
                    message_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    message_type TEXT DEFAULT 'text',
                    attachments TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES channel_sessions(session_id)
                )
            """)

            # 索引
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_channel_sessions_channel
                ON channel_sessions(channel_type, channel_user_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_channel_messages_session
                ON channel_messages(session_id, created_at)
            """)

            conn.commit()

    def _generate_session_id(self, channel_type: str, channel_user_id: str) -> str:
        """生成会话ID"""
        return f"{channel_type}_{channel_user_id}"

    def get_or_create_session(
        self,
        channel_type: str,
        channel_user_id: str,
        user_info: Optional[Dict[str, Any]] = None,
        channel_chat_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        获取或创建渠道会话

        Args:
            channel_type: 渠道类型
            channel_user_id: 渠道用户ID
            user_info: 用户信息
            channel_chat_id: 渠道会话/群ID
            metadata: 额外元数据

        Returns:
            会话信息字典
        """
        session_id = self._generate_session_id(channel_type, channel_user_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 查找已存在的会话
            cursor.execute("""
                SELECT * FROM channel_sessions
                WHERE channel_type = ? AND channel_user_id = ?
            """, (channel_type, channel_user_id))

            row = cursor.fetchone()

            if row:
                # 更新最后消息时间
                cursor.execute("""
                    UPDATE channel_sessions
                    SET last_message_at = ?, updated_at = ?
                    WHERE session_id = ?
                """, (now, now, session_id))
                conn.commit()

                return dict(row)
            else:
                # 创建新会话
                title = f"{channel_type}会话"
                if user_info and user_info.get("name"):
                    title = f"{user_info['name']}的{channel_type}会话"

                cursor.execute("""
                    INSERT INTO channel_sessions
                    (session_id, channel_type, channel_user_id, channel_chat_id,
                     user_id, username, title, context_data, created_at, updated_at,
                     last_message_at, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    channel_type,
                    channel_user_id,
                    channel_chat_id,
                    user_info.get("user_id") if user_info else None,
                    user_info.get("name") if user_info else None,
                    title,
                    "{}",  # context_data
                    now,
                    now,
                    now,
                    str(metadata) if metadata else None,
                ))
                conn.commit()

                return {
                    "session_id": session_id,
                    "channel_type": channel_type,
                    "channel_user_id": channel_user_id,
                    "channel_chat_id": channel_chat_id,
                    "user_id": user_info.get("user_id") if user_info else None,
                    "username": user_info.get("name") if user_info else None,
                    "title": title,
                    "created_at": now,
                    "updated_at": now,
                    "last_message_at": now,
                }

    def get_session(
        self,
        channel_type: str,
        channel_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        获取渠道会话

        Args:
            channel_type: 渠道类型
            channel_user_id: 渠道用户ID

        Returns:
            会话信息字典
        """
        session_id = self._generate_session_id(channel_type, channel_user_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM channel_sessions WHERE session_id = ?
            """, (session_id,))

            row = cursor.fetchone()
            return dict(row) if row else None

    def update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        context_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        更新会话信息

        Args:
            session_id: 会话ID
            title: 标题
            context_data: 上下文数据
            metadata: 额外数据

        Returns:
            是否成功
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        updates = ["updated_at = ?"]
        values = [now]

        if title is not None:
            updates.append("title = ?")
            values.append(title)

        if context_data is not None:
            updates.append("context_data = ?")
            values.append(str(context_data))

        if metadata is not None:
            updates.append("metadata = ?")
            values.append(str(metadata))

        values.append(session_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE channel_sessions
                SET {', '.join(updates)}
                WHERE session_id = ?
            """, values)
            conn.commit()

            return cursor.rowcount > 0

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        attachments: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        添加消息到会话

        Args:
            session_id: 会话ID
            role: 角色 (user/assistant/system)
            content: 消息内容
            message_type: 消息类型
            attachments: 附件列表
            metadata: 额外数据

        Returns:
            消息ID
        """
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO channel_messages
                (message_id, session_id, role, content, message_type,
                 attachments, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                message_id,
                session_id,
                role,
                content,
                message_type,
                str(attachments) if attachments else None,
                str(metadata) if metadata else None,
                now,
            ))

            # 更新会话最后消息时间
            cursor.execute("""
                UPDATE channel_sessions
                SET last_message_at = ?, updated_at = ?
                WHERE session_id = ?
            """, (now, now, session_id))

            conn.commit()

        return message_id

    def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        before_message_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        获取会话消息

        Args:
            session_id: 会话ID
            limit: 限制条数
            before_message_id: 分页基准消息ID

        Returns:
            消息列表
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            if before_message_id:
                cursor.execute("""
                    SELECT * FROM channel_messages
                    WHERE session_id = ? AND created_at < (
                        SELECT created_at FROM channel_messages WHERE message_id = ?
                    )
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (session_id, before_message_id, limit))
            else:
                cursor.execute("""
                    SELECT * FROM channel_messages
                    WHERE session_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (session_id, limit))

            rows = cursor.fetchall()
            return [dict(row) for row in reversed(rows)]

    def get_conversation_context(
        self,
        session_id: str,
        max_messages: int = 20,
    ) -> List[Dict[str, str]]:
        """
        获取对话上下文（用于LLM）

        Args:
            session_id: 会话ID
            max_messages: 最大消息数

        Returns:
            LLM格式的消息列表
        """
        messages = self.get_messages(session_id, limit=max_messages)

        context = []
        for msg in messages:
            context.append({
                "role": msg["role"],
                "content": msg["content"],
            })

        return context

    def list_sessions(
        self,
        channel_type: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        列出会话

        Args:
            channel_type: 渠道类型过滤
            user_id: 用户ID过滤
            limit: 限制条数

        Returns:
            会话列表
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            conditions = []
            values = []

            if channel_type:
                conditions.append("channel_type = ?")
                values.append(channel_type)

            if user_id:
                conditions.append("user_id = ?")
                values.append(user_id)

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            cursor.execute(f"""
                SELECT * FROM channel_sessions
                WHERE {where_clause}
                ORDER BY last_message_at DESC
                LIMIT ?
            """, values + [limit])

            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        """
        删除会话

        Args:
            session_id: 会话ID

        Returns:
            是否成功
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 删除消息
            cursor.execute("""
                DELETE FROM channel_messages WHERE session_id = ?
            """, (session_id,))

            # 删除会话
            cursor.execute("""
                DELETE FROM channel_sessions WHERE session_id = ?
            """, (session_id,))

            conn.commit()

            return cursor.rowcount > 0


# 全局会话管理器
channel_session_manager = ChannelSessionManager()
