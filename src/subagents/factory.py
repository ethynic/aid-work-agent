#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent Factory - 智能体工厂

统一创建主智能体和子智能体实例。

使用示例:
    factory = AgentFactory(registry)

    # 创建独立主智能体
    agent = factory.create_standalone_agent("hr-expert", memory)

    # 创建子智能体独立模式（入口级绑定）
    agent = AgentFactory.create_standalone_subagent("trade-specialist", session_id)

    # 创建子智能体（被委派模式）
    subagent = factory.create_subagent("code-reviewer", memory, session_id, execution_id)
"""

from typing import Optional, TYPE_CHECKING

from loguru import logger

from src.models.subagent import SubagentConfig, extract_llm_config

if TYPE_CHECKING:
    from src.memory.short_term import ShortTermMemory
    from src.subagents.registry import SubagentRegistry
    from src.core.agent import Agent


class AgentFactory:
    """
    智能体工厂

    统一创建独立主智能体和委托子智能体实例。

    所有智能体都是 Agent 类的实例，通过参数区分主/子模式。

    使用示例:
        from src.subagents import SubagentRegistry, AgentFactory
        from src.memory.short_term import ShortTermMemory

        # 加载注册表
        registry = SubagentRegistry()
        registry.load_from_directory(Path("subagents"))

        # 创建工厂
        factory = AgentFactory(registry)

        # 创建HR智能体（独立主智能体模式）
        memory = ShortTermMemory()
        hr_agent = factory.create_standalone_agent("hr-expert", memory)

        # 运行
        await hr_agent.process_message(...)
    """

    def __init__(self, registry: 'SubagentRegistry'):
        """
        初始化智能体工厂

        Args:
            registry: Subagent注册表
        """
        self.registry = registry
    
    def create_standalone_agent(
        self,
        agent_name: str,
        session_memory: 'ShortTermMemory',
        session_id: Optional[str] = None,
    ) -> 'Agent':
        """
        创建独立主智能体
        
        独立主智能体可以：
        1. 直接处理用户消息
        2. 委派任务给配置的子智能体
        
        Args:
            agent_name: 智能体名称
            session_memory: Session记忆实例
            session_id: Session ID（可选，自动生成）
            
        Returns:
            Agent实例（is_master=True）
            
        Raises:
            ValueError: 智能体不存在
        """
        from src.core.agent import Agent
        import uuid
        
        config = self.registry.get(agent_name)
        if not config:
            raise ValueError(f"Agent not found: {agent_name}")
        
        if session_id is None:
            session_id = f"standalone_{agent_name}_{uuid.uuid4().hex[:8]}"
        
        # 创建主智能体实例
        agent = Agent(
            is_master=True,
            session_id=session_id,
        )
        
        # 共享memory
        agent.memory = session_memory
        
        logger.info(f"Created standalone master agent: {agent_name} (session: {session_id})")
        return agent
    
    def create_subagent(
        self,
        agent_name: str,
        session_memory: 'ShortTermMemory',
        session_id: str,
        execution_id: str,
        parent_plan_manager=None,
    ) -> 'Agent':
        """
        创建委托子智能体
        
        子智能体用于：
        1. 执行主智能体委托的任务
        2. 共享session memory与主智能体通信
        3. 将执行记录同步到主智能体的计划管理器
        
        Args:
            agent_name: 智能体名称
            session_memory: 共享的Session记忆
            session_id: Session ID（与主智能体共享）
            execution_id: 执行ID
            parent_plan_manager: 主智能体的计划管理器
            
        Returns:
            Agent实例（is_master=False）
            
        Raises:
            ValueError: 智能体不存在
        """
        from src.core.agent import Agent

        config = AgentFactory.get_runtime_config(self.registry, agent_name)
        if not config:
            raise ValueError(f"Agent not found: {agent_name}")

        # 创建子智能体实例
        agent = Agent(
            is_master=False,
            subagent_config=config,
            session_id=session_id,
            execution_id=execution_id,
            parent_plan_manager=parent_plan_manager,
        )
        
        # 共享memory
        agent.memory = session_memory
        
        logger.info(f"Created subagent: {agent_name} (execution: {execution_id})")
        return agent
    
    @staticmethod
    def create_standalone_subagent(
        name: str,
        session_id: str,
        tenant_id: Optional[str] = None,
    ) -> Optional['Agent']:
        """
        创建子智能体独立模式（入口级绑定，直接作为主智能体处理请求）

        查找顺序：registry 缓存 → DB 按需加载 → 返回 None

        Args:
            name: 子智能体名称（name 或 dir_name/agent_id）
            session_id: 会话ID
            tenant_id: 租户ID（用于加载租户定制 extra.md）

        Returns:
            Agent 实例，如果子智能体不存在返回 None
        """
        from src.core.agent import Agent, AgentMode, master_agent

        registry = master_agent.subagent_registry if master_agent.subagent_registry else None
        if not registry:
            return None

        # 自定义智能体实时读库，确保跨 worker 配置一致
        config = AgentFactory.get_runtime_config(registry, name)

        if not config:
            return None

        return Agent(
            is_master=True,
            mode=AgentMode.STANDALONE,
            subagent_config=config,
            session_id=session_id,
            tenant_id=tenant_id,
        )

    @staticmethod
    def get_runtime_config(registry, name: str) -> Optional[SubagentConfig]:
        """
        执行期获取最新配置（跨 worker 一致的方案 A）。

        自定义智能体（from_db=True，配置存于 subagent_definitions 表）每次强制从数据库
        实时重读，避免 worker 进程内 registry._configs 缓存旧配置导致修改模型/工具等不生效；
        内置（文件系统 SUBAGENT.md）智能体走内存缓存，零影响。

        注意：仅限「真正执行智能体」的入口调用（委托、standalone 对话创建），
        列表展示/描述等非执行场景继续用 registry.get() 的内存缓存。
        """
        config = registry.get(name)
        if config is None:
            # 内存没有（如创建后未刷新 registry），按 agent_id 从 DB 按需加载
            return AgentFactory._load_single_from_db(registry, name)
        if getattr(config, "from_db", False):
            # 自定义智能体：实时重读 DB 定义；DB 行已删除时回退内存旧配置，避免中断进行中的委托
            return AgentFactory._load_single_from_db(registry, config.dir_name) or config
        return config

    @staticmethod
    def _load_single_from_db(registry, agent_id: str) -> Optional[SubagentConfig]:
        """从数据库按需加载单个子智能体定义并注册到 registry"""
        from src.db.subagent_definition_db import SubagentDefinitionDB
        from src.prompts.prompt_resolver import prompt_resolver

        row = SubagentDefinitionDB.get_by_agent_id(agent_id)
        if not row:
            logger.debug(f"DB 中无子智能体定义: {agent_id}")
            return None

        prompt_content = prompt_resolver.resolve(
            scope="subagent", scope_id=agent_id
        )
        if not prompt_content:
            logger.warning(f"DB 中有子智能体定义 {agent_id} 但无 system_prompt")
            return None

        _provider, _model_codes = extract_llm_config(row.get("llm_provider"))
        config = SubagentConfig(
            name=row["name"],
            dir_name=agent_id,
            description=row.get("description", ""),
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
            from_db=True,
        )

        registry._configs[config.name] = config
        registry._build_indices()
        logger.info(f"按需从 DB 加载子智能体: {config.name} (agent_id={agent_id})")
        return config

    def list_available_agents(self) -> list:
        """
        列出所有可用的智能体
        
        Returns:
            智能体名称列表
        """
        return self.registry.list_subagents()
    
    def get_agent_info(self, agent_name: str) -> Optional[dict]:
        """
        获取智能体信息
        
        Args:
            agent_name: 智能体名称
            
        Returns:
            智能体信息字典
        """
        config = self.registry.get(agent_name)
        if not config:
            return None
        
        return {
            "name": config.name,
            "description": config.description,
            "delegatable_to": config.delegatable_to,
            "allow_delegation": config.allow_delegation,
        }
    
    def create_delegation_tool(
        self,
        available_agents: Optional[list] = None
    ) -> Optional[dict]:
        """
        创建委派工具定义
        
        用于注入到主智能体的工具列表中。
        
        Args:
            available_agents: 可委派的智能体列表（可选，默认使用配置的）
            
        Returns:
            工具定义字典，如果没有可委派的智能体返回None
        """
        return self.registry.get_delegation_tool_definition(available_agents)


# 便捷函数
def create_agent(
    agent_name: str,
    subagents_dir: str = "subagents",
    **kwargs
) -> 'Agent':
    """
    便捷函数：创建独立智能体
    
    Args:
        agent_name: 智能体名称
        subagents_dir: Subagent配置目录
        **kwargs: 其他参数传递给create_standalone_agent
        
    Returns:
        Agent实例（主智能体模式）
    """
    from pathlib import Path
    from src.subagents.registry import SubagentRegistry
    from src.memory.short_term import ShortTermMemory
    
    registry = SubagentRegistry()
    registry.load_from_directory(Path(subagents_dir))
    
    factory = AgentFactory(registry)
    memory = kwargs.pop('session_memory', ShortTermMemory())
    
    return factory.create_standalone_agent(agent_name, memory, **kwargs)
