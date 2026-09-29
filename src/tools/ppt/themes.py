"""pptxgenjs 路径主题（设计系统）定义。

主题 = 配色角色 + 字体 + 图表色板。设计规则遵循业界共识（见
docs/research/ppt-generation-quality-research.md §3.2）：
- 三角色模型：PRIMARY 主色（60-70% 视觉权重）、ACCENT 强调色（5-10%）、
  中性背景/文字；深浅「三明治」：封面/章节深底，内容页浅底。
- 禁用元素：标题下装饰线、边缘色条（edge stripe）——业界明确列为 AI 生成痕迹。
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PptTheme:
    id: str
    name: str
    scenario: str  # 适用场景，供 planner 选择与向用户说明
    # 深色页（封面/章节）配色
    dark_bg: str
    dark_bg_alt: str  # 封面右侧拼块（比 dark_bg 亮一档）
    # 浅色内容页配色
    bg: str
    primary: str  # 主色：标题、表头、图形主结构
    accent: str  # 强调色：数字、编号、重点（克制使用）
    ink: str  # 正文
    muted: str  # 次要文字
    card_tint: str  # 卡片底色（primary 的极浅色调）
    card_tint_accent: str  # 强调侧卡片底色（accent 的极浅色调）
    line: str  # 分隔线/描边
    dark_text: str  # 深色页上的主文字（白系）
    dark_text_muted: str  # 深色页上的次要文字
    font_cn: str = "Microsoft YaHei"
    chart_colors: tuple[str, ...] = field(default_factory=tuple)

    def chart_palette(self) -> list[str]:
        return list(self.chart_colors)


THEMES: dict[str, PptTheme] = {
    "midnight_gold": PptTheme(
        id="midnight_gold", name="墨蓝金辉", scenario="商务/金融/汇报",
        dark_bg="0F2B4C", dark_bg_alt="1B3E66",
        bg="FFFFFF", primary="16406B", accent="C9A227",
        ink="1F2937", muted="6B7280",
        card_tint="EDF2F8", card_tint_accent="F8F3E3",
        line="D8E0EA", dark_text="FFFFFF", dark_text_muted="B9C8DA",
        chart_colors=("16406B", "C9A227", "5B7FA6", "8FB0CE", "E3C96B"),
    ),
    "teal_tech": PptTheme(
        id="teal_tech", name="青屿科技", scenario="科技/互联网/AI/数据",
        dark_bg="07393C", dark_bg_alt="0C5155",
        bg="FFFFFF", primary="0F5E62", accent="E8871E",
        ink="153435", muted="5F7375",
        card_tint="EAF5F5", card_tint_accent="FDF1E4",
        line="D3E4E4", dark_text="FFFFFF", dark_text_muted="B4D2D3",
        chart_colors=("0F5E62", "E8871E", "4E969A", "9CC3C5", "F2B26B"),
    ),
    "indigo_clean": PptTheme(
        id="indigo_clean", name="靛蓝简约", scenario="咨询/方案/通用",
        dark_bg="202A5C", dark_bg_alt="2F3D7E",
        bg="FFFFFF", primary="3A4A9F", accent="F0704A",
        ink="1E2340", muted="6E7391",
        card_tint="EEF0FA", card_tint_accent="FDEEE8",
        line="DCDFF0", dark_text="FFFFFF", dark_text_muted="C3C9E8",
        chart_colors=("3A4A9F", "F0704A", "7B87C9", "AEB6E3", "F5A48B"),
    ),
    "crimson_warm": PptTheme(
        id="crimson_warm", name="绛红暖调", scenario="营销/品牌/企业文化",
        dark_bg="5C1F26", dark_bg_alt="7E2E37",
        bg="FFFFFF", primary="8C2F39", accent="D9911F",
        ink="3A2226", muted="8A7073",
        card_tint="F9EDEE", card_tint_accent="FAF2E1",
        line="EBD9DB", dark_text="FFFFFF", dark_text_muted="E3C6CA",
        chart_colors=("8C2F39", "D9911F", "BA6E77", "D9A2A8", "EDC677"),
    ),
}

DEFAULT_THEME_ID = "midnight_gold"


def get_ppt_theme(theme_id: str | None) -> PptTheme:
    """按 id 取主题；未知/缺失回落默认主题（不抛错，保证渲染可用）。"""
    if theme_id and str(theme_id).strip().lower() in THEMES:
        return THEMES[str(theme_id).strip().lower()]
    return THEMES[DEFAULT_THEME_ID]


def format_theme_catalog() -> str:
    """生成 planner 提示词用的主题清单文本。"""
    return "\n".join(
        f"- {theme.id}（{theme.name}）：{theme.scenario}"
        for theme in THEMES.values()
    )
