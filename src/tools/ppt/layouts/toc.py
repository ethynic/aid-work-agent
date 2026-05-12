"""
目录页渲染器

支持 vertical（竖向列表）和 grid（网格卡片）两种子布局。
"""

from src.tools.ppt.theme import PPTTheme, CANVAS_WIDTH
from src.tools.ppt.utils import (
    set_slide_bg, add_textbox, add_shape_rect,
    add_shape_rounded_rect, add_page_number,
)


def render_toc(slide, data: dict, theme: PPTTheme, index: int, total: int):
    """渲染目录页。"""
    set_slide_bg(slide, theme.bg)

    layout = data.get("layout", "grid")
    sections = data.get("sections", [])

    # 页面标题
    add_textbox(
        slide, data.get("title", "目录"),
        x=0.8, y=0.5, w=5.0, h=1.0,
        font_size=32, bold=True, color=theme.primary,
    )

    # 标题下装饰线
    add_shape_rect(slide, x=0.8, y=1.35, w=1.5, h=0.05, fill_color=theme.accent)

    if layout == "grid" or len(sections) <= 6:
        _render_grid(slide, sections, theme)
    else:
        _render_vertical(slide, sections, theme)

    add_page_number(slide, index, total, color=theme.secondary)


def _render_grid(slide, sections: list, theme: PPTTheme):
    """网格卡片布局。"""
    n = len(sections)
    if n == 0:
        return

    cols = 3 if n <= 3 else min(3, n)
    rows = (n + cols - 1) // cols

    card_w = (CANVAS_WIDTH - 1.6 - (cols - 1) * 0.4) / cols
    card_h = 2.0
    start_y = 2.0

    for i, sec in enumerate(sections):
        row = i // cols
        col = i % cols
        x = 0.8 + col * (card_w + 0.4)
        y = start_y + row * (card_h + 0.3)

        # 卡片背景
        add_shape_rounded_rect(
            slide, x, y, card_w, card_h,
            fill_color=theme.light,
            corner_radius=0.1,
        )

        # 编号
        number = sec.get("number", str(i + 1).zfill(2))
        add_textbox(
            slide, number,
            x=x + 0.3, y=y + 0.3, w=card_w - 0.6, h=0.6,
            font_size=28, bold=True, color=theme.accent,
        )

        # 标题
        title = sec.get("title", "")
        add_textbox(
            slide, title,
            x=x + 0.3, y=y + 1.0, w=card_w - 0.6, h=0.7,
            font_size=16, bold=True, color=theme.primary,
        )


def _render_vertical(slide, sections: list, theme: PPTTheme):
    """竖向列表布局。"""
    start_y = 2.0
    item_h = 0.7

    for i, sec in enumerate(sections):
        y = start_y + i * item_h
        if y > 6.5:
            break

        number = sec.get("number", str(i + 1).zfill(2))
        title = sec.get("title", "")

        # 编号
        add_textbox(
            slide, number,
            x=1.0, y=y, w=1.0, h=0.5,
            font_size=22, bold=True, color=theme.accent,
        )

        # 标题
        add_textbox(
            slide, title,
            x=2.2, y=y, w=8.0, h=0.5,
            font_size=18, color=theme.primary,
        )

        # 分隔线
        if i < len(sections) - 1:
            add_shape_rect(
                slide, x=1.0, y=y + 0.55, w=10.0, h=0.02,
                fill_color=theme.light,
            )
