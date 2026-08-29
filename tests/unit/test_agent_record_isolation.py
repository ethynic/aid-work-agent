"""Agent.process_message_sync 请求级 record 隔离测试（不依赖 PG/LLM）

背景：record_service 原先写到共享 Agent 实例属性（_explicit_record_service），
并发请求共用同一 Agent 实例时会互相覆盖，token 计费串号。现改为：
- 显式 record 随协程参数传给 process_message（_record_service）
- 同时写入请求级 ContextVar（SessionRecordManager.set_current_record），
  finally 用 token 恢复进入前值（嵌套/异常/取消路径均不泄漏）
"""

import asyncio
from types import MethodType
from unittest.mock import MagicMock, patch

import pytest

from src.core.agent import Agent
from src.services.session_record import SessionRecordManager


class _FakeRecord:
    """最小 record 桩：仅满足 process_message 的 trace 分支读取"""

    def __init__(self, name):
        self.name = name
        self.source_type = "wecom_kf"
        self.session_id = f"sess-{name}"
        self.tenant_id = f"tenant-{name}"
        self.user_id = f"user-{name}"
        self.user_message = "msg"


@pytest.fixture(autouse=True)
def _clean_record_context():
    """用例前后清空 record ContextVar，避免用例间串扰"""
    SessionRecordManager.end_record()
    yield
    SessionRecordManager.end_record()


@pytest.fixture(autouse=True)
def _mock_trace_collector():
    """TraceCollector 涉及持久化调度，单测整体 mock 掉"""
    with patch("src.core.trace_collector.TraceCollector", MagicMock()):
        yield


def _make_agent(fake_impl):
    agent = object.__new__(Agent)
    agent._process_message_impl = MethodType(fake_impl, agent)
    return agent


@pytest.mark.asyncio
async def test_concurrent_process_message_sync_records_isolated():
    """两个并发 process_message_sync 各自 record_service 不互相覆盖"""
    agent = object.__new__(Agent)
    observed = {}
    arrivals = []
    all_arrived = asyncio.Event()
    release = asyncio.Event()

    async def fake_impl(self, **kwargs):
        observed[kwargs["session_id"]] = SessionRecordManager.get_current_record()
        arrivals.append(kwargs["session_id"])
        if len(arrivals) == 2:
            all_arrived.set()
        await release.wait()  # 制造并发覆盖窗口
        yield {"type": "complete"}

    agent._process_message_impl = MethodType(fake_impl, agent)

    record_a = _FakeRecord("a")
    record_b = _FakeRecord("b")

    async def run(session_id, record_service):
        return await agent.process_message_sync(
            "hi", session_id, record_service=record_service,
        )

    task_a = asyncio.create_task(run("sess-a", record_a))
    task_b = asyncio.create_task(run("sess-b", record_b))
    await asyncio.wait_for(all_arrived.wait(), timeout=5)
    release.set()
    await asyncio.gather(task_a, task_b)

    assert observed["sess-a"] is record_a, "sess-a 的 record 被并发请求覆盖"
    assert observed["sess-b"] is record_b, "sess-b 的 record 被并发请求覆盖"
    # 全部结束后不泄漏
    assert SessionRecordManager.get_current_record() is None


@pytest.mark.asyncio
async def test_process_message_sync_nested_restores_outer_record():
    """嵌套调用：内层显式 record 结束后恢复外层请求的 record；
    record_service=None 时沿用外层 record（不覆盖）"""
    seen = []

    async def fake_impl(self, **kwargs):
        seen.append(SessionRecordManager.get_current_record())
        yield {"type": "complete"}

    agent = _make_agent(fake_impl)

    outer = _FakeRecord("outer")
    token = SessionRecordManager.set_current_record(outer)
    try:
        await agent.process_message_sync("hi", "sess-outer", record_service=None)
        assert seen[-1] is outer, "record_service=None 应沿用外层 record"

        inner = _FakeRecord("inner")
        await agent.process_message_sync("hi", "sess-inner", record_service=inner)
        assert seen[-1] is inner, "内层应看到自己的显式 record"
        assert SessionRecordManager.get_current_record() is outer, \
            "内层结束后必须恢复外层 record，而不是清空"
    finally:
        SessionRecordManager.reset_current_record(token)
    assert SessionRecordManager.get_current_record() is None


@pytest.mark.asyncio
async def test_process_message_sync_exception_resets_record():
    async def fake_impl(self, **kwargs):
        yield {"type": "started"}
        raise RuntimeError("boom")

    agent = _make_agent(fake_impl)
    record = _FakeRecord("exploded")

    with pytest.raises(RuntimeError, match="boom"):
        await agent.process_message_sync("hi", "sess-x", record_service=record)

    assert SessionRecordManager.get_current_record() is None


@pytest.mark.asyncio
async def test_process_message_sync_cancel_resets_record():
    started = asyncio.Event()

    async def fake_impl(self, **kwargs):
        yield {"type": "started"}
        started.set()
        await asyncio.Event().wait()  # 挂起等待取消

    agent = _make_agent(fake_impl)
    record = _FakeRecord("cancelled")

    task = asyncio.create_task(
        agent.process_message_sync("hi", "sess-x", record_service=record)
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert SessionRecordManager.get_current_record() is None


def test_record_manager_set_reset_roundtrip():
    """SessionRecordManager 公共 set/reset 配对恢复进入前值"""
    record = _FakeRecord("roundtrip")
    assert SessionRecordManager.get_current_record() is None
    token = SessionRecordManager.set_current_record(record)
    assert SessionRecordManager.get_current_record() is record
    SessionRecordManager.reset_current_record(token)
    assert SessionRecordManager.get_current_record() is None
