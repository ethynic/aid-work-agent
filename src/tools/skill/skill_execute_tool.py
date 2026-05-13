#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SkillExecuteTool - 在技能上下文中执行命令

加载技能后使用此工具运行 pdftotext、python 脚本等命令。
"""

import base64
import re
from typing import Dict, Any, Optional
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field

from src.tools.base import BaseTool


class SkillExecuteInput(BaseModel):
    """执行技能参数"""
    skill: str = Field(..., description="技能名称")
    command: Optional[str] = Field(None, description="要执行的命令（可选）")
    files: Optional[Dict[str, str]] = Field(None, description="文件字典（文件名 -> base64 内容，仅用于二进制文件）")
    content: Optional[str] = Field(None, description="纯文本内容（如Markdown），将自动写入工作目录的 content.md 文件。创建Word文档时优先使用此参数传递Markdown内容，不要使用files参数做base64编码")
    session_id: Optional[str] = Field(None, description="会话ID")
    user_id: Optional[str] = Field(None, description="用户ID（可选）")
    workdir: Optional[str] = Field(None, description="工作目录（可选）")


class SkillExecuteTool(BaseTool):
    """技能命令执行工具"""

    name = "skill_execute"
    description = "在技能上下文中执行命令（仅当操作指南要求时才使用，如 python scripts/xxx.py）。引导式技能（无脚本的技能）通常不需要调用此工具。"
    usage_guide = ""
    display_name = "执行技能"
    category = "skill"
    InputModel = SkillExecuteInput

    def __init__(self, skill_executor, skill_registry):
        """
        Args:
            skill_executor: SkillExecutor 实例
            skill_registry: SkillRegistry 实例
        """
        self.skill_executor = skill_executor
        self.skill_registry = skill_registry

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行技能命令

        Args:
            skill: 技能名称
            command: 要执行的命令（可选）
            files: 文件字典（文件名 -> base64 内容）
            session_id: 会话ID
            user_id: 用户ID（可选）
            workdir: 工作目录（可选）

        Returns:
            执行结果字典
        """
        skill_name = kwargs.get("skill", "")
        command = kwargs.get("command", "") or None  # 空字符串转为 None
        files = kwargs.get("files", {})
        session_id = kwargs.get("session_id")
        user_id = kwargs.get("user_id")
        workdir = kwargs.get("workdir")

        # 后端日志：诊断 skill_execute 调用
        logger.info(f"后端日志：[trade-customer诊断] skill_execute 被调用, skill={skill_name}, session_id={session_id}, user_id={user_id}, command={'有' if command else '无'}")
        if command:
            # 截断过长的命令，只记录关键信息
            cmd_preview = command[:500] if len(command) > 500 else command
            logger.info(f"后端日志：[trade-customer诊断] 原始命令内容: {cmd_preview}")

        if not skill_name:
            logger.warning(f"后端日志：[trade-customer诊断] skill_name 为空，直接返回错误")
            return {
                "success": False,
                "error": "No skill name provided"
            }

        if not command:
            # 对于引导式技能（无脚本），不执行命令，直接返回提示
            return {
                "success": True,
                "message": f"技能 '{skill_name}' 是引导式技能，无需执行命令。请按照技能指南中的步骤，使用 content_generate 等工具完成任务。",
                "skill_name": skill_name
            }

        skill = self.skill_registry.get(skill_name)
        if not skill:
            return {
                "success": False,
                "error": f"Skill '{skill_name}' not found",
                "available_skills": self.skill_registry.list_skills()
            }

        processed_command = command

        # 自动替换 {user_id} 和 {session_id} 占位符
        real_user_id = user_id
        real_session_id = session_id

        # 如果没有传入 user_id，从 session 中获取
        if not real_user_id and session_id:
            from src.db.models import SessionDB
            session_info = SessionDB.get_by_id(session_id)
            if session_info:
                real_user_id = session_info.get("user_id")
                real_session_id = session_id
                logger.info(f"后端日志：skill_execute 获取真实 user_id={real_user_id}")

        # 替换占位符
        if "{user_id}" in processed_command and real_user_id:
            processed_command = processed_command.replace("{user_id}", real_user_id)
            logger.info(f"后端日志：已替换 {{user_id}} 占位符")
        if "{session_id}" in processed_command and real_session_id:
            processed_command = processed_command.replace("{session_id}", real_session_id)
            logger.info(f"后端日志：已替换 {{session_id}} 占位符")

        # 如果命令中仍然包含 --user-id 且值看起来像 LLM 编造的（包含日期等），强制替换
        user_id_pattern = r'--user-id["\s]+["\']?([^"\'\s]+)["\']?'
        matches = re.findall(user_id_pattern, processed_command)
        for old_user_id in matches:
            if old_user_id != real_user_id and real_user_id:
                processed_command = re.sub(
                    rf'--user-id["\s]+["\']?{re.escape(old_user_id)}["\']?',
                    f'--user-id "{real_user_id}"',
                    processed_command
                )
                logger.info(f"后端日志：强制替换 LLM 编造的 user_id '{old_user_id}' -> '{real_user_id}'")

        decoded_files = {}
        if files:
            for filename, content_b64 in files.items():
                try:
                    decoded_files[filename] = base64.b64decode(content_b64)
                except Exception as e:
                    logger.warning(f"Failed to decode file {filename}: {e}")

        # 处理纯文本 content 参数：通过 stdin 直接传给子进程，不写中间文件
        stdin_content = None
        content_text = kwargs.get("content")
        if content_text:
            # 支持字符串或列表类型，列表时转为 JSON 字符串
            if isinstance(content_text, list):
                import json
                content_text = json.dumps(content_text, ensure_ascii=False)

            # 自动注入 tenant_id：从 session 获取真实 tenant_id 注入到 content JSON 中
            import json as _json
            try:
                content_obj = _json.loads(content_text)
                if isinstance(content_obj, dict):
                    from src.db.models import SessionDB
                    session_info = SessionDB.get_by_id(real_session_id) if real_session_id else None
                    real_tenant_id = None
                    if session_info:
                        real_tenant_id = session_info.get("tenant_id")
                    if real_tenant_id:
                        old_val = content_obj.get("tenant_id", "")
                        if old_val != real_tenant_id:
                            logger.info(f"注入 tenant_id: {old_val} -> {real_tenant_id}")
                            content_obj["tenant_id"] = real_tenant_id
                            content_text = _json.dumps(content_obj, ensure_ascii=False)
            except (_json.JSONDecodeError, TypeError):
                pass  # 非 JSON 内容，跳过

            stdin_content = str(content_text).encode("utf-8", errors="surrogatepass")

        try:
            # 后端日志：诊断实际提交给执行器的命令
            logger.info(f"后端日志：[trade-customer诊断] 提交给 skill_executor 执行, skill={skill_name}, real_session_id={real_session_id}, real_user_id={real_user_id}")
            cmd_preview = processed_command[:500] if len(processed_command) > 500 else processed_command
            logger.info(f"后端日志：[trade-customer诊断] 最终命令: {cmd_preview}")

            result = await self.skill_executor.execute_skill_command(
                skill_name=skill_name,
                command=processed_command,
                files=decoded_files if decoded_files else None,
                session_id=real_session_id,
                user_id=real_user_id,
                stdin_content=stdin_content,
            )

            # 后端日志：诊断执行结果
            logger.info(f"后端日志：[trade-customer诊断] 执行结果: success={result.success}, exit_code={result.exit_code}, duration={result.duration:.2f}s, timed_out={result.timed_out}")
            if result.stdout:
                stdout_preview = result.stdout[:300] if len(result.stdout) > 300 else result.stdout
                logger.info(f"后端日志：[trade-customer诊断] stdout: {stdout_preview}")
            if result.stderr:
                stderr_preview = result.stderr[:300] if len(result.stderr) > 300 else result.stderr
                logger.warning(f"后端日志：[trade-customer诊断] stderr: {stderr_preview}")
            if not result.success:
                logger.error(f"后端日志：[trade-customer诊断] 命令执行失败! error={result.error}, stderr={result.stderr[:500] if result.stderr else '无'}")

            # 构建 error 字段：优先使用 result.error，fallback 到 stderr
            exec_error = result.error or result.stderr or f"exit_code={result.exit_code}"

            return {
                "success": result.success,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "duration": result.duration,
                "timed_out": result.timed_out,
                "error": exec_error
            }

        except Exception as e:
            logger.error(f"Skill execute failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
