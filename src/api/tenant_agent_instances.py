"""租户数字员工实例 API。

提供租户对数字员工的实例管理：副本实例列表、复制、覆盖项更新（重命名/简介/
快捷按钮/扩展提示词）、副本实例删除（软删）；直用实例不可删除。

鉴权复用 src/saas/api/tenant_auth.py 的 require_admin（支持平台管理员
X-Tenant-Id 代管理）；租户隔离由 DB 层 fail-closed 保证。
"""

from typing import Any, Dict, List, Optional
import re

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from src.config.settings import LLM_PROVIDERS
from src.db.tenant_agent_instance_db import TenantAgentInstanceDB
from src.models.subagent import extract_llm_config, pack_llm_config
from src.saas.api.tenant_auth import require_admin
from src.saas.db.tenant_db import TenantDB
from src.saas.services import tenant_agent_instance_service as svc

router = APIRouter(prefix="/api/saas/tenant/agent-instances", tags=["tenant-agent-instances"])


# ============== 请求模型 ==============

class CreateCopyRequest(BaseModel):
    base_agent_id: str = Field(..., min_length=1, max_length=128, description="父定义 agent_id")
    display_name: str = Field(..., min_length=1, max_length=100, description="副本实例显示名")


class UpdateInstanceRequest(BaseModel):
    """覆盖项更新。

    普通字段（display_name 等）：None 表示不改，留空字符串表示清除覆盖（继承父定义）。
    LLM 字段（llm_provider/llm_model_codes）契约不同：只要任一字段出现在请求体中
    即视为整体更新 LLM 配置（未提交的另一个按 None 处理）；两字段全空 = 清除覆盖。
    前端在租户开关开启时总是同时携带两字段。"""
    display_name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    extra_prompt: Optional[str] = None
    greeting_summary: Optional[str] = Field(None, max_length=500)
    greeting_prompts: Optional[List[Dict[str, str]]] = None
    llm_provider: Optional[str] = None
    llm_model_codes: Optional[Dict[str, str]] = None


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


def _sanitize_llm(body: UpdateInstanceRequest) -> Optional[Dict[str, Any]]:
    """LLM 字段清洗（提交即整体更新契约）。

    未提交任一 llm 字段返回 None（不更新）；提交则校验 provider 白名单与
    model_codes 键白名单（非法 400），值做 ASCII 清洗，全空返回 packed None（清除）。
    """
    fields_set = body.model_fields_set
    if "llm_provider" not in fields_set and "llm_model_codes" not in fields_set:
        return None

    provider = (body.llm_provider or "").strip()
    if provider and provider not in LLM_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"llm_provider 非法，须为 {'/'.join(LLM_PROVIDERS)}")

    raw_codes = body.llm_model_codes
    if raw_codes is not None and not isinstance(raw_codes, dict):
        raise HTTPException(status_code=400, detail="llm_model_codes 格式错误")
    cleaned_codes: Dict[str, str] = {}
    for key, value in (raw_codes or {}).items():
        if key not in LLM_PROVIDERS:
            raise HTTPException(status_code=400, detail=f"llm_model_codes 含未知 provider: {key}")
        cleaned = re.sub(r"[^\x21-\x7E]", "", str(value or "")).strip()[:128]
        if cleaned:
            cleaned_codes[key] = cleaned

    return {"llm_provider": pack_llm_config(provider or None, cleaned_codes or None)}


def _row_to_item(row: Dict[str, Any]) -> Dict[str, Any]:
    is_copy = row.get("agent_id") != row.get("base_agent_id")
    llm_provider, llm_model_codes = extract_llm_config(row.get("llm_provider"))
    return {
        "agent_id": row.get("agent_id"),
        "base_agent_id": row.get("base_agent_id"),
        "base_source": row.get("base_source"),
        "display_name": row.get("display_name"),
        "description": row.get("description"),
        "extra_prompt": row.get("extra_prompt"),
        "greeting_summary": row.get("greeting_summary"),
        "greeting_prompts": row.get("greeting_prompts"),
        "llm_provider": llm_provider,
        "llm_model_codes": llm_model_codes,
        "is_copy": is_copy,
        "status": row.get("status"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


# ============== API 路由 ==============

@router.get("")
async def list_instances(admin: dict = Depends(require_admin)):
    """本租户实例列表（active 行，直用实例行 + 副本实例行）"""
    try:
        tenant_id = admin["tenant_id"]
        rows = TenantAgentInstanceDB.list_by_tenant(tenant_id)
        return {"success": True, "data": [_row_to_item(r) for r in rows]}
    except Exception as e:
        logger.opt(exception=True).error(f"获取租户数字员工实例列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取实例列表失败")


@router.post("")
async def create_copy(body: CreateCopyRequest, admin: dict = Depends(require_admin)):
    """复制基础数字员工为本租户副本实例（继承语义）"""
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
    """更新实例覆盖项（直用实例行与副本实例行共用）"""
    try:
        overrides: Dict[str, Any] = {}
        if body.display_name is not None:
            overrides["display_name"] = body.display_name.strip() or None
        if body.description is not None:
            overrides["description"] = body.description.strip() or None
        extra_prompt_value: Optional[str] = None
        extra_prompt_stripped: str = ""
        if body.extra_prompt is not None:
            extra_prompt_stripped = body.extra_prompt.strip()
            extra_prompt_value = extra_prompt_stripped or None
            overrides["extra_prompt"] = extra_prompt_value
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

        llm_overrides = _sanitize_llm(body)
        if llm_overrides is not None and not TenantDB.is_llm_edit_allowed(admin["tenant_id"]):
            # fail-closed：租户未开通 LLM 提供者定制时拒绝 llm 字段（防绕过前端显隐）
            raise HTTPException(status_code=403, detail="未开通 LLM 提供者定制权限")
        if llm_overrides is not None:
            overrides["llm_provider"] = llm_overrides["llm_provider"]

        row = svc.update_overrides(admin["tenant_id"], agent_id, overrides)

        # 补充双写：显式保存 extra_prompt 时同步租户定制版本链（空串也写版本：
        # 「清空用默认配置」是生效状态，不记录则回退上一版会跳过它），链上「上一版」
        # 才能与实例行一致（支撑回退上一版）。只由「用户保存」与「回退」两个用户动作
        # 写链，不放 service 层避免内部同步路径反向二次写链。best-effort：链写失败
        # 不阻塞实例行保存（运行时实例行权威）。
        if "extra_prompt" in body.model_fields_set:
            try:
                from src.api.subagent_extra import _get_or_create_prompt
                from src.prompts.prompt_registry_service import PromptRegistryService

                registry = _get_or_create_prompt(agent_id, admin["tenant_id"], admin["user_id"])
                if registry:
                    PromptRegistryService.commit_version(
                        prompt_id=str(registry["id"]),
                        content=extra_prompt_stripped,
                        commit_message="租户前台编辑",
                        created_by=admin["user_id"],
                    )
            except Exception as e:
                logger.opt(exception=True).error(
                    f"同步租户定制版本链失败 tenant={admin['tenant_id']} agent={agent_id}: {e}"
                )

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
    """删除副本实例（软删归档）；直用实例行拒绝"""
    try:
        svc.delete_copy(admin["tenant_id"], agent_id)
        return {"success": True, "message": "已删除"}
    except svc.TenantAgentInstanceError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except Exception as e:
        logger.opt(exception=True).error(f"删除数字员工副本失败: {e}")
        raise HTTPException(status_code=500, detail="删除副本失败")
