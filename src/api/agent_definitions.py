"""
子智能体定义管理 API

提供 /api/admin/agent-definitions 路由，管理子智能体定义和 system_prompt。
"""

import json
import re
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
    triggers: Optional[Dict[str, Any]] = None
    tools: Optional[Dict[str, Any]] = None
    skills: Optional[Dict[str, Any]] = None
    context: Optional[Dict[str, Any]] = None
    delegatable_to: Optional[List[str]] = None
    allow_delegation: bool = True
    llm_provider: Optional[str] = None
    llm_model_codes: Optional[Dict[str, str]] = None
    reply_style: Optional[str] = None
    business_pages: Optional[List[Dict[str, Any]]] = None
    knowledge_sources: Optional[List[Dict[str, str]]] = None
    recap: Optional[Dict[str, Any]] = None
    chat_toolbar: Optional[List[str]] = None
    upload_accept: Optional[str] = None
    commit_message: str = "初始版本"


class UpdateDefinitionRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    author: Optional[str] = None
    triggers: Optional[Dict[str, Any]] = None
    tools: Optional[Dict[str, Any]] = None
    skills: Optional[Dict[str, Any]] = None
    context: Optional[Dict[str, Any]] = None
    delegatable_to: Optional[List[str]] = None
    allow_delegation: Optional[bool] = None
    llm_provider: Optional[str] = None
    llm_model_codes: Optional[Dict[str, str]] = None
    reply_style: Optional[str] = None
    business_pages: Optional[List[Dict[str, Any]]] = None
    knowledge_sources: Optional[List[Dict[str, str]]] = None
    recap: Optional[Dict[str, Any]] = None
    chat_toolbar: Optional[List[str]] = None
    upload_accept: Optional[str] = None
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


def _validate_recap_config(recap: Optional[Dict[str, Any]]) -> None:
    """保存前校验 recap 任务配置：任务名必须在注册表、when 必须在白名单

    运行期 runner 对非法项是静默跳过，保存时不拦会导致配置看起来生效实际不执行。
    技能带入的条目（apply_skill_requirements 合并）不在本次校验范围，由 SKILL.md 声明保证。
    """
    if not recap or not isinstance(recap, dict):
        return
    raw_tasks = recap.get("tasks")
    if raw_tasks in (None, []):
        return
    if not isinstance(raw_tasks, list):
        raise HTTPException(status_code=400, detail="recap.tasks 必须是数组")

    from src.services.recap.runner import VALID_WHEN
    from src.services.recap.tasks import RECAP_TASK_ADAPTERS

    for raw in raw_tasks:
        if not isinstance(raw, dict):
            raise HTTPException(status_code=400, detail="recap.tasks 中存在非法任务项（必须为对象）")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="recap.tasks 中存在缺少 name 的任务项")
        if name not in RECAP_TASK_ADAPTERS:
            valid = ", ".join(sorted(RECAP_TASK_ADAPTERS.keys()))
            raise HTTPException(status_code=400, detail=f"recap 任务 {name} 不存在（可选：{valid}）")
        when = str(raw.get("when") or "every_round").strip()
        if when not in VALID_WHEN:
            raise HTTPException(
                status_code=400, detail=f"recap 任务 {name} 的触发时机 {when} 不支持（仅 {'/'.join(VALID_WHEN)}）"
            )


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

        _validate_recap_config(body.recap)

        tools, recap, auto_completed = SubagentDefinitionService.apply_skill_requirements(
            body.tools, body.skills, body.recap,
        )

        result = SubagentDefinitionService.create_definition(
            agent_id=body.agent_id,
            name=body.name,
            system_prompt=body.system_prompt,
            description=body.description,
            version=body.version,
            author=body.author,
            triggers=body.triggers,
            tools=tools,
            skills=body.skills,
            context=body.context,
            delegatable_to=body.delegatable_to,
            allow_delegation=body.allow_delegation,
            llm_provider=body.llm_provider,
            llm_model_codes=body.llm_model_codes,
            reply_style=body.reply_style,
            business_pages=body.business_pages,
            knowledge_sources=body.knowledge_sources,
            recap=recap,
            chat_toolbar=body.chat_toolbar,
            upload_accept=body.upload_accept,
            created_by=admin.get("user_id"),
            commit_message=body.commit_message,
        )
        if not result:
            return _error("创建失败")
        return _success(result, auto_completed=auto_completed)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"创建子智能体定义失败: {e}")
        return _error("创建失败")


@router.get("/meta/tools")
async def list_tools_meta(request: Request):
    """获取工具元数据列表（供前端选择器使用）"""
    _require_admin(request)
    from src.core.agent import master_agent
    tool_registry = master_agent.tool_registry
    if not tool_registry:
        return _success([])
    tools = []
    for name, tool in tool_registry._tools.items():
        tools.append({
            "id": name,
            "name": getattr(tool, 'display_name', None) or name,
            "description": getattr(tool, 'description', '') or '',
        })
    return _success(tools)


@router.get("/meta/skills")
async def list_skills_meta(request: Request):
    """获取技能元数据列表（供前端选择器使用）

    展示全部基础目录已加载 skill（未经主智能体白名单过滤），
    这样管理员在配置子智能体 skills.allowed 时能看到全部可选 skill。
    """
    _require_admin(request)
    from src.core.agent import master_agent
    skill_registry = master_agent.skill_registry
    if not skill_registry:
        return _success([])
    skills = []
    for name in skill_registry.list_all_loaded_skills():
        skill = skill_registry.get_all_skill(name)
        skills.append({
            "id": name,
            "name": name,
            "description": skill.description if skill else '',
            "requires_tools": list(getattr(skill, "requires_tools", None) or []) if skill else [],
            "requires_recap": [dict(t) for t in getattr(skill, "requires_recap", None) or []] if skill else [],
        })
    return _success(skills)


@router.get("/meta/reply-styles")
async def list_reply_styles_meta(request: Request):
    """获取回复风格元数据列表（供前端选择器使用）

    返回系统内置风格 + 当前租户自定义风格，与回复风格管理页面保持一致。
    """
    admin = _require_admin(request)
    tenant_id = admin.get("tenant_id")
    styles = []

    # 1. 系统内置风格（从数据库获取真实的 name 和 description）
    from src.saas.db.reply_style_db import ReplyStyleDB
    system_styles = ReplyStyleDB.list_system_styles()
    seen_ids = set()
    for s in system_styles:
        seen_ids.add(s["style_id"])
        styles.append({
            "id": s["style_id"],
            "name": s["name"],
            "description": s.get("description") or "",
        })

    # 2. 当前租户自定义风格（如果有）
    if tenant_id:
        from src.saas.db.reply_style_db import SYSTEM_TENANT
        tenant_styles = ReplyStyleDB.list_active_by_tenant(tenant_id)
        for s in tenant_styles:
            if s["style_id"] not in seen_ids and s["tenant_id"] != SYSTEM_TENANT:
                seen_ids.add(s["style_id"])
                styles.append({
                    "id": s["style_id"],
                    "name": s["name"],
                    "description": s.get("description") or "",
                })

    return _success(styles)


@router.get("/meta/recap-tasks")
async def list_recap_tasks_meta(request: Request):
    """获取 recap 任务元数据（供前端 recap 任务编辑器下拉使用）

    任务名来自适配器注册表 RECAP_TASK_ADAPTERS，触发时机来自 runner.VALID_WHEN 白名单。
    """
    _require_admin(request)
    from src.services.recap.tasks import RECAP_TASK_ADAPTERS
    from src.services.recap.runner import VALID_WHEN

    tasks = []
    for name, adapter_cls in RECAP_TASK_ADAPTERS.items():
        tasks.append({
            "name": name,
            "description": (getattr(adapter_cls, "__doc__", "") or "").strip().splitlines()[0] if getattr(adapter_cls, "__doc__", "") else "",
        })
    return _success({"tasks": tasks, "when_options": list(VALID_WHEN)})


# ============== Page Metadata ==============

from src.api.page_metadata import list_pages as _list_pages_meta, recommend_pages as _recommend_pages_meta


class RecommendPagesRequest(BaseModel):
    agent_description: str
    current_pages: List[str] = Field(default_factory=list)


@router.get("/meta/pages")
async def get_pages_meta(request: Request, domain: Optional[str] = None):
    """获取已发布的业务页面元数据列表（供前端选择器使用）"""
    return await _list_pages_meta(request, domain)


@router.post("/meta/pages/recommend")
async def post_pages_recommend(request: Request, body: RecommendPagesRequest):
    """AI 推荐与智能体描述相关的业务页面"""
    return await _recommend_pages_meta(request, body.model_dump())


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
    """更新子智能体定义（不含 system_prompt）

    使用 exclude_unset 而非 exclude_none，以便区分「未传入字段」和「显式传 null 清空」。
    这对 llm_provider / llm_model_codes 至关重要：用户需要能通过传 null 清空已有 LLM 配置。
    """
    try:
        admin = _require_admin(request)
        updates = body.model_dump(exclude_unset=True)
        if not updates:
            return _error("没有需要更新的字段")

        # 技能依赖自动补全：tools/recap 未显式传入时读现有定义，避免整列覆盖丢失
        auto_completed: list = []
        if "recap" in updates:
            _validate_recap_config(updates.get("recap"))
        skills_upd = updates.get("skills")
        if isinstance(skills_upd, dict) and skills_upd.get("allowed"):
            current: Dict[str, Any] = {}
            if "tools" not in updates or "recap" not in updates:
                current = SubagentDefinitionService.get_definition(agent_id) or {}
            new_tools, new_recap, auto_completed = SubagentDefinitionService.apply_skill_requirements(
                updates.get("tools", current.get("tools")),
                skills_upd,
                updates.get("recap", current.get("recap")),
            )
            if auto_completed:
                updates["tools"] = new_tools
                updates["recap"] = new_recap

        result = SubagentDefinitionService.update_definition(
            agent_id=agent_id,
            created_by=admin.get("user_id"),
            **updates,
        )
        if not result:
            return _error(f"智能体 {agent_id} 不存在或更新失败")

        # 刷新 master_agent 的 subagent_registry 内存配置，使 LLM/工具/技能等变更即时生效（无需重启容器）
        from src.core.agent import get_master_agent
        registry = get_master_agent().subagent_registry
        if registry:
            registry.load_from_db()

        return _success({"agent_id": agent_id}, auto_completed=auto_completed)
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


# ============== 配置导出/导入（跨环境迁移） ==============

EXPORT_FORMAT = "agent_definition_export"
SUPPORTED_EXPORT_VERSION = 1

AGENT_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


class ImportDefinitionRequest(BaseModel):
    format: str = Field(..., description="导出文件格式标识")
    version: int = Field(..., description="导出文件结构版本")
    definition: Dict[str, Any] = Field(..., description="数字员工定义内容")
    conflict_strategy: Optional[str] = Field(None, description="冲突策略：overwrite=覆盖更新 / rename=另存为新")
    new_agent_id: Optional[str] = Field(None, description="rename 策略的新 Agent ID")
    new_name: Optional[str] = Field(None, description="rename 策略的新显示名称")


def _get_prompt_content(agent_id: str) -> str:
    """读取当前 production（兜底 latest）版本的 system_prompt 内容"""
    info = SubagentDefinitionService._get_prompt_info(agent_id)
    if not info:
        return ""
    version_num = info.get("production_version") or info.get("latest_version")
    if not version_num:
        return ""
    data = PromptRegistryService.get_version(str(info["prompt_id"]), int(version_num))
    return (data or {}).get("content", "")


@router.get("/{agent_id}/export")
async def export_definition(request: Request, agent_id: str):
    """导出数字员工配置为 JSON（敏感信息不导出，仅保留占位符提示）"""
    try:
        _require_admin(request)

        definition = SubagentDefinitionService.get_definition(agent_id)
        if not definition:
            return _error(f"自定义数字员工不存在: {agent_id}（内置数字员工不支持导出）", 404)

        from src.db.subagent_env_var import SubagentEnvVarDB

        env_placeholders = []
        try:
            env_placeholders = [
                {"name": v["var_name"], "description": v.get("description") or ""}
                for v in SubagentEnvVarDB.list_var_names(agent_id)
            ]
        except Exception as e:
            logger.opt(exception=True).warning(f"导出环境变量占位符失败: {e}")

        export_notes = [
            "环境变量与第三方凭证（AGENT_TOKEN 等）不随文件导出，导入后需在目标环境「租户设置-环境变量」中按占位符补充",
            "租户 API 文档（api-doc）请通过租户前台的配置文件上传/下载功能单独迁移",
            "knowledge_sources 仅作参考：栏目代号（source_type）由各环境独立生成，跨环境不通用，导入时不迁移；目标环境请按 display_name 在「租户设置-知识库关联」重新配置，知识库文档内容需单独上传或共享授权",
            "分段变量值（System Prompt 中 {{变量}} 的取值）随文件导出",
        ]

        try:
            sections = [
                {"section_key": s["section_key"], "content": s.get("content") or ""}
                for s in SubagentDefinitionService.get_sections(agent_id)
            ]
        except Exception as e:
            # 置 None（而非空列表）：导入侧将 None 视为「文件不含分段数据」，
            # 避免导出瞬时故障产生的空 sections 覆盖导入时清空目标环境分段
            logger.opt(exception=True).warning(f"导出分段变量值失败: {e}")
            sections = None

        return _success({
            "format": EXPORT_FORMAT,
            "version": SUPPORTED_EXPORT_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "definition": {
                "agent_id": definition.get("agent_id"),
                "name": definition.get("name"),
                "description": definition.get("description"),
                "version": definition.get("version"),
                "author": definition.get("author"),
                "status": definition.get("status") or "active",
                "triggers": definition.get("triggers") or {},
                "tools": definition.get("tools") or {},
                "skills": definition.get("skills") or {},
                "context": definition.get("context") or {},
                "delegatable_to": definition.get("delegatable_to") or [],
                "allow_delegation": definition.get("allow_delegation", True),
                "llm_provider": definition.get("llm_provider"),
                "llm_model_codes": definition.get("llm_model_codes"),
                "reply_style": definition.get("reply_style"),
                "business_pages": definition.get("business_pages"),
                "knowledge_sources": definition.get("knowledge_sources"),
                "chat_toolbar": definition.get("chat_toolbar"),
                "upload_accept": definition.get("upload_accept"),
                "recap": definition.get("recap"),
                "system_prompt": _get_prompt_content(agent_id),
                "sections": sections,
            },
            "env_var_placeholders": env_placeholders,
            "notes": export_notes,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"导出数字员工配置失败: {e}")
        return _error("导出失败")


def _collect_import_warnings(definition: Dict[str, Any]) -> List[str]:
    """校验技能/工具在目标环境的存在性，缺失项生成警告（不阻断导入）"""
    warnings: List[str] = []
    from src.core.agent import get_master_agent

    agent = get_master_agent()

    allowed_skills = ((definition.get("skills") or {}).get("allowed")) or []
    if allowed_skills:
        skill_registry = agent.skill_registry
        loaded = set(skill_registry.list_all_loaded_skills()) if skill_registry else set()
        missing = [s for s in allowed_skills if s not in loaded]
        if missing:
            warnings.append(f"以下技能在当前环境不存在，将忽略或需安装: {', '.join(missing)}")

    tools_cfg = definition.get("tools") or {}
    tool_list = tools_cfg.get("additional") or tools_cfg.get("allowed") or []
    if tool_list:
        tool_registry = agent.tool_registry
        available = set(tool_registry._tools.keys()) if tool_registry else set()
        missing_tools = [t for t in tool_list if t not in available]
        if missing_tools:
            warnings.append(f"以下工具在当前环境不存在: {', '.join(missing_tools)}")

    return warnings


SECTION_KEY_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _restore_sections(
    agent_id: str,
    sections: Any,
    updated_by: Optional[str],
    warnings: List[str],
    replace: bool,
) -> None:
    """恢复分段变量值到 subagent_prompt_sections 表

    sections 为 None 表示配置文件来自不含分段数据的旧版导出，跳过并提示；
    空列表仅在 replace 模式下清空目标残留。
    """
    if sections is None:
        warnings.append("配置文件未包含分段变量值（旧版导出格式），请在目标环境手动检查分段变量")
        return
    if not isinstance(sections, list):
        warnings.append("配置文件分段变量值格式异常，已跳过恢复")
        return

    from src.db.subagent_prompt_section_db import SubagentPromptSectionDB

    try:
        if replace:
            if not SubagentPromptSectionDB.delete_sections(agent_id):
                warnings.append("清理目标环境已有分段变量失败，残留分段可能与导入值合并，请手动检查分段变量")

        restored = 0
        for item in sections:
            if not isinstance(item, dict):
                warnings.append(f"分段变量项格式异常（应为对象），已跳过: {item!r}")
                continue
            key = str(item.get("section_key") or "").strip()
            content = item.get("content")
            if not key or not SECTION_KEY_RE.match(key) or not isinstance(content, str):
                warnings.append(f"分段变量 {key or '(空)'} 格式非法，已跳过")
                continue
            if SubagentPromptSectionDB.upsert_section(agent_id, key, content, updated_by):
                restored += 1
            else:
                warnings.append(f"分段变量 {key} 写入失败")

        if restored or replace:
            from src.core.cache_utils import CacheKeys, delete_cached
            delete_cached(CacheKeys.PROMPT_SECTIONS, agent_id)
    except Exception as e:
        # 定义主体已导入成功，分段失败不应使整体导入报「失败」
        logger.opt(exception=True).error(f"导入分段变量值失败: {e}")
        warnings.append("分段变量值恢复失败，请手动检查分段变量")


def _conflict_response(agent_id: str) -> JSONResponse:
    """agent_id 冲突时返回 409 与现有定义信息，供前端选择冲突策略"""
    from src.core.agent import master_agent

    reg_cfg = master_agent.subagent_registry.get(agent_id) if master_agent.subagent_registry else None
    if reg_cfg is not None and not getattr(reg_cfg, "from_db", False):
        conflict_type = "builtin"
        conflict_name = reg_cfg.name
    else:
        conflict_type = "custom"
        conflict_name = reg_cfg.name if reg_cfg else agent_id
    return JSONResponse(
        {
            "success": False,
            "error": f"智能体 {agent_id} 已存在",
            "conflict": {"agent_id": agent_id, "type": conflict_type, "name": conflict_name},
        },
        status_code=409,
    )


@router.post("/import")
async def import_definition(request: Request, body: ImportDefinitionRequest):
    """导入数字员工配置 JSON，创建新员工或按冲突策略覆盖/另存

    按文件原样导入，不做技能依赖自动补全（tools/recap 忠实透传）：导出文件
    是源环境已验证的最终配置，导入是跨环境迁移而非交互式编辑。技能依赖补全
    仅属于交互式编辑路径（前端勾选技能联动 + create/update 保存时合并）。
    背景（2026-10-23）：导出文件不含 lead_refresh 轮后任务，导入后被
    apply_skill_requirements 自动补上，与源环境配置不一致。
    """
    try:
        admin = _require_admin(request)

        if body.format != EXPORT_FORMAT:
            return _error(f"不支持的配置文件格式: {body.format}", 400)
        if body.version > SUPPORTED_EXPORT_VERSION:
            return _error(f"配置文件版本过新（v{body.version}），请先升级系统", 400)

        d = body.definition
        agent_id = str(d.get("agent_id") or "").strip()
        name = str(d.get("name") or "").strip()
        system_prompt = str(d.get("system_prompt") or "")
        if not agent_id or not name or not system_prompt.strip():
            return _error("配置文件缺少必需字段（agent_id / name / system_prompt）", 400)
        if not AGENT_ID_RE.match(agent_id):
            return _error("Agent ID 只能包含字母、数字、下划线和连字符", 400)

        _validate_recap_config(d.get("recap"))

        existing = SubagentDefinitionService.get_definition(agent_id)

        # 纯内置（未被 DB 定义覆盖）同名时同样走冲突流程：内置不可覆盖，只能另存
        from src.core.agent import master_agent
        reg_cfg = master_agent.subagent_registry.get(agent_id) if master_agent.subagent_registry else None
        is_pure_builtin = reg_cfg is not None and not getattr(reg_cfg, "from_db", False)

        if (existing or is_pure_builtin) and not body.conflict_strategy:
            return _conflict_response(agent_id)

        if is_pure_builtin and body.conflict_strategy == "overwrite":
            return _error("内置数字员工不可覆盖，请改用「另存为新」指定新的 Agent ID", 409)

        warnings = _collect_import_warnings(d)

        import_status = d.get("status")
        if import_status is not None and import_status not in ("active", "disabled"):
            warnings.append(f"状态值 {import_status!r} 非法，已忽略（导入后保持目标环境原状态）")
            import_status = None

        # 知识库栏目代号（source_type）由各环境独立生成，跨环境不通用，不随导入迁移：
        # 覆盖模式不传该字段（保持目标环境原值），创建模式走默认空列表
        if d.get("knowledge_sources"):
            warnings.append(
                "知识库栏目关联不随导入迁移（栏目代号跨环境不通用），请按导出文件中的 display_name "
                "在目标环境「租户设置-知识库关联」重新配置"
            )

        if existing and body.conflict_strategy == "overwrite":
            # 覆盖更新：空值传空容器/空串以清掉目标环境残留（DB update 过滤 None；
            # 例外：llm_provider/llm_model_codes 传 None 会强制写空 JSONB，恰好也是清残留）
            SubagentDefinitionService.update_definition(
                agent_id,
                created_by=admin.get("user_id"),
                name=name,
                description=d.get("description") or "",
                version=d.get("version"),
                author=d.get("author"),
                triggers=d.get("triggers") or {},
                tools=d.get("tools") or {},
                skills=d.get("skills") or {},
                context=d.get("context") or {},
                delegatable_to=d.get("delegatable_to") or [],
                allow_delegation=bool(d.get("allow_delegation", True)),
                llm_provider=d.get("llm_provider"),
                llm_model_codes=d.get("llm_model_codes"),
                reply_style=d.get("reply_style") or "",
                business_pages=d.get("business_pages") or [],
                recap=d.get("recap") or {},
                chat_toolbar=d.get("chat_toolbar") or [],
                upload_accept=d.get("upload_accept") or "",
                status=import_status,
            )
            SubagentDefinitionService.update_system_prompt(
                agent_id,
                system_prompt,
                commit_message="导入配置覆盖",
                created_by=admin.get("user_id"),
            )
            _restore_sections(agent_id, d.get("sections"), admin.get("user_id"), warnings, replace=True)
            # 刷新 registry 并清空态摘要缓存（与 update 路由口径一致）
            from src.core.agent import master_agent
            from src.core.cache_utils import CacheKeys, delete_cached
            if master_agent.subagent_registry:
                master_agent.subagent_registry.load_from_db()
            delete_cached(CacheKeys.SUBAGENT_GREETING, agent_id)

            logger.info(f"后端日志：管理员导入并覆盖数字员工 {agent_id}")
            return _success({"agent_id": agent_id, "name": name, "mode": "overwrite"},
                            warnings=warnings)

        if (existing or is_pure_builtin) and body.conflict_strategy == "rename":
            new_id = str(body.new_agent_id or "").strip()
            new_name = str(body.new_name or "").strip() or name
            if not new_id:
                return _error("另存为新的数字员工需要提供新 Agent ID", 400)
            if not AGENT_ID_RE.match(new_id):
                return _error("新 Agent ID 只能包含字母、数字、下划线和连字符", 400)
            if SubagentDefinitionService.get_definition(new_id):
                return _error(f"新 Agent ID 已存在: {new_id}", 409)
            agent_id, name = new_id, new_name

        # 创建路径（新员工或 rename 另存）
        result = SubagentDefinitionService.create_definition(
            agent_id=agent_id,
            name=name,
            system_prompt=system_prompt,
            description=d.get("description"),
            version=d.get("version") or "1.0.0",
            author=d.get("author"),
            triggers=d.get("triggers"),
            tools=d.get("tools"),
            skills=d.get("skills"),
            context=d.get("context"),
            delegatable_to=d.get("delegatable_to"),
            allow_delegation=bool(d.get("allow_delegation", True)),
            llm_provider=d.get("llm_provider"),
            llm_model_codes=d.get("llm_model_codes"),
            reply_style=d.get("reply_style"),
            business_pages=d.get("business_pages"),
            recap=d.get("recap"),
            chat_toolbar=d.get("chat_toolbar"),
            upload_accept=d.get("upload_accept"),
            created_by=admin.get("user_id"),
            commit_message="导入配置",
        )
        if not result:
            return _error("导入失败：创建定义未成功")
        if import_status == "disabled":
            # DB create 不接收 status（默认 active），禁用状态补一次更新
            SubagentDefinitionService.update_definition(
                agent_id, created_by=admin.get("user_id"), status="disabled",
            )
        _restore_sections(agent_id, d.get("sections"), admin.get("user_id"), warnings, replace=False)

        from src.core.agent import master_agent
        if master_agent.subagent_registry:
            master_agent.subagent_registry.load_from_db()

        logger.info(f"后端日志：管理员导入创建数字员工 {agent_id}")
        return _success({"agent_id": agent_id, "name": name, "mode": "create"},
                        warnings=warnings)
    except HTTPException:
        raise
    except Exception as e:
        logger.opt(exception=True).error(f"导入数字员工配置失败: {e}")
        return _error("导入失败")
