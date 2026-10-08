#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Executor - Skill执行器

负责执行Skill中的命令和脚本，直接在当前运行时环境中执行。

主要功能:
1. 加载Skill内容并注入到对话中
2. 直接执行Skill命令
3. 处理Skill依赖
4. 管理Skill工作目录和文件

使用示例:
    executor = SkillExecutor(skill_registry)
    
    # 加载Skill内容
    content = await executor.load_skill("pdf")
    
    # 执行Skill命令
    result = await executor.execute_skill_command(
        "pdf",
        "pdftotext input.pdf -",
        files={"input.pdf": pdf_bytes}
    )
"""

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from loguru import logger

from src.core.skill_loader import Skill, SkillDependency
from src.core.skill_registry import SkillRegistry
from src.config.settings import settings

SKILL_COMMAND_TIMEOUT_SECONDS = 600

# M2 起插件执行拦截文案（含未声明 device 的插件拦截、内置 device 声明拦截等）
# 统一迁移到 src/local_tools/skill_runner_proxy.py（executor 与 use_skill 共用同一
# 决策函数 evaluate_device_execution 与文案映射，保证「提示可执行 ⇔ 实际可执行」
# 不漂移；延迟 import 避免本模块顶层依赖 src.local_tools 造成循环 import——
# skill_runner_proxy 顶层 import 本模块的 ExecutionResult）。


@dataclass
class ExecutionResult:
    """执行结果"""
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration: float
    timed_out: bool = False
    error: Optional[str] = None


class SkillExecutionContext:
    """Skill执行上下文"""
    
    def __init__(
        self,
        skill_name: str,
        workdir: Path,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ):
        self.skill_name = skill_name
        self.workdir = workdir
        self.session_id = session_id
        self.user_id = user_id
        self.files: Dict[str, bytes] = {}
        self.variables: Dict[str, Any] = {}
        self.environment: Optional[Dict[str, str]] = None
        self.start_time = time.time()
    
    def add_file(self, filename: str, content: bytes):
        """添加文件到上下文"""
        self.files[filename] = content
    
    def get_file_path(self, filename: str) -> Path:
        """获取文件路径"""
        return self.workdir / filename
    
    def save_files(self):
        """保存所有文件到工作目录"""
        for filename, content in self.files.items():
            filepath = self.workdir / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_bytes(content)
    
    def cleanup(self):
        """清理工作目录"""
        if self.workdir.exists():
            try:
                shutil.rmtree(self.workdir)
            except Exception as e:
                logger.warning(f"Failed to cleanup workdir {self.workdir}: {e}")


class SkillExecutor:
    """
    Skill执行器
    
    负责执行Skill中的命令和脚本，直接在当前运行时环境中执行。
    
    执行流程:
    1. 加载Skill定义
    2. 创建执行上下文
    3. 准备依赖环境
    4. 执行命令
    5. 收集结果并清理
    """
    
    def __init__(
        self,
        skill_registry: SkillRegistry,
        workspace: Optional[Path] = None,
    ):
        """
        初始化Skill执行器
        
        Args:
            skill_registry: Skill注册表
            workspace: 工作空间路径
        """
        self.skill_registry = skill_registry
        self.workspace = workspace or Path(tempfile.gettempdir()) / "skill_executor"
        self.workspace.mkdir(parents=True, exist_ok=True)
        
        # 活跃的执行上下文
        self._active_contexts: Dict[str, SkillExecutionContext] = {}
    
    def _create_workdir(self, skill_name: str, session_id: Optional[str] = None) -> Path:
        """
        创建工作目录
        
        Args:
            skill_name: Skill名称
            session_id: 会话ID
            
        Returns:
            工作目录路径
        """
        timestamp = int(time.time() * 1000)
        dirname = f"{skill_name}_{session_id or 'default'}_{timestamp}"
        workdir = self.workspace / dirname
        workdir.mkdir(parents=True, exist_ok=True)
        return workdir
    
    def environment(self, skill_name, *, context=None, tenant_id=None, env_vars=None, env_extra=None):
        if context is None:
            from src.tools.context import current_tool_execution_context
            context = current_tool_execution_context()
        overrides = {**dict(env_extra or {}), **dict(env_vars or {})}
        return self.skill_registry.environment(skill_name, tenant_id=tenant_id,
                                               env_vars=overrides, context=context)

    async def load_skill(self, skill_name: str, substitutions: Optional[Dict[str, Any]] = None, *, tenant_id=None, env_vars=None, context=None) -> Optional[str]:
        """
        加载Skill内容

        这是Layer 2 - 完整的SKILL.md正文，用于注入到对话中。

        Args:
            skill_name: Skill名称
            substitutions: 可选的替换上下文

        Returns:
            Skill内容字符串，如果未找到返回None
        """
        skill = self.skill_registry.get(skill_name)
        if not skill:
            available = ", ".join(self.skill_registry.list_skills()) or "none"
            return f"Error: Unknown skill '{skill_name}'. Available: {available}"

        if context is None:
            from src.tools.context import current_tool_execution_context
            context = current_tool_execution_context()
        content = await asyncio.to_thread(self.skill_registry.get_content, skill_name, substitutions=substitutions,
            tenant_id=tenant_id, env_vars=env_vars, context=context)
        if content:
            # 包装在标签中，让模型知道这是Skill内容
            return f"""<skill-loaded name="{skill_name}">
{content}
</skill-loaded>

Follow the instructions in the skill above to complete the user's task."""

        return None
    
    def get_skill_descriptions(self) -> str:
        """
        获取所有Skill描述

        Returns:
            Skill描述字符串
        """
        return self.skill_registry.get_descriptions()

    def match_skill_by_file(self, filename: str) -> Optional[str]:
        """
        根据文件名匹配Skill（基于 paths 字段）

        Args:
            filename: 文件名

        Returns:
            匹配的Skill名称
        """
        return self.skill_registry.match_by_file(filename)

    async def prepare_dependencies(
        self,
        skill: Skill,
        context: SkillExecutionContext,
    ) -> Dict[str, bool]:
        """
        准备Skill依赖
        
        Args:
            skill: Skill对象
            context: 执行上下文
            
        Returns:
            依赖准备结果
        """
        results = {}
        
        for dep in skill.dependencies:
            if dep.type == "pip":
                # 检查Python包
                check_result = await self._execute_command(
                    f"python -c 'import {dep.name}'",
                    context.workdir,
                    timeout=30, env=context.environment
                )
                
                if check_result.success:
                    results[dep.name] = True
                    continue
                
                # 安装Python包
                version_spec = f"=={dep.version}" if dep.version else ""
                install_result = await self._execute_command(
                    f"pip install {dep.name}{version_spec}",
                    context.workdir,
                    timeout=SKILL_COMMAND_TIMEOUT_SECONDS, env=context.environment
                )
                results[dep.name] = install_result.success
                
                if install_result.success:
                    logger.info(f"Installed dependency: {dep.name}")
                else:
                    logger.error(f"Failed to install dependency: {dep.name}: {install_result.stderr}")
            
            elif dep.type == "apt":
                # 检查系统包
                check_result = await self._execute_command(
                    f"dpkg -l {dep.name}",
                    context.workdir,
                    timeout=30, env=context.environment
                )
                
                if check_result.success:
                    results[dep.name] = True
                    continue
                
                # 安装系统包（需要sudo权限）
                install_result = await self._execute_command(
                    f"apt-get update && apt-get install -y {dep.name}",
                    context.workdir,
                    timeout=SKILL_COMMAND_TIMEOUT_SECONDS, env=context.environment
                )
                results[dep.name] = install_result.success
            
            elif dep.type == "npm":
                # 检查npm包
                check_result = await self._execute_command(
                    f"npm list {dep.name}",
                    context.workdir,
                    timeout=30, env=context.environment
                )
                
                if check_result.success:
                    results[dep.name] = True
                    continue
                
                # 安装npm包
                install_result = await self._execute_command(
                    f"npm install {dep.name}",
                    context.workdir,
                    timeout=SKILL_COMMAND_TIMEOUT_SECONDS, env=context.environment
                )
                results[dep.name] = install_result.success
        
        return results
    
    def _extract_commands_from_body(self, body: str) -> List[Dict[str, str]]:
        """
        从Skill正文中提取命令示例
        
        Args:
            body: Skill正文
            
        Returns:
            命令列表
        """
        commands = []
        
        # 匹配代码块中的命令
        pattern = r"```(?:bash|shell|sh)\s*\n(.*?)\n```"
        matches = re.findall(pattern, body, re.DOTALL)
        
        for cmd in matches:
            cmd = cmd.strip()
            if cmd and not cmd.startswith("#"):  # 排除注释
                commands.append({"type": "bash", "command": cmd})
        
        # 匹配Python代码块
        pattern = r"```(?:python|py)\s*\n(.*?)\n```"
        matches = re.findall(pattern, body, re.DOTALL)
        
        for code in matches:
            code = code.strip()
            if code:
                commands.append({"type": "python", "code": code})
        
        return commands
    
    async def _execute_command(
        self,
        command: str,
        workdir: Path,
        timeout: int = SKILL_COMMAND_TIMEOUT_SECONDS,
        stdin_content: Optional[bytes] = None,
        env_extra: Optional[Dict[str, str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """
        执行命令

        Args:
            command: 要执行的命令
            workdir: 工作目录
            timeout: 超时时间（秒）
            stdin_content: 通过 stdin 传递给子进程的内容（bytes）
            env_extra: 额外注入子进程的环境变量（如子智能体 LLM 覆盖）

        Returns:
            执行结果
        """
        start_time = time.time()
        process = None

        try:
            # 获取当前进程的环境变量，确保子进程继承所有环境变量（包括 .env 加载的）
            from src.core.skill_environment import base_environment, bind_identity
            resolved_env = env is not None
            env = dict(env) if resolved_env else base_environment()
            # 注入子智能体 LLM 覆盖（provider/model），供技能脚本 llm_client 读取
            if env_extra:
                env.update(env_extra)
            # 注入请求级上下文标识（租户/会话/用户），供技能子进程计量归属
            # （record_skill_llm_usage 读 AID_* 环境变量回填 tenant/session/user）。
            # 上下文缺失（后台调度等场景）不设置，子进程自行兜底。
            try:
                from src.tools.context import current_tool_execution_context
                _tool_ctx = None if resolved_env else current_tool_execution_context()
            except Exception:
                _tool_ctx = None
            if _tool_ctx is not None:
                # 租户级子智能体环境变量（subagent_env_vars）随上下文传给子进程，
                # 替代旧的进程级 os.environ 注入（并发消息竞态已废弃）
                if _tool_ctx.env_vars:
                    env.update(dict(_tool_ctx.env_vars))
                if _tool_ctx.tenant_id:
                    env['AID_TENANT_ID'] = _tool_ctx.tenant_id
                if _tool_ctx.session_id:
                    env['AID_SESSION_ID'] = _tool_ctx.session_id
                if _tool_ctx.user_id:
                    env['AID_USER_ID'] = _tool_ctx.user_id
                if _tool_ctx.subagent_id:
                    env['AID_SUBAGENT_ID'] = _tool_ctx.subagent_id
            if _tool_ctx is not None:
                bind_identity(env, _tool_ctx)
            from src.llm.call_observer import (prepare_child_environment, observe_child_exit,
                prepare_child_process, register_child_process, release_child_process)
            prepare_child_environment(env)
            # 强制子进程使用 UTF-8 编码，避免 Windows 上 GBK/cp936 导致中文乱码
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUTF8'] = '1'
            # 注入项目根目录与项目级临时目录，供 skill 脚本写临时文件
            # 避免依赖系统 /tmp（部分生产环境无写入权限）
            project_root = str(Path.cwd())
            env['PROJECT_ROOT'] = project_root
            env['SKILL_TMP_DIR'] = str(Path(project_root) / 'storage' / 'tmp')

            # 后端日志：诊断子进程执行
            is_trade_customer_cmd = "customer_manager" in command or "save-customer" in command or "save-customers" in command
            if is_trade_customer_cmd:
                logger.info(f"后端日志：[trade-customer诊断] _execute_command 准备执行子进程, workdir={workdir}")
                cmd_preview = command[:500] if len(command) > 500 else command
                logger.info(f"后端日志：[trade-customer诊断] 子进程命令: {cmd_preview}")

            prepare_child_process(env)
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,  # 始终创建 PIPE，避免子进程 stdin 阻塞
                cwd=str(workdir),
                env=env,  # 显式传递环境变量
                start_new_session=os.name == 'posix',
            )
            register_child_process(process.pid,env)

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=stdin_content or b""),
                    timeout=timeout
                )
                duration = time.time() - start_time
                observe_child_exit(process.returncode)

                # 后端日志：诊断 trade-customer 子进程结果
                if is_trade_customer_cmd:
                    logger.info(f"后端日志：[trade-customer诊断] 子进程执行完成, returncode={process.returncode}, duration={duration:.2f}s")
                    if stdout:
                        stdout_preview = stdout.decode('utf-8', errors='replace')[:300]
                        logger.info(f"后端日志：[trade-customer诊断] 子进程 stdout: {stdout_preview}")
                    if stderr:
                        stderr_preview = stderr.decode('utf-8', errors='replace')[:300]
                        logger.info(f"后端日志：[trade-customer诊断] 子进程 stderr: {stderr_preview}")
                    if process.returncode != 0:
                        logger.error(f"后端日志：[trade-customer诊断] 子进程退出码非零! returncode={process.returncode}")

                return ExecutionResult(
                    success=process.returncode == 0,
                    stdout=stdout.decode('utf-8', errors='replace'),
                    stderr=stderr.decode('utf-8', errors='replace'),
                    exit_code=process.returncode or 0,
                    duration=duration,
                    timed_out=False,
                )
            except asyncio.TimeoutError:
                from src.core.subprocess_owner import stop_process_group_async
                await stop_process_group_async(process)
                observe_child_exit(None)
                duration = time.time() - start_time
                
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=f"Command timed out after {timeout} seconds",
                    exit_code=-1,
                    duration=duration,
                    timed_out=True,
                    error="Timeout",
                )
        except asyncio.CancelledError:
            from src.core.subprocess_owner import stop_process_group_async
            await stop_process_group_async(process)
            raise
        except Exception as e:
            if getattr(e,'authoritative_storage_failure',False):
                raise
            duration = time.time() - start_time
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration=duration,
                error=str(e),
            )
        finally:
            if 'env' in locals():
                from src.llm.call_observer import release_child_process
                release_child_process(env)

    def _evaluate_device_execution(self, skill_name: str):
        """M2 设备执行判定包装（plan §3.5）：evaluate_device_execution 的 a–d 全满足
        才放行设备路由；use_skill（手册尾注）与 execute_skill_script 同源判定。

        延迟 import：src.local_tools.skill_runner_proxy 顶层依赖本模块 ExecutionResult，
        顶层反向 import 会造成循环（包初始化副作用规范）。
        """
        from src.local_tools import skill_runner_proxy
        return skill_runner_proxy.evaluate_device_execution(self.skill_registry, skill_name)

    async def _route_device_execution(
        self,
        decision,
        *,
        command: str,
        files: Optional[Dict[str, bytes]],
        session_id: Optional[str],
        user_id: Optional[str],
        stdin_content: Optional[bytes],
        context=None,
        tenant_id=None,
    ) -> "ExecutionResult":
        """device_ready 路由分支（plan §4.2 伪代码：files fail-closed → 命令门禁 → dispatch）。

        判定顺序与拦截点：
        - files 非空 → INVALID_DEVICE_INPUT（§3.1——${filename} 占位替换仅在容器工作目录
          语义下生效，透传到设备端只会把字面量塞进 argv，拒绝而非静默丢弃）；
        - 命令解析失败 → INVALID_DEVICE_COMMAND（附合法 entries 清单促 LLM 自纠）；
        - 其余 → dispatch（payload 为结构化 entry/args，原始命令字符串与 stdin_content
          恒不透传——stdin_content 是服务端注入的身份 JSON，设备侧脚本无消费方，
          设备 spawn stdin 'ignore'，读 stdin 的脚本立即 EOF 快速失败）。
        """
        from src.local_tools import skill_runner_proxy

        # context 合并（与 server 现状路径同语义：identity mismatch 检查 + 三元组解析）
        if context is not None:
            if (session_id is not None and session_id != context.session_id
                    or user_id is not None and user_id != context.user_id
                    or tenant_id is not None and tenant_id != context.tenant_id):
                raise ValueError("SKILL_EXECUTION_IDENTITY_MISMATCH")
            session_id, user_id, tenant_id = context.session_id, context.user_id, context.tenant_id

        if files:
            logger.info(f"后端日志：skill_execute 设备路由拒绝 files 文件参数（INVALID_DEVICE_INPUT）", extra={
                "skill_name": decision.skill_name,
            })
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=skill_runner_proxy.invalid_device_input_message("files", decision.skill_name),
                exit_code=-1,
                duration=0,
                error=skill_runner_proxy.INVALID_DEVICE_INPUT,
            )
        parsed = skill_runner_proxy.parse_device_skill_command(
            command, decision.entries, skill_runner_proxy.DeviceCommandLimits.from_settings())
        if not parsed.ok:
            logger.info(f"后端日志：skill_execute 设备路由命令门禁拦截（INVALID_DEVICE_COMMAND）", extra={
                "skill_name": decision.skill_name, "parse_error": parsed.error,
                "allowed_entries": list(decision.entries),
            })
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=skill_runner_proxy.invalid_device_command_message(decision.skill_name, parsed),
                exit_code=-1,
                duration=0,
                error=skill_runner_proxy.INVALID_DEVICE_COMMAND,
            )
        return await skill_runner_proxy.dispatch_device_skill_script(
            skill=decision.skill_name, entry=parsed.entry, args=parsed.args,
            exec_hash=decision.exec_hash, version=decision.version,
            tenant_id=tenant_id, user_id=user_id, session_id=session_id, context=context)

    def _check_execution_gate(self, skill_name: str, decision=None) -> Optional["ExecutionResult"]:
        """执行边界拦截（M1 §3.1 / M2 §3.5——内部调用 evaluate_device_execution + 区分码文案）。

        覆盖两个调用方：
        - execute_skill_command（第一入口）：路由分支先行 evaluate 并传入 decision——
          该调用方对 device_ready 已在路由分支处理（本方法收到 device_ready 时只可能是
          显式传入的非放行场景或第二入口，见下），其余状态经本文案拦截；
        - execute_skill_script（第二入口，无调用方，防绕过）：仍走全拦——device_ready
          同样拦截（第二入口不路由设备，M1 全拦语义维持），文案为「不支持该入口」。

        拦截先于 _process_command 路径替换与子进程创建（沿 M1 顺序保证——插件手册中
        scripts/<name> 相对路径不会被替换为插件脚本绝对路径）。文案与 use_skill 尾注
        共用 skill_runner_proxy 的映射（M1 既有断言 substring 约束见该模块注释）。

        Returns:
            None 表示放行（server 现状路径不动）；ExecutionResult 表示拦截
            （success=False，fail-closed，error 为稳定区分码）。
        """
        from src.local_tools import skill_runner_proxy
        if decision is None:
            decision = skill_runner_proxy.evaluate_device_execution(self.skill_registry, skill_name)
        if decision.status == skill_runner_proxy.ROUTE_SERVER:
            return None
        logger.info(f"后端日志：skill_execute 执行边界拦截", extra={
            "skill_name": skill_name,
            "route": decision.status,
            "reason_code": decision.reason_code,
            "reason": decision.reason,
        })
        return ExecutionResult(
            success=False,
            stdout="",
            stderr=skill_runner_proxy.decision_error_message(decision),
            exit_code=-1,
            duration=0,
            error=decision.reason_code,
        )

    async def execute_skill_command(
        self,
        skill_name: str,
        command: str,
        files: Optional[Dict[str, bytes]] = None,
        variables: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        stdin_content: Optional[bytes] = None,
        env_extra: Optional[Dict[str, str]] = None,
        *, context=None, tenant_id=None, env_vars=None,
    ) -> ExecutionResult:
        """
        执行Skill命令

        Args:
            skill_name: Skill名称
            command: 要执行的命令
            files: 文件字典 {filename: content}
            variables: 变量字典
            session_id: 会话ID
            user_id: 用户ID
            stdin_content: 通过 stdin 传递给子进程的内容
            env_extra: 额外注入子进程的环境变量（如子智能体 LLM 覆盖）

        Returns:
            执行结果
        """
        # 获取Skill
        skill = self.skill_registry.get(skill_name)
        if not skill:
            available = ", ".join(self.skill_registry.list_skills()) or "none"
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Unknown skill '{skill_name}'. Available: {available}",
                exit_code=-1,
                duration=0,
                error="Unknown skill",
            )

        # M2 设备执行路由（plan §4.2）：判定与路由先于环境解析、_process_command 路径替换
        # 与子进程创建——插件手册中 scripts/<name> 相对路径不会被替换为插件脚本绝对路径
        # （沿 M1 顺序保证）。device_ready（已审批插件 + execution=device + 审批含
        # entries/exec_hash + 设备执行开关开）→ 设备链路 dispatch（stdin_content 恒不透传）；
        # 其余按区分码拦截（文案见 skill_runner_proxy）或 server 现状路径不动。
        decision = self._evaluate_device_execution(skill_name)
        if decision.executable:
            return await self._route_device_execution(
                decision, command=command, files=files,
                session_id=session_id, user_id=user_id, stdin_content=stdin_content,
                context=context, tenant_id=tenant_id)
        gate_result = self._check_execution_gate(skill_name, decision=decision)
        if gate_result is not None:
            return gate_result

        if context is not None:
            if (session_id is not None and session_id != context.session_id
                    or user_id is not None and user_id != context.user_id
                    or tenant_id is not None and tenant_id != context.tenant_id):
                raise ValueError("SKILL_EXECUTION_IDENTITY_MISMATCH")
            session_id, user_id, tenant_id = context.session_id, context.user_id, context.tenant_id
        environment = await asyncio.to_thread(self.environment, skill_name, context=context,
            tenant_id=tenant_id, env_vars=env_vars, env_extra=env_extra)

        # 创建执行上下文
        workdir = self._create_workdir(skill_name, session_id)
        context = SkillExecutionContext(
            skill_name=skill_name,
            workdir=workdir,
            session_id=session_id,
            user_id=user_id,
        )
        
        context.environment = environment

        # 添加文件
        if files:
            for filename, content in files.items():
                context.add_file(filename, content)
        
        # 添加变量
        if variables:
            context.variables.update(variables)

        # 添加 user_id 到变量（供命令替换使用）
        if context.user_id:
            context.variables["user_id"] = context.user_id
        if context.session_id:
            context.variables["session_id"] = context.session_id

        # 保存文件到工作目录
        context.save_files()
        
        # 注册活跃上下文
        context_id = f"{skill_name}_{session_id or 'default'}"
        self._active_contexts[context_id] = context
        
        try:
            # 准备依赖
            if skill.dependencies:
                dep_results = await self.prepare_dependencies(skill, context)
                failed_deps = [k for k, v in dep_results.items() if not v]
                if failed_deps:
                    logger.warning(f"Failed to install dependencies: {failed_deps}")
            
            # 替换命令中的变量
            processed_command = self._process_command(command, context)
            
            # 执行命令 - 直接执行，不使用沙盒
            result = await self._execute_command(
                processed_command,
                context.workdir,
                timeout=SKILL_COMMAND_TIMEOUT_SECONDS,
                stdin_content=stdin_content,
                env=environment,
            )

            return result
            
        finally:
            # 清理上下文
            if context_id in self._active_contexts:
                del self._active_contexts[context_id]
            context.cleanup()
    
    async def execute_skill_script(
        self,
        skill_name: str,
        script_name: str,
        args: Optional[List[str]] = None,
        files: Optional[Dict[str, bytes]] = None,
        session_id: Optional[str] = None,
    ) -> ExecutionResult:
        """
        执行Skill中的脚本
        
        Args:
            skill_name: Skill名称
            script_name: 脚本名称
            args: 脚本参数
            files: 文件字典
            session_id: 会话ID
            
        Returns:
            执行结果
        """
        # 获取Skill
        skill = self.skill_registry.get(skill_name)
        if not skill:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Unknown skill: {skill_name}",
                exit_code=-1,
                duration=0,
                error="Unknown skill",
            )

        # M2 执行边界（沿 M1 §3.1）：第二执行入口走同一判定（当前无调用方，防后续接线
        # 绕过）——device_ready 同样拦截（第二入口不路由设备，全拦语义维持）
        gate_result = self._check_execution_gate(skill_name)
        if gate_result is not None:
            return gate_result

        # 查找脚本
        script_path = None
        for script in skill.scripts:
            if script.name == script_name:
                script_path = script
                break
        
        if not script_path:
            available = [s.name for s in skill.scripts]
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Script not found: {script_name}. Available: {available}",
                exit_code=-1,
                duration=0,
                error="Script not found",
            )
        
        # 创建执行上下文
        workdir = self._create_workdir(skill_name, session_id)
        context = SkillExecutionContext(
            skill_name=skill_name,
            workdir=workdir,
            session_id=session_id,
        )
        
        context.environment = await asyncio.to_thread(self.environment, skill_name)

        # 添加文件
        if files:
            for filename, content in files.items():
                context.add_file(filename, content)
        
        context.save_files()
        
        try:
            # 准备依赖
            if skill.dependencies:
                await self.prepare_dependencies(skill, context)
            
            # 执行脚本 - 直接执行，不使用沙盒
            result = await self._execute_command(
                f"python {script_path}",
                context.workdir,
                env=context.environment,
                timeout=SKILL_COMMAND_TIMEOUT_SECONDS
            )
            
            return result
            
        finally:
            context.cleanup()
    
    async def execute_skill_python(
        self,
        skill_name: str,
        code: str,
        files: Optional[Dict[str, bytes]] = None,
        globals_dict: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
    ) -> ExecutionResult:
        """
        执行Skill Python代码
        
        Args:
            skill_name: Skill名称
            code: Python代码
            files: 文件字典
            globals_dict: 全局变量字典
            session_id: 会话ID
            
        Returns:
            执行结果
        """
        # 获取Skill
        skill = self.skill_registry.get(skill_name)
        if not skill:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Unknown skill: {skill_name}",
                exit_code=-1,
                duration=0,
                error="Unknown skill",
            )
        
        # 创建执行上下文
        workdir = self._create_workdir(skill_name, session_id)
        context = SkillExecutionContext(
            skill_name=skill_name,
            workdir=workdir,
            session_id=session_id,
        )
        
        # 添加文件
        if files:
            for filename, content in files.items():
                context.add_file(filename, content)
        
        context.save_files()
        
        try:
            # 准备依赖
            if skill.dependencies:
                await self.prepare_dependencies(skill, context)
            
            # 执行Python代码 - 直接执行
            import io
            import sys
            
            # 重定向stdout和stderr
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            sys.stdout = io.StringIO()
            sys.stderr = io.StringIO()
            
            start_time = time.time()
            
            try:
                # 准备执行环境
                exec_globals = globals_dict or {}
                exec_globals.update({
                    "__builtins__": __builtins__,
                    "workdir": context.workdir,
                })
                
                exec(code, exec_globals)
                
                stdout = sys.stdout.getvalue()
                stderr = sys.stderr.getvalue()
                duration = time.time() - start_time
                
                return ExecutionResult(
                    success=True,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=0,
                    duration=duration,
                )
            except Exception as e:
                duration = time.time() - start_time
                return ExecutionResult(
                    success=False,
                    stdout=sys.stdout.getvalue(),
                    stderr=f"{sys.stderr.getvalue()}\n{str(e)}",
                    exit_code=1,
                    duration=duration,
                    error=str(e),
                )
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
        finally:
            context.cleanup()
    
    def _resolve_file_path(self, file_path: str) -> Optional[str]:
        """
        解析文件路径，自动查找文件的实际位置
        
        支持以下路径格式：
        1. 绝对路径 - 直接返回
        2. 相对路径 - 在多个目录中查找文件
        
        查找顺序：
        1. 原路径
        2. test_uploads/ 目录
        3. 项目根目录
        4. storage/tenants/ 租户附件根目录
        
        Args:
            file_path: 文件路径（可能是相对或绝对路径）
            
        Returns:
            解析后的绝对路径（使用正斜杠），如果找不到返回None
        """
        path = Path(file_path)
        
        # 如果是绝对路径且存在，直接返回（使用正斜杠）
        if path.is_absolute() and path.exists():
            return path.as_posix()
        
        # 如果是相对路径，在多个目录中查找
        search_dirs = [
            Path.cwd(),  # 当前工作目录
            Path.cwd() / "test_uploads",  # test_uploads 目录
            Path.cwd() / "storage" / "tenants",  # 新租户附件根目录: storage/tenants
            self.workspace,  # 执行器工作空间
        ]
        
        # 首先尝试原路径
        if path.exists():
            return path.absolute().as_posix()
        
        # 在各个目录中查找
        for search_dir in search_dirs:
            candidate = search_dir / path
            if candidate.exists():
                logger.info(f"Resolved file path: {file_path} -> {candidate}")
                return candidate.absolute().as_posix()
        
        # 如果还是找不到，尝试在 test_uploads 中按文件名查找
        if not path.is_absolute():
            filename = path.name
            for search_dir in search_dirs:
                if search_dir.name == "test_uploads":
                    continue
                test_uploads_dir = search_dir / "test_uploads"
                if test_uploads_dir.exists():
                    candidate = test_uploads_dir / filename
                    if candidate.exists():
                        logger.info(f"Resolved file path: {file_path} -> {candidate}")
                        return candidate.absolute().as_posix()
        
        logger.warning(f"Could not resolve file path: {file_path}")
        return None
    
    def _process_command(self, command: str, context: SkillExecutionContext) -> str:
        """
        处理命令中的变量替换、脚本路径和文件路径
        
        Args:
            command: 原始命令
            context: 执行上下文
            
        Returns:
            处理后的命令
        """
        # 获取Skill信息
        skill = self.skill_registry.get(context.skill_name)
        
        # 替换脚本路径（使用正斜杠避免Windows转义问题）
        if skill:
            for script_path in skill.scripts:
                script_name = script_path.name
                # 使用正斜杠路径（Python在Windows上支持正斜杠）
                script_abs_path = script_path.absolute().as_posix()
                
                # 替换 scripts/script_name 格式
                if f"scripts/{script_name}" in command:
                    command = command.replace(
                        f"scripts/{script_name}",
                        script_abs_path
                    )
                # 替换 ./scripts/script_name 格式
                if f"./scripts/{script_name}" in command:
                    command = command.replace(
                        f"./scripts/{script_name}",
                        script_abs_path
                    )
        
        # 替换文件变量
        for filename in context.files.keys():
            placeholder = f"${{{filename}}}"
            filepath = context.workdir / filename
            command = command.replace(placeholder, filepath.as_posix())
            command = command.replace(f"${filename}", filepath.as_posix())
        
        # 替换自定义变量
        for key, value in context.variables.items():
            placeholder = f"${{{key}}}"
            command = command.replace(placeholder, str(value))
        
        # 解析命令中的文件路径（针对 --file-path 等参数）
        command = self._resolve_file_paths_in_command(command)
        
        return command
    
    def _resolve_file_paths_in_command(self, command: str) -> str:
        """
        解析命令中的文件路径参数
        
        识别常见的文件路径参数格式并自动解析：
        - --file-path "xxx"
        - --file-path=xxx
        - --input "xxx"
        - -f "xxx"
        
        Args:
            command: 原始命令
            
        Returns:
            解析后的命令
        """
        import re
        
        # 常见的文件路径参数
        file_params = [
            '--file-path',
            '--input',
            '--file',
            '--source',
            '--document',
        ]
        
        # 处理 --param=value 格式
        for param in file_params:
            pattern = rf'({param})=([^\s]+)'
            def replace_equals(match):
                param_name = match.group(1)
                value = match.group(2)
                resolved_path = self._resolve_file_path(value)
                if resolved_path:
                    return f"{param_name}={resolved_path}"
                return match.group(0)
            
            command = re.sub(pattern, replace_equals, command)
        
        # 处理 --param "value" 或 --param value 格式
        for param in file_params:
            # 匹配 --param "value" 或 --param value
            pattern = rf'({param})\s+["\']?([^\s"\']+)["\']?'
            
            def replace_param(match):
                param_name = match.group(1)
                value = match.group(2)
                resolved_path = self._resolve_file_path(value)
                if resolved_path:
                    return f'{param_name} "{resolved_path}"'
                return match.group(0)
            
            command = re.sub(pattern, replace_param, command)
        
        return command
    
    async def process_uploaded_file(
        self,
        filename: str,
        content: bytes,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        处理上传的文件

        基于 skill 的 paths 字段自动匹配并返回处理建议。

        Args:
            filename: 文件名
            content: 文件内容
            session_id: 会话ID

        Returns:
            处理建议字典
        """
        # 基于 paths 字段匹配 Skill
        skill_name = self.match_skill_by_file(filename)

        if not skill_name:
            return {
                "success": False,
                "message": f"No skill found for file: {filename}",
                "skill": None,
            }

        skill = self.skill_registry.get(skill_name)

        return {
            "success": True,
            "message": f"Matched skill: {skill_name}",
            "skill": skill_name,
            "skill_description": skill.description if skill else None,
            "suggested_actions": self._suggest_actions(skill, filename),
        }
    
    def _suggest_actions(self, skill: Optional[Skill], filename: str) -> List[str]:
        """
        根据Skill建议操作
        
        Args:
            skill: Skill对象
            filename: 文件名
            
        Returns:
            建议操作列表
        """
        if not skill:
            return []
        
        actions = []
        
        # 从Skill正文中提取命令示例
        commands = self._extract_commands_from_body(skill.body)
        
        for cmd in commands[:3]:  # 最多返回3个建议
            if cmd["type"] == "bash":
                # 替换文件名占位符
                action = cmd["command"]
                action = action.replace("input.pdf", filename)
                action = action.replace("INPUT", filename)
                actions.append(action)
        
        return actions
    
    def cleanup(self):
        """清理所有工作目录"""
        # 清理活跃上下文
        for context in self._active_contexts.values():
            context.cleanup()
        self._active_contexts.clear()
        
        # 清理工作空间
        if self.workspace.exists():
            try:
                shutil.rmtree(self.workspace)
                self.workspace.mkdir(parents=True, exist_ok=True)
                logger.info("Skill executor workspace cleaned")
            except Exception as e:
                logger.error(f"Failed to cleanup workspace: {e}")


# 创建全局Skill执行器工厂函数
def create_skill_executor(
    skills_dir: Optional[Path] = None,
    workspace: Optional[Path] = None,
) -> SkillExecutor:
    """
    创建Skill执行器
    
    Args:
        skills_dir: Skill目录
        workspace: 工作空间
        
    Returns:
        Skill执行器实例
    """
    from src.core.skill_registry import skill_registry
    
    if skills_dir and not skill_registry.list_skills():
        skill_registry.load_from_directory(skills_dir)
    
    return SkillExecutor(skill_registry, workspace=workspace)
