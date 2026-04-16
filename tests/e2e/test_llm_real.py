"""
端到端测试：真实 LLM API 调用

需要环境变量：API_KEYS
运行：pytest -m e2e tests/e2e/test_llm_real.py
"""

import os

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.llm,
    pytest.mark.slow,
]


@pytest.mark.skipif(
    not os.getenv("API_KEYS"),
    reason="需要 LLM API 密钥",
)
class TestLLMRealAPI:
    """真实 LLM API 调用测试"""

    @pytest.mark.asyncio
    async def test_basic_chat(self):
        """基本聊天请求"""
        from src.llm.gateway import LLMGateway

        gateway = LLMGateway()
        result = await gateway.chat(
            messages=[{"role": "user", "content": "你好，请回复'测试成功'"}],
            temperature=0.1,
            max_tokens=50,
        )
        assert "content" in result
        assert result["content"]

    @pytest.mark.asyncio
    async def test_stream_chat(self):
        """流式聊天请求"""
        from src.llm.gateway import LLMGateway

        gateway = LLMGateway()
        chunks = []
        async for chunk in gateway.stream_chat(
            messages=[{"role": "user", "content": "请回复'流式测试成功'"}],
            temperature=0.1,
            max_tokens=50,
        ):
            chunks.append(chunk)
        assert len(chunks) > 0
