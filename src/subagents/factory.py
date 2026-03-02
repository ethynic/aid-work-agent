#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent Factory - 智能体工厂

统一创建主智能体和子智能体实例。

使用示例:
    factory = AgentFactory(registry)
    
    # 创建独立主智能体
    agent = factory.create_standalone_agent("hr-expert", memory)
    
    # 创建子智能体
    subagent = factory.create_subagent("code-reviewer", memory, execution_ctx)
"""

from typing import Optional, TYPE_CHECKING

from loguru import logger

from src.models.subagent import SubagentConfig, SubagentExecutionContext
from src.subagents.instance import SubagentInstance

if TYPE_CHECKING:
    from src.memory.short_term import ShortTermMemory
    from src.subagents.registry import SubagentRegistry
    from src.llm.gateway import LLMGateway
    from src.tools.registry import ToolRegistry
    from src.core.skill_registry import SkillRegistry


class AgentFactory:
    """
    智能体工厂
    
    统一创建独立主智能体和委托子智能体实例。
    
    使用示例:
        from src.subagents import SubagentRegistry, AgentFactory
        from src.memory.short_term import ShortTermMemory
        
        # 加载注册表
        registry = SubagentRegistry()
        registry.load_from_directory(Path("subagents"))
        
        # 创建工厂
        factory = AgentFactory(registry)
        
        # 创建HR智能体（独立模式）
        memory = ShortTermMemory()
        hr_agent = factory.create_standalone_agent("hr-expert", memory)
        
        # 运行
        await hr_agent.run_standalone()
    """
    
    def __init__(
        self,
        registry: 'SubagentRegistry',
        llm: Optional['LLMGateway'] = None,
        tool_registry: Optional['ToolRegistry'] = None,
        skill_registry: Optional['SkillRegistry'] = None,
    ):
        """
        初始化智能体工厂
        
        Args:
            registry: Subagent注册表
            llm: LLM网关（可选，默认使用全局）
            tool_registry: 工具注册表（可选，用于继承）
            skill_registry: 技能注册表（可选，用于继承）
        """
        self.registry = registry
        self.llm = llm
        self.tool_registry = tool_registry
        self.skill_registry = skill_registry
    
    def create_standalone_agent(
        self,
        agent_name: str,
        session_memory: 'ShortTermMemory',
        session_id: Optional[str] = None,
    ) -> SubagentInstance:
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
            智能体实例
            
        Raises:
            ValueError: 智能体不存在
        """
        config = self.registry.get(agent_name)
        if not config:
            raise ValueError(f"Agent not found: {agent_name}")
        
        if session_id is None:
            import uuid
            session_id = f"standalone_{agent_name}_{uuid.uuid4().hex[:8]}"
        
        instance = SubagentInstance(
            config=config,
            session_memory=session_memory,
            session_id=session_id,
            execution_id=None,  # 独立模式
            llm=self.llm,
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
        )
        
        logger.info(f"Created standalone agent: {agent_name} (session: {session_id})")
        return instance
    
    def create_subagent(
        self,
        agent_name: str,
        session_memory: 'ShortTermMemory',
        session_id: str,
        execution_id: str,
        execution_context: Optional[SubagentExecutionContext] = None,
    ) -> SubagentInstance:
        """
        创建委托子智能体
        
        子智能体用于：
        1. 执行主智能体委托的任务
        2. 共享session memory与主智能体通信
        
        Args:
            agent_name: 智能体名称
            session_memory: 共享的Session记忆
            session_id: Session ID（与主智能体共享）
            execution_id: 执行ID
            execution_context: 执行上下文（可选）
            
        Returns:
            智能体实例
            
        Raises:
            ValueError: 智能体不存在
        """
        config = self.registry.get(agent_name)
        if not config:
            raise ValueError(f"Agent not found: {agent_name}")
        
        instance = SubagentInstance(
            config=config,
            session_memory=session_memory,
            session_id=session_id,
            execution_id=execution_id,
            llm=self.llm,
            tool_registry=self.tool_registry,
            skill_registry=self.skill_registry,
        )
        
        logger.info(f"Created subagent: {agent_name} (execution: {execution_id})")
        return instance
    
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
            "capabilities": config.capabilities,
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
) -> SubagentInstance:
    """
    便捷函数：创建独立智能体
    
    Args:
        agent_name: 智能体名称
        subagents_dir: Subagent配置目录
        **kwargs: 其他参数传递给create_standalone_agent
        
    Returns:
        智能体实例
    """
    from pathlib import Path
    from src.subagents.registry import SubagentRegistry
    from src.memory.short_term import ShortTermMemory
    
    registry = SubagentRegistry()
    registry.load_from_directory(Path(subagents_dir))
    
    factory = AgentFactory(registry)
    memory = kwargs.pop('session_memory', ShortTermMemory())
    
    return factory.create_standalone_agent(agent_name, memory, **kwargs)
