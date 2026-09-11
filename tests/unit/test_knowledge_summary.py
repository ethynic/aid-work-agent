"""generate_summary 单元测试：必须走 chat_lite 关思考通道，防止思考 token 吃掉 max_tokens 预算导致空摘要"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.unit


def _make_service():
    from src.knowledge.service import KnowledgeBaseService
    with patch("src.knowledge.service.TextChunker"):
        return KnowledgeBaseService()


class TestGenerateSummary:
    @pytest.mark.asyncio
    async def test_uses_chat_lite(self):
        svc = _make_service()
        mock_gateway = MagicMock()
        mock_gateway.chat_lite = AsyncMock(return_value={
            "content": "这是一个文档摘要",
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })
        with patch.object(type(svc), "llm_gateway", new_callable=lambda: property(lambda self: mock_gateway)):
            summary = await svc.generate_summary(text="文档正文内容", title="测试文档")

        assert summary == "这是一个文档摘要"
        mock_gateway.chat_lite.assert_awaited_once()
        mock_gateway.chat.assert_not_called()
        kwargs = mock_gateway.chat_lite.await_args.kwargs
        assert kwargs["max_tokens"] == 500
        assert svc._last_summary_usage["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_empty_text_skips_llm(self):
        svc = _make_service()
        summary = await svc.generate_summary(text="   ", title="测试文档")
        assert summary == ""
        assert svc._last_summary_usage is None

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        svc = _make_service()
        mock_gateway = MagicMock()
        mock_gateway.chat_lite = AsyncMock(side_effect=RuntimeError("api down"))
        with patch.object(type(svc), "llm_gateway", new_callable=lambda: property(lambda self: mock_gateway)):
            summary = await svc.generate_summary(text="文档正文内容", title="测试文档")
        assert summary == ""
        assert svc._last_summary_usage is None
