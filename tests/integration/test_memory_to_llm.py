"""
集成测试：Memory → LLM 消息格式全链路
"""

import pytest

from src.memory.short_term import ShortTermMemory


class TestMemoryToLlmPipeline:
    """Memory → LLM 消息格式转换全链路"""

    def test_full_conversation_flow(self, memory):
        """完整对话流程的消息格式"""
        sid = "s1"

        # 模拟完整对话
        memory.add(sid, "user", "你好")
        memory.add(sid, "assistant", "你好！有什么可以帮你的？")
        memory.add(sid, "user", "帮我发一封邮件")
        memory.add(sid, "assistant", "好的，请告诉我收件人地址。")

        msgs = memory.to_llm_messages(sid, system_prompt="你是一个企业助手。")

        # 第一条应该是 system prompt
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "你是一个企业助手。"

        # 后续消息交替
        assert len(msgs) == 5
        roles = [m["role"] for m in msgs[1:]]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_skill_summary_in_conversation(self, memory):
        """对话中穿插技能摘要"""
        sid = "s1"

        memory.add(sid, "user", "写一篇文章")
        memory.add_message(sid, {
            "role": "system",
            "content": "[技能执行记录] 使用技能「article-writing」完成。结果：已生成文章",
            "_skill_summary": True,
        })
        memory.add(sid, "assistant", "文章已生成，请查看。")

        msgs = memory.to_llm_messages(sid)

        # system prompt 没有，技能摘要转为 user
        assert len(msgs) == 3
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "user"  # 技能摘要转为 user
        assert msgs[2]["role"] == "assistant"

    def test_max_messages_with_system_prompt(self):
        """max_messages 限制不影响 system prompt"""
        mem = ShortTermMemory(max_messages=2)
        sid = "s1"

        for i in range(5):
            mem.add(sid, "user", f"msg{i}")
            mem.add(sid, "assistant", f"reply{i}")

        msgs = mem.to_llm_messages(sid, system_prompt="System")
        # system + max 2 messages (4 条消息中保留最后 2 条)
        assert msgs[0]["role"] == "system"
        assert len(msgs) <= 3
