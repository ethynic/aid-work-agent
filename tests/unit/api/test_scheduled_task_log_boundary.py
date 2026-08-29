"""定时任务日志 API 读取边界脱敏单测（无外部依赖）

历史遗留的 scheduled_task_logs 行 error_message/error_trace 可能含明文凭据，
get_task_logs / get_user_logs 在返回前必须经 sanitize_scheduled_task_log_rows
净化（DB 层读取本身不动，库内数据保持原样），且对新写入已脱敏的行幂等。
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.api

from src.api.scheduled_task import get_task_logs, get_user_logs


_HISTORICAL_ROWS = [
    {
        "log_id": "slog_hist1",
        "task_id": "task-a",
        "status": "failed",
        "error_message": "登录失败 password=hunter2@prod x",
        "error_trace": 'Traceback: requests.HTTPError api_key="sk-live-42"',
    },
    {
        "log_id": "slog_hist2",
        "task_id": "task-a",
        "status": "failed",
        "error_message": "连接失败 token=tt-leak-77",
        "error_trace": None,
    },
]


def _task_row():
    return {"task_id": "task-a", "user_id": "user-a", "tenant_id": "t1"}


@pytest.mark.asyncio
async def test_get_task_logs_masks_historical_plaintext():
    """get_task_logs：历史明文行返回前脱敏，明文凭据不出现在响应"""
    with (
        patch("src.api.scheduled_task._get_request_identity",
              return_value=("user-a", "t1")),
        patch("src.scheduler.db.ScheduledTaskDB.get_by_id",
              return_value=_task_row()),
        patch("src.scheduler.db.ScheduledTaskLogDB.list_by_task",
              return_value=[dict(r) for r in _HISTORICAL_ROWS]),
    ):
        resp = await get_task_logs(MagicMock(), "task-a")

    assert resp["success"] is True
    logs = resp["data"]["logs"]
    assert len(logs) == 2
    joined = "".join(
        f"{r.get('error_message')}{r.get('error_trace')}" for r in logs
    )
    assert "hunter2@prod" not in joined
    assert "sk-live-42" not in joined
    assert "tt-leak-77" not in joined
    assert "password=***" in logs[0]["error_message"]
    assert "api_key=***" in logs[0]["error_trace"]
    assert "token=***" in logs[1]["error_message"]


@pytest.mark.asyncio
async def test_get_user_logs_masks_historical_plaintext():
    """get_user_logs：历史明文行返回前脱敏，明文凭据不出现在响应"""
    with (
        patch("src.api.scheduled_task._get_request_identity",
              return_value=("user-a", "t1")),
        patch("src.scheduler.db.ScheduledTaskLogDB.list_by_user",
              return_value=[dict(r) for r in _HISTORICAL_ROWS]),
    ):
        resp = await get_user_logs(MagicMock())

    assert resp["success"] is True
    logs = resp["data"]["logs"]
    joined = "".join(
        f"{r.get('error_message')}{r.get('error_trace')}" for r in logs
    )
    assert "hunter2@prod" not in joined
    assert "sk-live-42" not in joined
    assert "tt-leak-77" not in joined
    assert "password=***" in logs[0]["error_message"]


@pytest.mark.asyncio
async def test_get_task_logs_idempotent_on_already_sanitized_rows():
    """幂等：新写入已脱敏（password=***）的行经边界再脱敏不变"""
    sanitized_rows = [{
        "log_id": "slog_new1",
        "task_id": "task-a",
        "status": "failed",
        "error_message": "登录失败 password=*** x",
        "error_trace": "Traceback: password=***",
    }]
    with (
        patch("src.api.scheduled_task._get_request_identity",
              return_value=("user-a", "t1")),
        patch("src.scheduler.db.ScheduledTaskDB.get_by_id",
              return_value=_task_row()),
        patch("src.scheduler.db.ScheduledTaskLogDB.list_by_task",
              return_value=sanitized_rows),
    ):
        resp = await get_task_logs(MagicMock(), "task-a")

    log = resp["data"]["logs"][0]
    assert log["error_message"] == "登录失败 password=*** x"
    assert log["error_trace"] == "Traceback: password=***"


@pytest.mark.asyncio
async def test_get_user_logs_idempotent_on_already_sanitized_rows():
    """幂等：user_logs 边界对已脱敏文本不再改写"""
    sanitized_rows = [{
        "log_id": "slog_new2",
        "task_id": "task-a",
        "status": "failed",
        "error_message": "登录失败 password=*** x",
        "error_trace": None,
    }]
    with (
        patch("src.api.scheduled_task._get_request_identity",
              return_value=("user-a", "t1")),
        patch("src.scheduler.db.ScheduledTaskLogDB.list_by_user",
              return_value=sanitized_rows),
    ):
        resp = await get_user_logs(MagicMock())

    assert resp["data"]["logs"][0]["error_message"] == "登录失败 password=*** x"
