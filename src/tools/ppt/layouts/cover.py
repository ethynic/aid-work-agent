"""
封面页渲染器

居中大标题 + 副标题 + 演讲者/日期信息。
"""

from pptx.enum.text import PP_ALIGN

from src.tools.ppt.theme import PPTTheme, CANVAS_WIDTH, CANVAS_HEIGHT
from src.tools.ppt.utils import (
    set_slide_bg, add_textbox, add_shape_rect,
)


def render_cover(slide, data: dict, theme: PPTTheme):
    """渲染封面页。"""
    # 背景
    set_slide_bg(slide, theme.bg)

    # 左侧装饰条
    add_shape_rect(
        slide,
        x=0, y=0, w=0.15, h=CANVAS_HEIGHT,
        fill_color=theme.accent,
    )

    # 大标题
    title = data.get("title", "")
    add_textbox(
        slide, title,
        x=1.5, y=2.0, w=10.3, h=2.0,
        font_size=48, bold=True, color=theme.primary,
        align="center",
    )

    # 装饰线
    add_shape_rect(
        slide,
        x=5.5, y=4.1, w=2.3, h=0.06,
        fill_color=theme.accent,
    )

    # 副标题
    subtitle = data.get("subtitle", "")
    add_textbox(
        slide, subtitle,
        x=2.5, y=4.4, w=8.3, h=0.8,
        font_size=20, color=theme.secondary,
        align="center",
    )

    # 演讲者 + 日期
    presenter = data.get("presenter", "")
    date_str = data.get("date", "")
    meta_parts = [p for p in [presenter, date_str] if p]
    meta = "  ·  ".join(meta_parts)
    if meta:
        add_textbox(
            slide, meta,
            x=3.0, y=5.6, w=7.3, h=0.5,
            font_size=14, color=theme.accent,
            align="center",
        )

    # 右下角装饰圆点
    add_shape_rect(
        slide,
        x=12.2, y=6.6, w=0.3, h=0.3,
        fill_color=theme.light,
    )
