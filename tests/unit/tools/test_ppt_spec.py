"""SlideDeckSpec and planner conversion tests."""

import pytest
from pydantic import ValidationError

from src.tools.ppt.spec import (
    ChartNode,
    ChartSeries,
    RasterLayerNode,
    SlideDeckSpec,
    SlideSpec,
    TableNode,
    TextNode,
)
from src.tools.ppt.spec_builder import SlideDeckSpecBuilder
from src.tools.ppt.layout_registry import LAYOUT_IDS, LAYOUT_REGISTRY, get_layout
from src.tools.ppt.planner import PPTPlanner

pytestmark = pytest.mark.tools


def test_spec_validates_supported_nodes_and_serializes_discriminator():
    spec = SlideDeckSpec(
        title="规格",
        slides=[
            SlideSpec(
                id="s1",
                nodes=[
                    TextNode(x=0, y=0, w=2, h=1, text="标题"),
                    TableNode(x=0, y=1, w=4, h=2, rows=[["A"], ["1"]]),
                    ChartNode(
                        x=4,
                        y=1,
                        w=4,
                        h=3,
                        labels=["A"],
                        series=[ChartSeries(name="数据", values=[1])],
                    ),
                    RasterLayerNode(x=0, y=3, w=2, h=2, path="page.png"),
                ],
            )
        ],
    )

    assert [node["type"] for node in spec.model_dump()["slides"][0]["nodes"]] == [
        "text", "table", "chart", "raster"
    ]


@pytest.mark.parametrize(
    "node",
    [
        {"type": "text", "x": -1, "y": 0, "w": 1, "h": 1, "text": "x"},
        {"type": "shape", "x": 0, "y": 0, "w": 0, "h": 1},
        {"type": "unknown", "x": 0, "y": 0, "w": 1, "h": 1},
    ],
)
def test_spec_rejects_invalid_coordinates_sizes_and_types(node):
    with pytest.raises(ValidationError):
        SlideDeckSpec.model_validate(
            {"title": "x", "slides": [{"id": "s1", "nodes": [node]}]}
        )


def test_spec_rejects_nodes_outside_slide_and_mismatched_chart_data():
    with pytest.raises(ValidationError, match="exceeds slide width"):
        SlideDeckSpec(
            title="x",
            slides=[SlideSpec(id="s1", nodes=[TextNode(x=13, y=0, w=1, h=1, text="x")])],
        )
    with pytest.raises(ValidationError, match="must match labels"):
        ChartNode(
            x=0, y=0, w=3, h=2, labels=["A", "B"],
            series=[ChartSeries(name="S", values=[1])],
        )


def test_spec_normalizes_colors_and_rejects_invalid_values():
    node = TextNode(x=0, y=0, w=1, h=1, text="x", color="#aabbcc")
    assert node.color == "AABBCC"
    with pytest.raises(ValidationError, match="6-digit hexadecimal"):
        TextNode(x=0, y=0, w=1, h=1, text="x", color="red")


def test_builder_covers_existing_planner_layouts():
    layouts = [
        {"layout": "bullets", "points": ["A"]},
        {"layout": "chart", "chart": {"type": "line", "labels": ["A"], "series": [{"name": "S", "values": [1]}]}},
        {"layout": "comparison", "left": {"items": ["A"]}, "right": {"items": ["B"]}},
        {"layout": "stat", "stats": [{"value": "10", "label": "数量"}]},
        {"layout": "timeline", "points": ["第一步", "第二步"]},
        {"layout": "image", "image_path": "image.png", "points": ["说明"]},
    ]
    plan = {
        "title": "布局覆盖",
        "slides": [
            {"type": "cover", "title": "布局覆盖"},
            *[{"type": "content", "title": f"内容 {index}", **item} for index, item in enumerate(layouts)],
            {"type": "summary", "takeaways": ["完成"]},
        ],
    }

    spec = SlideDeckSpecBuilder().from_planner(plan)

    assert len(spec.slides) == 8
    assert any(node.type == "chart" for node in spec.slides[2].nodes)
    assert any(node.type == "image" for node in spec.slides[6].nodes)


def test_layout_registry_covers_phase3_contract():
    assert LAYOUT_IDS == (
        "cover", "toc", "section", "bullets", "stat", "comparison",
        "timeline", "chart", "table", "image", "summary",
    )
    for layout_id, layout in LAYOUT_REGISTRY.items():
        assert layout.id == layout_id
        assert layout.scenario
        assert layout.slots
        assert layout.font_sizes
        assert layout.spacing > 0
        assert layout.safe_margin > 0
        assert layout.density.max_title_chars > 0
        assert layout.density.max_items > 0
        assert layout.density.max_item_chars > 0
        assert layout.density.max_total_chars > 0
        assert layout.image_aspect_ratio
        for frame in layout.slots.values():
            assert frame.x >= 0 and frame.y >= 0
            assert frame.x + frame.w <= 13.333
            assert frame.y + frame.h <= 7.5
    assert get_layout("image").image_aspect_ratio == "4:3"
    with pytest.raises(ValueError, match="unknown layout"):
        get_layout("custom")
    with pytest.raises(TypeError):
        LAYOUT_REGISTRY["cover"] = get_layout("cover")
    with pytest.raises(TypeError):
        get_layout("cover").slots["title"] = get_layout("cover").slots["title"]


def test_builder_builds_every_registered_layout():
    plan = {
        "title": "全部布局",
        "slides": [
            {"layout": "cover", "title": "封面", "subtitle": "副标题"},
            {"layout": "toc", "title": "目录", "sections": [{"number": 1, "title": "章节"}]},
            {"layout": "section", "number": "01", "title": "章节", "intro": "简介"},
            {"layout": "bullets", "title": "要点", "points": ["A"]},
            {"layout": "stat", "title": "指标", "stats": [{"value": "1", "label": "数量"}]},
            {"layout": "comparison", "title": "对比", "left": {"items": ["A"]}, "right": {"items": ["B"]}},
            {"layout": "timeline", "title": "时间线", "points": ["开始", "结束"]},
            {"layout": "chart", "title": "图表", "chart": {"labels": ["A"], "series": [{"name": "S", "values": [1]}]}},
            {"layout": "table", "title": "表格", "rows": [["列"], ["值"]]},
            {"layout": "image", "title": "图片", "caption": "说明"},
            {"layout": "summary", "title": "总结", "takeaways": ["结论"], "next_steps": ["行动"]},
        ],
    }
    spec = SlideDeckSpecBuilder().from_planner(plan)

    assert [slide.layout for slide in spec.slides] == list(LAYOUT_IDS)
    assert all(slide.nodes for slide in spec.slides)
    assert any(node.type == "table" for node in spec.slides[8].nodes)


def test_builder_is_stable_and_splits_over_capacity_with_auditable_warnings():
    plan = {
        "title": "稳定性",
        "slides": [{
            "layout": "bullets",
            "title": "这是一个超过二十个字符且必须确定性截断的页面标题",
            "points": [f"第{i}项" for i in range(12)],
        }],
    }
    first = SlideDeckSpecBuilder().from_planner(plan)
    second = SlideDeckSpecBuilder().from_planner(plan)

    assert first.model_dump() == second.model_dump()
    assert len(first.slides) == 3
    assert [slide.layout for slide in first.slides] == ["bullets"] * 3
    assert any("title truncated" in warning for warning in first.warnings)
    assert any("split into 3 slides" in warning for warning in first.warnings)
    body_nodes = [
        node for slide in first.slides for node in slide.nodes
        if node.type == "text" and node.text.startswith("•")
    ]
    assert all(node.text.count("\n") < get_layout("bullets").density.max_items for node in body_nodes)


def test_builder_splits_table_and_chart_and_truncates_dense_comparison():
    plan = {
        "title": "密度",
        "slides": [
            {"layout": "table", "title": "表格", "rows": [["列"], *[[str(i)] for i in range(16)]]},
            {"layout": "chart", "title": "图表", "chart": {
                "labels": [str(i) for i in range(10)],
                "series": [{"name": "S", "values": list(range(10))}],
            }},
            {"layout": "comparison", "left": {"items": list("ABCDE")}, "right": {"items": list("ABCDE")}},
        ],
    }
    spec = SlideDeckSpecBuilder().from_planner(plan)

    assert [slide.layout for slide in spec.slides].count("table") == 3
    assert [slide.layout for slide in spec.slides].count("chart") == 2
    assert any("left.items truncated to 4 items" in item for item in spec.warnings)


def test_planner_sanitizer_emits_only_layout_ids_and_removes_coordinates():
    plan = PPTPlanner._sanitize_plan({
        "title": "规划",
        "slides": [
            {"type": "cover", "title": "封面", "x": 10},
            {"type": "content", "layout": "freeform", "title": "内容", "position": {"x": 1}},
        ],
    })

    assert plan["slides"][0]["layout"] == "cover"
    assert plan["slides"][1]["layout"] == "bullets"
    assert all("type" not in slide and "x" not in slide and "position" not in slide for slide in plan["slides"])
    assert len(plan["warnings"]) == 3


def test_planner_sanitizer_preserves_legal_nested_content_and_filters_warnings():
    source = {
        "title": "规划",
        "warnings": [None, "", 1],
        "slides": [{
            "layout": "chart",
            "chart": {
                "type": "line",
                "labels": ["宽度", "高度"],
                "series": [{"name": "尺寸", "data": [10, 20]}],
            },
        }],
    }

    plan = PPTPlanner._sanitize_plan(source)

    assert "warnings" not in plan
    assert plan["slides"][0]["chart"] == source["slides"][0]["chart"]
    assert plan["slides"][0]["layout"] == "chart"


def test_builder_preserves_input_and_maps_summary_contact():
    plan = {
        "title": "映射",
        "slides": [{
            "layout": "summary",
            "title": "总结",
            "takeaways": ["结论"],
            "next_steps": ["行动"],
            "contact": "owner@example.com",
        }],
    }
    original = {
        "title": "映射",
        "slides": [{
            "layout": "summary",
            "title": "总结",
            "takeaways": ["结论"],
            "next_steps": ["行动"],
            "contact": "owner@example.com",
        }],
    }

    spec = SlideDeckSpecBuilder().from_planner(plan)

    assert plan == original
    assert any(
        node.type == "text" and node.text == "owner@example.com"
        for node in spec.slides[0].nodes
    )


def test_builder_normalizes_uneven_table_without_dropping_rows():
    spec = SlideDeckSpecBuilder().from_planner({
        "title": "表格",
        "slides": [{
            "layout": "table",
            "title": "表格",
            "rows": [["A", "B"], ["1"], "invalid", ["2", "3", "4"]],
        }],
    })

    table = next(node for node in spec.slides[0].nodes if node.type == "table")
    assert table.rows == [["A", "B", ""], ["1", "", ""], ["2", "3", "4"]]
    assert any("normalized to 3 columns" in warning for warning in spec.warnings)
    assert any("ignored 1 invalid rows" in warning for warning in spec.warnings)


def test_continued_titles_stay_bounded_and_chart_fallback_is_traced():
    spec = SlideDeckSpecBuilder().from_planner({
        "title": "边界",
        "slides": [
            {
                "layout": "bullets",
                "title": "这是一个超过二十个字符且会拆分页的标题文本",
                "points": [str(index) for index in range(6)],
            },
            {
                "layout": "chart",
                "title": "异常图表",
                "chart": {
                    "labels": ["A", "B"],
                    "series": [{"name": "缺失值", "values": [1]}],
                },
            },
        ],
    })

    for slide in spec.slides:
        title = next(node.text for node in slide.nodes if node.type == "text")
        assert len(title) <= get_layout(slide.layout).density.max_title_chars
    assert any("value count does not match labels" in warning for warning in spec.warnings)
    assert any("zero-value fallback series" in warning for warning in spec.warnings)
