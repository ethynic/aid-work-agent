"""
python-pptx 兜底渲染：图片页布局

主渲染路径为 pptxgenjs（spec_builder._image）；本模块仅在 Node 渲染器不可用
回退 python-pptx 时使用。文件缺失/插入失败降级为文字页，不中断生成。
"""

import os

from loguru import logger

from src.tools.ppt.theme import CANVAS_WIDTH, PPTTheme
from src.tools.ppt.utils import add_page_number, add_textbox, set_slide_bg

# 图片可用区域（英寸）：标题下方、说明上方，左右居中
_IMAGE_BOX_W = 9.6
_IMAGE_BOX_H = 4.4
_IMAGE_TOP = 1.5
_CAPTION_TOP = 6.1


def render_image(slide, data: dict, theme: PPTTheme, index: int, total: int):
    """渲染图片页：标题 + 等比适配图片 + 说明文字。"""
    set_slide_bg(slide, theme.bg)
    add_textbox(
        slide, data.get("title", "图片"),
        x=0.8, y=0.4, w=11.7, h=0.9,
        font_size=28, bold=True, color=theme.primary,
    )

    image_path = str(data.get("image_path", "") or "").strip()
    raw_caption = data.get("caption") or data.get("points") or ""
    if isinstance(raw_caption, (list, tuple)):
        caption = "\n".join(str(item) for item in raw_caption if str(item).strip())
    else:
        caption = str(raw_caption).strip()
    placed = False
    if image_path and os.path.isfile(image_path):
        try:
            _add_fitted_picture(slide, image_path)
            placed = True
        except Exception as e:
            logger.warning(f"[render_image] 图片插入失败，降级为文字页: {e}")

    if not placed:
        add_textbox(
            slide, f"（图片不可用：{os.path.basename(image_path) or '未指定'}）\n{caption}",
            x=1.7, y=_IMAGE_TOP, w=_IMAGE_BOX_W, h=_IMAGE_BOX_H,
            font_size=16, color=theme.secondary,
        )
    elif caption:
        add_textbox(
            slide, caption,
            x=1.2, y=_CAPTION_TOP, w=10.9, h=0.8,
            font_size=14, color=theme.secondary,
        )

    add_page_number(slide, index, total, color=theme.secondary)


def _add_fitted_picture(slide, path: str):
    """等比适配插入图片：先按高缩放，超宽则改按宽缩放，水平居中。"""
    from pptx.util import Inches

    picture = slide.shapes.add_picture(path, Inches(0), Inches(_IMAGE_TOP), height=Inches(_IMAGE_BOX_H))
    if picture.width > Inches(_IMAGE_BOX_W):
        ratio = Inches(_IMAGE_BOX_W) / picture.width
        picture.width = Inches(_IMAGE_BOX_W)
        picture.height = int(picture.height * ratio)
    emu_per_inch = 914400
    left = (CANVAS_WIDTH - picture.width / emu_per_inch) / 2
    picture.left = Inches(left)
    return picture
