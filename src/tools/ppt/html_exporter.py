"""Render HTML slide decks to raster-backed SlideDeckSpec objects."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image

from src.tools.ppt.spec import (
    ImageNode,
    RasterLayerNode,
    ShapeNode,
    SlideDeckSpec,
    SlideSpec,
    TableNode,
    TextNode,
)


class HtmlExportError(RuntimeError):
    """Sanitized HTML export failure."""


class HtmlExportDependencyError(HtmlExportError):
    """Playwright or Chromium is unavailable."""


class HtmlExportTimeoutError(HtmlExportError):
    """The HTML page did not reach a capturable state in time."""


class HtmlExportSecurityError(HtmlExportError):
    """The HTML input violates local export safety constraints."""


@dataclass(frozen=True)
class HtmlScreenshot:
    page_number: int
    path: str
    width: int
    height: int
    duration_ms: int
    size_bytes: int


@dataclass(frozen=True)
class HtmlExportResult:
    spec: SlideDeckSpec
    screenshots: list[HtmlScreenshot]
    editable_spec: SlideDeckSpec | None = None
    editability: dict | None = None
    dom_manifest: list[list[dict]] | None = None

    def screenshot_manifest(self, *, include_paths: bool = True) -> list[dict]:
        manifest = [asdict(item) for item in self.screenshots]
        if not include_paths:
            for item in manifest:
                item.pop("path", None)
        return manifest


class HtmlExporter:
    """Capture HTML strings or local HTML files with Playwright Chromium."""

    MAX_HTML_BYTES = 10 * 1024 * 1024
    MIN_SCREENSHOT_BYTES = 512
    TEXT_TAGS = {"H1", "H2", "P", "LI"}

    def __init__(
        self,
        *,
        viewport_width: int = 1920,
        viewport_height: int = 1080,
        image_format: str = "png",
        jpeg_quality: int = 90,
        timeout_ms: int = 30000,
    ):
        if not 320 <= viewport_width <= 7680 or not 240 <= viewport_height <= 4320:
            raise ValueError("HTML export viewport is outside the supported range")
        if image_format not in {"png", "jpeg"}:
            raise ValueError("HTML export image format must be png or jpeg")
        if not 1 <= jpeg_quality <= 100:
            raise ValueError("HTML export JPEG quality must be between 1 and 100")
        if not 1000 <= timeout_ms <= 120000:
            raise ValueError("HTML export timeout must be between 1000 and 120000 ms")
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.image_format = image_format
        self.jpeg_quality = jpeg_quality
        self.timeout_ms = timeout_ms

    def export(
        self,
        source: str | Path,
        output_dir: str | Path,
        *,
        title: str = "HTML 演示文稿",
        source_is_file: bool | None = None,
        include_editable: bool = False,
    ) -> HtmlExportResult:
        output = Path(output_dir).resolve()
        output.mkdir(parents=True, exist_ok=True)
        html_file = self._resolve_source_file(source, source_is_file)
        html_content = None if html_file else str(source)
        if html_content is not None and len(html_content.encode("utf-8")) > self.MAX_HTML_BYTES:
            raise HtmlExportSecurityError("HTML 内容超过 10MB 安全限制")

        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise HtmlExportDependencyError(
                "Playwright 未安装，无法执行 HTML 高保真导出"
            ) from exc

        screenshots: list[HtmlScreenshot] = []
        editable_slides: list[SlideSpec] = []
        dom_manifest: list[list[dict]] = []
        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except PlaywrightError as exc:
                    raise HtmlExportDependencyError(
                        "Playwright Chromium 未安装或不可用"
                    ) from exc
                try:
                    context = browser.new_context(
                        viewport={
                            "width": self.viewport_width,
                            "height": self.viewport_height,
                        },
                        device_scale_factor=1,
                        accept_downloads=False,
                    )
                    page = context.new_page()
                    page.set_default_timeout(self.timeout_ms)
                    page.on("dialog", lambda dialog: dialog.dismiss())
                    self._restrict_requests(page, html_file)
                    if html_file:
                        page.goto(html_file.as_uri(), wait_until="load", timeout=self.timeout_ms)
                    else:
                        page.set_content(
                            html_content or "",
                            wait_until="load",
                            timeout=self.timeout_ms,
                        )
                    self._wait_for_final_state(page)
                    slide_count = page.locator("section.slide").count()
                    if slide_count:
                        for index in range(slide_count):
                            self._activate_slide(page, index)
                            try:
                                screenshots.append(self._capture(page, output, index + 1))
                                if include_editable:
                                    editable_slide, elements = self._extract_editable_slide(
                                        page, output, index + 1
                                    )
                                    editable_slides.append(editable_slide)
                                    dom_manifest.append(elements)
                            finally:
                                self._restore_slide_state(page)
                    else:
                        screenshots.append(self._capture(page, output, 1))
                        if include_editable:
                            editable_slide, elements = self._extract_editable_slide(
                                page, output, 1
                            )
                            editable_slides.append(editable_slide)
                            dom_manifest.append(elements)
                finally:
                    browser.close()
        except HtmlExportError:
            raise
        except PlaywrightTimeoutError as exc:
            raise HtmlExportTimeoutError("HTML 渲染等待超时") from exc
        except PlaywrightError as exc:
            raise HtmlExportError("HTML 页面渲染失败") from exc
        except OSError as exc:
            raise HtmlExportError("HTML 截图文件写入失败") from exc

        self._validate_screenshots(screenshots)
        width_inches = 13.333
        height_inches = width_inches * self.viewport_height / self.viewport_width
        slides = [
            SlideSpec(
                id=f"html-slide-{item.page_number}",
                layout="html-raster",
                nodes=[
                    RasterLayerNode(
                        x=0,
                        y=0,
                        w=width_inches,
                        h=height_inches,
                        path=item.path,
                    )
                ],
            )
            for item in screenshots
        ]
        editable_spec = None
        editability = None
        if include_editable:
            editable_spec = SlideDeckSpec(
                title=title,
                width=width_inches,
                height=height_inches,
                slides=editable_slides,
            )
            editability = self._editability_summary(editable_spec)
        return HtmlExportResult(
            spec=SlideDeckSpec(
                title=title,
                width=width_inches,
                height=height_inches,
                slides=slides,
            ),
            screenshots=screenshots,
            editable_spec=editable_spec,
            editability=editability,
            dom_manifest=dom_manifest if include_editable else None,
        )

    def _extract_editable_slide(
        self, page, output: Path, page_number: int
    ) -> tuple[SlideSpec, list[dict]]:
        extracted = page.evaluate(
            """() => {
                const root = document.querySelector('section.slide[style*="2147483647"]')
                    || document.querySelector('section.slide') || document.body;
                const selector = (element) => {
                    if (element.id) return `#${CSS.escape(element.id)}`;
                    const parts = [];
                    for (let node = element; node && node !== root.parentElement; node = node.parentElement) {
                        let part = node.tagName.toLowerCase();
                        if (node.classList.length) part += '.' + [...node.classList].map(CSS.escape).join('.');
                        if (node.parentElement) {
                            const peers = [...node.parentElement.children].filter((item) => item.tagName === node.tagName);
                            if (peers.length > 1) part += `:nth-of-type(${peers.indexOf(node) + 1})`;
                        }
                        parts.unshift(part);
                    }
                    return parts.join(' > ');
                };
                const color = (value, fallback) => {
                    const match = value && value.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                    return match ? match.slice(1, 4).map((part) => Number(part).toString(16).padStart(2, '0')).join('').toUpperCase() : fallback;
                };
                const visible = (element, style, rect) =>
                    style.display !== 'none' && style.visibility !== 'hidden' &&
                    Number(style.opacity) > 0 && rect.width > 0 && rect.height > 0 &&
                    rect.right > 0 && rect.bottom > 0 && rect.left < innerWidth && rect.top < innerHeight;
                const all = [...root.querySelectorAll('*')];
                const textTags = new Set(['H1', 'H2', 'P', 'LI']);
                const rasterRoots = new Set(all.filter((element) => {
                    const style = getComputedStyle(element);
                    const tag = element.tagName.toUpperCase();
                    const svgComplex = tag === 'SVG' &&
                        (element.querySelectorAll('*').length > 40 || !!element.querySelector('filter,foreignObject,mask,pattern'));
                    return tag === 'CANVAS' || svgComplex ||
                        style.filter !== 'none' || style.backdropFilter !== 'none' ||
                        style.mixBlendMode !== 'normal';
                }));
                const insideRaster = (element) => [...rasterRoots].some((rootNode) =>
                    rootNode !== element && rootNode.contains(element));
                return all.flatMap((element, order) => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    if (!visible(element, style, rect) || insideRaster(element)) return [];
                    const tag = element.tagName.toUpperCase();
                    let kind = null;
                    if (rasterRoots.has(element)) kind = 'raster';
                    else if (['IMG', 'SVG'].includes(tag)) kind = 'image';
                    else if (tag === 'TABLE') kind = 'table';
                    else if (textTags.has(tag)) kind = 'text';
                    else {
                        const names = `${element.className || ''} ${element.getAttribute('role') || ''}`.toLowerCase();
                        const hasVisual = style.backgroundColor !== 'rgba(0, 0, 0, 0)' ||
                            parseFloat(style.borderTopWidth) > 0;
                        if (/(card|divider|separator|tag|badge|chip)/.test(names) || hasVisual) kind = 'shape';
                    }
                    if (!kind) return [];
                    const hasNestedText = [...element.querySelectorAll('h1,h2,p,li')]
                        .some((child) => visible(child, getComputedStyle(child), child.getBoundingClientRect()));
                    const directText = tag === 'LI'
                        ? [...element.childNodes].filter((node) => node.nodeType === Node.TEXT_NODE).map((node) => node.textContent).join(' ').trim()
                        : element.textContent.trim();
                    if (kind === 'text' && (!directText || hasNestedText)) return [];
                    const z = Number.parseInt(style.zIndex, 10);
                    let rows = null;
                    if (kind === 'table') {
                        const grid = [];
                        [...element.rows].forEach((row, rowIndex) => {
                            grid[rowIndex] ||= [];
                            let column = 0;
                            [...row.cells].forEach((cell) => {
                                while (grid[rowIndex][column] !== undefined) column += 1;
                                const rowSpan = Math.max(1, cell.rowSpan || 1);
                                const colSpan = Math.max(1, cell.colSpan || 1);
                                for (let rowOffset = 0; rowOffset < rowSpan; rowOffset += 1) {
                                    grid[rowIndex + rowOffset] ||= [];
                                    for (let colOffset = 0; colOffset < colSpan; colOffset += 1) {
                                        grid[rowIndex + rowOffset][column + colOffset] =
                                            rowOffset === 0 && colOffset === 0 ? cell.innerText.trim() : '';
                                    }
                                }
                                column += colSpan;
                            });
                        });
                        const width = Math.max(0, ...grid.map((row) => row.length));
                        rows = grid.map((row) =>
                            Array.from({length: width}, (_, index) => row[index] || ''));
                    }
                    element.dataset.pptExportId = `${order}`;
                    return [{
                        id: `${order}`, kind, selector: selector(element), order,
                        zIndex: Number.isFinite(z) ? z : 0,
                        rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
                        text: directText, rows,
                        style: {
                            color: color(style.color, '222222'),
                            background: color(style.backgroundColor, 'FFFFFF'),
                            borderColor: color(style.borderTopColor, 'FFFFFF'),
                            borderWidth: parseFloat(style.borderTopWidth) || 0,
                            borderRadius: parseFloat(style.borderRadius) || 0,
                            fontSize: parseFloat(style.fontSize) || 20,
                            fontFamily: style.fontFamily.split(',')[0].replace(/['"]/g, '').trim() || 'Microsoft YaHei',
                            fontWeight: Number.parseInt(style.fontWeight, 10) || (style.fontWeight === 'bold' ? 700 : 400),
                            textAlign: ['center', 'right'].includes(style.textAlign) ? style.textAlign : 'left'
                        }
                    }];
                }).sort((a, b) => a.zIndex - b.zIndex || a.order - b.order);
            }"""
        )
        nodes = []
        for item in extracted:
            frame = self._clip_frame(item["rect"])
            if frame is None:
                continue
            x, y, w, h, clip = frame
            style = item["style"]
            kind = item["kind"]
            if kind in {"image", "raster"}:
                path = output / f"slide-{page_number:03d}-layer-{item['id']}.png"
                if kind == "image":
                    selector = f'[data-ppt-export-id="{item["id"]}"]'
                    page.evaluate(
                        """(selector) => {
                            const target = document.querySelector(selector);
                            window.__pptImageBackgrounds = [];
                            for (let node = target?.parentElement; node; node = node.parentElement) {
                                window.__pptImageBackgrounds.push([
                                    node, node.style.getPropertyValue('background'),
                                    node.style.getPropertyPriority('background')
                                ]);
                                node.style.setProperty('background', 'transparent', 'important');
                            }
                        }""",
                        selector,
                    )
                    try:
                        page.locator(selector).screenshot(
                            path=str(path),
                            type="png",
                            animations="disabled",
                            omit_background=True,
                        )
                    finally:
                        page.evaluate(
                            """() => {
                                for (const [node, value, priority] of window.__pptImageBackgrounds || []) {
                                    if (value) node.style.setProperty('background', value, priority);
                                    else node.style.removeProperty('background');
                                }
                                delete window.__pptImageBackgrounds;
                            }"""
                        )
                else:
                    page.screenshot(
                        path=str(path), type="png", clip=clip, animations="disabled"
                    )
                node_class = RasterLayerNode if kind == "raster" else ImageNode
                nodes.append(node_class(x=x, y=y, w=w, h=h, path=str(path)))
            elif kind == "text":
                nodes.append(
                    TextNode(
                        x=x, y=y, w=w, h=h, text=item["text"],
                        font_size=max(1, min(200, style["fontSize"] * 0.75)),
                        font_face=style["fontFamily"], color=style["color"],
                        bold=style["fontWeight"] >= 600, align=style["textAlign"],
                        bullet=item["selector"].split(" > ")[-1].startswith("li"),
                    )
                )
            elif kind == "table" and item["rows"] and item["rows"][0]:
                width = len(item["rows"][0])
                rows = [row for row in item["rows"] if len(row) == width]
                if rows:
                    nodes.append(
                        TableNode(
                            x=x, y=y, w=w, h=h, rows=rows,
                            font_size=max(1, min(100, style["fontSize"] * 0.75)),
                            color=style["color"], border_color=style["borderColor"],
                        )
                    )
            elif kind == "shape":
                shape = "roundRect" if style["borderRadius"] > 0 else "rect"
                if h <= 0.04 or w <= 0.04:
                    shape = "line"
                nodes.append(
                    ShapeNode(
                        x=x, y=y, w=w, h=h, shape=shape,
                        fill=style["background"], line_color=style["borderColor"],
                        line_width=max(0, style["borderWidth"] * 0.75),
                    )
                )
        page.evaluate(
            "() => document.querySelectorAll('[data-ppt-export-id]').forEach((element) => delete element.dataset.pptExportId)"
        )
        background = page.evaluate(
            """() => {
                const root = document.querySelector('section.slide[style*="2147483647"]')
                    || document.querySelector('section.slide') || document.body;
                const value = getComputedStyle(root).backgroundColor;
                const match = value.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                return match ? match.slice(1, 4).map((part) => Number(part).toString(16).padStart(2, '0')).join('').toUpperCase() : 'FFFFFF';
            }"""
        )
        manifest = [
            {
                "kind": item["kind"],
                "selector": item["selector"],
                "z_index": item["zIndex"],
                "bounds": item["rect"],
            }
            for item in extracted
        ]
        return (
            SlideSpec(
                id=f"html-editable-slide-{page_number}",
                layout="html-editable",
                background=background,
                nodes=nodes,
            ),
            manifest,
        )

    def _clip_frame(self, rect: dict) -> tuple[float, float, float, float, dict] | None:
        left = max(0.0, float(rect["left"]))
        top = max(0.0, float(rect["top"]))
        right = min(float(self.viewport_width), float(rect["left"]) + float(rect["width"]))
        bottom = min(float(self.viewport_height), float(rect["top"]) + float(rect["height"]))
        if right - left < 0.5 or bottom - top < 0.5:
            return None
        width_inches = 13.333
        scale = width_inches / self.viewport_width
        return (
            left * scale, top * scale, (right - left) * scale, (bottom - top) * scale,
            {"x": left, "y": top, "width": right - left, "height": bottom - top},
        )

    @staticmethod
    def _editability_summary(spec: SlideDeckSpec) -> dict:
        counts = {"native_text_count": 0, "native_shape_count": 0, "image_count": 0,
                  "table_count": 0, "raster_layer_count": 0}
        raster_rectangles: dict[int, list[tuple[float, float, float, float]]] = {}
        total_area = spec.width * spec.height * len(spec.slides)
        for slide_index, slide in enumerate(spec.slides):
            for node in slide.nodes:
                key = {
                    "text": "native_text_count", "shape": "native_shape_count",
                    "image": "image_count", "table": "table_count",
                    "raster": "raster_layer_count",
                }.get(node.type)
                if key:
                    counts[key] += 1
                if node.type == "raster":
                    raster_rectangles.setdefault(slide_index, []).append(
                        (node.x, node.y, node.x + node.w, node.y + node.h)
                    )
        raster_area = sum(
            HtmlExporter._rectangle_union_area(rectangles)
            for rectangles in raster_rectangles.values()
        )
        ratio = min(1.0, raster_area / total_area) if total_area else 0.0
        return {
            **counts,
            "raster_area_ratio": round(ratio, 4),
            "editable_ratio": round(1.0 - ratio, 4),
        }

    @staticmethod
    def _rectangle_union_area(
        rectangles: list[tuple[float, float, float, float]]
    ) -> float:
        x_values = sorted({value for rect in rectangles for value in (rect[0], rect[2])})
        area = 0.0
        for left, right in zip(x_values, x_values[1:]):
            intervals = sorted(
                (top, bottom)
                for x1, top, x2, bottom in rectangles
                if x1 < right and x2 > left
            )
            covered = 0.0
            if intervals:
                start, end = intervals[0]
                for top, bottom in intervals[1:]:
                    if top > end:
                        covered += end - start
                        start, end = top, bottom
                    else:
                        end = max(end, bottom)
                covered += end - start
            area += (right - left) * covered
        return area

    def _resolve_source_file(
        self, source: str | Path, source_is_file: bool | None
    ) -> Path | None:
        candidate = Path(source) if isinstance(source, Path) else Path(str(source))
        use_file = source_is_file is True or (
            source_is_file is None and isinstance(source, Path)
        )
        if not use_file:
            return None
        try:
            resolved = candidate.expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise HtmlExportSecurityError("HTML 文件不存在或不可访问") from exc
        if not resolved.is_file() or resolved.suffix.lower() not in {".html", ".htm"}:
            raise HtmlExportSecurityError("仅支持本地 .html/.htm 文件")
        if resolved.stat().st_size > self.MAX_HTML_BYTES:
            raise HtmlExportSecurityError("HTML 文件超过 10MB 安全限制")
        return resolved

    def _restrict_requests(self, page, html_file: Path | None) -> None:
        allowed_root = html_file.parent.resolve() if html_file else None
        allowed_document = html_file.resolve() if html_file else None

        def handle_route(route) -> None:
            url = route.request.url
            parsed = urlparse(url)
            if parsed.scheme in {"data", "blob", "about"}:
                route.continue_()
                return
            if parsed.scheme != "file" or allowed_root is None:
                route.abort("blockedbyclient")
                return
            try:
                raw_path = unquote(parsed.path)
                if len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
                    raw_path = raw_path[1:]
                requested = Path(raw_path).resolve(strict=True)
                if requested == allowed_document or requested.is_relative_to(allowed_root):
                    route.continue_()
                    return
            except (OSError, RuntimeError, ValueError):
                pass
            route.abort("blockedbyclient")

        page.route("**/*", handle_route)

    def _wait_for_final_state(self, page) -> None:
        page.wait_for_function(
            """() => {
                const fontsReady = !document.fonts || document.fonts.status === "loaded";
                const imagesReady = Array.from(document.images).every((image) => image.complete);
                return fontsReady && imagesReady;
            }""",
            timeout=self.timeout_ms,
        )
        page.evaluate(
            """async () => {
                for (const animation of document.getAnimations()) {
                    try { animation.finish(); } catch (_) { animation.cancel(); }
                }
                await new Promise((resolve) => {
                    const fallback = setTimeout(resolve, 250);
                    requestAnimationFrame(() => requestAnimationFrame(() => {
                        clearTimeout(fallback);
                        resolve();
                    }));
                });
            }"""
        )
        page.add_style_tag(
            content="""*, *::before, *::after {
                animation-delay: 0s !important;
                animation-duration: 0s !important;
                transition: none !important;
                caret-color: transparent !important;
            }"""
        )

    def _activate_slide(self, page, index: int) -> None:
        page.evaluate(
            """(activeIndex) => {
                const slides = Array.from(document.querySelectorAll("section.slide"));
                const touched = new Set(slides);
                let ancestor = slides[activeIndex]?.parentElement;
                while (ancestor && ancestor !== document.documentElement) {
                    touched.add(ancestor);
                    ancestor = ancestor.parentElement;
                }
                window.__pptExportStyles = Array.from(touched, (element) => [
                    element, element.getAttribute("style")
                ]);
                for (const [index, slide] of slides.entries()) {
                    slide.style.setProperty("display", index === activeIndex ? "block" : "none", "important");
                    if (index !== activeIndex) continue;
                    slide.style.setProperty("position", "fixed", "important");
                    slide.style.setProperty("inset", "0", "important");
                    slide.style.setProperty("width", "100vw", "important");
                    slide.style.setProperty("height", "100vh", "important");
                    slide.style.setProperty("margin", "0", "important");
                    slide.style.setProperty("transform", "none", "important");
                    slide.style.setProperty("visibility", "visible", "important");
                    slide.style.setProperty("opacity", "1", "important");
                    slide.style.setProperty("z-index", "2147483647", "important");
                }
                ancestor = slides[activeIndex]?.parentElement;
                while (ancestor && ancestor !== document.documentElement) {
                    ancestor.style.setProperty("transform", "none", "important");
                    ancestor.style.setProperty("overflow", "visible", "important");
                    ancestor = ancestor.parentElement;
                }
                window.scrollTo(0, 0);
            }""",
            index,
        )

    def _restore_slide_state(self, page) -> None:
        page.evaluate(
            """() => {
                for (const [element, style] of window.__pptExportStyles || []) {
                    if (style === null) element.removeAttribute("style");
                    else element.setAttribute("style", style);
                }
                delete window.__pptExportStyles;
            }"""
        )

    def _capture(self, page, output: Path, page_number: int) -> HtmlScreenshot:
        extension = "jpg" if self.image_format == "jpeg" else "png"
        path = output / f"slide-{page_number:03d}.{extension}"
        options = {
            "path": str(path),
            "type": self.image_format,
            "animations": "disabled",
            "timeout": self.timeout_ms,
            "full_page": False,
        }
        if self.image_format == "jpeg":
            options["quality"] = self.jpeg_quality
        started = time.perf_counter()
        page.screenshot(**options)
        duration_ms = round((time.perf_counter() - started) * 1000)
        with Image.open(path) as image:
            width, height = image.size
        return HtmlScreenshot(
            page_number=page_number,
            path=str(path),
            width=width,
            height=height,
            duration_ms=duration_ms,
            size_bytes=path.stat().st_size,
        )

    def _validate_screenshots(self, screenshots: list[HtmlScreenshot]) -> None:
        if not screenshots:
            raise HtmlExportError("HTML 导出未生成任何页面")
        for item in screenshots:
            path = Path(item.path)
            if (
                not path.is_file()
                or item.size_bytes < self.MIN_SCREENSHOT_BYTES
                or item.width != self.viewport_width
                or item.height != self.viewport_height
            ):
                raise HtmlExportError(f"HTML 第 {item.page_number} 页截图无效")
            with Image.open(path) as image:
                rgb = image.convert("RGB")
                extrema = rgb.getextrema()
                is_uniform = all(low == high for low, high in extrema)
                is_near_white = all(low >= 250 for low, _ in extrema)
                if is_uniform and is_near_white:
                    raise HtmlExportError(f"HTML 第 {item.page_number} 页为空白页")
