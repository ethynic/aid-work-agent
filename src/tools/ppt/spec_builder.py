"""Convert planner JSON into the deterministic renderer specification."""

from typing import Any

from src.tools.ppt.spec import (
    ChartNode,
    ChartSeries,
    ImageNode,
    ShapeNode,
    SlideDeckSpec,
    SlideSpec,
    TextNode,
)


COLORS = {
    "navy": "17324D",
    "blue": "2F75B5",
    "light": "EAF1F8",
    "ink": "243240",
    "muted": "667788",
    "white": "FFFFFF",
    "green": "3A8D7D",
    "orange": "E88C45",
}


class SlideDeckSpecBuilder:
    """Build a renderable deck from the existing planner contract."""

    def from_planner(self, plan: dict[str, Any]) -> SlideDeckSpec:
        title = str(plan.get("title") or "演示文稿")
        raw_slides = plan.get("slides")
        if not isinstance(raw_slides, list) or not raw_slides:
            raise ValueError("planner output must contain slides")
        slides = [
            self._build_slide(index, slide if isinstance(slide, dict) else {})
            for index, slide in enumerate(raw_slides, start=1)
        ]
        return SlideDeckSpec(title=title, slides=slides)

    def _build_slide(self, index: int, data: dict[str, Any]) -> SlideSpec:
        slide_type = str(data.get("type") or "content")
        if slide_type == "cover":
            nodes = self._cover(data)
        elif slide_type == "summary":
            nodes = self._summary(data)
        elif slide_type in {"toc", "section"}:
            nodes = self._section_like(data, slide_type)
        else:
            nodes = self._content(data)
        return SlideSpec(id=f"slide-{index}", background="F7F9FC", nodes=nodes)

    def _cover(self, data: dict[str, Any]) -> list:
        nodes = [
            ShapeNode(x=0, y=0, w=4.25, h=7.5, fill=COLORS["navy"]),
            ShapeNode(x=4.25, y=1.15, w=0.12, h=4.9, fill=COLORS["orange"]),
            TextNode(x=4.8, y=1.45, w=7.7, h=1.8, text=self._text(data.get("title"), "演示文稿"),
                     font_size=36, bold=True, color=COLORS["navy"], valign="mid"),
            TextNode(x=4.8, y=5.75, w=7.2, h=0.5,
                     text="  |  ".join(filter(None, [self._text(data.get("presenter"), ""), self._text(data.get("date"), "")])) or "AID",
                     font_size=12, color=COLORS["muted"]),
        ]
        subtitle = self._text(data.get("subtitle"), "")
        if subtitle:
            nodes.append(TextNode(x=4.8, y=3.45, w=7.2, h=0.8, text=subtitle,
                                  font_size=20, color=COLORS["muted"]))
        return nodes

    def _summary(self, data: dict[str, Any]) -> list:
        nodes = self._title(data, "总结")
        takeaways = self._list(data.get("takeaways"))
        next_steps = self._list(data.get("next_steps"))
        nodes.extend(self._panel(0.75, 1.65, 5.7, 4.7, "关键结论", takeaways, COLORS["blue"]))
        nodes.extend(self._panel(6.85, 1.65, 5.7, 4.7, "下一步", next_steps, COLORS["green"]))
        return nodes

    def _section_like(self, data: dict[str, Any], slide_type: str) -> list:
        if slide_type == "toc":
            items = [
                f"{item.get('number', i + 1)}  {item.get('title', '')}"
                for i, item in enumerate(data.get("sections") or [])
                if isinstance(item, dict)
            ]
            return self._title(data, "目录") + [
                TextNode(x=1, y=1.65, w=11.2, h=4.9, text="\n".join(items) or "内容概览",
                         font_size=24, color=COLORS["ink"], margin=0.08)
            ]
        number = self._text(data.get("number"), "")
        title = self._text(data.get("title"), "章节")
        intro = self._text(data.get("intro"), "")
        nodes = [
            ShapeNode(x=0, y=0, w=13.333, h=7.5, fill=COLORS["navy"]),
            TextNode(x=1, y=1.35, w=2, h=1, text=number or "SECTION", font_size=18,
                     bold=True, color=COLORS["orange"]),
            TextNode(x=1, y=2.35, w=10.8, h=1.4, text=title, font_size=38,
                     bold=True, color=COLORS["white"], valign="mid"),
        ]
        if intro:
            nodes.append(TextNode(x=1, y=4.05, w=9.5, h=1, text=intro, font_size=18,
                                  color="D7E2EC"))
        return nodes

    def _content(self, data: dict[str, Any]) -> list:
        nodes = self._title(data, "内容")
        layout = str(data.get("layout") or "bullets")
        if layout == "chart":
            chart = data.get("chart") if isinstance(data.get("chart"), dict) else {}
            labels = [str(item) for item in chart.get("labels") or ["项目 A", "项目 B"]]
            series = []
            for i, item in enumerate(chart.get("series") or []):
                if isinstance(item, dict):
                    values = item.get("values", item.get("data", []))
                    if len(values) == len(labels):
                        series.append(ChartSeries(name=str(item.get("name") or f"系列 {i + 1}"), values=values))
            if not series:
                series = [ChartSeries(name="数据", values=[0 for _ in labels])]
            nodes.append(ChartNode(x=0.85, y=1.5, w=11.65, h=5.25,
                                   chart_type=self._chart_type(chart.get("type")), labels=labels, series=series))
        elif layout == "comparison":
            left = data.get("left") if isinstance(data.get("left"), dict) else {}
            right = data.get("right") if isinstance(data.get("right"), dict) else {}
            nodes.extend(self._panel(0.75, 1.55, 5.75, 4.95, self._text(left.get("title"), "方案 A"),
                                     self._list(left.get("items")), COLORS["blue"]))
            nodes.extend(self._panel(6.85, 1.55, 5.75, 4.95, self._text(right.get("title"), "方案 B"),
                                     self._list(right.get("items")), COLORS["orange"]))
        elif layout == "stat":
            stats = [item for item in data.get("stats") or [] if isinstance(item, dict)][:4]
            for i, item in enumerate(stats or [{"value": "-", "label": "指标"}]):
                x = 0.8 + (i % 2) * 6.1
                y = 1.55 + (i // 2) * 2.55
                nodes.extend(self._stat(x, y, item))
        elif layout == "timeline":
            points = self._list(data.get("points"))
            for i, point in enumerate(points[:5]):
                x = 0.85 + i * 2.45
                nodes.extend([
                    ShapeNode(x=x, y=3.05, w=0.35, h=0.35, shape="ellipse", fill=COLORS["orange"]),
                    TextNode(x=x - 0.35, y=3.65, w=2.05, h=1.5, text=point, font_size=14,
                             color=COLORS["ink"], align="center"),
                ])
            if len(points) > 1:
                nodes.append(ShapeNode(x=1.05, y=3.2, w=min(9.8, (len(points) - 1) * 2.45), h=0.03,
                                       shape="line", line_color=COLORS["blue"], line_width=2))
        elif layout == "image" and data.get("image_path"):
            nodes.append(ImageNode(x=0.8, y=1.45, w=7.7, h=5.35, path=str(data["image_path"])))
            nodes.append(TextNode(x=8.85, y=1.7, w=3.7, h=4.8,
                                  text="\n".join(self._list(data.get("points"))) or self._text(data.get("caption"), "图片说明"),
                                  font_size=18, color=COLORS["ink"]))
        else:
            nodes.extend(self._panel(0.8, 1.5, 11.75, 5.3, "核心要点",
                                     self._list(data.get("points")), COLORS["blue"]))
        return nodes

    def _title(self, data: dict[str, Any], fallback: str) -> list:
        return [
            TextNode(x=0.75, y=0.4, w=11.8, h=0.7, text=self._text(data.get("title"), fallback),
                     font_size=26, bold=True, color=COLORS["navy"], valign="mid"),
            ShapeNode(x=0.75, y=1.2, w=1.2, h=0.06, fill=COLORS["orange"]),
        ]

    def _panel(self, x: float, y: float, w: float, h: float, title: str,
               items: list[str], accent: str) -> list:
        body = "\n".join(f"• {item}" for item in items) or "• 暂无内容"
        return [
            ShapeNode(x=x, y=y, w=w, h=h, fill="FFFFFF", line_color="D9E2EC", line_width=1),
            ShapeNode(x=x, y=y, w=0.1, h=h, fill=accent),
            TextNode(x=x + 0.35, y=y + 0.3, w=w - 0.7, h=0.45, text=title,
                     font_size=18, bold=True, color=accent),
            TextNode(x=x + 0.35, y=y + 1.0, w=w - 0.7, h=h - 1.3, text=body,
                     font_size=18, color=COLORS["ink"], margin=0.05),
        ]

    def _stat(self, x: float, y: float, item: dict[str, Any]) -> list:
        nodes = [
            ShapeNode(x=x, y=y, w=5.65, h=2.1, fill="FFFFFF", line_color="D9E2EC", line_width=1),
            TextNode(x=x + 0.35, y=y + 0.3, w=3.7, h=0.75, text=self._text(item.get("value"), "-"),
                     font_size=30, bold=True, color=COLORS["blue"]),
            TextNode(x=x + 0.35, y=y + 1.2, w=3.7, h=0.45, text=self._text(item.get("label"), "指标"),
                     font_size=15, color=COLORS["muted"]),
        ]
        trend = self._text(item.get("trend"), "")
        if trend:
            nodes.append(TextNode(x=x + 4.0, y=y + 0.65, w=1.25, h=0.45, text=trend,
                                  font_size=14, bold=True, color=COLORS["green"], align="right"))
        return nodes

    @staticmethod
    def _list(value: Any) -> list[str]:
        return [str(item) for item in value] if isinstance(value, list) else []

    @staticmethod
    def _text(value: Any, fallback: str) -> str:
        text = str(value).strip() if value is not None else ""
        return text or fallback

    @staticmethod
    def _chart_type(value: Any) -> str:
        aliases = {"bar": "bar", "column": "column", "line": "line", "pie": "pie", "doughnut": "doughnut"}
        return aliases.get(str(value).lower(), "column")
