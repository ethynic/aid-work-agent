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
        *, env=None,
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

        process = None
        try:
            from src.core.skill_environment import base_environment
            import os
            from src.llm.call_observer import (prepare_child_environment,observe_child_exit,
                prepare_child_process,register_child_process)
            environment = dict(env) if env is not None else base_environment()
            prepare_child_environment(environment)
            prepare_child_process(environment)
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(skill_dir),
                env=environment,
                start_new_session=os.name == 'posix',
            )
            register_child_process(process.pid,environment)
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
            output = stdout.decode("utf-8", errors="replace").strip()
            from src.llm.call_observer import observe_child_exit
            observe_child_exit(process.returncode)

            if process.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                logger.warning(f"Skill hook returned non-zero exit code: {err}")
            else:
                logger.info(f"Skill hook executed successfully: {command[:80]}")

            return output if output else None

        except asyncio.TimeoutError:
            from src.core.subprocess_owner import stop_process_group_async
            await stop_process_group_async(process)
            observe_child_exit(None)
            logger.warning(f"Skill hook timed out after {timeout}s: {command[:80]}")
            return None
        except asyncio.CancelledError:
            from src.core.subprocess_owner import stop_process_group_async
            await stop_process_group_async(process)
            raise
        except Exception as e:
            if getattr(e,'authoritative_storage_failure',False):
                raise
            logger.error(f"Skill hook execution failed: {e}")
            return None
        finally:
            if 'environment' in locals():
                from src.llm.call_observer import release_child_process
                release_child_process(environment)

    @staticmethod
    async def run_on_load(hooks: dict, skill_dir: Path, *, env=None) -> Optional[str]:
        """执行 onLoad hook"""
        if not hooks or "onLoad" not in hooks:
            return None
        return await SkillHooks.run_hook(hooks["onLoad"], skill_dir, env=env)

    @staticmethod
    async def run_on_unload(hooks: dict, skill_dir: Path, *, env=None) -> Optional[str]:
        """执行 onUnload hook"""
        if not hooks or "onUnload" not in hooks:
            return None
        return await SkillHooks.run_hook(hooks["onUnload"], skill_dir, env=env)
