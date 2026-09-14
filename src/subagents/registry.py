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

from src.models.subagent import SubagentConfig, extract_llm_config
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
    
    def __init__(self, subagents_dir: Optional[Path] = None):
        """
        初始化Subagent注册表

        Args:
            subagents_dir: 内置Subagent目录路径
        """
        # key = dir_name（agent_id），显示名仅用于展示、允许重名
        self._configs: Dict[str, SubagentConfig] = {}
        self._loader: Optional[SubagentLoader] = None

        # 文件模式索引，value 为 dir_name
        self._file_pattern_index: Dict[str, str] = {}

        # 内置 agent_id 集合（dir_name）
        self._builtin_names: Set[str] = set()

        # 被 DB 定义覆盖的文件系统（内置）配置，key 为 dir_name，
        # 用于 DB 定义删除后恢复内置版本
        self._overridden_builtins: Dict[str, SubagentConfig] = {}

        if subagents_dir:
            self.load_from_directory(subagents_dir)

    def is_builtin(self, agent_id: str) -> bool:
        """判断是否为内置子智能体（按 dir_name/agent_id）"""
        return agent_id in self._builtin_names

    def get_all_subagents_with_type(self) -> List[Dict]:
        """返回所有子智能体列表，带 type 字段。

        type 按配置来源判定：DB 定义（含覆盖内置的 DB 定义）为 custom，
        文件系统内置为 builtin。内置保护判定用 is_builtin(agent_id)。
        """
        result = []
        for agent_id, config in self._configs.items():
            item = {
                "agent_id": agent_id,
                "name": config.name,
                "description": config.description,
                "type": "custom" if getattr(config, "from_db", False) else "builtin",
            }
            if config.business_pages:
                item["business_pages"] = config.business_pages
            # 透传 Phase 1.5 声明式 UI 字段（chat_toolbar/upload_accept）
            if config.chat_toolbar:
                item["chat_toolbar"] = config.chat_toolbar
            if config.upload_accept:
                item["upload_accept"] = config.upload_accept
            result.append(item)
        return result

    def validate_id_uniqueness(self, agent_id: str, exclude_id: str = None) -> bool:
        """检查 agent_id 是否在所有子智能体中唯一（_configs 的 key 即 agent_id）"""
        if agent_id in self._configs and agent_id != exclude_id:
            return False
        return True

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

        # 记录内置 agent_id（key 即 dir_name）
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
    
    def restore_overridden_builtins(self) -> int:
        """还原被 DB 定义覆盖的文件系统（内置）配置，保证本轮从文件系统基线出发"""
        restored = 0
        for key, cfg in self._overridden_builtins.items():
            current = self._configs.get(key)
            if current is None or (
                getattr(current, "from_db", False) and current.dir_name == cfg.dir_name
            ):
                self._configs[key] = cfg
                restored += 1
        self._overridden_builtins.clear()
        return restored

    def upsert_db_config(self, config: SubagentConfig) -> None:
        """以 DB 定义覆盖同 agent_id 的既有条目（内置或旧 DB 版本）。

        registry._configs 以 dir_name（agent_id）为键，同 agent_id 直接覆盖；
        不同 agent_id 的定义显示名相同也互不影响（显示名允许重名）。
        """
        agent_id = config.dir_name or config.name
        existing_cfg = self._configs.get(agent_id)
        if existing_cfg is not None and not getattr(existing_cfg, "from_db", False):
            self._overridden_builtins[agent_id] = existing_cfg
            logger.info(
                f"DB 定义覆盖内置条目: {agent_id} ({existing_cfg.name} -> {config.name})"
            )
        # 显示名重复仅告警，不拒绝（显示名仅用于展示）
        for key, cfg in self._configs.items():
            if key != agent_id and cfg.name == config.name:
                logger.warning(
                    f"显示名重复: agent_id={agent_id} 与 agent_id={key} 均为 "
                    f"「{config.name}」，两条并存，展示层需以 agent_id 区分"
                )
                break
        self._configs[agent_id] = config

    def register(self, config: SubagentConfig) -> bool:
        """
        注册一个Subagent配置

        Args:
            config: 配置对象

        Returns:
            是否注册成功
        """
        key = config.dir_name or config.name
        if key in self._configs:
            logger.warning(f"Subagent already registered: {key}")
            return False

        self._configs[key] = config

        # 更新索引
        patterns = config.triggers.get("file_patterns", [])
        for pattern in patterns:
            self._file_pattern_index[pattern.lower()] = key

        logger.info(f"Registered subagent: {config.name} (agent_id={key})")
        return True

    def unregister(self, agent_id: str) -> bool:
        """
        注销一个Subagent配置

        Args:
            agent_id: dir_name（agent_id）

        Returns:
            是否注销成功
        """
        if agent_id not in self._configs:
            logger.warning(f"Subagent not found: {agent_id}")
            return False

        config = self._configs.pop(agent_id)

        # 更新索引
        patterns = config.triggers.get("file_patterns", [])
        for pattern in patterns:
            self._file_pattern_index.pop(pattern.lower(), None)

        logger.info(f"Unregistered subagent: {agent_id}")
        return True
    
    def get(self, agent_id: str) -> Optional[SubagentConfig]:
        """
        获取Subagent配置

        优先按 dir_name（agent_id，_configs 的 key）直达查找；
        未命中时按显示名（config.name）兜底扫描——兼容 LLM 历史会话
        传入显示名委派、Redis task_record 历史数据等场景。
        dir_name 与显示名撞名时 dir_name 优先。

        Args:
            agent_id: dir_name（agent_id）或显示名

        Returns:
            配置对象，不存在返回None
        """
        config = self._configs.get(agent_id)
        if config:
            return config
        # 按显示名兜底查找
        matches = [cfg for cfg in self._configs.values() if cfg.name == agent_id]
        if len(matches) > 1:
            logger.warning(
                f"显示名「{agent_id}」命中 {len(matches)} 个子智能体，"
                "返回插入序第一条；建议调用方改用 agent_id（dir_name）定位"
            )
        if matches:
            return matches[0]
        return None
    
    def get_content(self, name: str) -> Optional[str]:
        """
        获取Subagent完整内容

        Args:
            name: dir_name（agent_id）或显示名

        Returns:
            内容字符串
        """
        if self._loader:
            # loader 按 dir_name（key）查找，registry.get 提供显示名兜底
            content = self._loader.get_subagent_content(name)
            if content:
                return content
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
        列出所有Subagent的 dir_name（agent_id）

        Returns:
            dir_name 列表
        """
        return list(self._configs.keys())

    def match_by_file(self, filename: str) -> Optional[str]:
        """
        根据文件名匹配Subagent

        Args:
            filename: 文件名

        Returns:
            匹配的Subagent dir_name（agent_id），如果没有匹配返回None
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
            names: 可选的 dir_name（agent_id）列表，用于过滤。如果为 None，返回所有。

        Returns:
            描述字符串（展示显示名 + agent_id）
        """
        if not self._configs:
            return "(no subagents available)"

        filtered_configs = self._configs.items()
        if names is not None:
            filtered_configs = [(agent_id, config) for agent_id, config in filtered_configs if agent_id in names]

        lines = []
        for agent_id, config in filtered_configs:
            lines.append(f"- {config.name}（ID: {agent_id}）: {config.description}")

        return "\n".join(lines) if lines else "(no subagents available)"
    
    def get_delegation_tool_definition(
        self, available_subagents: Optional[List[str]] = None
    ) -> Optional[Dict]:
        """
        获取委派工具定义

        用于LLM function calling。

        Args:
            available_subagents: 可用的subagent dir_name（agent_id）列表（用于限制可委派范围）

        Returns:
            工具定义字典
        """
        # 过滤可用的subagent（按 dir_name/agent_id）
        if available_subagents is not None:
            subagent_list = [
                (agent_id, self._configs[agent_id])
                for agent_id in available_subagents
                if agent_id in self._configs
            ]
        else:
            subagent_list = list(self._configs.items())

        if not subagent_list:
            return None

        descriptions = "\n".join(
            f"  - {config.name}（ID: {agent_id}）: {config.description}"
            for agent_id, config in subagent_list
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
                        "description": "要委派给的子智能体 agent_id（见可用列表中的 ID）",
                        "enum": [agent_id for agent_id, _ in subagent_list]
                    },
                    "task_description": {
                        "type": "string",
                        "description": "详细描述要执行的任务"
                    },
                    "image_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "用户上传图片的完整路径列表，仅当任务含图片且子智能体支持视觉时传入"
                        ),
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
            self._overridden_builtins.clear()
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

        # 先还原上一轮被覆盖的内置配置（DB 定义被删除后内置版本可恢复）
        restored = self.restore_overridden_builtins()

        effective_agent_ids = set()
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

            _provider, _model_codes = extract_llm_config(row.get("llm_provider"))
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
                llm_provider=_provider,
                llm_model_codes=_model_codes,
                reply_style=row.get("reply_style"),
                business_pages=row.get("business_pages"),
                chat_toolbar=row.get("chat_toolbar") or [],
                upload_accept=row.get("upload_accept"),
                knowledge_sources=row.get("knowledge_sources") or [],
                recap=row.get("recap") or {},
                from_db=True,
            )

            self.upsert_db_config(config)
            effective_agent_ids.add(agent_id)
            loaded += 1
            logger.info(f"从 DB 加载子智能体: {config.name} (agent_id={agent_id})")

        removed = 0
        # 对账：移除本轮未生效的自定义定义（DB 中已删除、改名或缺失 system_prompt 的旧条目）
        for key in list(self._configs.keys()):
            cfg = self._configs[key]
            if getattr(cfg, "from_db", False) and cfg.dir_name not in effective_agent_ids:
                logger.info(f"移除未生效的自定义子智能体: {key} (agent_id={cfg.dir_name})")
                del self._configs[key]
                removed += 1

        if loaded > 0 or removed > 0 or restored > 0:
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
