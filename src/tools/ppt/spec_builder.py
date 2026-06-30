"""Convert planner JSON into a deterministic, registry-backed renderer spec."""

from copy import deepcopy
from typing import Any

from src.tools.ppt.layout_registry import Frame, LAYOUT_IDS, get_layout
from src.tools.ppt.spec import (
    ChartNode,
    ChartSeries,
    ImageNode,
    ShapeNode,
    SlideDeckSpec,
    SlideSpec,
    TableNode,
    TextNode,
)

COLORS = {
    "navy": "17324D", "blue": "2F75B5", "light": "EAF1F8", "ink": "243240",
    "muted": "667788", "white": "FFFFFF", "green": "3A8D7D", "orange": "E88C45",
}
class SlideDeckSpecBuilder:
    """Build a renderable deck using deterministic layout and overflow rules."""

    def from_planner(self, plan: dict[str, Any]) -> SlideDeckSpec:
        title = self._text(plan.get("title"), "演示文稿")
        raw_slides = plan.get("slides")
        if not isinstance(raw_slides, list) or not raw_slides:
            raise ValueError("planner output must contain slides")

        warnings = [str(item) for item in plan.get("warnings", []) if str(item).strip()]
        normalized: list[tuple[str, dict[str, Any]]] = []
        for source_index, raw in enumerate(raw_slides, start=1):
            data = deepcopy(raw) if isinstance(raw, dict) else {}
            layout = self._resolve_layout(data, source_index, warnings)
            self._truncate_scalar_fields(data, layout, source_index, warnings)
            normalized.extend(
                (layout, page) for page in self._split_slide(data, layout, source_index, warnings)
            )

        slides = [
            self._build_slide(index, layout, data)
            for index, (layout, data) in enumerate(normalized, start=1)
        ]
        return SlideDeckSpec(title=title, warnings=warnings, slides=slides)

    def _resolve_layout(
        self, data: dict[str, Any], source_index: int, warnings: list[str],
    ) -> str:
        slide_type = str(data.get("type") or "content").lower()
        requested = str(data.get("layout") or "").lower()
        if requested in LAYOUT_IDS:
            layout = requested
        elif slide_type in {"cover", "toc", "section", "summary"}:
            layout = slide_type
        else:
            layout = "bullets"
            if requested:
                warnings.append(
                    f"slide[{source_index}] layout '{requested}' replaced with 'bullets'"
                )
        data["layout"] = layout
        return layout

    def _truncate_scalar_fields(
        self, data: dict[str, Any], layout_id: str, source_index: int, warnings: list[str],
    ) -> None:
        density = get_layout(layout_id).density
        self._truncate_field(data, "title", density.max_title_chars, source_index, warnings)
        limits = {
            "subtitle": density.max_item_chars * 2,
            "intro": density.max_item_chars * 2,
            "caption": density.max_item_chars * 2,
            "contact": density.max_item_chars,
            "presenter": density.max_item_chars,
            "date": density.max_item_chars,
            "number": density.max_item_chars,
        }
        for field, limit in limits.items():
            self._truncate_field(data, field, limit, source_index, warnings)

    def _truncate_field(
        self, data: dict[str, Any], field: str, limit: int,
        source_index: int, warnings: list[str], path: str | None = None,
    ) -> None:
        value = data.get(field)
        if value is None or len(str(value)) <= limit:
            return
        data[field] = self._truncate(str(value), limit)
        warnings.append(
            f"slide[{source_index}].{path or field} truncated to {limit} characters"
        )

    def _split_slide(
        self, data: dict[str, Any], layout_id: str, source_index: int, warnings: list[str],
    ) -> list[dict[str, Any]]:
        density = get_layout(layout_id).density
        if layout_id in {"bullets", "timeline", "image"}:
            return self._split_list_field(
                data, "points", density.max_items, density.max_item_chars,
                source_index, warnings,
            )
        if layout_id == "toc":
            return self._split_list_field(
                data, "sections", density.max_items, density.max_item_chars,
                source_index, warnings, item_fields=("title",),
            )
        if layout_id == "stat":
            return self._split_list_field(
                data, "stats", density.max_items, density.max_item_chars,
                source_index, warnings, item_fields=("value", "label", "trend"),
            )
        if layout_id == "table":
            return self._split_table(data, density.max_items, density.max_item_chars, source_index, warnings)
        if layout_id == "chart":
            return self._split_chart(data, density.max_items, density.max_item_chars, source_index, warnings)
        if layout_id in {"comparison", "summary"}:
            fields = ("takeaways", "next_steps") if layout_id == "summary" else ()
            if fields:
                for field in fields:
                    self._limit_nested_list(data, field, density.max_items, density.max_item_chars, source_index, warnings)
            else:
                for field in ("left", "right"):
                    side = data.get(field)
                    if isinstance(side, dict):
                        self._truncate_field(
                            side, "title", density.max_item_chars, source_index, warnings,
                            path=f"{field}.title",
                        )
                        self._limit_nested_list(side, "items", density.max_items, density.max_item_chars, source_index, warnings, prefix=field)
            return [data]
        return [data]

    def _split_list_field(
        self, data: dict[str, Any], field: str, capacity: int, char_limit: int,
        source_index: int, warnings: list[str], item_fields: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        raw_items = data.get(field)
        if not isinstance(raw_items, list) or not raw_items:
            return [data]
        items = [
            self._normalize_item(item, item_fields, char_limit, source_index, field, warnings)
            for item in raw_items
        ]
        pages = []
        for offset in range(0, len(items), capacity):
            page = deepcopy(data)
            page[field] = items[offset:offset + capacity]
            if offset:
                page["title"] = self._continued_title(
                    data.get("title"), get_layout(str(data["layout"])).density.max_title_chars
                )
            pages.append(page)
        if len(pages) > 1:
            warnings.append(
                f"slide[{source_index}].{field} split into {len(pages)} slides "
                f"(capacity {capacity})"
            )
        return pages

    def _normalize_item(
        self, item: Any, fields: tuple[str, ...], limit: int,
        source_index: int, field: str, warnings: list[str],
    ) -> Any:
        if fields and isinstance(item, dict):
            result = deepcopy(item)
            for key in fields:
                if key in result and len(str(result[key])) > limit:
                    result[key] = self._truncate(str(result[key]), limit)
                    warnings.append(
                        f"slide[{source_index}].{field}.{key} truncated to {limit} characters"
                    )
            return result
        text = str(item)
        if len(text) > limit:
            warnings.append(
                f"slide[{source_index}].{field} item truncated to {limit} characters"
            )
            return self._truncate(text, limit)
        return text

    def _limit_nested_list(
        self, container: dict[str, Any], field: str, capacity: int, char_limit: int,
        source_index: int, warnings: list[str], prefix: str = "",
    ) -> None:
        items = container.get(field)
        if not isinstance(items, list):
            return
        normalized = [
            self._normalize_item(item, (), char_limit, source_index, f"{prefix}.{field}".strip("."), warnings)
            for item in items[:capacity]
        ]
        container[field] = normalized
        if len(items) > capacity:
            warnings.append(
                f"slide[{source_index}].{prefix + '.' if prefix else ''}{field} "
                f"truncated to {capacity} items"
            )

    def _split_table(
        self, data: dict[str, Any], capacity: int, char_limit: int,
        source_index: int, warnings: list[str],
    ) -> list[dict[str, Any]]:
        rows = data.get("rows")
        if not isinstance(rows, list) or not rows:
            table = data.get("table")
            rows = table.get("rows") if isinstance(table, dict) else None
        if not isinstance(rows, list) or not rows:
            data["rows"] = [["暂无数据"]]
            return [data]
        source_rows = [row for row in rows if isinstance(row, list)]
        invalid_row_count = len(rows) - len(source_rows)
        if invalid_row_count:
            warnings.append(
                f"slide[{source_index}].rows ignored {invalid_row_count} invalid rows"
            )
        width = max((len(row) for row in source_rows), default=0)
        normalized = [
            [self._truncate(str(cell), char_limit) for cell in row]
            + [""] * (width - len(row))
            for row in source_rows
        ] if width else []
        if not normalized:
            normalized = [["暂无数据"]]
        elif any(len(row) != width for row in source_rows):
            warnings.append(f"slide[{source_index}].rows normalized to {width} columns")
        header, body = normalized[0], normalized[1:]
        body_capacity = max(1, capacity - 1)
        chunks = [body[i:i + body_capacity] for i in range(0, len(body), body_capacity)] or [[]]
        pages = []
        for index, chunk in enumerate(chunks):
            page = deepcopy(data)
            page["rows"] = [header, *chunk]
            if index:
                page["title"] = self._continued_title(
                    data.get("title"), get_layout("table").density.max_title_chars
                )
            pages.append(page)
        if len(chunks) > 1:
            warnings.append(
                f"slide[{source_index}].rows split into {len(chunks)} slides "
                f"(header repeated, capacity {capacity})"
            )
        if any(len(str(cell)) > char_limit for row in rows if isinstance(row, list) for cell in row):
            warnings.append(f"slide[{source_index}].rows cells truncated to {char_limit} characters")
        return pages

    def _split_chart(
        self, data: dict[str, Any], capacity: int, char_limit: int,
        source_index: int, warnings: list[str],
    ) -> list[dict[str, Any]]:
        chart = data.get("chart")
        if not isinstance(chart, dict):
            return [data]
        raw_labels = chart.get("labels") or []
        labels = [self._truncate(str(item), char_limit) for item in raw_labels]
        if not labels:
            return [data]
        if any(len(str(item)) > char_limit for item in raw_labels):
            warnings.append(
                f"slide[{source_index}].chart.labels truncated to {char_limit} characters"
            )
        valid_series = []
        for series_index, series in enumerate(chart.get("series") or [], start=1):
            if not isinstance(series, dict):
                warnings.append(
                    f"slide[{source_index}].chart.series[{series_index}] ignored: invalid object"
                )
                continue
            values = series.get("values", series.get("data", []))
            if not isinstance(values, list) or len(values) != len(labels):
                warnings.append(
                    f"slide[{source_index}].chart.series[{series_index}] ignored: "
                    "value count does not match labels"
                )
                continue
            valid_series.append({
                "name": self._truncate(
                    str(series.get("name") or f"系列 {series_index}"), char_limit
                ),
                "values": values,
            })
        if not valid_series:
            valid_series = [{"name": "数据", "values": [0 for _ in labels]}]
            warnings.append(f"slide[{source_index}].chart used zero-value fallback series")
        pages = []
        for offset in range(0, len(labels), capacity):
            page = deepcopy(data)
            page_chart = page["chart"]
            page_chart["labels"] = labels[offset:offset + capacity]
            page_chart["series"] = [
                {**series, "values": series["values"][offset:offset + capacity]}
                for series in valid_series
            ]
            if offset:
                page["title"] = self._continued_title(
                    data.get("title"), get_layout("chart").density.max_title_chars
                )
            pages.append(page)
        if len(pages) > 1:
            warnings.append(
                f"slide[{source_index}].chart split into {len(pages)} slides "
                f"(capacity {capacity} categories)"
            )
        return pages

    def _build_slide(self, index: int, layout_id: str, data: dict[str, Any]) -> SlideSpec:
        builders = {
            "cover": self._cover, "toc": self._toc, "section": self._section,
            "bullets": self._bullets, "stat": self._stats, "comparison": self._comparison,
            "timeline": self._timeline, "chart": self._chart, "table": self._table,
            "image": self._image, "summary": self._summary,
        }
        background = COLORS["navy"] if layout_id == "section" else "F7F9FC"
        return SlideSpec(
            id=f"slide-{index}", layout=layout_id, background=background,
            nodes=builders[layout_id](data),
        )

    def _cover(self, data: dict[str, Any]) -> list:
        layout = get_layout("cover")
        nodes = [
            self._shape(layout.slots["accent"], fill=COLORS["navy"]),
            TextNode(**self._pos(layout.slots["title"]), text=self._text(data.get("title"), "演示文稿"),
                     font_size=layout.font_sizes["title"], bold=True, color=COLORS["navy"], valign="mid"),
            TextNode(**self._pos(layout.slots["meta"]),
                     text="  |  ".join(filter(None, [self._text(data.get("presenter"), ""), self._text(data.get("date"), "")])) or "AID",
                     font_size=layout.font_sizes["meta"], color=COLORS["muted"]),
        ]
        if data.get("subtitle"):
            nodes.append(TextNode(**self._pos(layout.slots["subtitle"]), text=str(data["subtitle"]),
                                  font_size=layout.font_sizes["subtitle"], color=COLORS["muted"]))
        return nodes

    def _toc(self, data: dict[str, Any]) -> list:
        layout = get_layout("toc")
        items = [
            f"{item.get('number', i + 1)}  {item.get('title', '')}"
            for i, item in enumerate(data.get("sections") or []) if isinstance(item, dict)
        ]
        return self._title(data, layout, "目录") + [
            TextNode(**self._pos(layout.slots["body"]), text="\n".join(items) or "内容概览",
                     font_size=layout.font_sizes["body"], color=COLORS["ink"], margin=0.08)
        ]

    def _section(self, data: dict[str, Any]) -> list:
        layout = get_layout("section")
        nodes = [
            TextNode(**self._pos(layout.slots["number"]), text=self._text(data.get("number"), "SECTION"),
                     font_size=layout.font_sizes["number"], bold=True, color=COLORS["orange"]),
            TextNode(**self._pos(layout.slots["title"]), text=self._text(data.get("title"), "章节"),
                     font_size=layout.font_sizes["title"], bold=True, color=COLORS["white"], valign="mid"),
        ]
        if data.get("intro"):
            nodes.append(TextNode(**self._pos(layout.slots["intro"]), text=str(data["intro"]),
                                  font_size=layout.font_sizes["intro"], color="D7E2EC"))
        return nodes

    def _bullets(self, data: dict[str, Any]) -> list:
        layout = get_layout("bullets")
        return self._title(data, layout, "内容") + self._panel(
            layout.slots["body"], "核心要点", self._list(data.get("points")),
            COLORS["blue"], layout.font_sizes["panel_title"], layout.font_sizes["body"],
        )

    def _stats(self, data: dict[str, Any]) -> list:
        layout = get_layout("stat")
        nodes = self._title(data, layout, "数据亮点")
        grid = layout.slots["grid"]
        stats = [item for item in data.get("stats") or [] if isinstance(item, dict)]
        for i, item in enumerate(stats or [{"value": "-", "label": "指标"}]):
            frame = Frame(grid.x + (i % 2) * 6.1, grid.y + (i // 2) * 2.55, 5.65, 2.1)
            nodes.extend(self._stat(frame, item, layout.font_sizes))
        return nodes

    def _comparison(self, data: dict[str, Any]) -> list:
        layout = get_layout("comparison")
        nodes = self._title(data, layout, "对比")
        for slot, fallback, accent in (("left", "方案 A", COLORS["blue"]), ("right", "方案 B", COLORS["orange"])):
            side = data.get(slot) if isinstance(data.get(slot), dict) else {}
            nodes.extend(self._panel(layout.slots[slot], self._text(side.get("title"), fallback),
                                     self._list(side.get("items")), accent,
                                     layout.font_sizes["panel_title"], layout.font_sizes["body"]))
        return nodes

    def _timeline(self, data: dict[str, Any]) -> list:
        layout = get_layout("timeline")
        track = layout.slots["track"]
        points = self._list(data.get("points"))
        nodes = self._title(data, layout, "时间线")
        step = track.w / max(1, len(points))
        for i, point in enumerate(points):
            x = track.x + i * step + step / 2
            nodes.extend([
                ShapeNode(x=x - 0.17, y=track.y + 0.2, w=0.35, h=0.35, shape="ellipse", fill=COLORS["orange"]),
                TextNode(x=max(track.x, x - step / 2), y=track.y + 0.8, w=step, h=1.5,
                         text=point, font_size=layout.font_sizes["body"], color=COLORS["ink"], align="center"),
            ])
        if len(points) > 1:
            nodes.append(ShapeNode(x=track.x + step / 2, y=track.y + 0.36,
                                   w=step * (len(points) - 1), h=0.03, shape="line",
                                   line_color=COLORS["blue"], line_width=2))
        return nodes

    def _chart(self, data: dict[str, Any]) -> list:
        layout = get_layout("chart")
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
        return self._title(data, layout, "图表") + [
            ChartNode(**self._pos(layout.slots["chart"]), chart_type=self._chart_type(chart.get("type")),
                      labels=labels, series=series)
        ]

    def _table(self, data: dict[str, Any]) -> list:
        layout = get_layout("table")
        rows = data.get("rows") if isinstance(data.get("rows"), list) else [["暂无数据"]]
        width = len(rows[0]) if rows and isinstance(rows[0], list) else 1
        valid_rows = [[str(cell) for cell in row] for row in rows if isinstance(row, list) and len(row) == width]
        return self._title(data, layout, "表格") + [
            TableNode(**self._pos(layout.slots["table"]), rows=valid_rows or [["暂无数据"]],
                      font_size=layout.font_sizes["body"])
        ]

    def _image(self, data: dict[str, Any]) -> list:
        layout = get_layout("image")
        nodes = self._title(data, layout, "图片")
        if data.get("image_path"):
            nodes.append(ImageNode(**self._pos(layout.slots["image"]), path=str(data["image_path"])))
        caption = "\n".join(self._list(data.get("points"))) or self._text(data.get("caption"), "图片说明")
        nodes.append(TextNode(**self._pos(layout.slots["caption"]), text=caption,
                              font_size=layout.font_sizes["body"], color=COLORS["ink"]))
        return nodes

    def _summary(self, data: dict[str, Any]) -> list:
        layout = get_layout("summary")
        nodes = (
            self._title(data, layout, "总结")
            + self._panel(layout.slots["left"], "关键结论", self._list(data.get("takeaways")),
                          COLORS["blue"], layout.font_sizes["panel_title"], layout.font_sizes["body"])
            + self._panel(layout.slots["right"], "下一步", self._list(data.get("next_steps")),
                          COLORS["green"], layout.font_sizes["panel_title"], layout.font_sizes["body"])
        )
        if data.get("contact"):
            nodes.append(TextNode(
                **self._pos(layout.slots["contact"]), text=str(data["contact"]),
                font_size=12, color=COLORS["muted"], align="right",
            ))
        return nodes

    def _title(self, data: dict[str, Any], layout: Any, fallback: str) -> list:
        frame = layout.slots["title"]
        return [
            TextNode(**self._pos(frame), text=self._text(data.get("title"), fallback),
                     font_size=layout.font_sizes["title"], bold=True, color=COLORS["navy"], valign="mid"),
            ShapeNode(x=frame.x, y=frame.y + frame.h + 0.1, w=1.2, h=0.06, fill=COLORS["orange"]),
        ]

    def _panel(
        self, frame: Frame, title: str, items: list[str], accent: str,
        title_size: float, body_size: float,
    ) -> list:
        body = "\n".join(f"• {item}" for item in items) or "• 暂无内容"
        return [
            self._shape(frame, fill="FFFFFF", line_color="D9E2EC", line_width=1),
            ShapeNode(x=frame.x, y=frame.y, w=0.1, h=frame.h, fill=accent),
            TextNode(x=frame.x + 0.35, y=frame.y + 0.3, w=frame.w - 0.7, h=0.45,
                     text=title, font_size=title_size, bold=True, color=accent),
            TextNode(x=frame.x + 0.35, y=frame.y + 1.0, w=frame.w - 0.7, h=frame.h - 1.3,
                     text=body, font_size=body_size, color=COLORS["ink"], margin=0.05),
        ]

    def _stat(self, frame: Frame, item: dict[str, Any], fonts: Any) -> list:
        nodes = [
            self._shape(frame, fill="FFFFFF", line_color="D9E2EC", line_width=1),
            TextNode(x=frame.x + 0.35, y=frame.y + 0.3, w=3.7, h=0.75,
                     text=self._text(item.get("value"), "-"), font_size=fonts["value"],
                     bold=True, color=COLORS["blue"]),
            TextNode(x=frame.x + 0.35, y=frame.y + 1.2, w=3.7, h=0.45,
                     text=self._text(item.get("label"), "指标"), font_size=fonts["label"],
                     color=COLORS["muted"]),
        ]
        if item.get("trend"):
            nodes.append(TextNode(x=frame.x + 4.0, y=frame.y + 0.65, w=1.25, h=0.45,
                                  text=str(item["trend"]), font_size=fonts["trend"], bold=True,
                                  color=COLORS["green"], align="right"))
        return nodes

    @staticmethod
    def _shape(frame: Frame, **kwargs: Any) -> ShapeNode:
        return ShapeNode(**SlideDeckSpecBuilder._pos(frame), **kwargs)

    @staticmethod
    def _pos(frame: Frame) -> dict[str, float]:
        return {"x": frame.x, "y": frame.y, "w": frame.w, "h": frame.h}

    @staticmethod
    def _list(value: Any) -> list[str]:
        return [str(item) for item in value] if isinstance(value, list) else []

    @staticmethod
    def _text(value: Any, fallback: str) -> str:
        text = str(value).strip() if value is not None else ""
        return text or fallback

    @staticmethod
    def _truncate(value: str, limit: int) -> str:
        return value if len(value) <= limit else value[:max(1, limit - 1)].rstrip() + "…"

    @staticmethod
    def _continued_title(value: Any, limit: int) -> str:
        title = str(value).strip() if value is not None else "内容"
        suffix = "（续）"
        return f"{SlideDeckSpecBuilder._truncate(title, limit - len(suffix))}{suffix}"

    @staticmethod
    def _chart_type(value: Any) -> str:
        aliases = {"bar": "bar", "column": "column", "line": "line", "pie": "pie", "doughnut": "doughnut"}
        return aliases.get(str(value).lower(), "column")
