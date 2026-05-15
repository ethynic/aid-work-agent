#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SkillCompleteTool - 标记技能执行完成

标记当前技能执行完成并压缩上下文。
"""

from collections import deque
from datetime import datetime
from typing import Dict, Any, List

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class SkillCompleteInput(BaseModel):
    """完成技能参数"""
    skill: str = Field(..., description="技能名称")
    summary: str = Field(..., description="结果摘要，一句简洁的结果描述（1-3句话）")
    session_id: str = Field(..., description="会话ID")


class SkillCompleteTool(BaseTool):
    """技能完成工具"""

    name = "skill_complete"
    description = "标记技能执行完成（必须在所有步骤完成后调用）。调用后系统会自动清理中间过程，仅保留摘要到对话历史。"
    usage_guide = ""
    display_name = "完成技能"
    category = "skill"
    InputModel = SkillCompleteInput

    def __init__(self):
        """无外部依赖，通过 set_context 注入运行时状态"""
        self._active_sessions = None
        self._memory_cache = None

    def set_context(self, active_sessions: dict, memory_cache: dict):
        """
        注入运行时上下文

        Args:
            active_sessions: Agent._active_skill_sessions 字典
            memory_cache: Agent.memory._cache 字典
        """
        self._active_sessions = active_sessions
        self._memory_cache = memory_cache

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        标记技能完成并压缩上下文

        Args:
            skill: 技能名称
            summary: 结果摘要
            session_id: 会话ID

        Returns:
            结果字典
        """
        skill_name = kwargs.get("skill", "")
        summary = kwargs.get("summary", "")
        session_id = kwargs.get("session_id", "")

        if not skill_name:
            return {"success": False, "error": "No skill name provided"}

        if not self._active_sessions or skill_name not in self._active_sessions:
            return {"success": False, "error": f"没有找到活跃的技能会话: {skill_name}"}

        # 执行压缩
        self._compress_skill_context(session_id, skill_name, summary)

        return {"success": True, "message": f"技能 {skill_name} 已完成并清理上下文"}

    def compress(self, session_id: str, skill_name: str, summary: str) -> None:
        """公开接口：压缩 Skill 上下文（委托给 _compress_skill_context）"""
        self._compress_skill_context(session_id, skill_name, summary)

    def _compress_skill_context(
        self,
        session_id: str,
        skill_name: str,
        summary: str
    ) -> None:
        """
        压缩 Skill 执行过程中的中间消息，仅保留摘要。

        Args:
            session_id: 会话 ID
            skill_name: 技能名称
            summary: 技能执行结果摘要
        """
        try:
            session = self._active_sessions.get(skill_name)
            if not session:
                logger.warning(f"后端日志：_compress_skill_context 未找到活跃的 SkillSession: {skill_name}")
                return

            messages = self._memory_cache.get(session_id)
            if not messages:
                return

            original_count = len(messages)
            msg_count_before = session.message_count_before

            # 诊断日志：压缩前记录所有消息摘要
            before_roles = [f"{m.get('role', '?')}:{m.get('content', '')[:30]}" for m in messages]
            logger.info(
                f"[SKILL_COMPRESS_DIAG] 开始压缩 | session={session_id} | "
                f"skill={skill_name} | msg_count_before={msg_count_before} | "
                f"total_msgs={original_count} | msgs={before_roles}"
            )

            # 保留 Skill 开始之前的消息
            before_skill = list(messages)[:msg_count_before]

            # 保留 Skill 执行后添加的用户消息（避免并发请求添加的消息被丢弃）
            after_messages = list(messages)[msg_count_before:]
            preserved_user_messages = [
                msg for msg in after_messages
                if msg.get("role") == "user"
            ]
            if preserved_user_messages:
                preserved_roles = [f"user:{m.get('content', '')[:30]}" for m in preserved_user_messages]
                logger.info(
                    f"[SKILL_COMPRESS_DIAG] 保留并发用户消息 | "
                    f"count={len(preserved_user_messages)} | msgs={preserved_roles}"
                )

            # 构建精简摘要消息
            summary_message = {
                "role": "system",
                "content": f"[技能执行记录] 使用技能「{skill_name}」完成任务。结果：{summary}",
                "timestamp": datetime.now().isoformat(),
                "_skill_summary": True
            }

            # 重建消息列表（使用 deque 恢复 maxlen 限制）
            new_messages = deque(before_skill + [summary_message] + preserved_user_messages)
            self._memory_cache[session_id] = new_messages

            after_roles = [f"{m.get('role', '?')}:{m.get('content', '')[:30]}" for m in new_messages]
            logger.info(
                f"[SKILL_COMPRESS_DIAG] 压缩完成 | session={session_id} | "
                f"original={original_count} | after={len(new_messages)} | "
                f"msgs={after_roles}"
            )

            # 清理 Skill Session
            if skill_name in self._active_sessions:
                del self._active_sessions[skill_name]

            logger.info(f"后端日志：Skill 上下文已压缩", extra={
                "skill_name": skill_name,
                "session_id": session_id,
                "original_messages": original_count,
                "compressed_messages": len(new_messages),
                "saved_messages": original_count - len(new_messages)
            })
        except Exception as e:
            logger.error(f"[SKILL_COMPRESS_DIAG] 压缩异常 | session={session_id} | skill={skill_name} | error={e}", exc_info=True)
            # 清理 Skill Session，避免残留导致后续问题
            if self._active_sessions and skill_name in self._active_sessions:
                del self._active_sessions[skill_name]
