"""
数字员工公开 API

提供数字员工（子智能体）的列表查询、详情查询等接口。
仅包含只读 GET 操作。
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from loguru import logger

from src.core.agent import master_agent
from src.saas.context import get_current_tenant_id
from src.saas.permissions.checker import get_allowed_agent_ids_for_user

router = APIRouter(prefix="/api/subagents", tags=["数字员工"])


# ============== API 接口（只读） ==============

@router.get("")
async def list_subagents(request: Request):
    """列出所有数字员工（内置+定制，标记类型）"""
    try:
        registry = master_agent.subagent_registry
        if not registry:
            return {"success": False, "error": "子智能体注册表未初始化", "debug": "subagent_registry is None"}

        # 多 worker 部署时从磁盘刷新定制 subagent，确保读到最新数据
        if registry._custom_dir:
            registry._load_custom(registry._custom_dir)

        items = registry.get_all_subagents_with_type()

        # 租户模式下按权限过滤
        tenant_id = get_current_tenant_id()

        if tenant_id is not None:
            from src.api.auth import get_current_user
            user = get_current_user(request)
            if user:
                allowed_ids = set(get_allowed_agent_ids_for_user(user))
                items = [item for item in items if item["agent_id"] in allowed_ids]
                # 如果主智能体在允许列表中，添加到结果中
                if "main" in allowed_ids:
                    items.append({
                        "agent_id": "main",
                        "name": "CEO智能体",
                        "description": "系统主智能体，具备通用能力和工具",
                        "capabilities": [],
                        "type": "builtin",
                        "business_pages": []
                    })
            # user 为 None 时（token 无效），不添加 main，保持只有内置 subagent
        else:
            # 非租户模式（演示模式）：添加主智能体
            items.append({
                "agent_id": "main",
                "name": "CEO智能体",
                "description": "系统主智能体，具备通用能力和工具",
                "capabilities": [],
                "type": "builtin",
                "business_pages": []
            })

        return {"success": True, "data": items}

    except Exception as e:
        logger.error(f"列出数字员工失败: {e}", exc_info=True)
        return {"success": False, "error": "列出数字员工失败", "debug": str(e)}


@router.get("/{agent_id}")
async def get_subagent_detail(request: Request, agent_id: str):
    """获取单个数字员工详情"""
    try:
        registry = master_agent.subagent_registry
        if not registry:
            return {"success": False, "error": "子智能体注册表未初始化", "debug": "subagent_registry is None"}

        # 多 worker 部署时从磁盘刷新定制 subagent，确保读到最新数据
        if registry._custom_dir:
            registry._load_custom(registry._custom_dir)

        # 处理主智能体请求
        if agent_id == "main":
            data = {
                "agent_id": "main",
                "name": "CEO智能体",
                "description": "系统主智能体，具备通用能力和工具",
                "version": "1.0.0",
                "author": "system",
                "capabilities": [],
                "triggers": {},
                "tools": {},
                "skills": {},
                "context": {},
                "system_prompt": "",
                "type": "builtin",
                "business_pages": []
            }
            return {"success": True, "data": data}

        config = registry.get(agent_id)
        if not config:
            return {"success": False, "error": f"数字员工不存在: {agent_id}", "debug": f"agent_id={agent_id} not found"}

        data = {
            "agent_id": config.dir_name or agent_id,
            "name": config.name,
            "description": config.description,
            "version": config.version,
            "author": config.author,
            "capabilities": config.capabilities,
            "triggers": config.triggers,
            "tools": config.tools,
            "skills": config.skills,
            "context": config.context,
            "system_prompt": config.system_prompt,
            "type": "builtin" if registry.is_builtin(agent_id) else "custom",
        }
        if config.business_pages:
            data["business_pages"] = config.business_pages
        return {"success": True, "data": data}

    except Exception as e:
        logger.error(f"获取数字员工详情失败: {e}", exc_info=True)
        return {"success": False, "error": "获取数字员工详情失败", "debug": str(e)}


@router.get("/{agent_id}/content")
async def get_subagent_content(request: Request, agent_id: str):
    """获取 SUBAGENT.md 原始内容"""
    try:
        registry = master_agent.subagent_registry
        if not registry:
            return {"success": False, "error": "子智能体注册表未初始化", "debug": "subagent_registry is None"}

        # 多 worker 部署时从磁盘刷新定制 subagent，确保读到最新数据
        if registry._custom_dir:
            registry._load_custom(registry._custom_dir)

        # 处理主智能体请求
        if agent_id == "main":
            return {"success": True, "data": "# CEO智能体\n\n系统主智能体，具备通用能力和工具"}

        content = registry.get_content(agent_id)
        if not content:
            return {"success": False, "error": f"数字员工不存在: {agent_id}", "debug": f"agent_id={agent_id} not found"}

        return {"success": True, "data": content}

    except Exception as e:
        logger.error(f"获取数字员工内容失败: {e}", exc_info=True)
        return {"success": False, "error": "获取数字员工内容失败", "debug": str(e)}


@router.get("/skills")
async def list_available_skills(request: Request):
    """获取可选 skill 列表"""
    try:
        skill_registry = master_agent.skill_registry
        if not skill_registry:
            return {"success": True, "data": []}

        skills = skill_registry.list_skills()
        return {"success": True, "data": skills}

    except Exception as e:
        logger.error(f"获取技能列表失败: {e}", exc_info=True)
        return {"success": False, "error": "获取技能列表失败", "debug": str(e)}


@router.get("/tools")
async def list_available_tools(request: Request):
    """获取可选工具列表"""
    try:
        tool_registry = master_agent.tool_registry
        if not tool_registry:
            return {"success": True, "data": []}

        # 获取所有工具的名称和描述
        tools = []
        for name, tool in tool_registry._tools.items():
            tools.append({
                "name": name,
                "description": getattr(tool, 'description', '') or '',
                "display_name": getattr(tool, 'display_name', name) or name,
            })
        return {"success": True, "data": tools}

    except Exception as e:
        logger.error(f"获取工具列表失败: {e}", exc_info=True)
        return {"success": False, "error": "获取工具列表失败", "debug": str(e)}
