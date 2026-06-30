"""PDF P0 quality validation contract tests.

These tests intentionally use small real PDF files where practical so they do
not depend on the older broad mock-based PDF test suite.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.tools


def _create_pdf(path: Path, page_texts: list[str]) -> Path:
    fitz = pytest.importorskip("fitz")

    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()
    return path


def test_split_pdf_rejects_all_invalid_ranges_without_saving(tmp_path):
    from src.tools.pdf.pdf_merger import split_pdf

    pdf_path = _create_pdf(tmp_path / "source.pdf", ["page 1", "page 2"])

    with patch("src.tools.pdf.pdf_merger.PdfFileHandler.save_temp") as save_temp:
        result = split_pdf(str(pdf_path), ranges=["bad", "8-9", "2-1"])

    assert result["success"] is False
    assert "没有有效的页码范围" in result["error"]
    assert len(result["warnings"]) == 3
    save_temp.assert_not_called()


def test_split_pdf_valid_range_outputs_readable_pdf(tmp_path):
    from src.tools.pdf.pdf_lib import PdfFileHandler
    from src.tools.pdf.pdf_merger import split_pdf

    fitz = pytest.importorskip("fitz")
    pdf_path = _create_pdf(tmp_path / "source.pdf", ["page 1", "page 2", "page 3"])
    output_dir = tmp_path / "out"

    with patch.object(PdfFileHandler, "get_session_dir", return_value=output_dir):
        result = split_pdf(str(pdf_path), ranges=["2-3"], output_name="part")

    assert result["success"] is True
    assert result["count"] == 1

    output_pdf = result["files"][0]["file_path"]
    doc = fitz.open(output_pdf)
    try:
        assert doc.page_count == 2
        assert "page 2" in doc[0].get_text("text")
        assert "page 3" in doc[1].get_text("text")
    finally:
        doc.close()


def test_extract_pages_rejects_all_invalid_pages_without_saving(tmp_path):
    from src.tools.pdf.pdf_merger import extract_pages

    pdf_path = _create_pdf(tmp_path / "source.pdf", ["only page"])

    with patch("src.tools.pdf.pdf_merger.PdfFileHandler.save_temp") as save_temp:
        result = extract_pages(str(pdf_path), pages=[0, 2, 99])

    assert result["success"] is False
    assert "没有有效的页码" in result["error"]
    save_temp.assert_not_called()


@pytest.mark.asyncio
async def test_read_pipeline_converts_user_pages_to_zero_based_indices():
    from src.tools.pdf.pdf_process_tool import PipelineContext, PdfProcessTool

    tool = PdfProcessTool()
    ctx = PipelineContext(file_paths=["sample.pdf"], context="读取第1和第3页")

    with patch.object(tool, "_resolve_file", return_value="/fake/sample.pdf"), \
         patch("src.tools.pdf.pdf_lib.PdfFileHandler.get_page_count", return_value=3), \
         patch("src.tools.pdf.pdf_reader.read_text", return_value={"success": True}) as read_text:
        result = await tool._handle_read(ctx, {"pages": [1, 3, 4, 0, "x"]})

    assert result
    read_text.assert_called_once_with("/fake/sample.pdf", pages=[0, 2])


@pytest.mark.asyncio
async def test_html_to_pdf_pipeline_passes_css_for_explicit_warning():
    from src.tools.pdf.pdf_process_tool import PdfProcessTool

    tool = PdfProcessTool()
    router = AsyncMock()
    router.route.return_value = {
        "task": "html_to_pdf",
        "params": {"output_name": "styled.pdf", "css": "body { color: red; }"},
    }
    tool._router = router

    with patch("src.tools.pdf.pdf_writer.html_to_pdf", return_value={"success": True, "file_path": "/tmp/styled.pdf"}) as html_to_pdf:
        result = await tool.execute(context="<p>Hello</p>")

    assert result["success"] is True
    html_to_pdf.assert_called_once_with(
        html_text="<p>Hello</p>",
        output_name="styled.pdf",
        css="body { color: red; }",
        engine="auto",
    )


def test_render_auto_falls_back_to_pymupdf_when_poppler_fails(tmp_path):
    from src.tools.pdf.pdf_renderer import render_pages

    pdf_path = _create_pdf(tmp_path / "source.pdf", ["page 1"])
    failed_process = MagicMock(returncode=1, stderr="render failed")

    with patch("src.tools.pdf.pdf_renderer.shutil.which", return_value="pdftoppm"), \
         patch("src.tools.pdf.pdf_renderer.subprocess.run", return_value=failed_process):
        result = render_pages(str(pdf_path), pages=[1], output_dir=str(tmp_path / "renders"), renderer="auto")

    assert result["success"] is True
    assert result["renderer"] == "pymupdf"
    assert result["count"] == 1
    assert Path(result["pages"][0]["image_path"]).exists()


def test_render_poppler_mode_reports_failure_without_fallback(tmp_path):
    from src.tools.pdf.pdf_renderer import render_pages

    pdf_path = _create_pdf(tmp_path / "source.pdf", ["page 1"])
    failed_process = MagicMock(returncode=1, stderr="render failed")

    with patch("src.tools.pdf.pdf_renderer.shutil.which", return_value="pdftoppm"), \
         patch("src.tools.pdf.pdf_renderer.subprocess.run", return_value=failed_process):
        result = render_pages(str(pdf_path), pages=[1], output_dir=str(tmp_path / "renders"), renderer="poppler")

    assert result["success"] is False
    assert result["renderer"] == "poppler"
    assert result["count"] == 0
    assert "render failed" in result["errors"][0]


def test_validate_visual_warns_for_blank_rendered_page(tmp_path):
    Image = pytest.importorskip("PIL.Image")

    from src.tools.pdf.pdf_validator import validate_pdf

    image_path = tmp_path / "blank.png"
    Image.new("RGB", (24, 24), "white").save(image_path)

    inspection = {
        "success": True,
        "page_count": 1,
        "file_size": 100,
        "pages": [{"page": 1, "width": 595.28, "height": 841.89, "text_chars": 12}],
        "metadata": {},
        "warnings": [],
    }
    rendered = {
        "success": True,
        "pages": [{"page": 1, "image_path": str(image_path)}],
    }

    with patch("src.tools.pdf.pdf_validator.inspect_pdf", return_value=inspection), \
         patch("src.tools.pdf.pdf_validator.render_pages", return_value=rendered):
        result = validate_pdf("/fake/sample.pdf", level="visual")

    assert result["success"] is True
    assert "接近全白" in result["warnings"][0]


def test_generated_pdf_validation_failure_turns_result_into_failure(tmp_path):
    from src.tools.pdf.pdf_process_tool import PdfProcessTool

    pdf_path = tmp_path / "generated.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")
    tool = PdfProcessTool()

    validation = {"success": False, "errors": ["PDF页数为0"], "warnings": []}
    with patch("src.tools.pdf.pdf_validator.validate_pdf", return_value=validation):
        result = tool._validate_generated_result({"success": True, "file_path": str(pdf_path)}, {})

    assert result["success"] is False
    assert "PDF生成后校验失败" in result["error"]
    assert result["validation"] == validation


def test_html_table_parser_preserves_content_table_content_order():
    from src.tools.pdf.pdf_writer import _split_html_by_tables

    parts = _split_html_by_tables(
        "<h1>Before</h1>"
        "<table><tr><th>A</th></tr><tr><td>1</td></tr></table>"
        "<p>After</p>"
    )

    assert [part["type"] for part in parts] == ["html", "table", "html"]
    assert "Before" in parts[0]["content"]
    assert parts[1]["data"] == [["A"], ["1"]]
    assert "After" in parts[2]["content"]
