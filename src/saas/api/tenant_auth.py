"""
SaaS 管理员认证 API

路由：/api/saas/auth/*
- 手机号+验证码登录
- IM 平台 SSO 登录
- 登出、获取当前管理员信息
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.db.tenant_admin_db import TenantAdminDB, TenantAdminTokenDB
from src.saas.db.tenant_db import TenantDB

router = APIRouter(prefix="/api/saas/auth", tags=["SaaS 认证"])


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
    admin: Optional[dict] = None
    tenant: Optional[dict] = None
    message: Optional[str] = None


# ============== 认证依赖 ==============

def get_current_admin(request: Request) -> Optional[dict]:
    """
    从请求中获取当前管理员信息

    Returns: {"admin_id", "tenant_id", "phone", "name", "role"} 或 None
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]
    # 验证 token
    token_info = TenantAdminTokenDB.verify(token)
    if not token_info:
        return None

    # 获取管理员信息
    admin = TenantAdminDB.get_by_id(token_info["admin_id"])
    if not admin or admin.get("status", 1) != 1:
        return None

    return {
        "admin_id": admin["admin_id"],
        "tenant_id": admin["tenant_id"],
        "phone": admin["phone"],
        "name": admin.get("name"),
        "role": admin["role"],
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
    from src.saas.services.sms import send_admin_sms_code

    if send_admin_sms_code(request.phone):
        return {"success": True, "message": "验证码已发送", "expires_in": 300}
    return {"success": False, "message": "验证码发送失败"}


@router.post("/login")
async def admin_login(request: AdminLoginRequest):
    """管理员手机号+验证码登录"""
    from src.saas.services.sms import verify_admin_sms_code

    # 1. 验证验证码
    if not verify_admin_sms_code(request.phone, request.code):
        return AdminLoginResponse(success=False, message="验证码错误或已过期")

    # 2. 查找管理员
    admin = TenantAdminDB.get_by_phone(request.phone)
    if not admin:
        return AdminLoginResponse(success=False, message="该手机号未注册为管理员")

    if admin.get("status", 1) != 1:
        return AdminLoginResponse(success=False, message="管理员账号已停用")

    # 3. 生成 token
    token = TenantAdminTokenDB.create(admin["admin_id"], admin["tenant_id"])
    if not token:
        return AdminLoginResponse(success=False, message="登录失败，请重试")

    # 4. 获取租户信息
    tenant = TenantDB.get_by_id(admin["tenant_id"])

    logger.info(f"Admin login: {admin['admin_id']} ({request.phone}) -> tenant {admin['tenant_id']}")

    return AdminLoginResponse(
        success=True,
        token=token,
        admin={
            "admin_id": admin["admin_id"],
            "phone": admin["phone"],
            "name": admin.get("name"),
            "role": admin["role"],
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
    """
    IM 平台 SSO 登录

    provider: wecom / dingtalk / feishu
    """
    from src.saas.services.sso import get_sso_provider

    sso = get_sso_provider(provider)
    if not sso:
        raise HTTPException(status_code=400, detail=f"不支持的 SSO 平台: {provider}")

    # 1. 通过 OAuth code 获取手机号
    # TODO: 从租户渠道配置获取凭证（Phase 5 完善后可用）
    config = {}  # 占位
    phone = await sso.get_user_phone(request.code, config)

    if not phone:
        return AdminLoginResponse(success=False, message=f"SSO 登录失败：无法获取手机号")

    # 2. 匹配管理员
    admin = TenantAdminDB.get_by_phone(phone)
    if not admin:
        return AdminLoginResponse(success=False, message="该 IM 用户未注册为管理员")

    # 3. 生成 token
    token = TenantAdminTokenDB.create(admin["admin_id"], admin["tenant_id"])
    tenant = TenantDB.get_by_id(admin["tenant_id"])

    logger.info(f"Admin SSO login ({provider}): {admin['admin_id']} ({phone})")

    return AdminLoginResponse(
        success=True,
        token=token,
        admin={
            "admin_id": admin["admin_id"],
            "phone": admin["phone"],
            "name": admin.get("name"),
            "role": admin["role"],
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
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        TenantAdminTokenDB.delete(token)
    return {"success": True}


@router.get("/me")
async def get_admin_info(request: Request):
    """获取当前管理员信息"""
    admin = require_admin(request)
    tenant = TenantDB.get_by_id(admin["tenant_id"])

    return {
        "admin": {
            "admin_id": admin["admin_id"],
            "phone": admin["phone"],
            "name": admin.get("name"),
            "role": admin["role"],
        },
        "tenant": {
            "tenant_id": tenant["tenant_id"],
            "company_name": tenant["company_name"],
            "plan": tenant["plan"],
            "status": tenant["status"],
        } if tenant else None,
    }
