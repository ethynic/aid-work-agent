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

        # 获取描述
        descriptions = registry.get_descriptions()
    """
    
    def __init__(self, subagents_dir: Optional[Path] = None, custom_dir: Optional[Path] = None):
        """
        初始化Subagent注册表
        
        Args:
            subagents_dir: 内置Subagent目录路径
            custom_dir: 定制Subagent目录路径
        """
        self._configs: Dict[str, SubagentConfig] = {}
        self._loader: Optional[SubagentLoader] = None
        self._custom_loader: Optional[SubagentLoader] = None

        # 文件模式索引
        self._file_pattern_index: Dict[str, str] = {}
        
        # 内置名称集合
        self._builtin_names: Set[str] = set()
        # 定制目录路径
        self._custom_dir: Optional[Path] = None
        
        if subagents_dir:
            self.load_from_directory(subagents_dir)
        if custom_dir:
            self._custom_dir = custom_dir
            self._load_custom(custom_dir)

    def _load_custom(self, custom_dir: Path) -> int:
        """
        从定制目录加载Subagent配置（全量刷新，支持删除场景）

        多 worker 部署时，每个 worker 有独立的内存状态，
        需要从磁盘重新加载以获取其他 worker 写入的变更。

        Returns:
            加载的配置数量
        """
        if not custom_dir.exists():
            custom_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created custom subagents directory: {custom_dir}")

        self._custom_loader = SubagentLoader(custom_dir)
        custom_configs = self._custom_loader.configs

        # 全量刷新：先移除旧的定制条目，再合并新的
        # 仅移除定制条目（不在 _builtin_names 中的），保留内置
        stale_names = [name for name in self._configs if name not in self._builtin_names]
        for name in stale_names:
            self._configs.pop(name, None)

        # 合并：定制不覆盖内置
        for name, config in custom_configs.items():
            if name not in self._configs:
                self._configs[name] = config

        self._build_indices()
        loaded_count = len(custom_configs)
        logger.info(f"SubagentRegistry loaded {loaded_count} custom subagents from {custom_dir}")
        return loaded_count

    def is_builtin(self, name: str) -> bool:
        """判断是否为内置子智能体"""
        return name in self._builtin_names

    def get_all_subagents_with_type(self) -> List[Dict]:
        """返回所有子智能体列表，带 type 字段"""
        result = []
        for name, config in self._configs.items():
            item = {
                "agent_id": config.dir_name or name,
                "name": config.name,
                "description": config.description,
                "type": "builtin" if name in self._builtin_names else "custom",
            }
            if config.business_pages:
                item["business_pages"] = config.business_pages
            result.append(item)
        return result

    def validate_id_uniqueness(self, agent_id: str, exclude_id: str = None) -> bool:
        """检查 agent_id 是否在所有子智能体中唯一"""
        for config in self._configs.values():
            dir_name = config.dir_name
            if dir_name == agent_id and dir_name != exclude_id:
                return False
        return True

    def validate_name_uniqueness(self, name: str, exclude_name: str = None) -> bool:
        """检查 name 是否在所有子智能体中唯一"""
        for cfg_name in self._configs.keys():
            if cfg_name == name and cfg_name != exclude_name:
                return False
        return True

    def save_custom_subagent(self, agent_id: str, config: SubagentConfig, content: str) -> SubagentConfig:
        """创建或更新定制子智能体"""
        if not self._custom_dir:
            raise ValueError("定制目录未配置")

        md_path = SubagentLoader.save_subagent_md(self._custom_dir, agent_id, content)

        # 重新解析以获取完整配置
        new_config = SubagentLoader(self._custom_dir).get(config.name)
        if new_config:
            new_config.dir_name = agent_id
            self._configs[config.name] = new_config
            self._build_indices()
            return new_config

        # 回退：直接用传入的配置
        config.path = str(md_path)
        config.dir_name = agent_id
        self._configs[config.name] = config
        self._build_indices()
        return config

    def delete_custom_subagent(self, agent_id: str) -> bool:
        """删除定制子智能体"""
        if not self._custom_dir:
            return False
        if self.is_builtin(agent_id):
            logger.warning(f"Cannot delete builtin subagent: {agent_id}")
            return False

        # 找到对应的 config name
        config = self.get(agent_id)
        if not config:
            return False

        success = SubagentLoader.delete_subagent_dir(self._custom_dir, agent_id)
        if success:
            self._configs.pop(config.name, None)
            self._build_indices()
        return success
    
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
        
        # 记录内置名称
        self._builtin_names = set(self._configs.keys())
        
        # 构建索引
        self._build_indices()
        
        logger.info(f"SubagentRegistry loaded {len(self._configs)} subagents from {subagents_dir}")
        return len(self._configs)
    
    def _build_indices(self):
        """构建加速查找的索引"""
        self._file_pattern_index.clear()

        for name, config in self._configs.items():
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
        patterns = config.triggers.get("file_patterns", [])
        for pattern in patterns:
            self._file_pattern_index.pop(pattern.lower(), None)
        
        logger.info(f"Unregistered subagent: {name}")
        return True
    
    def get(self, name: str) -> Optional[SubagentConfig]:
        """
        获取Subagent配置

        支持按 name（YAML 中的 name 字段）或 dir_name（目录名）查找。

        Args:
            name: Subagent名称或目录名

        Returns:
            配置对象，不存在返回None
        """
        config = self._configs.get(name)
        if config:
            return config
        # 按目录名查找
        for cfg in self._configs.values():
            if cfg.dir_name == name:
                return cfg
        return None
    
    def get_content(self, name: str) -> Optional[str]:
        """
        获取Subagent完整内容
        
        Args:
            name: Subagent名称或目录名
            
        Returns:
            内容字符串
        """
        if self._loader:
            # 先尝试按 name 查找，再按 dir_name 查找
            content = self._loader.get_subagent_content(name)
            if content:
                return content
            # loader 不支持 dir_name 查找，通过 registry.get 补偿
            config = self.get(name)
            if config and config.path:
                from pathlib import Path as P
                try:
                    return P(config.path).read_text(encoding='utf-8')
                except Exception as e:
                    logger.error(f"Failed to read subagent content by dir_name: {e}")
            return None
        
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
    
    def get_descriptions(self, names: Optional[List[str]] = None) -> str:
        """
        获取Subagent的描述

        用于LLM系统提示中展示可用Subagent。

        Args:
            names: 可选的子智能体名称列表，用于过滤。如果为 None，返回所有。

        Returns:
            描述字符串
        """
        if not self._configs:
            return "(no subagents available)"

        filtered_configs = self._configs.items()
        if names is not None:
            filtered_configs = [(name, config) for name, config in filtered_configs if name in names]

        lines = []
        for name, config in filtered_configs:
            lines.append(f"- {name}: {config.description}")

        return "\n".join(lines) if lines else "(no subagents available)"
    
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

    def load_from_db(self) -> int:
        """
        从数据库加载子智能体定义（DB 优先，文件系统兜底）。

        加载策略：DB 优先于文件系统。
        - DB 中有定义 + system_prompt → 使用 DB 版本，覆盖文件系统版本
        - DB 中无定义或无 system_prompt → 保留文件系统版本

        Returns:
            加载的配置数量
        """
        from src.db.subagent_definition_db import SubagentDefinitionDB
        from src.prompts.prompt_resolver import prompt_resolver

        definitions = SubagentDefinitionDB.list_active()
        loaded = 0
        for row in definitions:
            agent_id = row["agent_id"]

            # 整体判断：定义 + system_prompt 必须同时存在
            prompt_content = prompt_resolver.resolve(
                scope="subagent", scope_id=agent_id
            )
            if not prompt_content:
                logger.warning(
                    f"跳过 DB 加载 {agent_id}：有定义但无 system_prompt，"
                    "由文件系统兜底"
                )
                continue

            config = SubagentConfig(
                name=row["name"],
                dir_name=agent_id,
                description=row.get("description") or "",
                version=row.get("version", "1.0.0"),
                author=row.get("author") or "unknown",
                triggers=row.get("triggers", {}),
                tools=row.get("tools", {}),
                skills=row.get("skills", {}),
                context=row.get("context", {}),
                system_prompt=prompt_content,
                delegatable_to=row.get("delegatable_to", []),
                allow_delegation=row.get("allow_delegation", True),
                llm_provider=row.get("llm_provider"),
                reply_style=row.get("reply_style"),
                business_pages=row.get("business_pages"),
                knowledge_sources=row.get("knowledge_sources") or [],
                from_db=True,
            )

            existing = self.get(agent_id)
            if existing:
                logger.info(f"DB 定义覆盖文件系统版本: {config.name} (agent_id={agent_id})")

            self._configs[config.name] = config
            loaded += 1
            logger.info(f"从 DB 加载子智能体: {config.name} (agent_id={agent_id})")

        if loaded > 0:
            self._build_indices()

        logger.info(f"SubagentRegistry.load_from_db() 加载了 {loaded} 个子智能体")
        return loaded
    
    def __len__(self) -> int:
        return len(self._configs)
    
    def __contains__(self, name: str) -> bool:
        return name in self._configs
    
    def __iter__(self):
        return iter(self._configs.items())


# 全局Subagent注册表实例
subagent_registry = SubagentRegistry()
