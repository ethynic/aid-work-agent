#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DelegateToSubagentTool - 委派任务给子智能体

将任务委派给专业的子智能体执行。
"""

import uuid
from typing import Dict, Any, List, Optional, Callable, Coroutine

from loguru import logger

from src.tools.base import BaseTool


class DelegateToSubagentTool(BaseTool):
    """子智能体委派工具"""

    name = "delegate_to_subagent"
    description = "将任务委派给专业的子智能体执行"
    category = "agent"

    def __init__(self, subagent_registry, subagent_executor):
        """
        Args:
            subagent_registry: SubagentRegistry 实例
            subagent_executor: SubagentExecutor 实例
        """
        self.subagent_registry = subagent_registry
        self.subagent_executor = subagent_executor

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        委派任务给子智能体

        Args:
            subagent_name: 子智能体名称
            task_description: 任务描述
            context_needed: 上下文关键词（可选）
            session_id: 会话ID（可选）
            progress_callback: 进度回调函数（可选）

        Returns:
            委派结果字典
        """
        subagent_name = kwargs.get("subagent_name", "")
        task_description = kwargs.get("task_description", "")
        context_needed = kwargs.get("context_needed")
        session_id = kwargs.get("session_id")
        progress_callback = kwargs.get("progress_callback")

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

        # Check if subagent exists
        config = self.subagent_registry.get(subagent_name)
        if not config:
            return {
                "success": False,
                "error": f"Subagent '{subagent_name}' not found",
                "available_subagents": self.subagent_registry.list_subagents()
            }

        try:
            # Generate task ID
            task_id = f"delegate_{uuid.uuid4().hex[:8]}"

            # 创建子智能体专用的回调包装器
            async def subagent_progress_wrapper(event):
                """将子智能体的事件转发给上层 progress_callback，避免嵌套包装"""
                if not progress_callback:
                    return
                if isinstance(event, str):
                    await progress_callback(event)
                elif isinstance(event, dict):
                    event_type = event.get("type", "")
                    event_data = event.get("data", "")
                    if event_type == "progress":
                        await progress_callback(event_data if isinstance(event_data, str) else str(event_data))
                    elif event_type in ("tool_start", "tool_result"):
                        tool_name = event.get("toolName", event.get("tool_name", ""))
                        if event_type == "tool_start":
                            await progress_callback(f"🔧 正在执行 {tool_name}...")
                        elif event_type == "tool_result":
                            success = event.get("success", True)
                            if success:
                                await progress_callback(f"✅ {tool_name} 执行完成")
                            else:
                                error = event.get("result", {}).get("error", "未知错误") if isinstance(event.get("result"), dict) else str(event.get("result", ""))
                                await progress_callback(f"❌ {tool_name} 执行失败: {error}")
                    elif event_type == "thinking":
                        await progress_callback(event_data if isinstance(event_data, str) else str(event_data))
                    else:
                        await progress_callback(str(event))
                else:
                    await progress_callback(str(event))

            # Delegate to subagent
            response = await self.subagent_executor.delegate(
                task_id=task_id,
                subagent_name=subagent_name,
                task_description=task_description,
                session_id=session_id or "default",
                progress_callback=subagent_progress_wrapper,
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
