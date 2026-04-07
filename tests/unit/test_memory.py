"""
ShortTermMemory 单元测试
"""

import pytest
from datetime import datetime

from src.memory.short_term import ShortTermMemory


class TestShortTermMemoryAddAndGet:
    """核心添加和获取操作"""

    def test_add_and_get_context_returns_messages(self, memory):
        sid = "s1"
        memory.add(sid, "user", "Hello")
        memory.add(sid, "assistant", "Hi there")
        ctx = memory.get_context(sid)
        assert len(ctx) == 2
        assert ctx[0]["role"] == "user"
        assert ctx[0]["content"] == "Hello"

    def test_get_context_returns_empty_for_unknown_session(self, memory):
        assert memory.get_context("nonexistent") == []


class TestShortTermMemoryMaxMessages:
    """滑动窗口限制"""

    def test_oldest_messages_evicted_when_over_limit(self):
        mem = ShortTermMemory(max_messages=3)
        sid = "s1"
        for i in range(5):
            mem.add(sid, "user", f"msg{i}")
        ctx = mem.get_context(sid)
        assert len(ctx) == 3
        assert ctx[0]["content"] == "msg2"  # msg0 和 msg1 被淘汰

    def test_exact_max_messages(self):
        mem = ShortTermMemory(max_messages=3)
        sid = "s1"
        for i in range(3):
            mem.add(sid, "user", f"msg{i}")
        ctx = mem.get_context(sid)
        assert len(ctx) == 3


class TestShortTermMemoryToLlmMessages:
    """to_llm_messages 格式转换"""

    def test_system_prompt_prepended(self, memory):
        sid = "s1"
        memory.add(sid, "user", "hi")
        msgs = memory.to_llm_messages(sid, system_prompt="You are helpful")
        assert msgs[0] == {"role": "system", "content": "You are helpful"}

    def test_skill_summary_preserved_as_user_message(self, memory):
        sid = "s1"
        memory.add(sid, "user", "do something")
        memory.add_message(sid, {
            "role": "system",
            "content": "[技能执行记录] 使用技能「test」完成任务。结果：已生成文章",
            "_skill_summary": True,
        })
        msgs = memory.to_llm_messages(sid)
        assert len(msgs) == 2
        assert msgs[1]["role"] == "user"
        assert "技能执行记录" in msgs[1]["content"]

    def test_regular_system_messages_excluded(self, memory):
        sid = "s1"
        memory.add(sid, "user", "hi")
        memory.add_message(sid, {"role": "system", "content": "ignored"})
        msgs = memory.to_llm_messages(sid)
        assert len(msgs) == 1


class TestShortTermMemorySessionCleanup:
    """会话清理"""

    def test_clear_removes_session(self, memory):
        sid = "s1"
        memory.add(sid, "user", "hi")
        memory.clear(sid)
        assert memory.get_context(sid) == []

    def test_message_count(self, memory):
        sid = "s1"
        assert memory.get_message_count(sid) == 0
        memory.add(sid, "user", "hi")
        assert memory.get_message_count(sid) == 1

    def test_get_active_sessions(self, memory):
        memory.add("s1", "user", "hi")
        memory.add("s2", "user", "hello")
        active = memory.get_active_sessions()
        assert "s1" in active
        assert "s2" in active


class TestShortTermMemoryRecentMessages:
    """最近消息获取"""

    def test_get_recent_messages(self, memory):
        sid = "s1"
        for i in range(10):
            memory.add(sid, "user", f"msg{i}")
        recent = memory.get_recent_messages(sid, limit=3)
        assert len(recent) == 3
        assert recent[-1]["content"] == "msg9"

    def test_get_last_user_message(self, memory):
        sid = "s1"
        memory.add(sid, "user", "hello")
        memory.add(sid, "assistant", "hi")
        memory.add(sid, "user", "world")
        last = memory.get_last_user_message(sid)
        assert last["content"] == "world"

    def test_get_last_assistant_message(self, memory):
        sid = "s1"
        memory.add(sid, "user", "hello")
        memory.add(sid, "assistant", "hi there")
        last = memory.get_last_assistant_message(sid)
        assert last["content"] == "hi there"
