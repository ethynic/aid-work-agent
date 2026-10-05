"""Compatibility API. Actual execution is owned by AgentEngine and runtime adapters."""
from __future__ import annotations
import asyncio
from contextvars import ContextVar
from typing import Any, Optional, List, Dict, AsyncGenerator, Callable, Coroutine, Tuple
from loguru import logger
from src.core.agent_engine.contracts import AgentMode, Identity, Outcome
from src.core.agent_engine.context import reorder_history
from src.core.agent_engine.engine import is_tool_result_echo
from src.core.agent_events import _extract_image_refs_from_tool_result, _normalize_image_placement, _truncate_tool_content
from src.core.request_context import AgentRequestContext
from src.core.verbose_feedback import (VerboseFeedbackConfig, VerboseFeedbackObserver, VerboseFeedbackState,
    build_policy_verbose_event, default_feedback_config, iter_with_verbose_feedback, prepare_turn_feedback)
from src.models.user import User
from src.services.agent_runner.runtime.executor import RuntimeExecution, CompatibilityControl, CompatibilityObserver
from src.services.agent_runner.runtime.profile import RuntimeResources
from src.config.settings import settings

_last_images = ContextVar("legacy_agent_response_images", default=())

class AgentResponse(str):
    def __new__(cls, text, images=()):
        result = super().__new__(cls, text)
        result.images = tuple(dict(image) for image in images)
        return result

class Agent:
    def __init__(self, is_master=True, subagent_config=None, session_id=None, execution_id=None,
                 parent_plan_manager=None, mode=AgentMode.MASTER, tenant_id=None, user_id=None):
        self.mode = AgentMode.SUBAGENT if mode == AgentMode.MASTER and not is_master else mode
        self.is_master = self.mode == AgentMode.MASTER
        self.subagent_config, self.session_id, self.execution_id = subagent_config, session_id, execution_id
        self.parent_plan_manager = parent_plan_manager
        self._default_tenant_id, self._default_user_id = tenant_id, user_id
        self._prototype = None

    def _resources(self, identity=None):
        return RuntimeResources(is_master=self.is_master, subagent_config=self.subagent_config,
            session_id=identity.session_id if identity else self.session_id, execution_id=self.execution_id,
            parent_plan_manager=self.parent_plan_manager, mode=self.mode,
            tenant_id=identity.tenant_id if identity else self._default_tenant_id,
            user_id=identity.user_id if identity else self._default_user_id)

    def __getattr__(self, name):
        if name not in {"llm", "memory", "prompt_manager", "style_manager", "skill_registry",
                        "skill_executor", "plan_manager", "subagent_registry", "subagent_executor",
                        "tool_registry", "tool_executor", "_tool_controls", "_tool_bundle"}:
            raise AttributeError(name)
        prototype = self.__dict__.get("_prototype")
        if prototype is None:
            # Metadata/legacy tools may inspect an Agent; execution never reuses
            # its tenant skill registry, memory or other mutable resources.
            if "mode" not in self.__dict__:
                raise AttributeError(name)
            prototype = self._resources()
            self._prototype = prototype
        if name in prototype.__dict__:
            return prototype.__dict__[name]
        raise AttributeError(name)

    @property
    def _last_response_images(self):
        return list(_last_images.get())

    def _identity(self, session_id, user=None):
        from src.saas.context import get_current_tenant_id, get_current_user_id
        from src.services.session_record import SessionRecordManager
        from src.services.agent_runner.runtime.history_repository import HistoryRepository
        try:
            record = SessionRecordManager.get_current_record()
        except Exception:
            record = None
        tenant = getattr(user, "tenant_id", None) or get_current_tenant_id() or self.__dict__.get("_default_tenant_id")
        user_id = getattr(user, "user_id", None) or get_current_user_id() or self.__dict__.get("_default_user_id")
        source = getattr(record, "source_type", None) or "chat"
        kind = "channel" if source in {"wecom", "wecom_kf", "wecom_personal_rpa", "dingtalk", "feishu"} else "web"
        # Legacy background callers did not carry source; check registration,
        # never select history by whichever message table happens to have rows.
        if record is None:
            try:
                kind = HistoryRepository.legacy_session_kind(session_id, tenant)
            except Exception:
                kind = "web"
        if kind == "channel" and source == "chat":
            source = "channel"
        return Identity(tenant, user_id, session_id, source, kind)

    def _execution(self, identity, cancel_check=None, record=None, task_record=None):
        resources = self._resources(identity)
        # Explicit resource replacements remain usable by legacy embedders and
        # tests. Ordinary requests always receive freshly assembled resources.
        prototype = self.__dict__.get("_prototype")
        for name in ("llm", "tool_registry", "tool_executor"):
            if name in self.__dict__:
                setattr(resources, name, self.__dict__[name])
        from src.services.agent_runner.runtime.legacy import LegacyHistoryReader
        from src.services.agent_runner.runtime.history_repository import HistoryRepository
        execution = RuntimeExecution(resources, identity, control=CompatibilityControl(cancel_check),
            observer=CompatibilityObserver(record, task_record), history_reader=LegacyHistoryReader(identity),
            tolerate_history_failure=True,
            legacy_clarification_authorizer=lambda bound_identity: HistoryRepository(bound_identity).assert_authorized(require_user=True))
        return execution

    _reorder_messages_for_llm = staticmethod(reorder_history)
    _is_tool_result_echo = staticmethod(is_tool_result_echo)

    async def _process_message_impl(self, user_input, session_id, user=None, attachments=None,
            cancel_check=None, extra_system_prompt=None, request_context=None,
            _continuation_tool_result=None, _defer_tool_names=None, _deferred_tool_call_id=None,
            verbose_config=None, verbose_state=None, verbose_observer=None):
        identity = await asyncio.to_thread(self._identity, session_id, user)
        execution = await asyncio.to_thread(self._execution, identity, cancel_check)
        try:
            async for event in execution.run(user_input, user, attachments, request_context,
                extra_system_prompt=extra_system_prompt, continuation=_continuation_tool_result,
                defer_names=_defer_tool_names, deferred_call_id=_deferred_tool_call_id,
                verbose_config=verbose_config, verbose_state=verbose_state):
                yield event
        finally:
            if execution.state is not None:
                # Only old callers observe MemoryManager. New service state has
                # one authority and never feeds this compatibility mirror back.
                prototype = self.__dict__.get("_prototype")
                if prototype is not None:
                    prototype.memory.clear(session_id)
                    prototype.memory.load_history(session_id, execution.state.messages)

    async def execute_as_subagent(self, task_description, parent_session_id, task_record=None,
                                 progress_callback=None, image_paths=None):
        if self.mode != AgentMode.SUBAGENT:
            raise RuntimeError("execute_as_subagent() is only for subagent mode")
        identity = await asyncio.to_thread(self._identity, parent_session_id)
        execution = await asyncio.to_thread(self._execution, identity, task_record=task_record)
        events = []
        try:
            async for event in execution.run(task_description, task_record=task_record, image_paths=image_paths):
                events.append(event)
                if progress_callback:
                    await progress_callback(event)
            state = execution.state
            from src.core.execution_usage import execution_token_usage
            usage = execution_token_usage(state.checkpoint())
            if state.outcome == Outcome.WAITING:
                wait = state.waiting or {}
                if wait.get("kind") == "clarification":
                    question = wait.get("question", "")
                    return {"result": {"content": question, "status": "clarifying", **wait},
                            "summary": f"需要补充信息: {question}", "status": "clarifying", "token_usage": usage}
                return {"result": None, "summary": "等待操作", "status": "waiting", "waiting": wait, "token_usage": usage}
            if state.outcome != Outcome.COMPLETED:
                return {"result": None, "summary": "任务未完成", "error": state.error_code or state.outcome.value, "token_usage": usage}
            return {"result": {"content": state.output}, "summary": state.output[:500] or "Task completed",
                    "token_usage": usage, "events": events}
        except Exception:
            logger.opt(exception=True).error("子智能体执行失败")
            return {"result": None, "summary": "子智能体执行失败", "error": "Subagent execution failed"}

    async def process_message(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        extra_system_prompt: Optional[str] = None,
        _continuation_tool_result: Optional[Dict[str, Any]] = None,
        request_context: Optional[AgentRequestContext] = None,
        _defer_tool_names: Optional[set[str]] = None,
        _deferred_tool_call_id: Optional[str] = None,
        _record_service=None,
        verbose_config: Optional[VerboseFeedbackConfig] = None,
        verbose_state: Optional[VerboseFeedbackState] = None,
        verbose_observer: Optional[VerboseFeedbackObserver] = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Process a user message and yield AgentEvent dicts (trace-wrapped).

        Wrapper around `_process_message_impl` that attaches a TraceCollector
        when a SessionRecordService is available, so all channels (Web/wecom/
        wecom_kf/dingtalk/feishu) produce traces without any caller-side change.
        Trace failures are swallowed (debug log) and never affect business logic.

        See: docs/infrastructure/observability-channel-sessions-design.md

        Args:
            request_context: 可信入口构造的通用请求级扩展上下文。
            _record_service: 调用方显式传入的 SessionRecordService（随协程
                参数传递，共享 Agent 的并发请求不会互相覆盖）；缺省时回落
                读当前上下文（ContextVar）的 record。
            verbose_config/state/observer: 可选 verbose 反馈参数（Phase 1），默认 None。
        """
        trace_collector = None
        _record = None
        try:
            from src.services.session_record import SessionRecordManager
            # 显式 record 随协程参数传递，避免共享 Agent 的并发请求互相覆盖。
            _record = _record_service or SessionRecordManager.get_current_record()
            if _record:
                try:
                    from src.core.trace_collector import TraceCollector
                    # 优先用 user_input（语音合并 / 追加消息后的最终输入），
                    # _record.user_message 是渠道入口 start_record 时设置的单条原始消息，
                    # 在 wecom_kf 等渠道的语音合并场景下只有首句，会导致 trace.input 丢失追加内容。
                    trace_collector = TraceCollector(
                        session_id=_record.session_id or session_id,
                        tenant_id=_record.tenant_id or '',
                        user_id=_record.user_id or '',
                        input_msg=user_input or _record.user_message,
                        source_type=_record.source_type or 'chat',
                        subagent_id=getattr(self, '_subagent_id', None),
                    )
                    # 注入 trace_collector 引用，process_and_persist 写入
                    # channel_messages 后通过它回填 user_message_id（用于
                    # monitor.py 精确匹配撤回状态）
                    if _record:
                        _record.trace_collector = trace_collector
                except Exception as e:
                    logger.debug(f"Trace collector init skipped: {e}")
                    trace_collector = None
        except Exception as e:
            logger.debug(f"Trace context resolve skipped: {e}")

        try:
            async for event in self._process_message_impl(
                user_input=user_input,
                session_id=session_id,
                user=user,
                attachments=attachments,
                cancel_check=cancel_check,
                extra_system_prompt=extra_system_prompt,
                request_context=request_context,
                _continuation_tool_result=_continuation_tool_result,
                _defer_tool_names=_defer_tool_names,
                _deferred_tool_call_id=_deferred_tool_call_id,
                verbose_config=verbose_config,
                verbose_state=verbose_state,
                verbose_observer=verbose_observer,
            ):
                if trace_collector:
                    try:
                        trace_collector.on_event(event)
                    except Exception as e:
                        logger.debug(f"Trace on_event failed: {e}")
                yield event
        except asyncio.CancelledError:
            # 取消不被 except Exception 捕获（CancelledError 继承 BaseException），
            # 必须单独标记 trace 为 cancelled，否则 on_complete 会落成 completed
            if trace_collector:
                try:
                    from src.core.agent_events import make_event
                    trace_collector.on_event(make_event("cancelled"))
                except Exception as ce:
                    logger.debug(f"Trace cancel mark failed: {ce}")
            raise
        except Exception as e:
            if trace_collector:
                try:
                    trace_collector.on_error(str(e))
                except Exception as ce:
                    logger.debug(f"Trace on_error failed: {ce}")
            raise
        finally:
            if trace_collector:
                try:
                    trace_collector.on_complete(_record)
                except Exception as e:
                    logger.debug(f"Trace on_complete failed: {e}")
    async def continue_tool_call(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        result: Dict[str, Any],
        user: Optional[User] = None,
        defer_tool_names: Optional[set[str]] = None,
        deferred_tool_call_id: Optional[str] = None,
    ) -> AsyncGenerator[dict, None]:
        """从已持久化的 assistant(tool_calls) 接回一次工具结果并继续 LLM。"""
        process_kwargs = {
            "user_input": "",
            "session_id": session_id,
            "user": user,
            "_continuation_tool_result": {
                "tool_call_id": tool_call_id,
                "content": result,
            },
        }
        if defer_tool_names is not None:
            process_kwargs["_defer_tool_names"] = defer_tool_names
            process_kwargs["_deferred_tool_call_id"] = deferred_tool_call_id
        async for event in self.process_message(**process_kwargs):
            yield event
    def process_message_with_feedback(
        self,
        *,
        surface: str,
        config: Optional[VerboseFeedbackConfig] = None,
        state: Optional[VerboseFeedbackState] = None,
        observer: Optional[VerboseFeedbackObserver] = None,
        **kwargs,
    ):
        """显式 verbose 反馈包装入口（Web/渠道专用，设计 §6.3/§8.1）：内层获得
        policy 注入能力，外层 wrapper 做事件过滤/观测（2026-09-01 起无系统兜底）。
        config/state 缺省时取全局配置/新建；kwargs 原样透传 process_message 全部参数。
        """
        if surface not in ("web", "channel"):
            raise ValueError(f"surface 仅允许 web|channel，实际: {surface!r}")
        cfg = config if config is not None else default_feedback_config()
        turn_state = state if state is not None else VerboseFeedbackState()
        inner = self.process_message(
            verbose_config=cfg, verbose_state=turn_state, verbose_observer=observer, **kwargs)
        return iter_with_verbose_feedback(
            inner, surface=surface, config=cfg, state=turn_state, observer=observer)
    async def process_message_sync(
        self,
        user_input: str,
        session_id: str,
        user: Optional[User] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        record_service=None,
        progress_callback=None,
        cancel_check=None,
        extra_system_prompt: Optional[str] = None,
        request_context: Optional[AgentRequestContext] = None,
        feedback_state: Optional[VerboseFeedbackState] = None,
        verbose_config: Optional[VerboseFeedbackConfig] = None,
        verbose_observer: Optional[VerboseFeedbackObserver] = None,
    ) -> str:
        """Process message and return complete response

        Args:
            record_service: Optional SessionRecordService for token tracking.
                When provided (e.g. from channel routes running in asyncio),
                the agent accumulates token usage to this service instead of
                relying on thread-local SessionRecordManager.
            progress_callback: Optional async callback for progress events
                (tool_start, tool_result, etc.)
            cancel_check: Optional callable returning True to cancel processing.
                Used by channel message serialization to cancel stale requests.
            extra_system_prompt: 渠道级额外提示词（如 wecom_kf 的渠道能力约束），
                透传给 process_message → _build_system_prompt。
            request_context: 可信入口构造的通用请求级扩展上下文。
            feedback_state: 渠道 owner 生命周期的 verbose 状态（设计 §7），外部传入时
                必须原样复用（禁止内部另建）；verbose_config/observer 为可选配置/钩子。
        """
        # 请求级 record 隔离：显式 record_service 写入 ContextVar 而非共享
        # Agent 实例属性——并发请求共用同一 Agent 实例，实例属性会互相覆盖。
        # finally 用 token 恢复进入前值而非无条件清空：本方法可能在已持有
        # record 的请求协程内被嵌套 await，清空会把外层请求的 record 一并清掉。
        from src.services.session_record import SessionRecordManager
        record_token = (
            SessionRecordManager.set_current_record(record_service)
            if record_service is not None else None
        )
        # Phase 2 P2.3 CodeReview P0 修复：每次调用前清空，供渠道层读取
        # （process_message 内部的 collected_images 是局部变量，外部无法访问；
        #  通过 images 事件 + 实例属性桥接，让 channels/session.process_and_persist
        #  能拿到 ImageRef 列表写入 UnifiedResponse.content.images）
        response_images: List[Dict[str, Any]] = []
        # verbose 包装（Phase 1，设计 §6.3）：仅存在用户投递 callback 时启用，scheduler 等无用户表面不受影响。
        feedback_enabled, cfg, turn_state = prepare_turn_feedback(progress_callback, feedback_state, verbose_config)
        try:
            response_parts = []
            event_iter = self.process_message(
                user_input, session_id, user, attachments,
                cancel_check=cancel_check,
                extra_system_prompt=extra_system_prompt,
                request_context=request_context,
                _record_service=record_service,
                verbose_config=cfg if feedback_enabled else None,
                verbose_state=turn_state,
                verbose_observer=verbose_observer,
            )
            if feedback_enabled:
                event_iter = iter_with_verbose_feedback(
                    event_iter, surface="channel", config=cfg,
                    state=turn_state, observer=verbose_observer)
            async for event in event_iter:
                if event.get("type") == "response":
                    response_parts.append(event.get("data", ""))
                # Phase 2 P2.3 CodeReview P0 修复：累积 images 事件到实例属性
                # 供渠道层（process_and_persist）读取后写入 UnifiedResponse.content.images
                if event.get("type") == "images":
                    ev_images = event.get("images") or []
                    if isinstance(ev_images, list):
                        for img in ev_images:
                            if isinstance(img, dict) and img.get("file_id"):
                                # 按 file_id 去重
                                if not any(existing.get("file_id") == img["file_id"]
                                            for existing in response_images):
                                    response_images.append(img)
                # Forward events to external progress_callback (e.g. channel routes)
                if progress_callback:
                    if callable(progress_callback):
                        await progress_callback(event)
            _last_images.set(tuple(response_images))
            return AgentResponse("".join(response_parts), response_images)
        finally:
            if record_token is not None:
                SessionRecordManager.reset_current_record(record_token)

# master_agent 单例：延迟构造
# Agent 构造时会注册全部工具、加载 skills 和 subagents，开销很大；放在模块顶层
# 会让任何 import src.core.* 的代码被迫拉起整套环境。改为按需构造。
_master_agent_instance: Optional["Agent"] = None


def get_master_agent() -> "Agent":
    """返回 master_agent 单例，第一次调用时构造"""
    global _master_agent_instance
    if _master_agent_instance is None:
        _master_agent_instance = Agent(is_master=True)
    return _master_agent_instance


def __getattr__(name: str):
    """模块级 __getattr__：让 `from src.core.agent import master_agent` 仍能工作，
    但只有在真正访问时才构造单例。"""
    if name in ("master_agent", "agent"):
        return get_master_agent()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# 兼容直接属性访问（极少数场景：src.core.agent.master_agent）
# 注意：不能写 `master_agent = get_master_agent()`，会立刻触发构造。
