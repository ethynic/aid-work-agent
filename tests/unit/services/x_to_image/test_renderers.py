"""x-to-image 渲染器测试（Phase 2）。

覆盖 src/services/x_to_image/renderers/ 下的三个渲染器：
- TextRenderer（纯文本 → <pre> HTML → 戏院）
- MarkdownRenderer（Markdown → HTML → 截图）
- HtmlRenderer（HTML 字符串 / 文件 → 截图）

分两类：
1. 注册测试（无浏览器）—— 验证 XToImageService 内置注册的三个渲染器类型与 name。
2. 真实渲染测试（需 Chromium）—— 通过 browser_pool.is_available() 探测，
   不可用时 pytest.skip()，使本测试在本地（有 Chromium）跑通、在 CI（可能无）跳过。

参见设计文档 §5.6 / §10。
"""
import tempfile
from pathlib import Path

import pytest

from src.services.x_to_image.models import XToImageInput, InputType
from src.services.x_to_image.service import x_to_image_service
from src.services.x_to_image.renderers.browser_pool import browser_pool
from src.services.x_to_image.renderers.text_renderer import TextRenderer, _build_html as text_build_html
from src.services.x_to_image.renderers.markdown_renderer import MarkdownRenderer
from src.services.x_to_image.renderers.html_renderer import HtmlRenderer

pytestmark = [pytest.mark.unit, pytest.mark.real_browser]


# ---------- 辅助 ----------


async def _render_to_png(renderer, source, content_type, **kw):
    """渲染器通用渲染辅助：探测 Chromium，不可用则 skip；可用则真正渲染返回路径。

    Returns:
        (paths: list[str], work_dir: Path)
    """
    if not await browser_pool.is_available():
        pytest.skip("Chromium unavailable")
    work_dir = Path(tempfile.mkdtemp(prefix="xti_test_"))
    inp = XToImageInput(source=source, content_type=content_type, **kw)
    paths = await renderer.render(inp, work_dir)
    return paths, work_dir


def _assert_valid_png(path: str, *, min_width_frac: float = 0.9) -> tuple:
    """断言路径是一个真实非空、非空白的 PNG，返回 (width, height, extrema)。"""
    from PIL import Image

    p = Path(path)
    assert p.exists(), f"PNG 文件不存在: {path}"
    assert p.stat().st_size > 0, f"PNG 文件大小为 0: {path}"
    with Image.open(p) as im:
        w, h = im.size
        assert w > 0, f"PNG 宽度为 0: {path}"
        assert h > 0, f"PNG 高度为 0: {path}"
        extrema = im.convert("L").getextrema()
        # 非空白：灰度 extrema 不应全是 255（全白）
        assert extrema != (255, 255), f"PNG 全白（空白）: {path}, extrema={extrema}"
    return w, h, extrema


# ============================================================
# 注册测试（无浏览器）
# ============================================================


def test_builtin_renderers_registered():
    """XToImageService 单例应注册 TEXT / MARKDOWN / HTML 三个渲染器。"""
    renderers = x_to_image_service._renderers
    assert InputType.TEXT in renderers, "TEXT 渲染器未注册"
    assert InputType.MARKDOWN in renderers, "MARKDOWN 渲染器未注册"
    assert InputType.HTML in renderers, "HTML 渲染器未注册"
    assert len(renderers) >= 3, f"注册渲染器数量 < 3: {len(renderers)}"


def test_registered_renderers_are_correct_classes():
    """三个注册的渲染器实例类型应分别为 TextRenderer/MarkdownRenderer/HtmlRenderer。"""
    renderers = x_to_image_service._renderers
    assert isinstance(renderers[InputType.TEXT], TextRenderer)
    assert isinstance(renderers[InputType.MARKDOWN], MarkdownRenderer)
    assert isinstance(renderers[InputType.HTML], HtmlRenderer)


def test_renderer_names_match_input_types():
    """每个渲染器的 .name 属性应为 "text"/"markdown"/"html"。"""
    renderers = x_to_image_service._renderers
    assert renderers[InputType.TEXT].name == "text"
    assert renderers[InputType.MARKDOWN].name == "markdown"
    assert renderers[InputType.HTML].name == "html"


# ============================================================
# TextRenderer（真实渲染，skip 守护）
# ============================================================


class TestTextRenderer:
    renderer = TextRenderer()

    async def test_plain_text_renders_valid_png(self):
        """渲染带换行和缩进的纯文本：1 张图，存在，非空，非空白，宽度≈视窗。"""
        source = (
            "Hello, World!\n"
            "    indented line\n"
            "second paragraph\n"
            "a < b & c > d\n"
            "line with\ttabs"
        )
        paths, _ = await _render_to_png(
            self.renderer, source, InputType.TEXT, width=600
        )
        assert isinstance(paths, list)
        assert len(paths) == 1, f"应返回 1 张页图，实际 {len(paths)}"
        w, h, _ = _assert_valid_png(paths[0])
        # full_page 截图宽度 == viewport 宽度（见 browser_pool.shoot）
        assert w >= 600 * 0.9, f"PNG 宽度 {w} 远小于视窗宽度 600"
        assert h > 0

    async def test_text_width_matches_input(self):
        """不同宽度应反映到截图宽度（full_page 截图宽度 == viewport 宽度）。"""
        source = "single line of text for width check"
        paths, _ = await _render_to_png(
            self.renderer, source, InputType.TEXT, width=700
        )
        w, _, _ = _assert_valid_png(paths[0])
        assert w >= 700 * 0.9, f"宽度 {w} 与输入 700 不符"

    async def test_html_special_chars_escaped_in_output(self):
        """渲染含 HTML 特殊字符的文本不崩溃、产出非空白图。"""
        source = "<b>not bold</b> & a < b > c"
        paths, _ = await _render_to_png(
            self.renderer, source, InputType.TEXT, width=500
        )
        assert len(paths) == 1
        _assert_valid_png(paths[0])


def test_text_build_html_escapes_special_chars():
    """TextRenderer 的 HTML 构造函数应转义 < > &，使文本不被当作标签解析（单元级断言）。

    _build_html 接收的是「已经转义后」的 safe_text（render 中先 html.escape 再传入），
    故这里模拟 render 的流程：先 escape(source) 再 _build_html，断言产物不含原始标签。
    """
    import html as html_lib

    raw = "<b>not bold</b> & a < b"
    safe = html_lib.escape(raw)
    doc = text_build_html(safe, width=400)
    # 转义后的 &lt; &gt; &amp; 应出现
    assert "&lt;b&gt;" in doc, "转义后的标签字符未出现在生成的 HTML 中"
    assert "&amp;" in doc
    # 原始（未转义的）<b> 标签不应作为活动标签出现在 <pre> 中
    # safe 文本里已不含字面 <b>，doc 中 <body>...<pre> 区域内也不应出现 <b>not bold</b>
    assert "<b>not bold</b>" not in doc, "未转义的 <b> 标签泄漏进 HTML"


# ============================================================
# MarkdownRenderer（真实渲染，skip 守护）
# ============================================================


class TestMarkdownRenderer:
    renderer = MarkdownRenderer()

    async def test_complex_markdown_renders_valid_png(self):
        """渲染含表格 + 代码块 + 标题 + 列表的 Markdown：1 张图，非空白，宽度≈视窗。"""
        source = (
            "# Project Title\n\n"
            "Some intro text.\n\n"
            "## Features\n\n"
            "- item one\n"
            "- item two\n"
            "- item three\n\n"
            "### Comparison Table\n\n"
            "| Name | Value | Notes |\n"
            "|------|-------|-------|\n"
            "| alpha | 1 | first |\n"
            "| beta | 2 | second |\n"
            "| gamma | 3 | third |\n\n"
            "```python\n"
            "def hello():\n"
            "    print('hi')\n"
            "```\n"
        )
        paths, _ = await _render_to_png(
            self.renderer, source, InputType.MARKDOWN, width=700
        )
        assert len(paths) == 1
        w, h, _ = _assert_valid_png(paths[0])
        assert w >= 700 * 0.9, f"宽度 {w} 与输入 700 不符"
        assert h > 0
        # 含表格+代码块的图应明显比纯文本更「高」
        # 断言高度合理（多元素渲染）
        assert h > 100, f"含表格/代码块的 Markdown 图高度过小: {h}"

    async def test_chinese_markdown_renders(self):
        """渲染含中文的 Markdown 不崩溃、非空白。"""
        source = "# 标题\n\n你好 **世界**\n\n- 列表项一\n- 列表项二\n"
        paths, _ = await _render_to_png(
            self.renderer, source, InputType.MARKDOWN, width=600
        )
        assert len(paths) == 1
        _assert_valid_png(paths[0])

    async def test_table_makes_image_taller_than_plain_text(self):
        """含表格的 Markdown 渲染高度应大于纯文本渲染高度（表格撑高）。

        注意：Playwright full_page 截图在内容「短于」视窗时返回视窗高度（800），
        因此这里用一张足够多行、能撑过视窗高度的表格，才能与短文本（被夹到
        视窗高度）形成高度差。
        """
        # 生成 ~40 行表格，足以超过默认 800px 视窗高度
        rows = "\n".join(
            f"| item-{i} | value-{i} |" for i in range(40)
        )
        md_with_table = (
            "# Many Rows Table\n\n"
            "| name | value |\n|------|-------|\n"
            f"{rows}\n"
        )
        plain = "just a short line of text"

        p_md, _ = await _render_to_png(
            self.renderer, md_with_table, InputType.MARKDOWN, width=600
        )
        p_txt, _ = await _render_to_png(
            self.renderer, plain, InputType.MARKDOWN, width=600
        )
        _, h_md, _ = _assert_valid_png(p_md[0])
        _, h_txt, _ = _assert_valid_png(p_txt[0])
        # 表格内容超过视窗 → 截图反映真实高度（>800）；短文本被夹到视窗高 800
        assert h_md > 800, (
            f"含 40 行表格的 Markdown 高度 {h_md} 未超过视窗高度 800"
        )
        assert h_md > h_txt, (
            f"含表格 Markdown 高度 {h_md} 未大于纯文本高度 {h_txt}"
        )

    def test_pygments_degradation(self):
        """若 pygments 未安装，MarkdownRenderer 应优雅降级（仍能产出 HTML）。

        本测试不依赖真实环境是否安装 pygments：直接调用 _markdown_to_html，
        断言它能处理含代码块的 Markdown 并返回非空 HTML 字符串。
        进一步，若 pygments 不可导入，函数应回退到无 codehilite 的扩展集合。
        """
        from src.services.x_to_image.renderers.markdown_renderer import (
            _markdown_to_html,
        )

        src = "```python\nprint(1)\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
        html = _markdown_to_html(src)
        assert isinstance(html, str)
        assert len(html) > 0
        # 代码块与表格应被渲染为对应 HTML 元素
        assert "<code" in html or "<pre" in html, "代码块未渲染"
        assert "<table" in html.lower(), "表格未渲染"


# ============================================================
# HtmlRenderer（真实渲染，skip 守护）
# ============================================================


class TestHtmlRenderer:
    renderer = HtmlRenderer()

    async def test_html_fragment_renders(self):
        """渲染 HTML 片段（is_file_path=False）：非空白 PNG。"""
        source = "<h1>Hi</h1><p>This is a <strong>fragment</strong> paragraph.</p>"
        paths, _ = await _render_to_png(
            self.renderer, source, InputType.HTML, width=600, is_file_path=False
        )
        assert len(paths) == 1
        _assert_valid_png(paths[0])

    async def test_full_html_document_renders(self):
        """渲染完整 HTML 文档（含 DOCTYPE，is_file_path=False）：用户样式被尊重，非空白。"""
        source = (
            "<!DOCTYPE html>\n"
            "<html><head><meta charset='utf-8'>"
            "<style>body{background:#eef;margin:0;padding:24px;}"
            "h2{color:#c0392b;}</style></head>"
            "<body><h2>Full Document</h2>"
            "<p>Styled by user CSS.</p></body></html>"
        )
        paths, _ = await _render_to_png(
            self.renderer, source, InputType.HTML, width=600, is_file_path=False
        )
        assert len(paths) == 1
        w, h, extrema = _assert_valid_png(paths[0])
        # 用户设置了 #eef 背景（非纯白），extrema 的最小值应 < 255
        assert extrema[0] < 255, (
            f"完整文档应使用用户背景色 #eef（非全白），extrema={extrema}"
        )

    async def test_html_from_file_renders(self):
        """从 .html 文件渲染（is_file_path=True）：非空白 PNG。"""
        work_dir = Path(tempfile.mkdtemp(prefix="xti_test_"))
        html_file = work_dir / "page.html"
        html_file.write_text(
            "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
            "<body><h1>From File</h1><p>loaded from disk.</p></body></html>",
            encoding="utf-8",
        )
        paths, _ = await _render_to_png(
            self.renderer,
            str(html_file),
            InputType.HTML,
            width=600,
            is_file_path=True,
        )
        assert len(paths) == 1
        _assert_valid_png(paths[0])

    async def test_missing_file_raises(self):
        """从不存在文件渲染（is_file_path=True）应抛 ValueError/FileNotFoundError。"""
        if not await browser_pool.is_available():
            pytest.skip("Chromium unavailable")
        nonexistent = "/nonexistent/path/does_not_exist.html"
        inp = XToImageInput(
            source=nonexistent,
            content_type=InputType.HTML,
            width=600,
            is_file_path=True,
        )
        work_dir = Path(tempfile.mkdtemp(prefix="xti_test_"))
        with pytest.raises((ValueError, FileNotFoundError)):
            await self.renderer.render(inp, work_dir)


# ============================================================
# 自定义宽度参数贯通测试
# ============================================================


async def test_width_param_flows_through():
    """width=400 vs width=1000 的截图宽度应不同（400 更窄），验证 width 参数贯通。"""
    md = "# Width Test\n\n| col1 | col2 |\n|------|------|\n| a | b |\n| c | d |\n"
    md_renderer = MarkdownRenderer()

    if not await browser_pool.is_available():
        pytest.skip("Chromium unavailable")

    narrow, _ = await _render_to_png(
        md_renderer, md, InputType.MARKDOWN, width=400
    )
    wide, _ = await _render_to_png(
        md_renderer, md, InputType.MARKDOWN, width=1000
    )
    w_narrow, _, _ = _assert_valid_png(narrow[0])
    w_wide, _, _ = _assert_valid_png(wide[0])
    assert w_narrow < w_wide, (
        f"窄宽图宽度 {w_narrow} 未小于宽宽图宽度 {w_wide} —— width 参数可能未贯通"
    )
    assert w_narrow <= 400 + 50, f"width=400 截图宽度 {w_narrow} 偏大"
    assert w_wide >= 1000 * 0.9, f"width=1000 截图宽度 {w_wide} 偏小"
