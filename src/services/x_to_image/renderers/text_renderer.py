"""
纯文本渲染器。

把纯文本转成一段完整 HTML（<pre> 包裹，保留换行/缩进，自动换行），
再交给 browser_pool 做 headless 全页长截图。参见设计文档 §5.6。
"""
import html as html_lib
from pathlib import Path
from typing import List

from loguru import logger

from src.services.x_to_image.models import XToImageInput
from src.services.x_to_image.renderers.base import ImageRendererBase


class TextRenderer(ImageRendererBase):
    """纯文本 → <pre> HTML → 截图。"""

    name = "text"

    async def render(self, inp: XToImageInput, work_dir: Path) -> List[str]:
        """
        将纯文本渲染为一张全页长图。

        Args:
            inp: 转换输入（source 为纯文本，width 为视窗宽度）
            work_dir: 本次转换的临时工作目录

        Returns:
            含一张页图 PNG 绝对路径的列表。
        """
        # HTML 转义，避免文本中的 < > & 等被当作标签/实体解析
        safe = html_lib.escape(inp.source)
        full_html = _build_html(safe, inp.width)
        path = await self._shoot_full_page(full_html, inp, work_dir)
        logger.debug(f"TextRenderer 渲染完成: {path}")
        return [path]


def _build_html(safe_text: str, width: int) -> str:
    """构造完整 HTML 文档（DOCTYPE / charset utf-8 / 样式 / body）。"""
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
    /* 文档默认字体栈，作为兜底（pre 自身再指定等宽栈） */
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  }}
  pre {{
    white-space: pre-wrap;
    word-break: break-word;
    font-family: "SFMono-Regular", Consolas, "Microsoft YaHei", monospace;
    font-size: 14px;
    line-height: 1.6;
    margin: 0;
    padding: 16px;
  }}
</style>
</head>
<body>
<pre>{content}</pre>
</body>
</html>""".format(width=width, content=safe_text)
