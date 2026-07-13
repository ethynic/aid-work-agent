"""WordProcessTool tenant_id 注入单元测试。

验证 _handle_md_to_word 在以下场景下把 tenant_id 正确传给 convert_async：
- set_tenant_id 注入优先
- ContextVar 兜底（get_current_tenant_id）
- 无注入无 ContextVar → tenant_id=None

所有用例 mock convert_async 与 save_as，避免真正调用 Pandoc。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.tools


def _make_tool():
    """构造一个跳过 router 初始化的 WordProcessTool。"""
    from src.tools.word.word_process_tool import WordProcessTool
    return WordProcessTool()


@patch("src.tools.word.md_to_word.save_as", return_value={"file_path": "/tmp/x.docx"})
@patch("src.tools.word.md_to_word.convert_async", new_callable=AsyncMock)
class TestHandleMdToWordTenantId:
    """_handle_md_to_word 的 tenant_id 注入测试。"""

    @pytest.mark.asyncio
    async def test_handle_md_to_word_passes_tenant_id_from_injection(
        self, mock_convert, mock_save
    ):
        """set_tenant_id 注入优先于 ContextVar。"""
        from src.tools.word.word_process_tool import PipelineContext

        tool = _make_tool()
        tool.set_tenant_id("t1")
        tool.set_user_id("u1")
        mock_convert.return_value = MagicMock(name="Document")

        from src.tools.word.word_process_tool import WordProcessTool
        with patch.object(WordProcessTool, "_extract_markdown_body", side_effect=lambda x: x), \
             patch.object(WordProcessTool, "_extract_title", return_value="标题"), \
             patch("src.saas.context.get_current_tenant_id", return_value="t_context"):
            await tool._handle_md_to_word(PipelineContext(context="# 标题\n正文"), {})

        kwargs = mock_convert.call_args.kwargs
        assert kwargs["tenant_id"] == "t1"
        assert kwargs["user_id"] == "u1"

    @pytest.mark.asyncio
    async def test_handle_md_to_word_passes_tenant_id_from_contextvar(
        self, mock_convert, mock_save
    ):
        """无注入时从 ContextVar 拿 tenant_id。"""
        from src.tools.word.word_process_tool import PipelineContext, WordProcessTool

        tool = _make_tool()
        mock_convert.return_value = MagicMock(name="Document")

        with patch.object(WordProcessTool, "_extract_markdown_body", side_effect=lambda x: x), \
             patch.object(WordProcessTool, "_extract_title", return_value="标题"), \
             patch("src.saas.context.get_current_tenant_id", return_value="t2"), \
             patch("src.saas.context.get_current_user_id", return_value="u2"):
            await tool._handle_md_to_word(PipelineContext(context="# 标题\n正文"), {})

        kwargs = mock_convert.call_args.kwargs
        assert kwargs["tenant_id"] == "t2"
        assert kwargs["user_id"] == "u2"

    @pytest.mark.asyncio
    async def test_handle_md_to_word_no_tenant_id(self, mock_convert, mock_save):
        """无注入无 ContextVar → tenant_id=None（向后兼容）。"""
        from src.tools.word.word_process_tool import PipelineContext, WordProcessTool

        tool = _make_tool()
        mock_convert.return_value = MagicMock(name="Document")

        with patch.object(WordProcessTool, "_extract_markdown_body", side_effect=lambda x: x), \
             patch.object(WordProcessTool, "_extract_title", return_value="标题"), \
             patch("src.saas.context.get_current_tenant_id", return_value=None), \
             patch("src.saas.context.get_current_user_id", return_value=None):
            await tool._handle_md_to_word(PipelineContext(context="# 标题\n正文"), {})

        kwargs = mock_convert.call_args.kwargs
        assert kwargs["tenant_id"] is None
        assert kwargs["user_id"] is None
