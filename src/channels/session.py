"""
渠道会话管理

管理第三方渠道的会话信息。
支持租户隔离：tenant_id 参与 session_id 生成和所有查询，防止跨租户数据串扰。
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from src.db.database import get_db_connection
from src.models.message import ChannelType
from src.core.cache_utils import CacheKeys, get_cached, set_cached, delete_cached


class ChannelSessionManager:
    """
    渠道会话管理器

    为每个租户的每个渠道用户创建和管理独立的会话。
    会话包含：聊天记录、用户信息、渠道信息。

    租户隔离：
    - session_id 由 tenant_id + channel_type + channel_user_id + subagent_id 组成，确保跨租户跨智能体唯一
    - 所有查询均包含 tenant_id 过滤
    - 缓存 key 包含 tenant_id 和 subagent_id
    """

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化会话管理器（延迟初始化）"""
        pass

    def _ensure_tables(self):
        """确保数据库表存在（延迟初始化）"""
        if self._initialized:
            return
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 渠道会话表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS channel_sessions (
                    session_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT '',
                    channel_type TEXT NOT NULL,
                    channel_user_id TEXT NOT NULL,
                    subagent_id TEXT,
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
                    tenant_id TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    message_type TEXT DEFAULT 'text',
                    attachments TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            # 索引：按租户+渠道+用户+智能体查找会话
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_channel_sessions_tenant_channel
                ON channel_sessions(tenant_id, channel_type, channel_user_id, subagent_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_channel_messages_session
                ON channel_messages(session_id, created_at)
            """)

            conn.commit()

        self._initialized = True
        logger.info("PostgreSQL: channel_sessions 表初始化完成")

    def _generate_session_id(self, tenant_id: str, channel_type: str, channel_user_id: str, subagent_id: str = "") -> str:
        """生成会话ID（包含租户和智能体信息，确保跨租户跨智能体唯一）"""
        return f"{tenant_id}_{channel_type}_{channel_user_id}_{subagent_id}"

    @staticmethod
    def _parse_json_field(value: Optional[str], default: Any = None) -> Any:
        """解析数据库中的 JSON 字段，失败时 fallback"""
        if value is None:
            return default
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            # 兼容旧数据（str(dict) 格式）
            return value

    def get_or_create_session(
        self,
        channel_type: str,
        channel_user_id: str,
        tenant_id: str = "",
        user_info: Optional[Dict[str, Any]] = None,
        channel_chat_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        subagent_id: str = "",
    ) -> Dict[str, Any]:
        """
        获取或创建渠道会话（优先从 Redis 缓存读取，TTL 10分钟）

        Args:
            channel_type: 渠道类型
            channel_user_id: 渠道用户ID
            tenant_id: 租户ID（用于隔离和 session_id 生成）
            user_info: 用户信息
            channel_chat_id: 渠道会话/群ID
            metadata: 额外元数据
            subagent_id: 关联的子智能体ID

        Returns:
            会话信息字典
        """
        session_id = self._generate_session_id(tenant_id, channel_type, channel_user_id, subagent_id)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 优先从缓存获取（缓存 key 包含 tenant_id 和 subagent_id）
        cached = get_cached(CacheKeys.CHANNEL_SESSION, tenant_id, channel_type, channel_user_id, subagent_id)
        if cached is not None:
            return cached

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 按租户+渠道+用户+智能体查找已存在的会话
            cursor.execute("""
                SELECT * FROM channel_sessions
                WHERE tenant_id = %s AND channel_type = %s AND channel_user_id = %s AND subagent_id = %s
            """, (tenant_id, channel_type, channel_user_id, subagent_id))

            row = cursor.fetchone()

            if row:
                # 更新最后消息时间
                cursor.execute("""
                    UPDATE channel_sessions
                    SET last_message_at = %s, updated_at = %s
                    WHERE session_id = %s
                """, (now, now, session_id))
                conn.commit()

                result = dict(row)
                result["context_data"] = self._parse_json_field(result.get("context_data"), {})
                result["metadata"] = self._parse_json_field(result.get("metadata"))
                set_cached(CacheKeys.CHANNEL_SESSION, tenant_id, channel_type, channel_user_id, subagent_id, value=result, ttl=600)
                return result
            else:
                # 创建新会话
                title = f"{channel_type}会话"
                if user_info and user_info.get("name"):
                    title = f"{user_info['name']}的{channel_type}会话"

                cursor.execute("""
                    INSERT INTO channel_sessions
                    (session_id, tenant_id, channel_type, channel_user_id, subagent_id,
                     channel_chat_id, user_id, username, title, context_data, created_at, updated_at,
                     last_message_at, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    session_id,
                    tenant_id,
                    channel_type,
                    channel_user_id,
                    subagent_id,
                    channel_chat_id,
                    user_info.get("user_id") if user_info else None,
                    user_info.get("name") if user_info else None,
                    title,
                    "{}",  # context_data
                    now,
                    now,
                    now,
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                ))
                conn.commit()

                result = {
                    "session_id": session_id,
                    "tenant_id": tenant_id,
                    "channel_type": channel_type,
                    "channel_user_id": channel_user_id,
                    "subagent_id": subagent_id,
                    "channel_chat_id": channel_chat_id,
                    "user_id": user_info.get("user_id") if user_info else None,
                    "username": user_info.get("name") if user_info else None,
                    "title": title,
                    "context_data": {},
                    "metadata": metadata,
                    "created_at": now,
                    "updated_at": now,
                    "last_message_at": now,
                }
                set_cached(CacheKeys.CHANNEL_SESSION, tenant_id, channel_type, channel_user_id, subagent_id, value=result, ttl=600)
                return result

    def get_session(
        self,
        channel_type: str,
        channel_user_id: str,
        tenant_id: str = "",
        subagent_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        获取渠道会话（优先从 Redis 缓存读取，TTL 10分钟）

        Args:
            channel_type: 渠道类型
            channel_user_id: 渠道用户ID
            tenant_id: 租户ID
            subagent_id: 关联的子智能体ID

        Returns:
            会话信息字典
        """
        # 优先从缓存获取
        cached = get_cached(CacheKeys.CHANNEL_SESSION, tenant_id, channel_type, channel_user_id, subagent_id)
        if cached is not None:
            return cached

        session_id = self._generate_session_id(tenant_id, channel_type, channel_user_id, subagent_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM channel_sessions WHERE session_id = %s
            """, (session_id,))

            row = cursor.fetchone()
            if not row:
                return None
            result = dict(row)
            result["context_data"] = self._parse_json_field(result.get("context_data"), {})
            result["metadata"] = self._parse_json_field(result.get("metadata"))
            # 写入缓存
            set_cached(CacheKeys.CHANNEL_SESSION, tenant_id, channel_type, channel_user_id, subagent_id, value=result, ttl=600)
            return result

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

        updates = ["updated_at = %s"]
        values = [now]

        if title is not None:
            updates.append("title = %s")
            values.append(title)

        if context_data is not None:
            updates.append("context_data = %s")
            values.append(json.dumps(context_data, ensure_ascii=False))

        if metadata is not None:
            updates.append("metadata = %s")
            values.append(json.dumps(metadata, ensure_ascii=False))

        values.append(session_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE channel_sessions
                SET {', '.join(updates)}
                WHERE session_id = %s
            """, values)
            conn.commit()

            success = cursor.rowcount > 0

            # 更新成功后清除缓存，确保下次读取获取最新数据
            if success:
                cursor.execute("""
                    SELECT tenant_id, channel_type, channel_user_id, subagent_id
                    FROM channel_sessions WHERE session_id = %s
                """, (session_id,))
                row = cursor.fetchone()
                if row:
                    delete_cached(CacheKeys.CHANNEL_SESSION, row["tenant_id"], row["channel_type"], row["channel_user_id"], row["subagent_id"])

            return success

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        attachments: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tenant_id: str = "",
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
            tenant_id: 租户ID

        Returns:
            消息ID
        """
        message_id = f"msg_{uuid.uuid4().hex[:16]}"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO channel_messages
                (message_id, session_id, tenant_id, role, content, message_type,
                 attachments, metadata, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                message_id,
                session_id,
                tenant_id,
                role,
                content,
                message_type,
                json.dumps(attachments, ensure_ascii=False) if attachments else None,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
                now,
            ))

            # 更新会话最后消息时间
            cursor.execute("""
                UPDATE channel_sessions
                SET last_message_at = %s, updated_at = %s
                WHERE session_id = %s
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
            消息列表（按 created_at ASC 时间正序）
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            placeholder = "%s"

            if before_message_id:
                cursor.execute(f"""
                    SELECT * FROM channel_messages
                    WHERE session_id = {placeholder} AND created_at < (
                        SELECT created_at FROM channel_messages WHERE message_id = {placeholder}
                    )
                    ORDER BY created_at ASC
                    LIMIT {limit}
                """, (session_id, before_message_id))
            else:
                cursor.execute(f"""
                    SELECT * FROM channel_messages
                    WHERE session_id = {placeholder}
                    ORDER BY created_at ASC
                    LIMIT {limit}
                """, (session_id,))

            rows = cursor.fetchall()
            messages = []
            for row in rows:
                msg = dict(row)
                msg["attachments"] = self._parse_json_field(msg.get("attachments"), [])
                msg["metadata"] = self._parse_json_field(msg.get("metadata"))
                messages.append(msg)
            return messages

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
        tenant_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        列出会话

        Args:
            channel_type: 渠道类型过滤
            user_id: 用户ID过滤
            tenant_id: 租户ID过滤
            limit: 限制条数

        Returns:
            会话列表
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            conditions = []
            values = []

            if tenant_id:
                conditions.append("tenant_id = %s")
                values.append(tenant_id)

            if channel_type:
                conditions.append("channel_type = %s")
                values.append(channel_type)

            if user_id:
                conditions.append(f"user_id = %s")
                values.append(user_id)

            where_clause = " AND ".join(conditions) if conditions else "1=1"

            # PostgreSQL 不支持 LIMIT %s，需要直接拼接
            cursor.execute(f"""
                SELECT * FROM channel_sessions
                WHERE {where_clause}
                ORDER BY last_message_at DESC
                LIMIT {limit}
            """, values)

            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def delete_session(self, session_id: str, tenant_id: Optional[str] = None) -> bool:
        """
        删除会话

        Args:
            session_id: 会话ID
            tenant_id: 租户ID（可选，提供时额外校验租户归属）

        Returns:
            是否成功
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            if tenant_id:
                # 带租户校验的删除
                cursor.execute("""
                    DELETE FROM channel_messages WHERE session_id = %s AND tenant_id = %s
                """, (session_id, tenant_id))
                cursor.execute("""
                    DELETE FROM channel_sessions WHERE session_id = %s AND tenant_id = %s
                """, (session_id, tenant_id))
            else:
                # 删除消息
                cursor.execute("""
                    DELETE FROM channel_messages WHERE session_id = %s
                """, (session_id,))
                # 删除会话
                cursor.execute("""
                    DELETE FROM channel_sessions WHERE session_id = %s
                """, (session_id,))

            conn.commit()

            return cursor.rowcount > 0

    def delete_messages(self, session_id: str, tenant_id: Optional[str] = None) -> bool:
        """
        仅删除会话中的消息，保留会话本身。

        Args:
            session_id: 会话ID
            tenant_id: 租户ID（可选，提供时额外校验租户归属）

        Returns:
            是否成功
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            if tenant_id:
                cursor.execute("""
                    DELETE FROM channel_messages WHERE session_id = %s AND tenant_id = %s
                """, (session_id, tenant_id))
            else:
                cursor.execute("""
                    DELETE FROM channel_messages WHERE session_id = %s
                """, (session_id,))

            conn.commit()
            logger.info(f"后端日志：channel_messages 已清空: session_id={session_id}")
            return cursor.rowcount > 0# 全局会话管理器
channel_session_manager = ChannelSessionManager()
