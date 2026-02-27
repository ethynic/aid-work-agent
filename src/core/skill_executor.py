#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Executor - Skill执行器

负责执行Skill中的命令和脚本，协调Skill加载、沙盒执行和结果处理。

主要功能:
1. 加载Skill内容并注入到对话中
2. 在沙盒环境中执行Skill命令
3. 处理Skill依赖
4. 管理Skill工作目录和文件

使用示例:
    executor = SkillExecutor(skill_registry, sandbox_manager)
    
    # 加载Skill内容
    content = await executor.load_skill("pdf")
    
    # 执行Skill命令
    result = await executor.execute_skill_command(
        "pdf",
        "pdftotext input.pdf -",
        files={"input.pdf": pdf_bytes}
    )
"""

import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from loguru import logger

from src.core.skill_loader import Skill, SkillDependency, SkillSandboxConfig
from src.core.skill_registry import SkillRegistry
from src.core.sandbox import SandboxManager, ExecutionResult


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
    
    负责执行Skill中的命令和脚本。
    
    执行流程:
    1. 加载Skill定义
    2. 创建执行上下文
    3. 准备依赖环境
    4. 在沙盒中执行命令
    5. 收集结果并清理
    """
    
    def __init__(
        self,
        skill_registry: SkillRegistry,
        sandbox_manager: Optional[SandboxManager] = None,
        workspace: Optional[Path] = None,
    ):
        """
        初始化Skill执行器
        
        Args:
            skill_registry: Skill注册表
            sandbox_manager: 沙盒管理器
            workspace: 工作空间路径
        """
        self.skill_registry = skill_registry
        self.sandbox_manager = sandbox_manager or SandboxManager()
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
    
    async def load_skill(self, skill_name: str) -> Optional[str]:
        """
        加载Skill内容
        
        这是Layer 2 - 完整的SKILL.md正文，用于注入到对话中。
        
        Args:
            skill_name: Skill名称
            
        Returns:
            Skill内容字符串，如果未找到返回None
        """
        skill = self.skill_registry.get(skill_name)
        if not skill:
            available = ", ".join(self.skill_registry.list_skills()) or "none"
            return f"Error: Unknown skill '{skill_name}'. Available: {available}"
        
        content = self.skill_registry.get_content(skill_name)
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
        根据文件名匹配Skill
        
        Args:
            filename: 文件名
            
        Returns:
            匹配的Skill名称
        """
        return self.skill_registry.match_by_file(filename)
    
    def match_skill_by_keyword(self, text: str) -> List[str]:
        """
        根据关键词匹配Skill
        
        Args:
            text: 输入文本
            
        Returns:
            匹配的Skill名称列表
        """
        return self.skill_registry.match_by_keyword(text)
    
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
                check_result = await self.sandbox_manager.execute_command(
                    f"python -c 'import {dep.name}'",
                    skill.sandbox_config,
                    context.workdir,
                )
                
                if check_result.success:
                    results[dep.name] = True
                    continue
                
                # 安装Python包
                version_spec = f"=={dep.version}" if dep.version else ""
                install_result = await self.sandbox_manager.execute_command(
                    f"pip install {dep.name}{version_spec}",
                    skill.sandbox_config,
                    context.workdir,
                )
                results[dep.name] = install_result.success
                
                if install_result.success:
                    logger.info(f"Installed dependency: {dep.name}")
                else:
                    logger.error(f"Failed to install dependency: {dep.name}: {install_result.stderr}")
            
            elif dep.type == "apt":
                # 检查系统包
                check_result = await self.sandbox_manager.execute_command(
                    f"dpkg -l {dep.name}",
                    skill.sandbox_config,
                    context.workdir,
                )
                
                if check_result.success:
                    results[dep.name] = True
                    continue
                
                # 安装系统包（需要sudo权限）
                install_result = await self.sandbox_manager.execute_command(
                    f"apt-get update && apt-get install -y {dep.name}",
                    skill.sandbox_config,
                    context.workdir,
                )
                results[dep.name] = install_result.success
            
            elif dep.type == "npm":
                # 检查npm包
                check_result = await self.sandbox_manager.execute_command(
                    f"npm list {dep.name}",
                    skill.sandbox_config,
                    context.workdir,
                )
                
                if check_result.success:
                    results[dep.name] = True
                    continue
                
                # 安装npm包
                install_result = await self.sandbox_manager.execute_command(
                    f"npm install {dep.name}",
                    skill.sandbox_config,
                    context.workdir,
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
    
    async def execute_skill_command(
        self,
        skill_name: str,
        command: str,
        files: Optional[Dict[str, bytes]] = None,
        variables: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
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
            
            # 执行命令
            result = await self.sandbox_manager.execute_command(
                processed_command,
                skill.sandbox_config,
                context.workdir,
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
            
            # 执行脚本
            result = await self.sandbox_manager.execute_script(
                script_path,
                skill.sandbox_config,
                context.workdir,
                args,
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
            
            # 执行Python代码
            result = await self.sandbox_manager.execute_python(
                code,
                skill.sandbox_config,
                context.workdir,
                globals_dict,
            )
            
            return result
            
        finally:
            context.cleanup()
    
    def _process_command(self, command: str, context: SkillExecutionContext) -> str:
        """
        处理命令中的变量替换
        
        Args:
            command: 原始命令
            context: 执行上下文
            
        Returns:
            处理后的命令
        """
        # 替换文件变量
        for filename in context.files.keys():
            placeholder = f"${{{filename}}}"
            filepath = context.workdir / filename
            command = command.replace(placeholder, str(filepath))
            command = command.replace(f"${filename}", str(filepath))
        
        # 替换自定义变量
        for key, value in context.variables.items():
            placeholder = f"${{{key}}}"
            command = command.replace(placeholder, str(value))
        
        return command
    
    async def process_uploaded_file(
        self,
        filename: str,
        content: bytes,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        处理上传的文件
        
        自动匹配Skill并返回处理建议。
        
        Args:
            filename: 文件名
            content: 文件内容
            session_id: 会话ID
            
        Returns:
            处理建议字典
        """
        # 匹配Skill
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
