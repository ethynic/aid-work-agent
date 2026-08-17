"""
x-to-image 核心服务。

将文本 / Markdown / HTML 等内容渲染为一张长图，写入系统临时目录。

镜像 NotificationService 的「注册表 + register_xxx 扩展」模式：
- __init__ 初始化渲染器注册表（dict[InputType, ImageRendererBase]）
- register_input_type 注册新输入类型渲染器（扩展点）
- convert 主流程：建临时工作目录 → 选渲染器 → render → 拼接/尺寸控制

参见设计文档 §5.4。
"""
import tempfile
from pathlib import Path
from typing import Dict

from loguru import logger

from src.services.x_to_image.models import XToImageInput, XToImageResult, InputType
from src.services.x_to_image.renderers.base import ImageRendererBase


class XToImageService:
    """x-to-image 核心服务：将文本/Markdown/HTML 等渲染为一张长图（写入临时目录）。"""

    def __init__(self):
        self._renderers: Dict[InputType, ImageRendererBase] = {}
        self._register_builtin()

    def _register_builtin(self):
        """注册内置渲染器（局部导入，避免 import 时序/循环依赖）。"""
        from .renderers.text_renderer import TextRenderer
        from .renderers.markdown_renderer import MarkdownRenderer
        from .renderers.html_renderer import HtmlRenderer
        self.register_input_type(InputType.TEXT, TextRenderer())
        self.register_input_type(InputType.MARKDOWN, MarkdownRenderer())
        self.register_input_type(InputType.HTML, HtmlRenderer())

    def register_input_type(self, content_type: InputType, renderer: ImageRendererBase):
        """注册输入类型渲染器（扩展点）。"""
        self._renderers[content_type] = renderer
        logger.info(f"注册 x-to-image 渲染器: content_type={content_type}, renderer={renderer.name}")

    async def convert(self, inp: XToImageInput) -> XToImageResult:
        """
        将输入内容转换为一张长图。

        Args:
            inp: 转换输入

        Returns:
            转换结果，含临时图片绝对路径（成功）或错误信息（失败）。
        """
        renderer = self._renderers.get(inp.content_type)
        if not renderer:
            return XToImageResult(
                success=False,
                error=f"不支持的输入类型: {inp.content_type}",
            )

        try:
            # 1. 建临时工作目录（mkdtemp，落在进程 TMPDIR）
            work_dir = Path(tempfile.mkdtemp(prefix="x_to_image_"))
            # 2. 渲染 → 一组页图路径（v1 文本/MD/HTML 通常为 1 张）
            page_paths = await renderer.render(inp, work_dir)
            if not page_paths:
                return XToImageResult(
                    success=False,
                    error="渲染未产生图片",
                    renderer=renderer.name,
                )
            # 3. 纵向拼接为单张长图 + 尺寸/体积控制（写入 work_dir）
            # Phase 3: finalize_long_image
            from .image_utils import finalize_long_image
            return await finalize_long_image(
                page_paths, inp, work_dir, renderer_name=renderer.name
            )
        except Exception as e:
            logger.opt(exception=True).error(f"[XToImage] 转换失败: {e}")
            return XToImageResult(success=False, error=str(e))


# 模块级单例
x_to_image_service = XToImageService()
