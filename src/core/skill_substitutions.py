#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Substitutions - AgentSkills 标准字符串替换系统

在 Layer 2 内容获取时，对 SKILL.md body 中的变量占位符进行替换。

支持的替换模式：
    $ARGUMENTS          — 完整参数文本
    $ARGUMENTS[N] / $N  — 第 N 个参数（空格分割）
    ${VAR_NAME}         — 上下文变量或环境变量
    ${CLAUDE_SESSION_ID} — 当前会话 ID
    ${CLAUDE_SKILL_DIR} — Skill 目录路径
    ${USER_ID}          — 当前用户 ID
"""

import os
import re
from typing import Dict, Optional


class SkillSubstitutor:
    """AgentSkills 标准字符串替换器"""

    # 匹配 $ARGUMENTS[N] 或 $N（N 为数字）
    _INDEXED_ARG_PATTERN = re.compile(r"\$ARGUMENTS\[(\d+)\]|\$(\d+)")
    # 匹配 $ARGUMENTS（非索引形式）
    _FULL_ARG_PATTERN = re.compile(r"\$ARGUMENTS(?!\[)")
    # 匹配 ${VAR_NAME}
    _VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")

    @staticmethod
    def substitute(body: str, context: Dict[str, str]) -> str:
        """
        对 body 执行字符串替换。

        Args:
            body: SKILL.md 正文
            context: 替换上下文，支持以下键：
                - "arguments": 完整参数文本
                - "session_id": 会话 ID
                - "skill_dir": Skill 目录路径
                - "user_id": 用户 ID
                其他键会作为额外变量使用。

        Returns:
            替换后的文本
        """
        if not body:
            return body

        arguments = context.get("arguments", "")
        skill_dir = context.get("skill_dir", "")

        # 1. 替换 <SKILL_ROOT>（Claude Code / Codex / OpenClaw 兼容）
        if skill_dir and "<SKILL_ROOT>" in body:
            body = body.replace("<SKILL_ROOT>", skill_dir)

        # 2. 替换 $ARGUMENTS[N] 和 $N
        def replace_indexed(match: re.Match) -> str:
            idx_str = match.group(1) or match.group(2)
            try:
                idx = int(idx_str)
                parts = arguments.split()
                return parts[idx] if idx < len(parts) else ""
            except (ValueError, IndexError):
                return ""

        body = SkillSubstitutor._INDEXED_ARG_PATTERN.sub(replace_indexed, body)

        # 3. 替换 $ARGUMENTS（完整参数）
        body = SkillSubstitutor._FULL_ARG_PATTERN.sub(arguments, body)

        # 4. 替换 ${VAR_NAME}
        def replace_var(match: re.Match) -> str:
            var_name = match.group(1)
            # 映射标准变量名到 context 键
            var_map = {
                "CLAUDE_SESSION_ID": "session_id",
                "CLAUDE_SKILL_DIR": "skill_dir",
                "USER_ID": "user_id",
            }
            context_key = var_map.get(var_name, var_name)
            # 先从 context 查找，再从租户级环境变量、进程环境变量查找
            value = context.get(context_key)
            if value is None:
                try:
                    from src.tools.context import current_tool_execution_context
                    _tool_ctx = current_tool_execution_context()
                except Exception:
                    _tool_ctx = None
                if _tool_ctx is not None:
                    value = _tool_ctx.env_vars.get(var_name)
            if value is None:
                value = os.environ.get(var_name)
            return str(value) if value is not None else match.group(0)

        body = SkillSubstitutor._VAR_PATTERN.sub(replace_var, body)

        return body
