"""
回复风格管理 API

路由：/api/saas/reply-styles/*
- 租户风格管理：/api/saas/reply-styles/*（tenant_admin）
- 系统内置风格管理：/api/saas/reply-styles/system/*（platform_admin）
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.api.tenant_auth import require_admin
from src.saas.db.reply_style_db import ReplyStyleDB, SYSTEM_TENANT
from src.config.settings import settings

router = APIRouter(prefix="/api/saas/reply-styles", tags=["SaaS 回复风格管理"])


class StyleCreateRequest(BaseModel):
    style_id: str = Field(..., min_length=1, max_length=50, description="风格标识，创建后不可修改")
    name: str = Field(..., min_length=1, max_length=50, description="风格名称")
    description: Optional[str] = Field(None, max_length=200, description="风格描述")
    content: str = Field(..., min_length=1, description="风格内容（Markdown）")


class StyleUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50, description="风格名称")
    description: Optional[str] = Field(None, max_length=200, description="风格描述")
    content: Optional[str] = Field(None, min_length=1, description="风格内容（Markdown）")


def _require_platform_admin(request: Request) -> dict:
    """要求平台管理员权限"""
    admin = require_admin(request)
    if admin.get("role") != "platform_admin":
        raise HTTPException(status_code=403, detail="需要平台管理员权限")
    return admin


def _find_style_for_tenant(style_id: str, tenant_id: str):
    """查找风格：先查租户自定义，再查系统内置"""
    style = ReplyStyleDB.get_active(style_id, tenant_id)
    if not style:
        style = ReplyStyleDB.get_active(style_id, SYSTEM_TENANT)
    return style


def _format_style(style: dict) -> dict:
    """格式化风格返回"""
    return {
        "style_id": style["style_id"],
        "tenant_id": style["tenant_id"],
        "name": style["name"],
        "description": style.get("description"),
        "content": style["content"],
        "version": style["version"],
        "is_active": style["is_active"],
        "is_system": style["tenant_id"] == SYSTEM_TENANT,
        "created_at": str(style["created_at"]) if style.get("created_at") else None,
        "updated_at": str(style["updated_at"]) if style.get("updated_at") else None,
    }


def _trigger_reload():
    """触发 StyleManager 热更新"""
    try:
        from src.prompts.style_manager import get_style_manager
        get_style_manager().reload()
    except Exception as e:
        logger.warning(f"Failed to trigger StyleManager reload: {e}")


# ==================== 系统内置风格管理（platform_admin）====================
# 注意：固定路径 /system/* 必须在动态路径 /{style_id} 之前注册

@router.get("/system/list")
async def list_system_styles(request: Request):
    """列出所有系统内置风格"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    _require_platform_admin(request)
    styles = ReplyStyleDB.list_system_styles()

    result = []
    for s in styles:
        result.append({
            "style_id": s["style_id"],
            "name": s["name"],
            "description": s.get("description"),
            "version": s["version"],
            "is_system": True,
            "created_at": str(s["created_at"]) if s.get("created_at") else None,
            "updated_at": str(s["updated_at"]) if s.get("updated_at") else None,
        })

    return {"success": True, "styles": result}


@router.post("/system/create")
async def create_system_style(request: Request, body: StyleCreateRequest):
    """新增系统内置风格"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    _require_platform_admin(request)

    if ReplyStyleDB.exists(body.style_id, SYSTEM_TENANT):
        raise HTTPException(status_code=400, detail="风格标识已存在")

    style = ReplyStyleDB.create(
        style_id=body.style_id,
        tenant_id=SYSTEM_TENANT,
        name=body.name,
        content=body.content,
        description=body.description,
    )
    if not style:
        raise HTTPException(status_code=500, detail="创建风格失败")

    _trigger_reload()
    return {"success": True, "style": _format_style(style)}


@router.get("/system/{style_id}")
async def get_system_style(style_id: str, request: Request):
    """获取系统内置风格详情"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    _require_platform_admin(request)
    style = ReplyStyleDB.get_active(style_id, SYSTEM_TENANT)
    if not style:
        raise HTTPException(status_code=404, detail="风格不存在")
    return {"success": True, "style": _format_style(style)}


@router.put("/system/{style_id}")
async def update_system_style(style_id: str, request: Request, body: StyleUpdateRequest):
    """更新系统内置风格（创建新版本）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    _require_platform_admin(request)

    existing = ReplyStyleDB.get_active(style_id, SYSTEM_TENANT)
    if not existing:
        raise HTTPException(status_code=404, detail="风格不存在")

    name = body.name or existing["name"]
    description = body.description if body.description is not None else existing.get("description")
    content = body.content or existing["content"]

    try:
        style = ReplyStyleDB.create_new_version(
            style_id=style_id,
            tenant_id=SYSTEM_TENANT,
            name=name,
            content=content,
            description=description,
        )
    except Exception as e:
        logger.opt(exception=True).error(f"Failed to update system style {style_id}: {e}")
        raise HTTPException(status_code=500, detail=f"更新风格失败: {e}")

    if not style:
        raise HTTPException(status_code=500, detail="更新风格失败：创建新版本后未找到激活记录")

    _trigger_reload()
    return {"success": True, "style": _format_style(style)}


@router.delete("/system/{style_id}")
async def delete_system_style(style_id: str, request: Request):
    """删除系统内置风格"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    _require_platform_admin(request)

    if not ReplyStyleDB.exists(style_id, SYSTEM_TENANT):
        raise HTTPException(status_code=404, detail="风格不存在")

    success = ReplyStyleDB.delete_all_versions(style_id, SYSTEM_TENANT)
    if success:
        _trigger_reload()
    return {"success": success}


@router.get("/system/{style_id}/versions")
async def list_system_versions(style_id: str, request: Request):
    """列出系统内置风格的版本历史"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    _require_platform_admin(request)

    versions = ReplyStyleDB.list_versions(style_id, SYSTEM_TENANT)
    result = []
    for v in versions:
        result.append({
            "version": v["version"],
            "is_active": v["is_active"],
            "content": v["content"],
            "name": v["name"],
            "created_at": str(v["created_at"]) if v.get("created_at") else None,
        })

    return {"success": True, "versions": result}


@router.post("/system/{style_id}/versions/{version}/activate")
async def activate_system_version(style_id: str, version: int, request: Request):
    """激活系统内置风格的指定版本"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    _require_platform_admin(request)

    success = ReplyStyleDB.activate_version(style_id, SYSTEM_TENANT, version)
    if not success:
        raise HTTPException(status_code=400, detail="激活版本失败，版本号可能不存在")

    _trigger_reload()
    return {"success": True}


# ==================== 租户风格管理（tenant_admin）====================

@router.get("")
async def list_styles(request: Request):
    """列出当前租户的风格 + 系统内置风格"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]
    styles = ReplyStyleDB.list_active_by_tenant(tenant_id)

    result = []
    for s in styles:
        result.append({
            "style_id": s["style_id"],
            "name": s["name"],
            "description": s.get("description"),
            "version": s["version"],
            "is_system": s["tenant_id"] == SYSTEM_TENANT,
            "created_at": str(s["created_at"]) if s.get("created_at") else None,
            "updated_at": str(s["updated_at"]) if s.get("updated_at") else None,
        })

    return {"success": True, "styles": result}


@router.post("")
async def create_style(request: Request, body: StyleCreateRequest):
    """新增风格"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    if ReplyStyleDB.exists(body.style_id, tenant_id):
        raise HTTPException(status_code=400, detail="风格标识已存在")

    style = ReplyStyleDB.create(
        style_id=body.style_id,
        tenant_id=tenant_id,
        name=body.name,
        content=body.content,
        description=body.description,
    )
    if not style:
        raise HTTPException(status_code=500, detail="创建风格失败")

    _trigger_reload()
    return {"success": True, "style": _format_style(style)}


@router.get("/{style_id}")
async def get_style(style_id: str, request: Request):
    """获取风格详情（当前激活版本）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    style = _find_style_for_tenant(style_id, tenant_id)
    if not style:
        raise HTTPException(status_code=404, detail="风格不存在")

    return {"success": True, "style": _format_style(style)}


@router.put("/{style_id}")
async def update_style(style_id: str, request: Request, body: StyleUpdateRequest):
    """更新风格（创建新版本）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    if ReplyStyleDB.is_system_style(style_id):
        raise HTTPException(status_code=403, detail="系统内置风格不可编辑")

    existing = _find_style_for_tenant(style_id, tenant_id)
    if not existing:
        raise HTTPException(status_code=404, detail="风格不存在")

    actual_tenant = existing["tenant_id"]

    name = body.name or existing["name"]
    description = body.description if body.description is not None else existing.get("description")
    content = body.content or existing["content"]

    try:
        style = ReplyStyleDB.create_new_version(
            style_id=style_id,
            tenant_id=actual_tenant,
            name=name,
            content=content,
            description=description,
        )
    except Exception as e:
        logger.opt(exception=True).error(f"Failed to update tenant style {style_id}: {e}")
        raise HTTPException(status_code=500, detail=f"更新风格失败: {e}")

    if not style:
        raise HTTPException(status_code=500, detail="更新风格失败：创建新版本后未找到激活记录")

    _trigger_reload()
    return {"success": True, "style": _format_style(style)}


@router.delete("/{style_id}")
async def delete_style(style_id: str, request: Request):
    """删除风格（所有版本）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    if ReplyStyleDB.is_system_style(style_id):
        raise HTTPException(status_code=403, detail="系统内置风格不可删除")

    if not ReplyStyleDB.exists(style_id, tenant_id):
        raise HTTPException(status_code=404, detail="风格不存在")

    success = ReplyStyleDB.delete_all_versions(style_id, tenant_id)
    if success:
        _trigger_reload()
    return {"success": success}


@router.get("/{style_id}/versions")
async def list_versions(style_id: str, request: Request):
    """列出版本历史"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    style = _find_style_for_tenant(style_id, tenant_id)
    if not style:
        raise HTTPException(status_code=404, detail="风格不存在")

    versions = ReplyStyleDB.list_versions(style_id, style["tenant_id"])
    result = []
    for v in versions:
        result.append({
            "version": v["version"],
            "is_active": v["is_active"],
            "content": v["content"],
            "name": v["name"],
            "created_at": str(v["created_at"]) if v.get("created_at") else None,
        })

    return {"success": True, "versions": result}


@router.post("/{style_id}/versions/{version}/activate")
async def activate_version(style_id: str, version: int, request: Request):
    """激活指定版本（回滚）"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    tenant_id = admin["tenant_id"]

    if ReplyStyleDB.is_system_style(style_id):
        raise HTTPException(status_code=403, detail="系统内置风格不可修改版本")

    style = _find_style_for_tenant(style_id, tenant_id)
    if not style:
        raise HTTPException(status_code=404, detail="风格不存在")

    success = ReplyStyleDB.activate_version(style_id, style["tenant_id"], version)
    if not success:
        raise HTTPException(status_code=400, detail="激活版本失败，版本号可能不存在")

    _trigger_reload()
    return {"success": True}


@router.post("/reload")
async def reload_styles(request: Request):
    """手动触发 StyleManager 热更新"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    require_admin(request)
    _trigger_reload()
    return {"success": True}
