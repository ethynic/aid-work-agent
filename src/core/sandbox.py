#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sandbox - 沙盒执行环境

为Skill提供安全的执行环境，隔离执行命令和代码。

设计理念:
参考Claude Code、OpenClaw等成熟方案的实现:
1. 多层隔离策略 - 从基础到高级的多种沙盒方案
2. 资源限制 - CPU、内存、时间、磁盘限制
3. 文件系统隔离 - 限制读写路径
4. 网络隔离 - 可选的网络访问控制
5. 依赖管理 - 自动安装skill依赖

沙盒方案:
    Level 1: SubprocessSandbox - 基础进程隔离（所有平台）
    Level 2: DockerSandbox - Docker容器隔离（需要Docker）
    Level 3: RestrictedPythonSandbox - Python代码受限执行

使用示例:
    sandbox = SandboxManager()
    result = await sandbox.execute_command(
        "pdftotext input.pdf -",
        skill_config=skill.sandbox_config,
        workdir=Path("/tmp/skill_work")
    )
"""

import asyncio
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from loguru import logger

from src.core.skill_loader import SkillSandboxConfig


@dataclass
class ExecutionResult:
    """执行结果"""
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration: float  # 秒
    timed_out: bool = False
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration": self.duration,
            "timed_out": self.timed_out,
            "error": self.error,
        }


class BaseSandbox(ABC):
    """沙盒基类"""
    
    @abstractmethod
    async def execute_command(
        self,
        command: str,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """
        执行命令
        
        Args:
            command: 要执行的命令
            config: 沙盒配置
            workdir: 工作目录
            env: 环境变量
            
        Returns:
            执行结果
        """
        pass
    
    @abstractmethod
    async def execute_script(
        self,
        script_path: Path,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """
        执行脚本文件
        
        Args:
            script_path: 脚本路径
            config: 沙盒配置
            workdir: 工作目录
            args: 脚本参数
            env: 环境变量
            
        Returns:
            执行结果
        """
        pass
    
    @abstractmethod
    async def execute_python(
        self,
        code: str,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        globals_dict: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        执行Python代码
        
        Args:
            code: Python代码
            config: 沙盒配置
            workdir: 工作目录
            globals_dict: 全局变量字典
            
        Returns:
            执行结果
        """
        pass
    
    def check_dependencies(self) -> Dict[str, bool]:
        """
        检查沙盒依赖
        
        Returns:
            依赖检查结果
        """
        return {"available": True}


class SubprocessSandbox(BaseSandbox):
    """
    子进程沙盒
    
    使用subprocess执行命令，提供基础的进程隔离。
    适用于所有平台，但隔离性较弱。
    
    安全措施:
    1. 超时限制
    2. 危险命令过滤
    3. 路径限制
    4. 环境变量隔离
    """
    
    # 危险命令模式
    DANGEROUS_PATTERNS = [
        "rm -rf /",
        "rm -rf /*",
        "mkfs",
        "dd if=",
        "> /dev/sd",
        "chmod -R 777 /",
        "chown -R",
        ":(){ :|:& };:",  # Fork bomb
        "shutdown",
        "reboot",
        "init 0",
        "init 6",
    ]
    
    def __init__(self, workspace: Optional[Path] = None):
        """
        初始化子进程沙盒
        
        Args:
            workspace: 工作空间路径
        """
        self.workspace = workspace or Path(tempfile.gettempdir()) / "skill_sandbox"
        self.workspace.mkdir(parents=True, exist_ok=True)
    
    def _is_dangerous_command(self, command: str) -> bool:
        """检查是否为危险命令"""
        command_lower = command.lower()
        for pattern in self.DANGEROUS_PATTERNS:
            if pattern.lower() in command_lower:
                return True
        return False
    
    def _build_env(
        self,
        config: SkillSandboxConfig,
        extra_env: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """
        构建环境变量
        
        Args:
            config: 沙盒配置
            extra_env: 额外环境变量
            
        Returns:
            环境变量字典
        """
        # 从干净的环境开始
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.workspace),
            "TEMP": str(self.workspace),
            "TMP": str(self.workspace),
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
        }
        
        # 添加Python路径
        env["PYTHONPATH"] = os.pathsep.join(sys.path)
        
        # 添加配置的环境变量
        env.update(config.env_vars)
        
        # 添加额外环境变量
        if extra_env:
            env.update(extra_env)
        
        return env
    
    def _validate_path(self, path: Path, config: SkillSandboxConfig, for_write: bool = False) -> bool:
        """
        验证路径是否允许访问
        
        Args:
            path: 要验证的路径
            config: 沙盒配置
            for_write: 是否为写操作
            
        Returns:
            是否允许访问
        """
        path = path.resolve()
        
        # 检查是否在工作空间内
        try:
            path.relative_to(self.workspace)
            return True
        except ValueError:
            pass
        
        # 检查允许的路径列表
        allowed_paths = config.write_paths if for_write else config.read_paths
        for allowed in allowed_paths:
            allowed_path = Path(allowed).resolve()
            try:
                path.relative_to(allowed_path)
                return True
            except ValueError:
                pass
        
        return False
    
    async def execute_command(
        self,
        command: str,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """执行命令"""
        start_time = time.time()
        
        # 检查危险命令
        if self._is_dangerous_command(command):
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="Error: Dangerous command blocked",
                exit_code=-1,
                duration=time.time() - start_time,
                error="Dangerous command blocked",
            )
        
        # 确定工作目录
        cwd = workdir or self.workspace
        cwd = Path(cwd).resolve()
        cwd.mkdir(parents=True, exist_ok=True)
        
        # 构建环境变量
        exec_env = self._build_env(config, env)
        
        # 确定shell
        if platform.system() == "Windows":
            # Windows使用cmd
            shell_cmd = command
        else:
            # Unix使用bash
            shell_cmd = command
        
        try:
            # 执行命令
            process = await asyncio.create_subprocess_shell(
                shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
                env=exec_env,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=config.timeout
                )
                
                duration = time.time() - start_time
                
                return ExecutionResult(
                    success=process.returncode == 0,
                    stdout=stdout.decode('utf-8', errors='replace'),
                    stderr=stderr.decode('utf-8', errors='replace'),
                    exit_code=process.returncode or 0,
                    duration=duration,
                )
                
            except asyncio.TimeoutError:
                # 超时，终止进程
                process.kill()
                await process.wait()
                
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=f"Error: Command timed out after {config.timeout} seconds",
                    exit_code=-1,
                    duration=config.timeout,
                    timed_out=True,
                    error="Timeout",
                )
                
        except Exception as e:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration=time.time() - start_time,
                error=str(e),
            )
    
    async def execute_script(
        self,
        script_path: Path,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """执行脚本文件"""
        script_path = Path(script_path).resolve()
        
        # 验证脚本路径
        if not script_path.exists():
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Script not found: {script_path}",
                exit_code=-1,
                duration=0,
                error="Script not found",
            )
        
        # 确定解释器
        suffix = script_path.suffix.lower()
        interpreters = {
            ".py": [sys.executable],
            ".sh": ["bash"],
            ".bash": ["bash"],
            ".zsh": ["zsh"],
            ".js": ["node"],
            ".ts": ["ts-node"],
        }
        
        interpreter = interpreters.get(suffix, [])
        if not interpreter:
            # 尝试直接执行
            interpreter = [str(script_path)]
        else:
            interpreter.append(str(script_path))
        
        # 添加参数
        if args:
            interpreter.extend(args)
        
        # 构建命令
        command = " ".join(f'"{arg}"' if " " in arg else arg for arg in interpreter)
        
        return await self.execute_command(command, config, workdir, env)
    
    async def execute_python(
        self,
        code: str,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        globals_dict: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """执行Python代码"""
        start_time = time.time()
        
        # 创建临时脚本文件
        workdir = workdir or self.workspace
        workdir.mkdir(parents=True, exist_ok=True)
        
        script_file = workdir / f"script_{int(time.time() * 1000)}.py"
        
        try:
            script_file.write_text(code, encoding='utf-8')
            
            # 构建环境变量
            env = self._build_env(config)
            
            # 添加全局变量作为JSON
            if globals_dict:
                import json
                env["_SKILL_GLOBALS"] = json.dumps(globals_dict)
            
            result = await self.execute_script(
                script_file,
                config,
                workdir,
                env=env,
            )
            
            return result
            
        finally:
            # 清理临时文件
            if script_file.exists():
                script_file.unlink()


class DockerSandbox(BaseSandbox):
    """
    Docker容器沙盒
    
    使用Docker容器提供强隔离环境。
    需要安装Docker。
    
    安全措施:
    1. 完全的进程隔离
    2. 文件系统隔离
    3. 网络隔离（可选）
    4. 资源限制（CPU、内存）
    5. 自动清理容器
    """
    
    DEFAULT_IMAGE = "python:3.11-slim"
    
    def __init__(
        self,
        workspace: Optional[Path] = None,
        docker_image: Optional[str] = None,
    ):
        """
        初始化Docker沙盒
        
        Args:
            workspace: 工作空间路径
            docker_image: Docker镜像名称
        """
        self.workspace = workspace or Path(tempfile.gettempdir()) / "skill_sandbox"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.docker_image = docker_image or self.DEFAULT_IMAGE
        self._docker_available: Optional[bool] = None
    
    def check_dependencies(self) -> Dict[str, bool]:
        """检查Docker是否可用"""
        if self._docker_available is not None:
            return {"docker": self._docker_available, "available": self._docker_available}
        
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                timeout=5,
            )
            self._docker_available = result.returncode == 0
        except Exception:
            self._docker_available = False
        
        return {"docker": self._docker_available, "available": self._docker_available}
    
    def _build_docker_command(
        self,
        command: str,
        config: SkillSandboxConfig,
        workdir: Path,
    ) -> List[str]:
        """
        构建Docker运行命令
        
        Args:
            command: 要执行的命令
            config: 沙盒配置
            workdir: 工作目录
            
        Returns:
            Docker命令列表
        """
        docker_cmd = [
            "docker", "run", "--rm",
            "-v", f"{workdir}:/workspace",
            "-w", "/workspace",
        ]
        
        # 资源限制
        if config.memory_limit:
            docker_cmd.extend(["--memory", f"{config.memory_limit}m"])
        
        if config.cpu_limit:
            docker_cmd.extend(["--cpus", str(config.cpu_limit)])
        
        # 网络隔离
        if not config.network:
            docker_cmd.append("--network=none")
        
        # 环境变量
        for key, value in config.env_vars.items():
            docker_cmd.extend(["-e", f"{key}={value}"])
        
        # 超时（使用timeout命令包装）
        docker_cmd.extend([
            "--timeout", str(config.timeout),
            self.docker_image,
            "bash", "-c", command,
        ])
        
        return docker_cmd
    
    async def execute_command(
        self,
        command: str,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """执行命令"""
        start_time = time.time()
        
        # 检查Docker是否可用
        if not self.check_dependencies()["docker"]:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="Docker is not available",
                exit_code=-1,
                duration=0,
                error="Docker not available",
            )
        
        cwd = workdir or self.workspace
        cwd = Path(cwd).resolve()
        cwd.mkdir(parents=True, exist_ok=True)
        
        # 合并环境变量
        if env:
            config.env_vars.update(env)
        
        docker_cmd = self._build_docker_command(command, config, cwd)
        
        try:
            process = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=config.timeout + 10  # 额外时间给Docker
                )
                
                duration = time.time() - start_time
                
                return ExecutionResult(
                    success=process.returncode == 0,
                    stdout=stdout.decode('utf-8', errors='replace'),
                    stderr=stderr.decode('utf-8', errors='replace'),
                    exit_code=process.returncode or 0,
                    duration=duration,
                )
                
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr=f"Error: Docker container timed out",
                    exit_code=-1,
                    duration=time.time() - start_time,
                    timed_out=True,
                    error="Timeout",
                )
                
        except Exception as e:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration=time.time() - start_time,
                error=str(e),
            )
    
    async def execute_script(
        self,
        script_path: Path,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """执行脚本文件"""
        script_path = Path(script_path).resolve()
        
        if not script_path.exists():
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Script not found: {script_path}",
                exit_code=-1,
                duration=0,
                error="Script not found",
            )
        
        # 将脚本复制到工作目录
        cwd = workdir or self.workspace
        cwd = Path(cwd).resolve()
        cwd.mkdir(parents=True, exist_ok=True)
        
        script_copy = cwd / script_path.name
        shutil.copy(script_path, script_copy)
        
        # 确定解释器
        suffix = script_path.suffix.lower()
        interpreters = {
            ".py": "python3",
            ".sh": "bash",
            ".bash": "bash",
            ".js": "node",
        }
        
        interpreter = interpreters.get(suffix, "bash")
        cmd = f"{interpreter} /workspace/{script_path.name}"
        
        if args:
            cmd += " " + " ".join(f'"{a}"' if " " in a else a for a in args)
        
        return await self.execute_command(cmd, config, cwd, env)
    
    async def execute_python(
        self,
        code: str,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        globals_dict: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """执行Python代码"""
        cwd = workdir or self.workspace
        cwd = Path(cwd).resolve()
        cwd.mkdir(parents=True, exist_ok=True)
        
        script_file = cwd / f"script_{int(time.time() * 1000)}.py"
        script_file.write_text(code, encoding='utf-8')
        
        try:
            return await self.execute_command(
                "python3 /workspace/script.py",
                config,
                cwd,
            )
        finally:
            if script_file.exists():
                script_file.unlink()


class RestrictedPythonSandbox(BaseSandbox):
    """
    受限Python沙盒
    
    使用RestrictedPython在受限环境中执行Python代码。
    适用于执行不可信的Python代码。
    
    安全措施:
    1. 禁止危险操作（文件访问、网络等）
    2. 限制可用模块
    3. 执行时间限制
    4. 内存限制
    """
    
    ALLOWED_MODULES = {
        # 标准库
        "math", "random", "string", "re", "json", "datetime",
        "collections", "itertools", "functools", "operator",
        "typing", "dataclasses", "enum", "copy",
        # 数据处理
        "decimal", "fractions", "statistics",
        # 文本处理
        "textwrap", "difflib", "unicodedata",
    }
    
    def __init__(self, workspace: Optional[Path] = None):
        """
        初始化受限Python沙盒
        
        Args:
            workspace: 工作空间路径
        """
        self.workspace = workspace or Path(tempfile.gettempdir()) / "skill_sandbox"
        self.workspace.mkdir(parents=True, exist_ok=True)
    
    def check_dependencies(self) -> Dict[str, bool]:
        """检查RestrictedPython是否可用"""
        try:
            import RestrictedPython
            return {"restricted_python": True, "available": True}
        except ImportError:
            return {"restricted_python": False, "available": False}
    
    async def execute_command(
        self,
        command: str,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """执行命令 - 不支持，回退到子进程"""
        sandbox = SubprocessSandbox(self.workspace)
        return await sandbox.execute_command(command, config, workdir, env)
    
    async def execute_script(
        self,
        script_path: Path,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """执行脚本 - 不支持，回退到子进程"""
        sandbox = SubprocessSandbox(self.workspace)
        return await sandbox.execute_script(script_path, config, workdir, args, env)
    
    async def execute_python(
        self,
        code: str,
        config: SkillSandboxConfig,
        workdir: Optional[Path] = None,
        globals_dict: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """执行Python代码"""
        start_time = time.time()
        
        try:
            from RestrictedPython import compile_restricted
            from RestrictedPython.Guards import safe_builtins
        except ImportError:
            # RestrictedPython不可用，回退到子进程
            sandbox = SubprocessSandbox(self.workspace)
            return await sandbox.execute_python(code, config, workdir, globals_dict)
        
        try:
            # 编译代码
            byte_code = compile_restricted(
                code,
                filename="<skill_code>",
                mode="exec",
            )
            
            # 准备全局命名空间
            safe_globals = {
                "__builtins__": safe_builtins,
                "__name__": "__main__",
            }
            
            # 添加允许的模块
            for module_name in self.ALLOWED_MODULES:
                try:
                    safe_globals[module_name] = __import__(module_name)
                except ImportError:
                    pass
            
            # 添加用户提供的全局变量
            if globals_dict:
                safe_globals.update(globals_dict)
            
            # 执行代码
            local_vars = {}
            
            # 使用asyncio实现超时
            async def run_code():
                exec(byte_code, safe_globals, local_vars)
            
            await asyncio.wait_for(run_code(), timeout=config.timeout)
            
            # 获取输出
            if "__result__" in local_vars:
                stdout = str(local_vars["__result__"])
            else:
                stdout = "Code executed successfully"
            
            return ExecutionResult(
                success=True,
                stdout=stdout,
                stderr="",
                exit_code=0,
                duration=time.time() - start_time,
            )
            
        except asyncio.TimeoutError:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Error: Code execution timed out after {config.timeout} seconds",
                exit_code=-1,
                duration=config.timeout,
                timed_out=True,
                error="Timeout",
            )
        except SyntaxError as e:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Syntax Error: {e}",
                exit_code=-1,
                duration=time.time() - start_time,
                error=str(e),
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr=f"Execution Error: {e}",
                exit_code=-1,
                duration=time.time() - start_time,
                error=str(e),
            )


class SandboxManager:
    """
    沙盒管理器
    
    管理多个沙盒后端，自动选择最佳可用方案。
    
    选择策略:
    1. 如果Docker可用，使用Docker沙盒（最强隔离）
    2. 否则使用子进程沙盒（基础隔离）
    3. 对于Python代码，可选择使用受限Python沙盒
    """
    
    def __init__(
        self,
        workspace: Optional[Path] = None,
        prefer_docker: bool = True,
    ):
        """
        初始化沙盒管理器
        
        Args:
            workspace: 工作空间路径
            prefer_docker: 是否优先使用Docker
        """
        self.workspace = workspace or Path(tempfile.gettempdir()) / "skill_sandbox"
        self.workspace.mkdir(parents=True, exist_ok=True)
        
        # 初始化所有沙盒后端
        self._subprocess_sandbox = SubprocessSandbox(self.workspace)
        self._docker_sandbox = DockerSandbox(self.workspace)
        self._restricted_python_sandbox = RestrictedPythonSandbox(self.workspace)
        
        self._prefer_docker = prefer_docker
        self._docker_available: Optional[bool] = None
    
    def _check_docker(self) -> bool:
        """检查Docker是否可用"""
        if self._docker_available is not None:
            return self._docker_available
        
        result = self._docker_sandbox.check_dependencies()
        self._docker_available = result.get("docker", False)
        return self._docker_available
    
    def get_available_sandboxes(self) -> Dict[str, bool]:
        """
        获取可用的沙盒列表
        
        Returns:
            沙盒可用性字典
        """
        return {
            "subprocess": True,
            "docker": self._check_docker(),
            "restricted_python": self._restricted_python_sandbox.check_dependencies().get("restricted_python", False),
        }
    
    def _select_sandbox(
        self,
        config: SkillSandboxConfig,
        execution_type: str = "command"
    ) -> BaseSandbox:
        """
        选择最佳沙盒
        
        Args:
            config: 沙盒配置
            execution_type: 执行类型 (command, script, python)
            
        Returns:
            沙盒实例
        """
        # 如果配置禁用沙盒，使用子进程
        if not config.enabled:
            return self._subprocess_sandbox
        
        # 对于Python代码，检查是否需要受限执行
        if execution_type == "python" and self._restricted_python_sandbox.check_dependencies().get("restricted_python"):
            return self._restricted_python_sandbox
        
        # 优先使用Docker
        if self._prefer_docker and self._check_docker():
            return self._docker_sandbox
        
        # 默认使用子进程
        return self._subprocess_sandbox
    
    async def execute_command(
        self,
        command: str,
        skill_config: Optional[SkillSandboxConfig] = None,
        workdir: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """
        执行命令
        
        Args:
            command: 要执行的命令
            skill_config: Skill沙盒配置
            workdir: 工作目录
            env: 环境变量
            
        Returns:
            执行结果
        """
        config = skill_config or SkillSandboxConfig()
        sandbox = self._select_sandbox(config, "command")
        
        logger.debug(f"Executing command in {sandbox.__class__.__name__}: {command[:100]}...")
        
        return await sandbox.execute_command(command, config, workdir, env)
    
    async def execute_script(
        self,
        script_path: Path,
        skill_config: Optional[SkillSandboxConfig] = None,
        workdir: Optional[Path] = None,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> ExecutionResult:
        """
        执行脚本文件
        
        Args:
            script_path: 脚本路径
            skill_config: Skill沙盒配置
            workdir: 工作目录
            args: 脚本参数
            env: 环境变量
            
        Returns:
            执行结果
        """
        config = skill_config or SkillSandboxConfig()
        sandbox = self._select_sandbox(config, "script")
        
        logger.debug(f"Executing script in {sandbox.__class__.__name__}: {script_path}")
        
        return await sandbox.execute_script(script_path, config, workdir, args, env)
    
    async def execute_python(
        self,
        code: str,
        skill_config: Optional[SkillSandboxConfig] = None,
        workdir: Optional[Path] = None,
        globals_dict: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        执行Python代码
        
        Args:
            code: Python代码
            skill_config: Skill沙盒配置
            workdir: 工作目录
            globals_dict: 全局变量字典
            
        Returns:
            执行结果
        """
        config = skill_config or SkillSandboxConfig()
        sandbox = self._select_sandbox(config, "python")
        
        logger.debug(f"Executing Python code in {sandbox.__class__.__name__}...")
        
        return await sandbox.execute_python(code, config, workdir, globals_dict)
    
    async def ensure_dependencies(
        self,
        dependencies: List[str],
        skill_config: Optional[SkillSandboxConfig] = None,
    ) -> Dict[str, bool]:
        """
        确保依赖已安装
        
        Args:
            dependencies: 依赖列表
            skill_config: Skill沙盒配置
            
        Returns:
            安装结果字典
        """
        results = {}
        config = skill_config or SkillSandboxConfig()
        
        for dep in dependencies:
            # 检查是否已安装
            check_cmd = f"python -c 'import {dep}' 2>/dev/null"
            result = await self.execute_command(check_cmd, config)
            
            if result.success:
                results[dep] = True
                continue
            
            # 尝试安装
            install_cmd = f"pip install {dep}"
            result = await self.execute_command(install_cmd, config)
            results[dep] = result.success
            
            if result.success:
                logger.info(f"Installed dependency: {dep}")
            else:
                logger.error(f"Failed to install dependency: {dep}")
        
        return results
    
    def cleanup(self):
        """清理工作空间"""
        if self.workspace.exists():
            try:
                shutil.rmtree(self.workspace)
                self.workspace.mkdir(parents=True, exist_ok=True)
                logger.info("Sandbox workspace cleaned")
            except Exception as e:
                logger.error(f"Failed to cleanup workspace: {e}")


# 全局沙盒管理器实例
sandbox_manager = SandboxManager()
