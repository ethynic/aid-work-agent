"""
SaaS 企业信息管理 API

路由：/api/saas/tenants/*
- 获取企业信息
- 更新企业信息
- 概览统计
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional
from loguru import logger

from src.saas.api.tenant_auth import require_admin
from src.saas.db.tenant_db import TenantDB
from src.config.settings import settings

router = APIRouter(prefix="/api/saas/tenants", tags=["SaaS 企业管理"])


# ============== 请求模型 ==============

class TenantUpdateRequest(BaseModel):
    company_name: Optional[str] = Field(None, max_length=100, description="企业名称")
    contact_name: Optional[str] = Field(None, max_length=50, description="联系人姓名")
    contact_phone: Optional[str] = Field(None, max_length=20, description="联系人电话")


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
