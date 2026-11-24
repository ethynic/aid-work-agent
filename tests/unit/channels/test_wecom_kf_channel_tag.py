"""渠道标识与旅行社提示词的分支条件必须一致，否则提示词分支会静默失效。"""

from pathlib import Path

import pytest

from src.saas.api.channel_routes import _KF_CHANNEL_EXTRA_SYSTEM_PROMPT

pytestmark = pytest.mark.channels

CHANNEL_TAG = "【渠道：微信客服】"
TRAVEL_PROMPT = Path(__file__).resolve().parents[3] / "subagents" / "travel-consultant" / "SUBAGENT.md"


def test_channel_hint_starts_with_fixed_tag():
    assert _KF_CHANNEL_EXTRA_SYSTEM_PROMPT.startswith(CHANNEL_TAG)


def test_travel_prompt_branch_depends_on_the_same_tag():
    text = TRAVEL_PROMPT.read_text(encoding="utf-8")
    assert f"对话中出现「{CHANNEL_TAG}」标识" in text
