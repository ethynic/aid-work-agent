"""
SaaS 管理员认证 API

路由：/api/saas/auth/*
- 手机号+验证码登录
- IM 平台 SSO 登录
- 登出、获取当前管理员信息

注意：管理员统一使用 users 表存储，通过 role 字段区分：
- platform_admin: 平台管理员，tenant_id 为空
- tenant_admin: 租户管理员，tenant_id 为租户ID
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.db.tenant_db import TenantDB
from src.config.settings import settings

router = APIRouter(prefix="/api/saas/auth", tags=["SaaS 认证"])

# 默认租户 ID（用于配置文件中的管理员）
DEFAULT_TENANT_ID = "tenant_default"


def _get_or_create_default_tenant() -> Optional[dict]:
    """获取或创建默认租户"""
    tenant = TenantDB.get_by_id(DEFAULT_TENANT_ID)
    if tenant:
        return tenant

    # 创建默认租户
    logger.info("Creating default tenant for config-based admins")
    return TenantDB.create(
        tenant_id=DEFAULT_TENANT_ID,
        company_name="默认租户",
        contact_name="系统管理员",
        plan="enterprise",
    )


def _ensure_config_admin(phone: str, role: str = "platform_admin") -> Optional[dict]:
    """
    如果手机号在配置的管理员列表中，确保该管理员存在
    返回管理员信息（如果不存在或不在配置中返回 None）

    Args:
        phone: 手机号
        role: 角色类型，platform_admin 或 tenant_admin
    """
    # 获取配置中的管理员手机号列表
    admin_phones = getattr(settings, "admin", None)
    if not admin_phones:
        return None

    phones = getattr(admin_phones, "phones", None)
    if not phones:
        return None

    if phone not in phones:
        return None

    # 查找已有用户
    from src.db.models import UserDB
    user = UserDB.get_by_phone(phone)
    if user:
        # 更新 role 为 platform_admin
        UserDB.update(user["user_id"], role="platform_admin")
        return user

    # 平台管理员不需要 tenant_id
    tenant_id = None
    if role == "tenant_admin":
        tenant = _get_or_create_default_tenant()
        if not tenant:
            logger.error(f"Failed to get or create default tenant for admin {phone}")
            return None
        tenant_id = tenant["tenant_id"]

    # 创建管理员用户
    logger.info(f"Creating admin from config: {phone} with role {role}")
    user = UserDB.create(
        username="管理员",
        phone=phone,
        role=role,
        tenant_id=tenant_id,
    )
    return user


# ============== 请求/响应模型 ==============

class SendSmsRequest(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11, description="手机号")


class AdminLoginRequest(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11, description="手机号")
    code: str = Field(..., min_length=4, max_length=6, description="短信验证码")


class SSOLoginRequest(BaseModel):
    code: str = Field(..., description="OAuth 授权码")
    redirect_uri: Optional[str] = Field(None, description="回调地址")


class AdminLoginResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    user: Optional[dict] = None
    tenant: Optional[dict] = None
    message: Optional[str] = None


def _check_saas_enabled():
    """检查 SaaS 模式是否启用，未启用时返回友好响应"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式，无法访问"}
    return None


# ============== 认证依赖 ==============

def get_current_admin(request: Request) -> Optional[dict]:
    """
    从请求中获取当前管理员信息

    Returns: {"user_id", "tenant_id", "phone", "username", "role"} 或 None
    """
    from src.db.models import UserDB
    from src.db.database import get_db_connection
    import secrets

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]

    # 验证 token
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, expires_at FROM tokens WHERE token = ?
        """, (token,))
        row = cursor.fetchone()

        if not row:
            return None

        # 检查过期
        from datetime import datetime
        if datetime.now() > datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S"):
            cursor.execute("DELETE FROM tokens WHERE token = ?", (token,))
            conn.commit()
            return None

        user_id = row["user_id"]

    # 获取用户信息
    user = UserDB.get_by_id(user_id)
    if not user or user.get("status", 1) != 1:
        return None

    # 检查是否是管理员
    role = user.get("role", "user")
    if role not in ("platform_admin", "tenant_admin"):
        return None

    return {
        "user_id": user["user_id"],
        "tenant_id": user.get("tenant_id"),
        "phone": user.get("phone"),
        "username": user.get("username"),
        "role": role,
    }


def require_admin(request: Request) -> dict:
    """要求管理员认证，否则返回 401"""
    admin = get_current_admin(request)
    if not admin:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return admin


# ============== API 端点 ==============

@router.post("/sms/send")
async def send_admin_sms(request: SendSmsRequest):
    """发送管理员短信验证码"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式，无法访问"}

    from src.saas.services.sms import send_admin_sms_code

    if send_admin_sms_code(request.phone):
        return {"success": True, "message": "验证码已发送", "expires_in": 300}
    return {"success": False, "message": "验证码发送失败"}


@router.post("/login")
async def admin_login(request: AdminLoginRequest):
    """管理员手机号+验证码登录"""
    from src.db.models import UserDB
    from src.db.database import get_db_connection
    import secrets
    from datetime import datetime, timedelta

    # 检查 SaaS 是否启用
    if not settings.saas.enabled:
        return AdminLoginResponse(success=False, message="未启用 SaaS 模式，无法访问")

    from src.saas.services.sms import verify_admin_sms_code

    # 1. 验证验证码
    if not verify_admin_sms_code(request.phone, request.code):
        return AdminLoginResponse(success=False, message="验证码错误或已过期，过期时间5分钟")

    # 2. 查找用户
    user = UserDB.get_by_phone(request.phone)
    if not user:
        # 3. 检查是否是配置中的管理员手机号，尝试自动创建
        user = _ensure_config_admin(request.phone, "platform_admin")
        if not user:
            return AdminLoginResponse(success=False, message="该手机号未注册为管理员")

    # 4. 检查是否是管理员
    role = user.get("role", "user")
    if role not in ("platform_admin", "tenant_admin"):
        return AdminLoginResponse(success=False, message="该手机号不是管理员")

    if user.get("status", 1) != 1:
        return AdminLoginResponse(success=False, message="账号已停用")

    # 5. 生成 token（复用 tokens 表）
    token = f"saas_{secrets.token_urlsafe(32)}"
    expires_at = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tokens (token, user_id, expires_at)
            VALUES (?, ?, ?)
        """, (token, user["user_id"], expires_at))
        conn.commit()

    # 6. 获取租户信息
    tenant = None
    if user.get("tenant_id"):
        tenant = TenantDB.get_by_id(user["tenant_id"])

    logger.info(f"Admin login: {user['user_id']} ({request.phone}) -> role {role}, tenant {user.get('tenant_id')}")

    return AdminLoginResponse(
        success=True,
        token=token,
        user={
            "user_id": user["user_id"],
            "phone": user["phone"],
            "username": user.get("username"),
            "role": role,
        },
        tenant={
            "tenant_id": tenant["tenant_id"],
            "company_name": tenant["company_name"],
            "plan": tenant["plan"],
            "status": tenant["status"],
        } if tenant else None,
    )


@router.post("/sso/{provider}")
async def admin_sso_login(provider: str, request: SSOLoginRequest):
    """IM 平台 SSO 登录"""
    from src.db.models import UserDB
    from src.db.database import get_db_connection
    import secrets
    from datetime import datetime, timedelta

    if not settings.saas.enabled:
        return AdminLoginResponse(success=False, message="未启用 SaaS 模式，无法访问")

    from src.saas.services.sso import get_sso_provider

    sso = get_sso_provider(provider)
    if not sso:
        raise HTTPException(status_code=400, detail=f"不支持的 SSO 平台: {provider}")

    # 1. 通过 OAuth code 获取手机号
    config = {}
    phone = await sso.get_user_phone(request.code, config)

    if not phone:
        return AdminLoginResponse(success=False, message=f"SSO 登录失败：无法获取手机号")

    # 2. 匹配用户
    user = UserDB.get_by_phone(phone)
    if not user:
        # 3. 检查是否是配置中的管理员手机号
        user = _ensure_config_admin(phone, "platform_admin")
        if not user:
            return AdminLoginResponse(success=False, message="该 IM 用户未注册为管理员")

    # 4. 检查是否是管理员
    role = user.get("role", "user")
    if role not in ("platform_admin", "tenant_admin"):
        return AdminLoginResponse(success=False, message="该 IM 用户不是管理员")

    # 5. 生成 token
    token = f"saas_{secrets.token_urlsafe(32)}"
    expires_at = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tokens (token, user_id, expires_at)
            VALUES (?, ?, ?)
        """, (token, user["user_id"], expires_at))
        conn.commit()

    tenant = None
    if user.get("tenant_id"):
        tenant = TenantDB.get_by_id(user["tenant_id"])

    logger.info(f"Admin SSO login ({provider}): {user['user_id']} ({phone})")

    return AdminLoginResponse(
        success=True,
        token=token,
        user={
            "user_id": user["user_id"],
            "phone": user["phone"],
            "username": user.get("username"),
            "role": role,
        },
        tenant={
            "tenant_id": tenant["tenant_id"],
            "company_name": tenant["company_name"],
            "plan": tenant["plan"],
            "status": tenant["status"],
        } if tenant else None,
    )


@router.post("/logout")
async def admin_logout(request: Request):
    """管理员登出"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    from src.db.database import get_db_connection

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tokens WHERE token = ?", (token,))
            conn.commit()
    return {"success": True}


@router.get("/me")
async def get_admin_info(request: Request):
    """获取当前管理员信息"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant = None
    if admin.get("tenant_id"):
        tenant = TenantDB.get_by_id(admin["tenant_id"])

    return {
        "user": {
            "user_id": admin["user_id"],
            "phone": admin["phone"],
            "username": admin.get("username"),
            "role": admin["role"],
        },
        "tenant": {
            "tenant_id": tenant["tenant_id"],
            "company_name": tenant["company_name"],
            "plan": tenant["plan"],
            "status": tenant["status"],
        } if tenant else None,
    }