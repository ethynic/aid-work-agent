"""会话存档方向判定测试。"""

import pytest

from src.channels.wecom_personal_rpa.archive.direction import (
    MessageDirection,
    classify_archive_message,
)


@pytest.mark.parametrize(
    ("sender", "recipients", "roomid", "expected", "peer", "conversation"),
    [
        ("external_a", ["self_a"], None, MessageDirection.INBOUND_EXTERNAL, "external_a", "dm:external_a"),
        ("self_a", ["external_a"], None, MessageDirection.OUTBOUND_SELF, "external_a", "dm:external_a"),
        ("self_a", ["external_a", "external_b"], None, MessageDirection.OUTBOUND_SELF, None, None),
        ("external_a", ["self_a"], "room_1", MessageDirection.INBOUND_GROUP, "external_a", "room_1"),
        ("self_a", ["external_a"], "room_1", MessageDirection.OUTBOUND_SELF, None, "room_1"),
        ("external_a", ["someone_else"], None, MessageDirection.DIRECTION_UNKNOWN, None, None),
    ],
)
def test_classify_archive_message(sender, recipients, roomid, expected, peer, conversation):
    decision = classify_archive_message(
        self_ids={"self_a", "alias_a"},
        from_user=sender,
        tolist=recipients,
        roomid=roomid,
    )
    assert decision.direction == expected
    assert decision.peer_id == peer
    assert decision.conversation_id == conversation


@pytest.mark.parametrize(
    ("self_ids", "sender", "recipients", "reason"),
    [
        (set(), "external", ["self"], "self_identity_missing"),
        ({"self"}, None, ["self"], "sender_missing"),
        ({"self"}, "external", "self", "recipient_type_invalid"),
    ],
)
def test_classify_archive_message_fails_closed(self_ids, sender, recipients, reason):
    decision = classify_archive_message(
        self_ids=self_ids, from_user=sender, tolist=recipients
    )
    assert decision.direction == MessageDirection.DIRECTION_UNKNOWN
    assert decision.reason == reason
