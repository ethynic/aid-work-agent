"""
公共认证服务

抽取 auth.py 和 tenant_auth.py 中重复的认证逻辑为统一服务方法。
中性认证原语 _verify_qb_token/verify_token/fresh_web_subject 落在此处，
服务层（含 AgentRunner）统一依赖本模块，不再反向导入 src.api。
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

from src.core.cache_utils import CacheKeys, get_cached, set_cached, delete_cached
from src.db.database import get_db_connection
from src.db.models import UserDB, hash_password, verify_password
from src.config.settings import settings


def _verify_qb_token(password: str) -> bool:
    """验证平台管理员 QBTOKEN（优先使用哈希比较，兼容明文比较）"""
    qb_token_hash = getattr(settings, "qb_token_hash", "")
    if qb_token_hash:
        return verify_password(password, qb_token_hash)
    # 向后兼容：如果未配置哈希，使用明文比较（不推荐）
    qb_token = getattr(settings, "qb_token", "")
    return bool(qb_token) and password == qb_token


def authenticate_user(identifier: str, password: str, *,
                      check_captcha: bool = True) -> Optional[dict]:
    """
    核心认证逻辑：根据标识符和密码验证用户身份

    支持两种标识符：手机号（11位数字）或用户名

    返回:
        成功: {"user": dict, "is_platform_admin": bool, "auto_created": bool}
        失败: {"error": str, "user_not_found": bool}
    """
    user = None
    is_phone = identifier.isdigit() and len(identifier) == 11

    # 1. 查找用户
    if is_phone:
        user = UserDB.get_by_phone(identifier, bypass_cache=True)
    else:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # 同 username 可能跨租户存在多条记录，需显式排序避免返回顺序不确定
            cursor.execute(
                "SELECT * FROM users WHERE username = %s ORDER BY created_at DESC LIMIT 1",
                (identifier,),
            )
            row = cursor.fetchone()
            if row:
                user = dict(row)

    # 2. 检查平台管理员条件
    admin_phones = getattr(settings, "admin", None)
    is_platform_admin = (
        is_phone
        and admin_phones
        and identifier in getattr(admin_phones, "phones", [])
        and _verify_qb_token(password)
    )

    auto_created = False

    # 3. 平台管理员自动创建用户
    if is_platform_admin and not user:
        logger.info(f"平台管理员用户不存在，自动创建，phone={identifier}")
        user_id = str(uuid.uuid4())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO users (user_id, username, phone, role, tenant_id, source, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (user_id, identifier, identifier, "platform_admin", None, None, now, now))
            conn.commit()
            cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            user = dict(cursor.fetchone())
        auto_created = True

    if not user:
        return {"error": "用户不存在", "user_not_found": True}

    # 4. 验证密码
    if is_platform_admin:
        # 确保 role 为 platform_admin
        if user.get("role") != "platform_admin":
            UserDB.update(user["user_id"], role="platform_admin")
            user = UserDB.get_by_id(user["user_id"])
    else:
        # 普通用户密码校验
        password_hash = user.get("password_hash")
        if not password_hash:
            return {"error": "密码未设置，请使用忘记密码功能重置"}
        if not verify_password(password, password_hash):
            return {"error": "手机号或密码有误"}

    return {
        "user": user,
        "is_platform_admin": is_platform_admin,
        "auto_created": auto_created,
    }


def check_platform_admin(phone: str) -> bool:
    """检查手机号是否在平台管理员配置中"""
    admin_phones = getattr(settings, "admin", None)
    if not admin_phones:
        return False
    return phone in getattr(admin_phones, "phones", [])


def verify_token(token: str, auto_refresh: bool = True) -> Optional[str]:
    """
    验证令牌并返回 user_id，支持自动刷新有效期（滑动窗口）

    优先从 Redis 缓存读取，缓存未命中时查询数据库。

    Args:
        token: 认证令牌
        auto_refresh: 是否自动刷新有效期

    刷新规则：
    1. token 剩余有效期 < 3 天时自动刷新回 7 天
    2. 租户用户：刷新后的有效期不能超过租户到期日期
    3. 平台管理员：不受租户限制，直接刷新到 7 天
    """
    from src.db.models import UserDB
    from src.saas.db.tenant_db import TenantDB

    now = datetime.now()

    # 优先从 Redis 缓存读取
    cached_data = get_cached(CacheKeys.TOKEN, token)
    if cached_data is not None:
        user_id = cached_data.get("user_id")
        expires_at_str = cached_data.get("expires_at")
        if user_id and expires_at_str:
            expires_at = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
            if now <= expires_at:
                # 缓存命中且有效，跳过 DB 查询
                # 但滑动刷新仍需间歇性检查（允许每天最多触发一次刷新）
                if auto_refresh:
                    remaining_seconds = (expires_at - now).total_seconds()
                    remaining_days = remaining_seconds / 86400
                    last_refresh = cached_data.get("last_refresh_check", 0)
                    # 只有缓存中没有近期刷新记录时才执行 DB 刷新逻辑
                    if remaining_days < 3 and (now.timestamp() - last_refresh) > 86400:
                        # 标记已检查，避免频繁刷新
                        cached_data["last_refresh_check"] = now.timestamp()
                        set_cached(CacheKeys.TOKEN, token, value=cached_data, ttl=int(min(remaining_seconds, 604800)))
                        # 异步思路：此处 DB 刷新可改为后台任务，此处简化处理
                        # 实际刷新由 DB 层负责，这里仅标记
                    return user_id
                return user_id
            else:
                # 缓存显示已过期，删除缓存
                delete_cached(CacheKeys.TOKEN, token)
                return None
        else:
            # 缓存数据异常，删除重建
            delete_cached(CacheKeys.TOKEN, token)

    # 缓存未命中，查询数据库
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, expires_at FROM tokens
            WHERE token = %s
        """, (token,))
        row = cursor.fetchone()

        if not row:
            return None

        # 检查是否过期
        expires_at = row["expires_at"]
        if isinstance(expires_at, str):
            expires_at = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S")

        if now > expires_at:
            cursor.execute("DELETE FROM tokens WHERE token = %s", (token,))
            conn.commit()
            delete_cached(CacheKeys.TOKEN, token)
            return None

        user_id = row["user_id"]
        remaining_seconds = (expires_at - now).total_seconds()

        # 写入缓存（TTL 取剩余有效期和 7 天的较小值）
        cache_ttl = int(min(remaining_seconds, 604800))
        if cache_ttl > 0:
            set_cached(CacheKeys.TOKEN, token, value={
                "user_id": user_id,
                "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S") if isinstance(expires_at, datetime) else str(expires_at),
                "last_refresh_check": 0,
            }, ttl=cache_ttl)

        # 滑动有效期：如果剩余时间 < 3 天，自动刷新
        if auto_refresh:
            remaining_days = remaining_seconds / 86400

            if remaining_days < 3:
                new_expires_at = now + timedelta(days=7)

                # 租户用户：检查租户到期日期，token 有效期不能超过租户到期日
                user = UserDB.get_by_id(user_id)
                if user and user.get("tenant_id") and user.get("role") != "platform_admin":
                    tenant = TenantDB.get_by_id(user["tenant_id"])
                    if tenant and tenant.get("expire_at"):
                        tenant_expire_at = tenant["expire_at"]
                        if isinstance(tenant_expire_at, str):
                            tenant_expire_at = datetime.fromisoformat(tenant_expire_at)
                        # 取 7 天和租户到期日中较早的一个
                        if tenant_expire_at < new_expires_at:
                            new_expires_at = tenant_expire_at
                            logger.info(f"Tenant {tenant['tenant_id']} expires earlier than 7 days, token expiry limited to {new_expires_at}")

                # 更新 token 有效期
                new_expires_at_str = new_expires_at.strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute("""
                    UPDATE tokens SET expires_at = %s WHERE token = %s
                """, (new_expires_at_str, token))
                conn.commit()

                # 更新缓存
                new_remaining = (new_expires_at - now).total_seconds()
                cache_ttl = int(min(new_remaining, 604800))
                if cache_ttl > 0:
                    set_cached(CacheKeys.TOKEN, token, value={
                        "user_id": user_id,
                        "expires_at": new_expires_at_str,
                        "last_refresh_check": now.timestamp(),
                    }, ttl=cache_ttl)

                logger.debug(f"Token refreshed for user {user_id}, new expiry: {new_expires_at_str}")

        return user_id


class AuthSubjectError(Exception):
    """中性认证主体错误：HTTP 适配层据此转换为同状态码/同 detail 的传输层错误。"""

    def __init__(self, code: str, status: int):
        super().__init__(code)
        self.code = code
        self.status = status


def fresh_web_subject(authorization):
    """无刷新校验当前 Web 主体并返回用户行（含 token_expires_at）。

    verify_token(auto_refresh=False) 后再以 tokens JOIN users 的真实行二验，
    失败抛中性 AuthSubjectError；HTTP/Runner 适配层负责各自的错误转换。
    """
    token = authorization[7:] if authorization.startswith('Bearer ') else None
    user_id = verify_token(token, auto_refresh=False) if token else None
    if not user_id:
        raise AuthSubjectError('USER_UNAUTHORIZED', 401)
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute('''SELECT u.*,t.expires_at AS token_expires_at FROM tokens t
                          JOIN users u ON u.user_id=t.user_id WHERE t.token=%s''', (token,))
        row = cursor.fetchone()
        expiration = row.get('token_expires_at') if row else None
        if isinstance(expiration, str):
            expiration = datetime.fromisoformat(expiration)
        now = datetime.now(timezone.utc) if expiration and expiration.tzinfo else datetime.now()
        if not row or row['user_id'] != user_id or row['status'] != 'active' or not expiration or expiration <= now:
            raise AuthSubjectError('USER_UNAUTHORIZED', 401)
        return dict(row)
