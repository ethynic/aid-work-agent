#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Loader - Skill文件加载器

负责从文件系统加载Skill定义，解析SKILL.md文件。
兼容 AgentSkills 开放规范 (agentskills.io)。

Skill目录结构:
    skills/
    ├── pdf/
    │   ├── SKILL.md          # 必需: YAML前置元数据 + Markdown正文
    │   ├── scripts/          # 可选: 辅助脚本
    │   ├── references/       # 可选: 参考文档
    │   └── assets/           # 可选: 模板、输出文件
    └── email/
        └── SKILL.md

SKILL.md格式（AgentSkills 标准）:
    ---
    name: pdf
    description: 处理PDF文件。用于读取、创建或合并PDF。
    version: 1.0.0
    author: system
    paths: "**/*.pdf"
    dependencies:
        - pdftotext
        - PyMuPDF
    ---

    # PDF处理技能

    ## 读取PDF

    使用pdftotext快速提取文本:
    ```bash
    pdftotext input.pdf -
    ```
    ...
"""

import re
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from loguru import logger
import yaml


@dataclass
class SkillDependency:
    """Skill依赖项"""
    name: str
    type: str = "pip"  # pip, apt, npm, etc.
    version: Optional[str] = None


@dataclass
class Skill:
    """Skill定义 - 兼容 AgentSkills 开放规范 (agentskills.io)

    支持 Claude Code / OpenClaw / SkillsMP 生态的 SKILL.md 标准格式。
    所有可选字段均有默认值，确保向后兼容。
    """
    name: str
    description: str
    body: str  # SKILL.md正文内容
    path: Path  # SKILL.md文件路径
    dir: Path  # Skill目录路径

    # 可选元数据
    version: str = "1.0.0"
    author: str = "unknown"
    license: Optional[str] = None
    compatibility: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    # AgentSkills 标准字段
    allowed_tools: Optional[List[str]] = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    argument_hint: Optional[str] = None
    context_mode: str = "inline"  # inline | fork
    model: Optional[str] = None
    effort: Optional[str] = None
    paths: Optional[List[str]] = None  # 文件 glob 模式，用于文件类型匹配
    shell: str = "bash"
    hooks: Optional[Dict[str, str]] = None  # {onLoad: cmd, onUnload: cmd}
    agent: Optional[str] = None  # fork 时使用的子智能体类型

    # 环境变量声明
    env: List[Dict[str, Any]] = field(default_factory=list)

    # 数据库表初始化脚本（相对于 scripts/ 目录，如 "after_sales_tool.py"）
    init_script: Optional[str] = None

    # 依赖项
    dependencies: List[SkillDependency] = field(default_factory=list)

    # 资源文件
    scripts: List[Path] = field(default_factory=list)
    references: List[Path] = field(default_factory=list)
    assets: List[Path] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "path": str(self.path),
            "dir": str(self.dir),
            "context_mode": self.context_mode,
            "argument_hint": self.argument_hint,
            "model": self.model,
            "effort": self.effort,
            "paths": self.paths,
            "shell": self.shell,
            "hooks": self.hooks,
            "agent": self.agent,
            "dependencies": [
                {"name": d.name, "type": d.type, "version": d.version}
                for d in self.dependencies
            ],
            "scripts": [str(p) for p in self.scripts],
            "references": [str(p) for p in self.references],
            "assets": [str(p) for p in self.assets],
        }


class SkillLoader:
    """
    Skill加载器
    
    负责从文件系统加载Skill定义，解析SKILL.md文件。
    
    渐进式加载策略:
        Layer 1: 元数据 (启动时加载) ~100 tokens/skill
                 仅name和description
        
        Layer 2: SKILL.md正文 (触发时加载) ~2000 tokens
                 详细指令
        
        Layer 3: 资源文件 (按需加载) 无限制
                 scripts/, references/, assets/
    
    这种设计保持上下文精简，同时允许任意深度的资源访问。
    """
    
    def __init__(self, skills_dir: Path):
        """
        初始化Skill加载器
        
        Args:
            skills_dir: Skill目录路径
        """
        self.skills_dir = Path(skills_dir)
        self.skills: Dict[str, Skill] = {}
        self._metadata_cache: Dict[str, Dict] = {}  # 元数据缓存
        
        if self.skills_dir.exists():
            self.load_skills()
        else:
            logger.warning(f"Skills directory not found: {skills_dir}")
    
    def parse_skill_md(self, path: Path) -> Optional[Skill]:
        """
        解析SKILL.md文件
        
        Args:
            path: SKILL.md文件路径
            
        Returns:
            Skill对象，如果解析失败返回None
        """
        try:
            content = path.read_text(encoding='utf-8')
        except Exception as e:
            logger.error(f"Failed to read skill file {path}: {e}")
            return None
        
        # 匹配YAML前置元数据
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if not match:
            logger.warning(f"Invalid SKILL.md format (no frontmatter): {path}")
            return None
        
        frontmatter_str, body = match.groups()
        
        # 解析YAML前置元数据
        try:
            frontmatter = yaml.safe_load(frontmatter_str)
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse YAML frontmatter in {path}: {e}")
            return None
        
        # 必需字段检查
        if "name" not in frontmatter or "description" not in frontmatter:
            logger.warning(f"Missing required fields in {path}: name, description")
            return None
        
        # 解析依赖项
        dependencies = []
        for dep in frontmatter.get("dependencies", []):
            if isinstance(dep, str):
                dependencies.append(SkillDependency(name=dep))
            elif isinstance(dep, dict):
                dependencies.append(SkillDependency(
                    name=dep.get("name", ""),
                    type=dep.get("type", "pip"),
                    version=dep.get("version"),
                ))

        # 解析 metadata（兼容 AgentSkills / OpenClaw）
        metadata = frontmatter.get("metadata")
        if isinstance(metadata, str):
            try:
                import json
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, ValueError):
                metadata = None
        if metadata is None:
            metadata = {}

        # 解析 allowed-tools
        allowed_tools_raw = frontmatter.get("allowed-tools", "")
        allowed_tools = allowed_tools_raw.split() if isinstance(allowed_tools_raw, str) and allowed_tools_raw else []

        # 解析 paths（AgentSkills 标准字段，逗号或空格分隔的 glob 模式）
        paths_raw = frontmatter.get("paths")
        paths = None
        if paths_raw:
            if isinstance(paths_raw, str):
                paths = [p.strip() for p in paths_raw.replace(",", " ").split() if p.strip()]
            elif isinstance(paths_raw, list):
                paths = paths_raw

        # 解析 hooks
        hooks_raw = frontmatter.get("hooks")
        hooks = None
        if isinstance(hooks_raw, dict):
            hooks = hooks_raw

        # 解析 env 环境变量声明
        env_raw = frontmatter.get("env", [])
        env_vars = []
        if isinstance(env_raw, list):
            for item in env_raw:
                if isinstance(item, str):
                    env_vars.append({"name": item})
                elif isinstance(item, dict):
                    env_vars.append({
                        "name": item.get("name", ""),
                        "default": item.get("default"),
                    })

        skill = Skill(
            name=frontmatter["name"],
            description=frontmatter["description"],
            body=body.strip(),
            path=path,
            dir=path.parent,
            version=frontmatter.get("version")
                or (metadata.get("version") if isinstance(metadata, dict) else None)
                or "1.0.0",
            author=frontmatter.get("author", "unknown"),
            license=frontmatter.get("license"),
            compatibility=frontmatter.get("compatibility"),
            metadata=metadata if metadata else None,
            allowed_tools=allowed_tools if allowed_tools else None,
            user_invocable=frontmatter.get("user-invocable", True),
            disable_model_invocation=frontmatter.get("disable-model-invocation", False),
            argument_hint=frontmatter.get("argument-hint"),
            context_mode=frontmatter.get("context", "inline"),
            model=frontmatter.get("model"),
            effort=frontmatter.get("effort"),
            paths=paths,
            shell=frontmatter.get("shell", "bash"),
            hooks=hooks,
            agent=frontmatter.get("agent"),
            env=env_vars,
            dependencies=dependencies,
            init_script=frontmatter.get("init_script"),
        )
        
        # 扫描资源文件
        self._scan_resources(skill)
        
        return skill
    
    def _scan_resources(self, skill: Skill):
        """
        扫描Skill的资源文件
        
        Args:
            skill: Skill对象
        """
        for folder, attr in [
            ("scripts", "scripts"),
            ("references", "references"),
            ("assets", "assets"),
        ]:
            folder_path = skill.dir / folder
            if folder_path.exists() and folder_path.is_dir():
                files = [
                    f for f in folder_path.iterdir()
                    if f.is_file() and not f.name.startswith(".")
                ]
                setattr(skill, attr, files)
    
    def load_skills(self):
        """
        扫描并加载所有Skill

        仅加载元数据，正文内容按需加载。
        这保持初始上下文精简。
        """
        if not self.skills_dir.exists():
            return

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            if skill_dir.name.startswith("."):
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            skill = self.parse_skill_md(skill_md)
            if skill:
                self.skills[skill.name] = skill
                # logger.info(f"Loaded skill: {skill.name} v{skill.version}")

        logger.info(f"Total skills loaded: {len(self.skills)}")

        # 对需要数据库初始化的 skill 执行初始化
        self._init_skill_tables()

    def _init_skill_tables(self):
        """初始化需要数据库表的 skill（通用机制）

        遍历所有已加载的 skill，检查其 SKILL.md 中是否声明了 init_script。
        如果声明了，自动 importlib 加载该脚本并调用 init_tables()。
        """
        for name, skill in self.skills.items():
            if not skill.init_script:
                continue
            try:
                import importlib.util
                script_path = skill.dir / "scripts" / skill.init_script
                if not script_path.exists():
                    logger.warning(f"Skill '{name}' init_script not found: {script_path}")
                    continue
                spec = importlib.util.spec_from_file_location(
                    f"{name}_init",
                    str(script_path),
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                if hasattr(module, "init_tables"):
                    module.init_tables()
                    logger.info(f"Skill '{name}' tables initialized via {skill.init_script}")
                else:
                    logger.warning(f"Skill '{name}' init_script has no init_tables() function")
            except Exception as e:
                logger.warning(f"Failed to initialize tables for skill '{name}': {e}")
    
    def get_skill_descriptions(self) -> str:
        """
        生成Skill描述列表

        这是Layer 1 - 仅name和description，约100 tokens/skill。
        完整内容(Layer 2)仅在Skill工具调用时加载。

        Returns:
            Skill描述字符串
        """
        if not self.skills:
            return "(no skills available)"

        return "\n".join(
            f"- {name} (v{skill.version}): {skill.description}"
            for name, skill in self.skills.items()
        )
    
    def get_skill_content(self, name: str, substitutions: Optional[Dict[str, str]] = None) -> Optional[str]:
        """
        获取Skill完整内容用于注入

        这是Layer 2 - 完整的SKILL.md正文，加上可用资源提示(Layer 3)。

        Args:
            name: Skill名称
            substitutions: 可选的替换上下文，用于 $ARGUMENTS 等变量替换

        Returns:
            Skill内容字符串，如果未找到返回None
        """
        if name not in self.skills:
            return None

        skill = self.skills[name]

        # 加载 Skill .env 文件到进程环境变量
        self._load_skill_env(skill)

        body = skill.body

        # 执行字符串替换（AgentSkills 标准）
        if substitutions:
            from src.core.skill_substitutions import SkillSubstitutor
            body = SkillSubstitutor.substitute(body, substitutions)

        # 处理动态上下文注入 !`command`（AgentSkills 标准）
        body = self._process_dynamic_context(body, skill.dir)

        content = f"# Skill: {skill.name}\n\n{body}"

        # 列出可用资源 (Layer 3提示)
        resources = []
        for folder, label in [
            ("scripts", "Scripts"),
            ("references", "References"),
            ("assets", "Assets"),
        ]:
            files = getattr(skill, folder, [])
            if files:
                resources.append(f"{label}: {', '.join(f.name for f in files)}")

        if resources:
            content += f"\n\n**Available resources in {skill.dir}:**\n"
            content += "\n".join(f"- {r}" for r in resources)

        return content

    def _load_skill_env(self, skill: Skill) -> None:
        """加载 Skill 目录下的 .env 文件到进程环境变量。

        加载优先级（从低到高）：
        1. Skill 默认级: skill_dir/.env
        2. 租户级: storage/tenants/{tenant_id}/skills/{skill_name}/.env

        使用 override=False，不覆盖已有的同名变量。
        """
        import os
        env_files = []

        # 优先级 1: Skill 默认级 .env
        skill_env = skill.dir / ".env"
        if skill_env.exists():
            env_files.append(skill_env)

        # 优先级 2: 租户级 .env
        tenant_id = os.environ.get("CURRENT_TENANT_ID")
        if tenant_id:
            tenant_env = Path(f"storage/tenants/{tenant_id}/skills/{skill.name}/.env")
            if tenant_env.exists():
                env_files.append(tenant_env)

        for env_file in env_files:
            try:
                from dotenv import load_dotenv
                load_dotenv(env_file, override=False)
                logger.debug(f"Loaded skill env from {env_file}")
            except ImportError:
                logger.warning("python-dotenv not installed, skipping .env loading")
            except Exception as e:
                logger.warning(f"Failed to load skill env from {env_file}: {e}")

        # 注入 env 声明中的默认值（仅对环境中尚不存在的变量）
        for var_decl in skill.env:
            var_name = var_decl.get("name", "")
            default_val = var_decl.get("default")
            if var_name and default_val is not None and var_name not in os.environ:
                os.environ[var_name] = str(default_val)
                logger.debug(f"Set default env var {var_name}={default_val}")

        # 检查必需变量（无默认值的变量是否已设置）
        for var_decl in skill.env:
            var_name = var_decl.get("name", "")
            default_val = var_decl.get("default")
            if var_name and default_val is None and var_name not in os.environ:
                logger.warning(
                    f"Skill '{skill.name}' requires env var '{var_name}' but it is not set"
                )

    def _process_dynamic_context(self, body: str, skill_dir: Path) -> str:
        """
        处理 SKILL.md body 中的动态上下文注入语法 !`command`。

        企业环境默认关闭，需要配置 skills.security.allow_dynamic_context: true
        或 skill 的 metadata.allow_dynamic_context: true 才会执行。

        Args:
            body: SKILL.md 正文
            skill_dir: Skill 目录路径

        Returns:
            处理后的正文
        """
        # 检查是否包含动态注入语法
        if "!`" not in body:
            return body

        # 安全检查：默认关闭，需要配置开启
        from src.config.settings import settings
        allow = getattr(settings, "skills_security_allow_dynamic_context", False)
        if not allow:
            # 也检查 skill 级别的配置
            # 此处无法直接获取 skill 对象，在调用方检查
            return body

        import subprocess

        pattern = r"!`([^`]+)`"

        def replacer(match: re.Match) -> str:
            cmd = match.group(1)
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=str(skill_dir),
                    timeout=10,
                )
                return result.stdout.strip()
            except subprocess.TimeoutExpired:
                return f"(error: command timed out: {cmd})"
            except Exception as e:
                return f"(error running: {cmd}: {e})"

        return re.sub(pattern, replacer, body)

    def get_skill(self, name: str) -> Optional[Skill]:
        """
        获取Skill对象
        
        Args:
            name: Skill名称
            
        Returns:
            Skill对象，如果未找到返回None
        """
        return self.skills.get(name)
    
    def list_skills(self) -> List[str]:
        """
        返回可用的Skill名称列表
        
        Returns:
            Skill名称列表
        """
        return list(self.skills.keys())
    
    def match_by_file(self, filename: str) -> Optional[str]:
        """
        根据文件名匹配Skill（基于 paths 字段）

        AgentSkills 标准使用 paths glob 模式进行文件类型匹配。
        从 paths 中提取文件扩展名进行匹配。

        Args:
            filename: 文件名

        Returns:
            匹配的Skill名称，如果没有匹配返回None
        """
        from fnmatch import fnmatch
        filename_lower = filename.lower()

        for name, skill in self.skills.items():
            if not skill.paths:
                continue
            for pattern in skill.paths:
                # 直接用 glob 模式匹配文件名
                if fnmatch(filename_lower, pattern.lower()):
                    return name
                # 从 glob 模式中提取扩展名进行后缀匹配
                # 例如 "src/**/*.pdf" → 匹配 .pdf 文件
                ext = self._extract_extension(pattern)
                if ext and filename_lower.endswith(ext.lower()):
                    return name

        return None

    @staticmethod
    def _extract_extension(pattern: str) -> Optional[str]:
        """从 glob 模式中提取文件扩展名"""
        # 匹配模式末尾的扩展名，如 **/*.pdf → .pdf
        match = re.search(r'\*?(\.\w+)$', pattern)
        return match.group(1) if match else None
    
    def reload_skills(self):
        """
        重新加载所有Skill
        """
        self.skills.clear()
        self._metadata_cache.clear()
        self.load_skills()
        logger.info("Skills reloaded")
