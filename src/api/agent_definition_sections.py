"""
子智能体 Prompt 分段管理 API

分段值作为模板变量存储，运行时由 render_sections() 填充到 prompt 模板中。
模板中使用 {{section_key}} 双花括号占位符（Phase 4.0 起）。
"""
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from src.api.agent_definitions import _require_admin, _success, _error
from src.services.subagent_definition_service import SubagentDefinitionService

router = APIRouter(
    prefix="/api/admin/agent-definitions",
    tags=["Prompt 分段管理"],
)


class SaveSectionRequest(BaseModel):
    content: str = Field(default="", description="分段内容")
    section_key: str = Field(..., description="分段键名")


@router.get("/{agent_id}/sections/keys")
async def get_section_keys(request: Request, agent_id: str):
    """从 production 模板中解析出所有 {{section_key}} 变量名"""
    try:
        _require_admin(request)
        keys = SubagentDefinitionService.get_section_keys(agent_id)
        return _success(keys)
    except Exception as e:
        if "401" in str(e) or "403" in str(e):
            raise
        logger.error(f"获取分段键列表失败: {e}")
        return _error("获取分段键列表失败")


@router.get("/{agent_id}/sections")
async def get_sections(request: Request, agent_id: str):
    """获取智能体的 prompt 分段"""
    try:
        _require_admin(request)
        sections = SubagentDefinitionService.get_sections(agent_id)
        return _success(sections)
    except Exception as e:
        if "401" in str(e) or "403" in str(e):
            raise
        logger.error(f"获取分段失败: {e}")
        return _error("获取分段失败")


@router.put("/{agent_id}/sections/{section_key}")
async def save_section(request: Request, agent_id: str, section_key: str, body: SaveSectionRequest):
    """保存单个分段值"""
    try:
        admin = _require_admin(request)
        if body.section_key != section_key:
            return _error("section_key 不匹配")
        result = SubagentDefinitionService.save_section(
            agent_id=agent_id,
            section_key=section_key,
            content=body.content,
            updated_by=admin.get("user_id"),
        )
        if not result:
            return _error("保存分段失败")
        return _success(result)
    except Exception as e:
        if "401" in str(e) or "403" in str(e):
            raise
        logger.error(f"保存分段失败: {e}")
        return _error("保存分段失败")


class OptimizeSectionRequest(BaseModel):
    content: str = Field(..., min_length=1, description="当前分段内容")
    section_key: str = Field(..., description="分段键名")
    agent_name: Optional[str] = None
    agent_description: Optional[str] = None


@router.post("/{agent_id}/sections/{section_key}/optimize")
async def optimize_section(request: Request, agent_id: str, section_key: str, body: OptimizeSectionRequest):
    """AI 优化单个分段"""
    try:
        _require_admin(request)

        section_hints = {
            "role_description": "优化角色描述，使角色定位更清晰、专业，职责边界更明确",
            "responsibilities": "优化职责描述，使每项职责具体、可执行，避免模糊表述",
            "workflow": "优化工作流程，使步骤清晰、逻辑严密、覆盖常见场景和异常处理",
            "reply_style": "优化回复风格指南，使风格指导更自然、更易遵循，包含正反示例",
            "other_notes": "优化补充说明，使注意事项更全面、更实用，强调容易出错的点",
        }

        optimize_hint = section_hints.get(section_key, "优化内容，使指令更清晰、具体、可执行")

        system_prompt = f"""你是一个 AI 提示词优化专家。用户会提供一个智能体 system prompt 的某个分段内容，请你优化它。

## 输出要求
- 直接输出优化后的内容，不要包含任何解释、前言、总结
- 使用中文
- 使用 Markdown 格式
- {optimize_hint}
- 保持核心意图不变，不要添加原内容没有的新要求

## 智能体信息
- 名称: {body.agent_name or agent_id}
- 描述: {body.agent_description or '未知'}"""

        from src.llm.gateway import llm_gateway
        result = await llm_gateway.chat_lite(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请优化以下分段内容：\n\n{body.content}"},
            ],
            temperature=0.3,
            max_tokens=4096,
        )

        from src.config.settings import settings
        from src.services.session_record import record_admin_llm_usage
        record_admin_llm_usage(
            result,
            tenant_id=getattr(request.state, "tenant_id", None),
            user_id=getattr(request.state, "user_id", None),
            source_label=f"optimize_section_{section_key}",
            model=settings.llm.get_lite_model(),  # chat_lite 实际消耗 lite 模型，按 lite 单价计费
        )

        optimized = result.get("content", "").strip()
        if not optimized:
            return _error("AI 返回内容为空", 500)

        # Strip markdown code block wrappers if present
        if optimized.startswith("```"):
            lines = optimized.split("\n")
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            optimized = "\n".join(lines).strip()

        return _success({"content": optimized})
    except Exception as e:
        if "401" in str(e) or "403" in str(e):
            raise
        logger.error(f"AI 优化分段失败: {e}")
        return _error("AI 优化失败，请稍后重试")
