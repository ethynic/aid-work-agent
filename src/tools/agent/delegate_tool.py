#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DelegateToSubagentTool - 委派任务给子智能体

将任务委派给专业的子智能体执行。
"""

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class DelegateToSubagentInput(BaseModel):
    """delegate_to_subagent 参数 schema，暴露给 LLM 显式感知字段。"""

    subagent_name: str = Field(..., description="子智能体名称")
    task_description: str = Field(..., description="任务描述，需包含完整上下文（用户需求、约束、期望产出）")
    image_paths: Optional[List[str]] = Field(
        None,
        description=(
            "用户上传图片的完整路径列表，仅当任务含图片且子智能体支持视觉（如 video-agent）时传入。"
            "不要把图片路径只写在 task_description 里，必须用此参数显式传递。"
        ),
    )
    context_needed: Optional[List[str]] = Field(None, description="上下文关键词")
    session_id: Optional[str] = Field(None, description="会话ID")
    user_id: Optional[str] = Field(None, description="用户ID")


class DelegateToSubagentTool(BaseTool):
    """子智能体委派工具"""

    # 控制工具：不进普通 Registry，由 ToolControlSet 晚绑定依赖，Agent loop 特殊处理。
    catalog = False
    name = "delegate_to_subagent"
    description = (
        "将任务委派给专业的子智能体执行。"
        "⚠️ 如果任务只需要委派给一个子智能体就能完成，直接调用此工具，不需要先 create_plan。"
        "task_description 必须包含完整信息（包括用户上传文件的完整路径）。"
        "若任务含用户上传图片且子智能体支持视觉（如 video-agent），必须用 image_paths 传完整路径列表，"
        "不要把图片路径只写在 task_description 里。"
    )
    display_name = "调用子智能体"
    category = "agent"
    InputModel = DelegateToSubagentInput

    def __init__(self, subagent_registry, subagent_executor, authorizer=None):
        """
        Args:
            subagent_registry: SubagentRegistry 实例
            subagent_executor: SubagentExecutor 实例
        """
        self.subagent_registry = subagent_registry
        self.subagent_executor = subagent_executor
        if authorizer is None:
            from src.tools.delegation_policy import DelegationAuthorizer
            authorizer = DelegationAuthorizer()
        self.authorizer = authorizer

    def get_usage_guide(self, **kwargs) -> str:
        """动态生成委派工具使用指南，包含当前可用的子智能体列表"""
        subagent_descriptions = kwargs.get("subagent_descriptions", "")
        if not subagent_descriptions:
            if self.subagent_registry:
                subagent_descriptions = self.subagent_registry.get_descriptions()
            if not subagent_descriptions:
                return ""

        return f"""**可用子智能体：**
{subagent_descriptions}"""

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        委派任务给子智能体

        Args:
            subagent_name: 子智能体名称
            task_description: 任务描述
            image_paths: 用户上传图片的完整路径列表（可选，传给多模态子智能体如 video-agent）
            context_needed: 上下文关键词（可选）
            session_id/user_id: 仅保留历史调用签名兼容，身份只从执行上下文读取

        Returns:
            委派结果字典
        """
        subagent_name = kwargs.get("subagent_name", "")
        task_description = kwargs.get("task_description", "")
        image_paths = kwargs.get("image_paths")
        context_needed = kwargs.get("context_needed")
        # session/user 属于可信执行上下文；同名模型参数只保留 schema 兼容，不能作为身份来源。
        from src.tools.context import current_tool_execution_context
        execution_context = current_tool_execution_context()
        session_id = execution_context.session_id if execution_context else None
        user_id = execution_context.user_id if execution_context else None

        if not subagent_name:
            return {
                "success": False,
                "error": "No subagent name provided"
            }

        if not task_description:
            return {
                "success": False,
                "error": "No task description provided"
            }

        # Check if subagent exists（registry → DB 按需加载）
        config = self.subagent_registry.get(subagent_name)
        if not config:
            from src.subagents.factory import AgentFactory
            config = await asyncio.to_thread(
                AgentFactory._load_single_from_db,
                self.subagent_registry,
                subagent_name,
            )
        if not config:
            return {
                "success": False,
                "error": f"Subagent '{subagent_name}' not found",
                "available_subagents": self.subagent_registry.list_subagents()
            }

        # 生产 authorizer 会同步查询订阅数据库；这里是 async 工具入口，必须
        # 移到工作线程，避免一次授权查询阻塞整个 worker 的事件循环。
        try:
            authorized = await asyncio.to_thread(
                self.authorizer.authorize,
                execution_context,
                subagent_name,
                config,
            )
        except Exception:
            logger.opt(exception=True).error(
                "后端日志：子智能体委派授权查询失败 subagent={}",
                subagent_name,
            )
            return {
                "success": False,
                "error_code": "SUBAGENT_AUTHORIZATION_UNAVAILABLE",
                "error": "暂时无法校验子智能体使用权限，请稍后重试",
            }
        if not authorized:
            return {
                "success": False,
                "permission_denied": True,
                "error_code": "SUBAGENT_PERMISSION_DENIED",
                "error": "当前租户无权使用该子智能体",
            }

        try:
            # Generate task ID
            task_id = f"delegate_{uuid.uuid4().hex[:8]}"

            # 规范化 image_paths：去重 + 过滤空值
            if image_paths:
                image_paths = [p for p in image_paths if p]
                if not image_paths:
                    image_paths = None

            if image_paths:
                logger.info(f"[DELEGATE] image_paths passed to subagent '{subagent_name}': {image_paths}")

            # Delegate to subagent
            response = await self.subagent_executor.delegate(
                task_id=task_id,
                subagent_name=subagent_name,
                task_description=task_description,
                session_id=session_id or "default",
                user_id=user_id,
                image_paths=image_paths,
            )

            if not response.success:
                return {
                    "success": False,
                    "error": response.error or "Delegation failed"
                }

            # Wait for result
            record = await self.subagent_executor.wait_for_result(
                response.execution_id,
                timeout=7200  # 2 hours timeout
            )

            if record:
                # 检查是否为 CLARIFYING 状态
                if record.is_clarifying():
                    question = record.clarification_request or "需要补充信息"
                    logger.info(f"[AGENT] Subagent '{subagent_name}' requesting clarification: {question[:100]}...")

                    return {
                        "success": False,
                        "subagent_name": subagent_name,
                        "execution_id": response.execution_id,
                        "status": "clarifying",
                        "question": question,
                        "error": f"子智能体 '{subagent_name}' 需要补充信息：{question}",
                    }

                return {
                    "success": record.status == "completed",
                    "subagent_name": subagent_name,
                    "execution_id": response.execution_id,
                    "result": record.result,
                    "summary": record.summary,
                    "error": record.error,
                    "token_usage": record.token_usage
                }

            return {
                "success": False,
                "error": "Delegation timed out"
            }

        except Exception as e:
            logger.error(f"Delegation failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
