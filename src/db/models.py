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
        placeholder = "%s"

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
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM users WHERE user_id = {placeholder}", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def get_by_phone(phone: str) -> Optional[Dict[str, Any]]:
        """根据手机号获取用户"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM users WHERE phone = {placeholder}", (phone,))
            row = cursor.fetchone()
            return dict(row) if row else None

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
    def verify_login(phone: str, password: str) -> Optional[Dict[str, Any]]:
        """验证手机号密码登录"""
        user = UserDB.get_by_phone(phone)
        if user and verify_password(password, user.get("password_hash", "")):
            return user
        return None

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
        allowed_fields = ["username", "avatar_url", "role", "tenant_id"]
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
            return cursor.rowcount > 0

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
                "SELECT COUNT(*) as cnt FROM users WHERE tenant_id = %s AND status = 'active'",
                (tenant_id,),
            )
            total = cursor.fetchone()["cnt"]
            cursor.execute("""
                SELECT * FROM users
                WHERE tenant_id = %s AND status = 'active'
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
                cursor.execute("""
                    SELECT * FROM users
                    WHERE role IN ('platform_admin', 'tenant_admin')
                    AND tenant_id = %s AND status = 'active'
                    ORDER BY created_at
                """, (tenant_id,))
            else:
                cursor.execute("""
                    SELECT * FROM users
                    WHERE role = 'platform_admin' AND status = 'active'
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
                return SessionDB.get_by_id(session_id)
            except Exception as e:
                logger.error(f"Failed to create chat session: {e}")
                return None

    @staticmethod
    def get_by_id(session_id: str) -> Optional[Dict[str, Any]]:
        """根据会话ID获取会话"""
        placeholder = "%s"
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
    def list_by_user(user_id: str, page: int = 1, page_size: int = 20, tenant_id: str = None) -> Dict[str, Any]:
        """获取用户的会话列表（分页）

        Args:
            user_id: 用户ID
            page: 页码，从1开始
            page_size: 每页数量
            tenant_id: 租户ID，传入时仅返回该租户下的会话；不传则返回所有会话（兼容非SaaS模式）
        """
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
            return {
                "sessions": sessions,
                "total": total,
                "page": page,
                "page_size": page_size
            }

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
            return cursor.rowcount > 0

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
            return cursor.rowcount > 0

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
            return cursor.rowcount > 0

    @staticmethod
    def touch(session_id: str) -> bool:
        """更新会话时间戳"""
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
        """获取会话的所有消息

        Args:
            session_id: 会话 ID
            limit: 最大返回数量
            roles: 可选，只返回指定角色的消息（如 ["user", "assistant"]）
        """
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
        placeholder = "%s"

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
