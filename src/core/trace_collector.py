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
    span_type: str = 'span'          # span / generation
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
    model: Optional[str] = None
    provider: Optional[str] = None
    agent_iterations: int = 0
    duration_ms: int = 0

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

    def on_event(self, event: dict):
        """处理从 agent.process_message() yield 出来的每个事件"""
        event_type = event.get("type")

        if event_type == "tool_start":
            self._handle_tool_start(event)
        elif event_type == "tool_result":
            self._handle_tool_result(event)
        elif event_type == "llm_call":
            self._handle_llm_call(event)
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
