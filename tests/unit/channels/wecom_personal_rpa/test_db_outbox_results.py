"""outbox 多 action 回执的持久化合并测试。"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.channels.wecom_personal_rpa import db


def _run(row, *, action_index, success=True):
    cursor = MagicMock()
    cursor.fetchone.return_value = row
    cursor.rowcount = 1
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def fake_connection():
        yield conn

    with patch.object(db, "get_db_connection", fake_connection):
        result = db.mark_outbox_result_for_client(
            tenant_id="tenant_1",
            client_id="client_1",
            request_id="req_1",
            action_index=action_index,
            success=success,
            error_message="masked" if not success else None,
        )
    return result, cursor, conn


def _row(action_results=None):
    return {
        "id": "act_1",
        "actions": [{"type": "noop"}, {"type": "noop"}, {"type": "noop"}],
        "action_results": action_results or {},
    }


def test_out_of_order_success_is_persisted_without_early_completion():
    result, cursor, conn = _run(_row(), action_index=2)
    assert result is True
    update = cursor.execute.call_args_list[1]
    assert update.args[1][0] == '{"2": "succeeded"}'
    assert update.args[1][1] is None
    conn.commit.assert_called_once()


def test_all_indices_success_required_for_succeeded():
    result, cursor, _ = _run(
        _row({"0": "succeeded", "2": "succeeded"}), action_index=1
    )
    assert result is True
    assert cursor.execute.call_args_list[1].args[1][1] == "succeeded"


def test_any_failure_immediately_marks_failed():
    result, cursor, _ = _run(_row({"2": "succeeded"}), action_index=0, success=False)
    assert result is True
    params = cursor.execute.call_args_list[1].args[1]
    assert params[1] == "failed"
    assert params[3] == "masked"


def test_duplicate_index_is_idempotent_and_does_not_overwrite():
    result, cursor, conn = _run(_row({"1": "succeeded"}), action_index=1, success=False)
    assert result is True
    assert cursor.execute.call_count == 1
    conn.commit.assert_not_called()


@pytest.mark.parametrize("index", [-1, 3])
def test_invalid_index_is_rejected(index):
    result, cursor, conn = _run(_row(), action_index=index)
    assert result is False
    assert cursor.execute.call_count == 1
    conn.commit.assert_not_called()


def test_db_failure_is_raised_so_callback_can_be_retried():
    conn = MagicMock()
    conn.cursor.return_value.execute.side_effect = RuntimeError("db unavailable")

    @contextmanager
    def fake_connection():
        yield conn

    with patch.object(db, "get_db_connection", fake_connection), pytest.raises(RuntimeError):
        db.mark_outbox_result_for_client(
            tenant_id="tenant_1",
            client_id="client_1",
            request_id="req_1",
            action_index=0,
            success=True,
        )
