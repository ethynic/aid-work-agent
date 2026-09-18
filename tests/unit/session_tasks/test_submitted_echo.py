"""Submitted echo ownership does not depend on OCR text equality."""
from unittest.mock import MagicMock
import json

import pytest

from src.session_tasks import decisions


def test_submitted_echo_uses_persisted_id_not_ocr_text():
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = [{"message_id": "self-1", "text_id": "text-1"}]
    assert not decisions.check_manual_intervention(
        conn, "tenant", "task", [{"local_message_id": "self-1", "text": "OCR changed"}],
        scenario_key="weixin.conversation.v1",
    )
    assert conn.cursor.return_value.execute.call_count == 1


def test_submitted_capacity_does_not_exempt_another_message():
    conn = MagicMock()
    conn.cursor.return_value.fetchall.side_effect = [[{"message_id": "self-1", "text_id": "text-1"}], []]
    assert decisions.check_manual_intervention(
        conn, "tenant", "task", [{"local_message_id": "self-2", "text": "OCR changed"}],
        scenario_key="weixin.conversation.v1",
    )


def test_submission_query_is_scoped_to_first_timely_unique_self_batch():
    conn = MagicMock()
    conn.cursor.return_value.fetchall.return_value = []
    assert decisions._submitted_echo_messages(
        conn, "tenant", "task", scenario_key="weixin.conversation.v1"
    ) == {}
    sql, args = conn.cursor.return_value.execute.call_args.args
    # B1.2：回执策略（mode/context/scenario）由描述器提供为 SQL 参数（值与原硬编码一致）
    assert args[:2] == ("tenant", "task")
    assert args[2:] == ("submission", "weixin_name", "weixin.conversation.v1")
    for clause in ["dl.phase='submitted'", "receipt_context", "b.input_version>d.input_version",
                   "b.created_at>=i.created_at", "LIMIT 1", "INTERVAL '60 seconds'",
                   "s.sender='self')=1", "COUNT(DISTINCT decision_id)=1"]:
        assert clause in sql


@pytest.mark.parametrize("mode,allowed", [("normal", True), ("two_self", False), ("late", False),
                                         ("later_batch", False), ("unknown", False), ("two_commands", False)])
def test_submitted_echo_sql_window_and_capacity(mode, allowed):
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cursor = conn.cursor()
        definitions = {
            "session_task_decisions": "id text, tenant_id text, task_id text, input_version int",
            "session_task_execution_links": "tenant_id text, decision_id text, delivery_id text, invocation_id text",
            "desktop_automation_deliveries": "id text, tenant_id text, state text, phase text, finished_at timestamptz",
            "local_tool_invocations": "id text, tenant_id text, created_at timestamptz, arguments_json jsonb, business_ref jsonb",
            "session_task_batches": "tenant_id text, task_id text, batch_id text, input_version int, synthetic boolean, created_at timestamptz",
            "session_task_messages": "tenant_id text, task_id text, batch_id text, sender text, message_id text, text_id text",
        }
        # Transaction-local shadow tables exercise the actual query without business fixtures.
        for table, columns in definitions.items():
            cursor.execute(f"CREATE TEMP TABLE {table} ({columns}) ON COMMIT DROP")
        cursor.execute("INSERT INTO session_task_decisions VALUES ('d','tenant','task',1)")
        cursor.execute("INSERT INTO session_task_execution_links VALUES ('tenant','d','dl','i')")
        cursor.execute("INSERT INTO desktop_automation_deliveries VALUES ('dl','tenant',%s,%s,NOW())",
                       ("unknown" if mode == "unknown" else "succeeded", "unknown" if mode == "unknown" else "submitted"))
        cursor.execute("INSERT INTO local_tool_invocations VALUES ('i','tenant',NOW()-INTERVAL '1 second',%s::jsonb,%s::jsonb)",
                       (json.dumps({"receipt_mode": "submission", "receipt_context": "weixin_name"}), json.dumps({"scenario_key": "weixin.conversation.v1"})))
        cursor.execute("INSERT INTO session_task_batches VALUES ('tenant','task','b',2,FALSE,NOW()+%s*INTERVAL '1 second')", (61 if mode == "late" else 1,))
        cursor.execute("INSERT INTO session_task_messages VALUES ('tenant','task','b','self','m','t')")
        if mode == "two_self":
            cursor.execute("INSERT INTO session_task_messages VALUES ('tenant','task','b','self','m2','t2')")
        if mode == "later_batch":
            cursor.execute("INSERT INTO session_task_batches VALUES ('tenant','task','earlier',2,FALSE,NOW())")
        if mode == "two_commands":
            cursor.execute("INSERT INTO session_task_decisions VALUES ('d2','tenant','task',1)")
            cursor.execute("INSERT INTO session_task_execution_links VALUES ('tenant','d2','dl','i')")
        assert decisions._submitted_echo_messages(
            conn, "tenant", "task", scenario_key="weixin.conversation.v1"
        ) == ({"m": "t"} if allowed else {})
        conn.rollback()
