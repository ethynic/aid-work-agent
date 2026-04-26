"""
数字员工管理 API

提供数字员工（子智能体）的 CRUD 管理、另存为、AI 完善等接口。
仅管理员可访问。
"""

from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.api.auth import get_current_user
from src.config.settings import settings
from src.core.agent import master_agent
from src.models.subagent import SubagentConfig
from src.subagents.loader import SubagentLoader

router = APIRouter(prefix="/api/admin", tags=["数字员工管理"])

# 定制子智能体存储目录
CUSTOM_SUBAGENTS_DIR = Path("./storage/subagents2")


# ============== 请求/响应模型 ==============

class CreateSubagentRequest(BaseModel):
    agent_id: str = Field(..., description="目录名，如 my-custom-agent")
    name: str = Field(..., description="显示名称")
    description: str = ""
    capabilities: List[str] = Field(default_factory=list)
    triggers: Dict[str, Any] = Field(default_factory=dict)
    tools: Dict[str, Any] = Field(default_factory=lambda: {"inherit": True})
    skills: Dict[str, Any] = Field(default_factory=dict)
    context: Dict[str, Any] = Field(default_factory=dict)
    system_prompt: str = ""


class DuplicateSubagentRequest(BaseModel):
    new_agent_id: str = Field(..., description="新的目录名")
    new_name: str = Field(..., description="新的显示名称")


class AiEnhanceRequest(BaseModel):
    content: str = Field(..., description="当前编辑器中的完整 SUBAGENT.md 内容")


# ============== 管理员权限检查 ==============

def _check_admin(request: Request) -> Optional[Dict]:
    """
    检查当前用户是否为管理员。
    返回用户信息（dict），非管理员返回 None。
    """
    user = get_current_user(request)
    if not user:
        return None

    admin_phones = getattr(settings, "admin", None)
    if admin_phones:
        phone_list = getattr(admin_phones, "phones", [])
        user_phone = user.get("phone", "")
        if user_phone in phone_list:
            return user

    # 如果未配置 admin.phones，默认第一个用户为管理员（开发阶段）
    logger.warning(f"Admin phones not configured or user not in admin list, phone={user.get('phone')}")
    return None


def _require_admin(request: Request) -> Dict:
    """管理员权限校验，失败直接返回 403 响应"""
    admin = _check_admin(request)
    if not admin:
        return None
    return admin


def _sanitize_error_info(error_msg: str) -> str:
    """过滤错误信息中的敏感信息"""
    if not error_msg:
        return error_msg
    import re
    patterns = [
        r'password["\s:=]+\S+',
        r'passwd["\s:=]+\S+',
        r'secret["\s:=]+\S+',
        r'token["\s:=]+\S+',
        r'api[_-]?key["\s:=]+\S+',
        r'access[_-]?key["\s:=]+\S+',
        r'private[_-]?key["\s:=]+\S+',
        r'auth[_-]?token["\s:=]+\S+',
    ]
    sanitized = error_msg
    for pattern in patterns:
        sanitized = re.sub(pattern, lambda m: m.group(0).split('=')[0] + '=***', sanitized, flags=re.IGNORECASE)
    return sanitized


def _error_response(error: str, debug: str, status_code: int = 500):
    """标准错误响应"""
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": error,
            "debug": _sanitize_error_info(debug),
        },
    )


# ============== API 接口 ==============

@router.get("/subagents")
async def list_subagents(request: Request):
    """列出所有数字员工（内置+定制，标记类型）"""
    try:
        admin = _require_admin(request)
        if admin is None:
            return _error_response("无管理员权限", "User not in admin phone list", 403)

        registry = master_agent.subagent_registry
        if not registry:
            return _error_response("子智能体注册表未初始化", "subagent_registry is None")

        # 多 worker 部署时从磁盘刷新定制 subagent，确保读到最新数据
        if registry._custom_dir:
            registry._load_custom(registry._custom_dir)

        items = registry.get_all_subagents_with_type()
        return {"success": True, "data": items}

    except Exception as e:
        logger.error(f"列出数字员工失败: {e}", exc_info=True)
        return _error_response("列出数字员工失败", str(e))


@router.get("/subagents/{agent_id}")
async def get_subagent_detail(request: Request, agent_id: str):
    """获取单个数字员工详情"""
    try:
        admin = _require_admin(request)
        if admin is None:
            return _error_response("无管理员权限", "User not in admin phone list", 403)

        registry = master_agent.subagent_registry
        if not registry:
            return _error_response("子智能体注册表未初始化", "subagent_registry is None")

        # 多 worker 部署时从磁盘刷新定制 subagent，确保读到最新数据
        if registry._custom_dir:
            registry._load_custom(registry._custom_dir)

        config = registry.get(agent_id)
        if not config:
            return _error_response(f"数字员工不存在: {agent_id}", f"agent_id={agent_id} not found", 404)

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
        return {
            "success": True,
            "data": data
        }

    except Exception as e:
        logger.error(f"获取数字员工详情失败: {e}", exc_info=True)
        return _error_response("获取数字员工详情失败", str(e))


@router.get("/subagents/{agent_id}/content")
async def get_subagent_content(request: Request, agent_id: str):
    """获取 SUBAGENT.md 原始内容（用于编辑）"""
    try:
        admin = _require_admin(request)
        if admin is None:
            return _error_response("无管理员权限", "User not in admin phone list", 403)

        registry = master_agent.subagent_registry
        if not registry:
            return _error_response("子智能体注册表未初始化", "subagent_registry is None")

        # 多 worker 部署时从磁盘刷新定制 subagent，确保读到最新数据
        if registry._custom_dir:
            registry._load_custom(registry._custom_dir)

        content = registry.get_content(agent_id)
        if not content:
            return _error_response(f"数字员工不存在: {agent_id}", f"agent_id={agent_id} not found", 404)

        return {"success": True, "data": content}

    except Exception as e:
        logger.error(f"获取数字员工内容失败: {e}", exc_info=True)
        return _error_response("获取数字员工内容失败", str(e))


@router.get("/skills")
async def list_available_skills(request: Request):
    """获取可选 skill 列表"""
    try:
        admin = _require_admin(request)
        if admin is None:
            return _error_response("无管理员权限", "User not in admin phone list", 403)

        skill_registry = master_agent.skill_registry
        if not skill_registry:
            return {"success": True, "data": []}

        skills = skill_registry.list_skills()
        return {"success": True, "data": skills}

    except Exception as e:
        logger.error(f"获取技能列表失败: {e}", exc_info=True)
        return _error_response("获取技能列表失败", str(e))


@router.get("/tools")
async def list_available_tools(request: Request):
    """获取可选工具列表"""
    try:
        admin = _require_admin(request)
        if admin is None:
            return _error_response("无管理员权限", "User not in admin phone list", 403)

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
        return _error_response("获取工具列表失败", str(e))


@router.post("/subagents")
async def create_subagent(request: Request, body: CreateSubagentRequest):
    """创建定制数字员工"""
    try:
        admin = _require_admin(request)
        if admin is None:
            return _error_response("无管理员权限", "User not in admin phone list", 403)

        registry = master_agent.subagent_registry
        if not registry:
            return _error_response("子智能体注册表未初始化", "subagent_registry is None")

        # agent_id 格式校验（只允许字母、数字、下划线、连字符）
        if not re.match(r'^[a-zA-Z0-9_-]+$', body.agent_id):
            return _error_response("ID 只能包含字母、数字、下划线和连字符", f"invalid agent_id format: {body.agent_id}", 400)

        # 唯一性校验
        if not registry.validate_id_uniqueness(body.agent_id):
            return _error_response(f"ID 已存在: {body.agent_id}", f"agent_id '{body.agent_id}' already exists", 400)
        if not registry.validate_name_uniqueness(body.name):
            return _error_response(f"名称已存在: {body.name}", f"name '{body.name}' already exists", 400)

        # 构建配置并序列化
        config = SubagentConfig(
            name=body.name,
            description=body.description,
            capabilities=body.capabilities,
            triggers=body.triggers,
            tools=body.tools,
            skills=body.skills,
            context=body.context,
            system_prompt=body.system_prompt,
            author=admin.get("username", admin.get("phone", "admin")),
        )
        content = SubagentLoader.serialize_to_subagent_md(config, body.system_prompt)

        saved = registry.save_custom_subagent(body.agent_id, config, content)

        logger.info(f"后端日志：管理员 {admin.get('phone')} 创建数字员工 {body.agent_id}")
        return {
            "success": True,
            "data": {
                "agent_id": saved.dir_name or body.agent_id,
                "name": saved.name,
                "type": "custom",
            },
        }

    except Exception as e:
        logger.error(f"创建数字员工失败: {e}", exc_info=True)
        return _error_response("创建数字员工失败", str(e))


@router.put("/subagents/{agent_id}")
async def update_subagent(request: Request, agent_id: str, body: CreateSubagentRequest):
    """更新定制数字员工"""
    try:
        admin = _require_admin(request)
        if admin is None:
            return _error_response("无管理员权限", "User not in admin phone list", 403)

        registry = master_agent.subagent_registry
        if not registry:
            return _error_response("子智能体注册表未初始化", "subagent_registry is None")

        # 内置不可修改
        if registry.is_builtin(agent_id):
            return _error_response("内置数字员工不可修改", f"agent_id={agent_id} is builtin", 400)

        # 唯一性校验（排除自身）
        existing = registry.get(agent_id)
        existing_name = existing.name if existing else None
        if not registry.validate_name_uniqueness(body.name, exclude_name=existing_name):
            return _error_response(f"名称已存在: {body.name}", f"name '{body.name}' already exists", 400)

        config = SubagentConfig(
            name=body.name,
            description=body.description,
            capabilities=body.capabilities,
            triggers=body.triggers,
            tools=body.tools,
            skills=body.skills,
            context=body.context,
            system_prompt=body.system_prompt,
        )
        content = SubagentLoader.serialize_to_subagent_md(config, body.system_prompt)

        saved = registry.save_custom_subagent(agent_id, config, content)

        logger.info(f"后端日志：管理员 {admin.get('phone')} 更新数字员工 {agent_id}")
        return {
            "success": True,
            "data": {
                "agent_id": saved.dir_name or agent_id,
                "name": saved.name,
                "type": "custom",
            },
        }

    except Exception as e:
        logger.error(f"更新数字员工失败: {e}", exc_info=True)
        return _error_response("更新数字员工失败", str(e))


@router.delete("/subagents/{agent_id}")
async def delete_subagent(request: Request, agent_id: str):
    """删除定制数字员工"""
    try:
        admin = _require_admin(request)
        if admin is None:
            return _error_response("无管理员权限", "User not in admin phone list", 403)

        registry = master_agent.subagent_registry
        if not registry:
            return _error_response("子智能体注册表未初始化", "subagent_registry is None")

        if registry.is_builtin(agent_id):
            return _error_response("内置数字员工不可删除", f"agent_id={agent_id} is builtin", 400)

        success = registry.delete_custom_subagent(agent_id)
        if not success:
            return _error_response(f"数字员工不存在: {agent_id}", f"agent_id={agent_id} not found or delete failed", 404)

        logger.info(f"后端日志：管理员 {admin.get('phone')} 删除数字员工 {agent_id}")
        return {"success": True, "message": f"已删除数字员工: {agent_id}"}

    except Exception as e:
        logger.error(f"删除数字员工失败: {e}", exc_info=True)
        return _error_response("删除数字员工失败", str(e))


@router.post("/subagents/{agent_id}/duplicate")
async def duplicate_subagent(request: Request, agent_id: str, body: DuplicateSubagentRequest):
    """另存为（内置/定制 → 新的定制）"""
    try:
        admin = _require_admin(request)
        if admin is None:
            return _error_response("无管理员权限", "User not in admin phone list", 403)

        registry = master_agent.subagent_registry
        if not registry:
            return _error_response("子智能体注册表未初始化", "subagent_registry is None")

        # 唯一性校验
        if not registry.validate_id_uniqueness(body.new_agent_id):
            return _error_response(f"ID 已存在: {body.new_agent_id}", f"agent_id '{body.new_agent_id}' already exists", 400)

        # 获取原始内容
        original_content = registry.get_content(agent_id)
        if not original_content:
            return _error_response(f"数字员工不存在: {agent_id}", f"agent_id={agent_id} not found", 404)

        # 替换 name 字段
        import re
        updated_content = re.sub(
            r"^(name:\s*).+$",
            rf"\1{body.new_name}",
            original_content,
            count=1,
            flags=re.MULTILINE,
        )

        # 保存为新定制
        SubagentLoader.save_subagent_md(CUSTOM_SUBAGENTS_DIR, body.new_agent_id, updated_content)
        registry._load_custom(CUSTOM_SUBAGENTS_DIR)

        logger.info(f"后端日志：管理员 {admin.get('phone')} 另存为数字员工 {agent_id} -> {body.new_agent_id}")
        return {
            "success": True,
            "data": {
                "agent_id": body.new_agent_id,
                "name": body.new_name,
                "type": "custom",
            },
        }

    except Exception as e:
        logger.error(f"另存为数字员工失败: {e}", exc_info=True)
        return _error_response("另存为数字员工失败", str(e))


@router.post("/subagents/{agent_id}/ai-enhance")
async def ai_enhance_subagent(request: Request, agent_id: str, body: AiEnhanceRequest):
    """
    AI 完善：调用 LLM 优化 SUBAGENT.md 全部内容。
    同步请求，返回优化前后的内容供前端对比。
    """
    try:
        admin = _require_admin(request)
        if admin is None:
            return _error_response("无管理员权限", "User not in admin phone list", 403)

        registry = master_agent.subagent_registry
        if not registry:
            return _error_response("子智能体注册表未初始化", "subagent_registry is None")

        if not body.content or not body.content.strip():
            return _error_response("内容不能为空", "content is empty", 400)

        # 系统提示词
        system_prompt = """你是一个数字员工（子智能体）配置优化专家。用户会提供一个 SUBAGENT.md 文件的当前内容，请你优化它。

## 输出要求
- **直接输出优化后的 SUBAGENT.md 内容**，不要包含任何解释、说明、前言、总结
- 不要输出 "以下是优化后的内容"、"Here is the optimized..."、"已优化" 等任何引导文字
- 输出格式：包含 YAML frontmatter（以 --- 开头和结尾）和 Markdown body 的完整内容
- 不要使用 markdown 代码块包裹（不要用 ```markdown ```）
- **description 字段必须使用中文**
- **system_prompt（Markdown body）必须使用中文**

## 优化方向
1. **description**：用中文使描述更精准、更专业，突出核心价值和适用场景
2. **capabilities**：补充遗漏的能力标签，使用英文小写下划线命名
3. **triggers.file_patterns**：根据智能体用途补充合理的文件触发模式
4. **skills.allowed**：根据能力需要补充或调整技能配置
5. **system_prompt（Markdown body）**：
   - 使用中文优化角色定义和职责描述
   - 补充工作流程、注意事项、禁止行为等
   - 确保提示词结构化，使用 Markdown 格式"""

        # 调用 LLM
        from src.llm.gateway import llm_gateway

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请优化以下 SUBAGENT.md 内容：\n\n{body.content}"},
        ]

        logger.info(f"后端日志：AI 完善 {agent_id}，开始调用 LLM")

        result = await llm_gateway.chat(
            messages=messages,
            temperature=0.3,
            max_tokens=8192,
        )

        enhanced_content = result.get("content", "").strip()

        if not enhanced_content:
            return _error_response("AI 返回内容为空", "LLM returned empty content", 500)

        # 清理 LLM 可能包裹的 markdown 代码块
        if enhanced_content.startswith("```"):
            lines = enhanced_content.split("\n")
            # 去掉首尾的 ``` 行
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            enhanced_content = "\n".join(lines).strip()

        logger.info(f"后端日志：AI 完善 {agent_id} 完成，原始长度={len(body.content)}，优化后长度={len(enhanced_content)}")

        return {
            "success": True,
            "data": {
                "original_content": body.content,
                "enhanced_content": enhanced_content,
            },
        }

    except Exception as e:
        logger.error(f"AI 完善失败: {e}", exc_info=True)
        return _error_response("AI 完善失败，请稍后重试", str(e))
