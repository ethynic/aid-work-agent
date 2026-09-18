"""
追踪数据收集器 — 在 SSE handler 的 async for 循环中旁路收集追踪数据。

不修改 agent.py，通过事件流的 type 字段识别并记录每个步骤。
所有 input/output 数据完整保存，不截断。
"""

import time
import uuid
import json
from typing import Optional, Dict, List
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class SpanRecord:
    """一个工具调用的追踪记录"""
    span_id: str
    name: str
    start_time: float
    span_type: str = 'span'          # span / generation / context_compressed
    tool_args: Optional[str] = None
    end_time: Optional[float] = None
    result: Optional[str] = None
    success: bool = True
    duration_ms: int = 0
    # LLM 调用专用字段（仅 generation span 使用）
    model: Optional[str] = None
    provider: Optional[str] = None
    usage: Optional[Dict] = None
    request_id: Optional[str] = None
    # 上下文压缩专用字段（仅 context_compressed span 使用，Phase 7 §7.1）
    # 前端根据 span_type='context_compressed' 渲染为紫色独立 span
    compression_info: Optional[Dict] = None


@dataclass
class TraceRecord:
    """一次完整请求的追踪记录"""
    trace_id: str
    session_id: str
    tenant_id: str
    user_id: str
    subagent_id: Optional[str]
    input: str
    source_type: str = 'chat'
    start_time: float = 0.0
    status: str = 'running'
    output: Optional[str] = None
    error_message: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    spans: List[SpanRecord] = field(default_factory=list)
    total_tokens: int = 0
    # 真实积分成本：初始 0，由 session_record.save() 算出 credit_cost 后回填
    # （内存 + DB 双写，覆盖 trace_persist worker 先后写入两种时序）。
    # 仅作观测统计口径，不是计费权威（最终金额以 billing / chat_records 链路为准）。
    total_cost: float = 0
    model: Optional[str] = None
    provider: Optional[str] = None
    agent_iterations: int = 0
    duration_ms: int = 0
    # 关联 channel_messages.message_id，用于 monitor.py 精确匹配撤回状态。
    # process_and_persist 在写入 channel_messages 后回填（覆盖 worker 未处理 + 已处理两种时序）。
    # 历史该字段为 NULL，monitor.py 不显示撤回标记（用户已确认接受降级）。
    user_message_id: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.start_time:
            self.start_time = time.time()


class TraceCollector:
    """
    追踪数据收集器 — 在 SSE handler 的 async for 循环中旁路调用。

    不修改 agent.py，通过事件流的 type 字段识别并记录每个步骤。
    所有 input/output 数据完整保存，不截断。
    """

    def __init__(self, session_id: str, tenant_id: str, user_id: str,
                 input_msg: str, source_type: str = 'chat',
                 subagent_id: str = None):
        self.trace = TraceRecord(
            trace_id=f"tr_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            subagent_id=subagent_id,
            input=input_msg,
            source_type=source_type,
        )
        self._active_spans: Dict[str, SpanRecord] = {}
        self._tool_name_counter: Dict[str, int] = {}
        self._last_llm_span: Optional[SpanRecord] = None  # 只保留最后一次 LLM 调用

    @property
    def trace_id(self) -> str:
        """当前 trace 的唯一 ID，用于回填 user_message_id 时定位记录"""
        return self.trace.trace_id

    def set_user_message_id(self, mid: Optional[str]):
        """
        回填 user_message_id（关联 channel_messages.message_id）。

        process_and_persist 在 add_messages_batch_transactional 拿到 created_ids[0]
        后调用，覆盖 trace_persist worker 尚未处理的场景。worker 已处理的情况
        由 trace_persist.update_user_message_id UPDATE 数据库补救。

        失败不抛异常（trace_collector 的所有操作都不应影响业务）。
        """
        try:
            self.trace.user_message_id = mid
        except Exception as e:
            logger.debug(f"set_user_message_id failed (trace_id={self.trace.trace_id}): {e}")

    def set_merge_semantics(self, *, termination_reason=None, merge_role=None):
        """记录可选消息合并语义；失败不得影响业务。"""
        try:
            if termination_reason:
                self.trace.metadata["termination_reason"] = termination_reason
            if merge_role:
                self.trace.metadata["merge_role"] = merge_role
        except Exception as e:
            logger.debug(f"set_merge_semantics failed (trace_id={self.trace.trace_id}): {e}")

    def on_event(self, event: dict):
        """处理从 agent.process_message() yield 出来的每个事件"""
        event_type = event.get("type")

        if event_type == "tool_start":
            self._handle_tool_start(event)
        elif event_type == "tool_result":
            self._handle_tool_result(event)
        elif event_type == "llm_call":
            self._handle_llm_call(event)
        elif event_type == "context_compressed":
            # Phase 7 §7.1：压缩完成事件 → 紫色独立 span
            self._handle_context_compressed(event)
        elif event_type == "response":
            data = event.get("data", "")
            if self.trace.output:
                self.trace.output += data
            else:
                self.trace.output = data
        elif event_type == "clarification":
            self.trace.tags.append("clarification")
        elif event_type == "cancelled":
            self.trace.status = "cancelled"
            self.trace.tags.append("cancelled")
            try:
                self.trace.metadata["termination_reason"] = "user_cancelled"
            except Exception:
                pass

    def on_error(self, error: str):
        """Agent 执行出错时调用"""
        self.trace.status = "failed"
        self.trace.error_message = error
        self.trace.tags.append("error")

    def on_complete(self, record_service=None):
        """
        请求完成时调用。从 SessionRecordService 获取 LLM 调用数据，
        然后将完整 trace + spans 异步持久化。
        """
        self.trace.duration_ms = int((time.time() - self.trace.start_time) * 1000)
        if self.trace.status == "running":
            self.trace.status = "completed"

        if record_service:
            self.trace.total_tokens = record_service.total_token_count
            self.trace.model = record_service.model
            self.trace.provider = record_service.provider
            self.trace.agent_iterations = record_service.agent_iterations

        # 关闭所有未完成的 span
        now = time.time()
        for span in self.trace.spans:
            if span.end_time is None:
                span.end_time = now
                span.duration_ms = int((now - span.start_time) * 1000)

        # 将最后一次 LLM 调用 span 加入 spans 列表（覆盖策略，只保留最后一次）
        if self._last_llm_span:
            self._last_llm_span.end_time = self._last_llm_span.start_time + self._last_llm_span.duration_ms / 1000.0
            self.trace.spans.append(self._last_llm_span)

        # 异步持久化
        try:
            from src.core.trace_persist import schedule_persist
            schedule_persist(self.trace)
        except Exception as e:
            logger.warning(f"Trace persist scheduling failed: {e}")

    def _handle_tool_start(self, event: dict):
        tool_name = event.get("toolName", "unknown")
        tool_args = event.get("toolArgs", {})

        count = self._tool_name_counter.get(tool_name, 0) + 1
        self._tool_name_counter[tool_name] = count

        span_id = f"sp_{uuid.uuid4().hex[:16]}"
        span = SpanRecord(
            span_id=span_id,
            name=f"tool:{tool_name}",
            start_time=time.time(),
            tool_args=json.dumps(tool_args, ensure_ascii=False),
        )
        self._active_spans[f"{tool_name}_{count}"] = span
        self.trace.spans.append(span)

    def _handle_tool_result(self, event: dict):
        tool_name = event.get("toolName", "unknown")
        result = event.get("result", {})
        success = event.get("success", True)

        count = self._tool_name_counter.get(tool_name, 1)
        key = f"{tool_name}_{count}"
        span = self._active_spans.get(key)

        if span:
            span.end_time = time.time()
            span.duration_ms = int((span.end_time - span.start_time) * 1000)
            span.result = json.dumps(result, ensure_ascii=False)
            span.success = success
            if not success:
                self.trace.tags.append("tool_error")
            del self._active_spans[key]

    def _handle_llm_call(self, event: dict):
        """处理 LLM 调用事件 — 只保留最后一次（覆盖策略）"""
        messages = event.get("messages", [])
        tools = event.get("tools", [])
        system_prompt = event.get("system_prompt", "")
        response_content = event.get("response_content", "")
        usage = event.get("usage", {})

        # 构造完整的 LLM 输入（system_prompt + messages + tools）
        llm_input = {
            "system_prompt": system_prompt,
            "messages": messages,
        }
        if tools:
            llm_input["tools"] = tools

        span = SpanRecord(
            span_id=f"sp_{uuid.uuid4().hex[:16]}",
            name="llm_call",
            start_time=time.time(),
            span_type='generation',
            tool_args=json.dumps(llm_input, ensure_ascii=False),
            result=response_content,
            duration_ms=event.get("duration_ms", 0),
            success=True,
            model=event.get("model"),
            provider=event.get("provider"),
            usage=usage,
            request_id=event.get("request_id"),
        )

        # 覆盖：只保留最后一次 LLM 调用
        self._last_llm_span = span

    def _handle_context_compressed(self, event: dict):
        """处理上下文压缩完成事件（Phase 7 §7.1）。

        创建一个独立的 span，span_type='context_compressed'，前端识别此 type
        并渲染为紫色（区别于 LLM 蓝色 / Tool 绿色）。所有压缩元数据保存在
        compression_info 字段，trace 详情页展开后可看到。
        """
        now = time.time()
        duration_ms = int(event.get("duration_ms") or 0)
        # 起止时间反推：end=now，start=end-duration
        start_time = now - (duration_ms / 1000.0 if duration_ms else 0)
        info = {
            "summary_id": event.get("summary_id", ""),
            "compressed_message_count": event.get("compressed_message_count", 0),
            "original_token_count": event.get("original_token_count", 0),
            "compressed_token_count": event.get("compressed_token_count", 0),
            "compression_ratio": event.get("compression_ratio", 0.0),
            "fallback_used": bool(event.get("fallback_used", False)),
            "trigger_reason": event.get("trigger_reason", ""),
        }
        span = SpanRecord(
            span_id=f"sp_{uuid.uuid4().hex[:16]}",
            name="context_compressed",
            start_time=start_time,
            span_type='context_compressed',
            tool_args=json.dumps(
                {"trigger_reason": info["trigger_reason"]},
                ensure_ascii=False,
            ),
            result=json.dumps(info, ensure_ascii=False),
            duration_ms=duration_ms,
            success=True,
            model=event.get("llm_model"),
            provider=event.get("llm_provider"),
            compression_info=info,
        )
        self.trace.spans.append(span)
        self.trace.tags.append("context_compressed")
        if info["fallback_used"]:
            self.trace.tags.append("compression_fallback")
