"""
渲染器基类。

所有具体渲染器（文本 / Markdown / HTML）继承 ImageRendererBase，
把任意输入最终转成一段 HTML，交给 browser_pool 做 headless 全页长截图。
"""
from pathlib import Path
from typing import List

from src.services.x_to_image.models import XToImageInput


class ImageRendererBase:
    """渲染器抽象基类。"""

    name: str = "base"

    async def render(self, inp: XToImageInput, work_dir: Path) -> List[str]:
        """
        渲染输入内容为一组页图 PNG 路径（落在 work_dir）。

        Args:
            inp: 转换输入
            work_dir: 本次转换的临时工作目录

        Returns:
            渲染得到的页图 PNG 绝对路径列表（v1 文本/MD/HTML 通常为 1 张）。

        Raises:
            NotImplementedError: 子类必须实现。
        """
        raise NotImplementedError

    async def _shoot_full_page(self, html: str, inp: XToImageInput, work_dir: Path) -> str:
        """
        通用：把 HTML 设置进页面并做全页长截图。

        Args:
            html: 完整 HTML 字符串
            inp: 转换输入（取 width 作为视窗宽度）
            work_dir: 截图输出目录

        Returns:
            生成的 PNG 文件绝对路径。
        """
        from .browser_pool import browser_pool
        return await browser_pool.shoot(html, width=inp.width, out_dir=work_dir)
