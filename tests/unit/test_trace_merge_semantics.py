from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from src.core.trace_collector import TraceCollector
from src.core.trace_persist import (
    _do_persist,
    _pending_metadata_updates,
    update_trace_metadata,
)
from src.core.trace_semantics import is_interrupted_trace
from src.services.session_record import SessionRecordService


def test_collector_and_record_service_write_structured_merge_metadata():
    record = SessionRecordService("sid", "user", "input", tenant_id="t1")
    collector = TraceCollector("sid", "t1", "user", "input", "wecom_personal_rpa")
    record.trace_collector = collector
    with patch("src.core.trace_persist.update_trace_metadata") as update:
        record.set_trace_merge_semantics(
            termination_reason="message_merged", merge_role="merged_follower"
        )
    assert collector.trace.metadata == {
        "termination_reason": "message_merged",
        "merge_role": "merged_follower",
    }
    assert collector.trace.user_message_id is None
    update.assert_called_once_with(collector.trace_id, collector.trace.metadata)


def test_failed_trace_is_never_interrupted_even_with_explicit_reason():
    assert not is_interrupted_trace({
        "metadata": {"termination_reason": "message_merged"},
        "status": "failed",
        "source_type": "wecom_personal_rpa",
    })


def test_strict_historical_interrupted_fallback_and_exclusions():
    base = {
        "metadata": {}, "status": "completed", "source_type": "wecom_personal_rpa",
        "subagent_id": None, "error_message": None, "output": None,
        "user_message_id": None,
    }
    assert is_interrupted_trace(base)
    for override in (
        {"output": "有效输出"},
        {"error_message": "boom"},
        {"status": "failed"},
        {"subagent_id": "sub_1"},
        {"source_type": "chat"},
        {"user_message_id": "msg_1"},
    ):
        assert not is_interrupted_trace({**base, **override})


@contextmanager
def _logs_cm(cursor):
    yield cursor


def test_update_trace_metadata_uses_jsonb_merge():
    cursor = MagicMock()
    cursor.rowcount = 1
    with patch("src.db.database.get_logs_connection", return_value=_logs_cm(cursor)):
        update_trace_metadata("tr_1", {"merge_role": "merged_owner"})
    sql, params = cursor.execute.call_args[0]
    assert "COALESCE(metadata" in sql
    assert "|| %s::jsonb" in sql
    assert params[1] == "tr_1"
    cursor.commit.assert_called_once()


def test_update_before_insert_is_replayed_by_trace_upsert():
    update_cursor = MagicMock()
    update_cursor.rowcount = 0
    with patch("src.db.database.get_logs_connection", return_value=_logs_cm(update_cursor)):
        update_trace_metadata("tr_race", {"merge_role": "merged_follower"})
    assert _pending_metadata_updates["tr_race"] == {
        "merge_role": "merged_follower"
    }

    trace = TraceCollector("sid", "t1", "user", "input", "wecom_personal_rpa").trace
    trace.trace_id = "tr_race"
    persist_cursor = MagicMock()
    with patch("src.db.database.get_logs_connection", return_value=_logs_cm(persist_cursor)):
        _do_persist(trace)

    replay_sql, replay_params = persist_cursor.execute.call_args_list[1][0]
    assert "COALESCE(metadata" in replay_sql
    assert "merged_follower" in replay_params[0]
    assert "tr_race" not in _pending_metadata_updates
