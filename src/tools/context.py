"""请求级工具执行上下文。

上下文只在可信执行边界构造，并由 :class:`ToolExecutor` 在单次调用期间安装。
工具实例不得保存这些请求态字段。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, Iterator, Mapping, Optional

from src.core.request_context import freeze_request_mapping


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
    request_data: Mapping[str, Any] = field(default_factory=dict)
    # 租户级子智能体环境变量（subagent_env_vars 表），替代旧的进程级 os.environ 注入，
    # 供 http_api ${VAR} 替换、skill 子进程继承等消费；随请求隔离，避免并发消息互相污染
    env_vars: Mapping[str, str] = field(default_factory=dict)
    # 调用方智能体的 LLM 网关实例，供工具内部 LLM 调用跟随外层智能体模型配置
    #（如数据分析 AnalysisAgent）；无上下文的调用方（后台调度/渠道侧）为 None，由工具自行兜底
    llm_gateway: Optional[Any] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_data", freeze_request_mapping(self.request_data))
        object.__setattr__(self, "env_vars", freeze_request_mapping(self.env_vars))

    def derive(self, **changes) -> "ToolExecutionContext":
        """显式派生嵌套调用上下文，未指定字段保持不变。"""
        return replace(self, **changes)


_CURRENT_TOOL_CONTEXT: ContextVar[Optional[ToolExecutionContext]] = ContextVar(
    "current_tool_execution_context", default=None
)


def current_tool_execution_context() -> Optional[ToolExecutionContext]:
    return _CURRENT_TOOL_CONTEXT.get()


def resolve_llm_gateway(fallback: Optional[Any] = None) -> Optional[Any]:
    """返回工具内 LLM 调用应使用的 gateway。

    优先取工具执行上下文中的调用方智能体 gateway（含子智能体 model_code
    覆盖，且与 SessionRecordService 的 record.model 同源，保证计费单价与
    实际消耗模型一致）；无上下文（后台调度/渠道侧/测试）时返回 fallback，
    由调用方自行兜底到全局配置实例。
    """
    ctx = current_tool_execution_context()
    if ctx is not None and ctx.llm_gateway is not None:
        return ctx.llm_gateway
    return fallback


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
        tool_call_id=None, request_data=None, env_vars=None, llm_gateway=None,
    ) -> ToolExecutionContext:
        if tenant_id is None or user_id is None:
            try:
                from src.saas.context import get_current_tenant_id, get_current_user_id
                tenant_id = tenant_id or get_current_tenant_id()
                user_id = user_id or get_current_user_id()
            except Exception:
                pass
        parent = current_tool_execution_context()
        if request_data is None:
            request_data = parent.request_data if parent else {}
        if env_vars is None:
            env_vars = parent.env_vars if parent else {}
        if llm_gateway is None:
            llm_gateway = parent.llm_gateway if parent else None
        return ToolExecutionContext(
            tenant_id=tenant_id, user_id=user_id, session_id=session_id,
            channel=channel, subagent_id=subagent_id,
            chat_record_id=chat_record_id, agent_execution_id=agent_execution_id,
            tool_call_id=tool_call_id,
            request_data=request_data,
            env_vars=env_vars,
            llm_gateway=llm_gateway,
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
