"""Render HTML slide decks to raster-backed SlideDeckSpec objects."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image

from src.tools.ppt.spec import RasterLayerNode, SlideDeckSpec, SlideSpec


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
                            finally:
                                self._restore_slide_state(page)
                    else:
                        screenshots.append(self._capture(page, output, 1))
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
        return HtmlExportResult(
            spec=SlideDeckSpec(
                title=title,
                width=width_inches,
                height=height_inches,
                slides=slides,
            ),
            screenshots=screenshots,
        )

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
