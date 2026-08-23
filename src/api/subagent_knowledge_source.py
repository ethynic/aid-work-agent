"""
租户级子智能体知识库关联 API。

提供 per-tenant per-agent 的知识库关联配置接口。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import List, Dict, Optional
from loguru import logger

from src.db.database import get_db_connection
from src.db.subagent_knowledge_source_db import SubagentKnowledgeSourceDB
from src.saas.api.tenant_auth import require_admin
from src.saas.context import get_current_tenant_id


router = APIRouter(prefix="/api/saas/tenant/subagent-knowledge", tags=["subagent-knowledge"])


# ============== 请求模型 ==============

class KnowledgeSourceItem(BaseModel):
    source_type: str = Field(..., min_length=1, max_length=100, description="知识库代号")
    display_name: str = Field(..., min_length=1, max_length=200, description="知识库名称")
    owner_tenant_id: Optional[str] = Field(None, description="共享来源租户 ID；本租户项为空，共享项为来源租户 ID")


def _validate_shared_source(tenant_id: str, item: KnowledgeSourceItem) -> Optional[str]:
    """校验共享来源项（owner_tenant_id 非空时）。

    与检索侧判定一致，避免"检索到却未授权关联"的越权：
    - 来源租户不能是当前租户自身
    - 租户级授权存在（tenant_knowledge_shares 中 from_tenant_id -> to_tenant_id）
    - source_type 确实属于来源租户（knowledge_categories）

    Returns:
        错误信息；None 表示通过。
    """
    owner = item.owner_tenant_id
    if not owner:
        return None
    if owner == tenant_id:
        return f"知识库 {item.source_type} 的来源租户不能是当前租户"
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                  EXISTS (SELECT 1 FROM tenant_knowledge_shares
                          WHERE from_tenant_id = %s AND to_tenant_id = %s) AS authorized,
                  EXISTS (SELECT 1 FROM knowledge_categories
                          WHERE tenant_id = %s AND source_type = %s) AS has_cat
            """, (owner, tenant_id, owner, item.source_type))
            row = cursor.fetchone()
    except Exception as e:
        logger.error(f"校验共享知识库来源失败: {e}")
        return "校验共享知识库来源失败"
    if not row or not row["authorized"]:
        return f"来源租户 {owner} 未授权共享知识库给当前租户"
    if not row["has_cat"]:
        return f"来源租户 {owner} 不存在知识库分类 {item.source_type}"
    return None


class SetKnowledgeSourcesRequest(BaseModel):
    sources: List[KnowledgeSourceItem] = Field(default_factory=list, description="知识库关联列表（全量覆盖）")


# ============== API 路由 ==============

@router.get("/{subagent_name}")
async def get_knowledge_sources(
    subagent_name: str,
    request_admin: dict = Depends(require_admin),
):
    """获取某子智能体的知识库关联"""
    try:
        tenant_id = get_current_tenant_id()
        sources = SubagentKnowledgeSourceDB.get(tenant_id, subagent_name)
        return {"success": True, "data": sources}
    except Exception as e:
        logger.error(f"获取知识库关联失败: {e}")
        return {"success": False, "error": "获取知识库关联失败"}


@router.put("/{subagent_name}")
async def set_knowledge_sources(
    subagent_name: str,
    req: SetKnowledgeSourcesRequest,
    request_admin: dict = Depends(require_admin),
):
    """设置某子智能体的知识库关联（全量覆盖）"""
    try:
        tenant_id = get_current_tenant_id()
        # 校验共享来源项：未授权的共享分类直接拒绝，不落库
        for s in req.sources:
            err = _validate_shared_source(tenant_id, s)
            if err:
                return {"success": False, "error": err}
        sources_list = [s.model_dump() for s in req.sources]
        success = SubagentKnowledgeSourceDB.set(tenant_id, subagent_name, sources_list)
        if success:
            return {"success": True, "message": f"已设置 {len(sources_list)} 个知识库关联"}
        return {"success": False, "error": "设置知识库关联失败"}
    except Exception as e:
        logger.error(f"设置知识库关联失败: {e}")
        return {"success": False, "error": "设置知识库关联失败"}


@router.delete("/{subagent_name}")
async def delete_knowledge_sources(
    subagent_name: str,
    request_admin: dict = Depends(require_admin),
):
    """删除某子智能体的知识库关联"""
    try:
        tenant_id = get_current_tenant_id()
        success = SubagentKnowledgeSourceDB.delete(tenant_id, subagent_name)
        if success:
            return {"success": True, "message": "知识库关联已删除"}
        return {"success": True, "message": "无关联数据"}
    except Exception as e:
        logger.error(f"删除知识库关联失败: {e}")
        return {"success": False, "error": "删除知识库关联失败"}
