"""企业微信会话存档消息方向判定（纯函数）。"""

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional


class MessageDirection(str, Enum):
    INBOUND_EXTERNAL = "inbound_external"
    INBOUND_GROUP = "inbound_group"
    OUTBOUND_SELF = "outbound_self"
    DIRECTION_UNKNOWN = "direction_unknown"


@dataclass(frozen=True)
class DirectionDecision:
    direction: MessageDirection
    peer_id: Optional[str]
    conversation_id: Optional[str]
    reason: str


def _normalize_ids(values: Iterable[str]) -> set[str]:
    return {str(value).strip() for value in values if isinstance(value, str) and value.strip()}


def classify_archive_message(
    *, self_ids: Iterable[str], from_user: object, tolist: object, roomid: object = None
) -> DirectionDecision:
    """按权威成员身份判定方向；任何歧义均失败关闭。"""
    identities = _normalize_ids(self_ids)
    if not identities:
        return DirectionDecision(MessageDirection.DIRECTION_UNKNOWN, None, None, "self_identity_missing")
    if not isinstance(from_user, str) or not from_user.strip():
        return DirectionDecision(MessageDirection.DIRECTION_UNKNOWN, None, None, "sender_missing")
    if not isinstance(tolist, list) or any(not isinstance(value, str) for value in tolist):
        return DirectionDecision(MessageDirection.DIRECTION_UNKNOWN, None, None, "recipient_type_invalid")

    sender = from_user.strip()
    recipients = [value.strip() for value in tolist if value.strip()]
    room = roomid.strip() if isinstance(roomid, str) else ""
    if room:
        if sender in identities:
            return DirectionDecision(MessageDirection.OUTBOUND_SELF, None, room, "sender_is_self")
        return DirectionDecision(MessageDirection.INBOUND_GROUP, sender, room, "group_sender_external")

    if sender in identities:
        peers = sorted(set(recipients) - identities)
        reason = "sender_is_self" if len(peers) == 1 else ("multiple_peers" if len(peers) > 1 else "peer_missing")
        return DirectionDecision(
            MessageDirection.OUTBOUND_SELF,
            peers[0] if len(peers) == 1 else None,
            f"dm:{peers[0]}" if len(peers) == 1 else None,
            reason,
        )
    if any(value in identities for value in recipients):
        return DirectionDecision(MessageDirection.INBOUND_EXTERNAL, sender, f"dm:{sender}", "self_in_recipient_list")
    return DirectionDecision(MessageDirection.DIRECTION_UNKNOWN, None, None, "recipient_not_self")
