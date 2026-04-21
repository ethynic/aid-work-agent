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
from loguru import logger

from src.saas.api.tenant_auth import require_admin
from src.saas.db.tenant_db import TenantDB
from src.saas.models.tenant import TenantCreate, TenantUpdate
from src.config.settings import settings

router = APIRouter(prefix="/api/saas/tenants", tags=["SaaS 企业管理"])


# ============== 请求模型 ==============

class TenantUpdateRequest(BaseModel):
    company_name: Optional[str] = Field(None, max_length=100, description="企业名称")
    contact_name: Optional[str] = Field(None, max_length=50, description="联系人姓名")
    contact_phone: Optional[str] = Field(None, max_length=20, description="联系人电话")


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
async def list_tenants(request: Request):
    """获取租户列表（仅平台管理员）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    # 只有平台管理员可以查看所有租户
    if admin.get("role") != "platform_admin":
        return {"success": False, "message": "权限不足"}

    # 获取所有租户（使用较大的 limit）
    tenants = TenantDB.list_tenants(limit=1000)
    return {"success": True, "tenants": tenants}


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
        tenant = TenantDB.create(
            company_name=body.company_name,
            contact_name=body.contact_name,
            contact_phone=body.contact_phone,
            plan=body.plan,
            max_instances=body.max_instances or 5,
            max_users=body.max_users or 50,
        )
        if tenant:
            logger.info(f"租户创建成功: {tenant['tenant_id']} by admin {admin['user_id']}")
            return {"success": True, "tenant": tenant}
        return {"success": False, "error": "创建租户失败", "debug": "TenantDB.create returned None"}
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
            updates["status"] = status_val  # 保持字符串格式
        elif isinstance(status_val, int):
            # 数字映射：1->active, 0->suspended, -1->deactivated
            int_to_str = {1: "active", 0: "suspended", -1: "deactivated"}
            updates["status"] = int_to_str.get(status_val, "active")

    try:
        success = TenantDB.update(tenant_id, **updates)
        if success:
            tenant = TenantDB.get_by_id(tenant_id)
            logger.info(f"租户更新成功: {tenant_id} by admin {admin['user_id']}, fields={list(updates.keys())}")
            return {"success": True, "tenant": tenant}
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
            logger.info(f"租户删除成功: {tenant_id} by admin {admin['user_id']}")
            return {"success": True, "message": "删除成功"}
        return {"success": False, "error": "删除失败", "debug": "TenantDB.delete returned False"}
    except Exception as e:
        logger.error(f"删除租户异常: {e}", exc_info=True)
        return {"success": False, "error": "删除失败", "debug": sanitize_error_info(str(e))}
