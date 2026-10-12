"""engine 观测计时字段：llm_call 事件携带墙钟起止、失败事实挂异常、工具事件计时。

见 docs/plans/traceobs-timing-precision.md（A1/A2/A3）。engine 是执行内核，
本文件只验证观测字段，不改变控制流断言（控制流见 test_agent_engine_acceptance.py）。
"""
import asyncio

import pytest

from src.core.agent_engine import (
    AgentEngine, AgentMode, DispatchResult, ExecutionState, Outcome,
)
from src.core.agent_engine.contracts import Identity, ToolCall, ToolFact

pytestmark = pytest.mark.unit


def state():
    return ExecutionState(
        identity=Identity("tenant-a", "user-a", "session-a"),
        execution_id="execution-a", role=AgentMode.STANDALONE, system_prompt="Be helpful.",
        messages=[{"role": "user", "content": "request-a"}],
        initial_len=1, max_iterations=4,
    )


def completion(text="done", calls=None):
    return {"content": text, "tool_calls": calls or [],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


def call(call_id="call-1", name="read"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": "{}"}}


class Model:
    def __init__(self, *responses, delay=0.0):
        self.responses = list(responses)
        self.delay = delay

    async def complete(self, current, call_id, definitions):
        if self.delay:
            await asyncio.sleep(self.delay)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class Tools:
    def definitions(self):
        return []

    def display_name(self, item):
        return item.name

    async def dispatch(self, current, item):
        yield DispatchResult({"success": True})


class Control:
    def command(self):
        return None

    async def save(self, current, boundary):
        pass


class Observer:
    async def event(self, current, event):
        pass

    async def model_usage(self, current, fact):
        pass


async def run(current, model, tools=None):
    return [event async for event in AgentEngine().run(
        current, model, tools or Tools(), Control(), Observer())]


@pytest.mark.asyncio
async def test_model_success_event_carries_wall_clock_window_matching_duration():
    current = state()
    events = await run(current, Model(completion("done"), delay=0.03))

    assert current.outcome == Outcome.COMPLETED
    llm_events = [event for event in events if event["type"] == "llm_call"]
    assert len(llm_events) == 1
    event = llm_events[0]
    assert isinstance(event["start_ts"], float) and isinstance(event["end_ts"], float)
    assert event["end_ts"] >= event["start_ts"]
    window_ms = (event["end_ts"] - event["start_ts"]) * 1000
    # duration_ms 是 monotonic 计量，墙钟窗口应与其一致（容忍时钟粒度差异）
    assert window_ms >= 20
    assert event["duration_ms"] == pytest.approx(window_ms, abs=50)


@pytest.mark.asyncio
@pytest.mark.parametrize("authoritative", [False, True])
async def test_model_failure_attaches_model_call_fact_to_exception(authoritative):
    current = state()
    error = RuntimeError("provider down")
    if authoritative:
        error.authoritative_storage_failure = True

    with pytest.raises(RuntimeError, match="provider down") as exc_info:
        await run(current, Model(error))

    fact = getattr(exc_info.value, "model_call_fact", None)
    assert fact, "failed model call must carry model_call_fact for trace collector"
    assert fact["error_code"] == "RuntimeError"
    assert fact["execution_id"] == "execution-a"
    assert fact["call_id"].startswith("model_")
    assert fact["iteration"] == current.iteration
    assert fact["end_ts"] >= fact["start_ts"]
    assert fact["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_tool_events_carry_dispatch_wall_clock_timing():
    current = state()
    events = await run(current, Model(completion("", [call()]), completion("done")))

    assert current.outcome == Outcome.COMPLETED
    start = next(event for event in events if event["type"] == "tool_start")
    result = next(event for event in events if event["type"] == "tool_result")
    assert isinstance(start["started_ts"], float)
    assert result["ended_ts"] >= start["started_ts"]
    assert result["duration_ms"] == pytest.approx(
        (result["ended_ts"] - start["started_ts"]) * 1000, abs=50)


@pytest.mark.asyncio
async def test_recovered_tool_result_carries_no_dispatch_timing():
    # 断点恢复：fact.phase=completed 直接从 fact 构造结果，未经本次 dispatch，
    # tool_result 不带计时字段（collector 端走缺省兜底）
    current = state()
    finished = ToolCall("finished", "read", {})
    current.pending = [finished]
    current.messages.append({"role": "assistant", "content": "",
                             "tool_calls": [finished.message_call()]})
    current.tools[finished.id] = ToolFact(finished, phase="completed",
                                          result={"success": True}, result_recorded=False)

    events = await run(current, Model(completion("resumed")))

    assert current.outcome == Outcome.COMPLETED
    assert not [event for event in events if event["type"] == "tool_start"]
    result = next(event for event in events if event["type"] == "tool_result")
    assert "ended_ts" not in result and "duration_ms" not in result
    assert "started_ts" not in result
