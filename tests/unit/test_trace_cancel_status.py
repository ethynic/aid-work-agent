"""用户取消时 trace 标记 cancelled 状态（不依赖 PG/LLM）

背景（2026-09-18 线上排查 tr_30005ad7dd284b3e）：用户取消请求后 trace 落库为
completed 且 output 为空，排查时极具误导性。根因：
1. cancel_check 命中时 _process_message_impl 直接 return，不 yield cancelled 事件
2. asyncio.CancelledError 继承 BaseException，不被 process_message 包装层的
   except Exception 捕获，TraceCollector 收不到任何取消信号
修复后：两条取消路径都必须让 trace 以 status=cancelled 落库。
"""

import asyncio
from types import MethodType
from unittest.mock import MagicMock, patch

import pytest

from src.core.agent import Agent
from src.core.agent_events import make_event
from src.core.trace_collector import TraceCollector
from src.services.session_record import SessionRecordManager

pytestmark = pytest.mark.unit


class _FakeRecord:
    """最小 record 桩：仅满足 process_message 的 trace 分支读取"""

    def __init__(self, name):
        self.name = name
        self.source_type = "chat"
        self.session_id = f"sess-{name}"
        self.tenant_id = f"tenant-{name}"
        self.user_id = f"user-{name}"
        self.user_message = "msg"
        # on_complete 从 record 读取这些观测字段
        self.total_token_count = 0
        self.model = "test-model"
        self.provider = "test"
        self.agent_iterations = 1


@pytest.fixture(autouse=True)
def _clean_record_context():
    SessionRecordManager.end_record()
    yield
    SessionRecordManager.end_record()


@pytest.fixture
def persisted_traces():
    """mock schedule_persist，捕获落库前的 trace 对象"""
    traces = []
    with patch("src.core.trace_persist.schedule_persist", side_effect=traces.append):
        yield traces


# ============================================================
# TraceCollector：cancelled 事件
# ============================================================


def test_collector_cancelled_event_sets_status_and_reason():
    collector = TraceCollector(
        session_id="sess-c", tenant_id="t", user_id="u",
        input_msg="hi", source_type="chat", subagent_id=None,
    )

    collector.on_event(make_event("cancelled"))

    assert collector.trace.status == "cancelled"
    assert "cancelled" in collector.trace.tags
    assert collector.trace.metadata.get("termination_reason") == "user_cancelled"


def test_collector_on_complete_keeps_cancelled_status(persisted_traces):
    """取消后 on_complete 不得把 status 升级为 completed"""
    collector = TraceCollector(
        session_id="sess-c", tenant_id="t", user_id="u",
        input_msg="hi", source_type="chat", subagent_id=None,
    )
    collector.on_event(make_event("cancelled"))

    collector.on_complete()

    assert collector.trace.status == "cancelled"
    assert len(persisted_traces) == 1


# ============================================================
# process_message 包装层：两条取消路径
# ============================================================


def _make_agent(fake_impl):
    agent = object.__new__(Agent)
    agent._process_message_impl = MethodType(fake_impl, agent)
    return agent


@pytest.mark.asyncio
async def test_cancelled_error_propagates_and_marks_trace(persisted_traces):
    """asyncio.CancelledError 穿透包装层时，trace 必须标记为 cancelled"""

    async def fake_impl(self, **kwargs):
        yield {"type": "response", "data": "partial"}
        raise asyncio.CancelledError()

    agent = _make_agent(fake_impl)
    record = _FakeRecord("cancel")

    with pytest.raises(asyncio.CancelledError):
        async for _ in agent.process_message("hi", "sess-cancel", _record_service=record):
            pass

    assert len(persisted_traces) == 1
    assert persisted_traces[0].status == "cancelled"
    assert persisted_traces[0].metadata.get("termination_reason") == "user_cancelled"


@pytest.mark.asyncio
async def test_cancel_check_yield_marks_trace(persisted_traces):
    """cancel_check 命中路径：impl yield cancelled 事件后正常返回，trace 标记 cancelled"""

    async def fake_impl(self, **kwargs):
        yield {"type": "response", "data": "partial"}
        if kwargs["cancel_check"] and kwargs["cancel_check"]():
            yield make_event("cancelled")
            return
        yield {"type": "complete"}

    agent = _make_agent(fake_impl)
    record = _FakeRecord("cancelck")

    async for _ in agent.process_message(
        "hi", "sess-cancelck",
        _record_service=record,
        cancel_check=lambda: True,
    ):
        pass

    assert len(persisted_traces) == 1
    assert persisted_traces[0].status == "cancelled"


@pytest.mark.asyncio
async def test_normal_completion_still_completed(persisted_traces):
    """正常完成路径不受影响：status 仍为 completed"""

    async def fake_impl(self, **kwargs):
        yield {"type": "response", "data": "done"}

    agent = _make_agent(fake_impl)
    record = _FakeRecord("ok")

    async for _ in agent.process_message("hi", "sess-ok", _record_service=record):
        pass

    assert persisted_traces[0].status == "completed"
