"""
PDF 文档处理工具单元测试

测试 PdfProcessTool 及其子模块（mock PyMuPDF、pdfplumber、pymupdf4llm 等）
"""

import os
import sys
import tempfile
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = pytest.mark.tools


def _mock_fitz(doc_mock=None):
    """创建 mock fitz 模块"""
    mock = MagicMock()
    if doc_mock:
        mock.open.return_value = doc_mock
    return mock


def _mock_path_obj(path_str, exists=True, stem="", name=""):
    """创建 mock Path 对象"""
    mock = MagicMock()
    mock.__str__ = lambda self: path_str
    mock.__fspath__ = lambda self: path_str
    mock.exists.return_value = exists
    mock.stem = stem or Path(path_str).stem
    mock.name = name or Path(path_str).name
    mock.suffix = Path(path_str).suffix
    mock.__truediv__ = lambda self, other: _mock_path_obj(str(Path(path_str) / other))
    return mock


def _mock_doc(pages_text=None, page_count=None, metadata=None):
    """创建 mock fitz.Document"""
    doc = MagicMock()
    texts = pages_text or ["Page content"] * (page_count or 1)
    doc.page_count = page_count or len(texts)
    doc.metadata = metadata or {}

    pages = []
    for t in texts:
        p = MagicMock()
        p.get_text.return_value = t
        pages.append(p)

    doc.__getitem__ = lambda self, idx: pages[idx]
    doc.close = MagicMock()
    return doc


# =============================================================================
# 工具定义和参数验证
# =============================================================================

class TestPdfProcessToolDefinition:
    """PdfProcessTool 工具定义测试"""

    def test_tool_name(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()
        assert tool.name == "pdf_process"

    def test_tool_display_name(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()
        assert tool.display_name == "PDF文档处理"

    def test_tool_definition_has_schema(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()
        defn = tool.to_tool_definition()
        assert defn["name"] == "pdf_process"
        assert "input_schema" in defn
        schema = defn["input_schema"]
        properties = schema.get("properties", {})
        assert "instruction" in properties
        assert "content" in properties
        assert "content_type" in properties
        assert "output_name" in properties
        assert "context" in properties
        assert "file_paths" in properties
        assert "task" not in properties
        assert "params" not in properties

    def test_tool_has_description(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()
        assert len(tool.description) > 50
        assert "PDF" in tool.description
        assert "触发规则" in tool.description

    def test_task_type_all(self):
        from src.tools.pdf.pdf_process_tool import TaskType
        expected = {
            "read", "read_tables", "ocr", "pdf_to_md",
            "md_to_pdf", "html_to_pdf", "docx_to_pdf",
            "merge", "split", "extract_pages",
            "inspect", "render_pages", "validate",
            "clean_metadata", "add_watermark", "protect",
            "compress", "extract_images", "rotate",
        }
        assert TaskType.ALL == expected


# =============================================================================
# Pipeline 执行测试（通过 mock router 直接测试各 handler）
# =============================================================================

class TestPdfProcessToolExecution:
    """工具执行测试"""

    @pytest.mark.asyncio
    async def test_execute_no_context_no_files(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "", "params": {}, "error": "缺少 context 和 file_paths"}
        tool._router = mock_router

        result = await tool.execute()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_router_returns_invalid_task(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "unknown_op", "params": {}, "error": ""}
        tool._router = mock_router

        result = await tool.execute(context="测试")
        assert result["success"] is False
        assert "无效操作" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_router_exception(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.side_effect = Exception("LLM down")
        tool._router = mock_router

        result = await tool.execute(context="读取文件", file_paths=["/tmp/test.pdf"])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_read_missing_file(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "read", "params": {}}
        tool._router = mock_router

        result = await tool.execute(context="读取文件", file_paths=["/nonexistent.pdf"])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_md_to_pdf_no_context(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "md_to_pdf", "params": {}}
        tool._router = mock_router

        result = await tool.execute(file_paths=["/nonexistent.md"])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_html_to_pdf_no_context(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "html_to_pdf", "params": {}}
        tool._router = mock_router

        result = await tool.execute()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_read_no_file_paths(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "read", "params": {}}
        tool._router = mock_router

        result = await tool.execute(context="读取PDF")
        assert result["success"] is False
        assert "file_paths" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_merge_insufficient_files(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "merge", "params": {}}
        tool._router = mock_router

        result = await tool.execute(context="合并PDF", file_paths=["/tmp/a.pdf"])
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_split_no_ranges(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "split", "params": {}}
        tool._router = mock_router

        result = await tool.execute(context="拆分PDF", file_paths=["/tmp/a.pdf"])
        assert result["success"] is False
        assert "ranges" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_extract_pages_no_pages(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "extract_pages", "params": {}}
        tool._router = mock_router

        result = await tool.execute(context="提取页面", file_paths=["/tmp/a.pdf"])
        assert result["success"] is False
        assert "pages" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_docx_to_pdf_no_file(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "docx_to_pdf", "params": {}}
        tool._router = mock_router

        result = await tool.execute(context="Word转PDF")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_read_tables_no_file(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "read_tables", "params": {}}
        tool._router = mock_router

        result = await tool.execute(context="提取表格")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_ocr_no_file(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "ocr", "params": {}}
        tool._router = mock_router

        result = await tool.execute(context="OCR识别")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_execute_pdf_to_md_no_file(self):
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "pdf_to_md", "params": {}}
        tool._router = mock_router

        result = await tool.execute(context="PDF转Markdown")
        assert result["success"] is False


# =============================================================================
# PdfReader 测试（使用 sys.modules mock fitz/pdfplumber）
# =============================================================================

class TestPdfReader:
    """pdf_reader 模块测试"""

    def test_read_text_success(self):
        """成功读取 PDF 文本"""
        from src.tools.pdf.pdf_reader import read_text

        doc = _mock_doc(
            pages_text=["Page 1 content", "Page 2 content"],
            metadata={"title": "Test", "author": "Author", "creationDate": "2026-01-01", "modDate": ""},
        )
        mock_fitz = _mock_fitz(doc)

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = read_text("/fake/test.pdf")

        assert result["success"] is True
        assert "Page 1 content" in result["content"]
        assert "Page 2 content" in result["content"]
        assert len(result["pages"]) == 2
        assert result["metadata"]["title"] == "Test"

    def test_read_text_with_specific_pages(self):
        """只读取指定页面"""
        from src.tools.pdf.pdf_reader import read_text

        doc = _mock_doc(pages_text=["P0", "P1", "P2", "P3", "P4"])
        mock_fitz = _mock_fitz(doc)

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = read_text("/fake/test.pdf", pages=[2])

        assert result["success"] is True
        assert result["pages"][0]["page"] == 3  # 0-indexed → 1-indexed display
        assert result["pages"][0]["text"] == "P2"

    def test_read_text_out_of_range_pages(self):
        """超出范围的页码被跳过"""
        from src.tools.pdf.pdf_reader import read_text

        doc = _mock_doc(pages_text=["Only one page"])
        mock_fitz = _mock_fitz(doc)

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = read_text("/fake/test.pdf", pages=[0, 99])

        assert result["success"] is True
        assert len(result["pages"]) == 1  # 只有 page 0 有效

    def test_read_text_file_error(self):
        """文件打开失败"""
        from src.tools.pdf.pdf_reader import read_text

        mock_fitz = MagicMock()
        mock_fitz.open.side_effect = Exception("文件损坏")

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = read_text("/fake/corrupt.pdf")

        assert result["success"] is False
        assert "读取PDF失败" in result["error"]

    def test_extract_tables_success(self):
        """成功提取表格"""
        from src.tools.pdf.pdf_reader import extract_tables

        mock_page = MagicMock()
        mock_page.extract_tables.return_value = [
            [["Header1", "Header2"], ["Value1", "Value2"]]
        ]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = lambda self: self
        mock_pdf.__exit__ = MagicMock(return_value=False)

        mock_pp = MagicMock()
        mock_pp.open.return_value = mock_pdf

        with patch.dict("sys.modules", {"pdfplumber": mock_pp}):
            result = extract_tables("/fake/test.pdf")

        assert result["success"] is True
        assert result["count"] == 1
        assert result["tables"][0]["page"] == 1
        assert result["tables"][0]["data"][0] == ["Header1", "Header2"]

    def test_extract_tables_error(self):
        """表格提取失败"""
        from src.tools.pdf.pdf_reader import extract_tables

        mock_pp = MagicMock()
        mock_pp.open.side_effect = Exception("pdfplumber error")

        with patch.dict("sys.modules", {"pdfplumber": mock_pp}):
            result = extract_tables("/fake/test.pdf")

        assert result["success"] is False
        assert "提取表格失败" in result["error"]

    def test_extract_tables_with_pages(self):
        """指定页面提取表格"""
        from src.tools.pdf.pdf_reader import extract_tables

        mock_page0 = MagicMock()
        mock_page0.extract_tables.return_value = [["A"]]
        mock_page1 = MagicMock()
        mock_page1.extract_tables.return_value = [["B"], ["C"]]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page0, mock_page1]
        mock_pdf.__enter__ = lambda self: self
        mock_pdf.__exit__ = MagicMock(return_value=False)

        mock_pp = MagicMock()
        mock_pp.open.return_value = mock_pdf

        with patch.dict("sys.modules", {"pdfplumber": mock_pp}):
            result = extract_tables("/fake/test.pdf", pages=[1])

        assert result["success"] is True
        assert result["count"] == 2  # page 1 (0-indexed) has 2 tables

    def test_get_metadata_success(self):
        """成功读取元数据"""
        from src.tools.pdf.pdf_reader import get_metadata

        doc = _mock_doc(metadata={"title": "Test PDF", "author": "Test"})
        doc.page_count = 10
        mock_fitz = _mock_fitz(doc)

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = get_metadata("/fake/test.pdf")

        assert result["success"] is True
        assert result["metadata"]["page_count"] == 10
        assert result["metadata"]["title"] == "Test PDF"

    def test_get_metadata_error(self):
        """元数据读取失败"""
        from src.tools.pdf.pdf_reader import get_metadata

        mock_fitz = MagicMock()
        mock_fitz.open.side_effect = Exception("error")

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = get_metadata("/fake/test.pdf")

        assert result["success"] is False
        assert "读取元数据失败" in result["error"]


# =============================================================================
# PdfToMd 测试
# =============================================================================

class TestPdfToMd:
    """pdf_to_md 模块测试"""

    def test_convert_success(self):
        """PyMuPDF4LLM 转换成功"""
        from src.tools.pdf.pdf_to_md import convert

        mock_chunks = [
            {"text": "# Heading\n\nParagraph"},
            {"text": "Page 2 content"},
        ]

        mock_pymupdf4llm = MagicMock()
        mock_pymupdf4llm.to_markdown.return_value = mock_chunks

        with patch.dict("sys.modules", {"pymupdf4llm": mock_pymupdf4llm}):
            result = convert("/fake/test.pdf")

        assert result["success"] is True
        assert "# Heading" in result["markdown"]
        assert "---" in result["markdown"]
        assert result["page_count"] == 2

    def test_convert_with_specific_pages(self):
        """指定页面转换"""
        from src.tools.pdf.pdf_to_md import convert

        mock_chunks = [
            {"text": "Page 0"},
            {"text": "Page 1"},
            {"text": "Page 2"},
        ]

        mock_pymupdf4llm = MagicMock()
        mock_pymupdf4llm.to_markdown.return_value = mock_chunks

        with patch.dict("sys.modules", {"pymupdf4llm": mock_pymupdf4llm}):
            result = convert("/fake/test.pdf", pages=[0, 2])

        assert result["success"] is True
        assert result["page_count"] == 2
        assert "Page 0" in result["markdown"]
        assert "Page 2" in result["markdown"]

    def test_convert_string_chunks(self):
        """chunk 是字符串而非 dict 的情况"""
        from src.tools.pdf.pdf_to_md import convert

        mock_chunks = ["Page 0 text", "Page 1 text"]

        mock_pymupdf4llm = MagicMock()
        mock_pymupdf4llm.to_markdown.return_value = mock_chunks

        with patch.dict("sys.modules", {"pymupdf4llm": mock_pymupdf4llm}):
            result = convert("/fake/test.pdf")

        assert result["success"] is True
        assert "Page 0 text" in result["markdown"]

    def test_convert_error(self):
        """转换失败"""
        from src.tools.pdf.pdf_to_md import convert

        mock_pymupdf4llm = MagicMock()
        mock_pymupdf4llm.to_markdown.side_effect = Exception("conversion error")

        with patch.dict("sys.modules", {"pymupdf4llm": mock_pymupdf4llm}):
            result = convert("/fake/test.pdf")

        assert result["success"] is False
        assert "PDF转Markdown失败" in result["error"]

    def test_convert_smart_text_pdf(self):
        """文字型 PDF 不走 OCR"""
        from src.tools.pdf.pdf_to_md import convert_smart

        mock_convert = MagicMock(return_value={
            "success": True,
            "markdown": "This is a text PDF with enough content to pass the threshold check.",
            "page_count": 1,
        })

        with patch("src.tools.pdf.pdf_to_md.convert", mock_convert):
            result = convert_smart("/fake/text.pdf")

        assert result["success"] is True
        assert result["source"] == "text_extract"

    def test_convert_smart_scanned_pdf_fallback_to_ocr(self):
        """扫描件 PDF 自动降级到 OCR"""
        from src.tools.pdf.pdf_to_md import convert_smart

        mock_convert = MagicMock(return_value={
            "success": True,
            "markdown": "",  # 空文本 → 触发 OCR
            "page_count": 1,
        })
        mock_ocr = MagicMock(return_value={
            "success": True,
            "markdown": "OCR extracted text",
            "source": "ocr",
            "page_count": 1,
        })

        with patch("src.tools.pdf.pdf_to_md.convert", mock_convert), \
             patch("src.tools.pdf.pdf_to_md._ocr_fallback", mock_ocr):
            result = convert_smart("/fake/scanned.pdf")

        assert result["success"] is True
        assert result["source"] == "ocr"
        mock_ocr.assert_called_once_with("/fake/scanned.pdf")

    def test_convert_smart_pymupdf4llm_fails_ocr_fallback(self):
        """PyMuPDF4LLM 失败时也降级到 OCR"""
        from src.tools.pdf.pdf_to_md import convert_smart

        mock_convert = MagicMock(return_value={
            "success": False,
            "error": "pymupdf4llm failed",
        })
        mock_ocr = MagicMock(return_value={
            "success": True,
            "markdown": "OCR result",
            "source": "ocr",
            "page_count": 1,
        })

        with patch("src.tools.pdf.pdf_to_md.convert", mock_convert), \
             patch("src.tools.pdf.pdf_to_md._ocr_fallback", mock_ocr):
            result = convert_smart("/fake/broken.pdf")

        assert result["success"] is True
        assert result["source"] == "ocr"

    def test_ocr_fallback_success(self):
        """OCR 降级成功"""
        from src.tools.pdf.pdf_to_md import _ocr_fallback

        mock_ocr_result = {
            "success": True,
            "full_text": "OCR text content",
            "texts": ["page1", "page2"],
        }

        mock_module = MagicMock()
        mock_module.paddleocr_doc_parsing.return_value = mock_ocr_result

        with patch.dict("sys.modules", {"src.tools.ocr.ocr_tool": mock_module}):
            result = _ocr_fallback("/fake/test.pdf")

        assert result["success"] is True
        assert result["markdown"] == "OCR text content"
        assert result["source"] == "ocr"
        assert result["page_count"] == 2

    def test_ocr_fallback_failure(self):
        """OCR 降级也失败"""
        from src.tools.pdf.pdf_to_md import _ocr_fallback

        mock_module = MagicMock()
        mock_module.paddleocr_doc_parsing.return_value = {
            "success": False,
            "error": "OCR service unavailable",
        }

        with patch.dict("sys.modules", {"src.tools.ocr.ocr_tool": mock_module}):
            result = _ocr_fallback("/fake/test.pdf")

        assert result["success"] is False
        assert "error" in result

    def test_ocr_fallback_import_error(self):
        """OCR 模块不存在"""
        from src.tools.pdf.pdf_to_md import _ocr_fallback

        with patch.dict("sys.modules", {"src.tools.ocr.ocr_tool": None}):
            result = _ocr_fallback("/fake/test.pdf")

        assert result["success"] is False
        assert "OCR识别失败" in result["error"]


# =============================================================================
# PdfWriter 测试
# =============================================================================

class TestPdfWriter:
    """pdf_writer 模块测试

    注意：pdf_writer 内部使用大量 lazy import（shutil, weasyprint 等），
    这些无法在模块级别 patch。因此 writer 的单元测试通过 mock sys.modules
    或直接 mock 整个函数来测试。完整的 pipeline 测试见 TestPdfProcessPipeline。
    """

    def test_md_to_pdf_success(self):
        """Markdown 转 HTML 后优先交给浏览器打印链路"""
        from src.tools.pdf.pdf_writer import md_to_pdf

        expected = {
            "success": True,
            "file_path": "/fake/test.pdf",
            "file_size": 1024,
            "engine": "playwright",
        }
        with patch("src.tools.pdf.pdf_writer.html_to_pdf", return_value=expected) as mocked:
            result = md_to_pdf("# Test\n\nHello world", output_name="test.pdf")

        assert result == expected
        assert ">Test</h1>" in mocked.call_args.kwargs["html_text"]
        assert mocked.call_args.kwargs["engine"] == "auto"

    def test_md_to_pdf_propagates_renderer_failure(self):
        """浏览器与 fpdf2 都失败时原样返回错误"""
        from src.tools.pdf.pdf_writer import md_to_pdf

        with patch(
            "src.tools.pdf.pdf_writer.html_to_pdf",
            return_value={"success": False, "error": "所有渲染引擎均不可用"},
        ):
            result = md_to_pdf("# Test")

        assert result["success"] is False
        assert "所有渲染引擎均不可用" in result["error"]

    def test_md_to_pdf_render_exception(self):
        """Markdown 预处理异常"""
        from src.tools.pdf.pdf_writer import md_to_pdf

        with patch("src.tools.pdf.pdf_writer._md_to_html", side_effect=RuntimeError("render failed")):
            result = md_to_pdf("# Test")

        assert result["success"] is False
        assert "生成PDF失败" in result["error"]

    def test_md_to_pdf_css_warning(self):
        """Markdown 转换会把 CSS 传给统一 HTML 渲染链路"""
        from src.tools.pdf.pdf_writer import md_to_pdf

        expected = {"success": True, "file_path": "/fake/test.pdf", "engine": "playwright"}
        with patch("src.tools.pdf.pdf_writer.html_to_pdf", return_value=expected) as mocked:
            result = md_to_pdf("# Test", css="/fake/style.css")

        assert result["success"] is True
        assert mocked.call_args.kwargs["css"] == "/fake/style.css"

    def test_md_to_pdf_preserves_chinese_content(self):
        """中文标题、正文和表格不得在转 HTML 阶段损坏"""
        from src.tools.pdf.pdf_writer import md_to_pdf

        markdown = "# 贵州行程\n\n| 天数 | 安排 |\n|---|---|\n| D1 | 黄果树瀑布 |"
        with patch(
            "src.tools.pdf.pdf_writer.html_to_pdf",
            return_value={"success": True, "engine": "playwright"},
        ) as mocked:
            md_to_pdf(markdown)

        html_text = mocked.call_args.kwargs["html_text"]
        assert "贵州行程" in html_text
        assert "天数" in html_text
        assert "黄果树瀑布" in html_text

    def test_html_to_pdf_success(self):
        """HTML 转 PDF 走 fpdf2 路径"""
        from src.tools.pdf.pdf_writer import html_to_pdf

        with tempfile.TemporaryDirectory() as tmpdir:
            output_pdf = os.path.join(tmpdir, "output.pdf")

            def fake_create_pdf(html_body, output_path, title=""):
                with open(output_path, "wb") as f:
                    f.write(b"%PDF-1.4")

            with patch("src.tools.pdf.pdf_writer._create_pdf_with_html", side_effect=fake_create_pdf), \
                 patch("src.tools.pdf.pdf_writer.PdfFileHandler.save_temp", return_value={"file_path": output_pdf, "file_size": 256}):
                result = html_to_pdf("<html><body>Hello</body></html>", engine="fpdf2")

        assert result["success"] is True

    def test_html_to_pdf_auto_uses_playwright_first(self):
        """HTML 转 PDF 默认优先走 Playwright print-to-pdf"""
        from src.tools.pdf.pdf_writer import html_to_pdf

        expected = {"success": True, "file_path": "/fake/playwright.pdf", "file_size": 256, "engine": "playwright"}
        with patch("src.tools.pdf.pdf_writer._html_to_pdf_via_playwright", return_value=expected) as mock_pw, \
             patch("src.tools.pdf.pdf_writer._html_to_pdf_via_fpdf2") as mock_fpdf2:
            result = html_to_pdf("<html><body>Hello</body></html>")

        assert result == expected
        mock_pw.assert_called_once()
        mock_fpdf2.assert_not_called()

    def test_html_to_pdf_auto_fallback_to_fpdf2(self):
        """Playwright 不可用时 auto 回退 fpdf2"""
        from src.tools.pdf.pdf_writer import html_to_pdf

        fallback = {"success": True, "file_path": "/fake/fpdf2.pdf", "file_size": 128, "engine": "fpdf2"}
        with patch("src.tools.pdf.pdf_writer._html_to_pdf_via_playwright", return_value={"success": False, "error": "no browser"}), \
             patch("src.tools.pdf.pdf_writer._html_to_pdf_via_fpdf2", return_value=fallback):
            result = html_to_pdf("<html><body>Hello</body></html>")

        assert result["success"] is True
        assert result["engine"] == "fpdf2"
        assert "warnings" in result

    def test_html_to_pdf_rejects_unknown_engine(self):
        """未知 HTML 转 PDF 引擎直接返回失败"""
        from src.tools.pdf.pdf_writer import html_to_pdf

        result = html_to_pdf("<p>Hello</p>", engine="unknown")

        assert result["success"] is False
        assert "不支持" in result["error"]

    def test_prepare_print_html_uses_inline_css(self):
        """Playwright 打印 HTML 支持内联 CSS"""
        from src.tools.pdf.pdf_writer import _prepare_print_html

        result = _prepare_print_html("<p>Hello</p>", css="body { color: red; }")

        assert "body { color: red; }" in result
        assert "<body>\n<p>Hello</p>" in result

    def test_prepare_print_html_reads_css_file(self, tmp_path):
        """Playwright 打印 HTML 支持 CSS 文件路径"""
        from src.tools.pdf.pdf_writer import _prepare_print_html

        css_path = tmp_path / "print.css"
        css_path.write_text(".title { font-weight: 700; }", encoding="utf-8")

        result = _prepare_print_html(
            "<html><body><h1 class='title'>Hello</h1></body></html>",
            css=str(css_path),
        )

        assert ".title { font-weight: 700; }" in result
        assert "</style></head><body>" in result

    def test_prepare_print_html_injects_css_when_full_html_has_no_head(self):
        """完整 HTML 没有 head 时仍注入 CSS"""
        from src.tools.pdf.pdf_writer import _prepare_print_html

        result = _prepare_print_html("<html><body>Hello</body></html>", css="body { margin: 0; }")

        assert "<head><style>body { margin: 0; }</style></head>" in result

    def test_split_html_by_tables_preserves_order(self):
        """HTML 表格拆分保持正文/表格顺序"""
        from src.tools.pdf.pdf_writer import _split_html_by_tables

        parts = _split_html_by_tables("<p>before</p><table><tr><td>A</td></tr></table><p>after</p>")

        assert parts[0]["type"] == "html"
        assert "before" in parts[0]["content"]
        assert parts[1]["type"] == "table"
        assert parts[2]["type"] == "html"
        assert "after" in parts[2]["content"]

    def test_docx_to_pdf_file_not_found(self):
        """Word 转 PDF 文件不存在"""
        from src.tools.pdf.pdf_writer import docx_to_pdf

        with patch("src.tools.pdf.pdf_writer.PdfFileHandler.resolve_path", return_value="/nonexistent.docx"), \
             patch("src.tools.pdf.pdf_writer.Path") as mock_path_cls:
            mock_path_cls.return_value.exists.return_value = False
            result = docx_to_pdf("/nonexistent.docx")

        assert result["success"] is False

    def test_docx_to_pdf_libreoffice_success(self):
        """Word 转 PDF 通过 LibreOffice 成功"""
        from src.tools.pdf.pdf_writer import _docx_to_pdf_via_libreoffice

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, "test.pdf")
            with open(pdf_path, "wb") as f:
                f.write(b"%PDF-1.4 fake")

            def fake_run(cmd, **kwargs):
                outdir_idx = cmd.index("--outdir")
                outdir = cmd[outdir_idx + 1]
                output = os.path.join(outdir, "test.pdf")
                with open(output, "wb") as f:
                    f.write(b"%PDF-1.4 fake")
                return MagicMock(stderr="")

            mock_shutil = MagicMock()
            mock_shutil.which.return_value = "/usr/bin/soffice"

            with patch.dict("sys.modules", {"shutil": mock_shutil}), \
                 patch("subprocess.run", side_effect=fake_run), \
                 patch("src.tools.pdf.pdf_writer.PdfFileHandler.save_temp", return_value={"file_path": pdf_path, "file_size": 100}):
                result = _docx_to_pdf_via_libreoffice("/fake/test.docx")

        assert result["success"] is True

    def test_docx_to_pdf_libreoffice_not_installed(self):
        """LibreOffice 未安装"""
        from src.tools.pdf.pdf_writer import _docx_to_pdf_via_libreoffice

        mock_shutil = MagicMock()
        mock_shutil.which.return_value = None

        with patch.dict("sys.modules", {"shutil": mock_shutil}):
            result = _docx_to_pdf_via_libreoffice("/fake/test.docx")

        assert result["success"] is False
        assert "LibreOffice" in result["error"]

    def test_docx_to_pdf_libreoffice_timeout(self):
        """LibreOffice 转换超时"""
        import subprocess
        from src.tools.pdf.pdf_writer import _docx_to_pdf_via_libreoffice

        mock_shutil = MagicMock()
        mock_shutil.which.return_value = "/usr/bin/soffice"

        with patch.dict("sys.modules", {"shutil": mock_shutil}), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="soffice", timeout=120)):
            result = _docx_to_pdf_via_libreoffice("/fake/test.docx")

        assert result["success"] is False
        assert "超时" in result["error"]


# =============================================================================
# PdfMerger 测试
# =============================================================================

class TestPdfMerger:
    """pdf_merger 模块测试"""

    def test_merge_insufficient_files(self):
        """合并需要至少 2 个文件"""
        from src.tools.pdf.pdf_merger import merge_pdfs

        assert merge_pdfs(["/tmp/a.pdf"])["success"] is False
        assert merge_pdfs([])["success"] is False

    def test_merge_file_not_found(self):
        """合并时文件不存在"""
        from src.tools.pdf.pdf_merger import merge_pdfs

        mock_doc = MagicMock()
        mock_fitz = _mock_fitz(mock_doc)

        with patch.dict("sys.modules", {"fitz": mock_fitz}), \
             patch("src.tools.pdf.pdf_merger.PdfFileHandler.resolve_path", side_effect=lambda x: x), \
             patch("src.tools.pdf.pdf_merger.Path", return_value=_mock_path_obj("/a.pdf", exists=False)):
            result = merge_pdfs(["/a.pdf", "/b.pdf"])

        assert result["success"] is False
        assert "不存在" in result["error"]

    def test_merge_success(self):
        """合并成功"""
        from src.tools.pdf.pdf_merger import merge_pdfs

        mock_merged = MagicMock()
        mock_merged.save = MagicMock()
        mock_merged.close = MagicMock()

        mock_doc = MagicMock()
        mock_doc.page_count = 3
        mock_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.side_effect = [mock_merged, mock_doc, mock_doc]

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            with tempfile.TemporaryDirectory() as tmpdir:
                # 创建假的源文件让 Path.exists() 返回 True
                for name in ["a.pdf", "b.pdf"]:
                    with open(os.path.join(tmpdir, name), "wb") as f:
                        f.write(b"%PDF-1.4")

                file_a = os.path.join(tmpdir, "a.pdf")
                file_b = os.path.join(tmpdir, "b.pdf")

                def resolve_and_check(path):
                    # 返回真实存在的路径
                    if path == "/a.pdf":
                        return file_a
                    if path == "/b.pdf":
                        return file_b
                    return path

                with patch("src.tools.pdf.pdf_merger.PdfFileHandler.resolve_path", side_effect=resolve_and_check), \
                     patch("src.tools.pdf.pdf_merger.PdfFileHandler.save_temp") as mock_save:

                    mock_save.return_value = {"file_path": os.path.join(tmpdir, "merged.pdf"), "file_size": 2048}

                    def save_to_path(path):
                        with open(path, "wb") as f:
                            f.write(b"%PDF-1.4")
                    mock_merged.save.side_effect = save_to_path

                    result = merge_pdfs(["/a.pdf", "/b.pdf"])

        assert result["success"] is True
        assert result["source_count"] == 2
        mock_merged.insert_pdf.assert_called()

    def test_split_file_not_found(self):
        """拆分时文件不存在"""
        from src.tools.pdf.pdf_merger import split_pdf

        with patch("src.tools.pdf.pdf_merger.PdfFileHandler.resolve_path", return_value="/nonexistent.pdf"), \
             patch("src.tools.pdf.pdf_merger.Path") as mock_path:
            mock_path.return_value.exists.return_value = False
            result = split_pdf("/nonexistent.pdf", ["1-3"])

        assert result["success"] is False

    def test_split_success(self):
        """拆分成功"""
        from src.tools.pdf.pdf_merger import split_pdf

        mock_doc = MagicMock()
        mock_doc.page_count = 10
        mock_doc.close = MagicMock()

        mock_new_doc = MagicMock()
        mock_new_doc.save = MagicMock()
        mock_new_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.side_effect = [mock_doc, mock_new_doc]

        def make_path(arg=""):
            return _mock_path_obj(str(arg), exists=True, stem="test")

        with patch.dict("sys.modules", {"fitz": mock_fitz}), \
             patch("src.tools.pdf.pdf_merger.PdfFileHandler.resolve_path", return_value="/fake/test.pdf"), \
             patch("src.tools.pdf.pdf_merger.Path", side_effect=make_path), \
             patch("src.tools.pdf.pdf_merger.PdfFileHandler.save_temp") as mock_save:

            mock_save.return_value = {"file_path": "/tmp/test_p1-3.pdf", "file_size": 512}

            def save_to_path(path):
                with open(path, "wb") as f:
                    f.write(b"%PDF-1.4")
            mock_new_doc.save.side_effect = save_to_path

            with tempfile.TemporaryDirectory() as tmpdir:
                result = split_pdf("/fake/test.pdf", ["1-3"])

        assert result["success"] is True
        assert result["count"] == 1

    def test_split_invalid_range(self):
        """无效的页码范围被跳过"""
        from src.tools.pdf.pdf_merger import split_pdf

        mock_doc = MagicMock()
        mock_doc.page_count = 10
        mock_doc.close = MagicMock()
        mock_fitz = _mock_fitz(mock_doc)

        def make_path(arg=""):
            return _mock_path_obj(str(arg), exists=True, stem="test")

        with patch.dict("sys.modules", {"fitz": mock_fitz}), \
             patch("src.tools.pdf.pdf_merger.PdfFileHandler.resolve_path", return_value="/fake/test.pdf"), \
             patch("src.tools.pdf.pdf_merger.Path", side_effect=make_path):
            result = split_pdf("/fake/test.pdf", ["invalid", "a-b"])

        assert result["success"] is False
        assert "没有有效的页码范围" in result["error"]

    def test_extract_pages_file_not_found(self):
        """提取页面时文件不存在"""
        from src.tools.pdf.pdf_merger import extract_pages

        with patch("src.tools.pdf.pdf_merger.PdfFileHandler.resolve_path", return_value="/nonexistent.pdf"), \
             patch("src.tools.pdf.pdf_merger.Path") as mock_path:
            mock_path.return_value.exists.return_value = False
            result = extract_pages("/nonexistent.pdf", [1, 3])

        assert result["success"] is False

    def test_extract_pages_success(self):
        """页面提取成功"""
        from src.tools.pdf.pdf_merger import extract_pages

        mock_doc = MagicMock()
        mock_doc.page_count = 10
        mock_doc.close = MagicMock()

        mock_new_doc = MagicMock()
        mock_new_doc.save = MagicMock()
        mock_new_doc.close = MagicMock()

        mock_fitz = MagicMock()
        mock_fitz.open.side_effect = [mock_doc, mock_new_doc]

        def make_path(arg=""):
            return _mock_path_obj(str(arg), exists=True, stem="test", name="test_extracted.pdf")

        with patch.dict("sys.modules", {"fitz": mock_fitz}), \
             patch("src.tools.pdf.pdf_merger.PdfFileHandler.resolve_path", return_value="/fake/test.pdf"), \
             patch("src.tools.pdf.pdf_merger.Path", side_effect=make_path), \
             patch("src.tools.pdf.pdf_merger.PdfFileHandler.save_temp") as mock_save:

            mock_save.return_value = {"file_path": "/tmp/test_extracted.pdf", "file_size": 1024}

            def save_to_path(path):
                with open(path, "wb") as f:
                    f.write(b"%PDF-1.4")
            mock_new_doc.save.side_effect = save_to_path

            with tempfile.TemporaryDirectory() as tmpdir:
                result = extract_pages("/fake/test.pdf", [1, 3, 5])

        assert result["success"] is True
        assert result["extracted_pages"] == [1, 3, 5]
        assert result["page_count"] == 3
        assert mock_new_doc.insert_pdf.call_count == 3


# =============================================================================
# PdfLib 测试
# =============================================================================

class TestPdfLib:
    """pdf_lib 共享工具测试"""

    def test_save_temp_success(self):
        """保存文件成功"""
        from src.tools.pdf.pdf_lib import PdfFileHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "source.pdf")
            with open(src, "wb") as f:
                f.write(b"%PDF-1.4 fake content")

            with patch.object(PdfFileHandler, 'get_session_dir', return_value=Path(tmpdir)):
                result = PdfFileHandler.save_temp(src, file_name="output.pdf")

            assert result["file_path"].endswith("output.pdf")
            assert result["file_size"] > 0

    def test_save_temp_source_not_found(self):
        """源文件不存在"""
        from src.tools.pdf.pdf_lib import PdfFileHandler

        result = PdfFileHandler.save_temp("/nonexistent.pdf")
        assert result["success"] is False

    def test_save_temp_auto_name(self):
        """自动生成文件名"""
        from src.tools.pdf.pdf_lib import PdfFileHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "source.pdf")
            with open(src, "wb") as f:
                f.write(b"%PDF-1.4")

            with patch.object(PdfFileHandler, 'get_session_dir', return_value=Path(tmpdir)):
                result = PdfFileHandler.save_temp(src)

            assert result["file_path"].endswith(".pdf")
            assert "document_" in result["file_path"]

    def test_save_temp_add_pdf_extension(self):
        """自动添加 .pdf 后缀"""
        from src.tools.pdf.pdf_lib import PdfFileHandler

        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "source.pdf")
            with open(src, "wb") as f:
                f.write(b"%PDF-1.4")

            with patch.object(PdfFileHandler, 'get_session_dir', return_value=Path(tmpdir)):
                result = PdfFileHandler.save_temp(src, file_name="report")

            assert result["file_path"].endswith("report.pdf")

    def test_resolve_path_existing(self):
        """解析已存在的文件路径"""
        from src.tools.pdf.pdf_lib import PdfFileHandler

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            tmp_path = f.name

        try:
            result = PdfFileHandler.resolve_path(tmp_path)
            assert os.path.isabs(result)
        finally:
            os.unlink(tmp_path)

    def test_get_page_count(self):
        """获取页数"""
        from src.tools.pdf.pdf_lib import PdfFileHandler

        doc = _mock_doc(page_count=42)
        mock_fitz = _mock_fitz(doc)

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = PdfFileHandler.get_page_count("/fake/test.pdf")

        assert result == 42

    def test_is_text_pdf_true(self):
        """文字型 PDF 判断"""
        from src.tools.pdf.pdf_lib import PdfFileHandler

        doc = _mock_doc(pages_text=["A" * 200, "B" * 200, "C" * 200])
        mock_fitz = _mock_fitz(doc)

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = PdfFileHandler.is_text_pdf("/fake/text.pdf")

        assert result is True

    def test_is_text_pdf_false(self):
        """扫描件 PDF 判断"""
        from src.tools.pdf.pdf_lib import PdfFileHandler

        doc = _mock_doc(pages_text=["", "", ""])
        mock_fitz = _mock_fitz(doc)

        with patch.dict("sys.modules", {"fitz": mock_fitz}):
            result = PdfFileHandler.is_text_pdf("/fake/scanned.pdf")

        assert result is False


# =============================================================================
# PdfRouter 测试
# =============================================================================

class TestPdfRouter:
    """PdfRouter 内部 LLM 路由器测试"""

    def test_build_prompt_with_context(self):
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()
        prompt = router._build_prompt("用户要求读取PDF", None)
        assert "用户要求读取PDF" in prompt
        assert "(无附件)" in prompt

    def test_build_prompt_with_files(self):
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()
        prompt = router._build_prompt("读取文件", ["/tmp/test.pdf"])
        assert "读取文件" in prompt
        assert "/tmp/test.pdf" in prompt

    def test_parse_response_valid_json(self):
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()
        result = router._parse_response('{"task": "read", "params": {}, "reason": "用户要求读取PDF"}')
        assert result["task"] == "read"

    def test_parse_response_json_in_code_block(self):
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()
        content = '```json\n{"task": "read", "params": {}, "reason": "test"}\n```'
        result = router._parse_response(content)
        assert result["task"] == "read"

    def test_parse_response_invalid_json(self):
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()
        result = router._parse_response("not json at all")
        assert result["task"] == ""
        assert "error" in result

    def test_parse_response_invalid_task(self):
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()
        result = router._parse_response('{"task": "invalid_op", "params": {}, "reason": "test"}')
        assert result["task"] == ""
        assert "error" in result

    def test_parse_response_multi_task(self):
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()
        result = router._parse_response('{"task": "read,pdf_to_md", "params": {}, "reason": "test"}')
        assert result["task"] == "read,pdf_to_md"

    def test_parse_response_all_valid_tasks(self):
        """所有合法操作都能通过校验"""
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()
        valid_tasks = [
            "read", "read_tables", "ocr", "pdf_to_md",
            "md_to_pdf", "html_to_pdf", "docx_to_pdf",
            "merge", "split", "extract_pages",
            "inspect", "render_pages", "validate",
            "clean_metadata", "add_watermark", "protect",
            "compress", "extract_images", "rotate",
        ]
        for task in valid_tasks:
            result = router._parse_response(f'{{"task": "{task}", "params": {{}}, "reason": "test"}}')
            assert result["task"] == task, f"Task '{task}' should be valid"

    def test_parse_response_split_with_params(self):
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()
        content = '{"task": "split", "params": {"ranges": ["1-3", "5-7"]}, "reason": "拆分PDF"}'
        result = router._parse_response(content)
        assert result["task"] == "split"
        assert result["params"]["ranges"] == ["1-3", "5-7"]

    def test_parse_response_code_block_no_language(self):
        """无语言标记的代码块"""
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()
        content = '```\n{"task": "md_to_pdf", "params": {}, "reason": "test"}\n```'
        result = router._parse_response(content)
        assert result["task"] == "md_to_pdf"

    @pytest.mark.asyncio
    async def test_route_llm_success(self):
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()

        mock_gateway = AsyncMock()
        mock_gateway.chat.return_value = {
            "content": '{"task": "read", "params": {}, "reason": "用户要求读取PDF内容"}'
        }
        router._gateway = mock_gateway

        result = await router.route("用户要求读取这个PDF文件", ["/tmp/test.pdf"])
        assert result["task"] == "read"

    @pytest.mark.asyncio
    async def test_route_llm_failure(self):
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()

        mock_gateway = AsyncMock()
        mock_gateway.chat.side_effect = Exception("LLM unavailable")
        router._gateway = mock_gateway

        result = await router.route("用户要求读取PDF", None)
        assert result["task"] == ""
        assert "error" in result

    @pytest.mark.asyncio
    async def test_route_llm_empty_response(self):
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()

        mock_gateway = AsyncMock()
        mock_gateway.chat.return_value = {"content": ""}
        router._gateway = mock_gateway

        result = await router.route("some context", None)
        assert result["task"] == ""
        assert "error" in result

    @pytest.mark.asyncio
    async def test_route_llm_cannot_determine(self):
        """LLM 返回无法解析的内容"""
        from src.tools.pdf.pdf_router import PdfRouter
        router = PdfRouter()

        mock_gateway = AsyncMock()
        mock_gateway.chat.return_value = {"content": "I don't understand"}
        router._gateway = mock_gateway

        result = await router.route("模糊请求", None)
        assert result["task"] == ""


# =============================================================================
# PdfProcessTool Pipeline 集成测试（handler mock）
# =============================================================================

class TestPdfProcessPipeline:
    """Pipeline 集成测试"""

    @pytest.mark.asyncio
    async def test_read_pipeline_success(self):
        """read 操作完整 pipeline"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "read", "params": {}}
        tool._router = mock_router

        mock_read_result = {
            "success": True,
            "content": "PDF text content",
            "pages": [{"page": 1, "text": "PDF text content"}],
            "metadata": {"page_count": 1},
        }

        with patch("src.tools.pdf.pdf_reader.read_text", return_value=mock_read_result), \
             patch.object(tool, "_resolve_file", return_value="/fake/test.pdf"):
            result = await tool.execute(context="读取PDF", file_paths=["test.pdf"])

        assert result["success"] is True
        assert result["content"] == "PDF text content"
        assert result["steps"] == 1

    @pytest.mark.asyncio
    async def test_read_tables_pipeline_success(self):
        """read_tables 操作完整 pipeline"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "read_tables", "params": {}}
        tool._router = mock_router

        mock_result = {
            "success": True,
            "tables": [{"page": 1, "table_index": 1, "data": [["A", "B"]]}],
            "count": 1,
        }

        with patch("src.tools.pdf.pdf_reader.extract_tables", return_value=mock_result), \
             patch.object(tool, "_resolve_file", return_value="/fake/test.pdf"):
            result = await tool.execute(context="提取表格", file_paths=["test.pdf"])

        assert result["success"] is True
        assert result["table_count"] == 1

    @pytest.mark.asyncio
    async def test_pdf_to_md_pipeline_success(self):
        """pdf_to_md 操作完整 pipeline"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "pdf_to_md", "params": {}}
        tool._router = mock_router

        mock_convert_result = {
            "success": True,
            "markdown": "# Heading\n\nParagraph text",
            "page_count": 1,
            "source": "text_extract",
        }

        with patch("src.tools.pdf.pdf_to_md.convert_smart", return_value=mock_convert_result), \
             patch.object(tool, "_resolve_file", return_value="/fake/test.pdf"):
            result = await tool.execute(context="转Markdown", file_paths=["test.pdf"])

        assert result["success"] is True
        assert result["markdown"] == "# Heading\n\nParagraph text"
        assert result["source"] == "text_extract"

    @pytest.mark.asyncio
    async def test_md_to_pdf_pipeline_success(self):
        """md_to_pdf 操作完整 pipeline"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "md_to_pdf", "params": {"output_name": "test.pdf"}}
        tool._router = mock_router

        mock_write_result = {
            "success": True,
            "file_path": "/tmp/test.pdf",
            "file_size": 2048,
        }

        with patch("src.tools.pdf.pdf_writer.md_to_pdf", return_value=mock_write_result):
            result = await tool.execute(context="# Test\n\nHello world", file_paths=[])

        assert result["success"] is True
        assert result["file_path"] == "/tmp/test.pdf"

    @pytest.mark.asyncio
    async def test_html_to_pdf_pipeline_success(self):
        """html_to_pdf 操作完整 pipeline"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "html_to_pdf", "params": {}}
        tool._router = mock_router

        mock_result = {"success": True, "file_path": "/tmp/test.pdf", "file_size": 1024}

        with patch("src.tools.pdf.pdf_writer.html_to_pdf", return_value=mock_result):
            result = await tool.execute(context="<html><body>Hello</body></html>")

        assert result["success"] is True
        assert result["file_path"] == "/tmp/test.pdf"

    @pytest.mark.asyncio
    async def test_docx_to_pdf_pipeline_success(self):
        """docx_to_pdf 操作完整 pipeline"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "docx_to_pdf", "params": {}}
        tool._router = mock_router

        mock_result = {"success": True, "file_path": "/tmp/test.pdf", "file_size": 1024}

        with patch("src.tools.pdf.pdf_writer.docx_to_pdf", return_value=mock_result), \
             patch.object(tool, "_resolve_file", return_value="/fake/test.docx"):
            result = await tool.execute(context="Word转PDF", file_paths=["test.docx"])

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_md_to_pdf_structured_content_uses_content_only(self):
        """instruction + content 调用时，Markdown 正文不被指令污染。"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.side_effect = AssertionError("确定性路由不应调用内部 LLM")
        tool._router = mock_router

        mock_result = {"success": True, "file_path": "/tmp/structured.pdf", "file_size": 1024}
        md_text = "# 行程安排\n\n| 天数 | 内容 |\n|---|---|\n| D1 | 抵达 |"

        with patch("src.tools.pdf.pdf_writer.md_to_pdf", return_value=mock_result) as mock_md_to_pdf:
            result = await tool.execute(
                instruction="生成PDF文件",
                content=md_text,
                content_type="markdown",
                output_name="安顺行程.pdf",
            )

        assert result["success"] is True
        kwargs = mock_md_to_pdf.call_args.kwargs
        assert kwargs["md_text"] == md_text
        assert kwargs["output_name"] == "安顺行程.pdf"

    @pytest.mark.asyncio
    async def test_md_to_pdf_legacy_context_strips_instruction_prefix(self):
        """旧 context 混合指令和正文时，转换正文应剥离指令前缀。"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.side_effect = AssertionError("确定性路由不应调用内部 LLM")
        tool._router = mock_router

        context = "请生成一份PDF文件，下面是Markdown正文：\n\n# 项目报告\n\n- 结论：通过"
        mock_result = {"success": True, "file_path": "/tmp/report.pdf", "file_size": 1024}

        with patch("src.tools.pdf.pdf_writer.md_to_pdf", return_value=mock_result) as mock_md_to_pdf:
            result = await tool.execute(context=context)

        assert result["success"] is True
        kwargs = mock_md_to_pdf.call_args.kwargs
        assert kwargs["md_text"].startswith("# 项目报告")
        assert "请生成" not in kwargs["md_text"]
        assert kwargs["output_name"] == "项目报告.pdf"

    @pytest.mark.asyncio
    async def test_html_to_pdf_structured_content_uses_content_only(self):
        """HTML 转 PDF 时优先消费 content，避免自然语言进入 HTML。"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.side_effect = AssertionError("确定性路由不应调用内部 LLM")
        tool._router = mock_router

        html = "<html><body><h1>报价单</h1><table><tr><td>A</td></tr></table></body></html>"
        mock_result = {"success": True, "file_path": "/tmp/html.pdf", "file_size": 1024}

        with patch("src.tools.pdf.pdf_writer.html_to_pdf", return_value=mock_result) as mock_html_to_pdf:
            result = await tool.execute(
                instruction="生成PDF文件",
                content=html,
                content_type="html",
                output_name="报价单.pdf",
            )

        assert result["success"] is True
        kwargs = mock_html_to_pdf.call_args.kwargs
        assert kwargs["html_text"] == html
        assert kwargs["output_name"] == "报价单.pdf"

    @pytest.mark.asyncio
    async def test_html_to_pdf_legacy_context_strips_instruction_prefix(self):
        """旧 context 混合指令和 HTML 时，HTML 结构不应被前缀污染。"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.side_effect = AssertionError("确定性路由不应调用内部 LLM")
        tool._router = mock_router

        context = "请把下面 HTML 生成PDF：\n\n<div><h1>报价单</h1><p>金额 100</p></div>"
        mock_result = {"success": True, "file_path": "/tmp/html.pdf", "file_size": 1024}

        with patch("src.tools.pdf.pdf_writer.html_to_pdf", return_value=mock_result) as mock_html_to_pdf:
            result = await tool.execute(context=context)

        assert result["success"] is True
        html_text = mock_html_to_pdf.call_args.kwargs["html_text"]
        assert html_text.startswith("<div>")
        assert "请把下面" not in html_text

    @pytest.mark.asyncio
    async def test_docx_to_pdf_deterministic_route_with_output_name(self):
        """DOCX 附件 + PDF 生成意图可确定性路由，并优先使用显式 output_name。"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.side_effect = AssertionError("确定性路由不应调用内部 LLM")
        tool._router = mock_router

        mock_result = {"success": True, "file_path": "/tmp/from_docx.pdf", "file_size": 1024}

        with patch("src.tools.pdf.pdf_writer.docx_to_pdf", return_value=mock_result) as mock_docx_to_pdf, \
             patch.object(tool, "_resolve_file", return_value="/fake/test.docx"):
            result = await tool.execute(
                instruction="把Word转换成PDF",
                file_paths=["test.docx"],
                output_name="正式报告.pdf",
            )

        assert result["success"] is True
        assert mock_docx_to_pdf.call_args.kwargs["output_name"] == "正式报告.pdf"

    @pytest.mark.asyncio
    async def test_merge_pipeline_success(self):
        """merge 操作完整 pipeline"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "merge", "params": {}}
        tool._router = mock_router

        mock_merge_result = {
            "success": True,
            "file_path": "/tmp/merged.pdf",
            "file_size": 4096,
            "page_count": 10,
            "source_count": 2,
        }

        with patch("src.tools.pdf.pdf_merger.merge_pdfs", return_value=mock_merge_result):
            result = await tool.execute(context="合并PDF", file_paths=["a.pdf", "b.pdf"])

        assert result["success"] is True
        assert result["source_count"] == 2

    @pytest.mark.asyncio
    async def test_split_pipeline_success(self):
        """split 操作完整 pipeline"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "split", "params": {"ranges": ["1-3"]}}
        tool._router = mock_router

        mock_split_result = {
            "success": True,
            "files": [{"file_path": "/tmp/test_p1-3.pdf", "file_size": 1024}],
            "count": 1,
        }

        with patch("src.tools.pdf.pdf_merger.split_pdf", return_value=mock_split_result), \
             patch.object(tool, "_resolve_file", return_value="/fake/test.pdf"):
            result = await tool.execute(context="拆分PDF", file_paths=["test.pdf"])

        assert result["success"] is True
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_extract_pages_pipeline_success(self):
        """extract_pages 操作完整 pipeline"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "extract_pages", "params": {"pages": [1, 3]}}
        tool._router = mock_router

        mock_result = {
            "success": True,
            "file_path": "/tmp/extracted.pdf",
            "file_size": 512,
            "extracted_pages": [1, 3],
            "page_count": 2,
        }

        with patch("src.tools.pdf.pdf_merger.extract_pages", return_value=mock_result), \
             patch.object(tool, "_resolve_file", return_value="/fake/test.pdf"):
            result = await tool.execute(context="提取页面", file_paths=["test.pdf"])

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_ocr_pipeline_success(self):
        """ocr 操作完整 pipeline"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "ocr", "params": {}}
        tool._router = mock_router

        with patch.object(tool, "_resolve_file", return_value="/fake/test.pdf"), \
             patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = {
                "success": True,
                "full_text": "OCR extracted content",
                "texts": ["page1 text", "page2 text"],
            }
            result = await tool.execute(context="OCR识别", file_paths=["test.pdf"])

        assert result["success"] is True
        assert result["content"] == "OCR extracted content"
        assert result["page_count"] == 2

    @pytest.mark.asyncio
    async def test_ocr_pipeline_failure(self):
        """ocr 操作失败"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "ocr", "params": {}}
        tool._router = mock_router

        with patch.object(tool, "_resolve_file", return_value="/fake/test.pdf"), \
             patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            mock_thread.return_value = {
                "success": False,
                "error": "OCR service unavailable",
            }
            result = await tool.execute(context="OCR识别", file_paths=["test.pdf"])

        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_multi_step_pipeline(self):
        """多步骤 pipeline：read → pdf_to_md"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "read,pdf_to_md", "params": {}}
        tool._router = mock_router

        mock_read = {
            "success": True,
            "content": "PDF text",
            "pages": [],
            "metadata": {},
        }
        mock_convert = {
            "success": True,
            "markdown": "# Heading",
            "page_count": 1,
            "source": "text_extract",
        }

        with patch("src.tools.pdf.pdf_reader.read_text", return_value=mock_read), \
             patch("src.tools.pdf.pdf_to_md.convert_smart", return_value=mock_convert), \
             patch.object(tool, "_resolve_file", return_value="/fake/test.pdf"):
            result = await tool.execute(context="读取并转Markdown", file_paths=["test.pdf"])

        assert result["success"] is True
        assert result["steps"] == 2
        assert result["markdown"] == "# Heading"

    @pytest.mark.asyncio
    async def test_handler_not_found(self):
        """handler 不存在（防御性测试）"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "read", "params": {}}
        tool._router = mock_router

        with patch.object(tool, "_get_handler", return_value=None):
            result = await tool.execute(context="test", file_paths=["test.pdf"])

        assert result["success"] is False
        assert "处理器未实现" in result["error"]

    @pytest.mark.asyncio
    async def test_step_failure_stops_pipeline(self):
        """中间步骤失败时停止 pipeline"""
        from src.tools.pdf.pdf_process_tool import PdfProcessTool
        tool = PdfProcessTool()

        mock_router = AsyncMock()
        mock_router.route.return_value = {"task": "read,pdf_to_md", "params": {}}
        tool._router = mock_router

        mock_read = {"success": False, "error": "文件加密无法读取"}

        with patch("src.tools.pdf.pdf_reader.read_text", return_value=mock_read), \
             patch.object(tool, "_resolve_file", return_value="/fake/test.pdf"):
            result = await tool.execute(context="读取并转Markdown", file_paths=["test.pdf"])

        assert result["success"] is False
        assert result["failed_at"] == "read"
