"""
租户间知识库共享授权 API（平台管理员）。

第一步「知识库接入」：在 B 租户基本信息中接入其他租户（A）的知识库，
建立 A->B 租户级共享授权。to_tenant_id 固定为当前编辑租户（B），
从 get_current_tenant_id() 读取。具体共享哪些分类由第二步
subagent-knowledge 关联弹框决定。
"""

from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from loguru import logger

from src.db.tenant_knowledge_share_db import TenantKnowledgeShareDB
from src.saas.api.tenant_auth import require_admin
from src.saas.context import get_current_tenant_id
from src.saas.db.tenant_db import TenantDB


router = APIRouter(prefix="/api/saas/tenant/knowledge-shares", tags=["knowledge-shares"])


# ============== 请求/响应模型 ==============

class ShareFromTenantItem(BaseModel):
    from_tenant_id: str = Field(..., min_length=1, max_length=100, description="来源租户 ID（知识库提供方）")


class SetKnowledgeSharesRequest(BaseModel):
    from_tenants: List[ShareFromTenantItem] = Field(
        default_factory=list,
        description="B 租户已接入的来源租户列表（全量覆盖）",
    )


def _company_name_of(tenant_id: str) -> str:
    """实时查来源租户公司名（无快照），查不到用租户 ID 兜底"""
    tenant = TenantDB.get_by_id(tenant_id)
    if tenant and tenant.get("company_name"):
        return tenant["company_name"]
    return tenant_id


# ============== API 路由 ==============

@router.get("")
async def get_knowledge_shares(
    request_admin: dict = Depends(require_admin),
):
    """获取 B 租户已接入的来源租户列表（含公司名，实时查 tenants 表）"""
    try:
        to_tenant_id = get_current_tenant_id()
        if not to_tenant_id:
            return {"success": False, "error": "无法确定当前租户"}
        records = TenantKnowledgeShareDB.list_for_to_tenant(to_tenant_id)
        data = [
            {
                "from_tenant_id": r["from_tenant_id"],
                "from_company_name": _company_name_of(r["from_tenant_id"]),
            }
            for r in records
        ]
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"获取知识库共享授权失败: {e}")
        return {"success": False, "error": "获取知识库共享授权失败"}


@router.put("")
async def set_knowledge_shares(
    req: SetKnowledgeSharesRequest,
    request_admin: dict = Depends(require_admin),
):
    """全量覆盖 B 租户已接入的来源租户列表（校验来源租户存在、非本租户）"""
    try:
        # 知识库接入是租户级授权（第一级闸门），仅平台管理员可配置；
        # require_admin 同时放行租户管理员，租户管理员自行接入其他租户会绕过
        # 平台管理员授权，构成跨租户越权读，故此处强制平台管理员角色。
        if request_admin.get("role") != "platform_admin":
            return {"success": False, "error": "仅平台管理员可配置知识库接入"}
        to_tenant_id = get_current_tenant_id()
        if not to_tenant_id:
            return {"success": False, "error": "无法确定当前租户"}

        from_tenant_ids: List[str] = []
        for item in req.from_tenants:
            from_id = item.from_tenant_id.strip()
            if not from_id:
                continue
            if from_id == to_tenant_id:
                return {"success": False, "error": "不能接入本租户自己的知识库"}
            if not TenantDB.get_by_id(from_id):
                return {"success": False, "error": f"来源租户不存在: {from_id}"}
            if from_id not in from_tenant_ids:
                from_tenant_ids.append(from_id)

        created_by = request_admin.get("user_id")
        success = TenantKnowledgeShareDB.set(to_tenant_id, from_tenant_ids, created_by=created_by)
        if success:
            return {"success": True, "message": f"已接入 {len(from_tenant_ids)} 个来源租户"}
        return {"success": False, "error": "保存知识库接入失败"}
    except Exception as e:
        logger.error(f"设置知识库共享授权失败: {e}")
        return {"success": False, "error": "保存知识库接入失败"}
