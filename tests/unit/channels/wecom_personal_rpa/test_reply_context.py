"""RPA 回复上下文协议测试。"""

import pytest

from src.channels.wecom_personal_rpa.schemas import ActionEnvelope
from src.saas.api.wecom_personal_rpa_routes import _normalize_conversation_search_name


@pytest.mark.parametrize(
    ("display_name", "expected"),
    [
        ("陆伟@微信", "陆伟"),
        (" Alice ", "Alice"),
        ("名称@微信中间", "名称@微信中间"),
        ("   ", None),
        (None, None),
    ],
)
def test_normalize_conversation_search_name_is_conservative(display_name, expected):
    assert _normalize_conversation_search_name(display_name) == expected


def test_old_action_envelope_without_reply_context_is_compatible():
    envelope = ActionEnvelope.model_validate(
        {
            "request_id": "req_old",
            "session_id": "sid_old",
            "account_id": "acct",
            "conversation_id": "stable_conv",
            "actions": [{"type": "send_text", "text": "reply"}],
        }
    )
    assert envelope.reply_context is None
