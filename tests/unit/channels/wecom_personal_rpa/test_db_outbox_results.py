"""outbox 多 action 回执的持久化合并测试。"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.channels.wecom_personal_rpa import db


def _run(row, *, action_index, success=True, started_at=None, executed_at=None):
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
            started_at=started_at,
            executed_at=executed_at,
        )
    return result, cursor, conn


def _row(action_results=None):
    return {
        "id": "act_1",
        "actions": [{"type": "noop"}, {"type": "noop"}, {"type": "noop"}],
        "action_results": action_results or {},
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=1),
    }


def test_out_of_order_success_is_persisted_without_early_completion():
    result, cursor, conn = _run(_row(), action_index=2)
    assert result is True
    update = cursor.execute.call_args_list[1]
    assert update.args[1][0] == '{"2": "succeeded"}'
    assert update.args[1][1] is None
    conn.commit.assert_called_once()


def test_first_result_persists_real_execution_started_at_idempotently():
    started = datetime.now(timezone.utc) - timedelta(seconds=10)
    result, cursor, _ = _run(
        _row(), action_index=0, started_at=started,
        executed_at=started + timedelta(seconds=1),
    )
    assert result is True
    sql = cursor.execute.call_args_list[1].args[0]
    params = cursor.execute.call_args_list[1].args[1]
    assert "send_started_at" in sql
    assert params[1:4] == (started, started, started)


@pytest.mark.parametrize(
    "started_at,created_at",
    [
        (datetime.now(), datetime.now(timezone.utc) - timedelta(minutes=1)),
        (
            datetime.now(timezone.utc) + timedelta(minutes=6),
            datetime.now(timezone.utc) - timedelta(minutes=1),
        ),
        (
            datetime.now(timezone.utc) - timedelta(minutes=10),
            datetime.now(timezone.utc),
        ),
    ],
    ids=["naive", "future", "before_outbox"],
)
def test_invalid_started_at_is_dropped_without_changing_business_result(
    started_at, created_at
):
    row = _row()
    row["created_at"] = created_at
    result, cursor, conn = _run(row, action_index=0, started_at=started_at)
    assert result is True
    params = cursor.execute.call_args_list[1].args[1]
    assert params[1:4] == (None, None, None)
    assert params[4] is None
    conn.commit.assert_called_once()


def test_out_of_order_results_keep_earliest_valid_started_at_via_sql_least():
    first = datetime.now(timezone.utc) - timedelta(seconds=5)
    result, cursor, _ = _run(_row(), action_index=2, started_at=first)
    assert result is True
    update_sql = cursor.execute.call_args_list[1].args[0]
    assert "LEAST(send_started_at, %s)" in update_sql
    assert cursor.execute.call_args_list[1].args[1][1:4] == (first, first, first)


def test_all_indices_success_required_for_succeeded():
    result, cursor, _ = _run(
        _row({"0": "succeeded", "2": "succeeded"}), action_index=1
    )
    assert result is True
    assert cursor.execute.call_args_list[1].args[1][4] == "succeeded"


def test_any_failure_immediately_marks_failed():
    result, cursor, _ = _run(_row({"2": "succeeded"}), action_index=0, success=False)
    assert result is True
    params = cursor.execute.call_args_list[1].args[1]
    assert params[4] == "failed"
    assert params[6] == "masked"


def test_failed_envelope_accepts_later_aborted_action_result():
    result, cursor, conn = _run(
        _row({"0": "failed"}), action_index=1, success=False
    )
    assert result is True
    select_sql = cursor.execute.call_args_list[0].args[0]
    update_sql = cursor.execute.call_args_list[1].args[0]
    assert "'failed'" in select_sql
    assert "'failed'" in update_sql
    assert cursor.execute.call_args_list[1].args[1][0] == '{"0": "failed", "1": "failed"}'
    conn.commit.assert_called_once()


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


def test_postgres_init_and_incremental_migrations_keep_outbox_fields_in_sync():
    for path in ("deploy/init-postgres.sql", "deploy/db_update.sql"):
        sql = open(path, encoding="utf-8").read()
        assert "send_started_at TIMESTAMP" in sql
        assert "reply_digests JSONB" in sql
        assert "ADD COLUMN IF NOT EXISTS target_peer_id" in sql
        assert "ADD COLUMN IF NOT EXISTS reply_digest" in sql
        assert "ADD COLUMN IF NOT EXISTS wecom_user_id" in sql


def test_recent_reply_digest_query_is_parameterized_and_fully_scoped():
    cursor = MagicMock()
    cursor.fetchone.return_value = {"matched": 1}
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def fake_connection():
        yield conn

    with patch.object(db, "get_db_connection", fake_connection):
        assert db.has_recent_completed_outbox_reply(
            "tenant_1", "account_1", "peer_1", "digest_1", 120
        )

    sql, params = cursor.execute.call_args.args
    assert "tenant_id=%s" in sql and "account_id=%s" in sql
    assert "target_peer_id=%s" in sql and "status='succeeded'" in sql
    assert "completed_at >=" in sql and "reply_digests @> %s::jsonb" in sql
    assert params == (
        "tenant_1", "account_1", "peer_1", '["digest_1"]', "digest_1", 120
    )
