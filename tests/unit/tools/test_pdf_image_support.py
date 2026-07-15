"""PDF 图片支持 + 落盘闭环 + 错误脱敏 单测。

覆盖：
- _embed_local_images_as_data_uri：本地 <img src> → base64 data URI；远程/data: 不动；缺文件保留
- _handle_md_to_pdf / _handle_html_to_pdf：tenant 注入时调 inline_images（html 走 syntax="html"）；
  tenant 缺省时不调（向后兼容）
- _merge_results：超长 content/markdown 走 spill_large_content 落盘，返回 file_path + truncated
- execute：非 FileNotFoundError 异常经 sanitize_error 脱敏，不泄漏 str(e)

不依赖真实 Playwright/Chromium。
"""
import base64
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.tools


# 1×1 PNG（合法字节，避免依赖 PIL）
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


# ============================================================
# _embed_local_images_as_data_uri
# ============================================================

def test_embed_local_image_converts_to_data_uri(tmp_path):
    from src.tools.pdf.pdf_writer import _embed_local_images_as_data_uri

    img = tmp_path / "t.png"
    img.write_bytes(_PNG_1x1)
    html = f'<p>正文</p><p><img src="{img}" alt="图"/></p>'

    out = _embed_local_images_as_data_uri(html)

    assert "data:image/png;base64," in out
    assert f'src="{img}"' not in out  # 原 src 被替换
    assert "<p>正文</p>" in out  # 其余内容保留


def test_embed_keeps_remote_and_data_uri_untouched():
    from src.tools.pdf.pdf_writer import _embed_local_images_as_data_uri

    html = '<img src="https://example.com/a.png"><img src="data:image/png;base64,QUJD">'
    assert _embed_local_images_as_data_uri(html) == html


def test_embed_missing_local_file_keeps_src():
    from src.tools.pdf.pdf_writer import _embed_local_images_as_data_uri

    html = '<img src="/definitely/not/here/x.png">'
    assert _embed_local_images_as_data_uri(html) == html


# ============================================================
# _handle_md_to_pdf / _handle_html_to_pdf：inline_images 集成
# ============================================================

@pytest.mark.asyncio
async def test_md_to_pdf_handler_inlines_images_when_tenant_set():
    """tenant 注入时 → inline_images 被调（markdown 语法），md_to_pdf 收到解析后的文本。"""
    from src.tools.pdf.pdf_process_tool import PipelineContext, PdfProcessTool

    tool = PdfProcessTool()
    tool.set_tenant_id("t1")
    tool.set_user_id("u1")
    ctx = PipelineContext(content="# 标题\n\n![图](file_id:file_x)")

    seen = {}

    async def fake_inline(text, tenant_id=None, user_id=None, syntax="markdown", fetch_remote=True):
        seen["syntax"] = syntax
        seen["tenant_id"] = tenant_id
        return ("# 标题\n\n![图](/local/resolved.png)", [])

    with patch("src.tools._image_inliner.inline_images", new=fake_inline), \
         patch("src.tools.pdf.pdf_writer.md_to_pdf", return_value={"success": True, "file_path": "/tmp/x.pdf"}) as md2pdf, \
         patch.object(tool, "_validate_generated_result", side_effect=lambda r, p: r):
        result = await tool._handle_md_to_pdf(ctx, {})

    assert seen.get("syntax") == "markdown"
    assert seen.get("tenant_id") == "t1"
    assert "/local/resolved.png" in md2pdf.call_args.kwargs["md_text"]
    assert result["success"] is True


@pytest.mark.asyncio
async def test_html_to_pdf_handler_uses_html_syntax_inliner():
    """html_to_pdf + tenant → inline_images 以 syntax='html' 调用。"""
    from src.tools.pdf.pdf_process_tool import PipelineContext, PdfProcessTool

    tool = PdfProcessTool()
    tool.set_tenant_id("t1")
    ctx = PipelineContext(content='<img src="file_id:file_x">')

    seen = {}

    async def fake_inline(text, tenant_id=None, user_id=None, syntax="markdown", fetch_remote=True):
        seen["syntax"] = syntax
        return ('<img src="/local/x.png">', [])

    with patch("src.tools._image_inliner.inline_images", new=fake_inline), \
         patch("src.tools.pdf.pdf_writer.html_to_pdf", return_value={"success": True, "file_path": "/tmp/x.pdf"}) as h2p, \
         patch.object(tool, "_validate_generated_result", side_effect=lambda r, p: r):
        await tool._handle_html_to_pdf(ctx, {})

    assert seen.get("syntax") == "html"
    assert "/local/x.png" in h2p.call_args.kwargs["html_text"]


@pytest.mark.asyncio
async def test_md_to_pdf_handler_skips_inliner_without_tenant():
    """无 tenant_id → 不调 inline_images，行为向后兼容。"""
    from src.tools.pdf.pdf_process_tool import PipelineContext, PdfProcessTool

    tool = PdfProcessTool()  # 不注入 tenant
    ctx = PipelineContext(content="# 标题\n\n![图](file_id:file_x)")

    with patch("src.tools._image_inliner.inline_images", new=AsyncMock()) as inline_mock, \
         patch("src.tools.pdf.pdf_writer.md_to_pdf", return_value={"success": True, "file_path": "/tmp/x.pdf"}), \
         patch.object(tool, "_validate_generated_result", side_effect=lambda r, p: r):
        await tool._handle_md_to_pdf(ctx, {})

    inline_mock.assert_not_called()


@pytest.mark.asyncio
async def test_md_to_pdf_handler_inline_failure_falls_back_gracefully():
    """inline_images 抛异常 → 记 warning 回退原文，md_to_pdf 仍用原始文本生成。"""
    from src.tools.pdf.pdf_process_tool import PipelineContext, PdfProcessTool

    tool = PdfProcessTool()
    tool.set_tenant_id("t1")
    original = "# 标题\n\n![图](file_id:file_x)"
    ctx = PipelineContext(content=original)

    with patch("src.tools._image_inliner.inline_images", new=AsyncMock(side_effect=RuntimeError("boom"))), \
         patch("src.tools.pdf.pdf_writer.md_to_pdf", return_value={"success": True, "file_path": "/tmp/x.pdf"}) as md2pdf, \
         patch.object(tool, "_validate_generated_result", side_effect=lambda r, p: r):
        await tool._handle_md_to_pdf(ctx, {})

    # 回退到原文（file_id 引用仍在）
    assert "file_id:file_x" in md2pdf.call_args.kwargs["md_text"]


# ============================================================
# _merge_results：落盘闭环（#34 Phase 3b）
# ============================================================

def test_merge_results_spills_oversized_read_content():
    """read 的 content > 2000 → 全文落盘，返回 file_path + truncated + full_size。"""
    from src.tools.pdf.pdf_process_tool import PipelineContext, PdfProcessTool

    tool = PdfProcessTool()
    big = "A" * 5000
    ctx = PipelineContext()
    ctx.results = [{"operation": "read", "success": True, "content": big, "pages": [], "metadata": {}}]

    merged = tool._merge_results(ctx)

    assert merged["content_truncated"] is True
    assert merged.get("content_file_path")
    assert os.path.exists(merged["content_file_path"])
    assert merged["content_full_size"] == 5000
    assert len(merged["content"]) <= 2000 + 3  # preview(2000) + 后缀(3)
    # 落盘的是完整原文
    assert Path(merged["content_file_path"]).read_text(encoding="utf-8") == big


def test_merge_results_spills_oversized_markdown():
    """pdf_to_md 的 markdown > 5000 → 落盘（.md）。"""
    from src.tools.pdf.pdf_process_tool import PipelineContext, PdfProcessTool

    tool = PdfProcessTool()
    big = "# title\n" + "x" * 6000
    ctx = PipelineContext()
    ctx.results = [{"operation": "pdf_to_md", "success": True, "markdown": big, "source": "text_extract"}]

    merged = tool._merge_results(ctx)

    assert merged["markdown_truncated"] is True
    assert merged.get("markdown_file_path")
    assert os.path.exists(merged["markdown_file_path"])
    assert merged["markdown_full_size"] == len(big)


def test_merge_results_small_content_not_spilled():
    """content ≤ 2000 → 原样返回，无 truncated / file_path。"""
    from src.tools.pdf.pdf_process_tool import PipelineContext, PdfProcessTool

    tool = PdfProcessTool()
    ctx = PipelineContext()
    ctx.results = [{"operation": "read", "success": True, "content": "短内容", "pages": [], "metadata": {}}]

    merged = tool._merge_results(ctx)

    assert merged["content"] == "短内容"
    assert "content_truncated" not in merged
    assert "content_file_path" not in merged


# ============================================================
# execute：错误脱敏（B2）
# ============================================================

@pytest.mark.asyncio
async def test_execute_generic_exception_is_sanitized():
    """非 FileNotFoundError 异常 → sanitize_error 兜底，不泄漏 str(e) 原文。"""
    from src.tools.pdf.pdf_process_tool import PdfProcessTool

    tool = PdfProcessTool()
    secret = "internal detail /etc/secret_token"
    tool._handle_read = AsyncMock(side_effect=ValueError(secret))

    with patch.object(tool, "_resolve_task", new=AsyncMock(return_value={"task": "read", "params": {}})):
        result = await tool.execute(context="读取文件", file_paths=["x.pdf"])

    assert result["success"] is False
    assert "secret" not in result["error"]
    assert "internal detail" not in result["error"]
