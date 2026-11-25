"""中断执行可观测性：claim 记录 cancel_only_from_status + obs_traces 中断投影。

背景（2026-11-25，聊易 trace tr_runner_1c300ad9 调查，设计
runner-interrupt-observability-design.md）：
- skill fatal / lease 失效等系统中断 -> interrupt_attempt（status='interrupted'，
  非用户取消）-> 渠道侧 ChannelRunnerAgent._cancel_and_wait 收尾（cancel_requested=t）
  -> claim 以 cancel_only 领取 -> cancel_only finalization 落终态 'cancelled'。
- obs_traces 若照抄终态会被前端渲染成「用户取消」，而 wecom_kf 等渠道根本没有
  用户取消入口，排查误导。修复：claim 在 checkpoint 记录收尾前状态
  cancel_only_from_status，_persist_trace 的 finalization_retry 分支把
  interrupted 来源的 cancelled 终态投影为 interrupted。
- agent_runners 终态保持 cancelled（终态机/结算/认领深度耦合，渠道收尾语义成立），
  只修正观测投影。
"""

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.services.agent_runner.execution_repository import ExecutionRepository
from src.services.agent_runner.worker import RunnerWorker

pytestmark = pytest.mark.unit


# ============================================================
# claim：cancel_only 收尾记录 cancel_only_from_status
# ============================================================


def _interrupted_row():
    return {
        "runner_id": "runner-1",
        "status": "interrupted",
        "cancel_requested": True,
        "checkpoint": {"version": 1},
        "scope_key": "scope",
        "session_kind": "channel",
        "session_id": "session",
        "queue_order": 1,
        "attempt": 1,
        "source": "wecom_kf",
    }


def _run_claim(row):
    """驱动 ExecutionRepository.acquire，捕获 checkpoint UPDATE 参数。"""
    repo = object.__new__(ExecutionRepository)
    captured = []
    second_fetch = iter([dict(row, status="finalizing")])

    class Cursor:
        def execute(self, sql, params=()):
            captured.append((sql, params))

        def fetchone(self):
            if "SELECT r.* FROM agent_runners" in captured[0][0]:
                return dict(row)
            return next(second_fetch, None)

    conn = MagicMock()
    conn.cursor.return_value = Cursor()

    @contextmanager
    def factory():
        yield conn

    repo.connection_factory = factory

    with patch("src.services.agent_runner.execution_repository.EventRepository") as mock_events:
        mock_events.notify_in_tx.side_effect = lambda cursor, before, after: after
        result = repo.acquire("worker-1", 300)

    updates = [(sql, params) for sql, params in captured
               if "UPDATE agent_runners" in sql and "finalizing" in sql]
    assert len(updates) == 1, "claim 应恰好执行一次 finalizing 认领 UPDATE"
    checkpoint = json.loads(updates[0][1][0])
    return result, checkpoint, row


def test_claim_interrupted_records_cancel_only_from_status():
    """interrupted 行 cancel_only 认领：checkpoint 记 cancel_only_from_status='interrupted'。"""
    result, checkpoint, _row = _run_claim(_interrupted_row())
    assert result["cancel_only"] is True
    assert checkpoint["finalization_intent"] == "cancel_only"
    assert checkpoint["cancel_only_from_status"] == "interrupted", (
        "不记录收尾前状态，观测层无法区分系统中断与用户取消"
    )


def test_claim_waiting_records_cancel_only_from_status():
    """waiting 行 cancel_only 认领（用户取消等待中任务）：记录来源 waiting。"""
    row = _interrupted_row()
    row["status"] = "waiting"
    _result, checkpoint, _row2 = _run_claim(row)
    assert checkpoint["cancel_only_from_status"] == "waiting"


# ============================================================
# _persist_trace：finalization_retry 分支的中断投影
# ============================================================


def _worker(runner_row):
    worker = object.__new__(RunnerWorker)
    worker.repository = SimpleNamespaceGet(runner_row)
    return worker


class SimpleNamespaceGet:
    def __init__(self, row):
        self._row = row

    def get(self, runner_id):
        return {"attempt": 2, **self._row}


def _collector():
    from types import SimpleNamespace
    return SimpleNamespace(
        trace=SimpleNamespace(trace_id='tr_x', status='cancelled', output='',
                              error_message=None, metadata={}, total_tokens=0,
                              agent_iterations=0, tags=[]),
        on_complete=MagicMock(),
    )


def _control():
    from types import SimpleNamespace
    return SimpleNamespace(snapshot={}, attempt=SimpleNamespace(number=2))


def test_finalize_retry_interrupted_projects_interrupted_status():
    """中断收尾：cancelled 终态 + cancel_only_from_status=interrupted -> obs 投影 interrupted。"""
    runner = {
        "status": "cancelled",
        "result": {"status": "cancelled", "output": ""},
        "checkpoint": {"finalization_intent": "cancel_only",
                       "cancel_only_from_status": "interrupted"},
        "record_id": "record-1",
    }
    worker = _worker(runner)
    collector = _collector()
    with patch("src.core.trace_persist.update_completion") as mock_update:
        worker._persist_trace(collector, "runner-1", _control(), finalization_retry=True)

    assert mock_update.call_args.kwargs["status"] == "interrupted", (
        "系统中断收尾不得投影为 cancelled（会被前端渲染成「用户取消」）"
    )
    # error_message 传 None（中断当轮 UPSERT 已写入 EXECUTION_INTERRUPTED，
    # update_completion 对非 completed/cancelled 状态走 ELSE 保留分支）


def test_finalize_retry_user_cancel_keeps_cancelled():
    """用户取消 waiting/paused 任务收尾：不误伤，obs 终态保持 cancelled。"""
    runner = {
        "status": "cancelled",
        "result": {"status": "cancelled", "output": ""},
        "checkpoint": {"finalization_intent": "cancel_only",
                       "cancel_only_from_status": "waiting"},
        "record_id": "record-1",
    }
    worker = _worker(runner)
    collector = _collector()
    with patch("src.core.trace_persist.update_completion") as mock_update:
        worker._persist_trace(collector, "runner-1", _control(), finalization_retry=True)

    assert mock_update.call_args.kwargs["status"] == "cancelled"


def test_finalize_retry_legacy_cancelled_without_marker_keeps_cancelled():
    """存量 checkpoint 无 cancel_only_from_status：保持 cancelled（向后兼容）。"""
    runner = {
        "status": "cancelled",
        "result": {"status": "cancelled", "output": ""},
        "checkpoint": {"finalization_intent": "cancel_only"},
        "record_id": "record-1",
    }
    worker = _worker(runner)
    collector = _collector()
    with patch("src.core.trace_persist.update_completion") as mock_update:
        worker._persist_trace(collector, "runner-1", _control(), finalization_retry=True)

    assert mock_update.call_args.kwargs["status"] == "cancelled"
