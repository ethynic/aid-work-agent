"""
Subagent模块

提供子智能体的注册、加载、执行和管理功能。

核心组件:
- SubagentLoader: 配置加载器
- SubagentRegistry: 注册表
- SubagentExecutor: 执行管理器
- AgentFactory: 智能体工厂

注意: 所有智能体（主/子）现在统一使用 Agent 类，不再需要单独的 SubagentInstance
"""

from .loader import SubagentLoader
from .registry import SubagentRegistry
from .protocol import SubagentTaskRecord
from .executor import SubagentExecutor
from .factory import AgentFactory

__all__ = [
    "SubagentLoader",
    # 注册
    "SubagentRegistry",
    # 协议
    "SubagentTaskRecord",
    # 执行
    "SubagentExecutor",
    # 工厂
    "AgentFactory",
]
