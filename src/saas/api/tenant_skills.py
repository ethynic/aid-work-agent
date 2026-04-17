"""
SaaS 租户自定义 Skill 管理 API

路由：/api/saas/skills/*
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from loguru import logger

from src.saas.api.tenant_auth import require_admin
from src.saas.services.skill_resolver import SkillResolver
from src.config.settings import settings

router = APIRouter(prefix="/api/saas/skills", tags=["SaaS Skill 管理"])


class SkillUploadRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="Skill 名称")
    content: str = Field(..., description="SKILL.md 文件内容")


class SkillUpdateRequest(BaseModel):
    content: str = Field(..., description="SKILL.md 文件内容")


@router.get("")
async def list_skills(request: Request):
    """列出租户自定义 Skills"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)
    skills = SkillResolver.list_tenant_skills(admin["tenant_id"])
    return {"success": True, "skills": skills}


@router.post("")
async def upload_skill(request: Request, body: SkillUploadRequest):
    """上传自定义 Skill"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    success = SkillResolver.save_tenant_skill(
        tenant_id=admin["tenant_id"],
        skill_name=body.name,
        content=body.content,
    )

    if success:
        return {"success": True, "message": "Skill 上传成功"}
    raise HTTPException(status_code=500, detail="Skill 上传失败")


@router.put("/{skill_name}")
async def update_skill(skill_name: str, request: Request, body: SkillUpdateRequest):
    """更新自定义 Skill"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    success = SkillResolver.save_tenant_skill(
        tenant_id=admin["tenant_id"],
        skill_name=skill_name,
        content=body.content,
    )

    if success:
        return {"success": True, "message": "Skill 更新成功"}
    raise HTTPException(status_code=500, detail="Skill 更新失败")


@router.delete("/{skill_name}")
async def delete_skill(skill_name: str, request: Request):
    """删除自定义 Skill"""
    if not settings.saas.enabled:
        return {"success": False, "message": "未启用 SaaS 模式无法访问"}

    admin = require_admin(request)

    success = SkillResolver.delete_tenant_skill(
        tenant_id=admin["tenant_id"],
        skill_name=skill_name,
    )

    if success:
        return {"success": True, "message": "Skill 已删除"}
    raise HTTPException(status_code=404, detail="Skill 不存在")
