"""
端到端测试：完整 Agent + 真实 LLM 交互场景

需要环境变量：QWEN_API_KEYS 或 ZHIPU_API_KEYS
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


@pytest.mark.skipif(
    not os.getenv("QWEN_API_KEYS") and not os.getenv("ZHIPU_API_KEYS"),
    reason="需要 LLM API 密钥",
)
class TestAgentFull:
    """完整 Agent 交互测试"""

    @pytest.mark.asyncio
    async def test_simple_conversation(self):
        """简单对话：用户打招呼，Agent 应正常回复"""
        from src.core.agent import Agent

        agent = Agent(is_master=True)
        session_id = f"test_session_{os.getpid()}"

        responses = []
        async for chunk in agent.process_message(
            user_input="你好，请回复'测试成功'",
            session_id=session_id,
        ):
            if chunk:
                responses.append(chunk)

        assert len(responses) > 0
        full_response = "".join(responses)
        assert len(full_response) > 0
