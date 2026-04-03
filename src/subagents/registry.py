#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Subagent Registry - Subagent注册表

管理所有可用的Subagent配置，提供发现、匹配和访问接口。

主要功能:
1. Subagent注册和注销
2. 按名称、能力匹配Subagent
3. 生成Subagent描述供LLM使用
4. 管理Subagent生命周期
"""

from pathlib import Path
from typing import Dict, List, Optional, Set
from loguru import logger

from src.models.subagent import SubagentConfig
from src.subagents.loader import SubagentLoader


class SubagentRegistry:
    """
    Subagent注册表
    
    管理所有可用的Subagent配置，提供统一的访问接口。
    
    使用示例:
        registry = SubagentRegistry()
        registry.load_from_directory(Path("subagents"))
        
        # 获取配置
        config = registry.get("code-reviewer")
        
        # 匹配Subagent
        name = registry.match_by_capability("审查代码的安全性")
        
        # 获取描述
        descriptions = registry.get_descriptions()
    """
    
    def __init__(self, subagents_dir: Optional[Path] = None):
        """
        初始化Subagent注册表
        
        Args:
            subagents_dir: Subagent目录路径，如果提供则自动加载
        """
        self._configs: Dict[str, SubagentConfig] = {}
        self._loader: Optional[SubagentLoader] = None
        
        # 能力索引
        self._capability_index: Dict[str, Set[str]] = {}
        # 文件模式索引
        self._file_pattern_index: Dict[str, str] = {}
        
        if subagents_dir:
            self.load_from_directory(subagents_dir)
    
    def load_from_directory(self, subagents_dir: Path) -> int:
        """
        从目录加载所有Subagent配置
        
        Args:
            subagents_dir: Subagent目录路径
            
        Returns:
            加载的配置数量
        """
        self._loader = SubagentLoader(subagents_dir)
        self._configs = self._loader.configs
        
        # 构建索引
        self._build_indices()
        
        logger.info(f"SubagentRegistry loaded {len(self._configs)} subagents from {subagents_dir}")
        return len(self._configs)
    
    def _build_indices(self):
        """构建加速查找的索引"""
        self._capability_index.clear()
        self._file_pattern_index.clear()

        for name, config in self._configs.items():
            # 能力索引
            for capability in config.capabilities:
                cap_lower = capability.lower()
                if cap_lower not in self._capability_index:
                    self._capability_index[cap_lower] = set()
                self._capability_index[cap_lower].add(name)

            # 文件模式索引
            patterns = config.triggers.get("file_patterns", [])
            for pattern in patterns:
                self._file_pattern_index[pattern.lower()] = name
    
    def register(self, config: SubagentConfig) -> bool:
        """
        注册一个Subagent配置
        
        Args:
            config: 配置对象
            
        Returns:
            是否注册成功
        """
        if config.name in self._configs:
            logger.warning(f"Subagent already registered: {config.name}")
            return False
        
        self._configs[config.name] = config

        # 更新索引
        for capability in config.capabilities:
            cap_lower = capability.lower()
            if cap_lower not in self._capability_index:
                self._capability_index[cap_lower] = set()
            self._capability_index[cap_lower].add(config.name)

        patterns = config.triggers.get("file_patterns", [])
        for pattern in patterns:
            self._file_pattern_index[pattern.lower()] = config.name
        
        logger.info(f"Registered subagent: {config.name}")
        return True
    
    def unregister(self, name: str) -> bool:
        """
        注销一个Subagent配置
        
        Args:
            name: Subagent名称
            
        Returns:
            是否注销成功
        """
        if name not in self._configs:
            logger.warning(f"Subagent not found: {name}")
            return False
        
        config = self._configs.pop(name)

        # 更新索引
        for capability in config.capabilities:
            cap_lower = capability.lower()
            if cap_lower in self._capability_index:
                self._capability_index[cap_lower].discard(name)
                if not self._capability_index[cap_lower]:
                    del self._capability_index[cap_lower]

        patterns = config.triggers.get("file_patterns", [])
        for pattern in patterns:
            self._file_pattern_index.pop(pattern.lower(), None)
        
        logger.info(f"Unregistered subagent: {name}")
        return True
    
    def get(self, name: str) -> Optional[SubagentConfig]:
        """
        获取Subagent配置
        
        Args:
            name: Subagent名称
            
        Returns:
            配置对象，不存在返回None
        """
        return self._configs.get(name)
    
    def get_content(self, name: str) -> Optional[str]:
        """
        获取Subagent完整内容
        
        Args:
            name: Subagent名称
            
        Returns:
            内容字符串
        """
        if self._loader:
            return self._loader.get_subagent_content(name)
        
        config = self.get(name)
        if config:
            return f"# {config.name}\n\n{config.description}\n\n{config.system_prompt}"
        return None
    
    def list_subagents(self) -> List[str]:
        """
        列出所有Subagent名称
        
        Returns:
            名称列表
        """
        return list(self._configs.keys())
    
    def match_by_capability(self, description: str) -> Optional[str]:
        """
        根据任务描述匹配Subagent
        
        Args:
            description: 任务描述
            
        Returns:
            匹配的Subagent名称，如果没有匹配返回None
        """
        desc_lower = description.lower()
        
        # 检查能力索引
        matched: Set[str] = set()
        for capability, names in self._capability_index.items():
            if capability in desc_lower:
                matched.update(names)

        if matched:
            # 返回第一个匹配的
            return list(matched)[0]
        
        return None
    
    def match_by_file(self, filename: str) -> Optional[str]:
        """
        根据文件名匹配Subagent
        
        Args:
            filename: 文件名
            
        Returns:
            匹配的Subagent名称，如果没有匹配返回None
        """
        import fnmatch
        
        filename_lower = filename.lower()
        for pattern, name in self._file_pattern_index.items():
            if fnmatch.fnmatch(filename_lower, pattern):
                return name
        
        # 检查每个配置的文件模式
        for name, config in self._configs.items():
            if config.matches_file(filename):
                return name
        
        return None
    
    def get_descriptions(self) -> str:
        """
        获取所有Subagent的描述
        
        用于LLM系统提示中展示可用Subagent。
        
        Returns:
            描述字符串
        """
        if not self._configs:
            return "(no subagents available)"
        
        lines = []
        for name, config in self._configs.items():
            capabilities = ", ".join(config.capabilities) if config.capabilities else "general"
            lines.append(f"- {name}: {config.description} (capabilities: {capabilities})")
        
        return "\n".join(lines)
    
    def get_delegation_tool_definition(self, available_subagents: Optional[List[str]] = None) -> Dict:
        """
        获取委派工具定义
        
        用于LLM function calling。
        
        Args:
            available_subagents: 可用的subagent列表（用于限制可委派范围）
            
        Returns:
            工具定义字典
        """
        # 过滤可用的subagent
        if available_subagents:
            subagent_list = [
                (name, self._configs[name]) 
                for name in available_subagents 
                if name in self._configs
            ]
        else:
            subagent_list = list(self._configs.items())
        
        if not subagent_list:
            return None
        
        descriptions = "\n".join(
            f"  - {name}: {config.description}"
            for name, config in subagent_list
        )
        
        return {
            "name": "delegate_to_subagent",
            "description": f"""将任务委托给专业的子智能体执行。

可用的子智能体:
{descriptions}

何时使用:
- 当任务需要专业领域的知识或能力时
- 当任务匹配某个子智能体的能力描述时
- 当你需要专门的工具或技能来完成任务时

子智能体将在后台执行任务并返回结果。""",
            "input_schema": {
                "type": "object",
                "properties": {
                    "subagent_name": {
                        "type": "string",
                        "description": "要委派给的子智能体名称",
                        "enum": [name for name, _ in subagent_list]
                    },
                    "task_description": {
                        "type": "string",
                        "description": "详细描述要执行的任务"
                    },
                    "context_needed": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要传递的上下文关键词（可选）"
                    }
                },
                "required": ["subagent_name", "task_description"]
            }
        }
    
    def reload(self) -> int:
        """
        重新加载所有Subagent配置
        
        Returns:
            加载的配置数量
        """
        if self._loader:
            self._loader.reload()
            self._configs = self._loader.configs
            self._build_indices()
            logger.info(f"SubagentRegistry reloaded {len(self._configs)} subagents")
        return len(self._configs)
    
    def __len__(self) -> int:
        return len(self._configs)
    
    def __contains__(self, name: str) -> bool:
        return name in self._configs
    
    def __iter__(self):
        return iter(self._configs.items())


# 全局Subagent注册表实例
subagent_registry = SubagentRegistry()
