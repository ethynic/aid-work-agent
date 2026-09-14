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
        # key = dir_name（目录名/agent_id），显示名仅用于展示、允许重名
        self.configs: Dict[str, SubagentConfig] = {}
        
        if subagents_dir and subagents_dir.exists():
            self.load_all()
    
    def load_all(self) -> Dict[str, SubagentConfig]:
        """
        加载所有Subagent配置

        Returns:
            配置字典 {dir_name: SubagentConfig}，以目录名（agent_id）为 key
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
                config.dir_name = subdir.name
                # key 用 dir_name（agent_id），显示名仅用于展示、允许重名
                self.configs[config.dir_name] = config
        
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
        
        # 提取所有 {provider}_model_code 字段，组装为 {provider: model_code} 字典
        llm_model_codes = {
            k.removesuffix("_model_code"): v
            for k, v in frontmatter.items()
            if k.endswith("_model_code") and isinstance(v, str) and v
        } or None

        # 构建配置对象
        config = SubagentConfig(
            name=frontmatter.get("name", ""),
            description=frontmatter.get("description", ""),
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", "unknown"),
            triggers=frontmatter.get("triggers", {}),
            tools=frontmatter.get("tools", {}),
            skills=frontmatter.get("skills", {}),
            context=frontmatter.get("context", {}),
            recap=frontmatter.get("recap") or {},
            system_prompt=frontmatter.get("system_prompt", body.strip()),
            delegatable_to=frontmatter.get("delegatable_to", []),
            allow_delegation=frontmatter.get("allow_delegation", True),
            llm_provider=frontmatter.get("llm_provider", None),
            llm_model_codes=llm_model_codes,
            business_pages=frontmatter.get("business_pages", None),
            reply_style=frontmatter.get("reply_style", None),
            chat_toolbar=frontmatter.get("chat_toolbar", []) or [],
            upload_accept=frontmatter.get("upload_accept", None),
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
            name: dir_name（目录名/agent_id）

        Returns:
            配置对象，不存在返回None
        """
        return self.configs.get(name)

    def list_subagents(self) -> List[str]:
        """
        列出所有Subagent的 dir_name

        Returns:
            dir_name 列表
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

    @staticmethod
    def serialize_to_subagent_md(config, body: str = "") -> str:
        """
        将 SubagentConfig 序列化为 SUBAGENT.md 格式

        Args:
            config: SubagentConfig 配置对象
            body: Markdown 正文内容（system_prompt 的详细内容）

        Returns:
            SUBAGENT.md 格式的字符串
        """
        frontmatter = {
            "name": config.name,
            "description": config.description,
            "version": config.version or "1.0.0",
            "author": config.author or "admin",
        }

        if config.triggers:
            frontmatter["triggers"] = config.triggers
        if config.tools:
            frontmatter["tools"] = config.tools
        if config.skills:
            frontmatter["skills"] = config.skills
        if config.context:
            frontmatter["context"] = config.context
        if config.reply_style:
            frontmatter["reply_style"] = config.reply_style
        if config.business_pages:
            frontmatter["business_pages"] = config.business_pages
        if config.chat_toolbar:
            frontmatter["chat_toolbar"] = config.chat_toolbar
        if config.recap:
            frontmatter["recap"] = config.recap
        if config.upload_accept:
            frontmatter["upload_accept"] = config.upload_accept
        if config.llm_provider:
            frontmatter["llm_provider"] = config.llm_provider
        if config.llm_model_codes:
            for provider, model in config.llm_model_codes.items():
                frontmatter[f"{provider}_model_code"] = model

        yaml_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return f"---\n{yaml_str}---\n\n{body}"

