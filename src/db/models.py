"""
数据库访问模型

提供用户和会话的数据库CRUD操作
"""

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from loguru import logger

from src.db.database import get_db_connection, get_db_placeholder, get_current_timestamp, DB_TYPE


# ============== 密码哈希 ==============

def hash_password(password: str) -> str:
    """简单密码哈希（生产环境应使用bcrypt）"""
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码"""
    return hash_password(password) == password_hash


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
              role: str = "user", tenant_id: str = None) -> Optional[Dict[str, Any]]:
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
        placeholder = get_db_placeholder()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(f"""
                    INSERT INTO users (user_id, phone, password_hash, wx_openid, username, role, tenant_id)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                """, (user_id, phone, hash_password(password) if password else None,
                      wx_openid, username or (f"用户{phone[-4:]}" if phone else f"用户{user_id[-4:]}"),
                      role, tenant_id))
                conn.commit()

                logger.info(f"User created: {user_id} with role {role}")
                return UserDB.get_by_id(user_id)
            except Exception as e:
                logger.error(f"Failed to create user: {e}")
                return None

    @staticmethod
    def get_by_id(user_id: str) -> Optional[Dict[str, Any]]:
        """根据用户ID获取用户"""
        placeholder = get_db_placeholder()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM users WHERE user_id = {placeholder}", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_phone(phone: str) -> Optional[Dict[str, Any]]:
        """根据手机号获取用户"""
        placeholder = get_db_placeholder()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM users WHERE phone = {placeholder}", (phone,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_wx_openid(openid: str) -> Optional[Dict[str, Any]]:
        """根据微信openid获取用户"""
        placeholder = get_db_placeholder()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM users WHERE wx_openid = {placeholder}", (openid,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def verify_login(phone: str, password: str) -> Optional[Dict[str, Any]]:
        """验证手机号密码登录"""
        user = UserDB.get_by_phone(phone)
        if user and user.get("password_hash") == hash_password(password):
            return user
        return None

    @staticmethod
    def update_wx_openid(user_id: str, wx_openid: str) -> bool:
        """绑定微信openid"""
        placeholder = get_db_placeholder()
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
        allowed_fields = ["username", "avatar_url", "role", "tenant_id"]
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return False

        placeholder = get_db_placeholder()
        set_clause = ", ".join([f"{k} = {placeholder}" for k in updates.keys()])
        ts = get_current_timestamp()
        values = list(updates.values()) + [user_id]

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE users SET {set_clause}, updated_at = {ts} WHERE user_id = {placeholder}",
                          values)
            conn.commit()
            return cursor.rowcount > 0

    # 别名方法，保持向后兼容
    update = update_info

    @staticmethod
    def list_users() -> List[Dict[str, Any]]:
        """获取所有用户（管理用）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def list_by_tenant(tenant_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取指定租户下的所有用户

        Args:
            tenant_id: 租户ID
            limit: 返回数量限制
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM users
                WHERE tenant_id = ? AND status = 1
                ORDER BY created_at DESC
                LIMIT ?
            """, (tenant_id, limit))
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def list_admins(tenant_id: str = None) -> List[Dict[str, Any]]:
        """获取管理员列表

        Args:
            tenant_id: 如果指定则列出该租户的管理员，否则列出所有平台管理员
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id:
                cursor.execute("""
                    SELECT * FROM users
                    WHERE role IN ('platform_admin', 'tenant_admin')
                    AND tenant_id = ? AND status = 1
                    ORDER BY created_at
                """, (tenant_id,))
            else:
                cursor.execute("""
                    SELECT * FROM users
                    WHERE role = 'platform_admin' AND status = 1
                    ORDER BY created_at
                """)
            return [dict(row) for row in cursor.fetchall()]


# ============== 会话数据库访问 ==============

class SessionDB:
    """会话数据库访问类"""

    @staticmethod
    def create(user_id: str, title: str = None, context_data: dict = None) -> Optional[Dict[str, Any]]:
        """创建新会话"""
        session_id = generate_session_id()
        placeholder = get_db_placeholder()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(f"""
                    INSERT INTO chat_sessions (session_id, user_id, title, context_data)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder})
                """, (session_id, user_id, title or "新会话",
                      json.dumps(context_data) if context_data else None))
                conn.commit()

                logger.info(f"Chat session created: {session_id} for user: {user_id}")
                return SessionDB.get_by_id(session_id)
            except Exception as e:
                logger.error(f"Failed to create chat session: {e}")
                return None

    @staticmethod
    def get_by_id(session_id: str) -> Optional[Dict[str, Any]]:
        """根据会话ID获取会话"""
        placeholder = get_db_placeholder()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM chat_sessions WHERE session_id = {placeholder}", (session_id,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                if result.get("context_data"):
                    result["context_data"] = json.loads(result["context_data"])
                return result
            return None

    @staticmethod
    def list_by_user(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """获取用户的所有会话"""
        placeholder = get_db_placeholder()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT * FROM chat_sessions
                WHERE user_id = {placeholder}
                ORDER BY updated_at DESC
                LIMIT {placeholder}
            """, (user_id, limit))
            sessions = []
            for row in cursor.fetchall():
                result = dict(row)
                if result.get("context_data"):
                    result["context_data"] = json.loads(result["context_data"])
                sessions.append(result)
            return sessions

    @staticmethod
    def update_title(session_id: str, title: str) -> bool:
        """更新会话标题"""
        placeholder = get_db_placeholder()
        ts = get_current_timestamp()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE chat_sessions
                SET title = {placeholder}, updated_at = {ts}
                WHERE session_id = {placeholder}
            """, (title, session_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_context(session_id: str, context_data: dict) -> bool:
        """更新会话上下文数据"""
        placeholder = get_db_placeholder()
        ts = get_current_timestamp()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                UPDATE chat_sessions
                SET context_data = {placeholder}, updated_at = {ts}
                WHERE session_id = {placeholder}
            """, (json.dumps(context_data), session_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def touch(session_id: str) -> bool:
        """更新会话时间戳"""
        placeholder = get_db_placeholder()
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
        placeholder = get_db_placeholder()
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
                return True
            except Exception as e:
                logger.error(f"Failed to delete chat session: {e}")
                return False


class MessageDB:
    """消息数据库访问类"""

    @staticmethod
    def create(session_id: str, role: str, content: str,
               metadata: dict = None) -> Optional[Dict[str, Any]]:
        """创建新消息"""
        message_id = generate_message_id()
        placeholder = get_db_placeholder()

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
                return MessageDB.get_by_id(message_id)
            except Exception as e:
                logger.error(f"Failed to create chat message: {e}")
                return None

    @staticmethod
    def get_by_id(message_id: str) -> Optional[Dict[str, Any]]:
        """根据消息ID获取消息"""
        placeholder = get_db_placeholder()
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
    def list_by_session(session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """获取会话的所有消息"""
        placeholder = get_db_placeholder()
        with get_db_connection() as conn:
            cursor = conn.cursor()
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
            return messages

    @staticmethod
    def delete(message_id: str) -> bool:
        """删除消息"""
        placeholder = get_db_placeholder()
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
    """会话记录数据库访问类 - 记录每次和AI的对话"""

    @staticmethod
    def create(
        session_id: str,
        user_id: str,
        user_message: str,
        assistant_message: str = None,
        total_token_count: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        model: str = None,
        execution_details: dict = None,
        status: str = "completed",
        error_message: str = None,
        duration_ms: int = 0
    ) -> Optional[Dict[str, Any]]:
        """创建新的会话记录"""
        record_id = generate_record_id()
        placeholder = get_db_placeholder()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(f"""
                    INSERT INTO chat_records
                    (record_id, session_id, user_id, user_message, assistant_message,
                     total_token_count, prompt_tokens, completion_tokens, model,
                     execution_details, status, error_message, duration_ms)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                            {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                            {placeholder}, {placeholder}, {placeholder})
                """, (
                    record_id, session_id, user_id, user_message, assistant_message,
                    total_token_count, prompt_tokens, completion_tokens, model,
                    json.dumps(execution_details) if execution_details else None,
                    status, error_message, duration_ms
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
        placeholder = get_db_placeholder()
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
        placeholder = get_db_placeholder()
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
        placeholder = get_db_placeholder()
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
        placeholder = get_db_placeholder()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT
                    COALESCE(SUM(total_token_count), 0) as total,
                    COALESCE(SUM(prompt_tokens), 0) as prompt,
                    COALESCE(SUM(completion_tokens), 0) as completion
                FROM chat_records
                WHERE session_id = {placeholder}
            """, (session_id,))
            row = cursor.fetchone()
            if row:
                return {
                    "total_tokens": row["total"],
                    "prompt_tokens": row["prompt"],
                    "completion_tokens": row["completion"]
                }
            return {"total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0}

    @staticmethod
    def delete(record_id: str) -> bool:
        """删除会话记录"""
        placeholder = get_db_placeholder()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM chat_records WHERE record_id = {placeholder}", (record_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete_by_session(session_id: str) -> bool:
        """删除会话的所有记录"""
        placeholder = get_db_placeholder()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"DELETE FROM chat_records WHERE session_id = {placeholder}", (session_id,))
            conn.commit()
            return cursor.rowcount > 0


# ============== 短信验证码 ==============

def send_sms_code(phone: str) -> bool:
    """
    发送短信验证码
    当前为Mock实现，固定验证码888888
    """
    code = "888888"
    placeholder = get_db_placeholder()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE sms_codes SET used = 1 WHERE phone = {placeholder}", (phone,))

        expires_at = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(f"""
            INSERT INTO sms_codes (phone, code, expires_at)
            VALUES ({placeholder}, {placeholder}, {placeholder})
        """, (phone, code, expires_at))
        conn.commit()

    logger.info(f"[MOCK SMS] 验证码 {code} 已发送到 {phone}")
    return True


def verify_sms_code(phone: str, code: str) -> bool:
    """验证短信验证码"""
    placeholder = get_db_placeholder()
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
