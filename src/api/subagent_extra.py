"""子智能体租户定制 extra.md 管理 API

提供 extra.md 的读取、保存、删除接口，供前端 Markdown 编辑器使用。
"""

from pathlib import Path
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from loguru import logger

from src.api.auth import get_current_user


router = APIRouter(prefix="/api/v1/subagents", tags=["subagent-extra"])

EXTRA_BASE_DIR = Path("storage/subagents")


class ExtraMdRequest(BaseModel):
    """extra.md 内容请求"""
    content: str = Field(..., description="extra.md 文件内容（Markdown 格式）")


class ExtraMdResponse(BaseModel):
    """extra.md 内容响应"""
    success: bool
    content: Optional[str] = None
    updated_at: Optional[str] = None


def _resolve_extra_path(subagent_name: str, tenant_id: str) -> Path:
    """构建 extra.md 文件路径"""
    return EXTRA_BASE_DIR / subagent_name / f"extra_{tenant_id}.md"


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


@router.get("/{subagent_name}/extra")
async def get_extra_md(subagent_name: str, request: Request):
    """获取当前租户的 extra.md 内容"""
    tenant_id = _resolve_tenant_id(request)
    if not tenant_id:
        return {"success": True, "content": None, "message": "无租户上下文，使用默认配置"}

    extra_path = _resolve_extra_path(subagent_name, tenant_id)
    if not extra_path.exists():
        return {"success": True, "content": None, "message": "未配置租户定制，使用默认配置"}

    try:
        content = extra_path.read_text(encoding='utf-8')
        return {"success": True, "content": content}
    except Exception as e:
        logger.error(f"读取 extra.md 失败: {e}")
        raise HTTPException(status_code=500, detail="读取配置失败")


@router.put("/{subagent_name}/extra")
async def save_extra_md(subagent_name: str, body: ExtraMdRequest, request: Request):
    """保存 extra.md 内容"""
    tenant_id = _resolve_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法确定租户ID")

    extra_path = _resolve_extra_path(subagent_name, tenant_id)

    try:
        extra_path.parent.mkdir(parents=True, exist_ok=True)
        extra_path.write_text(body.content, encoding='utf-8')
        logger.info(f"保存 extra.md: {extra_path}")
        return {"success": True, "message": "保存成功"}
    except Exception as e:
        logger.error(f"保存 extra.md 失败: {e}")
        raise HTTPException(status_code=500, detail="保存失败")


@router.delete("/{subagent_name}/extra")
async def delete_extra_md(subagent_name: str, request: Request):
    """删除 extra.md，恢复默认配置"""
    tenant_id = _resolve_tenant_id(request)
    if not tenant_id:
        raise HTTPException(status_code=400, detail="无法确定租户ID")

    extra_path = _resolve_extra_path(subagent_name, tenant_id)

    try:
        if extra_path.exists():
            extra_path.unlink()
            logger.info(f"删除 extra.md: {extra_path}")
        return {"success": True, "message": "已恢复默认配置"}
    except Exception as e:
        logger.error(f"删除 extra.md 失败: {e}")
        raise HTTPException(status_code=500, detail="删除失败")
