"""
Agent._reorder_messages_for_llm 连续 user 兜底回归测试

背景（P0-3 兜底，详见 docs/research/wecom-kf-context-loss-research.md §3.3 / §7.6）：
历史脏数据 + 未改造渠道 + 合并 cancel 极端 race 都可能在 channel_messages 中产生
连续 user 消息（user → user → assistant）。这种序列被 LLM API 视为新轮次输入，
历史 assistant 上下文失效，agent 胡乱推断指代对象。

P0-3 在 _reorder_messages_for_llm 末尾增加兜底：丢弃前一条 user，仅保留最新一条。
这是「最后保险」——即使 P0-1 / P0-2 失效或脏数据回流，LLM 也不会看到连续 user。

本测试聚焦连续 user 兜底，与 test_build_messages.py 的 tool 序列重排测试互补。
"""

import pytest

from src.core.agent import Agent


pytestmark = pytest.mark.agent


def _roles(messages):
    """提取 [(role, content_preview), ...]，便于断言"""
    return [
        (m["role"], m["content"] if isinstance(m["content"], str) else str(m["content"]))
        for m in messages
    ]


class TestConsecutiveUserFallback:
    def test_no_consecutive_users_in_output(self):
        """连续两条 user → 输出中绝不出现连续 user（核心不变量），且保留最新一条。

        为什么重要：用户截图现象 #1 就是 channel_messages 里出现
        user(上次) → user(本次 "可以的") → 缺 assistant，LLM 把"可以的"
        误判为新轮次的独立指令，上下文彻底错乱。P0-3 兜底必须保证
        无论输入多脏，到达 LLM 的 messages 序列中都不会有连续 user。
        P0-2 进一步修复方向：丢弃较早的，保留最新一条 user（本次的"可以的"），
        因为最新一条才是用户本轮真正想问的。
        """
        history = [
            {"role": "user", "content": "保持 4 天"},
            {"role": "user", "content": "可以的"},
            {"role": "assistant", "content": "好的"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        roles = [m["role"] for m in out]
        # 核心不变量：输出中不出现连续 user
        for i in range(1, len(roles)):
            assert not (roles[i] == "user" and roles[i - 1] == "user"), (
                f"输出仍包含连续 user：{roles}"
            )
        # assistant 必须保留（不能因清洗丢失）
        assert "assistant" in roles, "清洗不应丢失 assistant 消息"
        # 方向性断言（P0-2 修复）：保留最新一条 user（"可以的"），丢弃较早的
        user_contents = [m["content"] for m in out if m["role"] == "user"]
        assert user_contents == ["可以的"], (
            f"P0-2 修复后应保留最新一条 user '可以的'，实际: {user_contents}"
        )

    def test_three_consecutive_users_collapsed_to_one(self):
        """连续三条 user → 输出中只剩一条 user（保留最新一条）。

        为什么重要：极端脏数据可能产生超过两条连续 user，兜底必须能处理任意长度。
        P0-2 修复方向后：保留最新一条（"第三条"），丢弃前两条。
        """
        history = [
            {"role": "user", "content": "第一条"},
            {"role": "user", "content": "第二条"},
            {"role": "user", "content": "第三条"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        roles = [m["role"] for m in out]
        assert roles.count("user") == 1, (
            f"三条连续 user 应被折叠为一条；实际 user 数: {roles.count('user')}，"
            f"完整序列: {roles}"
        )
        # 方向性断言：保留最新一条
        user_contents = [m["content"] for m in out if m["role"] == "user"]
        assert user_contents == ["第三条"], (
            f"P0-2 修复后应保留最新一条 user '第三条'，实际: {user_contents}"
        )

    def test_normal_alternating_sequence_unchanged(self):
        """正常 user/assistant 交替序列不应触发兜底，原样输出。

        为什么重要：兜底只针对异常序列，不能误伤正常对话。
        user → assistant → user → assistant 应保持原样（中间有 assistant 隔开，不连续）。
        """
        history = [
            {"role": "user", "content": "问1"},
            {"role": "assistant", "content": "答1"},
            {"role": "user", "content": "问2"},
            {"role": "assistant", "content": "答2"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        roles = _roles(out)
        assert roles == [
            ("user", "问1"), ("assistant", "答1"),
            ("user", "问2"), ("assistant", "答2"),
        ], "正常交替序列应原样输出，不应触发兜底"

    def test_sequence_with_tool_calls_not_affected_by_user_fallback(self):
        """含 tool_calls 的合法序列：user → assistant(tool_calls) → tool → assistant → user 不受兜底影响。

        为什么重要：兜底只检查连续 user，不应干扰 tool 序列的重排逻辑。
        带 tool_calls 的对话流必须保持完整。
        """
        tc = {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
        history = [
            {"role": "user", "content": "查一下"},
            {"role": "assistant", "content": "", "tool_calls": [tc]},
            {"role": "tool", "tool_call_id": "c1", "content": "结果"},
            {"role": "assistant", "content": "这是答案"},
            {"role": "user", "content": "追问"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        # 不应丢失 tool 序列，也不应触发连续 user（中间隔了 assistant/tool/assistant）
        roles = [m["role"] for m in out]
        assert roles == ["user", "assistant", "tool", "assistant", "user"]
        # tool_call_id 必须保留
        tool_msg = next(m for m in out if m["role"] == "tool")
        assert tool_msg["tool_call_id"] == "c1"

    def test_consecutive_users_in_middle_of_history_cleaned(self):
        """连续 user 出现在历史中间（不是开头）也必须被清洗，保留最新一条。

        为什么重要：脏数据可能出现在历史任意位置，兜底必须对全序列生效，
        不能只处理开头的连续 user。P0-2 修复方向后保留最新一条（"脏数据B"）。
        """
        history = [
            {"role": "user", "content": "正常问题"},
            {"role": "assistant", "content": "正常回答"},
            {"role": "user", "content": "脏数据A"},  # 较早的，应被丢弃
            {"role": "user", "content": "脏数据B"},  # 最新一条，保留
            {"role": "assistant", "content": "后续回答"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        roles = _roles(out)
        assert roles == [
            ("user", "正常问题"),
            ("assistant", "正常回答"),
            ("user", "脏数据B"),  # P0-2 修复后保留最新一条
            ("assistant", "后续回答"),
        ], "中间的连续 user 也应被清洗，只保留最新一条"

    def test_empty_user_between_users_does_not_create_consecutive(self):
        """连续 user 中间夹着一条空 content 的 user（被跳过）→ 输出仍不出现连续 user。

        为什么重要：空 content 的 user 在 _reorder 的第一阶段会被跳过（不入 messages 列表），
        所以兜底看到的 messages 序列是 [user(A), user(B)]，仍会被清洗为单一 user。
        保证各种边界输入都不会让连续 user 漏到 LLM。
        """
        history = [
            {"role": "user", "content": "A"},
            {"role": "user", "content": ""},  # 空 content，被跳过
            {"role": "user", "content": "B"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        roles = [m["role"] for m in out]
        # 核心不变量：不出现连续 user
        for i in range(1, len(roles)):
            assert not (roles[i] == "user" and roles[i - 1] == "user"), (
                f"空 user 夹击下仍出现连续 user：{roles}"
            )
        assert roles.count("user") >= 1, "至少保留一条非空 user"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
