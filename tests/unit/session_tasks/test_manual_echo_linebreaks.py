"""Successful send evidence and bounded OCR echo matching, without desktop I/O."""
from unittest.mock import MagicMock

import pytest

from src.session_tasks import decisions


@pytest.mark.parametrize("sent,observed,manual", [
    (["hello world"], ["hello\n world"], False),
    (["hello world"], ["hello\r\n world"], False),
    (["hello world"], ["helloworld"], False),
    (["hello world"], ["hello worl"], True),
    (["hello world"], ["hello worlds"], False),
    (["hello world"], [" hello world"], False),
    (["hello world"], ["hello world "], False),
    (["hello world"], ["hello world", "hello\n world"], True),
    (["hello world", "hello world"], ["hello world", "hello\n world"], False),
    ([], ["hello\n world"], True),
    (["hello\nworld"], ["helloworld"], False),
    (["hello\nworld"], ["hello\nworld"], False),
])
def test_echo_keeps_success_capacity_with_ocr_variants(monkeypatch, sent, observed, manual):
    conn = MagicMock()
    cursor = conn.cursor.return_value
    decisions_rows = [{"reply_text_id": f"d{i}", "sent_count": 1} for i in range(len(sent))]
    message_rows = [{"text_id": f"m{i}"} for i in range(len(observed))]
    cursor.fetchall.side_effect = [[], decisions_rows, message_rows]
    payloads = {**{f"d{i}": {"text": text} for i, text in enumerate(sent)},
                **{f"m{i}": {"text": text} for i, text in enumerate(observed)}}
    monkeypatch.setattr(decisions, "load_text", lambda conn, tenant, task, key, **kw: payloads[key])
    assert decisions.check_manual_intervention(
        conn, "tenant", "task", [{"text": observed[-1]}],
        scenario_key="weixin.conversation.v1",
    ) is manual
    assert "dl.state='succeeded'" in cursor.execute.call_args_list[0].args[0]


@pytest.mark.parametrize("has_peer", [False, True])
def test_reply_only_calls_model_for_current_batch_peer(monkeypatch, has_peer):
    from datetime import datetime, timezone

    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.fetchone.return_value = {"exists": 1} if has_peer else None
    connection_factory = MagicMock()
    connection_factory.return_value.__enter__.return_value = connection
    transcript = MagicMock(return_value=[{"message_id": "old", "sender": "peer", "text": "historical"}])
    model = MagicMock(return_value="model-output")
    finalize = MagicMock(return_value=True)
    hooks = MagicMock()
    hooks.validate_decision_output.return_value = {"action": "wait"}
    monkeypatch.setattr(decisions, "_conn", connection_factory)
    monkeypatch.setattr(decisions, "_load_transcript", transcript)
    monkeypatch.setattr(decisions, "_call_model_with_budget", model)
    monkeypatch.setattr(decisions, "_finalize_decision", finalize)
    decisions._process_reply("tenant", {"id": "task"}, {},
        {"id": "decision", "lease_owner": "lease", "input_version": 7},
        hooks, {}, datetime.now(timezone.utc), MagicMock())
    assert cursor.execute.call_args.args[1] == ("tenant", "task", 7)
    sql = cursor.execute.call_args.args[0]
    assert "m.input_version=%s" in sql and "b.status='accepted'" in sql
    assert "m.sender='peer'" in sql
    assert model.call_count == int(has_peer)
    assert transcript.call_count == int(has_peer)
    assert hooks.build_decision_messages.call_count == int(has_peer)
    assert finalize.call_args.kwargs["action"] == "wait"
    assert finalize.call_args.kwargs["status"] == "ready"
    if not has_peer:
        assert finalize.call_args.kwargs["evidence"]["reason_code"] == "no_new_peer"


def test_unreadable_current_peer_cannot_become_no_peer_wait(monkeypatch):
    from datetime import datetime, timezone
    from src.session_tasks.constants import SessionTaskError

    conn = MagicMock()
    cursor = conn.cursor.return_value
    cursor.fetchone.return_value = {"exists": 1}
    cursor.fetchall.return_value = [{"message_id": "new", "sender": "peer", "input_version": 7, "text_id": "bad"}]
    factory = MagicMock()
    factory.return_value.__enter__.return_value = conn
    monkeypatch.setattr(decisions, "_conn", factory)
    monkeypatch.setattr(decisions, "load_text", MagicMock(side_effect=SessionTaskError("unreadable", "CRYPTO_UNAVAILABLE", 503)))
    model, finalize = MagicMock(), MagicMock()
    monkeypatch.setattr(decisions, "_call_model_with_budget", model)
    monkeypatch.setattr(decisions, "_finalize_decision", finalize)
    with pytest.raises(SessionTaskError) as error:
        decisions._process_reply("tenant", {"id": "task"}, {},
            {"id": "decision", "lease_owner": "lease", "input_version": 7},
            MagicMock(), {}, datetime.now(timezone.utc), MagicMock())
    assert error.value.code == "CRYPTO_UNAVAILABLE"
    model.assert_not_called()
    finalize.assert_not_called()
