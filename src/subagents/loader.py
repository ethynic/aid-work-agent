#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Subagent Loader - Subagent配置加载器

负责从文件系统加载Subagent定义，解析SUBAGENT.md文件。

Subagent目录结构:
    subagents/
    ├── code-reviewer/
    │   ├── SUBAGENT.md          # 必需: YAML前置元数据 + Markdown正文
    │   ├── tools/               # 可选: 专属工具
    │   └── prompts/             # 可选: 专属提示词模板
    ├── data-analyst/
    │   └── SUBAGENT.md
    └── hr-expert/
        └── SUBAGENT.md

SUBAGENT.md格式:
    ---
    name: code-reviewer
    description: 代码审查专家
    version: 1.0.0
    capabilities:
      - code_review
      - security_audit
    triggers:
      file_patterns:
        - "*.py"
        - "*.js"
    tools:
      inherit: false
      allowed:
        - web_search
        - skill_execute
    skills:
      allowed:
        - pdf
    system_prompt: |
      你是一个专业的代码审查助手...
    delegatable_to:
      - pdf-expert
    allow_delegation: true
    ---

    # 代码审查指南
    ...
"""

import re
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger
import yaml

from src.models.subagent import SubagentConfig


class SubagentLoader:
    """
    Subagent配置加载器
    
    负责从文件系统加载Subagent定义，解析SUBAGENT.md文件。
    
    使用示例:
        loader = SubagentLoader(Path("subagents"))
        configs = loader.load_all()
        
        # 获取特定配置
        config = loader.get("code-reviewer")
    """
    
    # 配置文件名
    CONFIG_FILE = "SUBAGENT.md"
    
    def __init__(self, subagents_dir: Optional[Path] = None):
        """
        初始化Subagent加载器
        
        Args:
            subagents_dir: Subagent目录路径
        """
        self.subagents_dir = subagents_dir
        self.configs: Dict[str, SubagentConfig] = {}
        
        if subagents_dir and subagents_dir.exists():
            self.load_all()
    
    def load_all(self) -> Dict[str, SubagentConfig]:
        """
        加载所有Subagent配置
        
        Returns:
            配置字典 {name: SubagentConfig}
        """
        if not self.subagents_dir or not self.subagents_dir.exists():
            logger.warning(f"Subagents directory not found: {self.subagents_dir}")
            return {}
        
        self.configs.clear()
        
        # 遍历子目录
        for subdir in self.subagents_dir.iterdir():
            if not subdir.is_dir():
                continue
            
            config_file = subdir / self.CONFIG_FILE
            if not config_file.exists():
                continue
            
            config = self.parse_subagent_md(config_file)
            if config:
                self.configs[config.name] = config
                logger.info(f"Loaded subagent: {config.name}")
        
        logger.info(f"SubagentLoader loaded {len(self.configs)} subagents")
        return self.configs
    
    def parse_subagent_md(self, path: Path) -> Optional[SubagentConfig]:
        """
        解析SUBAGENT.md文件
        
        Args:
            path: SUBAGENT.md文件路径
            
        Returns:
            SubagentConfig对象，解析失败返回None
        """
        try:
            content = path.read_text(encoding='utf-8')
        except Exception as e:
            logger.error(f"Failed to read subagent file {path}: {e}")
            return None
        
        # 匹配YAML前置元数据
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if not match:
            logger.warning(f"Invalid SUBAGENT.md format (no frontmatter): {path}")
            return None
        
        frontmatter_str, body = match.groups()
        
        # 解析YAML前置元数据
        try:
            frontmatter = yaml.safe_load(frontmatter_str)
        except yaml.YAMLError as e:
            logger.error(f"Failed to parse YAML frontmatter in {path}: {e}")
            return None
        
        # 必需字段检查
        if "name" not in frontmatter:
            logger.warning(f"Missing required field 'name' in {path}")
            return None
        
        # 构建配置对象
        config = SubagentConfig(
            name=frontmatter.get("name", ""),
            description=frontmatter.get("description", ""),
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", "unknown"),
            capabilities=frontmatter.get("capabilities", []),
            triggers=frontmatter.get("triggers", {}),
            tools=frontmatter.get("tools", {}),
            skills=frontmatter.get("skills", {}),
            context=frontmatter.get("context", {}),
            system_prompt=frontmatter.get("system_prompt", body.strip()),
            delegatable_to=frontmatter.get("delegatable_to", []),
            allow_delegation=frontmatter.get("allow_delegation", True),
            path=str(path),
            dir=str(path.parent),
        )
        
        # 如果system_prompt为空，使用body作为系统提示词
        if not config.system_prompt:
            config.system_prompt = body.strip()
        
        return config
    
    def get(self, name: str) -> Optional[SubagentConfig]:
        """
        获取指定Subagent配置
        
        Args:
            name: Subagent名称
            
        Returns:
            配置对象，不存在返回None
        """
        return self.configs.get(name)
    
    def list_subagents(self) -> List[str]:
        """
        列出所有Subagent名称
        
        Returns:
            名称列表
        """
        return list(self.configs.keys())
    
    def reload(self) -> Dict[str, SubagentConfig]:
        """
        重新加载所有配置
        
        Returns:
            配置字典
        """
        return self.load_all()
    
    def get_subagent_content(self, name: str) -> Optional[str]:
        """
        获取Subagent的完整内容（用于注入到上下文）
        
        Args:
            name: Subagent名称
            
        Returns:
            内容字符串
        """
        config = self.get(name)
        if not config:
            return None
        
        # 读取原始文件内容
        if config.path:
            try:
                return Path(config.path).read_text(encoding='utf-8')
            except Exception as e:
                logger.error(f"Failed to read subagent content: {e}")
        
        # 返回基本信息
        return f"# {config.name}\n\n{config.description}\n\n{config.system_prompt}"
