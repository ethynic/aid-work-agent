"""
数字员工公开 API

提供数字员工（子智能体）的列表查询、详情查询等接口。
仅包含只读 GET 操作。
"""

import json
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from loguru import logger

from src.core.agent import master_agent
from src.core.cache_utils import CacheKeys, get_cached, set_cached
from src.llm.gateway import llm_gateway
from src.reports.summarizer import get_lite_model
from src.saas.context import get_current_tenant_id
from src.saas.permissions.checker import get_allowed_agent_ids_for_user

router = APIRouter(prefix="/api/subagents", tags=["数字员工"])

# 数字员工空态摘要缓存 TTL（7 天）：智能体能力变更低频，长缓存避免重复计费
_GREETING_TTL = 7 * 24 * 3600
# 快捷按钮上限：空态固定展示 2 个
_GREETING_MAX_PROMPTS = 2


# ============== API 接口（只读） ==============

@router.get("")
async def list_subagents(request: Request):
    """列出所有数字员工（内置+定制，标记类型）"""
    try:
        registry = master_agent.subagent_registry
        if not registry:
            return {"success": False, "error": "子智能体注册表未初始化", "debug": "subagent_registry is None"}

        # 多 worker 部署时从 DB 刷新定制 subagent
        registry.load_from_db()

        items = registry.get_all_subagents_with_type()

        # 租户模式下按权限过滤
        tenant_id = get_current_tenant_id()

        if tenant_id is not None:
            from src.api.auth import get_current_user
            user = get_current_user(request)
            if user:
                # 传递 tenant_id 作为 target_tenant_id，确保平台管理员代管租户时
                # 按目标租户的订阅过滤，而不是 fallback 到 user["tenant_id"]（平台管理员为 NULL）
                allowed_ids = set(get_allowed_agent_ids_for_user(user, target_tenant_id=tenant_id))
                items = [item for item in items if item["agent_id"] in allowed_ids]
                # 如果主智能体在允许列表中，添加到结果中
                if "main" in allowed_ids:
                    items.append({
                        "agent_id": "main",
                        "name": "CEO智能体",
                        "description": "系统主智能体，具备通用能力和工具",
                        "type": "builtin",
                        "business_pages": []
                    })
            # user 为 None 时（token 无效），不添加 main，保持只有内置 subagent

        return {"success": True, "data": items}

    except Exception as e:
        logger.opt(exception=True).error(f"列出数字员工失败: {e}")
        return {"success": False, "error": "列出数字员工失败", "debug": str(e)}


@router.get("/skills")
async def list_available_skills(request: Request):
    """获取可选 skill 列表

    展示全部基础目录已加载 skill（未经主智能体白名单过滤），
    供前端技能选择器使用。
    """
    try:
        skill_registry = master_agent.skill_registry
        if not skill_registry:
            return {"success": True, "data": []}

        skills = skill_registry.list_all_loaded_skills()
        return {"success": True, "data": skills}

    except Exception as e:
        logger.opt(exception=True).error(f"获取技能列表失败: {e}")
        return {"success": False, "error": "获取技能列表失败", "debug": str(e)}


def _parse_greeting_llm_output(content: str) -> Optional[Dict[str, Any]]:
    """解析 LLM 生成的空态摘要 JSON，容错 markdown code fence / 前后杂质"""
    if not content:
        return None
    text = content.strip()
    # 剥离 ```json ... ``` 包裹
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # 兜底：提取第一个 { ... } 块
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            return None
    if not isinstance(data, dict):
        return None
    summary = str(data.get("summary") or "").strip()
    raw_prompts = data.get("prompts")
    if not isinstance(raw_prompts, list):
        raw_prompts = []
    prompts = []
    for item in raw_prompts[: _GREETING_MAX_PROMPTS]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        message = str(item.get("message") or "").strip()
        if label and message:
            prompts.append({"label": label[:20], "message": message[:100]})
    if not summary:
        return None
    return {"summary": summary, "prompts": prompts}


def _build_greeting_input(config) -> Dict[str, Any]:
    """从 SubagentConfig 组装 LLM 输入：描述 + 可用工具/技能/业务页面"""
    allowed_tools = config.get_allowed_tools()
    excluded_tools = config.get_excluded_tools()
    tools_str = "、".join(allowed_tools) if allowed_tools else "继承主智能体工具集"
    if excluded_tools:
        tools_str += f"（排除：{'、'.join(excluded_tools)}）"
    skills = config.get_allowed_skills()
    skills_str = "、".join(skills) if skills else "无"
    pages = [p.get("title", "") for p in (config.business_pages or []) if isinstance(p, dict)]
    pages_str = "、".join(pages) if pages else "无"
    return {
        "name": config.name,
        "description": config.description,
        "tools_str": tools_str,
        "skills_str": skills_str,
        "pages_str": pages_str,
    }


@router.get("/{agent_id}/greeting")
async def get_subagent_greeting(request: Request, agent_id: str):
    """获取数字员工「新会话空态」摘要与快捷操作按钮

    缓存优先（subagent_greeting:{agent_id}，TTL 7 天）；未命中时用
    description + tools/skills 调用小模型（lite_model）生成，并把计费
    归属到当前访问的租户用户；LLM 失败/解析失败时降级为 description + 通用按钮。
    """
    try:
        # 缓存命中直接返回
        cached = get_cached(CacheKeys.SUBAGENT_GREETING, agent_id)
        if cached is not None:
            return {"success": True, "data": cached, "source": "cache"}

        # 主智能体无 registry 配置，直接用通用兜底
        if agent_id == "main":
            data = {"summary": "系统主智能体，具备通用能力和工具", "prompts": [
                {"label": "帮我写一份周报", "message": "帮我写一份周报"},
                {"label": "分析上传的文件", "message": "分析上传的文件"},
            ]}
            return {"success": True, "data": data, "source": "fallback"}

        registry = master_agent.subagent_registry
        if not registry:
            return {"success": False, "error": "子智能体注册表未初始化", "debug": "subagent_registry is None"}

        registry.load_from_db()
        config = registry.get(agent_id)
        if not config:
            # 不存在的智能体直接兜底，避免缓存无效项
            return {"success": False, "error": f"数字员工不存在: {agent_id}", "debug": f"agent_id={agent_id} not found"}

        info = _build_greeting_input(config)
        lite_model = get_lite_model()

        system_prompt = (
            "你是企业智能体系统的文案助手。请根据给定数字员工的能力信息，生成该员工在新会话页面的"
            "能力摘要文字和 2 个快捷操作按钮。\n"
            f"【数字员工名称】{info['name']}\n"
            f"【功能描述】{info['description']}\n"
            f"【可用工具】{info['tools_str']}\n"
            f"【可用技能】{info['skills_str']}\n"
            f"【业务页面】{info['pages_str']}\n"
            "【要求】\n"
            "1. summary：一段 40-80 字的中文能力摘要，向用户说明该员工能做什么，直接可展示、口语化、不啰嗦。\n"
            "2. prompts：恰好 2 个按钮。每个含 label（按钮文案，≤6 字）和 message（点击后发送给该员工的完整指令，"
            "必须限定在该员工能力范围内，可执行）。\n"
            "3. 只输出 JSON，不要输出任何解释文字，格式：{\"summary\": \"...\", \"prompts\": [{\"label\": \"...\", \"message\": \"...\"}]}"
        )

        usage = {}
        data = None
        try:
            result = await llm_gateway.chat_lite(
                messages=[{"role": "user", "content": system_prompt}],
                temperature=0.3,
                max_tokens=512,
            )
            usage = result.get("usage") if isinstance(result, dict) else None
            data = _parse_greeting_llm_output(result.get("content", "") or "")
        except Exception as e:
            logger.opt(exception=True).error(f"数字员工空态摘要生成失败: agent={agent_id}: {e}")
        else:
            # LLM 实际已执行，无论解析成败都计费归属当前访问者（真实 token 已消耗）
            try:
                from src.services.session_record import record_background_llm_usage
                record_background_llm_usage(
                    usage,
                    tenant_id=request.state.tenant_id,
                    user_id=request.state.user_id,
                    source="subagent_greeting",
                    user_message=f"[数字员工空态] {info['name']} 摘要生成",
                    model=lite_model,
                )
            except Exception as e:
                logger.opt(exception=True).error(f"数字员工空态摘要计费失败: {e}")

        # 生成成功：写缓存
        if data:
            set_cached(CacheKeys.SUBAGENT_GREETING, agent_id, value=data, ttl=_GREETING_TTL)
            return {"success": True, "data": data, "source": "llm"}

        # 兜底：description + 通用按钮，不阻塞 UI
        data = {
            "summary": info["description"] or "输入您的问题或任务，AI助手将为您处理。可以上传文件进行智能分析。",
            "prompts": [
                {"label": "帮我写一份周报", "message": "帮我写一份周报"},
                {"label": "分析上传的文件", "message": "分析上传的文件"},
            ],
        }
        return {"success": True, "data": data, "source": "fallback"}

    except Exception as e:
        logger.opt(exception=True).error(f"获取数字员工空态摘要失败: {e}")
        return {"success": False, "error": "获取数字员工空态摘要失败", "debug": str(e)}


@router.get("/{agent_id}")
async def get_subagent_detail(request: Request, agent_id: str):
    """获取单个数字员工详情"""
    try:
        registry = master_agent.subagent_registry
        if not registry:
            return {"success": False, "error": "子智能体注册表未初始化", "debug": "subagent_registry is None"}

        # 多 worker 部署时从 DB 刷新定制 subagent
        registry.load_from_db()

        # 处理主智能体请求
        if agent_id == "main":
            data = {
                "agent_id": "main",
                "name": "CEO智能体",
                "description": "系统主智能体，具备通用能力和工具",
                "version": "1.0.0",
                "author": "system",
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
        logger.opt(exception=True).error(f"获取数字员工详情失败: {e}")
        return {"success": False, "error": "获取数字员工详情失败", "debug": str(e)}


@router.get("/{agent_id}/content")
async def get_subagent_content(request: Request, agent_id: str):
    """获取 SUBAGENT.md 原始内容"""
    try:
        registry = master_agent.subagent_registry
        if not registry:
            return {"success": False, "error": "子智能体注册表未初始化", "debug": "subagent_registry is None"}

        # 多 worker 部署时从 DB 刷新定制 subagent
        registry.load_from_db()

        # 处理主智能体请求
        if agent_id == "main":
            return {"success": True, "data": "# CEO智能体\n\n系统主智能体，具备通用能力和工具"}

        content = registry.get_content(agent_id)
        if not content:
            return {"success": False, "error": f"数字员工不存在: {agent_id}", "debug": f"agent_id={agent_id} not found"}

        return {"success": True, "data": content}

    except Exception as e:
        logger.opt(exception=True).error(f"获取数字员工内容失败: {e}")
        return {"success": False, "error": "获取数字员工内容失败", "debug": str(e)}


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
        logger.opt(exception=True).error(f"获取工具列表失败: {e}")
        return {"success": False, "error": "获取工具列表失败", "debug": str(e)}
