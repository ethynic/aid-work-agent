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


# ============================================================
# superseded 投影（2026-11-26，web_c trace 2f41da/356b58 调查）：
# 渠道新消息取代在途执行（runner_agent.submit supersede）-> repository.cancel
# 写 checkpoint.cancel_reason -> _persist_trace 投影 'superseded'。
# ============================================================


def test_persist_trace_superseded_projects_superseded():
    """渠道取代收尾（cancel_only claim 路径）：cancelled + cancel_reason -> 投影 superseded。"""
    runner = {
        "status": "cancelled",
        "result": {"status": "cancelled", "output": ""},
        "checkpoint": {"finalization_intent": "cancel_only",
                       "cancel_only_from_status": "running",
                       "cancel_reason": "superseded_by_new_message"},
        "record_id": "record-1",
    }
    worker = _worker(runner)
    collector = _collector()
    with patch("src.core.trace_persist.update_completion") as mock_update:
        worker._persist_trace(collector, "runner-1", _control(), finalization_retry=True)

    assert mock_update.call_args.kwargs["status"] == "superseded"


def test_persist_trace_superseded_engine_path_sets_collector_status():
    """engine 优雅取消路径（非 finalization_retry）：collector.trace.status 投影 superseded。"""
    runner = {
        "status": "cancelled",
        "result": {"status": "cancelled", "output": ""},
        "checkpoint": {"execution": {}, "cancel_reason": "superseded_by_new_message"},
        "record_id": "record-1",
        "attempt": 2,
    }
    worker = _worker(runner)
    collector = _collector()
    with patch("src.db.models.ChatRecordDB") as mock_db:
        mock_db.get_by_id.return_value = None
        worker._persist_trace(collector, "runner-1", _control(), finalization_retry=False)

    assert collector.trace.status == "superseded"
    collector.on_complete.assert_called_once()


def test_persist_trace_user_stop_without_reason_keeps_cancelled():
    """用户主动停止（无 cancel_reason）：不误伤，保持 cancelled。"""
    runner = {
        "status": "cancelled",
        "result": {"status": "cancelled", "output": ""},
        "checkpoint": {"execution": {}},
        "record_id": "record-1",
    }
    worker = _worker(runner)
    collector = _collector()
    with patch("src.core.trace_persist.update_completion") as mock_update:
        worker._persist_trace(collector, "runner-1", _control(), finalization_retry=True)

    assert mock_update.call_args.kwargs["status"] == "cancelled"


def test_preserve_server_checkpoint_keeps_cancel_reason():
    """engine 保存 checkpoint 跨写保留 cancel_reason（repository.cancel 写入不被覆盖）。"""
    from src.services.agent_runner.execution_repository import _preserve_server_checkpoint

    row = {"checkpoint": {"cancel_reason": "superseded_by_new_message",
                          "automatic_local_cancel": True}}
    saved = _preserve_server_checkpoint(row, {"execution": {"iteration": 3}})
    assert saved["cancel_reason"] == "superseded_by_new_message"
    assert saved["automatic_local_cancel"] is True
    assert saved["execution"] == {"iteration": 3}

    # 无标记的普通保存不注入
    assert "cancel_reason" not in _preserve_server_checkpoint({"checkpoint": {}}, {})


def _run_cancel(row, reason):
    """驱动 RunnerRepository.cancel，捕获 UPDATE checkpoint 片段与参数。"""
    from src.services.agent_runner.repository import RunnerRepository

    repo = object.__new__(RunnerRepository)
    repo.assert_owner = lambda *args, **kwargs: None
    captured = []
    updates = {"checkpoint_update": None}
    # fetchone 序列：SELECT FOR UPDATE -> UPDATE RETURNING -> resume UPDATE RETURNING
    responses = iter([dict(row),
                      dict(row, status="cancelled", cancel_requested=True),
                      dict(row, status="cancelled", cancel_requested=True)])

    class Cursor:
        def execute(self, sql, params=()):
            captured.append((sql, params))
            if "UPDATE agent_runners SET cancel_requested=TRUE" in sql or (
                    "SET status='cancelled'" in sql and "checkpoint" in sql):
                updates["checkpoint_update"] = (sql, params)

        def fetchone(self):
            return next(responses, None)

    conn = MagicMock()
    conn.cursor.return_value = Cursor()

    @contextmanager
    def factory():
        yield conn

    repo.connection_factory = factory
    with patch("src.services.agent_runner.repository.EventRepository") as mock_events, \
            patch("src.services.agent_runner.control_repository.close_pending_controls"):
        mock_events.notify_in_tx.side_effect = lambda cursor, before, after=None: after
        repo.cancel({"principal": True}, "runner-1", reason=reason)
    return updates["checkpoint_update"]


def test_repository_cancel_running_writes_reason_checkpoint():
    """running 取消带 reason：checkpoint 写入 cancel_reason（与 automatic 标记合成单表达式）。"""
    row = {"runner_id": "runner-1", "status": "running", "cancel_requested": False,
           "checkpoint": {}, "session_kind": "channel", "source": "web_c",
           "worker_id": "w", "lease_until": None}
    sql, params = _run_cancel(row, reason="superseded_by_new_message")
    assert "ARRAY['cancel_reason']" in sql
    # 占位符顺序：automatic -> CANCEL_CHECKPOINT_KEY -> reason -> runner_id
    from src.services.agent_runner.auto_local_recovery import CANCEL_CHECKPOINT_KEY
    assert params[0] is False and params[1] == CANCEL_CHECKPOINT_KEY
    assert params[-2] == "superseded_by_new_message"
    assert params[-1] == "runner-1"


def test_repository_cancel_running_without_reason_keeps_original_sql():
    """无 reason（用户停止）：SQL 不引入 cancel_reason，既有参数序不变。"""
    row = {"runner_id": "runner-1", "status": "running", "cancel_requested": False,
           "checkpoint": {}, "session_kind": "channel", "source": "web_c",
           "worker_id": "w", "lease_until": None}
    sql, params = _run_cancel(row, reason=None)
    assert "cancel_reason" not in sql
    assert params[-1] == "runner-1"
    assert len(params) == 3


def test_repository_cancel_queued_writes_reason_checkpoint():
    """queued 直接取消带 reason：checkpoint 写入 cancel_reason（NULL 兜底 COALESCE）。"""
    row = {"runner_id": "runner-1", "status": "queued", "cancel_requested": False,
           "checkpoint": None, "session_kind": "channel", "source": "web_c"}
    sql, params = _run_cancel(row, reason="superseded_by_new_message")
    assert "ARRAY['cancel_reason']" in sql
    assert params[-2] == "superseded_by_new_message"
    assert "COALESCE" in sql
