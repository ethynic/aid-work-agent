"""请求级工具执行上下文。

上下文只在可信执行边界构造，并由 :class:`ToolExecutor` 在单次调用期间安装。
工具实例不得保存这些请求态字段。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Iterator, Optional


@dataclass(frozen=True)
class ToolExecutionContext:
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    channel: Optional[str] = None
    subagent_id: Optional[str] = None
    chat_record_id: Optional[int] = None
    agent_execution_id: Optional[str] = None
    tool_call_id: Optional[str] = None

    def derive(self, **changes) -> "ToolExecutionContext":
        """显式派生嵌套调用上下文，未指定字段保持不变。"""
        return replace(self, **changes)


_CURRENT_TOOL_CONTEXT: ContextVar[Optional[ToolExecutionContext]] = ContextVar(
    "current_tool_execution_context", default=None
)


def current_tool_execution_context() -> Optional[ToolExecutionContext]:
    return _CURRENT_TOOL_CONTEXT.get()


@contextmanager
def tool_execution_scope(context: Optional[ToolExecutionContext]) -> Iterator[None]:
    """安装上下文并用 token 精确恢复外层 scope。"""
    token = _CURRENT_TOOL_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_TOOL_CONTEXT.reset(token)


class ExecutionContextFactory:
    """可信边界使用的唯一上下文构造器。"""

    @staticmethod
    def for_agent_call(
        *, tenant_id=None, user_id=None, session_id=None, channel=None,
        subagent_id=None, chat_record_id=None, agent_execution_id=None,
        tool_call_id=None,
    ) -> ToolExecutionContext:
        if tenant_id is None or user_id is None:
            try:
                from src.saas.context import get_current_tenant_id, get_current_user_id
                tenant_id = tenant_id or get_current_tenant_id()
                user_id = user_id or get_current_user_id()
            except Exception:
                pass
        return ToolExecutionContext(
            tenant_id=tenant_id, user_id=user_id, session_id=session_id,
            channel=channel, subagent_id=subagent_id,
            chat_record_id=chat_record_id, agent_execution_id=agent_execution_id,
            tool_call_id=tool_call_id,
        )

    @staticmethod
    def for_remote_gateway(
        *, authenticated_tenant_id, authenticated_user_id, correlation
    ) -> ToolExecutionContext:
        return ToolExecutionContext(
            tenant_id=authenticated_tenant_id,
            user_id=authenticated_user_id,
            session_id=correlation.get("session_ref") or correlation.get("task_id"),
            agent_execution_id=correlation.get("execution_id"),
            tool_call_id=correlation.get("invocation_id"),
            channel="desktop_remote_gateway",
        )
