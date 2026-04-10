#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Hooks - AgentSkills 标准 Hooks 系统

执行 skill 的生命周期 hooks（onLoad / onUnload）。
Hooks 是 SKILL.md frontmatter 中定义的 shell 命令，在 skill 加载/卸载时执行。
"""

import asyncio
from pathlib import Path
from typing import Optional

from loguru import logger


class SkillHooks:
    """Skill 生命周期 hooks 执行器"""

    @staticmethod
    async def run_hook(
        command: str,
        skill_dir: Path,
        timeout: int = 10,
    ) -> Optional[str]:
        """
        执行一个 hook 命令。

        Args:
            command: 要执行的 shell 命令
            skill_dir: Skill 目录路径（作为 cwd）
            timeout: 超时时间（秒），默认 10 秒

        Returns:
            hook 命令的 stdout 输出，失败返回 None
        """
        if not command:
            return None

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(skill_dir),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
            output = stdout.decode("utf-8", errors="replace").strip()

            if process.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                logger.warning(f"Skill hook returned non-zero exit code: {err}")
            else:
                logger.info(f"Skill hook executed successfully: {command[:80]}")

            return output if output else None

        except asyncio.TimeoutError:
            logger.warning(f"Skill hook timed out after {timeout}s: {command[:80]}")
            return None
        except Exception as e:
            logger.error(f"Skill hook execution failed: {e}")
            return None

    @staticmethod
    async def run_on_load(hooks: dict, skill_dir: Path) -> Optional[str]:
        """执行 onLoad hook"""
        if not hooks or "onLoad" not in hooks:
            return None
        return await SkillHooks.run_hook(hooks["onLoad"], skill_dir)

    @staticmethod
    async def run_on_unload(hooks: dict, skill_dir: Path) -> Optional[str]:
        """执行 onUnload hook"""
        if not hooks or "onUnload" not in hooks:
            return None
        return await SkillHooks.run_hook(hooks["onUnload"], skill_dir)
