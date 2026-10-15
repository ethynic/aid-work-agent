"""租户数字员工实例 API。

提供租户对数字员工的实例管理：副本列表、复制、覆盖项更新（重命名/简介/
快捷按钮/扩展提示词）、副本删除（软删）。

鉴权复用 src/saas/api/tenant_auth.py 的 require_admin（支持平台管理员
X-Tenant-Id 代管理）；租户隔离由 DB 层 fail-closed 保证。
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from src.db.tenant_agent_instance_db import TenantAgentInstanceDB
from src.saas.api.tenant_auth import require_admin
from src.saas.services import tenant_agent_instance_service as svc

router = APIRouter(prefix="/api/saas/tenant/agent-instances", tags=["tenant-agent-instances"])


# ============== 请求模型 ==============

class CreateCopyRequest(BaseModel):
    base_agent_id: str = Field(..., min_length=1, max_length=128, description="父定义 agent_id")
    display_name: str = Field(..., min_length=1, max_length=100, description="副本显示名")


class UpdateInstanceRequest(BaseModel):
    """覆盖项更新：None 表示不改，留空字符串表示清除覆盖（继承父定义）"""
    display_name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    extra_prompt: Optional[str] = None
    greeting_summary: Optional[str] = Field(None, max_length=500)
    greeting_prompts: Optional[List[Dict[str, str]]] = None


def _sanitize_greeting_prompts(prompts: Optional[Any]) -> Optional[Any]:
    """快捷按钮规范化：最多 2 组，每组 label/message 截断；非列表返回标记值跳过更新"""
    if prompts is None:
        return None
    if not isinstance(prompts, list):
        return False  # 非法类型，调用方报 400
    cleaned = []
    for item in prompts[:2]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:20]
        message = str(item.get("message") or "").strip()[:200]
        if label and message:
            cleaned.append({"label": label, "message": message})
    return cleaned


def _sanitize_greeting_summary(summary: Optional[str]) -> Optional[str]:
    return summary.strip()[:500] if isinstance(summary, str) else summary


def _row_to_item(row: Dict[str, Any]) -> Dict[str, Any]:
    is_copy = row.get("agent_id") != row.get("base_agent_id")
    return {
        "agent_id": row.get("agent_id"),
        "base_agent_id": row.get("base_agent_id"),
        "base_source": row.get("base_source"),
        "display_name": row.get("display_name"),
        "description": row.get("description"),
        "extra_prompt": row.get("extra_prompt"),
        "greeting_summary": row.get("greeting_summary"),
        "greeting_prompts": row.get("greeting_prompts"),
        "is_copy": is_copy,
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ============== API 路由 ==============

@router.get("")
async def list_instances(admin: dict = Depends(require_admin)):
    """本租户实例列表（active 行，直定制行 + 副本行）"""
    try:
        tenant_id = admin["tenant_id"]
        rows = TenantAgentInstanceDB.list_by_tenant(tenant_id)
        return {"success": True, "data": [_row_to_item(r) for r in rows]}
    except Exception as e:
        logger.opt(exception=True).error(f"获取租户数字员工实例列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取实例列表失败")


@router.post("")
async def create_copy(body: CreateCopyRequest, admin: dict = Depends(require_admin)):
    """复制基础数字员工为本租户副本（继承语义）"""
    try:
        row = svc.create_copy(
            tenant_id=admin["tenant_id"],
            base_agent_id=body.base_agent_id,
            display_name=body.display_name,
            created_by=admin["user_id"],
        )
        return {"success": True, "data": _row_to_item(row)}
    except svc.TenantAgentInstanceError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        logger.opt(exception=True).error(f"复制数字员工失败: {e}")
        raise HTTPException(status_code=500, detail="复制数字员工失败")


@router.put("/{agent_id}")
async def update_instance(agent_id: str, body: UpdateInstanceRequest, admin: dict = Depends(require_admin)):
    """更新实例覆盖项（直定制行与副本行共用）"""
    try:
        overrides: Dict[str, Any] = {}
        if body.display_name is not None:
            overrides["display_name"] = body.display_name.strip() or None
        if body.description is not None:
            overrides["description"] = body.description.strip() or None
        if body.extra_prompt is not None:
            overrides["extra_prompt"] = body.extra_prompt.strip() or None
        if body.greeting_summary is not None:
            cleaned = _sanitize_greeting_summary(body.greeting_summary)
            if cleaned is None:
                raise HTTPException(status_code=400, detail="greeting_summary 格式错误")
            overrides["greeting_summary"] = cleaned or None
        if body.greeting_prompts is not None:
            cleaned = _sanitize_greeting_prompts(body.greeting_prompts)
            if cleaned is False:
                raise HTTPException(status_code=400, detail="greeting_prompts 格式错误")
            # 空列表 = 清除覆盖（恢复 LLM 生成）；非空则要求每组完整
            if cleaned and any(not p for p in cleaned):
                raise HTTPException(status_code=400, detail="快捷按钮 label/message 不能为空")
            overrides["greeting_prompts"] = cleaned or None

        row = svc.update_overrides(admin["tenant_id"], agent_id, overrides)
        logger.info(
            f"后端日志：租户 {admin['tenant_id']} 更新数字员工实例 {agent_id} "
            f"覆盖项: {list(overrides.keys())}"
        )
        return {"success": True, "data": _row_to_item(row)}
    except svc.TenantAgentInstanceError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"更新数字员工实例失败: {e}")
        raise HTTPException(status_code=500, detail="更新实例失败")


@router.delete("/{agent_id}")
async def delete_instance(agent_id: str, admin: dict = Depends(require_admin)):
    """删除副本（软删归档）；直定制行拒绝"""
    try:
        svc.delete_copy(admin["tenant_id"], agent_id)
        return {"success": True, "message": "已删除"}
    except svc.TenantAgentInstanceError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        logger.opt(exception=True).error(f"删除数字员工副本失败: {e}")
        raise HTTPException(status_code=500, detail="删除副本失败")
