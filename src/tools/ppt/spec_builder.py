"""Convert planner JSON into a deterministic, registry-backed renderer spec.

视觉规范由主题（themes.py）驱动：深浅「三明治」结构（封面/章节深底、内容页浅底）、
卡片用浅色调圆角（不用边缘色条）、标题不带装饰线——业界明确的 AI 痕迹红线，
详见 docs/research/ppt-generation-quality-research.md。
"""

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
from src.tools.ppt.themes import PptTheme, get_ppt_theme

DECK_W, DECK_H = 13.333, 7.5


class SlideDeckSpecBuilder:
    """Build a renderable deck using deterministic layout and overflow rules."""

    def __init__(self, theme: PptTheme | str | None = None):
        if isinstance(theme, PptTheme):
            self.theme = theme
        else:
            self.theme = get_ppt_theme(theme)

    def from_planner(self, plan: dict[str, Any]) -> SlideDeckSpec:
        # plan 显式给了 theme 才覆盖构造时传入的主题；未知值回落默认主题
        theme_id = plan.get("theme") or plan.get("theme_id")
        if theme_id is not None:
            self.theme = get_ppt_theme(str(theme_id))
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

    # ── 视觉构建（主题驱动）──────────────────────────────────────────

    def _build_slide(self, index: int, layout_id: str, data: dict[str, Any]) -> SlideSpec:
        theme = self.theme
        dark = layout_id in {"cover", "section"}
        background = theme.dark_bg if dark else theme.bg
        builders = {
            "cover": self._cover, "toc": self._toc, "section": self._section,
            "bullets": self._bullets, "stat": self._stats, "comparison": self._comparison,
            "timeline": self._timeline, "chart": self._chart, "table": self._table,
            "image": self._image, "summary": self._summary,
        }
        return SlideSpec(
            id=f"slide-{index}", layout=layout_id, background=background,
            nodes=builders[layout_id](data),
        )

    def _cover(self, data: dict[str, Any]) -> list:
        """分栏封面：左侧深底承载标题，右侧次深色块承载汇报信息。"""
        theme = self.theme
        nodes = [
            ShapeNode(x=0, y=0, w=8.35, h=DECK_H, shape="rect", fill=theme.dark_bg),
            ShapeNode(x=8.35, y=0, w=DECK_W - 8.35, h=DECK_H, shape="rect", fill=theme.dark_bg_alt),
            TextNode(x=1.0, y=1.45, w=7.0, h=1.6,
                     text=self._text(data.get("title"), "演示文稿"),
                     font_size=32, bold=True, color=theme.dark_text, valign="mid"),
        ]
        if data.get("subtitle"):
            nodes.append(TextNode(x=1.0, y=3.4, w=7.0, h=1.0, text=str(data["subtitle"]),
                                  font_size=15, color=theme.dark_text_muted, valign="top"))
        # 右栏汇报信息：小标签 + 值，成组呈现
        info = [("汇报人", data.get("presenter")), ("日期", data.get("date"))]
        y = 2.35
        for label, value in info:
            if value is None or not str(value).strip():
                continue
            nodes.append(TextNode(x=9.15, y=y, w=3.4, h=0.35, text=str(label),
                                  font_size=12, color=theme.dark_text_muted))
            nodes.append(TextNode(x=9.15, y=y + 0.38, w=3.4, h=0.5, text=str(value),
                                  font_size=16, bold=True, color=theme.dark_text))
            y += 1.15
        return nodes

    def _toc(self, data: dict[str, Any]) -> list:
        """目录：双列浅色圆角卡片，强调色编号。"""
        theme = self.theme
        sections = [item for item in data.get("sections") or [] if isinstance(item, dict)]
        nodes = self._title(data, get_layout("toc"), "目录")
        if not sections:
            nodes.append(TextNode(x=0.8, y=2.2, w=11.7, h=1.0, text="内容概览",
                                  font_size=18, color=theme.muted))
            return nodes
        cols = 1 if len(sections) <= 3 else 2
        rows = (len(sections) + cols - 1) // cols
        card_w, card_h = 5.7 if cols == 2 else 11.75, 1.0
        gap_x, gap_y = 0.35, 0.28
        for i, item in enumerate(sections):
            col, row = i % cols, i // cols
            x = 0.8 + col * (card_w + gap_x)
            y = 1.7 + row * (card_h + gap_y)
            number = self._text(item.get("number"), f"{i + 1:02d}")
            nodes.extend([
                ShapeNode(x=x, y=y, w=card_w, h=card_h, shape="roundRect",
                          fill=theme.card_tint, line_color=theme.card_tint),
                TextNode(x=x + 0.3, y=y, w=0.85, h=card_h, text=number,
                         font_size=20, bold=True, color=theme.accent, valign="mid"),
                TextNode(x=x + 1.25, y=y, w=card_w - 1.5, h=card_h,
                         text=self._text(item.get("title"), "未命名章节"),
                         font_size=16, bold=True, color=theme.ink, valign="mid"),
            ])
        return nodes

    def _section(self, data: dict[str, Any]) -> list:
        """章节页：超大强调色编号 + 白色标题 + 浅色导语。"""
        theme = self.theme
        nodes = [
            TextNode(x=1.0, y=1.35, w=4.0, h=1.3, text=self._text(data.get("number"), "SECTION"),
                     font_size=48, bold=True, color=theme.accent),
            TextNode(x=1.05, y=2.75, w=10.5, h=1.3, text=self._text(data.get("title"), "章节"),
                     font_size=34, bold=True, color=theme.dark_text, valign="mid"),
        ]
        if data.get("intro"):
            nodes.append(TextNode(x=1.05, y=4.15, w=9.6, h=0.9, text=str(data["intro"]),
                                  font_size=16, color=theme.dark_text_muted))
        return nodes

    def _bullets(self, data: dict[str, Any]) -> list:
        layout = get_layout("bullets")
        return self._title(data, layout, "内容") + self._card(
            layout.slots["body"], "核心要点", self._list(data.get("points")), self.theme.card_tint,
            layout.font_sizes["panel_title"], layout.font_sizes["body"],
        )

    def _stats(self, data: dict[str, Any]) -> list:
        theme = self.theme
        layout = get_layout("stat")
        nodes = self._title(data, layout, "数据亮点")
        grid = layout.slots["grid"]
        stats = [item for item in data.get("stats") or [] if isinstance(item, dict)]
        if not stats:
            stats = [{"value": "-", "label": "指标"}]
        for i, item in enumerate(stats):
            frame = Frame(grid.x + (i % 2) * 6.1, grid.y + (i // 2) * 2.55, 5.65, 2.1)
            nodes.extend(self._stat(frame, item, layout.font_sizes))
        return nodes

    def _comparison(self, data: dict[str, Any]) -> list:
        """左右对比：两块浅色卡片（主色调 / 强调色调），缺内容不填占位符。"""
        theme = self.theme
        layout = get_layout("comparison")
        nodes = self._title(data, layout, "对比")
        slots = (("left", theme.card_tint), ("right", theme.card_tint_accent))
        for slot, tint in slots:
            side = data.get(slot) if isinstance(data.get(slot), dict) else {}
            nodes.extend(self._card(
                layout.slots[slot], self._text(side.get("title"), ""),
                self._list(side.get("items")), tint,
                layout.font_sizes["panel_title"], layout.font_sizes["body"],
            ))
        return nodes

    def _timeline(self, data: dict[str, Any]) -> list:
        theme = self.theme
        layout = get_layout("timeline")
        track = layout.slots["track"]
        points = self._list(data.get("points"))
        nodes = self._title(data, layout, "时间线")
        step = track.w / max(1, len(points))
        for i, point in enumerate(points):
            x = track.x + i * step + step / 2
            nodes.extend([
                ShapeNode(x=x - 0.17, y=track.y + 0.2, w=0.35, h=0.35, shape="ellipse", fill=theme.accent),
                TextNode(x=max(track.x, x - step / 2), y=track.y + 0.8, w=step, h=1.5,
                         text=point, font_size=layout.font_sizes["body"], color=theme.ink, align="center"),
            ])
        if len(points) > 1:
            nodes.append(ShapeNode(x=track.x + step / 2, y=track.y + 0.36,
                                   w=step * (len(points) - 1), h=0.03, shape="line",
                                   line_color=theme.line, line_width=1.5))
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
                      labels=labels, series=series, chart_colors=self.theme.chart_palette())
        ]

    def _table(self, data: dict[str, Any]) -> list:
        theme = self.theme
        layout = get_layout("table")
        rows = data.get("rows") if isinstance(data.get("rows"), list) else []
        width = len(rows[0]) if rows and isinstance(rows[0], list) else 1
        valid_rows = [[str(cell) for cell in row] for row in rows if isinstance(row, list) and len(row) == width]
        valid_rows = valid_rows or [["暂无数据"]]
        frame = layout.slots["table"]
        # 行高封顶后按实际行数收缩高度并垂直居中（渲染端同样按 0.62 封顶）
        row_h = min(frame.h / len(valid_rows), 0.62)
        pos = {
            "x": frame.x, "w": frame.w,
            "y": frame.y + (frame.h - row_h * len(valid_rows)) / 2,
            "h": row_h * len(valid_rows),
        }
        return self._title(data, layout, "明细数据") + [
            TableNode(**pos, rows=valid_rows,
                      font_size=layout.font_sizes["body"],
                      color=theme.ink, header_fill=theme.primary, header_color="FFFFFF",
                      border_color=theme.line, zebra_color=theme.card_tint)
        ]

    def _image(self, data: dict[str, Any]) -> list:
        """图片页：等比适配（contain）嵌入左框，右侧浅色卡片承载图注。"""
        theme = self.theme
        layout = get_layout("image")
        nodes = self._title(data, layout, "图表解读")
        points = self._list(data.get("points"))
        caption = self._text(data.get("caption"), "") if not points else ""
        frame = layout.slots["image"]
        if data.get("image_path"):
            # 白卡承托图表（上游 PNG 自带米色底，与主题浅底直排会显脏；白底卡片统一观感）
            nodes.append(ShapeNode(x=frame.x - 0.12, y=frame.y - 0.12, w=frame.w + 0.24,
                                   h=frame.h + 0.24, shape="roundRect", fill="FFFFFF",
                                   line_color=theme.line, line_width=0.75))
            nodes.append(ImageNode(**self._fit_image(str(data["image_path"]), frame),
                                   path=str(data["image_path"])))
        cap_frame = layout.slots["caption"]
        card = Frame(cap_frame.x - 0.25, cap_frame.y - 0.25, cap_frame.w + 0.5, cap_frame.h + 0.5)
        if points or caption:
            # 与 _card 同款行分布 + 垂直居中，图注不再堆在顶部
            nodes.extend(self._card(card, "图表解读", points or ([caption] if caption else []),
                                    theme.card_tint, 16, layout.font_sizes["body"]))
        return nodes

    def _summary(self, data: dict[str, Any]) -> list:
        theme = self.theme
        layout = get_layout("summary")
        nodes = (
            self._title(data, layout, "总结")
            + self._card(layout.slots["left"], "关键结论", self._list(data.get("takeaways")),
                         theme.card_tint, layout.font_sizes["panel_title"], layout.font_sizes["body"])
            + self._card(layout.slots["right"], "下一步", self._list(data.get("next_steps")),
                         theme.card_tint_accent, layout.font_sizes["panel_title"], layout.font_sizes["body"])
        )
        if data.get("contact"):
            nodes.append(TextNode(
                **self._pos(layout.slots["contact"]), text=str(data["contact"]),
                font_size=12, color=theme.muted, align="right",
            ))
        return nodes

    def _title(self, data: dict[str, Any], layout: Any, fallback: str) -> list:
        """内容页标题：强调色小方块 + 主色标题（无装饰线——AI 痕迹红线）。"""
        theme = self.theme
        frame = layout.slots["title"]
        return [
            ShapeNode(x=frame.x, y=frame.y + frame.h / 2 - 0.09, w=0.18, h=0.18,
                      shape="roundRect", fill=theme.accent, line_color=theme.accent),
            TextNode(x=frame.x + 0.38, y=frame.y, w=frame.w - 0.38, h=frame.h,
                     text=self._text(data.get("title"), fallback),
                     font_size=layout.font_sizes["title"], bold=True, color=theme.primary, valign="mid"),
        ]

    def _card(
        self, frame: Frame, title: str, items: list[str], tint: str,
        title_size: float, body_size: float,
    ) -> list:
        """浅色调圆角卡片：条目按行分布并整体垂直居中，避免内容挤在顶部。

        行高封顶（0.95in）：条目少时上下留白均衡，不会撑出过大空隙。
        """
        theme = self.theme
        nodes = [ShapeNode(x=frame.x, y=frame.y, w=frame.w, h=frame.h,
                           shape="roundRect", fill=tint, line_color=tint)]
        top = frame.y + 0.3
        if title:
            nodes.append(TextNode(x=frame.x + 0.35, y=top, w=frame.w - 0.7, h=0.45,
                                  text=title, font_size=title_size, bold=True, color=theme.primary))
            top += 0.62
        if items:
            body_h = frame.y + frame.h - top - 0.25
            row_h = min(0.95, body_h / len(items))
            start_y = top + max(0, (body_h - row_h * len(items)) / 2)
            for i, item in enumerate(items):
                nodes.append(TextNode(x=frame.x + 0.35, y=start_y + i * row_h,
                                      w=frame.w - 0.7, h=row_h, text=f"• {item}",
                                      font_size=body_size, color=theme.ink,
                                      valign="mid", margin=0.05))
        return nodes

    def _stat(self, frame: Frame, item: dict[str, Any], fonts: Any) -> list:
        theme = self.theme
        nodes = [
            ShapeNode(x=frame.x, y=frame.y, w=frame.w, h=frame.h,
                      shape="roundRect", fill=theme.card_tint, line_color=theme.card_tint),
            TextNode(x=frame.x + 0.4, y=frame.y + 0.3, w=frame.w - 1.9, h=0.8,
                     text=self._text(item.get("value"), "-"), font_size=fonts["value"],
                     bold=True, color=theme.accent),
            TextNode(x=frame.x + 0.4, y=frame.y + 1.25, w=frame.w - 0.8, h=0.5,
                     text=self._text(item.get("label"), "指标"), font_size=fonts["label"],
                     color=theme.muted),
        ]
        if item.get("trend"):
            nodes.append(TextNode(x=frame.x + frame.w - 1.75, y=frame.y + 0.35, w=1.4, h=0.45,
                                  text=str(item["trend"]), font_size=fonts["trend"], bold=True,
                                  color=theme.primary, align="right"))
        return nodes

    @staticmethod
    def _fit_image(path: str, frame: Frame) -> dict[str, float]:
        """等比 contain 适配：读图片实际像素，长边贴合、短边居中，防拉伸变形。"""
        try:
            from PIL import Image

            with Image.open(path) as image:
                pixel_w, pixel_h = image.size
        except Exception:
            return {"x": frame.x, "y": frame.y, "w": frame.w, "h": frame.h}
        if pixel_w <= 0 or pixel_h <= 0:
            return {"x": frame.x, "y": frame.y, "w": frame.w, "h": frame.h}
        frame_ratio = frame.w / frame.h
        image_ratio = pixel_w / pixel_h
        if image_ratio > frame_ratio:
            w = frame.w
            h = w / image_ratio
        else:
            h = frame.h
            w = h * image_ratio
        return {
            "x": frame.x + (frame.w - w) / 2,
            "y": frame.y + (frame.h - h) / 2,
            "w": w,
            "h": h,
        }

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
