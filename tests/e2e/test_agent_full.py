"""
端到端测试：完整 Agent + 真实 LLM 交互场景

需要配置当前 LLM_PROVIDER 对应的 *_API_KEYS（或启用的备用 provider 密钥）
运行：pytest -m e2e tests/e2e/test_agent_full.py
"""

import os

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.agent,
    pytest.mark.llm,
    pytest.mark.slow,
]


class TestAgentFull:
    """完整 Agent 交互测试"""

    @pytest.mark.asyncio
    async def test_simple_conversation(self):
        """简单对话：用户打招呼，Agent 应正常回复"""
        from src.core.agent import Agent

        agent = Agent(is_master=True)
        session_id = f"test_session_{os.getpid()}"

        responses = []
        async for event in agent.process_message(
            user_input="你好，请回复'测试成功'",
            session_id=session_id,
        ):
            if event.get("type") == "response":
                data = event.get("data", "")
                if data:
                    responses.append(data)

        assert len(responses) > 0
        full_response = "".join(responses)
        assert len(full_response) > 0
