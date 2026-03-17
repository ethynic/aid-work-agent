#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Skill Loader - Skill文件加载器

负责从文件系统加载Skill定义，解析SKILL.md文件。

Skill目录结构:
    skills/
    ├── pdf/
    │   ├── SKILL.md          # 必需: YAML前置元数据 + Markdown正文
    │   ├── scripts/          # 可选: 辅助脚本
    │   ├── references/       # 可选: 参考文档
    │   └── assets/           # 可选: 模板、输出文件
    └── email/
        └── SKILL.md

SKILL.md格式:
    ---
    name: pdf
    description: 处理PDF文件。用于读取、创建或合并PDF。
    version: 1.0.0
    author: system
    dependencies:
        - pdftotext
        - PyMuPDF
    triggers:
        - .pdf
        - pdf文件
        - PDF
    sandbox:
        enabled: true
        timeout: 60
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
class SkillTrigger:
    """Skill触发器"""
    type: str  # file_extension, keyword, regex
    pattern: str
    case_sensitive: bool = False


@dataclass
class Skill:
    """Skill定义"""
    name: str
    description: str
    body: str  # SKILL.md正文内容
    path: Path  # SKILL.md文件路径
    dir: Path  # Skill目录路径
    
    # 可选元数据
    version: str = "1.0.0"
    author: str = "unknown"
    
    # 依赖项
    dependencies: List[SkillDependency] = field(default_factory=list)
    
    # 触发器
    triggers: List[SkillTrigger] = field(default_factory=list)
    
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
            "dependencies": [
                {"name": d.name, "type": d.type, "version": d.version}
                for d in self.dependencies
            ],
            "triggers": [
                {"type": t.type, "pattern": t.pattern, "case_sensitive": t.case_sensitive}
                for t in self.triggers
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
        
        # 解析触发器
        triggers = []
        for trigger in frontmatter.get("triggers", []):
            if isinstance(trigger, str):
                # 自动判断触发器类型
                if trigger.startswith("."):
                    triggers.append(SkillTrigger(
                        type="file_extension",
                        pattern=trigger,
                    ))
                elif trigger.startswith("^") or trigger.endswith("$"):
                    triggers.append(SkillTrigger(
                        type="regex",
                        pattern=trigger,
                    ))
                else:
                    triggers.append(SkillTrigger(
                        type="keyword",
                        pattern=trigger,
                    ))
            elif isinstance(trigger, dict):
                triggers.append(SkillTrigger(
                    type=trigger.get("type", "keyword"),
                    pattern=trigger.get("pattern", ""),
                    case_sensitive=trigger.get("case_sensitive", False),
                ))
        
        # 解析沙盒配置 (已移除，保留向后兼容性忽略sandbox字段)
        # sandbox配置不再使用，直接在运行时环境执行
        
        skill = Skill(
            name=frontmatter["name"],
            description=frontmatter["description"],
            body=body.strip(),
            path=path,
            dir=path.parent,
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", "unknown"),
            dependencies=dependencies,
            triggers=triggers,
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
                logger.info(f"Loaded skill: {skill.name} v{skill.version}")
        
        logger.info(f"Total skills loaded: {len(self.skills)}")
    
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
            f"- {name}: {skill.description}"
            for name, skill in self.skills.items()
        )
    
    def get_skill_content(self, name: str) -> Optional[str]:
        """
        获取Skill完整内容用于注入
        
        这是Layer 2 - 完整的SKILL.md正文，加上可用资源提示(Layer 3)。
        
        Args:
            name: Skill名称
            
        Returns:
            Skill内容字符串，如果未找到返回None
        """
        if name not in self.skills:
            return None
        
        skill = self.skills[name]
        content = f"# Skill: {skill.name}\n\n{skill.body}"
        
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
    
    def match_skill_by_file(self, filename: str) -> Optional[str]:
        """
        根据文件名匹配Skill
        
        Args:
            filename: 文件名
            
        Returns:
            匹配的Skill名称，如果没有匹配返回None
        """
        filename_lower = filename.lower()
        
        for name, skill in self.skills.items():
            for trigger in skill.triggers:
                if trigger.type == "file_extension":
                    pattern = trigger.pattern.lower()
                    if filename_lower.endswith(pattern):
                        return name
                elif trigger.type == "regex":
                    flags = 0 if trigger.case_sensitive else re.IGNORECASE
                    if re.search(trigger.pattern, filename, flags):
                        return name
        
        return None
    
    def match_skill_by_keyword(self, text: str) -> Optional[str]:
        """
        根据关键词匹配Skill
        
        Args:
            text: 输入文本
            
        Returns:
            匹配的Skill名称，如果没有匹配返回None
        """
        text_lower = text.lower()
        
        for name, skill in self.skills.items():
            for trigger in skill.triggers:
                if trigger.type == "keyword":
                    pattern = trigger.pattern if trigger.case_sensitive else trigger.pattern.lower()
                    search_text = text if trigger.case_sensitive else text_lower
                    if pattern in search_text:
                        return name
                elif trigger.type == "regex":
                    flags = 0 if trigger.case_sensitive else re.IGNORECASE
                    if re.search(trigger.pattern, text, flags):
                        return name
        
        return None
    
    def reload_skills(self):
        """
        重新加载所有Skill
        """
        self.skills.clear()
        self._metadata_cache.clear()
        self.load_skills()
        logger.info("Skills reloaded")
