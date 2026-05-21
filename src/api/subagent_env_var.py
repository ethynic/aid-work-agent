"""
子智能体环境变量管理 API。

提供子智能体级别环境变量的 CRUD 接口，按租户隔离。
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from loguru import logger

from src.db.subagent_env_var import SubagentEnvVarDB
from src.saas.api.tenant_auth import require_admin
from src.saas.context import get_current_tenant_id


router = APIRouter(prefix="/api/saas/tenant/subagent-env-vars", tags=["subagent-env-vars"])


# ============== 请求模型 ==============

class EnvVarItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="环境变量名")
    value: str = Field(default="", description="环境变量值")
    description: Optional[str] = Field(None, max_length=200, description="变量描述")


class BatchSetEnvVarsRequest(BaseModel):
    vars: List[EnvVarItem] = Field(..., description="环境变量列表（全量覆盖）")


# ============== API 路由 ==============

@router.get("")
async def list_all_env_vars(request_admin: dict = Depends(require_admin)):
    """列出当前租户所有子智能体的环境变量"""
    try:
        tenant_id = get_current_tenant_id()
        env_vars = SubagentEnvVarDB.get_all_vars_for_tenant(tenant_id)
        return {"success": True, "data": env_vars}
    except Exception as e:
        logger.error(f"列出环境变量失败: {e}")
        return {"success": False, "error": "获取环境变量列表失败"}


@router.get("/{subagent_name}")
async def list_subagent_env_vars(
    subagent_name: str,
    request_admin: dict = Depends(require_admin),
):
    """列出某子智能体的环境变量"""
    try:
        tenant_id = get_current_tenant_id()
        env_vars = SubagentEnvVarDB.get_vars(tenant_id, subagent_name)
        return {"success": True, "data": env_vars}
    except Exception as e:
        logger.error(f"列出环境变量失败: {e}")
        return {"success": False, "error": "获取环境变量列表失败"}


@router.put("/{subagent_name}")
async def batch_set_env_vars(
    subagent_name: str,
    req: BatchSetEnvVarsRequest,
    request_admin: dict = Depends(require_admin),
):
    """批量设置某子智能体的环境变量（全量覆盖）"""
    try:
        tenant_id = get_current_tenant_id()
        vars_list = [v.model_dump() for v in req.vars]
        success = SubagentEnvVarDB.batch_set(tenant_id, subagent_name, vars_list)
        if success:
            return {"success": True, "message": f"已设置 {len(vars_list)} 个环境变量"}
        return {"success": False, "error": "设置环境变量失败"}
    except Exception as e:
        logger.error(f"设置环境变量失败: {e}")
        return {"success": False, "error": "设置环境变量失败"}


@router.delete("/{subagent_name}/{var_name}")
async def delete_env_var(
    subagent_name: str,
    var_name: str,
    request_admin: dict = Depends(require_admin),
):
    """删除一条环境变量"""
    try:
        tenant_id = get_current_tenant_id()
        success = SubagentEnvVarDB.delete_var(tenant_id, subagent_name, var_name)
        if success:
            return {"success": True, "message": "环境变量删除成功"}
        return {"success": False, "error": "环境变量不存在或删除失败"}
    except Exception as e:
        logger.error(f"删除环境变量失败: {e}")
        return {"success": False, "error": "删除环境变量失败"}
