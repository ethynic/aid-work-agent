"""
子智能体定义管理 API

提供 /api/admin/agent-definitions 路由，管理子智能体定义和 system_prompt。
"""

import json
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID
from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.config.settings import settings
from src.services.subagent_definition_service import SubagentDefinitionService
from src.prompts.prompt_registry_service import PromptRegistryService


# ============== 请求模型 ==============

class CreateDefinitionRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=100, description="智能体唯一标识")
    name: str = Field(..., min_length=1, max_length=100, description="显示名称")
    description: Optional[str] = None
    system_prompt: str = Field(..., min_length=1, description="System Prompt 内容")
    version: str = "1.0.0"
    author: Optional[str] = None
    capabilities: Optional[List[str]] = None
    triggers: Optional[Dict[str, Any]] = None
    tools: Optional[Dict[str, Any]] = None
    skills: Optional[Dict[str, Any]] = None
    context: Optional[Dict[str, Any]] = None
    delegatable_to: Optional[List[str]] = None
    allow_delegation: bool = True
    llm_provider: Optional[str] = None
    reply_style: Optional[str] = None
    business_pages: Optional[List[Dict[str, Any]]] = None
    commit_message: str = "初始版本"


class UpdateDefinitionRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    author: Optional[str] = None
    capabilities: Optional[List[str]] = None
    triggers: Optional[Dict[str, Any]] = None
    tools: Optional[Dict[str, Any]] = None
    skills: Optional[Dict[str, Any]] = None
    context: Optional[Dict[str, Any]] = None
    delegatable_to: Optional[List[str]] = None
    allow_delegation: Optional[bool] = None
    llm_provider: Optional[str] = None
    reply_style: Optional[str] = None
    business_pages: Optional[List[Dict[str, Any]]] = None
    status: Optional[str] = None


class UpdateSystemPromptRequest(BaseModel):
    content: str = Field(..., min_length=1)
    commit_message: Optional[str] = None


# ============== 工具函数 ==============

def _serialize(obj):
    """递归将不可 JSON 序列化的类型转为可序列化的值"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    return obj


def _success(data=None, **kwargs) -> JSONResponse:
    body = {"success": True}
    if data is not None:
        body["data"] = _serialize(data)
    body.update(kwargs)
    return JSONResponse(body)


def _error(error: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"success": False, "error": error}, status_code=status_code)


def _require_admin(request: Request):
    """平台管理员权限校验"""
    auth_header = request.headers.get("Authorization", "")
    logger.debug(f"agent-definitions auth: Authorization={auth_header[:20] + '...' if auth_header else 'MISSING'}")
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")

    admin_phones = getattr(settings, "admin", None)
    if admin_phones:
        phone_list = getattr(admin_phones, "phones", [])
        if user.get("phone", "") in phone_list:
            return user

    if user.get("role") == "platform_admin":
        return user

    raise HTTPException(status_code=403, detail="无管理员权限")


# ============== API 路由 ==============

router = APIRouter(prefix="/api/admin/agent-definitions", tags=["智能体定义管理"])


@router.get("")
async def list_definitions(
    request: Request,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    """列出子智能体定义"""
    try:
        _require_admin(request)
        result = SubagentDefinitionService.list_definitions(status, page, page_size)
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"列出子智能体定义失败: {e}")
        return _error("获取列表失败")


@router.post("")
async def create_definition(request: Request, body: CreateDefinitionRequest):
    """创建子智能体定义 + 初始 system_prompt"""
    try:
        admin = _require_admin(request)

        existing = SubagentDefinitionService.get_definition(body.agent_id)
        if existing:
            return _error(f"智能体 {body.agent_id} 已存在")

        result = SubagentDefinitionService.create_definition(
            agent_id=body.agent_id,
            name=body.name,
            system_prompt=body.system_prompt,
            description=body.description,
            version=body.version,
            author=body.author,
            capabilities=body.capabilities,
            triggers=body.triggers,
            tools=body.tools,
            skills=body.skills,
            context=body.context,
            delegatable_to=body.delegatable_to,
            allow_delegation=body.allow_delegation,
            llm_provider=body.llm_provider,
            reply_style=body.reply_style,
            business_pages=body.business_pages,
            created_by=admin.get("user_id"),
            commit_message=body.commit_message,
        )
        if not result:
            return _error("创建失败")
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建子智能体定义失败: {e}")
        return _error("创建失败")


@router.get("/{agent_id}")
async def get_definition(request: Request, agent_id: str):
    """获取子智能体定义详情"""
    try:
        _require_admin(request)
        result = SubagentDefinitionService.get_definition(agent_id)
        if not result:
            return _error(f"智能体 {agent_id} 不存在", 404)
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取子智能体定义失败: {e}")
        return _error("获取失败")


@router.put("/{agent_id}")
async def update_definition(request: Request, agent_id: str, body: UpdateDefinitionRequest):
    """更新子智能体定义（不含 system_prompt）"""
    try:
        admin = _require_admin(request)
        updates = body.model_dump(exclude_none=True)
        if not updates:
            return _error("没有需要更新的字段")

        result = SubagentDefinitionService.update_definition(
            agent_id=agent_id,
            created_by=admin.get("user_id"),
            **updates,
        )
        if not result:
            return _error(f"智能体 {agent_id} 不存在或更新失败")
        return _success({"agent_id": agent_id})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新子智能体定义失败: {e}")
        return _error("更新失败")


@router.delete("/{agent_id}")
async def delete_definition(request: Request, agent_id: str):
    """删除子智能体定义 + 关联的 Prompt"""
    try:
        _require_admin(request)
        result = SubagentDefinitionService.delete_definition(agent_id)
        if not result:
            return _error(f"智能体 {agent_id} 不存在或删除失败")
        return _success({"agent_id": agent_id})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除子智能体定义失败: {e}")
        return _error("删除失败")


@router.put("/{agent_id}/system-prompt")
async def update_system_prompt(request: Request, agent_id: str, body: UpdateSystemPromptRequest):
    """提交 system_prompt 新版本并标记 production"""
    try:
        admin = _require_admin(request)
        result = SubagentDefinitionService.update_system_prompt(
            agent_id=agent_id,
            content=body.content,
            commit_message=body.commit_message,
            created_by=admin.get("user_id"),
        )
        if not result:
            return _error(f"智能体 {agent_id} 的 system_prompt 更新失败")
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新 system_prompt 失败: {e}")
        return _error("更新失败")


# ========== Prompt 版本管理（委托给 PromptRegistryService） ==========

def _get_prompt_id(agent_id: str) -> Optional[str]:
    prompt = PromptRegistryService.get_prompt_by_scope(None, "subagent", agent_id)
    return str(prompt["id"]) if prompt else None


@router.get("/{agent_id}/versions")
async def list_versions(request: Request, agent_id: str, page: int = 1, page_size: int = 20):
    """列出 system_prompt 版本"""
    try:
        _require_admin(request)
        prompt_id = _get_prompt_id(agent_id)
        if not prompt_id:
            return _error(f"智能体 {agent_id} 无关联 Prompt", 404)
        result = PromptRegistryService.list_versions(prompt_id, page, page_size)
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"列出版本失败: {e}")
        return _error("获取版本列表失败")


@router.get("/{agent_id}/versions/{version}")
async def get_version(request: Request, agent_id: str, version: int):
    """获取特定版本"""
    try:
        _require_admin(request)
        prompt_id = _get_prompt_id(agent_id)
        if not prompt_id:
            return _error(f"智能体 {agent_id} 无关联 Prompt", 404)
        result = PromptRegistryService.get_version(prompt_id, version)
        if not result:
            return _error(f"版本 {version} 不存在", 404)
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取版本失败: {e}")
        return _error("获取版本失败")


@router.get("/{agent_id}/versions/diff")
async def diff_versions(request: Request, agent_id: str, v1: int, v2: int):
    """两个版本 diff 对比"""
    try:
        _require_admin(request)
        prompt_id = _get_prompt_id(agent_id)
        if not prompt_id:
            return _error(f"智能体 {agent_id} 无关联 Prompt", 404)
        result = PromptRegistryService.diff_versions(prompt_id, v1, v2)
        if not result:
            return _error("版本不存在", 404)
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"版本对比失败: {e}")
        return _error("版本对比失败")


@router.get("/{agent_id}/draft")
async def get_draft(request: Request, agent_id: str):
    """获取草稿"""
    try:
        _require_admin(request)
        prompt_id = _get_prompt_id(agent_id)
        if not prompt_id:
            return _error(f"智能体 {agent_id} 无关联 Prompt", 404)
        result = PromptRegistryService.get_draft(prompt_id)
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取草稿失败: {e}")
        return _error("获取草稿失败")


@router.put("/{agent_id}/draft")
async def save_draft(request: Request, agent_id: str, body: dict):
    """保存草稿"""
    try:
        admin = _require_admin(request)
        prompt_id = _get_prompt_id(agent_id)
        if not prompt_id:
            return _error(f"智能体 {agent_id} 无关联 Prompt", 404)
        result = PromptRegistryService.save_draft(
            prompt_id=prompt_id,
            content=body.get("content", ""),
            variables=body.get("variables"),
            base_version=body.get("base_version"),
            updated_by=admin.get("user_id"),
        )
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存草稿失败: {e}")
        return _error("保存草稿失败")


@router.delete("/{agent_id}/draft")
async def delete_draft(request: Request, agent_id: str):
    """删除草稿"""
    try:
        _require_admin(request)
        prompt_id = _get_prompt_id(agent_id)
        if not prompt_id:
            return _error(f"智能体 {agent_id} 无关联 Prompt", 404)
        PromptRegistryService.delete_draft(prompt_id)
        return _success()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除草稿失败: {e}")
        return _error("删除草稿失败")


@router.post("/{agent_id}/draft/commit")
async def commit_draft(request: Request, agent_id: str, body: dict):
    """提交草稿为新版本"""
    try:
        admin = _require_admin(request)
        prompt_id = _get_prompt_id(agent_id)
        if not prompt_id:
            return _error(f"智能体 {agent_id} 无关联 Prompt", 404)
        result = PromptRegistryService.commit_draft(
            prompt_id=prompt_id,
            commit_message=body.get("commit_message"),
            created_by=admin.get("user_id"),
        )
        if not result or not result.get("version"):
            return _error("提交草稿失败")
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"提交草稿失败: {e}")
        return _error("提交草稿失败")


@router.get("/{agent_id}/labels")
async def list_labels(request: Request, agent_id: str):
    """列出标签"""
    try:
        _require_admin(request)
        prompt_id = _get_prompt_id(agent_id)
        if not prompt_id:
            return _error(f"智能体 {agent_id} 无关联 Prompt", 404)
        result = PromptRegistryService.list_labels(prompt_id)
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"列出标签失败: {e}")
        return _error("获取标签失败")


@router.put("/{agent_id}/labels/{label}")
async def set_label(request: Request, agent_id: str, label: str, body: dict):
    """设置标签"""
    try:
        admin = _require_admin(request)
        prompt_id = _get_prompt_id(agent_id)
        if not prompt_id:
            return _error(f"智能体 {agent_id} 无关联 Prompt", 404)
        version = body.get("version")
        if not version:
            return _error("缺少 version 参数")
        result = PromptRegistryService.set_label(
            prompt_id=prompt_id,
            label=label,
            version=version,
            created_by=admin.get("user_id"),
        )
        if not result:
            return _error("设置标签失败")
        return _success(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置标签失败: {e}")
        return _error("设置标签失败")


@router.delete("/{agent_id}/labels/{label}")
async def delete_label(request: Request, agent_id: str, label: str):
    """删除标签"""
    try:
        _require_admin(request)
        prompt_id = _get_prompt_id(agent_id)
        if not prompt_id:
            return _error(f"智能体 {agent_id} 无关联 Prompt", 404)
        result = PromptRegistryService.delete_label(prompt_id, label)
        if not result:
            return _error("删除标签失败")
        return _success()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除标签失败: {e}")
        return _error("删除标签失败")
