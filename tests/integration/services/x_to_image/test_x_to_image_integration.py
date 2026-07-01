"""x-to-image 端到端集成测试。

验证完整管线：输入 → 渲染器(headless Playwright) → image_utils(拼接/截断/体积) → 临时目录输出。
需要真实 Chromium，无 Chromium 时整体 skip。
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from src.services.x_to_image import XToImageInput, InputType, x_to_image_service
from src.services.x_to_image.renderers.browser_pool import browser_pool

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _skip_if_no_chromium():
    """无 Chromium 时 skip 本测试模块。"""
    if not await browser_pool.is_available():
        pytest.skip("Chromium 不可用，跳过端到端集成测试")


# ---------- 各内容类型端到端 ----------


@pytest.mark.parametrize(
    "content_type, source, label",
    [
        (
            InputType.TEXT,
            "第一行文本\n第二行\n    缩进行\n特殊字符 <b> & 'quote'",
            "纯文本",
        ),
        (
            InputType.MARKDOWN,
            "# 标题\n\nMarkdown **加粗** 内容\n\n"
            "| 列A | 列B |\n|-----|-----|\n| 1 | 2 |\n| 3 | 4 |\n\n"
            "```python\nprint('hello')\n```\n\n- 列表项一\n- 列表项二",
            "Markdown（表格+代码块+列表）",
        ),
        (
            InputType.HTML,
            "<div style='padding:20px;font-family:sans-serif;'>"
            "<h2 style='color:#c0392b;'>HTML 片段</h2>"
            "<p>用户自定义 <strong>样式</strong> 内容。</p>"
            "<ul><li>项目一</li><li>项目二</li></ul></div>",
            "HTML 片段",
        ),
    ],
)
async def test_convert_each_content_type_end_to_end(content_type, source, label):
    """文本/Markdown/HTML 各一条真实样例，端到端转换并校验输出。"""
    await _skip_if_no_chromium()

    result = await x_to_image_service.convert(
        XToImageInput(source=source, content_type=content_type)
    )

    # 1. 成功
    assert result.success, f"{label} 转换失败: {result.error}"
    assert result.error is None

    # 2. image_path 存在且非空，位于临时目录之下
    assert result.image_path, f"{label} 未返回 image_path"
    p = Path(result.image_path)
    assert p.exists(), f"{label} 输出文件不存在: {p}"
    assert p.is_file()
    assert result.file_size > 0, f"{label} 文件大小为 0"
    assert p.stat().st_size == result.file_size, f"{label} 报告大小与实际不符"

    # 位于系统临时目录之下
    tmp_root = Path(tempfile.gettempdir()).resolve()
    assert tmp_root in p.resolve().parents or p.resolve() == tmp_root, (
        f"{label} 输出不在临时目录 {tmp_root} 之下: {p.resolve()}"
    )

    # 3. 尺寸合理
    assert result.width > 0 and result.height > 0, f"{label} 尺寸异常"
    assert result.height <= 20_000, (
        f"{label} 高度 {result.height} 超过默认 max_height(20000)"
    )

    # 4. 用 PIL 打开校验真实图片且非空白
    from PIL import Image

    with Image.open(p) as img:
        assert img.width == result.width and img.height == result.height, (
            f"{label} PIL 尺寸与报告不符: {img.size} vs ({result.width},{result.height})"
        )
        extrema = img.convert("L").getextrema()
        assert extrema != (255, 255), f"{label} 输出为空白图片"

    # 5. 渲染器名匹配
    assert result.renderer == content_type.value


# ---------- 截断行为 ----------


async def test_convert_truncates_overlong_content():
    """超长内容（生成高度超过 max_height）应被截断并标记 truncated。"""
    await _skip_if_no_chromium()

    # 构造一个很长的 Markdown（大量表格行），预期渲染高度远超 small_max
    rows = "\n".join(f"| 行{i} | 数据{i} |" for i in range(2000))
    long_md = "# 长内容测试\n\n" + rows

    small_max = 1000  # 远小于渲染出的高度，强制触发截断
    result = await x_to_image_service.convert(
        XToImageInput(source=long_md, content_type=InputType.MARKDOWN, max_height=small_max)
    )

    assert result.success, f"截断测试转换失败: {result.error}"
    assert result.truncated is True, "超长内容应标记 truncated=True"
    # 截断后高度 = max_height + 40px 提示条
    assert result.height == small_max + 40, (
        f"截断后高度 {result.height} != max_height({small_max})+40"
    )


# ---------- HTML 文件路径输入 ----------


async def test_convert_html_from_file_path():
    """is_file_path=True，source 为 .html 文件路径。"""
    await _skip_if_no_chromium()

    work_dir = Path(tempfile.mkdtemp(prefix="xti_int_html_"))
    html_file = work_dir / "sample.html"
    html_file.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
        "<body><h1>来自文件的 HTML</h1><p>文件路径输入测试。</p></body></html>",
        encoding="utf-8",
    )

    result = await x_to_image_service.convert(
        XToImageInput(
            source=str(html_file), content_type=InputType.HTML, is_file_path=True
        )
    )

    assert result.success, f"HTML 文件转换失败: {result.error}"
    assert Path(result.image_path).exists()
    assert result.renderer == "html"


async def test_convert_missing_html_file_fails_cleanly():
    """不存在的 HTML 文件路径应返回失败（而非抛异常）。"""
    await _skip_if_no_chromium()

    result = await x_to_image_service.convert(
        XToImageInput(
            source="/nonexistent/path/no.html",
            content_type=InputType.HTML,
            is_file_path=True,
        )
    )

    assert result.success is False
    assert result.error  # 有错误信息


# ---------- 自定义宽度 ----------


async def test_convert_custom_width_affects_output():
    """自定义宽度应反映到输出图片宽度。"""
    await _skip_if_no_chromium()

    md = "# 宽度测试\n\n一段用于宽度比较的文本内容。"

    narrow = await x_to_image_service.convert(
        XToImageInput(source=md, content_type=InputType.MARKDOWN, width=400)
    )
    wide = await x_to_image_service.convert(
        XToImageInput(source=md, content_type=InputType.MARKDOWN, width=1000)
    )

    assert narrow.success and wide.success
    assert wide.width > narrow.width, (
        f"宽版({wide.width})应大于窄版({narrow.width})"
    )
