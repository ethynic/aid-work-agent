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
