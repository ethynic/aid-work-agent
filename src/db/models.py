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

from src.db.database import get_db_connection


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
              wx_openid: str = None, username: str = None) -> Optional[Dict[str, Any]]:
        """创建新用户"""
        user_id = generate_user_id()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO users (user_id, phone, password_hash, wx_openid, username)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, phone, hash_password(password) if password else None,
                      wx_openid, username or f"用户{user_id[-4:]}"))
                conn.commit()

                logger.info(f"User created: {user_id}")
                return UserDB.get_by_id(user_id)
            except Exception as e:
                logger.error(f"Failed to create user: {e}")
                return None

    @staticmethod
    def get_by_id(user_id: str) -> Optional[Dict[str, Any]]:
        """根据用户ID获取用户"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_phone(phone: str) -> Optional[Dict[str, Any]]:
        """根据手机号获取用户"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE phone = ?", (phone,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_wx_openid(openid: str) -> Optional[Dict[str, Any]]:
        """根据微信openid获取用户"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE wx_openid = ?", (openid,))
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET wx_openid = ? WHERE user_id = ?",
                          (wx_openid, user_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_info(user_id: str, **kwargs) -> bool:
        """更新用户信息"""
        allowed_fields = ["username", "avatar_url"]
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return False

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [user_id]

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE users SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                          values)
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def list_users() -> List[Dict[str, Any]]:
        """获取所有用户（管理用）"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]


# ============== 会话数据库访问 ==============

class SessionDB:
    """会话数据库访问类"""

    @staticmethod
    def create(user_id: str, title: str = None, context_data: dict = None) -> Optional[Dict[str, Any]]:
        """创建新会话"""
        session_id = generate_session_id()

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO chat_sessions (session_id, user_id, title, context_data)
                    VALUES (?, ?, ?, ?)
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,))
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM chat_sessions
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE chat_sessions
                SET title = ?, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
            """, (title, session_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def update_context(session_id: str, context_data: dict) -> bool:
        """更新会话上下文数据"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE chat_sessions
                SET context_data = ?, updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
            """, (json.dumps(context_data), session_id))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def touch(session_id: str) -> bool:
        """更新会话时间戳"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE session_id = ?
            """, (session_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete(session_id: str) -> bool:
        """删除会话及其所有消息"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
                cursor.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))
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

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO chat_messages (message_id, session_id, role, content, metadata)
                    VALUES (?, ?, ?, ?, ?)
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chat_messages WHERE message_id = ?", (message_id,))
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM chat_messages
                WHERE session_id = ?
                ORDER BY created_at ASC
                LIMIT ?
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_messages WHERE message_id = ?", (message_id,))
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

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO chat_records 
                    (record_id, session_id, user_id, user_message, assistant_message,
                     total_token_count, prompt_tokens, completion_tokens, model,
                     execution_details, status, error_message, duration_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chat_records WHERE record_id = ?", (record_id,))
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM chat_records
                WHERE session_id = ?
                ORDER BY created_at ASC
                LIMIT ?
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM chat_records
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COALESCE(SUM(total_token_count), 0) as total,
                    COALESCE(SUM(prompt_tokens), 0) as prompt,
                    COALESCE(SUM(completion_tokens), 0) as completion
                FROM chat_records
                WHERE session_id = ?
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
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_records WHERE record_id = ?", (record_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def delete_by_session(session_id: str) -> bool:
        """删除会话的所有记录"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_records WHERE session_id = ?", (session_id,))
            conn.commit()
            return cursor.rowcount > 0


# ============== 短信验证码 ==============

def send_sms_code(phone: str) -> bool:
    """
    发送短信验证码
    当前为Mock实现，固定验证码888888
    """
    code = "888888"

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE sms_codes SET used = 1 WHERE phone = ?", (phone,))

        expires_at = (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            INSERT INTO sms_codes (phone, code, expires_at)
            VALUES (?, ?, ?)
        """, (phone, code, expires_at))
        conn.commit()

    logger.info(f"[MOCK SMS] 验证码 {code} 已发送到 {phone}")
    return True


def verify_sms_code(phone: str, code: str) -> bool:
    """验证短信验证码"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM sms_codes
            WHERE phone = ? AND code = ? AND used = 0
            AND expires_at > ?
            ORDER BY created_at DESC LIMIT 1
        """, (phone, code, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        row = cursor.fetchone()

        if row:
            cursor.execute("UPDATE sms_codes SET used = 1 WHERE phone = ? AND code = ?",
                         (phone, code))
            conn.commit()
            return True
        return False
