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
from src.core.cache_utils import CacheKeys, get_cached, set_cached, delete_cached, delete_cached_pattern, invalidate_user_cache, invalidate_tenant_cache


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
              wx_openid: str = None, wx_unionid: str = None,
              username: str = None,
              role: str = "user", tenant_id: str = None,
              source: str = None, nickname: str = None) -> Optional[Dict[str, Any]]:
        """创建新用户

        Args:
            phone: 手机号
            password: 密码
            wx_openid: 微信openid
            username: 用户名
            role: 角色，platform_admin/tenant_admin/user
            tenant_id: 租户ID，平台管理员为空
            nickname: 昵称（微信昵称等渠道用户昵称）
        """
        user_id = generate_user_id()
        placeholder = "%s"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(f"""
                    INSERT INTO users (user_id, phone, password_hash, wx_openid, wx_unionid, username, role, tenant_id, source, nickname)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                """, (user_id, phone, hash_password(password) if password else None,
                      wx_openid, wx_unionid, username or (f"用户{phone[-4:]}" if phone else f"用户{user_id[-4:]}"),
                      role, tenant_id, source, nickname))
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
            # 同一 phone 可能存在多条记录（不同 tenant_id 的同号用户、历史重复创建的 platform_admin）
            # 必须显式排序，否则 PostgreSQL 返回顺序不确定，导致不同 worker / 不同请求拿到不同用户
            cursor.execute(
                f"SELECT * FROM users WHERE phone = {placeholder} ORDER BY created_at DESC LIMIT 1",
                (phone,),
            )
            row = cursor.fetchone()
            user = dict(row) if row else None
            if user and not bypass_cache:
                # 缓存时不存 password_hash
                safe_user = {k: v for k, v in user.items() if k != "password_hash"}
                set_cached(CacheKeys.USER, f"phone:{phone}", value=safe_user, ttl=600)
            return user

    @staticmethod
    def get_platform_admin_by_phone(phone: str) -> Optional[Dict[str, Any]]:
        """查找 phone 对应的 platform_admin 记录（tenant_id 为空，符合 platform_admin 不变量）

        平台管理员应全局唯一（按 phone）：tenant_id 为空、role=platform_admin。
        本方法用于登录/自动建号时避免重复创建/转换。
        """
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM users WHERE phone = {placeholder} "
                f"AND role = 'platform_admin' AND tenant_id IS NULL "
                f"ORDER BY created_at DESC LIMIT 1",
                (phone,),
            )
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
        allowed_fields = ["username", "avatar_url", "role", "tenant_id", "source", "nickname", "wx_openid", "wx_unionid", "phone"]
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return False

        placeholder = "%s"
        set_clause = ", ".join([f"{k} = {placeholder}" for k in updates.keys()])
        ts = get_current_timestamp()
        values = list(updates.values()) + [user_id]

        # phone 变更时需要清理按 phone 维度的缓存（user:phone:{phone}），
        # 该 key 不在 invalidate_user_cache 默认清理范围（默认只清 user:{user_id}），
        # 不清理会导致 get_by_phone 在缓存 TTL 内仍返回旧记录
        old_phone = None
        if "phone" in updates:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT phone FROM users WHERE user_id = {placeholder}",
                    (user_id,),
                )
                row = cursor.fetchone()
                if row:
                    old_phone = row["phone"] if isinstance(row, dict) else row[0]

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE users SET {set_clause}, updated_at = {ts} WHERE user_id = {placeholder}",
                          values)
            conn.commit()
            result = cursor.rowcount > 0
            if result:
                # 清除用户缓存，下次查询从数据库重新加载
                invalidate_user_cache(user_id)
                # phone 变更时同步清理 phone 维度缓存
                if "phone" in updates:
                    new_phone = updates["phone"]
                    if old_phone:
                        delete_cached(CacheKeys.USER, f"phone:{old_phone}")
                    if new_phone:
                        delete_cached(CacheKeys.USER, f"phone:{new_phone}")
            return result

    # 别名方法，保持向后兼容
    update = update_info

    @staticmethod
    def list_users(page: int = 1, page_size: int = 20, tenant_id: str = None) -> dict:
        """获取用户列表（分页）

        只返回内部用户（source IS NULL），外部用户（如企业微信客服）由
        /api/saas/external-customers 接口单独管理。

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
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM users WHERE tenant_id = %s AND source IS NULL",
                    (tenant_id,),
                )
                total = cursor.fetchone()["cnt"]
                cursor.execute(
                    "SELECT * FROM users WHERE tenant_id = %s AND source IS NULL ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    (tenant_id, page_size, offset),
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM users WHERE source IS NULL"
                )
                total = cursor.fetchone()["cnt"]
                cursor.execute(
                    "SELECT * FROM users WHERE source IS NULL ORDER BY created_at DESC LIMIT %s OFFSET %s",
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

    @staticmethod
    def list_external_users(
        tenant_id: str,
        username: str = None,
        source: str = None,
        referrer_user_id: str = None,
        visible_kf_ids: Optional[list] = None,
        channel_chat_id: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """获取租户的外部用户列表（source 不为空的用户）

        返回组合粒度：每个「用户 × 渠道会话（客服账号）」一行。
        仅 wecom_kf 渠道按 channel_chat_id（open_kfid，客服账号）拆分，
        其它渠道折叠为单个组合（channel_chat_id 为空），避免 wecom_personal_rpa
        等渠道因 conversation_id 不稳定导致一个客户多行。

        Args:
            tenant_id: 租户ID
            username: 用户名搜索（可选）
            source: 用户来源筛选（可选）
            referrer_user_id: 引流员工筛选（可选，命中则只返回该员工引流的客户）
            visible_kf_ids: 普通用户可见的客服账号 open_kfid 列表（None 表示管理员全量可见）。
                非 None 时，只返回「在该账号下有会话的客户」，不返回客户在其他客服账号下的会话
            page: 页码，从1开始
            page_size: 每页数量

        Returns:
            {"users": [...], "total": int, "page": int, "page_size": int}
        """
        offset = (page - 1) * page_size
        # 渠道会话组合子查询：wecom_kf 按客服账号拆分（legacy NULL 折叠为空），其它渠道折叠为空
        cs_subquery = """
            SELECT user_id,
                   channel_type,
                   CASE WHEN channel_type = 'wecom_kf' THEN COALESCE(channel_chat_id, '') ELSE '' END AS channel_chat_id,
                   MIN(created_at) AS first_session_at,
                   MAX(updated_at) AS last_session_at
            FROM channel_sessions
            WHERE user_id IS NOT NULL
            GROUP BY user_id, channel_type,
                     CASE WHEN channel_type = 'wecom_kf' THEN COALESCE(channel_chat_id, '') ELSE '' END
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 构建查询条件
            conditions = ["u.tenant_id = %s", "u.source IS NOT NULL"]
            params = [tenant_id]

            if username:
                conditions.append("(u.username ILIKE %s OR u.nickname ILIKE %s)")
                params.append(f"%{username}%")
                params.append(f"%{username}%")

            if source:
                conditions.append("u.source = %s")
                params.append(source)

            if channel_chat_id:
                conditions.append("cs.channel_chat_id = %s")
                params.append(channel_chat_id)

            if referrer_user_id:
                conditions.append("cr.referrer_user_id = %s")
                params.append(referrer_user_id)

            if visible_kf_ids is not None:
                # 普通用户（引流员工）只能看到自己负责的客服账号下的对话记录
                conditions.append("cs.channel_chat_id = ANY(%s)")
                params.append(visible_kf_ids)

            where_clause = " AND ".join(conditions)

            # 统计总数：与列表查询同一套 FROM/JOIN/WHERE 结构，count == 组合行数
            # customer_referrals.customer_user_id 有 UNIQUE、cs 子查询已按组合去重，外层 JOIN 单射。
            cursor.execute(f"""
                SELECT COUNT(*) as cnt FROM (
                    SELECT u.user_id
                    FROM users u
                    LEFT JOIN ({cs_subquery}) cs ON cs.user_id = u.user_id
                    LEFT JOIN customer_referrals cr ON cr.customer_user_id = u.user_id
                    WHERE {where_clause}
                ) AS filtered
            """, params)
            total = cursor.fetchone()["cnt"]

            # 查询列表：按该组合最近一次渠道会话的 updated_at 倒序排序
            # 没有会话的用户排在最后（NULLS LAST）
            # 同时返回该组合最早/最近一次渠道会话的 created_at / updated_at，
            # 用于前端展示"[创建日期] ~ [更新日期]"。
            # 引流人关联：LEFT JOIN customer_referrals + users 返回 referrer_user_id / referrer_name。
            cursor.execute(f"""
                SELECT u.user_id, u.username, u.nickname, u.avatar_url, u.source, u.tenant_id, u.created_at,
                       cs.channel_type,
                       cs.channel_chat_id,
                       cs.first_session_at,
                       cs.last_session_at,
                       cr.referrer_user_id,
                       ru.nickname AS referrer_nickname,
                       ru.username AS referrer_username,
                       cr.created_at AS referral_time
                FROM users u
                LEFT JOIN ({cs_subquery}) cs ON cs.user_id = u.user_id
                LEFT JOIN customer_referrals cr ON cr.customer_user_id = u.user_id
                LEFT JOIN users ru ON ru.user_id = cr.referrer_user_id
                WHERE {where_clause}
                ORDER BY cs.last_session_at DESC NULLS LAST, u.created_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            users = [dict(row) for row in cursor.fetchall()]

        for u in users:
            # 组合归一化：无会话用户 channel_type 为 None、channel_chat_id 为空串
            u["channel_chat_id"] = u.get("channel_chat_id") or ""
            # 引流人名称：优先昵称，其次用户名，引流人被删除时显示"已删除员工"
            if u.get("referrer_user_id"):
                u["referrer_name"] = (
                    u.get("referrer_nickname") or u.get("referrer_username") or "已删除员工"
                )
            else:
                u["referrer_name"] = None
        return {"users": users, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def get_user_sessions(
        user_id: str,
        instance_id: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        """获取用户的会话列表（分页）

        Args:
            user_id: 用户ID
            instance_id: 数字员工实例ID筛选（可选）
            page: 页码，从1开始
            page_size: 每页数量

        Returns:
            {"sessions": [...], "total": int, "page": int, "page_size": int}
        """
        offset = (page - 1) * page_size
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 构建查询条件
            conditions = ["user_id = %s"]
            params = [user_id]

            if instance_id:
                conditions.append("instance_id = %s")
                params.append(instance_id)

            where_clause = " AND ".join(conditions)

            # 统计总数
            cursor.execute(f"SELECT COUNT(*) as cnt FROM chat_sessions WHERE {where_clause}", params)
            total = cursor.fetchone()["cnt"]

            # 查询列表
            cursor.execute(f"""
                SELECT session_id, user_id, tenant_id, subagent_id, instance_id, title, created_at, updated_at
                FROM chat_sessions
                WHERE {where_clause}
                ORDER BY updated_at DESC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            sessions = [dict(row) for row in cursor.fetchall()]
        return {"sessions": sessions, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def get_session_messages(
        session_id: str,
        content_search: str = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """获取会话的消息列表（支持内容搜索）

        Args:
            session_id: 会话ID
            content_search: 聊天内容搜索（可选）
            page: 页码，从1开始
            page_size: 每页数量

        Returns:
            {"messages": [...], "total": int, "page": int, "page_size": int}
        """
        offset = (page - 1) * page_size
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 构建查询条件
            conditions = ["session_id = %s"]
            params = [session_id]

            if content_search:
                conditions.append("content LIKE %s")
                params.append(f"%{content_search}%")

            where_clause = " AND ".join(conditions)

            # 统计总数
            cursor.execute(f"SELECT COUNT(*) as cnt FROM chat_messages WHERE {where_clause}", params)
            total = cursor.fetchone()["cnt"]

            # 查询列表
            cursor.execute(f"""
                SELECT message_id, session_id, role, content, metadata, created_at
                FROM chat_messages
                WHERE {where_clause}
                ORDER BY id ASC
                LIMIT %s OFFSET %s
            """, params + [page_size, offset])

            messages = []
            for row in cursor.fetchall():
                msg = dict(row)
                if msg.get("metadata"):
                    msg["metadata"] = json.loads(msg["metadata"])
                messages.append(msg)
        return {"messages": messages, "total": total, "page": page, "page_size": page_size}


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
    def update_context_token_count(session_id: str, token_count: int) -> bool:
        """更新 session 的上下文 token 缓存（v3.1）。

        Agent 主循环每次 LLM 调用后写入最后一次 prompt_tokens + completion_tokens，
        供 ContextCompressionService._should_compress 优先读取，避免全量 count_tokens。

        Args:
            session_id: 会话 ID
            token_count: 最后一次 LLM 调用的 prompt+completion token 数

        Returns:
            是否更新成功（session 不存在时返回 False）
        """
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"UPDATE chat_sessions SET context_token_count = {placeholder} "
                    f"WHERE session_id = {placeholder}",
                    (int(token_count), session_id),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                logger.exception(
                    f"Failed to update context_token_count: session={session_id}, err={e}"
                )
                try:
                    conn.rollback()
                except Exception as rollback_err:
                    logger.error(f"rollback failed: {rollback_err}")
                return False

    @staticmethod
    def delete(session_id: str) -> bool:
        """删除会话及其消息（保留 chat_records 用于计费审计）"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 先获取 user_id 用于清除会话列表缓存（在删除之前）
                cursor.execute(f"SELECT user_id FROM chat_sessions WHERE session_id = {placeholder}", (session_id,))
                session_row = cursor.fetchone()
                user_id = session_row["user_id"] if session_row else None

                # 保留 chat_records：该表是计费/审计数据，按 tenant_id + user_id + 时间段聚合，
                # 与会话生命周期解耦，不能跟随会话删除。
                cursor.execute(f"DELETE FROM chat_messages WHERE session_id = {placeholder}", (session_id,))
                cursor.execute(f"DELETE FROM chat_sessions WHERE session_id = {placeholder}", (session_id,))
                conn.commit()
                logger.info(f"Chat session deleted: {session_id}")

                # 清除会话和消息缓存
                delete_cached(CacheKeys.SESSION, session_id)
                delete_cached_pattern(CacheKeys.SESSION_MSGS, session_id, "")
                if user_id:
                    delete_cached_pattern(CacheKeys.USER_SESSIONS, user_id, "")
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
    def create_batch_transactional(session_id: str, messages: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """事务性批量创建消息。所有消息作为一个原子事务写入，要么全部成功要么全部失败。

        Args:
            session_id: 会话 ID
            messages: 消息列表，每条是 {"role": str, "content": str, "metadata": dict|None,
                                       "created_at": datetime|None}
                      role 可以是 "user" / "assistant" / "tool"
                      assistant 角色有两种子情况（靠 metadata.tool_calls 是否存在区分），本方法不区分，原样存储
                      created_at 可选，未传时使用数据库默认值 CURRENT_TIMESTAMP。
                      注意：PostgreSQL 的 CURRENT_TIMESTAMP 返回事务开始时间，同一事务内所有行
                      会拿到完全相同的 created_at。若需区分先后（如用户消息和助手回复）必须显式传入。

        Returns:
            成功时返回创建的消息列表（含 message_id）；失败时返回 None
        """
        if not messages:
            return []

        placeholder = "%s"
        created_messages: List[Dict[str, Any]] = []

        # 单个连接保证所有 INSERT 在同一事务中（psycopg2 默认 autocommit=False）
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                for msg in messages:
                    message_id = generate_message_id()
                    role = msg.get("role")
                    content = msg.get("content")
                    metadata = msg.get("metadata")
                    created_at = msg.get("created_at")

                    # 显式传入 created_at 时写入该列；否则省略列让数据库走默认值
                    if created_at is not None:
                        cursor.execute(f"""
                            INSERT INTO chat_messages (message_id, session_id, role, content, metadata, created_at)
                            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                        """, (message_id, session_id, role, content,
                              json.dumps(metadata, ensure_ascii=False, default=str) if metadata else None,
                              created_at))
                    else:
                        cursor.execute(f"""
                            INSERT INTO chat_messages (message_id, session_id, role, content, metadata)
                            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                        """, (message_id, session_id, role, content,
                              json.dumps(metadata, ensure_ascii=False, default=str) if metadata else None))
                    created_messages.append({
                        "message_id": message_id,
                        "session_id": session_id,
                        "role": role,
                        "content": content,
                        "metadata": metadata,
                        "created_at": created_at,
                    })

                conn.commit()

                # 缓存失效只调一次，避免重复 IO
                SessionDB.touch(session_id)
                delete_cached_pattern(CacheKeys.SESSION_MSGS, session_id, "")
                return created_messages
            except Exception as e:
                # 任何异常必须 rollback，不能留下部分写入的消息
                try:
                    conn.rollback()
                except Exception as rollback_err:
                    logger.error(f"Failed to rollback batch message insert: {rollback_err}")
                logger.error(f"Failed to create chat messages batch: {e}")
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
        include_compacted: bool = False,
    ) -> List[Dict[str, Any]]:
        """获取会话的所有消息（优先从 Redis 缓存读取，TTL 60秒）

        Args:
            session_id: 会话 ID
            limit: 最大返回数量
            roles: 可选，只返回指定角色的消息（如 ["user", "assistant"]）
            include_compacted: 是否包含 compacted=true 的消息（v3.1）。
                默认 False：前端展示和 Agent 重建 memory 都自动跳过被压缩的历史消息；
                运维/审计场景可传 True 取回全部消息。
        """
        # 生成缓存 key（角色 / include_compacted 不同会影响结果）
        roles_str = "_".join(sorted(roles)) if roles else "all"
        compacted_flag = "1" if include_compacted else "0"
        cached = get_cached(CacheKeys.SESSION_MSGS, session_id, str(limit), roles_str, compacted_flag)
        if cached is not None:
            return cached

        placeholder = "%s"
        # v3.1: 默认过滤掉 compacted=true 的消息（前端展示与 Agent memory 都跳过）
        compacted_clause = "" if include_compacted else " AND (compacted = FALSE OR compacted IS NULL)"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if roles:
                role_placeholders = ",".join([placeholder] * len(roles))
                # 取最近 N 条（按 id 倒序取 N 条）再正序返回，避免长会话丢掉最近一轮对话。
                # v3.1: compacted_clause 用于过滤已压缩消息（默认排除）
                cursor.execute(f"""
                    SELECT * FROM (
                        SELECT * FROM chat_messages
                        WHERE session_id = {placeholder} AND role IN ({role_placeholders})
                        {compacted_clause}
                        ORDER BY id DESC
                        LIMIT {placeholder}
                    ) AS recent
                    ORDER BY id ASC
                """, (session_id, *roles, limit))
            else:
                cursor.execute(f"""
                    SELECT * FROM (
                        SELECT * FROM chat_messages
                        WHERE session_id = {placeholder}
                        {compacted_clause}
                        ORDER BY id DESC
                        LIMIT {placeholder}
                    ) AS recent
                    ORDER BY id ASC
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
            set_cached(CacheKeys.SESSION_MSGS, session_id, str(limit), roles_str, compacted_flag,
                       value=messages, ttl=60)
            return messages

    @staticmethod
    def count_messages_by_session(session_id: str, include_compacted: bool = False) -> int:
        """统计会话消息条数（v3.2 新增，用于压缩阈值快路径检查）。

        只 SELECT COUNT(*)，不拉数据，走索引，开销 O(log n)。

        Args:
            session_id: 会话 ID
            include_compacted: 是否包含 compacted=true 的消息。
                默认 False：与 list_by_session 默认行为一致（只数未压缩的消息）。

        Returns:
            消息条数；查询异常时返回 0（容错，让阈值判断降级到 token 缓存）
        """
        placeholder = "%s"
        compacted_clause = "" if include_compacted else " AND (compacted = FALSE OR compacted IS NULL)"
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"SELECT COUNT(*) AS cnt FROM chat_messages "
                    f"WHERE session_id = {placeholder}{compacted_clause}",
                    (session_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return 0
                try:
                    return int(row.get("cnt") or 0)
                except (TypeError, ValueError):
                    return 0
        except Exception as e:
            logger.warning(
                f"MessageDB.count_messages_by_session failed: sid={session_id}, err={e}"
            )
            return 0

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
        source_type: str = "chat",
        credit_cost: float = 0.0,
        embedding_tokens: int = 0,
        asr_calls: int = 0,
        usage_breakdown: dict = None,
    ) -> Optional[Dict[str, Any]]:
        """创建新的会话记录

        在同一事务内完成：
        1. INSERT chat_records（含 credit_cost）
        2. 若 tenant_id 非空且 credit_cost > 0，原子扣减 tenants.credit_balance

        扣减失败时整体 rollback，保证对话记录与余额变更一致性。
        """
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
                     status, error_message, duration_ms, source_type, credit_cost,
                     embedding_tokens, asr_calls, usage_breakdown)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                            {placeholder}, {placeholder}, {placeholder}, {placeholder},
                            {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                            {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                            {placeholder}, {placeholder}, {placeholder})
                    RETURNING *
                """, (
                    record_id, session_id, tenant_id, user_id, user_message, assistant_message,
                    total_token_count, prompt_tokens, completion_tokens, cached_input_tokens,
                    model, provider,
                    json.dumps(execution_details) if execution_details else None,
                    agent_iterations,
                    json.dumps(subagent_calls) if subagent_calls else None,
                    status, error_message, duration_ms, source_type, credit_cost,
                    embedding_tokens or 0,
                    asr_calls or 0,
                    json.dumps(usage_breakdown) if usage_breakdown else None,
                ))
                row = cursor.fetchone()

                # 同事务原子扣减余额：tenant_id 为空（非 SaaS 模式）或 credit_cost = 0 时跳过
                if tenant_id and credit_cost and credit_cost > 0:
                    cursor.execute(
                        "UPDATE tenants SET credit_balance = credit_balance - %s WHERE tenant_id = %s",
                        (credit_cost, tenant_id)
                    )

                conn.commit()

                # 扣费后失效租户缓存，确保下一轮入口拦截（_check_tenant_credit_blocked）
                # 能读到最新余额；失败只记 warning，不影响已落库的对话记录
                if tenant_id and credit_cost and credit_cost > 0:
                    try:
                        invalidate_tenant_cache(tenant_id)
                    except Exception as cache_err:
                        logger.warning(
                            f"扣费后失效租户缓存失败 tenant_id={tenant_id}: {cache_err}"
                        )

                logger.info(
                    f"Chat record created: {record_id} for session: {session_id}, "
                    f"credit_cost={credit_cost}, tenant_id={tenant_id}"
                )
                if not row:
                    return None
                result = dict(row)
                if result.get("execution_details"):
                    try:
                        result["execution_details"] = json.loads(result["execution_details"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                if result.get("subagent_calls"):
                    try:
                        result["subagent_calls"] = json.loads(result["subagent_calls"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                return result
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
            # input_cost 计费规则：
            #   - 模型配了 cached_input_price_per_m：非缓存部分按 input_price_per_m，缓存命中部分按 cached_input_price_per_m
            #   - 模型未配 cached_input_price_per_m：全部输入按 input_price_per_m（等价原逻辑）
            #   - 模型未配价（tcp 为 NULL）：input_cost = 0（COALESCE 处理）
            cursor.execute("""
                SELECT
                    cr.tenant_id,
                    COUNT(*) as conversation_count,
                    COALESCE(SUM(cr.prompt_tokens), 0) as input_tokens,
                    COALESCE(SUM(cr.completion_tokens), 0) as output_tokens,
                    COALESCE(SUM(
                        CASE WHEN tcp.cached_input_price_per_m IS NOT NULL THEN
                            (cr.prompt_tokens - COALESCE(cr.cached_input_tokens, 0)) * tcp.input_price_per_m / 1000000
                            + COALESCE(cr.cached_input_tokens, 0) * tcp.cached_input_price_per_m / 1000000
                        ELSE
                            cr.prompt_tokens * tcp.input_price_per_m / 1000000
                        END
                    ), 0) as input_cost,
                    COALESCE(SUM(cr.completion_tokens * tcp.output_price_per_m / 1000000), 0) as output_cost,
                    COALESCE(SUM(cr.credit_cost), 0) as credit_cost,
                    EXISTS(
                        SELECT 1 FROM chat_records cr2
                        LEFT JOIN token_cost_prices tcp2 ON LOWER(cr2.model) = LOWER(tcp2.model_name)
                        WHERE cr2.tenant_id = cr.tenant_id
                          AND cr2.created_at >= %s AND cr2.created_at <= %s
                          AND NOT (cr2.prompt_tokens = 0 AND cr2.completion_tokens = 0)
                          AND tcp2.model_name IS NULL
                          AND cr2.model IS NOT NULL
                    ) as has_unpriced_tokens
                FROM chat_records cr
                LEFT JOIN token_cost_prices tcp ON LOWER(cr.model) = LOWER(tcp.model_name)
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
            total_credit_cost = 0
            has_unpriced = False

            for row in rows:
                if row["conversation_count"] > 0:
                    input_cost = float(row["input_cost"]) if row["input_cost"] else 0.0
                    output_cost = float(row["output_cost"]) if row["output_cost"] else 0.0
                    tenant_unpriced = bool(row["has_unpriced_tokens"])
                    tenant_credit_cost = float(row["credit_cost"] or 0)
                    tenant_data.append({
                        "tenant_id": row["tenant_id"],
                        "input_tokens": row["input_tokens"],
                        "output_tokens": row["output_tokens"],
                        "conversation_count": row["conversation_count"],
                        "input_cost": input_cost,
                        "output_cost": output_cost,
                        "total_cost": round(input_cost + output_cost, 2),
                        "credit_cost": tenant_credit_cost,
                        "has_unpriced_tokens": tenant_unpriced
                    })
                    total_input_tokens += row["input_tokens"]
                    total_output_tokens += row["output_tokens"]
                    total_conversations += row["conversation_count"]
                    total_input_cost += input_cost
                    total_output_cost += output_cost
                    total_credit_cost += tenant_credit_cost
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
                    "total_credit_cost": total_credit_cost,
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
                    cr.assistant_message,
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
                    "assistant_message": row["assistant_message"] or "",
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


# ============== 微信客服引流归因（customer_referrals） ==============


class CustomerReferralDB:
    """C端客户→引流员工 first-touch 归因

    扫码 enter_session 事件按 scene 反查绑定员工后写入。UNIQUE(customer_user_id) +
    INSERT ... ON CONFLICT DO NOTHING 保证一个 C 端客户只归属第一个扫码的引流员工。
    """

    @staticmethod
    def record(
        tenant_id: str,
        referrer_user_id: str,
        customer_user_id: str,
        open_kfid: str,
        scene: str,
    ) -> bool:
        """写入引流归因。重复扫码（同一 customer_user_id）不覆盖，返回是否新写入。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO customer_referrals (tenant_id, referrer_user_id, customer_user_id, open_kfid, scene)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (customer_user_id) DO NOTHING
                """,
                (tenant_id, referrer_user_id, customer_user_id, open_kfid, scene),
            )
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def get_by_customer(customer_user_id: str) -> Optional[Dict[str, Any]]:
        """按 C 端客户 user_id 查询归因记录。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM customer_referrals WHERE customer_user_id = %s",
                (customer_user_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def count_by_open_kfid(tenant_id: str, open_kfid: str) -> int:
        """客服账号的引流人数（列表页展示用，口径：扫码即算）。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS cnt FROM customer_referrals WHERE tenant_id = %s AND open_kfid = %s",
                (tenant_id, open_kfid),
            )
            row = cursor.fetchone()
            return row["cnt"] if isinstance(row, dict) else row[0]

    @staticmethod
    def referral_stats(
        tenant_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        referrer_user_id: Optional[str] = None,
    ) -> dict:
        """引流统计：总引流数 + 员工分组（referral_count / ratio）。

        过滤基准 = customer_referrals.created_at（引流发生时间）。
        referrer_user_id 传入时（普通用户），仅统计该引流员工自己的数据。
        """
        date_cond = ""
        params: list = [tenant_id]
        if start_date:
            date_cond += " AND cr.created_at >= %s"
            params.append(start_date)
        if end_date:
            # end_date 含当日：< 次日零点 语义，SQL 内 +1 天，使传入当天也能统计到当天全天数据
            date_cond += " AND cr.created_at < (%s::date + INTERVAL '1 day')"
            params.append(end_date)
        if referrer_user_id:
            date_cond += " AND cr.referrer_user_id = %s"
            params.append(referrer_user_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT COUNT(*) AS cnt FROM customer_referrals cr WHERE cr.tenant_id = %s{date_cond}",
                params,
            )
            row = cursor.fetchone()
            total = row["cnt"] if isinstance(row, dict) else row[0]

            cursor.execute(
                f"""
                SELECT cr.referrer_user_id,
                       u.nickname AS referrer_nickname,
                       u.username AS referrer_username,
                       COUNT(*) AS referral_count
                FROM customer_referrals cr
                LEFT JOIN users u ON u.user_id = cr.referrer_user_id
                WHERE cr.tenant_id = %s{date_cond}
                GROUP BY cr.referrer_user_id, u.nickname, u.username
                ORDER BY referral_count DESC
                """,
                params,
            )
            referrers = []
            for r in cursor.fetchall():
                d = dict(r)
                d["referrer_name"] = d.get("referrer_nickname") or d.get("referrer_username") or "已删除员工"
                d["ratio"] = round(d["referral_count"] * 100.0 / total, 1) if total else 0.0
                referrers.append(d)
        return {"total_referrals": total, "referrers": referrers}

    @staticmethod
    def count_referred_messages(
        tenant_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        referrer_user_id: Optional[str] = None,
    ) -> int:
        """总对话消息数：customer_referrals.customer_user_id → channel_sessions.user_id
        → channel_messages.session_id，过滤 created_at 在日期段内（is_recalled=FALSE）。"""
        cond = "cr.tenant_id = %s AND cm.is_recalled = FALSE"
        params: list = [tenant_id]
        if start_date:
            cond += " AND cm.created_at >= %s"
            params.append(start_date)
        if end_date:
            # end_date 含当日：< 次日零点 语义，SQL 内 +1 天
            cond += " AND cm.created_at < (%s::date + INTERVAL '1 day')"
            params.append(end_date)
        if referrer_user_id:
            cond += " AND cr.referrer_user_id = %s"
            params.append(referrer_user_id)

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT COUNT(*) AS cnt
                FROM channel_messages cm
                JOIN channel_sessions cs ON cs.session_id = cm.session_id
                JOIN customer_referrals cr ON cr.customer_user_id = cs.user_id
                WHERE {cond}
                """,
                params,
            )
            row = cursor.fetchone()
            return row["cnt"] if isinstance(row, dict) else row[0]

    @staticmethod
    def sum_kf_account_credit(tenant_id: str, open_kfid: str) -> float:
        """账号级积分消耗归集（credit_limit 依据）：wecom_kf 渠道该 open_kfid 账号累计 credit_cost。"""
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COALESCE(SUM(cr.credit_cost), 0) AS total_cost
                FROM chat_records cr
                JOIN channel_sessions cs ON cs.session_id = cr.session_id
                WHERE cs.tenant_id = %s AND cs.channel_type = 'wecom_kf' AND cs.channel_chat_id = %s
                """,
                (tenant_id, open_kfid),
            )
            row = cursor.fetchone()
            return float(row["total_cost"] if isinstance(row, dict) else row[0])


# ============== 短信验证码 ==============

def send_sms_code(phone: str) -> bool:
    """
    发送短信验证码

    使用配置的短信通道真实发送短信。
    """
    from src.sms.manager import sms_manager

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

        expires_at = (datetime.now() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S")
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


# ============== 会话内上下文压缩摘要 ==============

def generate_summary_id() -> str:
    """生成唯一上下文摘要ID"""
    return f"csum_{uuid.uuid4().hex[:12]}"


class ContextSummaryDB:
    """会话内上下文压缩摘要数据库访问类 - 操作 chat_context_summaries 表

    每次压缩产生一行新记录，旧的 active summary 被置为 'superseded'，永不删除。
    同一 (session_id, source_type) 同时只能有一条 status='active' 的记录
    （由部分索引 idx_ccs_session_active 保证）。
    """

    @staticmethod
    def create(
        summary_id: str,
        session_id: str,
        source_type: str,
        tenant_id: Optional[str],
        user_id: Optional[str],
        subagent_id: Optional[str],
        summary_text: str,
        summary_version: int,
        compressed_message_ids: List[int],
        compressed_message_count: int,
        original_token_count: int,
        compressed_token_count: int,
        compression_ratio: float,
        llm_provider: Optional[str] = None,
        llm_model: Optional[str] = None,
        llm_tokens_used: Optional[int] = None,
        status: str = "active",
        fallback_used: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """创建新的上下文摘要记录

        Args:
            summary_id: 摘要唯一 ID（csum_xxx）
            session_id: 会话 ID
            source_type: 来源类型（chat / wecom_kf / dingtalk / feishu / wecom_personal_rpa）
            tenant_id: 租户 ID（租户隔离）
            user_id: 用户 ID
            subagent_id: 子智能体 ID（NULL 表示主智能体）
            summary_text: 摘要文本
            summary_version: 该 session 第几次压缩（递增）
            compressed_message_ids: 被压缩的原消息 BIGINT id 列表
            compressed_message_count: 被压缩的消息数量
            original_token_count: 压缩前 token 数
            compressed_token_count: 压缩后 token 数（摘要 + TAIL）
            compression_ratio: 压缩比（compressed / original）
            llm_provider: 摘要 LLM 提供者
            llm_model: 摘要 LLM 模型名
            llm_tokens_used: 摘要 LLM 调用消耗 token 数
            status: 初始状态（默认 'active'）
            fallback_used: 是否走了同步降级路径

        Returns:
            创建成功的记录字典，失败返回 None
        """
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"""
                    INSERT INTO chat_context_summaries (
                        summary_id, session_id, source_type, tenant_id, user_id, subagent_id,
                        summary_text, summary_version,
                        compressed_message_ids, compressed_message_count,
                        original_token_count, compressed_token_count, compression_ratio,
                        llm_provider, llm_model, llm_tokens_used,
                        fallback_used, status
                    ) VALUES (
                        {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder},
                        {placeholder}, {placeholder},
                        {placeholder}, {placeholder},
                        {placeholder}, {placeholder}, {placeholder},
                        {placeholder}, {placeholder}, {placeholder},
                        {placeholder}, {placeholder}
                    )
                    RETURNING *
                    """,
                    (
                        summary_id, session_id, source_type, tenant_id, user_id, subagent_id,
                        summary_text, summary_version,
                        list(compressed_message_ids), compressed_message_count,
                        original_token_count, compressed_token_count, compression_ratio,
                        llm_provider, llm_model, llm_tokens_used,
                        fallback_used, status,
                    ),
                )
                row = cursor.fetchone()
                conn.commit()
                return dict(row) if row else None
            except Exception as e:
                try:
                    conn.rollback()
                except Exception as rollback_err:
                    logger.error(f"Failed to rollback ContextSummaryDB.create: {rollback_err}")
                logger.error(f"Failed to create context summary: {e}")
                return None

    @staticmethod
    def get_active_by_session(
        session_id: str,
        source_type: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """获取会话当前 active 的摘要（同一 session 同一 source_type 至多一条）。

        Args:
            session_id: 会话 ID
            source_type: 来源类型
            tenant_id: 租户 ID（租户隔离）。非 SaaS 场景可传 None，此时不加租户过滤。
        """
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute(
                    f"""
                    SELECT * FROM chat_context_summaries
                    WHERE session_id = {placeholder}
                      AND source_type = {placeholder}
                      AND tenant_id = {placeholder}
                      AND status = 'active'
                    ORDER BY summary_version DESC
                    LIMIT 1
                    """,
                    (session_id, source_type, tenant_id),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT * FROM chat_context_summaries
                    WHERE session_id = {placeholder}
                      AND source_type = {placeholder}
                      AND status = 'active'
                    ORDER BY summary_version DESC
                    LIMIT 1
                    """,
                    (session_id, source_type),
                )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def mark_superseded(summary_id: str) -> bool:
        """将指定摘要置为 superseded（被新摘要替代）"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"""
                    UPDATE chat_context_summaries
                    SET status = 'superseded',
                        superseded_at = CURRENT_TIMESTAMP
                    WHERE summary_id = {placeholder} AND status = 'active'
                    """,
                    (summary_id,),
                )
                conn.commit()
                return cursor.rowcount > 0
            except Exception as e:
                try:
                    conn.rollback()
                except Exception as rollback_err:
                    logger.error(f"Failed to rollback ContextSummaryDB.mark_superseded: {rollback_err}")
                logger.error(f"Failed to mark summary superseded {summary_id}: {e}")
                return False

    @staticmethod
    def list_by_session(
        session_id: str,
        source_type: str,
        include_inactive: bool = False,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """列出会话的所有摘要记录（默认只看 active）

        Args:
            session_id: 会话 ID
            source_type: 来源类型
            include_inactive: 是否包含已被 superseded 的历史摘要
            tenant_id: 租户 ID（租户隔离）。非 SaaS 场景可传 None，不加租户过滤。
        """
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            conditions = [
                f"session_id = {placeholder}",
                f"source_type = {placeholder}",
            ]
            params: list = [session_id, source_type]
            if tenant_id is not None:
                conditions.append(f"tenant_id = {placeholder}")
                params.append(tenant_id)
            if not include_inactive:
                conditions.append("status = 'active'")
            where_clause = " AND ".join(conditions)
            cursor.execute(
                f"""
                SELECT * FROM chat_context_summaries
                WHERE {where_clause}
                ORDER BY summary_version DESC
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def get_by_id(
        summary_id: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """根据 summary_id 获取摘要。

        Args:
            summary_id: 摘要 ID
            tenant_id: 租户 ID（租户隔离）。非 SaaS 场景可传 None，不加租户过滤。
        """
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if tenant_id is not None:
                cursor.execute(
                    f"""
                    SELECT * FROM chat_context_summaries
                    WHERE summary_id = {placeholder} AND tenant_id = {placeholder}
                    """,
                    (summary_id, tenant_id),
                )
            else:
                cursor.execute(
                    f"SELECT * FROM chat_context_summaries WHERE summary_id = {placeholder}",
                    (summary_id,),
                )
            row = cursor.fetchone()
            return dict(row) if row else None


# ============== Token 成本价 ==============

class TokenCostPriceDB:
    """token_cost_prices 表访问类（全平台统一价，无 tenant_id）

    关联方式：chat_records.model = token_cost_prices.model_name
    """

    @staticmethod
    def get_by_model_name(model_name: str) -> Optional[Dict[str, Any]]:
        """按模型名查询单价（不区分大小写——模型名在不同渠道存在 GLM-5.3-Flash / glm-5.3-flash 等多种写法）

        Returns:
            {"model_name", "input_price_per_m", "cached_input_price_per_m",
             "output_price_per_m", "price_per_second", "price_per_second_by_resolution",
             "embedding_price_per_m", "asr_price_per_call", "tiered_pricing",
             "is_multimodal"} 或 None
            cached_input_price_per_m 为 NULL 表示该模型计费不区分缓存命中
            price_per_second 为 NULL 表示该模型不按秒计费（文本模型）
            price_per_second_by_resolution 为 NULL 表示视频模型不按分辨率区分，用 price_per_second
            embedding_price_per_m 为 NULL 表示该模型非 embedding 模型（无向量单价）
            asr_price_per_call 为 NULL 表示该模型非 ASR 模型（无语音识别单价）
            tiered_pricing 为 NULL 表示该模型不分段计价（走 input/output/cached 统一单价）
            is_multimodal 为 TRUE 表示模型原生支持图片输入（收到用户上传图片可直接进
            content 数组原生理解）；FALSE 表示纯文本模型（图片需先 OCR 识别文字）
        """
        if not model_name:
            return None
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT model_name, input_price_per_m, cached_input_price_per_m,
                       output_price_per_m, price_per_second, price_per_second_by_resolution,
                       embedding_price_per_m, asr_price_per_call, tiered_pricing,
                       is_multimodal
                FROM token_cost_prices
                WHERE LOWER(model_name) = LOWER({placeholder})
                """,
                (model_name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def list_multimodal_models() -> List[Dict[str, Any]]:
        """查询所有原生多模态模型（is_multimodal=TRUE）

        供图片路由使用：模型在返回清单内时，用户上传图片可直接进 content
        数组原生理解；不在清单内（或查不到）的纯文本模型维持先 OCR。

        Returns:
            [{"model_name", "input_price_per_m", "output_price_per_m",
              "cached_input_price_per_m", "is_multimodal"}, ...]，按 model_name 排序
        """
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT model_name, input_price_per_m, cached_input_price_per_m,
                       output_price_per_m, is_multimodal
                FROM token_cost_prices
                WHERE is_multimodal = TRUE
                ORDER BY model_name
                """
            )
            return [dict(row) for row in cursor.fetchall()]


# ============== 租户充值流水 ==============

def generate_recharge_id() -> str:
    """生成唯一充值记录 ID"""
    return f"rc_{uuid.uuid4().hex[:12]}"


class TenantRechargesDB:
    """tenant_recharges 表访问类（租户充值流水，#37）

    平台级计费表，记录每一次租户充值（手动/在线支付）。
    创建/删除时与 tenants.credit_balance 同事务原子变更。
    """

    @staticmethod
    def create(
        tenant_id: str,
        amount_yuan: float,
        credits: int,
        rate: int,
        source: str = "manual",
        operator_id: str = None,
        operator_name: str = None,
        remark: str = None,
        payment_order_id: str = None,
        created_at: str = None,
    ) -> Optional[Dict[str, Any]]:
        """创建充值记录，同事务原子增加 tenants.credit_balance

        在同一事务内 SELECT ... FOR UPDATE 锁定租户行、读取当前余额、
        计算 balance_after 快照写入本表，再更新 tenants.credit_balance，
        保证快照与最终余额一致且防并发充值竞态。

        Args:
            created_at: 可选，自定义充值时间（"YYYY-MM-DD HH:MM:SS"），未传则使用 DB 默认 CURRENT_TIMESTAMP

        Returns:
            新建记录字典；失败返回 None
        """
        if not tenant_id:
            logger.error("TenantRechargesDB.create: tenant_id is required")
            return None

        recharge_id = generate_recharge_id()
        placeholder = "%s"

        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                # 锁定租户行，读取当前余额（防并发充值竞态）
                cursor.execute(
                    f"SELECT credit_balance FROM tenants WHERE tenant_id = {placeholder} FOR UPDATE",
                    (tenant_id,),
                )
                tenant_row = cursor.fetchone()
                if not tenant_row:
                    logger.error(f"TenantRechargesDB.create: tenant not found: {tenant_id}")
                    return None
                current_balance = float(tenant_row.get("credit_balance") or 0)
                balance_after = current_balance + credits

                # 构造 INSERT：balance_after 紧跟 remark 之后；created_at 可选（未传走 DB 默认）
                if created_at:
                    cols = "(tenant_id, amount_yuan, credits, rate, source, payment_order_id, operator_id, operator_name, remark, balance_after, created_at)"
                    vals = f"({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})"
                    params = (
                        tenant_id, amount_yuan, credits, rate, source, payment_order_id,
                        operator_id, operator_name, remark, balance_after, created_at,
                    )
                else:
                    cols = "(tenant_id, amount_yuan, credits, rate, source, payment_order_id, operator_id, operator_name, remark, balance_after)"
                    vals = f"({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})"
                    params = (
                        tenant_id, amount_yuan, credits, rate, source, payment_order_id,
                        operator_id, operator_name, remark, balance_after,
                    )

                cursor.execute(
                    f"""
                    INSERT INTO tenant_recharges
                    {cols}
                    VALUES {vals}
                    RETURNING *
                    """,
                    params,
                )
                row = cursor.fetchone()

                # 同事务原子更新余额（用计算好的 balance_after，与快照一致）
                cursor.execute(
                    f"UPDATE tenants SET credit_balance = {placeholder} WHERE tenant_id = {placeholder}",
                    (balance_after, tenant_id),
                )

                conn.commit()
                logger.info(
                    f"Recharge created: tenant={tenant_id}, amount_yuan={amount_yuan}, "
                    f"credits={credits}, rate={rate}, source={source}, balance_after={balance_after}"
                )
                return dict(row) if row else None
            except Exception as e:
                logger.error(f"Failed to create recharge: {e}")
                return None

    @staticmethod
    def list(
        tenant_id: str = None,
        date_from: str = None,
        date_to: str = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """列表查询（按 created_at DESC）

        Args:
            tenant_id: 可选，按租户筛选
            date_from: 可选，开始日期 (YYYY-MM-DD)
            date_to: 可选，结束日期 (YYYY-MM-DD)
            page: 页码，从 1 开始
            page_size: 每页记录数

        Returns:
            {"items": [...], "total": int, "page": int, "page_size": int}
        """
        where_clauses: list = []
        params: list = []
        if tenant_id:
            where_clauses.append("tenant_id = %s")
            params.append(tenant_id)
        if date_from:
            where_clauses.append("created_at >= %s")
            params.append(f"{date_from} 00:00:00")
        if date_to:
            where_clauses.append("created_at <= %s")
            params.append(f"{date_to} 23:59:59")
        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

        offset = (page - 1) * page_size
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) AS cnt FROM tenant_recharges WHERE {where_sql}", params)
            total = int(cursor.fetchone()["cnt"] or 0)

            cursor.execute(
                f"""
                SELECT * FROM tenant_recharges
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (*params, page_size, offset),
            )
            items = [dict(row) for row in cursor.fetchall()]

        return {"items": items, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def get_by_id(recharge_id: int) -> Optional[Dict[str, Any]]:
        """根据主键 id 获取充值记录"""
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM tenant_recharges WHERE id = {placeholder}",
                (recharge_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def delete(recharge_id: int) -> Optional[Dict[str, Any]]:
        """删除充值记录，同事务原子回扣 tenants.credit_balance

        Returns:
            被删除的记录字典（含 tenant_id / credits）；记录不存在返回 None
        """
        placeholder = "%s"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"SELECT * FROM tenant_recharges WHERE id = {placeholder}",
                    (recharge_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                record = dict(row)
                credits = int(record.get("credits") or 0)
                tenant_id = record.get("tenant_id")

                cursor.execute(
                    f"DELETE FROM tenant_recharges WHERE id = {placeholder}",
                    (recharge_id,),
                )

                # 同事务原子回扣余额
                if tenant_id and credits > 0:
                    cursor.execute(
                        "UPDATE tenants SET credit_balance = credit_balance - %s WHERE tenant_id = %s",
                        (credits, tenant_id),
                    )

                conn.commit()
                logger.info(
                    f"Recharge deleted: id={recharge_id}, tenant={tenant_id}, credits={credits}"
                )
                return record
            except Exception as e:
                logger.error(f"Failed to delete recharge: {e}")
                return None

    @staticmethod
    def stats(tenant_id: str = None) -> Dict[str, Any]:
        """汇总统计：总充值金额、总积分、最近 7 天趋势"""
        where_sql = "WHERE tenant_id = %s" if tenant_id else "WHERE TRUE"
        params: list = [tenant_id] if tenant_id else []

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT
                    COALESCE(SUM(amount_yuan), 0) AS total_amount_yuan,
                    COALESCE(SUM(credits), 0) AS total_credits,
                    COUNT(*) AS total_count
                FROM tenant_recharges
                {where_sql}
                """,
                params,
            )
            row = cursor.fetchone() or {}
            total_amount_yuan = float(row.get("total_amount_yuan") or 0)
            total_credits = int(row.get("total_credits") or 0)
            total_count = int(row.get("total_count") or 0)

            # 最近 7 天趋势
            cursor.execute(
                f"""
                SELECT
                    DATE(created_at) AS date,
                    COALESCE(SUM(amount_yuan), 0) AS amount_yuan,
                    COALESCE(SUM(credits), 0) AS credits,
                    COUNT(*) AS count
                FROM tenant_recharges
                {where_sql}
                  AND created_at >= CURRENT_DATE - INTERVAL '6 days'
                GROUP BY DATE(created_at)
                ORDER BY DATE(created_at) ASC
                """,
                params,
            )
            trend = [
                {
                    "date": str(r["date"]) if r.get("date") else None,
                    "amount_yuan": float(r.get("amount_yuan") or 0),
                    "credits": int(r.get("credits") or 0),
                    "count": int(r.get("count") or 0),
                }
                for r in cursor.fetchall()
            ]

        return {
            "total_amount_yuan": total_amount_yuan,
            "total_credits": total_credits,
            "total_count": total_count,
            "recent_7d_trend": trend,
        }
