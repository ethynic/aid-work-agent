"""
公共认证服务

抽取 auth.py 和 tenant_auth.py 中重复的认证逻辑为统一服务方法。
"""

import uuid
from datetime import datetime
from typing import Optional

from loguru import logger

from src.db.database import get_db_connection
from src.db.models import UserDB, hash_password, verify_password
from src.config.settings import settings
from src.api.auth import _verify_qb_token


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
            cursor.execute("SELECT * FROM users WHERE username = %s", (identifier,))
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
                INSERT INTO users (user_id, username, phone, role, tenant_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (user_id, identifier, identifier, "platform_admin", None, now, now))
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
