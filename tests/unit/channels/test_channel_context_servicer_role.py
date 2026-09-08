"""
人工客服消息 LLM 上下文角色转换单元测试

背景（2026-09-08 修复）：
人工客服消息落库为 role=user + metadata.source=servicer + content 前缀 "[人工客服] "。
此前重建 LLM 上下文时原样透传为 user 消息，导致两个问题：
1. servicer 消息与真实 user 消息形成连续 user，被 _reorder_messages_for_llm 的
   P0-3 兜底整条丢弃（2026-08-31 实证："[人工客服] 好的，已经帮您预定好了" 被丢弃，
   AI 随后答称"我这边看不到实际进度"）。
2. 即使未被丢弃，LLM 也无从得知该前缀约定，可能把人工客服发言当客户自言自语。

修复：在上下文重建层（不改落库存储）把 source=servicer 的 user 消息转为
role=assistant，保留 "[人工客服] " 前缀供 LLM 区分人工同事与自身发言。两个入口：
- Agent._load_channel_history（agent 主循环渠道会话）
- ChannelSessionManager.get_conversation_context（wecom 渠道 channel_routes 路径）
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.core.agent import Agent
from src.channels.session import channel_session_manager

pytestmark = pytest.mark.agent


def _channel_msg(role, content, source=None, created_at="2026-09-07 14:00:00"):
    meta = {"source": source} if source else None
    return {
        "role": role,
        "content": content,
        "metadata": meta,
        "created_at": created_at,
    }


def _make_agent(max_messages=30):
    """最小 Agent 实例（跳过 __init__），仅提供 _load_channel_history 所需属性。"""
    agent = Agent.__new__(Agent)
    agent.memory = SimpleNamespace(
        short_term=SimpleNamespace(max_messages=max_messages)
    )
    return agent


class TestLoadChannelHistoryServicerRole:
    def _load(self, msgs, current_input=""):
        agent = _make_agent()
        with patch.object(
            channel_session_manager, "get_messages", return_value=list(msgs)
        ):
            return agent._load_channel_history("sess1", current_input)

    def test_servicer_message_converted_to_assistant(self):
        """source=servicer 的 user 消息 → role=assistant，content 前缀保留。"""
        history = self._load([
            _channel_msg("user", "帮我订酒店"),
            _channel_msg("assistant", "已经帮你转接人工客服了"),
            _channel_msg("user", "[人工客服] 好的，已经帮您预定好了", source="servicer"),
        ])
        roles = [(m["role"], m["content"]) for m in history]
        assert ("assistant", "[人工客服] 好的，已经帮您预定好了") in roles
        # 不应存在 servicer 来源的 user 消息
        assert all(
            not (m["role"] == "user" and "人工客服" in m["content"]) for m in history
        )

    def test_customer_human_message_stays_user(self):
        """真实客户消息（source=customer_human）保持 user 角色，不受影响。"""
        history = self._load([
            _channel_msg("user", "在吗", source="customer_human"),
        ])
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "在吗"

    def test_plain_user_message_stays_user(self):
        """无 metadata 的普通 user 消息保持 user 角色。"""
        history = self._load([
            _channel_msg("user", "想去天眼"),
        ])
        assert history[0]["role"] == "user"

    def test_system_marker_unchanged(self):
        """system 标记消息（转人工 marker）保持 system 角色，由 _build_messages 提取。"""
        history = self._load([
            _channel_msg(
                "system",
                "[已转人工] 用户此前已请求转人工并已转接给人工客服（sunchen）",
                source="servicer",  # 即使 metadata 混入 source 也不影响 system 角色
            ),
        ])
        assert history[0]["role"] == "system"

    def test_no_consecutive_user_after_conversion(self):
        """servicer 消息紧跟真实 user 消息时，转换后不再形成连续 user。

        回归 2026-08-31 场景：servicer 确认消息 + 客户追问，修复前 servicer
        消息在 P0-3 兜底被整条丢弃。
        """
        history = self._load([
            _channel_msg("user", "帮我订酒店"),
            _channel_msg("assistant", "已经帮你转接人工客服了"),
            _channel_msg("user", "[人工客服] 好的，已经帮您预定好了", source="servicer"),
            _channel_msg("user", "酒店帮我订好了吗", source="customer_human"),
        ])
        messages = Agent._reorder_messages_for_llm(history)
        roles = [m["role"] for m in messages]
        for i in range(1, len(roles)):
            assert not (roles[i] == "user" and roles[i - 1] == "user"), f"连续 user: {roles}"
        # servicer 确认消息必须保留在最终序列中
        assert any(
            m["role"] == "assistant" and "已经帮您预定好了" in m["content"]
            for m in messages
        )


class TestGetConversationContextServicerRole:
    def _context(self, msgs):
        with patch.object(
            channel_session_manager, "get_messages", return_value=list(msgs)
        ):
            return channel_session_manager.get_conversation_context("sess1", 20)

    def test_servicer_message_converted_to_assistant(self):
        context = self._context([
            _channel_msg("user", "[人工客服] 已经帮您预定好了", source="servicer"),
            _channel_msg("user", "好的", source="customer_human"),
        ])
        assert context[0] == {
            "role": "assistant",
            "content": "[人工客服] 已经帮您预定好了",
        }
        assert context[1]["role"] == "user"

    def test_customer_human_message_stays_user(self):
        context = self._context([
            _channel_msg("user", "请问价格", source="customer_human"),
        ])
        assert context[0]["role"] == "user"

    def test_assistant_message_unchanged(self):
        context = self._context([
            _channel_msg("assistant", "已经帮你转接人工客服了"),
        ])
        assert context[0]["role"] == "assistant"
