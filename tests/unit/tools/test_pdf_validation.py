"""PDF 质量验证增强测试。"""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.tools


def test_capabilities_report_shape():
    from src.tools.pdf.pdf_capabilities import get_capabilities

    result = get_capabilities()

    assert result["success"] is True
    assert "packages" in result
    assert "commands" in result
    assert result["renderer"] in {"poppler", "pymupdf", "none"}


def test_inspect_pdf_success_with_mocks(tmp_path):
    from src.tools.pdf.pdf_inspector import inspect_pdf

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_pypdf = MagicMock()
    mock_pypdf.PdfReader.return_value = mock_reader

    mock_page = MagicMock()
    mock_page.rect.width = 595.28
    mock_page.rect.height = 841.89
    mock_page.rotation = 0
    mock_page.get_text.return_value = "hello"

    mock_doc = MagicMock()
    mock_doc.page_count = 1
    mock_doc.metadata = {"title": "Test"}
    mock_doc.__getitem__ = lambda self, idx: mock_page

    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc

    with patch.dict("sys.modules", {"pypdf": mock_pypdf, "fitz": mock_fitz}):
        result = inspect_pdf(str(pdf_path))

    assert result["success"] is True
    assert result["page_count"] == 1
    assert result["pages"][0]["text_chars"] == 5


def test_inspect_pdf_encrypted(tmp_path):
    from src.tools.pdf.pdf_inspector import inspect_pdf

    pdf_path = tmp_path / "encrypted.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    mock_reader = MagicMock()
    mock_reader.is_encrypted = True
    mock_pypdf = MagicMock()
    mock_pypdf.PdfReader.return_value = mock_reader

    with patch.dict("sys.modules", {"pypdf": mock_pypdf}):
        result = inspect_pdf(str(pdf_path))

    assert result["success"] is False
    assert result["encrypted"] is True


def test_render_pages_pymupdf_fallback(tmp_path):
    from src.tools.pdf.pdf_renderer import render_pages

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    mock_pix = MagicMock()

    def save_png(path):
        Path(path).write_bytes(b"png")

    mock_pix.save.side_effect = save_png

    mock_page = MagicMock()
    mock_page.get_pixmap.return_value = mock_pix

    mock_doc = MagicMock()
    mock_doc.page_count = 2
    mock_doc.__getitem__ = lambda self, idx: mock_page

    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc
    mock_fitz.Matrix.return_value = "matrix"

    with patch("src.tools.pdf.pdf_renderer.shutil.which", return_value=None), \
         patch.dict("sys.modules", {"fitz": mock_fitz}):
        result = render_pages(str(pdf_path), pages=[1], output_dir=str(tmp_path / "renders"))

    assert result["success"] is True
    assert result["renderer"] == "pymupdf"
    assert result["count"] == 1


def test_validate_pdf_structural_success():
    from src.tools.pdf.pdf_validator import validate_pdf

    inspection = {
        "success": True,
        "page_count": 1,
        "file_size": 100,
        "pages": [{"page": 1, "width": 595.28, "height": 841.89, "text_chars": 20}],
        "metadata": {},
        "warnings": [],
    }

    with patch("src.tools.pdf.pdf_validator.inspect_pdf", return_value=inspection):
        result = validate_pdf("/fake/sample.pdf", level="structural")

    assert result["success"] is True
    assert result["level"] == "structural"


@pytest.mark.asyncio
async def test_pdf_process_inspect_pipeline():
    from src.tools.pdf.pdf_process_tool import PdfProcessTool

    tool = PdfProcessTool()
    mock_router = AsyncMock()
    mock_router.route.return_value = {"task": "inspect", "params": {}}
    tool._router = mock_router

    inspection = {"success": True, "page_count": 1, "pages": [], "metadata": {}}
    with patch.object(tool, "_resolve_file", return_value="/fake/sample.pdf"), \
         patch("src.tools.pdf.pdf_inspector.inspect_pdf", return_value=inspection):
        result = await tool.execute(context="检查PDF", file_paths=["sample.pdf"])

    assert result["success"] is True
    assert result["inspection"]["page_count"] == 1


@pytest.mark.asyncio
async def test_pdf_process_validate_pipeline_failure():
    from src.tools.pdf.pdf_process_tool import PdfProcessTool

    tool = PdfProcessTool()
    mock_router = AsyncMock()
    mock_router.route.return_value = {"task": "validate", "params": {}}
    tool._router = mock_router

    validation = {"success": False, "errors": ["PDF页数为0"], "warnings": []}
    with patch.object(tool, "_resolve_file", return_value="/fake/sample.pdf"), \
         patch("src.tools.pdf.pdf_validator.validate_pdf", return_value=validation):
        result = await tool.execute(context="验证PDF", file_paths=["sample.pdf"])

    assert result["success"] is False
    assert result["validation"]["errors"] == ["PDF页数为0"]


def test_save_temp_sanitizes_filename(tmp_path):
    from src.tools.pdf.pdf_lib import PdfFileHandler

    src = tmp_path / "source.pdf"
    src.write_bytes(b"%PDF-1.4")

    with patch.object(PdfFileHandler, "get_session_dir", return_value=tmp_path):
        result = PdfFileHandler.save_temp(str(src), file_name="../unsafe:name")

    assert result["file_path"].endswith("unsafe_name.pdf")
    assert Path(result["file_path"]).exists()


def test_clean_metadata_success(tmp_path):
    from src.tools.pdf.pdf_enhancer import clean_metadata

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    output_pdf = tmp_path / "clean.pdf"

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_reader.pages = ["page"]

    mock_writer = MagicMock()
    mock_writer.write.side_effect = lambda f: f.write(b"%PDF-1.4")

    mock_pypdf = MagicMock()
    mock_pypdf.PdfReader.return_value = mock_reader
    mock_pypdf.PdfWriter.return_value = mock_writer

    with patch.dict("sys.modules", {"pypdf": mock_pypdf}), \
         patch("src.tools.pdf.pdf_enhancer.PdfFileHandler.save_temp", return_value={"file_path": str(output_pdf), "file_size": 10}):
        result = clean_metadata(str(pdf_path))

    assert result["success"] is True
    mock_writer.add_metadata.assert_called_once_with({})


def test_protect_pdf_does_not_return_password(tmp_path):
    from src.tools.pdf.pdf_enhancer import protect_pdf

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    mock_reader = MagicMock()
    mock_reader.is_encrypted = False
    mock_reader.pages = ["page"]

    mock_writer = MagicMock()
    mock_writer.write.side_effect = lambda f: f.write(b"%PDF-1.4")

    mock_pypdf = MagicMock()
    mock_pypdf.PdfReader.return_value = mock_reader
    mock_pypdf.PdfWriter.return_value = mock_writer

    with patch.dict("sys.modules", {"pypdf": mock_pypdf}), \
         patch("src.tools.pdf.pdf_enhancer.PdfFileHandler.save_temp", return_value={"file_path": "protected.pdf", "file_size": 10}):
        result = protect_pdf(str(pdf_path), password="Secret123")

    assert result["success"] is True
    assert result["protected"] is True
    assert "Secret123" not in str(result)
    mock_writer.encrypt.assert_called_once()


def test_rotate_pages_success(tmp_path):
    from src.tools.pdf.pdf_enhancer import rotate_pages

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    mock_page = MagicMock()
    mock_page.rotation = 0
    mock_doc = MagicMock()
    mock_doc.is_encrypted = False
    mock_doc.page_count = 2
    mock_doc.__getitem__ = lambda self, idx: mock_page
    mock_doc.save.side_effect = lambda path, **kwargs: Path(path).write_bytes(b"%PDF-1.4")

    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc

    with patch.dict("sys.modules", {"fitz": mock_fitz}), \
         patch("src.tools.pdf.pdf_enhancer.PdfFileHandler.save_temp", return_value={"file_path": "rotated.pdf", "file_size": 10}):
        result = rotate_pages(str(pdf_path), rotation=90, pages=[1])

    assert result["success"] is True
    assert result["rotated_pages"] == [1]
    mock_page.set_rotation.assert_called_once_with(90)


def test_extract_images_success(tmp_path):
    from src.tools.pdf.pdf_enhancer import extract_images

    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    mock_page = MagicMock()
    mock_page.get_images.return_value = [(7,)]
    mock_doc = MagicMock()
    mock_doc.is_encrypted = False
    mock_doc.page_count = 1
    mock_doc.__getitem__ = lambda self, idx: mock_page
    mock_doc.extract_image.return_value = {"ext": "png", "image": b"image"}

    mock_fitz = MagicMock()
    mock_fitz.open.return_value = mock_doc

    with patch.dict("sys.modules", {"fitz": mock_fitz}):
        result = extract_images(str(pdf_path), output_dir=str(tmp_path / "images"))

    assert result["success"] is True
    assert result["count"] == 1
    assert Path(result["images"][0]["file_path"]).exists()


@pytest.mark.asyncio
async def test_pdf_process_protect_pipeline_keeps_cp_contract():
    from src.tools.pdf.pdf_process_tool import PdfProcessTool

    tool = PdfProcessTool()
    mock_router = AsyncMock()
    mock_router.route.return_value = {"task": "protect", "params": {"password": "Secret123"}}
    tool._router = mock_router

    protected = {"success": True, "file_path": "/fake/protected.pdf", "file_size": 100, "protected": True}
    with patch.object(tool, "_resolve_file", return_value="/fake/sample.pdf"), \
         patch("src.tools.pdf.pdf_enhancer.protect_pdf", return_value=protected):
        result = await tool.execute(context="加密PDF", file_paths=["sample.pdf"])

    assert result["success"] is True
    assert result["file_path"] == "/fake/protected.pdf"
    assert "Secret123" not in str(result)
