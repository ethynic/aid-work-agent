"""Core engine module

⚠️ 不要在本 __init__.py 顶层 import master_agent。Agent 构造时会注册全部
工具、加载全部 skills 和 subagents（开销很大），而任何对 src.core.* 的
import 都会触发本 __init__.py 执行。曾经在顶层写 `from .agent import
master_agent`，导致 `from src.config.settings import settings` 这种最轻的
import 都会把整套 Agent 环境拉起来。

现在改为按需构造：第一次访问 master_agent / agent 时才实例化。
"""

from typing import Any


def __getattr__(name: str) -> Any:
    """按需导出，避免顶层 import 触发 Agent 构造"""
    if name in ("Agent", "AgentMode"):
        from src.core.agent import Agent, AgentMode
        return {"Agent": Agent, "AgentMode": AgentMode}[name]
    if name in ("master_agent", "agent", "MasterAgent"):
        from src.core.agent import get_master_agent
        ma = get_master_agent()
        if name == "MasterAgent":
            # 向后兼容：MasterAgent 是 Agent 类的别名
            from src.core.agent import Agent
            return Agent
        return ma
    raise AttributeError(f"module 'src.core' has no attribute {name!r}")


def __dir__():
    return ["Agent", "AgentMode", "MasterAgent", "master_agent", "agent"]


__all__ = ["Agent", "AgentMode", "MasterAgent", "master_agent", "agent"]
