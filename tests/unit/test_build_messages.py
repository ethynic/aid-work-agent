"""
_build_messages / _reorder_messages_for_llm 回归测试

背景：chat_messages 单事务批量写入会让同轮消息 created_at 相同，若查询缺二级排序键，
返回顺序会错乱（tool 结果散落到 assistant(tool_calls) 前后、甚至夹进 user/assistant 消息），
导致 DeepSeek/OpenAI 报 400 "Messages with role 'tool' must be a response to a
preceding message with 'tool_calls'"。

_reorder_messages_for_llm 按 tool_call_id 重新配对，确保输出无论输入顺序如何都满足 API 约束。
"""

import pytest

from src.core.agent import Agent


pytestmark = pytest.mark.agent


def _tc(tc_id, name="x"):
    return {"id": tc_id, "type": "function", "function": {"name": name, "arguments": "{}"}}


def _is_valid_sequence(messages):
    """校验 LLM API 不变量：每个 tool 往前回溯遇到的最近 assistant 必须带 tool_calls 且
    包含该 tool_call_id，且中间不能夹非 tool 消息；每个 assistant(tool_calls) 后必须紧跟 tool。"""
    for i, m in enumerate(messages):
        if m["role"] == "tool":
            j = i - 1
            while j >= 0 and messages[j]["role"] == "tool":
                j -= 1
            if j < 0 or messages[j]["role"] != "assistant" or not messages[j].get("tool_calls"):
                return False, f"orphan tool at {i}"
            if m["tool_call_id"] not in {t["id"] for t in messages[j]["tool_calls"]}:
                return False, f"tool {m['tool_call_id']} not in preceding tool_calls at {i}"
        if m["role"] == "assistant" and m.get("tool_calls"):
            if i + 1 >= len(messages) or messages[i + 1]["role"] != "tool":
                return False, f"dangling tool_calls at {i}"
    return True, "ok"


class TestReorderMessages:
    def test_scrambled_parallel_tools_reassembled(self):
        """4 个并行 tool 结果散落到 assistant(tool_calls) 前后，应被收齐并按声明顺序连续输出。"""
        history = [
            {"role": "user", "content": "帮我查"},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},   # 散落在前
            {"role": "tool", "tool_call_id": "c2", "content": "r2"},
            {"role": "assistant", "content": "最终回复"},
            {"role": "assistant", "content": "", "tool_calls": [_tc("c1"), _tc("c2"), _tc("c3"), _tc("c4")]},
            {"role": "user", "content": "用户追问"},
            {"role": "tool", "tool_call_id": "c4", "content": "r4"},   # 散落在后
            {"role": "tool", "tool_call_id": "c3", "content": "r3"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        ok, msg = _is_valid_sequence(out)
        assert ok, msg

        # assistant(tool_calls) 紧跟 4 个 tool，按声明顺序 c1,c2,c3,c4
        idx = next(i for i, m in enumerate(out) if m["role"] == "assistant" and m.get("tool_calls"))
        assert [t["id"] for t in out[idx]["tool_calls"]] == ["c1", "c2", "c3", "c4"]
        assert [out[idx + k]["tool_call_id"] for k in range(1, 5)] == ["c1", "c2", "c3", "c4"]

    def test_orphan_tool_dropped(self):
        """没有对应 assistant(tool_calls) 的 tool 消息必须丢弃。"""
        history = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "ghost", "content": "no caller"},
            {"role": "assistant", "content": "你好"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        assert all(m["role"] != "tool" for m in out), "孤儿 tool 应被丢弃"
        ok, msg = _is_valid_sequence(out)
        assert ok, msg

    def test_dangling_tool_calls_downgraded(self):
        """assistant(tool_calls) 没有任何匹配 tool 结果时，降级为普通 assistant。"""
        history = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "想想", "tool_calls": [_tc("c1")]},
        ]
        out = Agent._reorder_messages_for_llm(history)
        # 不应出现带 tool_calls 的 assistant，也不应出现 tool
        assert all(not m.get("tool_calls") for m in out)
        assert all(m["role"] != "tool" for m in out)
        # content 保留为普通 assistant
        assert any(m["role"] == "assistant" and m["content"] == "想想" for m in out)
        ok, msg = _is_valid_sequence(out)
        assert ok, msg

    def test_normal_order_preserved(self):
        """正常顺序的输入应保持等价合法序列。"""
        history = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [_tc("c1")]},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
            {"role": "assistant", "content": "答"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        ok, msg = _is_valid_sequence(out)
        assert ok, msg
        assert [m["role"] for m in out] == ["user", "assistant", "tool", "assistant"]

    def test_system_skipped_and_empty_skipped(self):
        """system 消息跳过（不再转 user）；空 content 的 user/assistant 跳过。

        system 标记消息（如转人工标记 transfer_to_human_marker）由 _build_messages
        提取到 system_markers 并拼接到 system_prompt，不进入对话序列。此处直接调
        _reorder_messages_for_llm 时，system 消息应被跳过，避免转成 user 形成连续 user。
        """
        history = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": ""},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "你好"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        roles = [(m["role"], m["content"]) for m in out]
        assert roles == [("user", "你好")]
        # system 消息不应以任何角色残留
        assert all(m["content"] != "你是助手" for m in out)

    def test_system_between_assistant_and_user_skipped(self):
        """system 夹在 assistant 和 user 之间（转人工标记真实场景）→ 跳过，不产生连续 user。

        复现生产告警：assistant(房型价格) → system([已转人工]...) → user(帮我预定...)。
        若 system 转 user，会与后面的 user 形成连续 user，触发清洗并丢弃标记。
        """
        history = [
            {"role": "user", "content": "查房型"},
            {"role": "assistant", "content": "酒店房型价格"},
            {"role": "system", "content": "[已转人工] 该次请求已处理完成。"},
            {"role": "user", "content": "帮我预定"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        roles = [m["role"] for m in out]
        assert roles == ["user", "assistant", "user"], f"system 应被跳过，实际 {roles}"
        assert all(m["content"] != "[已转人工] 该次请求已处理完成。" for m in out)

    def test_reasoning_content_preserved(self):
        """DeepSeek 思考模式的 reasoning_content 在 assistant 消息上保留。"""
        history = [
            {"role": "assistant", "content": "", "tool_calls": [_tc("c1")], "reasoning_content": "思考"},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        asst = next(m for m in out if m["role"] == "assistant")
        assert asst["reasoning_content"] == "思考"

    def test_real_scrambled_session_shape(self):
        """复刻本次 bug 的乱序形态（多 assistant + 散落 tool + 夹入 user）必须输出合法序列。"""
        history = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "greet"},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
            {"role": "tool", "tool_call_id": "c2", "content": "r2"},
            {"role": "assistant", "content": "reply"},
            {"role": "assistant", "content": "", "tool_calls": [_tc("c1"), _tc("c2"), _tc("c3"), _tc("c4"), ]},
            {"role": "user", "content": "u2"},
            {"role": "tool", "tool_call_id": "c4", "content": "r4"},
            {"role": "tool", "tool_call_id": "c3", "content": "r3"},
        ]
        out = Agent._reorder_messages_for_llm(history)
        ok, msg = _is_valid_sequence(out)
        assert ok, msg
