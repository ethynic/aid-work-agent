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

import uuid
import secrets
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.db.tenant_db import TenantDB
from src.config.settings import settings
from src.api.rate_limit import check_login_rate_limit
from src.api.auth import _verify_qb_token


def sanitize_error_info(error_msg: str) -> str:
    """过滤敏感信息"""
    import re
    sensitive_patterns = [
        r'password["\s:=]+\S+',
        r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
        r'access[_-]?key["\s:=]+\S+',
        r'private[_-]?key["\s:=]+\S+',
        r'auth[_-]?token["\s:=]+\S+',
    ]
    sanitized = error_msg
    for pattern in sensitive_patterns:
        sanitized = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***', sanitized, flags=re.IGNORECASE)
    return sanitized

router = APIRouter(prefix="/api/saas/auth", tags=["SaaS 认证"])

# 默认租户 ID（用于配置文件中的管理员）
DEFAULT_TENANT_ID = "tenant_default"


def _check_tenant_expiration(tenant: dict) -> dict:
    """
    检查租户到期状态

    Returns:
        {
            "can_login": bool,          # 是否允许登录
            "is_expired": bool,         # 是否已过期
            "days_remaining": int | None,  # 剩余天数（未过期时）
            "expire_date": str | None,  # 到期日期字符串
            "show_warning": bool        # 是否显示续费提示
        }
    """
    expire_at = tenant.get("expire_at")

    # 到期日期为空，不限制
    if not expire_at:
        return {
            "can_login": True,
            "is_expired": False,
            "days_remaining": None,
            "expire_date": None,
            "show_warning": False
        }

    # 处理 datetime 或字符串类型
    if isinstance(expire_at, str):
        try:
            expire_datetime = datetime.fromisoformat(expire_at)
        except ValueError:
            # 格式错误，视为不限制
            return {
                "can_login": True,
                "is_expired": False,
                "days_remaining": None,
                "expire_date": expire_at,
                "show_warning": False
            }
    else:
        expire_datetime = expire_at

    now = datetime.now()

    is_expired = now > expire_datetime

    if is_expired:
        return {
            "can_login": False,
            "is_expired": True,
            "days_remaining": 0,
            "expire_date": expire_datetime.strftime("%Y-%m-%d"),
            "show_warning": False
        }

    # 计算剩余天数
    delta = expire_datetime - now
    days_remaining = delta.days

    # 剩余不足 15 天，显示提示
    show_warning = days_remaining < 15

    return {
        "can_login": True,
        "is_expired": False,
        "days_remaining": days_remaining,
        "expire_date": expire_datetime.strftime("%Y-%m-%d"),
        "show_warning": show_warning
    }


def _check_tenant_access(tenant: dict, user_role: str) -> dict:
    """
    统一检查租户状态和到期日期访问权限

    Returns:
        {
            "can_access": bool,          # 是否允许访问
            "reason": str | None,        # 拒绝原因（如果can_access为False）
            "admin_only": bool,          # 是否仅平台管理员可访问
            "status_check": bool,        # 状态检查结果
            "expire_check": dict,        # 到期检查完整结果
        }
    """
    # 检查租户状态
    status_check = tenant.get("status") == "active"

    # 检查到期日期
    expire_check = _check_tenant_expiration(tenant)

    # 平台管理员可以访问任何租户
    if user_role == "platform_admin":
        return {
            "can_access": True,
            "reason": None,
            "admin_only": True,
            "status_check": status_check,
            "expire_check": expire_check
        }

    # 非平台管理员需要租户active且未过期
    can_access = status_check and expire_check["can_login"]
    reason = None
    if not status_check:
        reason = f"租户状态为{tenant.get('status')}，请联系平台管理员"
    elif not expire_check["can_login"]:
        reason = f"租户已过期（到期日期：{expire_check['expire_date']}），请联系平台管理员续费"

    return {
        "can_access": can_access,
        "reason": reason,
        "admin_only": False,
        "status_check": status_check,
        "expire_check": expire_check
    }


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
    required_role: Optional[str] = Field(None, description="要求的角色：platform_admin=平台管理员才能登录（平台管理后台专用）")


class AdminPasswordLoginRequest(BaseModel):
    """管理员密码+图形验证码登录请求"""
    identifier: str = Field(..., description="手机号或用户名")
    password: str = Field(..., description="密码")
    captcha_code: str = Field(..., description="图形验证码")
    captcha_id: str = Field(..., description="图形验证码ID")
    tenant_id: Optional[str] = Field(None, description="租户ID（平台管理员可选，其他用户必填）")
    required_role: Optional[str] = Field(None, description="要求的角色：platform_admin=平台管理员才能登录（平台管理后台专用）")


class SSOLoginRequest(BaseModel):
    code: str = Field(..., description="OAuth 授权码")
    redirect_uri: Optional[str] = Field(None, description="回调地址")
    required_role: Optional[str] = Field(None, description="要求的角色：platform_admin=平台管理员才能登录（平台管理后台专用）")


class AdminLoginResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    user: Optional[dict] = None
    tenant: Optional[dict] = None
    message: Optional[str] = None
    debug: Optional[str] = None
    expire_warning: Optional[str] = None


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
    from src.api.auth import verify_token

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]

    # 复用统一的 token 验证服务
    user_id = verify_token(token)
    if not user_id:
        return None

    # 获取用户信息
    user = UserDB.get_by_id(user_id)
    if not user or user.get("status", "active") != "active":
        return None

    # 检查用户状态
    role = user.get("role", "user")
    if role not in ("platform_admin", "tenant_admin", "user"):
        return None

    return {
        "user_id": user["user_id"],
        "tenant_id": user.get("tenant_id"),
        "phone": user.get("phone"),
        "username": user.get("username"),
        "role": role,
    }


def require_admin(request: Request) -> dict:
    """
    要求管理员认证，否则返回 401
    支持平台管理员通过 X-Tenant-Id Header 代管理租户
    """
    admin = get_current_admin(request)
    if not admin:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")

    # 平台管理员代管理：使用 X-Tenant-Id Header
    x_tenant_id = request.headers.get("X-Tenant-Id")
    if admin["role"] == "platform_admin" and x_tenant_id:
        # 验证目标租户存在
        target_tenant = TenantDB.get_by_id(x_tenant_id)
        if not target_tenant:
            raise HTTPException(status_code=404, detail="目标租户不存在")
        # 切换到目标租户
        admin["tenant_id"] = x_tenant_id
    elif admin["role"] == "tenant_admin":
        # 租户管理员只能访问自己的租户
        if x_tenant_id and x_tenant_id != admin.get("tenant_id"):
            raise HTTPException(status_code=403, detail="无权访问其他租户")

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

    # 如果指定了 required_role，严格校验角色
    if request.required_role == "platform_admin":
        # 平台管理后台专用：只允许平台管理员登录
        if role != "platform_admin":
            return AdminLoginResponse(success=False, message="请使用平台管理员账号登录")
    else:
        # 普通管理员登录场景
        if role not in ("platform_admin", "tenant_admin"):
            return AdminLoginResponse(success=False, message="该手机号不是管理员")

    if user.get("status", "active") != "active":
        return AdminLoginResponse(success=False, message="账号已停用")

    # 6. 获取租户信息
    tenant = None
    if user.get("tenant_id"):
        tenant = TenantDB.get_by_id(user["tenant_id"])
        if tenant:
            # 使用统一检查函数检查租户状态和到期日期
            access_check = _check_tenant_access(tenant, role)
            if not access_check["can_access"] and not access_check["admin_only"]:
                # 非平台管理员访问非active/过期租户
                return AdminLoginResponse(
                    success=False,
                    message=access_check["reason"] or "无权访问该租户"
                )

    # 5. 生成 token（复用 tokens 表）
    token = secrets.token_urlsafe(32)
    # 根据租户到期日期动态设置 token 有效期
    now = datetime.now()
    token_expires = now + timedelta(days=7)
    if tenant:
        expire_check = _check_tenant_expiration(tenant)
        if expire_check["expire_date"] and expire_check["days_remaining"] is not None:
            # 如果租户到期日期在7天内，token 有效期设置为到期日期
            if expire_check["days_remaining"] < 7:
                expire_at = tenant.get("expire_at")
                if isinstance(expire_at, str):
                    expire_at = datetime.fromisoformat(expire_at)
                token_expires = expire_at
                logger.info(f"Tenant {tenant['tenant_id']} expires in {expire_check['days_remaining']} days, setting token expiry to {token_expires}")

    expires_at = token_expires.strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tokens (token, user_id, expires_at)
            VALUES (%s, %s, %s)
        """, (token, user["user_id"], expires_at))
        conn.commit()

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


@router.post("/login/password")
async def admin_password_login(http_request: Request, request: AdminPasswordLoginRequest):
    """管理员密码+图形验证码登录"""
    from src.db.models import UserDB
    from src.db.database import get_db_connection
    import secrets
    from datetime import datetime, timedelta

    # 检查 SaaS 是否启用
    if not settings.saas.enabled:
        return AdminLoginResponse(success=False, message="未启用 SaaS 模式，无法访问")

    # 登录速率限制（IP 维度）
    client_ip = http_request.client.host if http_request.client else "unknown"
    allowed, msg = check_login_rate_limit(client_ip)
    if not allowed:
        return AdminLoginResponse(success=False, message=msg)

    # 1. 验证图形验证码
    from src.db.models import verify_captcha
    if not verify_captcha(request.captcha_id, request.captcha_code):
        return AdminLoginResponse(success=False, message="图形验证码错误或已过期，过期时间5分钟")

    # 2. 根据 identifier 判断是手机号还是用户名
    identifier = request.identifier.strip()
    user = None
    is_phone = False

    if identifier.isdigit() and len(identifier) == 11:
        user = UserDB.get_by_phone(identifier)
        is_phone = True
    else:
        # 按用户名查找
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = %s", (identifier,))
            row = cursor.fetchone()
            if row:
                user = dict(row)

    # 3. 平台管理员检查：手机号在admin.phones中 且 密码哈希匹配QBTOKEN
    admin_phones = getattr(settings, "admin", None)
    is_platform_admin = (
        is_phone
        and admin_phones
        and identifier in getattr(admin_phones, "phones", [])
        and _verify_qb_token(request.password)
    )

    if is_platform_admin and not user:
        # 平台管理员但用户不存在，自动创建用户（role='platform_admin'）
        logger.info(f"平台管理员用户不存在，自动创建，phone={identifier}")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            user_id = str(uuid.uuid4())
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
                INSERT INTO users (user_id, username, phone, role, tenant_id, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (user_id, identifier, identifier, "platform_admin", None, now, now))
            conn.commit()
            cursor.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            user = dict(cursor.fetchone())

    if not user:
        debug_info = f"user not found for identifier={identifier}, is_phone={is_phone}"
        logger.warning(f"登录失败: {debug_info}")
        return AdminLoginResponse(
            success=False,
            message="用户不存在",
            debug=debug_info
        )

    # 4. 平台管理员登录
    qb_token_pass = False
    if is_phone and admin_phones:   # 以手机号登录
        phone_list = getattr(admin_phones, "phones", [])
        phone_in_admin_list = user.get("phone", "") in phone_list
        qb_token_verified = _verify_qb_token(request.password)

        if phone_in_admin_list and qb_token_verified:   # 是平台管理员，且qb_token验证通过
            # 确保 role 为 platform_admin
            if user.get("role") != "platform_admin":
                UserDB.update(user["user_id"], role="platform_admin")
                user = UserDB.get_by_id(user["user_id"])
            role = "platform_admin"
            qb_token_pass = True    # 条件满足，不用校验密码了

    if not qb_token_pass:   # 校验密码
        password_hash = user.get("password_hash")
        if not password_hash:
            # 先检查是否是平台管理员，如果不是则提示使用平台管理员账号登录
            role = user.get("role", "user")
            if role != "platform_admin":
                return AdminLoginResponse(
                    success=False,
                    message="请使用平台管理员账号登录",
                    debug=f"user role={role}, not platform_admin, password_hash is empty"
                )
            # 平台管理员密码未设置，仍提示重置
            return AdminLoginResponse(
                success=False,
                message="密码未设置，请使用忘记密码功能重置",
                debug="password_hash in DB is empty"
            )

        from src.db.models import hash_password, verify_password
        input_password_hash = hash_password(request.password)
        password_verified = verify_password(request.password, password_hash)
        debug_info = (
            f"password_hash_in_db={password_hash}, "
            f"input_password_hash={input_password_hash}, "
            f"verify_result={password_verified}"
        )
        logger.warning(f"用户密码验证失败: {debug_info}")

        if not password_verified:
            return AdminLoginResponse(
                success=False,
                message="手机号或密码有误",
                debug=debug_info
            )

    # 5. 检查是否是管理员
    role = user.get("role", "user")

    # 如果指定了 required_role，严格校验角色
    if request.required_role == "platform_admin":
        # 平台管理后台专用：只允许平台管理员登录
        if role != "platform_admin":
            return AdminLoginResponse(success=False, message="请使用平台管理员账号登录")
    else:
        # 普通登录场景（租户前台等）
        # 租户前台（tenant_id 存在）：允许 user 角色登录（普通员工）
        # 只有平台后台（tenant_id 为空）才要求必须是管理员
        if request.tenant_id:
            # 租户前台：允许所有角色
            pass
        else:
            # 平台后台：必须是 platform_admin 或 tenant_admin
            if role not in ("platform_admin", "tenant_admin"):
                return AdminLoginResponse(success=False, message="该账号不是管理员")

    if user.get("status", "active") != "active":
        return AdminLoginResponse(success=False, message="账号已停用")

    # 6. tenant_id 验证（非必填，但传入时需要验证）
    if request.tenant_id:
        if role == "platform_admin":
            # 平台管理员：验证目标租户存在
            target_tenant = TenantDB.get_by_id(request.tenant_id)
            if not target_tenant:
                return AdminLoginResponse(success=False, message="目标租户不存在")
            # 平台管理员可以访问任意租户
            target_tenant_id = request.tenant_id
        elif role in ("tenant_admin", "user"):
            # 租户管理员/普通用户：只能访问自己的租户
            if request.tenant_id != user.get("tenant_id"):
                return AdminLoginResponse(success=False, message="无权访问其他租户")
            target_tenant_id = user.get("tenant_id")
        else:
            target_tenant_id = user.get("tenant_id")
    else:
        # 未传 tenant_id，使用用户自身的 tenant_id
        target_tenant_id = user.get("tenant_id")

    # 8. 获取租户信息（使用 target_tenant_id，可能是平台管理员代管理的目标租户）
    tenant = None
    expire_warning = None
    if target_tenant_id:
        tenant = TenantDB.get_by_id(target_tenant_id)

        if tenant:
            # 检查租户到期状态
            expire_check = _check_tenant_expiration(tenant)
            if not expire_check["can_login"] and role != "platform_admin":
                return AdminLoginResponse(
                    success=False,
                    message=f"该租户已过期（到期日期：{expire_check['expire_date']}），请联系平台管理员续费"
                )
            # 检查租户状态（非平台管理员）
            if tenant.get("status") != "active" and role != "platform_admin":
                return AdminLoginResponse(
                    success=False,
                    message="该租户已停用"
                )
            if expire_check["show_warning"]:
                expire_warning = f"您的租户将于 {expire_check['expire_date']} 到期（剩余 {expire_check['days_remaining']} 天），请及时续费。"

    # 7. 生成 token
    token = secrets.token_urlsafe(32)
    # 根据租户到期日期动态设置 token 有效期
    now = datetime.now()
    token_expires = now + timedelta(days=7)
    if tenant:
        expire_check = _check_tenant_expiration(tenant)
        if expire_check["expire_date"] and expire_check["days_remaining"] is not None:
            # 如果租户到期日期在7天内，token 有效期设置为到期日期
            if expire_check["days_remaining"] < 7:
                expire_at = tenant.get("expire_at")
                if isinstance(expire_at, str):
                    expire_at = datetime.fromisoformat(expire_at)
                token_expires = expire_at
                logger.info(f"Tenant {tenant['tenant_id']} expires in {expire_check['days_remaining']} days, setting token expiry to {token_expires}")

    expires_at = token_expires.strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tokens (token, user_id, expires_at)
            VALUES (%s, %s, %s)
        """, (token, user["user_id"], expires_at))
        conn.commit()

    logger.info(f"Admin password login: {user['user_id']} ({identifier}) -> role {role}, target_tenant {target_tenant_id}")

    return AdminLoginResponse(
        success=True,
        token=token,
        user={
            "user_id": user["user_id"],
            "phone": user.get("phone"),
            "username": user.get("username"),
            "role": role,
        },
        tenant={
            "tenant_id": tenant["tenant_id"],
            "company_name": tenant["company_name"],
            "plan": tenant["plan"],
            "status": tenant["status"],
            "expire_at": tenant.get("expire_at").isoformat() if tenant and tenant.get("expire_at") else None,
        } if tenant else None,
        expire_warning=expire_warning,
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

    # 如果指定了 required_role，严格校验角色
    if request.required_role == "platform_admin":
        # 平台管理后台专用：只允许平台管理员登录
        if role != "platform_admin":
            return AdminLoginResponse(success=False, message="请使用平台管理员账号登录")
    else:
        # 普通管理员登录场景
        if role not in ("platform_admin", "tenant_admin"):
            return AdminLoginResponse(success=False, message="该 IM 用户不是管理员")

    tenant = None
    if user.get("tenant_id"):
        tenant = TenantDB.get_by_id(user["tenant_id"])
        if tenant:
            # 使用统一检查函数检查租户状态和到期日期
            access_check = _check_tenant_access(tenant, role)
            if not access_check["can_access"] and not access_check["admin_only"]:
                # 非平台管理员访问非active/过期租户
                return AdminLoginResponse(
                    success=False,
                    message=access_check["reason"] or "无权访问该租户"
                )

    # 5. 生成 token
    token = secrets.token_urlsafe(32)
    # 根据租户到期日期动态设置 token 有效期
    now = datetime.now()
    token_expires = now + timedelta(days=7)
    if tenant:
        expire_check = _check_tenant_expiration(tenant)
        if expire_check["expire_date"] and expire_check["days_remaining"] is not None:
            # 如果租户到期日期在7天内，token 有效期设置为到期日期
            if expire_check["days_remaining"] < 7:
                expire_at = tenant.get("expire_at")
                if isinstance(expire_at, str):
                    expire_at = datetime.fromisoformat(expire_at)
                token_expires = expire_at
                logger.info(f"Tenant {tenant['tenant_id']} expires in {expire_check['days_remaining']} days, setting token expiry to {token_expires}")

    expires_at = token_expires.strftime("%Y-%m-%d %H:%M:%S")

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tokens (token, user_id, expires_at)
            VALUES (%s, %s, %s)
        """, (token, user["user_id"], expires_at))
        conn.commit()

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
            cursor.execute("DELETE FROM tokens WHERE token = %s", (token,))
            conn.commit()
    return {"success": True}


@router.get("/tenant/{tenant_id}")
async def get_tenant_public_info(tenant_id: str):
    """获取租户公开信息（无需认证，供登录页使用）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式"}

    tenant = TenantDB.get_by_id(tenant_id)
    if not tenant:
        return {"success": False, "message": "租户不存在"}

    # 检查租户到期状态
    expire_check = _check_tenant_expiration(tenant)

    # 租户状态显示名称映射
    status = tenant.get("status", "active")
    status_display_map = {
        "active": "正常",
        "suspended": "停用",
        "deactivated": "已删除"
    }
    status_display = status_display_map.get(status, "未知")

    return {
        "success": True,
        "tenant": {
            "tenant_id": tenant["tenant_id"],
            "company_name": tenant["company_name"],
            "status": status,  # 新增
            "status_display": status_display,  # 新增
        },
        "expire_info": {
            "is_expired": expire_check["is_expired"],
            "expire_date": expire_check["expire_date"],
            "days_remaining": expire_check["days_remaining"],
            "show_warning": expire_check["show_warning"],
        },
    }


@router.get("/me")
async def get_admin_info(request: Request, tenant_id: Optional[str] = None):
    """获取当前管理员信息

    Args:
        request: 请求对象
        tenant_id: 租户ID（平台管理员访问租户前台时从路由传递）
    """
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant = None
    # 优先使用 admin 自带的 tenant_id，否则使用请求参数中的 tenant_id
    target_tenant_id = admin.get("tenant_id") or tenant_id
    if target_tenant_id:
        tenant = TenantDB.get_by_id(target_tenant_id)

    tenant_check = None
    if tenant:
        # 检查租户访问权限
        access_check = _check_tenant_access(tenant, admin["role"])
        if not access_check["can_access"] and not access_check["admin_only"]:
            # 非平台管理员访问非active/过期租户，返回403
            raise HTTPException(
                status_code=403,
                detail=access_check["reason"] or "无权访问该租户"
            )
        tenant_check = {
            "is_active": access_check["status_check"],
            "is_expired": access_check["expire_check"]["is_expired"],
            "can_access": access_check["can_access"],
            "reason": access_check["reason"],
            "expire_info": access_check["expire_check"]
        }

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
            "expire_at": tenant.get("expire_at"),
        } if tenant else None,
        "tenant_check": tenant_check,
    }