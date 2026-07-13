"""md_to_word image_inliner 集成单元测试。

验证 src/tools/word/md_to_word.py 的 sync/async 拆分：
- convert_async 传 tenant_id 时调用 inline_images
- convert_async 不传 tenant_id 时跳过 inline_images（向后兼容）
- inline_images 抛异常时 fallback 到原始 md_text 并记 warning
- 同步 convert() 不传 tenant_id 时行为与改造前一致

所有用例 mock inline_images 与 _convert_sync（避免真正调用 Pandoc）。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.tools


# ============================================================
# convert_async：tenant_id 路径
# ============================================================

class TestConvertAsync:
    """convert_async 行为测试。"""

    @pytest.mark.asyncio
    async def test_convert_async_with_tenant_id_calls_inliner(self):
        """传 tenant_id 时 inline_images 被调用，返回 Document 正常。"""
        from src.tools.word import md_to_word

        mock_doc = MagicMock(name="Document")
        with patch("src.tools._image_inliner.inline_images", new_callable=AsyncMock) as mock_inline, \
             patch.object(md_to_word, "_convert_sync", return_value=mock_doc) as mock_sync:
            mock_inline.return_value = ("![alt](/local/path.png)", [MagicMock()])

            doc = await md_to_word.convert_async(
                "# title\n\n![alt](file_id:file_xxx)",
                tenant_id="tenant_a",
                user_id="user_b",
            )

            mock_inline.assert_awaited_once()
            args, kwargs = mock_inline.call_args
            # 兼容位置/关键字两种调用方式
            assert "tenant_a" in args or kwargs.get("tenant_id") == "tenant_a"
            assert kwargs.get("user_id") == "user_b"
            # _convert_sync 收到 inline 后的文本
            assert mock_sync.call_args.args[0] == "![alt](/local/path.png)"
            assert doc is mock_doc

    @pytest.mark.asyncio
    async def test_convert_async_without_tenant_id_skips_inliner(self):
        """不传 tenant_id 时 inline_images 不被调用（向后兼容）。"""
        from src.tools.word import md_to_word

        mock_doc = MagicMock(name="Document")
        with patch("src.tools._image_inliner.inline_images", new_callable=AsyncMock) as mock_inline, \
             patch.object(md_to_word, "_convert_sync", return_value=mock_doc) as mock_sync:
            doc = await md_to_word.convert_async("# title\n\ntext")

            mock_inline.assert_not_awaited()
            # _convert_sync 收到原始文本
            assert mock_sync.call_args.args[0] == "# title\n\ntext"
            assert doc is mock_doc

    @pytest.mark.asyncio
    async def test_convert_async_inliner_failure_falls_back(self):
        """inline_images 抛异常时记 warning 并 fallback 到原始 md_text。"""
        from src.tools.word import md_to_word

        mock_doc = MagicMock(name="Document")
        original_md = "# title\n\n![alt](file_id:file_xxx)"
        with patch("src.tools._image_inliner.inline_images", new_callable=AsyncMock) as mock_inline, \
             patch.object(md_to_word, "_convert_sync", return_value=mock_doc) as mock_sync, \
             patch.object(md_to_word.logger, "warning") as mock_warn:
            mock_inline.side_effect = RuntimeError("registry down")

            doc = await md_to_word.convert_async(original_md, tenant_id="tenant_a")

            mock_inline.assert_awaited_once()
            # 走 fallback：_convert_sync 收到原始 md_text，不抛异常
            assert mock_sync.call_args.args[0] == original_md
            assert doc is mock_doc
            mock_warn.assert_called_once()
            assert "inline_images" in mock_warn.call_args.args[0]


# ============================================================
# 同步 convert：向后兼容
# ============================================================

class TestConvertSyncBackwardCompat:
    """同步 convert() 不传 tenant_id 时行为与改造前一致。"""

    def test_convert_sync_no_tenant_id_backward_compatible(self):
        """同步 convert() 不传 tenant_id 时直接走 _convert_sync。"""
        from src.tools.word import md_to_word

        mock_doc = MagicMock(name="Document")
        with patch("src.tools._image_inliner.inline_images", new_callable=AsyncMock) as mock_inline, \
             patch.object(md_to_word, "_convert_sync", return_value=mock_doc) as mock_sync:
            doc = md_to_word.convert("# title\n\ntext", template="default.docx")

            mock_inline.assert_not_called()
            mock_sync.assert_called_once_with(
                "# title\n\ntext", template="default.docx", title="", author=""
            )
            assert doc is mock_doc
