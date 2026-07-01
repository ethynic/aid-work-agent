"""Agent._run_compression_phase 可观测性集成测试（Phase 7 §7.1 + §7.2）

**这是 Phase 7 测试的核心**。现有 test_process_message_order.py 只验证
v3.2 顺序（check_threshold → compress_now），完全没有验证 Phase 7 新增的可观测性逻辑：

1. 压缩成功 → ContextCompressedEvent 被构造并存到 `_pending_compression_event`
2. 压缩成功 → CompressionMetrics.record_invocation('success'|'fallback', source_type) 被调
3. 压缩成功 → record_duration + record_ratio 被调
4. 未达阈值 → record_invocation('skipped', source_type) 被调
5. 异常路径 → record_invocation('failed', '') 被调
6. fallback_used=True 时 event.fallback_used 也为 True（链路一致性）

这些是 mutation testing 的关键断点：
- 删掉 ContextCompressedEvent 构造 → 测试 1 失败
- 删掉 record_invocation 调用 → 测试 2 失败
- 把 'success' 改成 'failed' → 测试 2 失败
"""

from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest


# ============== 测试用最小 Agent 桩（与 test_process_message_order.py 同构） ==============


class _StubMemory:
    class _ShortTerm:
        max_messages = 100

    short_term = _ShortTerm()


def _make_minimal_agent():
    """构造最小 Agent 实例（绕过 __init__）。"""
    from src.core.agent import Agent

    agent = Agent.__new__(Agent)
    agent.memory = _StubMemory()  # type: ignore[assignment]
    return agent


def _patch_compression_service_with_result(
    monkeypatch,
    *,
    should_compress: bool,
    fallback_used: bool = False,
    trigger_reason: str = "token_threshold(10000/7000, 71%, cached=True)",
    check_exc: Optional[Exception] = None,
    compress_exc: Optional[Exception] = None,
):
    """patch compression_service，返回可配置的 CompressionResult-like MagicMock。"""
    fake_cs = MagicMock()

    async def _check_threshold(session_id, source_type):
        if check_exc is not None:
            raise check_exc
        return (should_compress, trigger_reason, MagicMock(name="meta"))

    async def _compress_now(session_id, source_type, meta, *, force=False, trigger_reason=""):
        if compress_exc is not None:
            raise compress_exc
        return MagicMock(
            summary_id="csum_abc",
            trigger_reason=trigger_reason or "force",
            compressed_message_count=42,
            original_token_count=10000,
            compressed_token_count=3000,
            compression_ratio=0.3,
            fallback_used=fallback_used,
            llm_provider="deepseek",
            llm_model="deepseek-chat",
        )

    fake_cs.check_threshold = _check_threshold
    fake_cs.compress_now = _compress_now

    import src.memory.mid_term as mid_term_mod
    monkeypatch.setattr(mid_term_mod, "get_compression_service", lambda: fake_cs)
    return fake_cs


# ============== 主测试 ==============


@pytest.mark.asyncio
async def test_compression_success_emits_context_compressed_event(monkeypatch):
    """压缩成功 → agent._pending_compression_event 应被设置为 ContextCompressedEvent 字典。

    Mutation testing 关键断点：如果有人删掉 _run_compression_phase 里的
    `event = ContextCompressedEvent(...)` 和 `self._pending_compression_event = event.to_dict()`，
    本测试会失败。
    """
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    _patch_compression_service_with_result(monkeypatch, should_compress=True)

    # 初始状态：没有 pending event
    assert not hasattr(agent, "_pending_compression_event") or agent._pending_compression_event is None

    await agent._run_compression_phase("sess_1")

    # Phase 7 §7.1：压缩成功后必须构造并暂存事件
    assert hasattr(agent, "_pending_compression_event")
    evt = agent._pending_compression_event
    assert evt is not None
    assert evt["type"] == "context_compressed"
    assert evt["summary_id"] == "csum_abc"
    assert evt["compressed_message_count"] == 42
    assert evt["original_token_count"] == 10000
    assert evt["compressed_token_count"] == 3000
    assert evt["compression_ratio"] == 0.3
    assert evt["trigger_reason"] == "token_threshold(10000/7000, 71%, cached=True)"
    assert evt["llm_provider"] == "deepseek"
    assert evt["llm_model"] == "deepseek-chat"
    # duration_ms 是 int(perf_counter * 1000)，Windows 低分辨率下可能为 0，
    # 但字段必须存在且为 int 类型（不是 None / 字符串）
    assert isinstance(evt["duration_ms"], int)
    assert evt["duration_ms"] >= 0


@pytest.mark.asyncio
async def test_compression_success_records_metrics_success(monkeypatch):
    """压缩成功（fallback_used=False）→ record_invocation('success', source_type) 被调一次。

    Mutation testing：把代码里的 'success' 改成 'failed'，此测试会失败。
    """
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    _patch_compression_service_with_result(
        monkeypatch, should_compress=True, fallback_used=False
    )

    calls: List[tuple] = []
    mock_metrics = MagicMock()
    mock_metrics.record_invocation = lambda result, st="": calls.append((result, st))
    mock_metrics.record_duration = MagicMock()
    mock_metrics.record_ratio = MagicMock()

    import src.core.compression_metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "get_compression_metrics", lambda: mock_metrics)

    await agent._run_compression_phase("sess_1")

    # 必须有 'success' 调用
    success_calls = [c for c in calls if c[0] == "success"]
    assert len(success_calls) == 1, f"expected 1 success call, got {calls}"
    assert success_calls[0][1] == "chat"  # source_type


@pytest.mark.asyncio
async def test_compression_fallback_records_metrics_fallback(monkeypatch):
    """fallback_used=True → record_invocation('fallback', source_type) 被调（不是 'success'）。

    Mutation testing：把代码 `result.fallback_used else 'success'` 改成 `else 'success'`，
    此测试会失败。
    """
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    _patch_compression_service_with_result(
        monkeypatch, should_compress=True, fallback_used=True
    )

    calls: List[tuple] = []
    mock_metrics = MagicMock()
    mock_metrics.record_invocation = lambda result, st="": calls.append((result, st))
    mock_metrics.record_duration = MagicMock()
    mock_metrics.record_ratio = MagicMock()

    import src.core.compression_metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "get_compression_metrics", lambda: mock_metrics)

    await agent._run_compression_phase("sess_1")

    fallback_calls = [c for c in calls if c[0] == "fallback"]
    assert len(fallback_calls) == 1, f"expected 1 fallback call, got {calls}"
    assert fallback_calls[0][1] == "chat"

    # 同时 event.fallback_used 也应为 True（链路一致性）
    assert agent._pending_compression_event["fallback_used"] is True


@pytest.mark.asyncio
async def test_compression_success_records_duration_and_ratio(monkeypatch):
    """压缩成功 → record_duration + record_ratio 都被调。

    Mutation testing：删掉任一调用，测试失败。
    """
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "wecom_kf"  # type: ignore[assignment]
    _patch_compression_service_with_result(monkeypatch, should_compress=True)

    mock_metrics = MagicMock()
    mock_metrics.record_invocation = MagicMock()
    mock_metrics.record_duration = MagicMock()
    mock_metrics.record_ratio = MagicMock()

    import src.core.compression_metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "get_compression_metrics", lambda: mock_metrics)

    await agent._run_compression_phase("sess_1")

    mock_metrics.record_duration.assert_called_once()
    # duration 应为非负数秒（Windows 低分辨率下可能为 0.0）
    duration_arg = mock_metrics.record_duration.call_args.args[0]
    assert isinstance(duration_arg, float)
    assert duration_arg >= 0.0

    mock_metrics.record_ratio.assert_called_once()
    ratio_arg = mock_metrics.record_ratio.call_args.args[0]
    assert ratio_arg == 0.3


@pytest.mark.asyncio
async def test_compression_below_threshold_records_skipped(monkeypatch):
    """未达阈值 → record_invocation('skipped', source_type) 被调。

    Mutation testing：把 else 分支的 'skipped' 改成 'failed' 或删掉，测试失败。
    """
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    _patch_compression_service_with_result(monkeypatch, should_compress=False)

    calls: List[tuple] = []
    mock_metrics = MagicMock()
    mock_metrics.record_invocation = lambda result, st="": calls.append((result, st))

    import src.core.compression_metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "get_compression_metrics", lambda: mock_metrics)

    result = await agent._run_compression_phase("sess_1")

    assert result is None
    # 必须有 skipped 调用
    skipped_calls = [c for c in calls if c[0] == "skipped"]
    assert len(skipped_calls) == 1, f"expected 1 skipped call, got {calls}"
    assert skipped_calls[0][1] == "chat"
    # 未达阈值时不构造 event
    assert getattr(agent, "_pending_compression_event", None) is None


@pytest.mark.asyncio
async def test_compression_exception_records_failed(monkeypatch):
    """异常路径 → record_invocation('failed', '') 被调。

    Mutation testing：把 except 块的 'failed' 改成 'skipped'，测试失败。
    """
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    _patch_compression_service_with_result(
        monkeypatch,
        should_compress=True,
        check_exc=RuntimeError("service down"),
    )

    calls: List[tuple] = []
    mock_metrics = MagicMock()
    mock_metrics.record_invocation = lambda result, st="": calls.append((result, st))

    import src.core.compression_metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "get_compression_metrics", lambda: mock_metrics)

    # 用 loguru sink 吞掉异常日志（避免污染测试输出）
    from loguru import logger as loguru_logger
    sink_id = loguru_logger.add(lambda msg: None, level="ERROR")
    try:
        result = await agent._run_compression_phase("sess_1")
    finally:
        loguru_logger.remove(sink_id)

    assert result is None
    failed_calls = [c for c in calls if c[0] == "failed"]
    assert len(failed_calls) == 1, f"expected 1 failed call, got {calls}"
    # 异常路径 source_type 为空字符串
    assert failed_calls[0][1] == ""


@pytest.mark.asyncio
async def test_metrics_exception_does_not_break_main_flow(monkeypatch):
    """指标埋点自身抛异常 → 仅 debug 日志，不影响主流程返回 result。

    被测代码：
        try: metrics.record_invocation(...)
        except Exception as oe: logger.debug(...)
    """
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    _patch_compression_service_with_result(monkeypatch, should_compress=True)

    mock_metrics = MagicMock()
    mock_metrics.record_invocation = MagicMock(side_effect=RuntimeError("metrics broken"))
    mock_metrics.record_duration = MagicMock(side_effect=RuntimeError("metrics broken"))
    mock_metrics.record_ratio = MagicMock(side_effect=RuntimeError("metrics broken"))

    import src.core.compression_metrics as metrics_mod
    monkeypatch.setattr(metrics_mod, "get_compression_metrics", lambda: mock_metrics)

    # 吞掉 debug 日志
    from loguru import logger as loguru_logger
    sink_id = loguru_logger.add(lambda msg: None, level="DEBUG")
    try:
        # 不应抛异常
        result = await agent._run_compression_phase("sess_1")
    finally:
        loguru_logger.remove(sink_id)

    # 主流程仍正常返回（压缩已完成，只是 metrics 埋点失败）
    assert result is not None
    assert result.summary_id == "csum_abc"


# ============== 事件字段一致性 ==============


@pytest.mark.asyncio
async def test_event_fields_match_compression_result(monkeypatch):
    """event 字段必须与 CompressionResult 一一对应（不丢字段、不写错字段）。

    Mutation testing：把 `original_token_count=result.original_token_count` 改成
    `original_token_count=result.compressed_token_count`（抄错），此测试会失败。
    """
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]
    _patch_compression_service_with_result(
        monkeypatch,
        should_compress=True,
        fallback_used=False,
        trigger_reason="message_threshold(160/150)",
    )

    await agent._run_compression_phase("sess_1")
    evt = agent._pending_compression_event
    assert evt is not None

    # 字段一致性（每个字段独立断言，便于定位抄错）
    assert evt["summary_id"] == "csum_abc"
    assert evt["compressed_message_count"] == 42
    assert evt["original_token_count"] == 10000
    assert evt["compressed_token_count"] == 3000
    assert evt["compression_ratio"] == 0.3
    assert evt["fallback_used"] is False
    assert evt["trigger_reason"] == "message_threshold(160/150)"
    assert evt["llm_provider"] == "deepseek"
    assert evt["llm_model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_trigger_reason_passthrough_force(monkeypatch):
    """force 触发的 reason 也应被透传到 event.trigger_reason。

    验证不同 trigger_reason 值（force / token_threshold / message_threshold）
    都能正确透传。
    """
    agent = _make_minimal_agent()
    agent._detect_source_type = lambda: "chat"  # type: ignore[assignment]

    for reason in [
        "force",
        "token_threshold(10000/7000, 71%, cached=True)",
        "message_threshold(160/200)",
    ]:
        # 重置 pending event
        agent._pending_compression_event = None
        _patch_compression_service_with_result(
            monkeypatch, should_compress=True, trigger_reason=reason
        )
        await agent._run_compression_phase("sess_1")
        assert agent._pending_compression_event["trigger_reason"] == reason, \
            f"reason not passed through: {reason}"
