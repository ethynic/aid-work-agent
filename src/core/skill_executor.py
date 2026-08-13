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

# 确保 .env 文件被加载
from dotenv import load_dotenv
load_dotenv()

from src.core.skill_loader import Skill, SkillDependency
from src.core.skill_registry import SkillRegistry
from src.config.settings import settings

SKILL_COMMAND_TIMEOUT_SECONDS = 600


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
    
    async def load_skill(self, skill_name: str, substitutions: Optional[Dict[str, Any]] = None) -> Optional[str]:
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

        content = self.skill_registry.get_content(skill_name, substitutions=substitutions)
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
                    timeout=30
                )
                
                if check_result.success:
                    results[dep.name] = True
                    continue
                
                # 安装Python包
                version_spec = f"=={dep.version}" if dep.version else ""
                install_result = await self._execute_command(
                    f"pip install {dep.name}{version_spec}",
                    context.workdir,
                    timeout=SKILL_COMMAND_TIMEOUT_SECONDS
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
                    timeout=30
                )
                
                if check_result.success:
                    results[dep.name] = True
                    continue
                
                # 安装系统包（需要sudo权限）
                install_result = await self._execute_command(
                    f"apt-get update && apt-get install -y {dep.name}",
                    context.workdir,
                    timeout=SKILL_COMMAND_TIMEOUT_SECONDS
                )
                results[dep.name] = install_result.success
            
            elif dep.type == "npm":
                # 检查npm包
                check_result = await self._execute_command(
                    f"npm list {dep.name}",
                    context.workdir,
                    timeout=30
                )
                
                if check_result.success:
                    results[dep.name] = True
                    continue
                
                # 安装npm包
                install_result = await self._execute_command(
                    f"npm install {dep.name}",
                    context.workdir,
                    timeout=SKILL_COMMAND_TIMEOUT_SECONDS
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
    ) -> ExecutionResult:
        """
        执行命令

        Args:
            command: 要执行的命令
            workdir: 工作目录
            timeout: 超时时间（秒）
            stdin_content: 通过 stdin 传递给子进程的内容（bytes）

        Returns:
            执行结果
        """
        start_time = time.time()

        try:
            # 获取当前进程的环境变量，确保子进程继承所有环境变量（包括 .env 加载的）
            env = os.environ.copy()
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

            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,  # 始终创建 PIPE，避免子进程 stdin 阻塞
                cwd=str(workdir),
                env=env,  # 显式传递环境变量
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=stdin_content or b""),
                    timeout=timeout
                )
                duration = time.time() - start_time

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
                process.kill()
                await process.wait()
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
        except Exception as e:
            duration = time.time() - start_time
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration=duration,
                error=str(e),
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
        
        # 创建执行上下文
        workdir = self._create_workdir(skill_name, session_id)
        context = SkillExecutionContext(
            skill_name=skill_name,
            workdir=workdir,
            session_id=session_id,
            user_id=user_id,
        )
        
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
        4. uploads/ 目录
        
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
            Path.cwd() / settings.storage.uploads_dir,  # 旧存储目录（向后兼容）: storage/uploads
            Path.cwd() / "storage" / "tenants",  # 新租户附件根目录: storage/tenants
            Path.cwd() / "uploads",  # 旧目录（向后兼容）
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
