"""Phase 4 HTML high-fidelity PPTX export tests."""

from pathlib import Path

import pytest
from PIL import Image, ImageChops

from src.tools.ppt.html_exporter import HtmlExporter, HtmlExportError
from src.tools.ppt.ppt_capabilities import get_capabilities

pytestmark = [pytest.mark.tools, pytest.mark.real_browser]


def _require_playwright():
    capability = get_capabilities()["html_export"]
    if not capability["available"]:
        pytest.skip(f"真实 Playwright 测试跳过: {capability['reason']}")


def _deck_html() -> str:
    return """<!doctype html>
<html><head><style>
html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; }
.deck { display: flex; width: 200vw; transform: translateX(-100vw); }
section.slide { width: 100vw; height: 100vh; flex: none; position: relative; }
.first { background: #f5f7fa; color: #17324d; }
.second { background: #102a43; color: white; }
h1 { margin: 0; padding: 100px; font: 72px Arial; }
canvas { position: absolute; left: 200px; top: 260px; }
</style></head><body><main class="deck">
<section class="slide first"><h1>第一页</h1></section>
<section class="slide second"><h1>Canvas 页面</h1><canvas id="chart" width="600" height="300"></canvas></section>
</main><script>
const ctx = document.getElementById("chart").getContext("2d");
ctx.fillStyle = "#22c55e"; ctx.fillRect(0, 0, 600, 300);
ctx.fillStyle = "#facc15"; ctx.fillRect(80, 50, 180, 180);
</script></body></html>"""


def _assert_nonblank(path: str) -> None:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        flat = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
        assert ImageChops.difference(rgb, flat).getbbox() is not None


def test_real_playwright_exports_multi_page_canvas_without_blank_pages(tmp_path):
    _require_playwright()
    result = HtmlExporter(viewport_width=960, viewport_height=540).export(
        _deck_html(), tmp_path, title="真实浏览器测试"
    )

    assert len(result.spec.slides) == 2
    assert [item.page_number for item in result.screenshots] == [1, 2]
    assert all(item.width == 960 and item.height == 540 for item in result.screenshots)
    assert all(item.duration_ms >= 0 and item.size_bytes > 512 for item in result.screenshots)
    assert all(slide.nodes[0].type == "raster" for slide in result.spec.slides)
    for screenshot in result.screenshots:
        _assert_nonblank(screenshot.path)


def test_real_playwright_accepts_html_file_and_single_page_fallback(tmp_path):
    _require_playwright()
    html_path = tmp_path / "single.html"
    html_path.write_text(
        "<html><body style='margin:0;background:white'><h1>单页文件</h1></body></html>",
        encoding="utf-8",
    )

    result = HtmlExporter(viewport_width=800, viewport_height=450).export(
        html_path, tmp_path / "images", source_is_file=True
    )

    assert len(result.screenshots) == 1
    _assert_nonblank(result.screenshots[0].path)


def test_real_playwright_blocks_file_resources_outside_html_directory(tmp_path):
    _require_playwright()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (tmp_path / "outside.js").write_text(
        "document.body.innerHTML = ''; document.body.style.background = 'white';",
        encoding="utf-8",
    )
    html_path = source_dir / "deck.html"
    html_path.write_text(
        """<html><body style="margin:0;background:#135;color:white">
        <h1>受保护页面</h1><script src="../outside.js"></script></body></html>""",
        encoding="utf-8",
    )

    result = HtmlExporter(viewport_width=800, viewport_height=450).export(
        html_path, tmp_path / "images", source_is_file=True
    )

    _assert_nonblank(result.screenshots[0].path)


def test_real_playwright_handles_failed_image_and_no_section(tmp_path):
    _require_playwright()
    result = HtmlExporter(
        viewport_width=800, viewport_height=450, timeout_ms=2000
    ).export(
        """<html><body style="margin:0;background:#123456;color:white">
        <img src="https://invalid.example/not-loaded.png">
        <h1>外部图片失败仍可导出</h1></body></html>""",
        tmp_path,
    )

    assert len(result.screenshots) == 1
    _assert_nonblank(result.screenshots[0].path)


@pytest.mark.parametrize("image_format,extension", [("png", ".png"), ("jpeg", ".jpg")])
def test_real_playwright_supports_png_and_jpeg(tmp_path, image_format, extension):
    _require_playwright()
    result = HtmlExporter(
        viewport_width=640,
        viewport_height=360,
        image_format=image_format,
        jpeg_quality=80,
    ).export(
        "<html><body style='margin:0;background:#135'><h1 style='color:white'>格式</h1></body></html>",
        tmp_path,
    )

    assert Path(result.screenshots[0].path).suffix == extension
    assert result.screenshots[0].width == 640
    assert result.screenshots[0].height == 360


def test_real_playwright_rejects_blank_page(tmp_path):
    _require_playwright()
    with pytest.raises(HtmlExportError, match="空白页"):
        HtmlExporter(viewport_width=640, viewport_height=360).export(
            "<html><body style='margin:0;background:white'></body></html>",
            tmp_path,
        )


def test_real_playwright_restores_slide_inline_styles(tmp_path):
    _require_playwright()
    html = """<html><body><main style="transform:translateX(-100vw)">
    <section class="slide" style="display:flex;background:#246;color:white"><h1>A</h1></section>
    <section class="slide" style="display:grid;background:#642;color:white"><h1>B</h1></section>
    </main></body></html>"""
    exporter = HtmlExporter(viewport_width=640, viewport_height=360)
    result = exporter.export(html, tmp_path)

    assert len(result.screenshots) == 2
    assert all(item.size_bytes > 512 for item in result.screenshots)


def test_exporter_rejects_out_of_range_timeout():
    with pytest.raises(ValueError, match="timeout"):
        HtmlExporter(timeout_ms=999)


@pytest.mark.asyncio
async def test_tool_both_returns_distinct_files(tmp_path, monkeypatch):
    from src.tools.ppt.ppt_process_tool import PptProcessTool
    from src.tools.ppt.renderer import NodePptRenderer

    monkeypatch.setenv("PPT_ENABLE_HTML_EXPORT", "true")
    monkeypatch.setenv("PPT_HTML_VIEWPORT_WIDTH", "800")
    monkeypatch.setenv("PPT_HTML_VIEWPORT_HEIGHT", "450")
    monkeypatch.setattr(PptProcessTool, "_get_output_dir", lambda self: tmp_path)
    monkeypatch.setattr(PptProcessTool, "_check_node_renderer_ready", lambda self: True)

    def fake_render(self, spec, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"x" * (11 * 1024))
        return {
            "file_path": str(output_path),
            "qa": {"slide_count": len(spec.slides), "node_count": len(spec.slides)},
        }

    monkeypatch.setattr(NodePptRenderer, "render", fake_render)
    result = await PptProcessTool().execute(
        content="<html><body><h1>高保真</h1></body></html>",
        content_type="html",
        export_mode="both",
    )

    assert result["success"] is True
    assert result["export_mode"] == "both"
    assert result["file_path"].endswith("_editable.pptx")
    assert result["alternate_file_path"].endswith("_high_fidelity.pptx")
    assert result["file_path"] != result["alternate_file_path"]
    assert all("path" not in item for item in result["screenshots"])
    assert not list(tmp_path.glob(".*_html_assets-*"))


@pytest.mark.asyncio
async def test_real_tool_renders_before_cleaning_screenshot_directory(tmp_path, monkeypatch):
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    if not get_capabilities()["node_renderer"]["available"]:
        pytest.skip("真实 Node renderer 测试跳过")
    monkeypatch.setenv("PPT_ENABLE_HTML_EXPORT", "true")
    monkeypatch.setenv("PPT_HTML_VIEWPORT_WIDTH", "800")
    monkeypatch.setenv("PPT_HTML_VIEWPORT_HEIGHT", "450")
    monkeypatch.setattr(PptProcessTool, "_get_output_dir", lambda self: tmp_path)

    result = await PptProcessTool().execute(
        content=_deck_html(),
        content_type="html",
        output_name="真实端到端",
        export_mode="high_fidelity",
    )

    assert result["success"] is True
    assert result["slide_count"] == 2
    assert Path(result["file_path"]).is_file()
    assert Path(result["file_path"]).stat().st_size > 10 * 1024
    assert all("path" not in item for item in result["screenshots"])
    assert not list(tmp_path.glob(".*_html_assets-*"))


@pytest.mark.asyncio
async def test_tool_supports_editable_export(monkeypatch):
    from src.tools.ppt.ppt_process_tool import PptProcessTool

    monkeypatch.setenv("PPT_ENABLE_HTML_EXPORT", "true")
    result = await PptProcessTool().execute(
        content="<html><body><h1>不可伪造</h1></body></html>",
        content_type="html",
        export_mode="editable",
    )

    assert result["success"] is True
    assert result["export_mode"] == "editable"
    assert result["qa_summary"]["native_text_count"] == 1
