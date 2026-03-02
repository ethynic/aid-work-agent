"""
Subagent模块

提供子智能体的注册、加载、执行和管理功能。

核心组件:
- SubagentConfig: 子智能体配置
- SubagentLoader: 配置加载器
- SubagentRegistry: 注册表
- SubagentExecutor: 执行管理器
- SubagentInstance: 运行实例
- AgentFactory: 智能体工厂
"""

from .config import SubagentConfig
from .loader import SubagentLoader
from .registry import SubagentRegistry
from .protocol import SubagentTaskRecord
from .executor import SubagentExecutor
from .instance import SubagentInstance
from .factory import AgentFactory

__all__ = [
    # 配置
    "SubagentConfig",
    "SubagentLoader",
    # 注册
    "SubagentRegistry",
    # 协议
    "SubagentTaskRecord",
    # 执行
    "SubagentExecutor",
    "SubagentInstance",
    # 工厂
    "AgentFactory",
]
