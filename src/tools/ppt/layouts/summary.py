"""
总结页渲染器

支持 takeaways（要点总结）和 thankyou（感谢页）两种子布局。
"""

from src.tools.ppt.theme import PPTTheme, CANVAS_WIDTH, CANVAS_HEIGHT
from src.tools.ppt.utils import (
    set_slide_bg, add_textbox, add_shape_rect,
    add_shape_rounded_rect,
)


def render_summary(slide, data: dict, theme: PPTTheme):
    """渲染总结页。"""
    set_slide_bg(slide, theme.bg)

    layout = data.get("layout", "takeaways")
    if layout == "thankyou":
        _render_thankyou(slide, data, theme)
    else:
        _render_takeaways(slide, data, theme)


def _render_takeaways(slide, data: dict, theme: PPTTheme):
    """要点总结布局。"""
    title = data.get("title", "总结与展望")
    add_textbox(
        slide, title,
        x=0.8, y=0.5, w=11.7, h=0.9,
        font_size=32, bold=True, color=theme.primary,
    )

    add_shape_rect(slide, x=0.8, y=1.3, w=1.5, h=0.04, fill_color=theme.accent)

    takeaways = data.get("takeaways", [])
    next_steps = data.get("next_steps", [])

    # 左侧：关键要点
    if takeaways:
        add_textbox(
            slide, "关键要点",
            x=0.8, y=1.8, w=5.5, h=0.6,
            font_size=18, bold=True, color=theme.accent,
        )
        for i, item in enumerate(takeaways[:5]):
            y = 2.5 + i * 0.6
            add_shape_rounded_rect(
                slide,
                x=1.0, y=y + 0.08, w=0.12, h=0.12,
                fill_color=theme.accent,
                corner_radius=0.06,
            )
            add_textbox(
                slide, item,
                x=1.4, y=y, w=5.0, h=0.5,
                font_size=15, color=theme.secondary,
            )

    # 右侧：下一步
    if next_steps:
        right_x = 7.0
        add_textbox(
            slide, "下一步",
            x=right_x, y=1.8, w=5.5, h=0.6,
            font_size=18, bold=True, color=theme.accent,
        )
        for i, item in enumerate(next_steps[:5]):
            y = 2.5 + i * 0.6
            add_shape_rounded_rect(
                slide,
                x=right_x + 0.2, y=y + 0.08, w=0.12, h=0.12,
                fill_color=theme.primary,
                corner_radius=0.06,
            )
            add_textbox(
                slide, item,
                x=right_x + 0.6, y=y, w=5.0, h=0.5,
                font_size=15, color=theme.secondary,
            )

    # 联系方式
    contact = data.get("contact", "")
    if contact:
        add_textbox(
            slide, f"联系: {contact}",
            x=0.8, y=6.3, w=10.0, h=0.4,
            font_size=11, color=theme.accent,
        )


def _render_thankyou(slide, data: dict, theme: PPTTheme):
    """感谢页布局。"""
    # 大号感谢文字
    add_textbox(
        slide, "Thank You",
        x=1.5, y=2.0, w=10.3, h=2.0,
        font_size=60, bold=True, color=theme.primary,
        align="center",
    )

    # 装饰线
    add_shape_rect(
        slide,
        x=5.5, y=4.2, w=2.3, h=0.05,
        fill_color=theme.accent,
    )

    # 副文本
    subtitle = data.get("title", "")
    if subtitle:
        add_textbox(
            slide, subtitle,
            x=2.0, y=4.5, w=9.3, h=0.8,
            font_size=18, color=theme.secondary,
            align="center",
        )

    # 联系方式
    contact = data.get("contact", "")
    if contact:
        add_textbox(
            slide, contact,
            x=3.0, y=5.5, w=7.3, h=0.5,
            font_size=14, color=theme.accent,
            align="center",
        )
