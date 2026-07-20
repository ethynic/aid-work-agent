"""Agent Runtime 的一等工具挂起控制契约。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolSuspension:
    """工具暂停信号；不得作为普通 tool result 发送给模型。"""

    tenant_id: str
    user_id: str
    session_id: str
    agent_execution_id: str
    tool_call_id: str
    run_id: str
    assistance_id: str
    continuation_id: str
    event: dict
