"""
Prompt 版本管理 API

仅提供租户管理员路由：
- tenant_router: /api/prompts - 租户管理员，限定 scope=tenant_extra

平台管理员全量管理路由（/api/admin/prompts/*）已随前端 prompts.ts 一并移除。
"""

from typing import Optional, Dict, Any, List

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.prompts.prompt_registry_service import PromptRegistryService


# ============== 请求模型 ==============

class RegisterPromptRequest(BaseModel):
    scope: str = Field(..., pattern=r"^(subagent|skill|system_template|tenant_extra)$")
    scope_id: str = Field(..., min_length=1, max_length=200)
    tenant_id: Optional[str] = None
    prompt_type: str = "normal"
    display_name: Optional[str] = None
    description: Optional[str] = None


class CommitVersionRequest(BaseModel):
    content: str = Field(..., min_length=1)
    variables: Optional[Dict[str, Any]] = None
    llm_config: Optional[Dict[str, Any]] = None
    commit_message: Optional[str] = None


class SaveDraftRequest(BaseModel):
    content: str = Field(..., min_length=1)
    variables: Optional[Dict[str, Any]] = None
    base_version: Optional[int] = None


class SetLabelRequest(BaseModel):
    version: int = Field(..., ge=1)


class CommitDraftRequest(BaseModel):
    commit_message: Optional[str] = None
    variables: Optional[Dict[str, Any]] = None
    llm_config: Optional[Dict[str, Any]] = None


# ============== 工具函数 ==============

def _success_response(data=None, **kwargs) -> JSONResponse:
    body = {"success": True}
    if data is not None:
        body["data"] = data
    body.update(kwargs)
    return JSONResponse(body)


def _error_response(error: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"success": False, "error": error}, status_code=status_code)


def _serialize_record(record: Optional[Dict]) -> Optional[Dict]:
    """将数据库记录中的 UUID 和 timestamp 转为字符串"""
    if not record:
        return None
    result = {}
    for k, v in record.items():
        if v is None:
            result[k] = None
        else:
            result[k] = str(v)
    return result


def _serialize_records(records: List[Dict]) -> List[Dict]:
    return [_serialize_record(r) for r in records]


# ============== 租户管理员 API ==============

tenant_router = APIRouter(prefix="/api/prompts", tags=["租户 Prompt 管理"])


def _require_tenant_admin(request: Request) -> dict:
    """租户管理员权限校验，返回用户信息（含 tenant_id）"""
    from src.saas.api.tenant_auth import require_admin
    return require_admin(request)


def _get_tenant_id(request: Request) -> str:
    """从请求中获取 tenant_id"""
    user = _require_tenant_admin(request)
    tenant_id = user.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="用户无租户关联")
    return tenant_id


@tenant_router.get("/")
async def tenant_list_prompts(
    request: Request,
    page: int = 1,
    page_size: int = 20,
):
    try:
        tenant_id = _get_tenant_id(request)
        result = PromptRegistryService.list_prompts(
            tenant_id=tenant_id, scope="tenant_extra", page=page, page_size=page_size
        )
        result["items"] = _serialize_records(result["items"])
        return _success_response(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list tenant prompts: {e}")
        return _error_response("获取 Prompt 列表失败")


@tenant_router.post("/")
async def tenant_create_prompt(request: Request, body: RegisterPromptRequest):
    try:
        tenant_id = _get_tenant_id(request)
        user = _require_tenant_admin(request)
        # 强制 scope=tenant_extra 和 tenant_id
        result = PromptRegistryService.register_prompt(
            scope="tenant_extra",
            scope_id=body.scope_id,
            tenant_id=tenant_id,
            prompt_type="normal",
            display_name=body.display_name,
            description=body.description,
            created_by=user.get("user_id"),
        )
        if not result:
            return _error_response("创建 Prompt 失败")
        return _success_response(_serialize_record(result))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create tenant prompt: {e}")
        return _error_response("创建 Prompt 失败")


@tenant_router.get("/{scope_id}")
async def tenant_get_prompt(request: Request, scope_id: str):
    try:
        tenant_id = _get_tenant_id(request)
        result = PromptRegistryService.get_prompt_by_scope(tenant_id, "tenant_extra", scope_id)
        if not result:
            return _error_response("Prompt 不存在", 404)
        return _success_response(_serialize_record(result))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get tenant prompt: {e}")
        return _error_response("获取 Prompt 失败")


@tenant_router.post("/{scope_id}/versions")
async def tenant_commit_version(request: Request, scope_id: str, body: CommitVersionRequest):
    try:
        tenant_id = _get_tenant_id(request)
        user = _require_tenant_admin(request)
        registry = PromptRegistryService.get_prompt_by_scope(tenant_id, "tenant_extra", scope_id)
        if not registry:
            return _error_response("Prompt 不存在", 404)
        result = PromptRegistryService.commit_version(
            prompt_id=str(registry["id"]),
            content=body.content,
            variables=body.variables,
            model_config=body.llm_config,
            commit_message=body.commit_message,
            created_by=user.get("user_id"),
        )
        return _success_response({
            "version": _serialize_record(result.get("version")),
            "dedup": result.get("dedup", False),
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to commit tenant version: {e}")
        return _error_response("提交版本失败")


@tenant_router.get("/{scope_id}/versions")
async def tenant_list_versions(
    request: Request, scope_id: str, page: int = 1, page_size: int = 20
):
    try:
        tenant_id = _get_tenant_id(request)
        registry = PromptRegistryService.get_prompt_by_scope(tenant_id, "tenant_extra", scope_id)
        if not registry:
            return _error_response("Prompt 不存在", 404)
        result = PromptRegistryService.list_versions(str(registry["id"]), page, page_size)
        result["items"] = _serialize_records(result["items"])
        return _success_response(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list tenant versions: {e}")
        return _error_response("获取版本列表失败")


@tenant_router.get("/{scope_id}/draft")
async def tenant_get_draft(request: Request, scope_id: str):
    try:
        tenant_id = _get_tenant_id(request)
        registry = PromptRegistryService.get_prompt_by_scope(tenant_id, "tenant_extra", scope_id)
        if not registry:
            return _error_response("Prompt 不存在", 404)
        result = PromptRegistryService.get_draft(str(registry["id"]))
        return _success_response(_serialize_record(result))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get tenant draft: {e}")
        return _error_response("获取草稿失败")


@tenant_router.put("/{scope_id}/draft")
async def tenant_save_draft(request: Request, scope_id: str, body: SaveDraftRequest):
    try:
        tenant_id = _get_tenant_id(request)
        user = _require_tenant_admin(request)
        registry = PromptRegistryService.get_prompt_by_scope(tenant_id, "tenant_extra", scope_id)
        if not registry:
            return _error_response("Prompt 不存在", 404)
        result = PromptRegistryService.save_draft(
            prompt_id=str(registry["id"]),
            content=body.content,
            variables=body.variables,
            base_version=body.base_version,
            updated_by=user.get("user_id"),
        )
        if not result:
            return _error_response("保存草稿失败")
        return _success_response(_serialize_record(result))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save tenant draft: {e}")
        return _error_response("保存草稿失败")


@tenant_router.post("/{scope_id}/draft/commit")
async def tenant_commit_draft(request: Request, scope_id: str, body: CommitDraftRequest):
    try:
        tenant_id = _get_tenant_id(request)
        user = _require_tenant_admin(request)
        registry = PromptRegistryService.get_prompt_by_scope(tenant_id, "tenant_extra", scope_id)
        if not registry:
            return _error_response("Prompt 不存在", 404)
        result = PromptRegistryService.commit_draft(
            prompt_id=str(registry["id"]),
            commit_message=body.commit_message,
            created_by=user.get("user_id"),
            variables=body.variables,
            model_config=body.llm_config,
        )
        if not result or not result.get("version"):
            return _error_response("提交草稿失败")
        return _success_response({
            "version": _serialize_record(result.get("version")),
            "dedup": result.get("dedup", False),
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to commit tenant draft: {e}")
        return _error_response("提交草稿失败")


@tenant_router.get("/{scope_id}/labels")
async def tenant_list_labels(request: Request, scope_id: str):
    try:
        tenant_id = _get_tenant_id(request)
        registry = PromptRegistryService.get_prompt_by_scope(tenant_id, "tenant_extra", scope_id)
        if not registry:
            return _error_response("Prompt 不存在", 404)
        result = PromptRegistryService.list_labels(str(registry["id"]))
        return _success_response(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list tenant labels: {e}")
        return _error_response("获取标签列表失败")


@tenant_router.put("/{scope_id}/labels/{label}")
async def tenant_set_label(
    request: Request, scope_id: str, label: str, body: SetLabelRequest
):
    try:
        tenant_id = _get_tenant_id(request)
        user = _require_tenant_admin(request)
        registry = PromptRegistryService.get_prompt_by_scope(tenant_id, "tenant_extra", scope_id)
        if not registry:
            return _error_response("Prompt 不存在", 404)
        result = PromptRegistryService.set_label(
            prompt_id=str(registry["id"]),
            label=label,
            version=body.version,
            created_by=user.get("user_id"),
        )
        if not result:
            return _error_response("设置标签失败")
        return _success_response(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to set tenant label: {e}")
        return _error_response("设置标签失败")
