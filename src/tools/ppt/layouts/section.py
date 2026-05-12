"""
章节分隔页渲染器

支持 center（居中）和 left-accent（左侧强调）两种子布局。
"""

from src.tools.ppt.theme import PPTTheme, CANVAS_WIDTH, CANVAS_HEIGHT
from src.tools.ppt.utils import (
    set_slide_bg, add_textbox, add_shape_rect,
)


def render_section(slide, data: dict, theme: PPTTheme):
    """渲染章节分隔页。"""
    set_slide_bg(slide, theme.primary)

    layout = data.get("layout", "center")
    number = data.get("number", "")
    title = data.get("title", "")
    intro = data.get("intro", "")

    if layout == "left-accent":
        _render_left_accent(slide, number, title, intro, theme)
    else:
        _render_center(slide, number, title, intro, theme)


def _render_center(slide, number: str, title: str, intro: str, theme: PPTTheme):
    """居中布局 — 大编号 + 标题。"""
    # 编号
    if number:
        add_textbox(
            slide, number,
            x=2.0, y=1.8, w=9.3, h=1.0,
            font_size=60, bold=True, color=theme.light,
            align="center",
        )

    # 标题
    add_textbox(
        slide, title,
        x=1.5, y=3.2, w=10.3, h=1.5,
        font_size=44, bold=True, color=theme.bg,
        align="center",
    )

    # 副文本
    if intro:
        add_textbox(
            slide, intro,
            x=2.5, y=4.8, w=8.3, h=0.8,
            font_size=18, color=theme.accent,
            align="center",
        )

    # 底部装饰线
    add_shape_rect(
        slide,
        x=5.5, y=6.2, w=2.3, h=0.05,
        fill_color=theme.accent,
    )


def _render_left_accent(slide, number: str, title: str, intro: str, theme: PPTTheme):
    """左侧强调布局 — 左侧色块 + 右侧文字。"""
    # 左侧色块（占 40% 宽度）
    left_w = CANVAS_WIDTH * 0.4
    add_shape_rect(
        slide,
        x=0, y=0, w=left_w, h=CANVAS_HEIGHT,
        fill_color=theme.accent,
    )

    # 编号（左侧）
    if number:
        add_textbox(
            slide, number,
            x=0.5, y=2.5, w=left_w - 1.0, h=1.5,
            font_size=72, bold=True, color=theme.bg,
            align="center",
        )

    # 标题（右侧）
    right_x = left_w + 0.8
    right_w = CANVAS_WIDTH - left_w - 1.6
    add_textbox(
        slide, title,
        x=right_x, y=2.5, w=right_w, h=1.5,
        font_size=36, bold=True, color=theme.bg,
    )

    # 副文本
    if intro:
        add_textbox(
            slide, intro,
            x=right_x, y=4.2, w=right_w, h=0.8,
            font_size=16, color=theme.light,
        )
