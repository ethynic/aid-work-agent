"""PDF P1 enterprise enhancement contract tests.

These tests use small real PDFs where possible. They are meant to catch
behavior that broad mock-based tests can miss.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.tools


def _create_pdf(path: Path, page_texts: list[str], metadata: dict | None = None) -> Path:
    fitz = pytest.importorskip("fitz")

    doc = fitz.open()
    if metadata:
        doc.set_metadata(metadata)
    for text in page_texts:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


def test_add_watermark_default_parameters_work_on_real_pdf(tmp_path):
    fitz = pytest.importorskip("fitz")

    from src.tools.pdf.pdf_enhancer import add_watermark
    from src.tools.pdf.pdf_lib import PdfFileHandler

    pdf_path = _create_pdf(tmp_path / "source.pdf", ["original text"])

    with patch.object(PdfFileHandler, "get_session_dir", return_value=tmp_path / "out"):
        result = add_watermark(str(pdf_path), text="CONFIDENTIAL")

    assert result["success"] is True

    doc = fitz.open(result["file_path"])
    try:
        text = "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()
    assert "CONFIDENTIAL" in text


def test_clean_metadata_removes_user_supplied_metadata_on_real_pdf(tmp_path):
    pytest.importorskip("pypdf")

    from src.tools.pdf.pdf_enhancer import clean_metadata
    from src.tools.pdf.pdf_lib import PdfFileHandler
    from src.tools.pdf.pdf_reader import get_metadata

    pdf_path = _create_pdf(
        tmp_path / "source.pdf",
        ["contract"],
        metadata={"title": "Secret Contract", "author": "Alice"},
    )

    with patch.object(PdfFileHandler, "get_session_dir", return_value=tmp_path / "out"):
        result = clean_metadata(str(pdf_path))

    assert result["success"] is True
    metadata = get_metadata(result["file_path"])["metadata"]
    assert metadata.get("title", "") == ""
    assert metadata.get("author", "") == ""


def test_protect_pdf_creates_encrypted_pdf_without_password_echo(tmp_path):
    pypdf = pytest.importorskip("pypdf")

    from src.tools.pdf.pdf_enhancer import protect_pdf
    from src.tools.pdf.pdf_lib import PdfFileHandler

    pdf_path = _create_pdf(tmp_path / "source.pdf", ["protected content"])

    with patch.object(PdfFileHandler, "get_session_dir", return_value=tmp_path / "out"):
        result = protect_pdf(str(pdf_path), password="Secret123")

    assert result["success"] is True
    assert result["protected"] is True
    assert "Secret123" not in str(result)
    assert pypdf.PdfReader(result["file_path"]).is_encrypted is True


def test_add_watermark_unsupported_rotation_degrades_with_warning(tmp_path):
    fitz = pytest.importorskip("fitz")

    from src.tools.pdf.pdf_enhancer import add_watermark
    from src.tools.pdf.pdf_lib import PdfFileHandler

    pdf_path = _create_pdf(tmp_path / "source.pdf", ["original text"])

    with patch.object(PdfFileHandler, "get_session_dir", return_value=tmp_path / "out"):
        result = add_watermark(str(pdf_path), text="INTERNAL", rotate=45)

    assert result["success"] is True
    assert "0/90/180/270" in result["warnings"][0]

    doc = fitz.open(result["file_path"])
    try:
        text = "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()
    assert "INTERNAL" in text


def test_rotate_pages_rotates_only_selected_pages_on_real_pdf(tmp_path):
    fitz = pytest.importorskip("fitz")

    from src.tools.pdf.pdf_enhancer import rotate_pages
    from src.tools.pdf.pdf_lib import PdfFileHandler

    pdf_path = _create_pdf(tmp_path / "source.pdf", ["page 1", "page 2"])

    with patch.object(PdfFileHandler, "get_session_dir", return_value=tmp_path / "out"):
        result = rotate_pages(str(pdf_path), rotation=90, pages=[2])

    assert result["success"] is True
    assert result["rotated_pages"] == [2]

    doc = fitz.open(result["file_path"])
    try:
        assert doc[0].rotation == 0
        assert doc[1].rotation == 90
    finally:
        doc.close()


@pytest.mark.asyncio
async def test_enhancement_pipeline_updates_context_for_followup_validate():
    from src.tools.pdf.pdf_process_tool import PdfProcessTool

    tool = PdfProcessTool()
    router = AsyncMock()
    router.route.return_value = {"task": "clean_metadata,validate", "params": {}}
    tool._router = router

    clean_result = {"success": True, "file_path": "/fake/clean.pdf", "file_size": 100}
    validation = {"success": True, "errors": [], "warnings": []}

    with patch.object(tool, "_resolve_file", side_effect=lambda path: path), \
         patch("src.tools.pdf.pdf_enhancer.clean_metadata", return_value=clean_result), \
         patch.object(tool, "_validate_generated_result", side_effect=lambda result, params: result), \
         patch("src.tools.pdf.pdf_validator.validate_pdf", return_value=validation) as validate_pdf:
        result = await tool.execute(context="清理元数据后验证", file_paths=["/fake/source.pdf"])

    assert result["success"] is True
    validate_pdf.assert_called_once_with(
        "/fake/clean.pdf",
        level="structural",
        pages=None,
        render_dpi=150,
        max_pages=10,
    )
