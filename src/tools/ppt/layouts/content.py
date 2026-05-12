"""
内容页渲染器

支持 bullets / stat / comparison / timeline / image 五种子布局。
"""

from src.tools.ppt.theme import PPTTheme, CANVAS_WIDTH, CANVAS_HEIGHT
from src.tools.ppt.utils import (
    set_slide_bg, add_textbox, add_shape_rect,
    add_shape_rounded_rect, add_page_number, truncate_text,
)


def render_content(slide, data: dict, theme: PPTTheme, index: int, total: int):
    """渲染内容页。"""
    set_slide_bg(slide, theme.bg)

    # 页面标题
    add_textbox(
        slide, data.get("title", ""),
        x=0.8, y=0.4, w=11.7, h=0.9,
        font_size=28, bold=True, color=theme.primary,
    )

    # 标题下装饰线
    add_shape_rect(slide, x=0.8, y=1.2, w=1.5, h=0.04, fill_color=theme.accent)

    layout = data.get("layout", "bullets")
    renderer = {
        "bullets": _render_bullets,
        "stat": _render_stat,
        "comparison": _render_comparison,
        "timeline": _render_timeline,
        "image": _render_image,
    }.get(layout, _render_bullets)

    renderer(slide, data, theme)
    add_page_number(slide, index, total, color=theme.secondary)


# ── bullets：要点列表 ──

def _render_bullets(slide, data: dict, theme: PPTTheme):
    points = data.get("points", [])
    if not points:
        return

    start_y = 1.8
    item_h = 0.7

    for i, point in enumerate(points):
        y = start_y + i * item_h
        if y > 6.2:
            break

        # 圆点标记
        add_shape_rounded_rect(
            slide,
            x=1.0, y=y + 0.15, w=0.15, h=0.15,
            fill_color=theme.accent,
            corner_radius=0.07,
        )

        # 要点文本
        add_textbox(
            slide, point,
            x=1.5, y=y, w=10.5, h=0.55,
            font_size=16, color=theme.secondary,
        )

    # takeaway 注释
    takeaway = data.get("takeaway", "")
    if takeaway:
        add_textbox(
            slide, f"💡 {takeaway}",
            x=1.0, y=6.0, w=10.0, h=0.5,
            font_size=12, color=theme.accent,
        )


# ── stat：数据亮点卡片 ──

def _render_stat(slide, data: dict, theme: PPTTheme):
    stats = data.get("stats", [])
    if not stats:
        return

    n = len(stats)
    card_w = min(3.2, (11.7 - (n - 1) * 0.4) / n)
    start_x = (CANVAS_WIDTH - n * card_w - (n - 1) * 0.4) / 2
    card_h = 3.5

    for i, stat in enumerate(stats):
        x = start_x + i * (card_w + 0.4)
        y = 2.0

        # 卡片背景
        add_shape_rounded_rect(
            slide, x, y, card_w, card_h,
            fill_color=theme.light,
            corner_radius=0.1,
        )

        # 数值
        value = stat.get("value", "")
        add_textbox(
            slide, value,
            x=x, y=y + 0.5, w=card_w, h=1.2,
            font_size=36, bold=True, color=theme.primary,
            align="center",
        )

        # 标签
        label = stat.get("label", "")
        add_textbox(
            slide, label,
            x=x, y=y + 1.8, w=card_w, h=0.5,
            font_size=14, color=theme.secondary,
            align="center",
        )

        # 趋势
        trend = stat.get("trend", "")
        if trend:
            trend_color = "22c55e" if trend.startswith("+") else "ef4444"
            add_textbox(
                slide, trend,
                x=x, y=y + 2.4, w=card_w, h=0.5,
                font_size=16, bold=True, color=trend_color,
                align="center",
            )


# ── comparison：左右对比 ──

def _render_comparison(slide, data: dict, theme: PPTTheme):
    left = data.get("left", {})
    right = data.get("right", {})

    half_w = 5.5
    gap = 0.5
    start_y = 1.8

    # 左侧
    _render_comparison_side(
        slide, left, x=0.8, y=start_y, w=half_w,
        bg_color=theme.light, title_color=theme.primary, theme=theme,
    )

    # 右侧
    _render_comparison_side(
        slide, right, x=0.8 + half_w + gap, y=start_y, w=half_w,
        bg_color=theme.light, title_color=theme.primary, theme=theme,
    )


def _render_comparison_side(slide, side_data: dict, x: float, y: float, w: float,
                            bg_color: str, title_color: str, theme: PPTTheme):
    title = side_data.get("title", "")
    items = side_data.get("items", [])

    # 背景
    add_shape_rounded_rect(
        slide, x, y, w, 4.5,
        fill_color=bg_color,
        corner_radius=0.1,
    )

    # 标题
    add_textbox(
        slide, title,
        x=x + 0.3, y=y + 0.3, w=w - 0.6, h=0.6,
        font_size=20, bold=True, color=title_color,
    )

    # 项目列表
    for i, item in enumerate(items[:5]):
        iy = y + 1.1 + i * 0.6
        add_textbox(
            slide, f"• {item}",
            x=x + 0.4, y=iy, w=w - 0.8, h=0.5,
            font_size=14, color=theme.secondary,
        )


# ── timeline：时间线/流程 ──

def _render_timeline(slide, data: dict, theme: PPTTheme):
    points = data.get("points", [])
    if not points:
        return

    n = len(points)
    line_y = 3.5
    start_x = 1.5
    end_x = CANVAS_WIDTH - 1.5
    total_w = end_x - start_x
    gap = total_w / max(n - 1, 1) if n > 1 else total_w

    # 横线
    add_shape_rect(
        slide, x=start_x, y=line_y, w=total_w, h=0.04,
        fill_color=theme.accent,
    )

    for i, point in enumerate(points):
        cx = start_x + i * gap if n > 1 else start_x + total_w / 2

        # 圆点
        add_shape_rounded_rect(
            slide,
            x=cx - 0.15, y=line_y - 0.15, w=0.3, h=0.3,
            fill_color=theme.primary,
            corner_radius=0.15,
        )

        # 文本（奇偶交替上下放置）
        text_y = line_y - 1.2 if i % 2 == 0 else line_y + 0.6
        add_textbox(
            slide, truncate_text(point, 20),
            x=cx - 1.2, y=text_y, w=2.4, h=0.9,
            font_size=13, color=theme.secondary,
            align="center",
        )


# ── image：图片占位（记录图片路径信息） ──

def _render_image(slide, data: dict, theme: PPTTheme):
    image_path = data.get("image_path", "")
    caption = data.get("caption", "")
    points = data.get("points", [])

    if image_path:
        try:
            from pptx.util import Inches
            slide.shapes.add_picture(
                image_path,
                Inches(1.0), Inches(2.0),
                Inches(5.0), Inches(4.0),
            )
        except Exception:
            # 图片加载失败时显示占位框
            add_shape_rounded_rect(
                slide, 1.0, 2.0, 5.0, 4.0,
                fill_color=theme.light,
                corner_radius=0.1,
            )
            add_textbox(
                slide, "图片区域",
                x=1.5, y=3.5, w=4.0, h=1.0,
                font_size=20, color=theme.secondary,
                align="center",
            )
    else:
        add_shape_rounded_rect(
            slide, 1.0, 2.0, 5.0, 4.0,
            fill_color=theme.light,
            corner_radius=0.1,
        )
        add_textbox(
            slide, "图片区域",
            x=1.5, y=3.5, w=4.0, h=1.0,
            font_size=20, color=theme.secondary,
            align="center",
        )

    # 右侧要点
    if points:
        for i, point in enumerate(points[:5]):
            y = 2.0 + i * 0.7
            add_textbox(
                slide, f"• {point}",
                x=6.5, y=y, w=5.5, h=0.5,
                font_size=14, color=theme.secondary,
            )

    # 图片说明
    if caption:
        add_textbox(
            slide, caption,
            x=1.0, y=6.2, w=5.0, h=0.4,
            font_size=11, color=theme.accent,
        )
