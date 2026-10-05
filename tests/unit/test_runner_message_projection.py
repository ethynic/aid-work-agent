"""Public display projection preserves visible fields and private source rows."""
from copy import deepcopy
import json

from src.api.message_projection import public_conversation_messages


def test_public_history_filters_internal_rows_and_keeps_legacy_and_runner_display_without_mutating_dal():
    rows = [
        {"message_id": "system", "role": "system", "content": "private-system"},
        {"message_id": "tool", "role": "tool", "content": "private-tool-output"},
        {"message_id": "internal", "role": "assistant", "content": "", "metadata": {
            "tool_calls": [{"id": "private-call", "function": {"arguments": "private-tool-arguments"}}]}},
        {"message_id": "legacy", "role": "assistant", "content": "old visible text", "metadata": {
            "legacy_display_extension": {"original": "retained"}}},
        {"message_id": "control:user", "session_id": "owned", "role": "user", "content": "visible answer", "metadata": {
            "runner_id": "runner", "runner_queue_order": 5, "control_id": "control", "accepted_at": "fixture-time",
            "attachments": [{"file_id": "file-fixture", "name": "visible.txt", "type": "file", "size": 9,
                             "content": "private-inline-base64", "url": "private-source-url"}]}},
        {"message_id": "final", "session_id": "owned", "role": "assistant", "content": "visible final", "metadata": {
            "runner_id": "runner", "reasoning_content": "private-reasoning", "unknown": "private-extension",
            "images": [{"file_id": "image-fixture", "display_name": "visible.png", "download_url": "/api/files/fixture",
                        "placement": "after_text", "content": "private-image-bytes"}],
            "imagesPlacement": "after_text", "progressMessages": [{"type": "tool_start", "toolCallId": "call",
                "toolName": "read", "toolArgs": {"nested": {"password": "fixture-password"}},
                "result": {"success": True, "summary": "visible progress", "raw": "private-result"}}],
            "cancelled": True}},
    ]
    original = deepcopy(rows)
    result = public_conversation_messages(rows)
    assert [item["message_id"] for item in result] == ["legacy", "control:user", "final"]
    assert result[0] == original[3]
    assert result[1]["metadata"]["attachments"] == [{"file_id": "file-fixture", "name": "visible.txt", "type": "file", "size": 9}]
    display = result[2]["metadata"]
    assert display["images"][0]["download_url"] == "/api/files/fixture"
    assert display["images"][0]["display_name"] == "visible.png" and display["imagesPlacement"] == "after_text"
    assert display["progressMessages"][0]["toolArgs"] == {"nested": {"password": "***"}}
    assert display["progressMessages"][0]["result"] == {"success": True, "summary": "visible progress"}
    assert display["cancelled"] is True
    assert "private-" not in json.dumps(result) and "fixture-password" not in json.dumps(result)
    assert rows == original
    result[0]["metadata"]["legacy_display_extension"]["original"] = "mutated caller copy"
    assert rows == original
