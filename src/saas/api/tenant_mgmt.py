"""
SaaS 企业信息管理 API

路由：/api/saas/tenants/*
- 获取企业信息
- 更新企业信息
- 概览统计
- 租户增删改查（仅平台管理员）
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from loguru import logger
import re

from src.saas.api.tenant_auth import require_admin
from src.saas.db.tenant_db import TenantDB
from src.saas.db.subscription_db import SubscriptionDB
from src.saas.models.tenant import TenantCreate, TenantUpdate
from src.config.settings import settings
from src.db.models import UserDB
from src.db.database import get_db_connection

router = APIRouter(prefix="/api/saas/tenants", tags=["SaaS 企业管理"])


def _normalize_expire_date(date_str: str | None) -> datetime | None:
    """
    将日期字符串标准化为当天 23:59:59 的 datetime

    输入格式: YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS
    输出: datetime 对象（时分秒为 23:59:59）
    """
    if not date_str:
        return None

    try:
        # 如果已经是完整的 datetime 格式
        if " " in date_str or "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("T", " "))
            # 强制设置为当天 23:59:59
            return dt.replace(hour=23, minute=59, second=59, microsecond=0)
        else:
            # 只有日期部分
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.replace(hour=23, minute=59, second=59, microsecond=0)
    except ValueError:
        return None


# ============== 请求模型 ==============

class TenantUpdateRequest(BaseModel):
    company_name: Optional[str] = Field(None, max_length=100, description="企业名称")
    contact_name: Optional[str] = Field(None, max_length=50, description="联系人姓名")
    contact_phone: Optional[str] = Field(None, max_length=20, description="联系人电话")
    initial_admin_name: Optional[str] = Field(None, max_length=50, description="初始管理员姓名")
    initial_admin_phone: Optional[str] = Field(None, max_length=11, description="初始管理员手机号")


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


def is_valid_phone(phone: str) -> bool:
    """验证手机号是否为合法中国手机号（11位，以1开头）"""
    if not phone or len(phone) != 11:
        return False
    return bool(re.match(r'^1[3-9]\d{9}$', phone))


def create_initial_admin(tenant_id: str, admin_name: str, admin_phone: str) -> Optional[dict]:
    """
    创建初始租户管理员账户
    如果该租户内手机号已存在用户，则返回 None
    """
    # 检查租户内用户是否已存在
    existing_user = UserDB.get_by_phone_in_tenant(admin_phone, tenant_id)
    if existing_user:
        logger.info(f"手机号 {admin_phone} 在租户 {tenant_id} 内已存在用户，无法创建初始管理员")
        return None

    # 创建租户管理员（密码留空）
    user = UserDB.create(
        phone=admin_phone,
        username=admin_name or f"管理员{admin_phone[-4:]}",
        role="tenant_admin",
        tenant_id=tenant_id,
    )

    if user:
        logger.info(f"为租户 {tenant_id} 创建初始管理员: {admin_phone}")
        return {
            "user_id": user["user_id"],
            "phone": admin_phone,
            "username": user["username"],
            "role": user["role"],
        }
    return None


# ============== API 端点 ==============

@router.get("/me")
async def get_tenant_info(request: Request):
    """获取当前企业信息"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant = TenantDB.get_by_id(admin["tenant_id"])
    if not tenant:
        raise HTTPException(status_code=404, detail="企业不存在")
    return {"success": True, "tenant": tenant}


@router.patch("/me")
async def update_tenant_info(request: Request, body: TenantUpdateRequest):
    """更新当前企业信息"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    updates = body.model_dump(exclude_unset=True)

    if not updates:
        return {"success": False, "message": "没有需要更新的字段"}

    success = TenantDB.update(admin["tenant_id"], **updates)
    if success:
        tenant = TenantDB.get_by_id(admin["tenant_id"])
        logger.info(f"Tenant updated: {admin['tenant_id']} fields={list(updates.keys())}")
        return {"success": True, "tenant": tenant}
    return {"success": False, "message": "更新失败"}


@router.get("/me/stats")
async def get_tenant_stats(request: Request):
    """获取企业概览统计"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    stats = TenantDB.get_stats(admin["tenant_id"])
    return {"success": True, "stats": stats}


@router.get("/list")
async def list_tenants(request: Request, page: int = 1, page_size: int = 20):
    """获取租户列表（仅平台管理员，分页）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    # 只有平台管理员可以查看所有租户
    if admin.get("role") != "platform_admin":
        return {"success": False, "message": "权限不足"}

    result = TenantDB.list_tenants(page=page, page_size=page_size)
    return {"success": True, **result}


# ============== 平台管理员 - 租户 CRUD ==============

@router.post("/")
async def create_tenant(request: Request, body: TenantCreate):
    """创建租户（仅平台管理员）"""
    if not settings.saas.enabled:
        return {"success": False, "error": "未启用 SaaS 模式无法访问", "debug": "SaaS mode disabled"}

    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        return {"success": False, "error": "权限不足", "debug": "Not platform_admin"}

    try:
        # 处理到期日期
        expire_at = _normalize_expire_date(body.expire_at) if body.expire_at else None

        tenant = TenantDB.create(
            company_name=body.company_name,
            contact_name=body.contact_name,
            contact_phone=body.contact_phone,
            initial_admin_name=body.initial_admin_name,
            initial_admin_phone=body.initial_admin_phone,
            plan=body.plan,
            max_instances=body.max_instances or 5,
            max_users=body.max_users or 50,
            expire_at=expire_at,
        )
        if not tenant:
            return {"success": False, "error": "创建租户失败", "debug": "TenantDB.create returned None"}

        # 检查是否需要创建初始管理员
        admin_account = None
        if body.initial_admin_phone and is_valid_phone(body.initial_admin_phone):
            admin_account = create_initial_admin(
                tenant["tenant_id"],
                body.initial_admin_name,
                body.initial_admin_phone,
            )

        logger.info(f"租户创建成功: {tenant['tenant_id']} by admin {admin['user_id']}")

        response = {"success": True, "tenant": tenant}
        if admin_account:
            response["message"] = "租户信息保存成功，且创建初始管理员账户 {}，初始密码为空，用户可以点击'忘记密码'通过短信验证码重置密码。".format(
                admin_account["phone"]
            )
            response["admin_account"] = admin_account
        return response

    except Exception as e:
        logger.error(f"创建租户异常: {e}", exc_info=True)
        return {"success": False, "error": "创建租户失败", "debug": sanitize_error_info(str(e))}


@router.get("/{tenant_id}")
async def get_tenant(request: Request, tenant_id: str):
    """获取租户详情（仅平台管理员）"""
    if not settings.saas.enabled:
        return {"success": False, "error": "未启用 SaaS 模式无法访问", "debug": "SaaS mode disabled"}

    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        return {"success": False, "error": "权限不足", "debug": "Not platform_admin"}

    tenant = TenantDB.get_by_id(tenant_id)
    if not tenant:
        return {"success": False, "error": "租户不存在", "debug": f"Tenant {tenant_id} not found"}
    return {"success": True, "tenant": tenant}


@router.put("/{tenant_id}")
async def update_tenant(request: Request, tenant_id: str, body: TenantUpdate):
    """更新租户信息（仅平台管理员）"""
    if not settings.saas.enabled:
        return {"success": False, "error": "未启用 SaaS 模式无法访问", "debug": "SaaS mode disabled"}

    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        return {"success": False, "error": "权限不足", "debug": "Not platform_admin"}

    existing = TenantDB.get_by_id(tenant_id)
    if not existing:
        return {"success": False, "error": "租户不存在", "debug": f"Tenant {tenant_id} not found"}

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return {"success": False, "error": "没有需要更新的字段", "debug": "No fields to update"}

    # 处理 status 字段：直接使用字符串值，不做数字映射
    # 数据库按 enums.py 规范存储：active/suspended/deactivated
    if "status" in updates:
        status_val = updates["status"]
        # 验证并规范化 status 值
        valid_statuses = {"active", "suspended", "deactivated"}
        if isinstance(status_val, str) and status_val in valid_statuses:
            updates["status"] = status_val

    # 处理到期日期：标准化为当天 23:59:59
    if "expire_at" in updates:
        updates["expire_at"] = _normalize_expire_date(updates["expire_at"])

    try:
        success = TenantDB.update(tenant_id, **updates)
        if success:
            tenant = TenantDB.get_by_id(tenant_id)
            logger.info(f"租户更新成功: {tenant_id} by admin {admin['user_id']}, fields={list(updates.keys())}")

            # 检查是否需要创建初始管理员
            admin_account = None
            if body.initial_admin_phone and is_valid_phone(body.initial_admin_phone):
                admin_account = create_initial_admin(
                    tenant_id,
                    body.initial_admin_name,
                    body.initial_admin_phone,
                )

            response = {"success": True, "tenant": tenant}
            if admin_account:
                response["message"] = "租户信息保存成功，且创建初始管理员账户 {}，初始密码为空，用户可以点击'忘记密码'通过短信验证码重置密码。".format(
                    admin_account["phone"]
                )
                response["admin_account"] = admin_account
            return response
        return {"success": False, "error": "更新失败", "debug": "TenantDB.update returned False"}
    except Exception as e:
        logger.error(f"更新租户异常: {e}", exc_info=True)
        return {"success": False, "error": "更新失败", "debug": sanitize_error_info(str(e))}


@router.delete("/{tenant_id}")
async def delete_tenant(request: Request, tenant_id: str):
    """删除租户（仅平台管理员）"""
    if not settings.saas.enabled:
        return {"success": False, "error": "未启用 SaaS 模式无法访问", "debug": "SaaS mode disabled"}

    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        return {"success": False, "error": "权限不足", "debug": "Not platform_admin"}

    existing = TenantDB.get_by_id(tenant_id)
    if not existing:
        return {"success": False, "error": "租户不存在", "debug": f"Tenant {tenant_id} not found"}

    try:
        success = TenantDB.delete(tenant_id)
        if success:
            # 级联删除：删除该租户所有的数字员工授权（租户级和用户级）
            with get_db_connection() as conn:
                SubscriptionDB.delete_all_for_tenant(conn, tenant_id)
            logger.info(f"租户删除成功: {tenant_id} by admin {admin['user_id']}, permissions deleted")
            return {"success": True, "message": "删除成功"}
        return {"success": False, "error": "删除失败", "debug": "TenantDB.delete returned False"}
    except Exception as e:
        logger.error(f"删除租户异常: {e}", exc_info=True)
        return {"success": False, "error": "删除失败", "debug": sanitize_error_info(str(e))}
