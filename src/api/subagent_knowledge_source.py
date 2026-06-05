"""
租户级子智能体知识库关联 API。

提供 per-tenant per-agent 的知识库关联配置接口。
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import List, Dict
from loguru import logger

from src.db.subagent_knowledge_source_db import SubagentKnowledgeSourceDB
from src.saas.api.tenant_auth import require_admin
from src.saas.context import get_current_tenant_id


router = APIRouter(prefix="/api/saas/tenant/subagent-knowledge", tags=["subagent-knowledge"])


# ============== 请求模型 ==============

class KnowledgeSourceItem(BaseModel):
    source_type: str = Field(..., min_length=1, max_length=100, description="知识库代号")
    display_name: str = Field(..., min_length=1, max_length=200, description="知识库名称")


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
