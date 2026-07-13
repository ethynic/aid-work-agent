from src.api.monitor import _trace_display_fields


def test_display_state_priority_failed_message_interrupted_internal():
    failed = _trace_display_fields({
        "status": "failed", "error_message": "boom",
        "user_message_id": "msg_1", "output": "partial",
    })
    assert failed["display_state"] == "failed"
    assert failed["is_intermediate"] is False

    message = _trace_display_fields({
        "status": "completed", "user_message_id": "msg_1", "output": None,
        "source_type": "wecom_personal_rpa",
    })
    assert message["display_state"] == "message"
    assert message["is_persisted_message"] is True

    interrupted = _trace_display_fields({
        "status": "cancelled", "metadata": {
            "termination_reason": "message_merged", "merge_role": "merged_follower",
        }, "source_type": "wecom_personal_rpa",
    })
    assert interrupted == {
        "termination_reason": "message_merged",
        "merge_role": "merged_follower",
        "is_persisted_message": False,
        "is_intermediate": True,
        "display_state": "interrupted",
    }

    internal = _trace_display_fields({
        "status": "completed", "source_type": "chat", "output": None,
    })
    assert internal["display_state"] == "internal"
