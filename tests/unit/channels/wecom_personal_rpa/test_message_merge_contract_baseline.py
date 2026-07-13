"""RPA Phase 1 稳定会话与附件输入行为契约。"""

from src.models.message import Attachment
from src.saas.api.wecom_personal_rpa_routes import (
    _build_rpa_attachment_inputs,
    _resolve_stable_conversation_id,
)


def test_same_stable_identity_survives_local_conversation_id_change():
    binding = {"stable_id": "wm_stable"}
    assert _resolve_stable_conversation_id("local_old", None, binding) == "wm_stable"
    assert _resolve_stable_conversation_id("local_new", None, binding) == "wm_stable"


def test_stable_identity_fallback_order_and_isolation_inputs():
    assert _resolve_stable_conversation_id("local", "sender_id", None) == "sender_id"
    assert _resolve_stable_conversation_id("local", None, None) == "local"


def test_attachment_inputs_preserve_event_and_agent_fields():
    attachment = Attachment(
        type="image",
        url="https://files.example/a.png",
        name="a.png",
        size=12,
        mime_type="image/png",
    )
    metadata, agent_inputs = _build_rpa_attachment_inputs([attachment], "evt_1")
    assert metadata == [{
        "type": "image",
        "url": "https://files.example/a.png",
        "name": "a.png",
        "size": 12,
        "mime_type": "image/png",
        "event_id": "evt_1",
    }]
    assert agent_inputs == [{
        "type": "image",
        "url": "https://files.example/a.png",
        "name": "a.png",
        "size": 12,
        "mime_type": "image/png",
    }]
