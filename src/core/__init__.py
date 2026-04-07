"""Core engine module"""

from .agent import Agent, AgentMode, master_agent
from .dialog_manager import DialogManager
from .intent_engine import IntentEngine, intent_engine
from .planner import Planner, planner
from .executor import ToolExecutor

# 向后兼容：MasterAgent 是 Agent 的别名
MasterAgent = Agent

__all__ = [
    "Agent",
    "AgentMode",
    "MasterAgent",  # 向后兼容
    "master_agent",
    "DialogManager",
    "IntentEngine",
    "intent_engine",
    "Planner",
    "planner",
    "ToolExecutor",
]
