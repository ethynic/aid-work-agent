"""Agent._run_compression_phase 执行顺序测试（v3.2.1 P1-3 重写）

v3.2 原版本是「重构型测试」：把 v3.2 顺序代码硬编码在测试里，不 import agent.py，
有人改坏 agent.py 这测试仍会全绿（零回归保护）。

v3.2.1 重写：调真实 `Agent._run_compression_phase`（从 _process_message_impl 提取的
独立方法），通过 spy compression_service 各方法验证调用顺序与次数，
真正保护 v3.2 执行顺序不被破坏。

覆盖：
- 未达阈值场景：compress_now 不被调用
- 达阈值场景：compress_now 透传 reason（v3.2.1 P0-1）
- check_threshold / compress_now 抛异常时不向上传播（异常隔离）
- mid_term.enabled=False 时直接返回 None
- _run_compression_phase 自身不调 MessageDB.list_by_session（v3.2 IO 优化）
"""

from typing import Any, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest


# ============== 测试用最小 Agent 桩 ==============


class _StubMemory:
    """最小 memory 桩：仅暴露 _run_compression_phase 用到的属性。"""

    class _ShortTerm:
        max_messages = 100

    short_term = _ShortTerm()


def _make_minimal_agent():
    """构造一个最小可用的 Agent 实例（绕过 __init__ 的复杂依赖）。

    _run_compression_phase 只用到 self._detect_source_type()（间接）。
    我们直接导入 Agent 类，给实例赋上必要属性即可。
    """
    from src.core.agent import Agent

    agent = Agent.__new__(Agent)  # 跳过 __init__
    agent.memory = _StubMemory()  # type: ignore[assignment]
    return agent


def _patch_compression_service(monkeypatch, *, should_compress, check_exc=None, compress_exc=None):
    """patch 模块级 get_compression_service，返回带 trace 的桩。

    Returns:
        call_trace（list）：记录 check_threshold / compress_now 的调用顺序
    """
    call_trace: List[str] = []

    fake_cs = MagicMock()

    async def _check_threshold(session_id, source_type):
        call_trace.append(f"check_threshold:{session_id}:{source_type}")
        if check_exc is not None:
            raise check_exc
        return (should_compress, "token_threshold(...)", MagicMock(name="meta"))

    async def _compress_now(session_id, source_type, meta, *, force=False, trigger_reason=""):
        call_trace.append(
            f"compress_now:{session_id}:{source_type}:force={force}:reason={trigger_reason}"
        )
        if compress_exc is not None:
            raise compress_exc
        return MagicMock(summary_id="csum_x", trigger_reason=trigger_reason or "force")

    fake_cs.check_threshold = _check_threshold
    fake_cs.compress_now = _compress_now

    # _run_compression_phase 内部是延迟 import：`from src.memory.mid_term import get_compression_service`
    # patch 模块级函数即可生效
    import src.memory.mid_term as mid_term_mod
    monkeypatch.setattr(mid_term_mod, "get_compression_service", lambda: fake_cs)
    return call_trace


def _patch_list_by_session(monkeypatch) -> dict:
    """spy MessageDB.list_by_session，返回调用计数 dict。"""
    list_calls = {"n": 0}
    import src.db.models as models_mod

    def _fake_list(session_id, **kw):
        list_calls["n"] += 1
        return []
    monkeypatch.setattr(models_mod.MessageDB, "list_by_session", staticmethod(_fake_list))
    return list_calls


# ============== 主测试 ==============


@pytest.mark.asyncio
async def test_v32_below_threshold_skips_compress_now(monkeypatch):
    """v3.2 顺序断言 1：未达阈值 → compress_now 不被调用。"""
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    call_trace = _patch_compression_service(monkeypatch, should_compress=False)
    list_calls = _patch_list_by_session(monkeypatch)

    result = await agent._run_compression_phase("sess")

    assert result is None, "未达阈值应返回 None"
    assert list_calls["n"] == 0, "压缩阶段不应触发 list_by_session"
    assert len(call_trace) == 1
    assert call_trace[0].startswith("check_threshold:")
    assert not any("compress_now" in c for c in call_trace), \
        "未达阈值时 compress_now 不应被调用"


@pytest.mark.asyncio
async def test_v32_at_threshold_calls_compress_now_with_reason(monkeypatch):
    """v3.2 顺序断言 2：达阈值 → check_threshold → compress_now，且 reason 透传。"""
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    call_trace = _patch_compression_service(monkeypatch, should_compress=True)

    result = await agent._run_compression_phase("sess")

    assert result is not None, "达阈值应返回 CompressionResult"
    assert len(call_trace) == 2
    assert call_trace[0].startswith("check_threshold:")
    assert call_trace[1].startswith("compress_now:")
    # v3.2.1 P0-1：compress_now 必须收到 check_threshold 返回的精确 reason
    assert "reason=token_threshold(...)" in call_trace[1], \
        "compress_now 必须透传 check_threshold 的精确 reason"


@pytest.mark.asyncio
async def test_v32_check_threshold_exception_isolated(monkeypatch):
    """v3.2 顺序断言 3：check_threshold 抛异常 → 不向上抛，返回 None。

    v3.2.1 P1-4：日志必须含 action=skipped_due_to_error。
    """
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    _patch_compression_service(
        monkeypatch,
        should_compress=True,
        check_exc=RuntimeError("compress service down"),
    )

    # 用 loguru sink 捕获日志（项目用 loguru，标准 caplog 拿不到）
    from loguru import logger as loguru_logger
    captured: list = []

    sink_id = loguru_logger.add(
        lambda msg: captured.append(msg.record["message"]),
        level="ERROR",
    )
    try:
        # 不应抛异常
        result = await agent._run_compression_phase("sess")
    finally:
        loguru_logger.remove(sink_id)

    assert result is None, "异常路径应返回 None（异常被吞掉）"
    # v3.2.1 P1-4：日志含 action=skipped_due_to_error
    assert any("skipped_due_to_error" in m for m in captured), \
        "异常日志必须含 action=skipped_due_to_error"


@pytest.mark.asyncio
async def test_v32_compress_now_exception_isolated(monkeypatch):
    """v3.2 顺序断言 4：compress_now 抛异常 → 不向上抛，返回 None。"""
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    _patch_compression_service(
        monkeypatch,
        should_compress=True,
        compress_exc=RuntimeError("disk full"),
    )

    result = await agent._run_compression_phase("sess")
    assert result is None, "compress_now 异常时应返回 None"


@pytest.mark.asyncio
async def test_v32_disabled_mid_term_skips_compression(monkeypatch):
    """v3.2 顺序断言 5：mid_term.enabled=False 时直接返回 None，不调用任何 service。"""
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]

    from src.config.settings import settings
    monkeypatch.setattr(settings.memory.mid_term, "enabled", False)

    call_trace = _patch_compression_service(monkeypatch, should_compress=True)

    result = await agent._run_compression_phase("sess")
    assert result is None, "禁用压缩时应返回 None"
    assert call_trace == [], "禁用时应完全跳过 compression_service"


@pytest.mark.asyncio
async def test_v32_run_compression_phase_does_not_load_messages(monkeypatch):
    """v3.2 IO 优化：_run_compression_phase 自身不调 MessageDB.list_by_session。

    压缩阶段唯一可能触发 list_by_session 的是 compress_now 内的 _load_messages，
    但在本测试中 compression_service 被 stub 替换，_load_messages 不会被调用。
    主流程的 memory 重建在 _run_compression_phase 之外。
    """
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    _patch_compression_service(monkeypatch, should_compress=True)
    list_calls = _patch_list_by_session(monkeypatch)

    await agent._run_compression_phase("sess")
    assert list_calls["n"] == 0, \
        "_run_compression_phase 自身不应直接调 list_by_session（IO 优化）"
