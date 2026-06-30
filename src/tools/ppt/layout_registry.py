"""Deterministic layout definitions shared by planning and spec building."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class Frame:
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True)
class TextDensity:
    max_title_chars: int
    max_items: int
    max_item_chars: int
    max_total_chars: int


@dataclass(frozen=True)
class LayoutDefinition:
    id: str
    scenario: str
    slots: Mapping[str, Frame]
    font_sizes: Mapping[str, float]
    spacing: float
    safe_margin: float
    density: TextDensity
    image_aspect_ratio: str = "16:9"


def _layout(
    layout_id: str,
    scenario: str,
    slots: dict[str, Frame],
    font_sizes: dict[str, float],
    density: TextDensity,
    *,
    spacing: float = 0.25,
    safe_margin: float = 0.75,
    image_aspect_ratio: str = "16:9",
) -> LayoutDefinition:
    return LayoutDefinition(
        id=layout_id,
        scenario=scenario,
        slots=MappingProxyType(slots),
        font_sizes=MappingProxyType(font_sizes),
        spacing=spacing,
        safe_margin=safe_margin,
        density=density,
        image_aspect_ratio=image_aspect_ratio,
    )


_TITLE = Frame(0.75, 0.4, 11.8, 0.7)
def _build_registry() -> Mapping[str, LayoutDefinition]:
    layouts = {
    "cover": _layout(
        "cover", "演示文稿封面",
        {"accent": Frame(0, 0, 4.25, 7.5), "title": Frame(4.8, 1.45, 7.7, 1.8),
         "subtitle": Frame(4.8, 3.45, 7.2, 0.8), "meta": Frame(4.8, 5.75, 7.2, 0.5)},
        {"title": 36, "subtitle": 20, "meta": 12},
        TextDensity(36, 2, 40, 100), image_aspect_ratio="16:9",
    ),
    "toc": _layout(
        "toc", "章节目录",
        {"title": _TITLE, "body": Frame(1, 1.65, 11.2, 4.9)},
        {"title": 26, "body": 22}, TextDensity(20, 8, 28, 220),
    ),
    "section": _layout(
        "section", "章节分隔",
        {"number": Frame(1, 1.35, 2, 1), "title": Frame(1, 2.35, 10.8, 1.4),
         "intro": Frame(1, 4.05, 9.5, 1)},
        {"number": 18, "title": 38, "intro": 18}, TextDensity(30, 1, 60, 90),
    ),
    "bullets": _layout(
        "bullets", "核心要点或论点列表",
        {"title": _TITLE, "body": Frame(0.8, 1.5, 11.75, 5.3)},
        {"title": 26, "panel_title": 18, "body": 18}, TextDensity(20, 5, 30, 150),
    ),
    "stat": _layout(
        "stat", "关键指标和数据亮点",
        {"title": _TITLE, "grid": Frame(0.8, 1.55, 11.75, 4.65)},
        {"title": 26, "value": 30, "label": 15, "trend": 14},
        TextDensity(20, 4, 24, 96),
    ),
    "comparison": _layout(
        "comparison", "两个方案或对象对比",
        {"title": _TITLE, "left": Frame(0.75, 1.55, 5.75, 4.95),
         "right": Frame(6.85, 1.55, 5.75, 4.95)},
        {"title": 26, "panel_title": 18, "body": 17}, TextDensity(20, 4, 28, 224),
    ),
    "timeline": _layout(
        "timeline", "按时间或阶段展示里程碑",
        {"title": _TITLE, "track": Frame(0.85, 2.85, 11.65, 2.5)},
        {"title": 26, "body": 14}, TextDensity(20, 5, 24, 120),
    ),
    "chart": _layout(
        "chart", "结构化数据趋势或占比",
        {"title": _TITLE, "chart": Frame(0.85, 1.5, 11.65, 5.25)},
        {"title": 26, "label": 14}, TextDensity(20, 8, 20, 160),
    ),
    "table": _layout(
        "table", "结构化明细或多字段比较",
        {"title": _TITLE, "table": Frame(0.75, 1.45, 11.85, 5.55)},
        {"title": 26, "body": 13}, TextDensity(20, 8, 24, 480),
    ),
    "image": _layout(
        "image", "主视觉配合简短说明",
        {"title": _TITLE, "image": Frame(0.8, 1.45, 7.7, 5.35),
         "caption": Frame(8.85, 1.7, 3.7, 4.8)},
        {"title": 26, "body": 18}, TextDensity(20, 4, 28, 112),
        image_aspect_ratio="4:3",
    ),
    "summary": _layout(
        "summary", "结论和后续行动总结",
        {"title": _TITLE, "left": Frame(0.75, 1.65, 5.7, 4.7),
         "right": Frame(6.85, 1.65, 5.7, 4.7),
         "contact": Frame(0.75, 6.55, 11.8, 0.4)},
        {"title": 26, "panel_title": 18, "body": 17}, TextDensity(20, 4, 28, 224),
    ),
    }
    return MappingProxyType(layouts)


LAYOUT_REGISTRY = _build_registry()
LAYOUT_IDS = tuple(LAYOUT_REGISTRY)


def get_layout(layout_id: str) -> LayoutDefinition:
    try:
        return LAYOUT_REGISTRY[layout_id]
    except KeyError as exc:
        raise ValueError(f"unknown layout: {layout_id}") from exc
