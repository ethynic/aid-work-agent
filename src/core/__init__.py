"""Core engine module"""

from .agent import Agent, AgentMode, master_agent

# 向后兼容：MasterAgent 是 Agent 的别名
MasterAgent = Agent

__all__ = [
    "Agent",
    "AgentMode",
    "MasterAgent",  # 向后兼容
    "master_agent",
]
