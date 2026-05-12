"""
PPT 主题配色系统

18 套配色方案 + 4 种视觉风格 + 字号规范。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class PPTTheme:
    """PPT 主题配色 — 5 色体系"""

    primary: str       # 最深色，标题文字
    secondary: str     # 次要色，正文文字
    accent: str        # 强调色，高亮/按钮
    light: str         # 浅色强调，背景装饰
    bg: str            # 页面背景色

    font_cn: str = "微软雅黑"
    font_en: str = "Arial"
    style: str = "soft"  # sharp / soft / rounded / pill

    @property
    def colors(self) -> Dict[str, str]:
        return {
            "primary": self.primary,
            "secondary": self.secondary,
            "accent": self.accent,
            "light": self.light,
            "bg": self.bg,
        }


# ── 18 套配色方案 ──

THEME_PRESETS: Dict[int, Tuple[str, PPTTheme]] = {
    1: ("现代健康", PPTTheme(
        primary="006d77", secondary="83c5be", accent="edf6f9",
        light="ffddd2", bg="e29578",
    )),
    2: ("商务权威", PPTTheme(
        primary="2b2d42", secondary="8d99ae", accent="edf2f4",
        light="ef233c", bg="d90429",
    )),
    3: ("自然户外", PPTTheme(
        primary="606c38", secondary="283618", accent="fefae0",
        light="dda15e", bg="bc6c25",
    )),
    4: ("复古学术", PPTTheme(
        primary="780000", secondary="c1121f", accent="fdf0d5",
        light="003049", bg="669bbc",
    )),
    5: ("柔和创意", PPTTheme(
        primary="cdb4db", secondary="ffc8dd", accent="ffafcc",
        light="bde0fe", bg="a2d2ff",
    )),
    6: ("波西米亚", PPTTheme(
        primary="ccd5ae", secondary="e9edc9", accent="fefae0",
        light="faedcd", bg="d4a373",
    )),
    7: ("活力科技", PPTTheme(
        primary="8ecae6", secondary="219ebc", accent="023047",
        light="ffb703", bg="fb8500",
    )),
    8: ("工匠艺术", PPTTheme(
        primary="7f5539", secondary="a68a64", accent="ede0d4",
        light="656d4a", bg="414833",
    )),
    9: ("深夜科技", PPTTheme(
        primary="000814", secondary="001d3d", accent="003566",
        light="ffc300", bg="ffd60a",
    )),
    10: ("教育图表", PPTTheme(
        primary="264653", secondary="2a9d8f", accent="e9c46a",
        light="f4a261", bg="e76f51",
    )),
    11: ("森林生态", PPTTheme(
        primary="dad7cd", secondary="a3b18a", accent="588157",
        light="3a5a40", bg="344e41",
    )),
    12: ("优雅时尚", PPTTheme(
        primary="edafb8", secondary="f7e1d7", accent="dedbd2",
        light="b0c4b1", bg="4a5759",
    )),
    13: ("艺术美食", PPTTheme(
        primary="335c67", secondary="fff3b0", accent="e09f3e",
        light="9e2a2b", bg="540b0e",
    )),
    14: ("奢华神秘", PPTTheme(
        primary="22223b", secondary="4a4e69", accent="9a8c98",
        light="c9ada7", bg="f2e9e4",
    )),
    15: ("纯净科技蓝", PPTTheme(
        primary="03045e", secondary="0077b6", accent="00b4d8",
        light="90e0ef", bg="caf0f8",
    )),
    16: ("海岸珊瑚", PPTTheme(
        primary="0081a7", secondary="00afb9", accent="fdfcdc",
        light="fed9b7", bg="f07167",
    )),
    17: ("活力橙薄荷", PPTTheme(
        primary="ff9f1c", secondary="ffbf69", accent="ffffff",
        light="cbf3f0", bg="2ec4b6",
    )),
    18: ("铂金白金", PPTTheme(
        primary="0a0a0a", secondary="0070F3", accent="D4AF37",
        light="f5f5f5", bg="ffffff",
    )),
}


# ── 4 种视觉风格参数 ──

STYLE_PRESETS: Dict[str, Dict] = {
    "sharp": {
        "border_radius": 0.05,   # 英寸
        "spacing": "tight",
        "padding": 0.3,
    },
    "soft": {
        "border_radius": 0.1,
        "spacing": "medium",
        "padding": 0.4,
    },
    "rounded": {
        "border_radius": 0.2,
        "spacing": "relaxed",
        "padding": 0.5,
    },
    "pill": {
        "border_radius": 0.4,
        "spacing": "open",
        "padding": 0.6,
    },
}

# ── 字号规范 (pt) ──

FONT_SIZES = {
    "annotation": 10,
    "annotation_max": 12,
    "body": 14,
    "body_max": 16,
    "subtitle": 18,
    "subtitle_max": 22,
    "page_title": 28,
    "page_title_max": 36,
    "heading": 44,
    "heading_max": 60,
    "stat_number": 60,
    "stat_number_max": 96,
}

# ── 画布规格 ──

CANVAS_WIDTH = 13.333   # 英寸 (16:9)
CANVAS_HEIGHT = 7.5
PAGE_NUMBER_X = 12.3
PAGE_NUMBER_Y = 6.8
SAFE_MARGIN = 0.5


def get_theme(theme_id: Optional[int] = None, style: str = "soft") -> PPTTheme:
    """获取指定配色方案，无效 ID 返回默认主题。"""
    if theme_id and theme_id in THEME_PRESETS:
        theme = THEME_PRESETS[theme_id][1]
    else:
        theme = THEME_PRESETS[14][1]  # 默认奢华神秘
    theme.style = style
    return theme


def list_themes() -> List[Dict]:
    """返回所有配色方案的摘要列表。"""
    return [
        {"id": tid, "name": name, "colors": theme.colors}
        for tid, (name, theme) in THEME_PRESETS.items()
    ]


def auto_select_theme(topic: str) -> int:
    """根据主题关键词自动推荐配色方案。"""
    topic_lower = topic.lower()
    if any(kw in topic_lower for kw in ["商务", "企业", "年报", "金融", "汇报"]):
        return 2
    if any(kw in topic_lower for kw in ["科技", "互联网", "ai", "技术", "数字化"]):
        return 15
    if any(kw in topic_lower for kw in ["教育", "培训", "课程", "学习"]):
        return 10
    if any(kw in topic_lower for kw in ["健康", "医疗", "医药", "养生"]):
        return 1
    if any(kw in topic_lower for kw in ["创意", "设计", "艺术", "品牌"]):
        return 5
    if any(kw in topic_lower for kw in ["环保", "自然", "生态", "绿色", "esg"]):
        return 11
    if any(kw in topic_lower for kw in ["产品", "营销", "市场", "推广"]):
        return 7
    return 14  # 默认奢华神秘
