"""
数据库访问模型

提供用户和会话的数据库CRUD操作
"""

import json
import random
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import bcrypt
from loguru import logger

from src.config.settings import settings
from src.db.database import get_db_connection, get_current_timestamp
from src.saas.db.permission_db import UserAgentPermissionDB
from src.saas.models.enums import UserStatus
from src.core.cache_utils import CacheKeys, get_cached, set_cached, delete_cached, delete_cached_pattern, invalidate_user_cache


# ============== 密码哈希 ==============

def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码"""
    try:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
    except Exception:
        return False


def generate_user_id() -> str:
    """生成唯一用户ID"""
    return f"user_{uuid.uuid4().hex[:12]}"


def generate_session_id() -> str:
    """生成唯一会话ID"""
    return f"session_{uuid.uuid4().hex[:12]}"


def generate_message_id() -> str:
    """生成唯一消息ID"""
    return f"msg_{uuid.uuid4().hex[:12]}"


# ============== 用户数据库访问 ==============

class UserDB:
    """用户数据库访问类"""

    @staticmethod
    def create(phone: str = None, password: str = None,
              wx_openid: str = None, username: str = None,
              role: str = "user", tenant_id: str = None,
              source: str = None) -> Optional[Dict[str, Any]]:
        """创建新用户

        Args:
            phone: 手机号
            password: 密码
            wx_openid: 微信openid
            username: 用户名
            role: 角色，platform_admin/tenant_admin/user
            tenant_id: 租户ID，平台管理员为空
        """
        user_id = generate_user_id()
        placeholder = "%s"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(f"""
                    INSERT INTO users (user_id, phone, password_hash, wx_openid, username, role, tenant_id, source)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                """, (user_id, phone, hash_password(password) if password else None,
                      wx_openid, username or (f"用户{phone[-4:]}" if phone else f"用户{user_id[-4:]}"),
                      role, tenant_id, source))
                conn.commit()

                logger.info(f"User created: {user_id} with role {role}")
                return UserDB.get_by_id(user_id)
            except Exception as e:
                logger.error(f"Failed to create user: {e}")
                return None

    @staticmethod
    def get_by_id(user_id: str) -> Optional[Dict[str, Any]]:
        """根据用户ID获取用户（优先从 Redis 缓存读取）"""
        # 优先从缓存获取
        cached = get_cached(CacheKeys.USER, user_id)
        if cached is not None:
            return cached

        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM users WHERE user_id = {placeholder}", (user_id,))
            row = cursor.fetchone()
            user = dict(row) if row else None
            if user:
                # 缓存时过滤敏感字段
                safe_user = {k: v for k, v in user.items() if k != "password_hash"}
                set_cached(CacheKeys.USER, user_id, value=safe_user, ttl=600)
            return user

    @staticmethod
    def get_by_phone(phone: str, bypass_cache: bool = False) -> Optional[Dict[str, Any]]:
        """根据手机号获取用户（优先从 Redis 缓存读取）

        Args:
            phone: 手机号
            bypass_cache: 绕过缓存，直接查询数据库（登录等需要 password_hash 的场景）
        """
        if not bypass_cache:
            # 优先从缓存获取
            cached = get_cached(CacheKeys.USER, f"phone:{phone}")
            if cached is not None:
                return cached

        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM users WHERE phone = {placeholder}", (phone,))
            row = cursor.fetchone()
            user = dict(row) if row else None
            if user and not bypass_cache:
                # 缓存时不存 password_hash
                safe_user = {k: v for k, v in user.items() if k != "password_hash"}
                set_cached(CacheKeys.USER, f"phone:{phone}", value=safe_user, ttl=600)
            return user

    @staticmethod
    def get_by_phone_in_tenant(phone: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """根据手机号和租户ID获取用户（用于检查租户内手机号是否重复）"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM users WHERE phone = {placeholder} AND tenant_id = {placeholder}",
                (phone, tenant_id)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_username_in_tenant(username: str, tenant_id: str) -> Optional[Dict[str, Any]]:
        """根据用户名和租户ID获取用户（用于检查租户内用户名是否重复）"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM users WHERE username = {placeholder} AND tenant_id = {placeholder}",
                (username, tenant_id)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_wx_openid(openid: str) -> Optional[Dict[str, Any]]:
        """根据微信openid获取用户"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM users WHERE wx_openid = {placeholder}", (openid,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def update_wx_openid(user_id: str, wx_openid: str) -> bool:
        """绑定微信openid"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE users SET wx_openid = {placeholder} WHERE user_id = {placeholder}",
                          (wx_openid, user_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_info(user_id: str, **kwargs) -> bool:
        """更新用户信息

        Args:
            user_id: 用户ID
            **kwargs: 可更新字段，支持 username, avatar_url, role, tenant_id
        """
        allowed_fields = ["username", "avatar_url", "role", "tenant_id", "source"]
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return False

        placeholder = "%s"
        set_clause = ", ".join([f"{k} = {placeholder}" for k in updates.keys()])
        ts = get_current_timestamp()
        values = list(updates.values()) + [user_id]

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE users SET {set_clause}, updated_at = {ts} WHERE user_id = {placeholder}",
                          values)
            conn.commit()
            result = cursor.rowcount > 0
            if result:
                # 清除用户缓存，下次查询从数据库重新加载
                invalidate_user_cache(user_id)
            return result

    # 别名方法，保持向后兼容
    update = update_info

    @staticmethod
    def list_users(page: int = 1, page_size: int = 20, tenant_id: str = None) -> dict:
        """获取用户列表（分页）

        Args:
            page: 页码，从1开始
            page_size: 每页数量
            tenant_id: 可选，按租户过滤

        Returns:
            {"users": [...], "total": int, "page": int, "page_size": int}
        """
        offset = (page - 1) * page_size
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id:
                cursor.execute("SELECT COUNT(*) as cnt FROM users WHERE tenant_id = %s", (tenant_id,))
                total = cursor.fetchone()["cnt"]
                cursor.execute(
                    "SELECT * FROM users WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    (tenant_id, page_size, offset),
                )
            else:
                cursor.execute("SELECT COUNT(*) as cnt FROM users")
                total = cursor.fetchone()["cnt"]
                cursor.execute(
                    "SELECT * FROM users ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    (page_size, offset),
                )
            users = [dict(row) for row in cursor.fetchall()]
            # 添加每个用户的数字员工授权数量
            for u in users:
                u["agent_count"] = UserAgentPermissionDB.count_allowed(conn, u["user_id"])
        return {"users": users, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def list_by_tenant(tenant_id: str, page: int = 1, page_size: int = 20) -> dict:
        """获取指定租户下的用户列表（分页）

        Args:
            tenant_id: 租户ID
            page: 页码，从1开始
            page_size: 每页数量

        Returns:
            {"users": [...], "total": int, "page": int, "page_size": int}
        """
        offset = (page - 1) * page_size
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) as cnt FROM users WHERE tenant_id = %s AND status = '{UserStatus.ACTIVE.value}'",
                (tenant_id,),
            )
            total = cursor.fetchone()["cnt"]
            cursor.execute(f"""
                SELECT * FROM users
                WHERE tenant_id = %s AND status = '{UserStatus.ACTIVE.value}'
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
            """, (tenant_id, page_size, offset))
            users = [dict(row) for row in cursor.fetchall()]
            # 添加每个用户的数字员工授权数量
            for u in users:
                u["agent_count"] = UserAgentPermissionDB.count_allowed(conn, u["user_id"])
        return {"users": users, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def list_admins(tenant_id: str = None) -> List[Dict[str, Any]]:
        """获取管理员列表

        Args:
            tenant_id: 如果指定则列出该租户的管理员，否则列出所有平台管理员
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id:
                cursor.execute(f"""
                    SELECT * FROM users
                    WHERE role IN ('platform_admin', 'tenant_admin')
                    AND tenant_id = %s AND status = '{UserStatus.ACTIVE.value}'
                    ORDER BY created_at
                """, (tenant_id,))
            else:
                cursor.execute(f"""
                    SELECT * FROM users
                    WHERE role = 'platform_admin' AND status = '{UserStatus.ACTIVE.value}'
                    ORDER BY created_at
                """)
            return [dict(row) for row in cursor.fetchall()]


# ============== 会话数据库访问 ==============

class SessionDB:
    """会话数据库访问类"""

    @staticmethod
    def create(user_id: str, title: str = None, context_data: dict = None, tenant_id: str = None, subagent_id: str = None, instance_id: str = None) -> Optional[Dict[str, Any]]:
        """创建新会话"""
        session_id = generate_session_id()
        placeholder = "%s"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(f"""
                    INSERT INTO chat_sessions (session_id, user_id, tenant_id, subagent_id, instance_id, title, context_data)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                """, (session_id, user_id, tenant_id, subagent_id, instance_id, title or "新会话",
                      json.dumps(context_data) if context_data else None))
                conn.commit()

                logger.info(f"Chat session created: {session_id} for user: {user_id}, tenant: {tenant_id}, subagent: {subagent_id}")
                # 清除用户的会话列表缓存
                delete_cached_pattern(CacheKeys.USER_SESSIONS, user_id, "")
                return SessionDB.get_by_id(session_id)
            except Exception as e:
                logger.error(f"Failed to create chat session: {e}")
                return None

    @staticmethod
    def get_by_id(session_id: str) -> Optional[Dict[str, Any]]:
        """根据会话ID获取会话（优先从 Redis 缓存读取，TTL 5分钟）"""
        cached = get_cached(CacheKeys.SESSION, session_id)
        if cached is not None:
            return cached

        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM chat_sessions WHERE session_id = {placeholder}", (session_id,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                if result.get("context_data"):
                    result["context_data"] = json.loads(result["context_data"])
                set_cached(CacheKeys.SESSION, session_id, value=result, ttl=300)
                return result
            return None

    @staticmethod
    def list_by_user(user_id: str, page: int = 1, page_size: int = 20, tenant_id: str = None) -> Dict[str, Any]:
        """获取用户的会话列表（分页，优先从 Redis 缓存读取，TTL 30秒）

        Args:
            user_id: 用户ID
            page: 页码，从1开始
            page_size: 每页数量
            tenant_id: 租户ID，传入时仅返回该租户下的会话；不传则返回所有会话（兼容非SaaS模式）
        """
        tid = tenant_id or "none"
        cached = get_cached(CacheKeys.USER_SESSIONS, user_id, tid, str(page), str(page_size))
        if cached is not None:
            return cached

        offset = (page - 1) * page_size
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 先统计总数
            if tenant_id is not None:
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM chat_sessions WHERE user_id = %s AND tenant_id = %s",
                    (user_id, tenant_id)
                )
                total = cursor.fetchone()["cnt"]
                cursor.execute(f"""
                    SELECT * FROM chat_sessions
                    WHERE user_id = {placeholder} AND tenant_id = {placeholder}
                    ORDER BY updated_at DESC
                    LIMIT {placeholder} OFFSET {placeholder}
                """, (user_id, tenant_id, page_size, offset))
            else:
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM chat_sessions WHERE user_id = %s",
                    (user_id,)
                )
                total = cursor.fetchone()["cnt"]
                cursor.execute(f"""
                    SELECT * FROM chat_sessions
                    WHERE user_id = {placeholder}
                    ORDER BY updated_at DESC
                    LIMIT {placeholder} OFFSET {placeholder}
                """, (user_id, page_size, offset))
            sessions = []
            for row in cursor.fetchall():
                result = dict(row)
                if result.get("context_data"):
                    result["context_data"] = json.loads(result["context_data"])
                sessions.append(result)
            paginated = {
                "sessions": sessions,
                "total": total,
                "page": page,
                "page_size": page_size
            }
            set_cached(CacheKeys.USER_SESSIONS, user_id, tid, str(page), str(page_size),
                       value=paginated, ttl=30)
            return paginated

    @staticmethod
    def update_title(session_id: str, title: str) -> bool:
        """更新会话标题"""
        placeholder = "%s"
        ts = get_current_timestamp()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE chat_sessions
                SET title = {placeholder}, updated_at = {ts}
                WHERE session_id = {placeholder}
            """, (title, session_id))
            conn.commit()
            result = cursor.rowcount > 0
            if result:
                delete_cached(CacheKeys.SESSION, session_id)
            return result

    @staticmethod
    def update_context(session_id: str, context_data: dict) -> bool:
        """更新会话上下文数据"""
        placeholder = "%s"
        ts = get_current_timestamp()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE chat_sessions
                SET context_data = {placeholder}, updated_at = {ts}
                WHERE session_id = {placeholder}
            """, (json.dumps(context_data), session_id))
            conn.commit()
            result = cursor.rowcount > 0
            if result:
                delete_cached(CacheKeys.SESSION, session_id)
            return result

    @staticmethod
    def update_subagent_id(session_id: str, subagent_id: Optional[str]) -> bool:
        """更新会话关联的数字员工ID"""
        placeholder = "%s"
        ts = get_current_timestamp()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE chat_sessions
                SET subagent_id = {placeholder}, updated_at = {ts}
                WHERE session_id = {placeholder}
            """, (subagent_id, session_id))
            conn.commit()
            result = cursor.rowcount > 0
            if result:
                delete_cached(CacheKeys.SESSION, session_id)
            return result

    @staticmethod
    def touch(session_id: str) -> bool:
        """更新会话时间戳（仅更新 updated_at，不失效会话缓存，避免频繁查库）"""
        placeholder = "%s"
        ts = get_current_timestamp()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE chat_sessions SET updated_at = {ts} WHERE session_id = {placeholder}",
                          (session_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete(session_id: str) -> bool:
        """删除会话及其所有消息和记录"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 先删除子表记录（按外键依赖顺序）
                cursor.execute(f"DELETE FROM chat_records WHERE session_id = {placeholder}", (session_id,))
                cursor.execute(f"DELETE FROM chat_messages WHERE session_id = {placeholder}", (session_id,))
                # 最后删除主表
                cursor.execute(f"DELETE FROM chat_sessions WHERE session_id = {placeholder}", (session_id,))
                conn.commit()
                logger.info(f"Chat session deleted: {session_id}")
                # 获取 user_id 用于清除会话列表缓存
                cursor.execute(f"SELECT user_id FROM chat_sessions WHERE session_id = {placeholder}", (session_id,))
                session_row = cursor.fetchone()
                # 清除会话和消息缓存
                delete_cached(CacheKeys.SESSION, session_id)
                delete_cached_pattern(CacheKeys.SESSION_MSGS, session_id, "")
                if session_row:
                    delete_cached_pattern(CacheKeys.USER_SESSIONS, session_row["user_id"], "")
                return True
            except Exception as e:
                logger.error(f"Failed to delete chat session: {e}")
                return False


class MessageDB:
    """消息数据库访问类 - 操作 chat_messages 表

    与 ChatRecordDB 的区别：
    - 存储粒度：单条消息（最小单元） vs 完整对话交互（用户输入+助手回复）
    - 使用场景：消息展示和上下文构建 vs 用量统计、计费、审计、性能监控
    - 数据结构：简单的 role/content/metadata vs 包含token统计、执行详情、状态等完整信息

    两个表存在内容冗余但设计合理，服务于不同的业务目的。
    """

    @staticmethod
    def create(session_id: str, role: str, content: str,
               metadata: dict = None) -> Optional[Dict[str, Any]]:
        """创建新消息"""
        message_id = generate_message_id()
        placeholder = "%s"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(f"""
                    INSERT INTO chat_messages (message_id, session_id, role, content, metadata)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                """, (message_id, session_id, role, content,
                      json.dumps(metadata) if metadata else None))
                conn.commit()

                SessionDB.touch(session_id)
                # 清除消息缓存（新消息追加后缓存失效）
                delete_cached_pattern(CacheKeys.SESSION_MSGS, session_id, "")
                return MessageDB.get_by_id(message_id)
            except Exception as e:
                logger.error(f"Failed to create chat message: {e}")
                return None

    @staticmethod
    def get_by_id(message_id: str) -> Optional[Dict[str, Any]]:
        """根据消息ID获取消息"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM chat_messages WHERE message_id = {placeholder}", (message_id,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                if result.get("metadata"):
                    result["metadata"] = json.loads(result["metadata"])
                return result
            return None

    @staticmethod
    def list_by_session(
        session_id: str,
        limit: int = 100,
        roles: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """获取会话的所有消息（优先从 Redis 缓存读取，TTL 60秒）

        Args:
            session_id: 会话 ID
            limit: 最大返回数量
            roles: 可选，只返回指定角色的消息（如 ["user", "assistant"]）
        """
        # 生成缓存 key（角色不同会影响结果）
        roles_str = "_".join(sorted(roles)) if roles else "all"
        cached = get_cached(CacheKeys.SESSION_MSGS, session_id, str(limit), roles_str)
        if cached is not None:
            return cached

        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if roles:
                role_placeholders = ",".join([placeholder] * len(roles))
                cursor.execute(f"""
                    SELECT * FROM chat_messages
                    WHERE session_id = {placeholder} AND role IN ({role_placeholders})
                    ORDER BY created_at ASC
                    LIMIT {placeholder}
                """, (session_id, *roles, limit))
            else:
                cursor.execute(f"""
                    SELECT * FROM chat_messages
                    WHERE session_id = {placeholder}
                    ORDER BY created_at ASC
                    LIMIT {placeholder}
                """, (session_id, limit))
            messages = []
            for row in cursor.fetchall():
                result = dict(row)
                if result.get("metadata"):
                    metadata = json.loads(result["metadata"])
                    # 将 progressMessages 中的 data 字段转换为 content 字段（与前端和实时数据格式一致）
                    if metadata.get("progressMessages") and isinstance(metadata["progressMessages"], list):
                        for pm in metadata["progressMessages"]:
                            if "data" in pm:
                                pm["content"] = pm.pop("data")
                    result["metadata"] = metadata
                messages.append(result)

            # 写入缓存（TTL 60 秒，消息变化频率较高）
            set_cached(CacheKeys.SESSION_MSGS, session_id, str(limit), roles_str,
                       value=messages, ttl=60)
            return messages

    @staticmethod
    def delete(message_id: str) -> bool:
        """删除消息"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM chat_messages WHERE message_id = {placeholder}", (message_id,))
            conn.commit()
            return cursor.rowcount > 0


# ============== 会话记录数据库访问 ==============

def generate_record_id() -> str:
    """生成唯一记录ID"""
    return f"rec_{uuid.uuid4().hex[:12]}"


class ChatRecordDB:
    """会话记录数据库访问类 - 操作 chat_records 表，记录每次和AI的对话

    与 MessageDB 的区别：
    - 存储粒度：完整对话交互（用户输入+助手回复） vs 单条消息（最小单元）
    - 使用场景：用量统计、计费、审计、性能监控 vs 消息展示和上下文构建
    - 数据结构：包含token统计、执行详情、状态等完整信息 vs 简单的 role/content/metadata

    两个表存在内容冗余但设计合理，服务于不同的业务目的。
    """

    @staticmethod
    def create(
        session_id: str,
        tenant_id: str = None,
        user_id: str = None,
        user_message: str = None,
        assistant_message: str = None,
        total_token_count: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_input_tokens: int = 0,
        model: str = None,
        provider: str = None,
        execution_details: dict = None,
        agent_iterations: int = 0,
        subagent_calls: list = None,
        status: str = "completed",
        error_message: str = None,
        duration_ms: int = 0,
        source_type: str = "chat"
    ) -> Optional[Dict[str, Any]]:
        """创建新的会话记录"""
        record_id = generate_record_id()
        placeholder = "%s"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(f"""
                    INSERT INTO chat_records
                    (record_id, session_id, tenant_id, user_id, user_message, assistant_message,
                     total_token_count, prompt_tokens, completion_tokens, cached_input_tokens,
                     model, provider, execution_details, agent_iterations, subagent_calls,
                     status, error_message, duration_ms, source_type)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                            {placeholder}, {placeholder}, {placeholder}, {placeholder},
                            {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                            {placeholder}, {placeholder}, {placeholder}, {placeholder})
                """, (
                    record_id, session_id, tenant_id, user_id, user_message, assistant_message,
                    total_token_count, prompt_tokens, completion_tokens, cached_input_tokens,
                    model, provider,
                    json.dumps(execution_details) if execution_details else None,
                    agent_iterations,
                    json.dumps(subagent_calls) if subagent_calls else None,
                    status, error_message, duration_ms, source_type
                ))
                conn.commit()

                logger.info(f"Chat record created: {record_id} for session: {session_id}")
                return ChatRecordDB.get_by_id(record_id)
            except Exception as e:
                logger.error(f"Failed to create chat record: {e}")
                return None

    @staticmethod
    def get_by_id(record_id: str) -> Optional[Dict[str, Any]]:
        """根据记录ID获取会话记录"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM chat_records WHERE record_id = {placeholder}", (record_id,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                if result.get("execution_details"):
                    result["execution_details"] = json.loads(result["execution_details"])
                return result
            return None

    @staticmethod
    def list_by_session(session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取会话的所有记录"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT * FROM chat_records
                WHERE session_id = {placeholder}
                ORDER BY created_at ASC
                LIMIT {placeholder}
            """, (session_id, limit))
            records = []
            for row in cursor.fetchall():
                result = dict(row)
                if result.get("execution_details"):
                    result["execution_details"] = json.loads(result["execution_details"])
                records.append(result)
            return records

    @staticmethod
    def list_by_user(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """获取用户的所有会话记录"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT * FROM chat_records
                WHERE user_id = {placeholder}
                ORDER BY created_at DESC
                LIMIT {placeholder}
            """, (user_id, limit))
            records = []
            for row in cursor.fetchall():
                result = dict(row)
                if result.get("execution_details"):
                    result["execution_details"] = json.loads(result["execution_details"])
                records.append(result)
            return records

    @staticmethod
    def get_total_token_by_session(session_id: str) -> Dict[str, int]:
        """获取会话的总token消耗"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT
                    COALESCE(SUM(total_token_count), 0) as total,
                    COALESCE(SUM(prompt_tokens), 0) as prompt,
                    COALESCE(SUM(completion_tokens), 0) as completion,
                    COALESCE(SUM(cached_input_tokens), 0) as cached
                FROM chat_records
                WHERE session_id = {placeholder}
            """, (session_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "total_tokens": row["total"],
                    "prompt_tokens": row["prompt"],
                    "completion_tokens": row["completion"],
                    "cached_input_tokens": row["cached"]
                }
            return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_input_tokens": 0}

    @staticmethod
    def delete(record_id: str) -> bool:
        """删除会话记录"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM chat_records WHERE record_id = {placeholder}", (record_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete_by_session(session_id: str) -> bool:
        """删除会话的所有记录"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM chat_records WHERE session_id = {placeholder}", (session_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def get_token_usage_by_tenant(
        tenant_id: str,
        start_date: str = None,
        end_date: str = None,
        group_by: str = "day"
    ) -> List[Dict[str, Any]]:
        """
        按租户统计 token 用量（优先从 Redis 缓存读取，TTL 10分钟）

        Args:
            tenant_id: 租户ID
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            group_by: 分组维度 (day/model/user)
        """
        sd = start_date or "none"
        ed = end_date or "none"
        cached = get_cached(CacheKeys.TOKEN_USAGE, tenant_id, sd, ed, group_by)
        if cached is not None:
            return cached

        where_clauses = ["tenant_id = %s"]
        params: list = [tenant_id]

        if start_date:
            where_clauses.append("created_at >= %s")
            params.append(start_date)
        if end_date:
            where_clauses.append("created_at < %s")
            params.append(end_date + " 23:59:59")

        where_sql = " AND ".join(where_clauses)

        if group_by == "model":
            group_expr = "model"
            select_expr = "model"
        elif group_by == "user":
            group_expr = "user_id"
            select_expr = "user_id"
        else:
            group_expr = "DATE(created_at)"
            select_expr = "DATE(created_at) as date"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT
                    {select_expr},
                    COUNT(*) as conversation_count,
                    COALESCE(SUM(total_token_count), 0) as total_tokens,
                    COALESCE(SUM(prompt_tokens), 0) as input_tokens,
                    COALESCE(SUM(completion_tokens), 0) as output_tokens,
                    COALESCE(SUM(cached_input_tokens), 0) as cached_tokens,
                    COALESCE(SUM(duration_ms), 0) as total_duration_ms
                FROM chat_records
                WHERE {where_sql}
                GROUP BY {group_expr}
                ORDER BY {group_expr} DESC
            """, params)
            columns = [desc[0] for desc in cursor.description]
            result = [dict(zip(columns, row)) for row in cursor.fetchall()]
            set_cached(CacheKeys.TOKEN_USAGE, tenant_id, sd, ed, group_by, value=result, ttl=600)
            return result

    @staticmethod
    def parse_month_range(month_str: str) -> tuple[str, str]:
        """
        将YYYY-MM格式的月份字符串转换为时间范围

        Args:
            month_str: 月份字符串，如 "2026-05"

        Returns:
            (start_date, end_date): 格式为 "YYYY-MM-DD HH:MM:SS"
        """
        from datetime import datetime, timedelta

        try:
            # 解析月份
            year_month = datetime.strptime(month_str, "%Y-%m")

            # 计算月份的开始和结束
            start_date = year_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if year_month.month == 12:
                end_date = year_month.replace(year=year_month.year + 1, month=1, day=1)
            else:
                end_date = year_month.replace(month=year_month.month + 1, day=1)

            # 减去1秒得到当月的最后一刻
            end_date = end_date - timedelta(seconds=1)

            return (
                start_date.strftime("%Y-%m-%d %H:%M:%S"),
                end_date.strftime("%Y-%m-%d %H:%M:%S")
            )
        except ValueError as e:
            raise ValueError(f"无效的月份格式: {month_str}, 请使用 YYYY-MM 格式")

    @staticmethod
    def get_platform_token_usage(month_str: str) -> Dict[str, Any]:
        """
        获取平台Token消耗汇总报表（所有租户按月统计，优先从 Redis 缓存读取，TTL 1小时）

        Args:
            month_str: 月份字符串，格式 YYYY-MM

        Returns:
            包含汇总信息和租户列表的字典
        """
        # 按月缓存，月度数据不变
        cached = get_cached(CacheKeys.PLATFORM_USAGE, month_str)
        if cached is not None:
            return cached

        start_date, end_date = ChatRecordDB.parse_month_range(month_str)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 按租户分组统计，LEFT JOIN 成本价表计算费用，排除测试数据
            # 同时用子查询标记有未配单价的租户
            cursor.execute("""
                SELECT
                    cr.tenant_id,
                    COUNT(*) as conversation_count,
                    COALESCE(SUM(cr.prompt_tokens), 0) as input_tokens,
                    COALESCE(SUM(cr.completion_tokens), 0) as output_tokens,
                    COALESCE(SUM(cr.prompt_tokens * tcp.input_price_per_m / 1000000), 0) as input_cost,
                    COALESCE(SUM(cr.completion_tokens * tcp.output_price_per_m / 1000000), 0) as output_cost,
                    EXISTS(
                        SELECT 1 FROM chat_records cr2
                        LEFT JOIN token_cost_prices tcp2 ON cr2.model = tcp2.model_name
                        WHERE cr2.tenant_id = cr.tenant_id
                          AND cr2.created_at >= %s AND cr2.created_at <= %s
                          AND NOT (cr2.prompt_tokens = 0 AND cr2.completion_tokens = 0)
                          AND tcp2.model_name IS NULL
                          AND cr2.model IS NOT NULL
                    ) as has_unpriced_tokens
                FROM chat_records cr
                LEFT JOIN token_cost_prices tcp ON cr.model = tcp.model_name
                WHERE cr.created_at >= %s AND cr.created_at <= %s
                  AND NOT (cr.prompt_tokens = 0 AND cr.completion_tokens = 0)
                GROUP BY cr.tenant_id
                ORDER BY conversation_count DESC
            """, (start_date, end_date, start_date, end_date))
            rows = cursor.fetchall()

            tenant_data = []
            total_input_tokens = 0
            total_output_tokens = 0
            total_conversations = 0
            total_input_cost = 0.0
            total_output_cost = 0.0
            has_unpriced = False

            for row in rows:
                if row["conversation_count"] > 0:
                    input_cost = float(row["input_cost"]) if row["input_cost"] else 0.0
                    output_cost = float(row["output_cost"]) if row["output_cost"] else 0.0
                    tenant_unpriced = bool(row["has_unpriced_tokens"])
                    tenant_data.append({
                        "tenant_id": row["tenant_id"],
                        "input_tokens": row["input_tokens"],
                        "output_tokens": row["output_tokens"],
                        "conversation_count": row["conversation_count"],
                        "input_cost": input_cost,
                        "output_cost": output_cost,
                        "total_cost": round(input_cost + output_cost, 2),
                        "has_unpriced_tokens": tenant_unpriced
                    })
                    total_input_tokens += row["input_tokens"]
                    total_output_tokens += row["output_tokens"]
                    total_conversations += row["conversation_count"]
                    total_input_cost += input_cost
                    total_output_cost += output_cost
                    if tenant_unpriced:
                        has_unpriced = True

            result = {
                "month": month_str,
                "summary": {
                    "total_input_tokens": total_input_tokens,
                    "total_output_tokens": total_output_tokens,
                    "total_conversations": total_conversations,
                    "tenant_count": len(tenant_data),
                    "total_input_cost": round(total_input_cost, 2),
                    "total_output_cost": round(total_output_cost, 2),
                    "total_cost": round(total_input_cost + total_output_cost, 2),
                    "has_unpriced_tokens": has_unpriced
                },
                "data": tenant_data
            }
            set_cached(CacheKeys.PLATFORM_USAGE, month_str, value=result, ttl=3600)
            return result

    @staticmethod
    def get_tenant_token_details(tenant_id: str, month_str: str, page: int = 1, page_size: int = 100) -> Dict[str, Any]:
        """
        获取租户Token消耗明细（分页，优先从 Redis 缓存读取，TTL 10分钟）

        Args:
            tenant_id: 租户ID
            month_str: 月份字符串，格式 YYYY-MM
            page: 页码，从1开始
            page_size: 每页记录数

        Returns:
            包含汇总信息和分页明细的字典
        """
        # 优先从缓存获取
        cached = get_cached(CacheKeys.TENANT_USAGE, tenant_id, month_str, str(page), str(page_size))
        if cached is not None:
            return cached

        start_date, end_date = ChatRecordDB.parse_month_range(month_str)
        offset = (page - 1) * page_size

        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 查询明细数据
            cursor.execute("""
                SELECT
                    cr.record_id,
                    cr.user_id,
                    u.username,
                    cr.user_message,
                    cr.prompt_tokens as input_tokens,
                    cr.completion_tokens as output_tokens,
                    cr.created_at
                FROM chat_records cr
                LEFT JOIN users u ON cr.user_id = u.user_id
                WHERE cr.tenant_id = %s
                  AND cr.created_at >= %s AND cr.created_at <= %s
                  AND NOT (cr.prompt_tokens = 0 AND cr.completion_tokens = 0)
                ORDER BY cr.created_at DESC
                LIMIT %s OFFSET %s
            """, (tenant_id, start_date, end_date, page_size, offset))

            rows = cursor.fetchall()
            details = []
            for row in rows:
                details.append({
                    "record_id": row["record_id"],
                    "user_id": row["user_id"] or "",
                    "username": row["username"] or "",
                    "user_message": row["user_message"] or "",
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"],
                    "created_at": row["created_at"].strftime("%Y-%m-%d %H:%M:%S") if row["created_at"] else ""
                })

            # 查询月度汇总
            cursor.execute("""
                SELECT
                    COUNT(*) as total_conversations,
                    COALESCE(SUM(prompt_tokens), 0) as total_input_tokens,
                    COALESCE(SUM(completion_tokens), 0) as total_output_tokens
                FROM chat_records
                WHERE tenant_id = %s
                  AND created_at >= %s AND created_at <= %s
                  AND NOT (prompt_tokens = 0 AND completion_tokens = 0)
            """, (tenant_id, start_date, end_date))

            summary_row = cursor.fetchone()
            total_conversations = summary_row["total_conversations"] if summary_row else 0
            total_input_tokens = summary_row["total_input_tokens"] if summary_row else 0
            total_output_tokens = summary_row["total_output_tokens"] if summary_row else 0

            # 查询总记录数用于分页
            cursor.execute("""
                SELECT COUNT(*) as total_count
                FROM chat_records
                WHERE tenant_id = %s
                  AND created_at >= %s AND created_at <= %s
                  AND NOT (prompt_tokens = 0 AND completion_tokens = 0)
            """, (tenant_id, start_date, end_date))

            total_count_row = cursor.fetchone()
            total_count = total_count_row["total_count"] if total_count_row else 0

            result = {
                "month": month_str,
                "tenant_id": tenant_id,
                "summary": {
                    "total_input_tokens": total_input_tokens,
                    "total_output_tokens": total_output_tokens,
                    "total_conversations": total_conversations
                },
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_count": total_count,
                    "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 0
                },
                "data": details
            }
            set_cached(CacheKeys.TENANT_USAGE, tenant_id, month_str, str(page), str(page_size),
                       value=result, ttl=600)
            return result


# ============== 短信验证码 ==============

def send_sms_code(phone: str) -> bool:
    """
    发送短信验证码

    使用配置的短信通道真实发送短信。
    演示模式下使用固定验证码 888888。
    """
    from src.sms.manager import sms_manager

    # 演示模式：不实际发送，验证码固定为 888888
    if settings.demo.enabled:
        code = "888888"
        logger.info(f"演示模式，手机号 {phone} 使用固定验证码 888888")
    else:
        # 生成6位验证码
        code = str(random.randint(100000, 999999))
        logger.info(f"发送验证码到 {phone}，验证码: {code}")

        # 检查短信通道是否可用
        sender = sms_manager.get_sender()
        if sender is None or not sender.is_available():
            logger.error("短信通道未配置或不可用，无法发送验证码")
            return False

        # 调用短信通道发送
        result = sms_manager.send(phone, template_params={"code": code})
        if result is None:
            logger.error(f"发送验证码失败: 无可用通道")
            return False

        code_result = result.get("code")
        if code_result != 200:
            logger.error(f"验证码发送失败: phone={phone}, code={code_result}, msg={result.get('msg')}")
            return False

        logger.info(f"验证码发送成功: phone={phone}, result={result}")

    # 保存验证码到数据库
    placeholder = "%s"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE sms_codes SET used = 1 WHERE phone = {placeholder}", (phone,))

        expires_at = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(f"""
            INSERT INTO sms_codes (phone, code, expires_at)
            VALUES ({placeholder}, {placeholder}, {placeholder})
        """, (phone, code, expires_at))
        conn.commit()

    return True


def verify_sms_code(phone: str, code: str) -> bool:
    """验证短信验证码"""

    # 如果 code 等于 qb_sms_code 配置的值，也返回 True（用于测试）
    if settings.sms.qb_sms_code and code == settings.sms.qb_sms_code:
        logger.info(f"后端日志：QBSMSCODE bypass 验证成功，phone={phone}, code={code}")
        return True

    placeholder = "%s"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT * FROM sms_codes
            WHERE phone = {placeholder} AND code = {placeholder} AND used = 0
            AND expires_at > {placeholder}
            ORDER BY created_at DESC LIMIT 1
        """, (phone, code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        row = cursor.fetchone()

        if row:
            cursor.execute(f"UPDATE sms_codes SET used = 1 WHERE phone = {placeholder} AND code = {placeholder}",
                         (phone, code))
            conn.commit()
            return True
        return False


# ============== 图形验证码 ==============

import string
import uuid


def generate_captcha_code(length: int = 4) -> str:
    """生成图形验证码（去除易混淆字符 O,0,I,1）"""
    chars = string.digits + string.ascii_uppercase + string.ascii_lowercase
    # 去除易混淆字符
    exclude_chars = {'O', '0', 'I', '1', 'o', 'l'}
    chars = ''.join(c for c in chars if c not in exclude_chars)
    return ''.join(random.choice(chars) for _ in range(length))


def _generate_captcha_svg(code: str) -> str:
    """生成图形验证码 SVG 图片"""
    width, height = 120, 40
    # 随机颜色
    def rand_color(start=50, end=180):
        r = random.randint(start, end)
        g = random.randint(start, end)
        b = random.randint(start, end)
        return f"rgb({r},{g},{b})"

    # 干扰线
    lines = ""
    for _ in range(5):
        x1, y1 = random.randint(0, width), random.randint(0, height)
        x2, y2 = random.randint(0, width), random.randint(0, height)
        lines += f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{rand_color(100, 200)}" stroke-width="1"/>'

    # 干扰点
    dots = ""
    for _ in range(30):
        x, y = random.randint(0, width), random.randint(0, height)
        dots += f'<circle cx="{x}" cy="{y}" r="1" fill="{rand_color(100, 200)}"/>'

    # 文字
    chars = ""
    for i, ch in enumerate(code):
        x = 15 + i * 25
        y = 28 + random.randint(-5, 5)
        angle = random.randint(-15, 15)
        color = rand_color(10, 80)
        chars += f'<text x="{x}" y="{y}" fill="{color}" font-size="24" font-weight="bold" transform="rotate({angle},{x},{y})">{ch}</text>'

    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><rect width="{width}" height="{height}" fill="#f0f0f0"/>{lines}{dots}{chars}</svg>'
    return svg


def generate_captcha() -> dict:
    """
    生成图形验证码
    返回 captcha_id 和 svg 图片（base64），不再返回明文 code
    """
    captcha_id = str(uuid.uuid4())
    code = generate_captcha_code(4)
    expires_at = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
    placeholder = "%s"

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 清理过期的验证码
        cursor.execute(f"DELETE FROM captchas WHERE expires_at < {placeholder}",
                      (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        # 插入新验证码
        cursor.execute(f"""
            INSERT INTO captchas (captcha_id, code, expires_at)
            VALUES ({placeholder}, {placeholder}, {placeholder})
        """, (captcha_id, code, expires_at))
        conn.commit()

    svg = _generate_captcha_svg(code)
    import base64
    svg_b64 = base64.b64encode(svg.encode("utf-8")).decode("utf-8")

    return {"captcha_id": captcha_id, "svg_base64": svg_b64}


def verify_captcha(captcha_id: str, code: str) -> bool:
    """
    验证图形验证码
    验证成功后删除验证码（一次性）
    """
    placeholder = "%s"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT * FROM captchas
            WHERE captcha_id = {placeholder} AND LOWER(code) = {placeholder}
            AND expires_at > {placeholder}
        """, (captcha_id, code.lower(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        row = cursor.fetchone()

        if row:
            # 验证成功后删除验证码（一次性）
            cursor.execute(f"DELETE FROM captchas WHERE captcha_id = {placeholder}", (captcha_id,))
            conn.commit()
            return True
        return False


def get_captcha_image(captcha_id: str) -> Optional[str]:
    """获取验证码对应的SVG图片（可选，用于直接返回图片）"""
    # 如果需要返回图片而非纯文本，可以在这里生成SVG
    # 当前实现返回纯文本code，由前端生成图片
    placeholder = "%s"
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT code FROM captchas
            WHERE captcha_id = {placeholder}
            AND expires_at > {placeholder}
        """, (captcha_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        row = cursor.fetchone()
        if row:
            return row["code"]
    return None


# ============== Token 数据库访问 ==============

class TokenDB:
    """Token 数据库访问类"""

    @staticmethod
    def delete_by_tenant(tenant_id: str) -> int:
        """删除指定租户下所有用户的 token，返回删除数量"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM tokens
                WHERE user_id IN (SELECT user_id FROM users WHERE tenant_id = %s)
            """, (tenant_id,))
            conn.commit()
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"Deleted {deleted} tokens for tenant {tenant_id}")
            return deleted

    @staticmethod
    def update_expires_by_tenant(tenant_id: str, new_expire_at: datetime) -> int:
        """
        将指定租户下所有用户 token 的过期时间提前到 new_expire_at。
        只更新那些 expires_at 比 new_expire_at 更晚的 token。
        返回更新数量。
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE tokens
                SET expires_at = %s
                WHERE user_id IN (SELECT user_id FROM users WHERE tenant_id = %s)
                  AND expires_at > %s
            """, (new_expire_at, tenant_id, new_expire_at))
            conn.commit()
            updated = cursor.rowcount
            if updated > 0:
                logger.info(f"Updated {updated} token expires_at for tenant {tenant_id} to {new_expire_at}")
            return updated
