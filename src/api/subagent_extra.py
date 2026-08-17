"""子智能体租户定制 extra.md 管理 API（Phase 4 起纯 DB 驱动）

提供 extra.md 的读取、保存、删除接口，供前端 Markdown 编辑器使用。
内容存入 prompt_versions 表（scope='tenant_extra'），不再读写文件系统。
"""

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from loguru import logger

from src.api.auth import get_current_user
from src.prompts.prompt_registry_service import PromptRegistryService


router = APIRouter(prefix="/api/v1/subagents", tags=["subagent-extra"])


class ExtraMdRequest(BaseModel):
    """extra.md 内容请求"""
    content: str = Field(..., description="extra.md 文件内容（Markdown 格式）")


def _resolve_tenant_id(request: Request) -> Optional[str]:
    """从请求中解析 tenant_id"""
    # 优先从 request.state 获取（中间件已设置）
    tenant_id = getattr(request.state, 'tenant_id', None)
    if tenant_id:
        return tenant_id

    # 从用户信息中获取
    user = get_current_user(request)
    if user and user.get('tenant_id'):
        return user['tenant_id']

    return None


def _resolve_user_id(request: Request) -> Optional[str]:
    """从请求中解析 user_id（用于审计字段 created_by）"""
    user = get_current_user(request)
    if user:
        return user.get('user_id') or user.get('sub') or user.get('id')
    return None


def _get_or_create_prompt(subagent_name: str, tenant_id: str, user_id: Optional[str] = None) -> Optional[dict]:
    """获取或创建 tenant_extra prompt 注册记录"""
    scope_id = f"extra:{subagent_name}:{tenant_id}"
    # 先查是否存在
    existing = PromptRegistryService.get_prompt_by_scope(tenant_id, "tenant_extra", scope_id)
    if existing:
        return existing
    # 不存在则创建
    return PromptRegistryService.register_prompt(
        scope="tenant_extra",
        scope_id=scope_id,
        tenant_id=tenant_id,
        display_name=f"租户定制-{subagent_name}",
        description=f"租户 {tenant_id} 对子智能体 {subagent_name} 的定制 Prompt",
        created_by=user_id,
    )


@router.get("/{subagent_name}/extra")
async def get_extra_md(subagent_name: str, request: Request):
    """获取当前租户的 extra.md 内容（从 DB production 版本读取）"""
    tenant_id = _resolve_tenant_id(request)
    if not tenant_id:
        return {"success": True, "content": None, "message": "无租户上下文，使用默认配置"}

    scope_id = f"extra:{subagent_name}:{tenant_id}"
    try:
        # 检查是否有注册记录
        registry = PromptRegistryService.get_prompt_by_scope(tenant_id, "tenant_extra", scope_id)
        if not registry:
            return {"success": True, "content": None, "message": "未配置租户定制，使用默认配置"}

        # 读 production 内容
        from src.prompts.prompt_resolver import prompt_resolver
        content = prompt_resolver.resolve(scope="tenant_extra", scope_id=scope_id, tenant_id=tenant_id)
        if not content:
            return {"success": True, "content": None, "message": "未配置租户定制，使用默认配置"}

        return {"success": True, "content": content.strip()}
    except Exception as e:
        logger.opt(exception=True).error(f"读取 extra.md 失败 (DB): {e}")
        raise HTTPException(status_code=500, detail="读取配置失败")


@router.put("/{subagent_name}/extra")
async def save_extra_md(subagent_name: str, body: ExtraMdRequest, request: Request):
    """保存 extra.md 内容（提交新版本 + 标记 production）

    tenant_extra 非 HIGH_RISK_SCOPE，不走 staging，直接标 production。
    """
    tenant_id = _resolve_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法确定租户ID")

    user_id = _resolve_user_id(request)

    try:
        # 1. 获取或创建 prompt 注册记录
        registry = _get_or_create_prompt(subagent_name, tenant_id, user_id)
        if not registry:
            raise HTTPException(status_code=500, detail="创建 prompt 记录失败")
        prompt_id = str(registry["id"])

        # 2. 提交新版本（SHA-256 去重，内容相同则不产生新版本）
        commit_result = PromptRegistryService.commit_version(
            prompt_id=prompt_id,
            content=body.content,
            commit_message="租户前台编辑",
            created_by=user_id,
        )
        version_record = commit_result.get("version")
        if not version_record:
            raise HTTPException(status_code=500, detail="提交版本失败")
        version_num = version_record["version"]

        # 3. 标记 production（tenant_extra 非高危，直接标）
        PromptRegistryService.set_label(
            prompt_id=prompt_id,
            label="production",
            version=version_num,
            created_by=user_id,
        )

        logger.info(
            f"保存 extra.md (DB): tenant={tenant_id}, subagent={subagent_name}, "
            f"version={version_num}, dedup={commit_result.get('dedup', False)}"
        )
        return {"success": True, "message": "保存成功", "version": version_num}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"保存 extra.md 失败 (DB): {e}")
        raise HTTPException(status_code=500, detail="保存失败")


@router.delete("/{subagent_name}/extra")
async def delete_extra_md(subagent_name: str, request: Request):
    """删除 extra.md（级联删除 prompt 注册记录 + 所有版本/标签/草稿）"""
    tenant_id = _resolve_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法确定租户ID")

    scope_id = f"extra:{subagent_name}:{tenant_id}"
    try:
        registry = PromptRegistryService.get_prompt_by_scope(tenant_id, "tenant_extra", scope_id)
        if not registry:
            # 本来就没有，等价于已删除
            return {"success": True, "message": "已恢复默认配置"}

        ok = PromptRegistryService.delete_prompt(str(registry["id"]))
        if not ok:
            raise HTTPException(status_code=500, detail="删除失败")

        logger.info(f"删除 extra.md (DB): tenant={tenant_id}, subagent={subagent_name}")
        return {"success": True, "message": "已恢复默认配置"}
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"删除 extra.md 失败 (DB): {e}")
        raise HTTPException(status_code=500, detail="删除失败")
