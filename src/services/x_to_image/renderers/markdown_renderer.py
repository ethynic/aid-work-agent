"""
Markdown 渲染器。

把 Markdown 转成 HTML（表格 / 代码块 / 代码高亮 / 目录扩展），
套用面向长图的样式模板（表头底色、代码块底色、max-width 放宽），
再交给 browser_pool 做 headless 全页长截图。参见设计文档 §5.6。

样式思路复刻 src/channels/wecom_kf/renderer.py 的 WeComKfRenderer.HTML_TEMPLATE，
但放宽 max-width 以适配长图。
"""
from pathlib import Path
from typing import List

from loguru import logger

from src.services.x_to_image.models import XToImageInput
from src.services.x_to_image.renderers.base import ImageRendererBase


class MarkdownRenderer(ImageRendererBase):
    """Markdown → HTML → 截图。"""

    name = "markdown"

    async def render(self, inp: XToImageInput, work_dir: Path) -> List[str]:
        """
        将 Markdown 渲染为一张全页长图。

        Args:
            inp: 转换输入（source 为 Markdown，width 为视窗宽度）
            work_dir: 本次转换的临时工作目录

        Returns:
            含一张页图 PNG 绝对路径的列表。
        """
        html_body = _markdown_to_html(inp.source)
        full_html = _build_html(html_body, inp.width)
        path = await self._shoot_full_page(full_html, inp, work_dir)
        logger.debug(f"MarkdownRenderer 渲染完成: {path}")
        return [path]


def _markdown_to_html(source: str) -> str:
    """
    Markdown → HTML。

    优先启用 codehilite（代码语法高亮，需 pygments）；若 pygments 未安装，
    codehilite 扩展会 import 失败，优雅降级为不含 codehilite 的扩展集合。
    """
    try:
        import markdown
    except ImportError as e:
        # markdown 已在 requirements.txt 中固定，理论上不会走到这里；
        # 真发生时让异常向上抛，由 service.convert 统一转失败结果。
        raise ImportError(f"markdown 库未安装，无法渲染 Markdown: {e}") from e

    base_extensions = ["tables", "fenced_code", "toc"]
    try:
        # codehilite 依赖 pygments；pygments 未安装时该 import 会失败
        import pygments  # noqa: F401
        extensions = base_extensions + ["codehilite"]
        return markdown.markdown(source, extensions=extensions)
    except ImportError:
        logger.warning("pygments 未安装，Markdown 代码块将以纯文本呈现（不语法高亮）")
        return markdown.markdown(source, extensions=base_extensions)


def _build_html(html_body: str, width: int) -> str:
    """构造完整 HTML 文档（DOCTYPE / charset utf-8 / 长图样式 / body）。"""
    return """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<style>
  html, body {{
    margin: 0;
    padding: 0;
    background: #fff;
    color: #333;
  }}
  body {{
    width: {width}px;
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 15px;
    line-height: 1.7;
    padding: 16px;
  }}
  table {{
    border-collapse: collapse;
    width: 100%;
    max-width: 100%;
  }}
  th {{
    background: #f0f0f0;
    font-weight: bold;
    text-align: left;
    padding: 6px 10px;
    border: 1px solid #d0d0d0;
  }}
  td {{
    padding: 6px 10px;
    border: 1px solid #d0d0d0;
  }}
  tr:nth-child(even) td {{
    background: #fafafa;
  }}
  pre, code {{
    background: #f6f8fa;
  }}
  pre {{
    padding: 10px;
    border-radius: 4px;
    overflow-x: auto;
  }}
  h1, h2, h3 {{
    margin-top: 1em;
  }}
  img {{
    max-width: 100%;
  }}
</style>
</head>
<body>
{content}
</body>
</html>""".format(width=width, content=html_body)
