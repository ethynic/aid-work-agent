"""页面操作封装

从 tools_semantic.py 提取的核心页面操作逻辑，
供 BrowserOrchestrator 内部调用。
"""

import asyncio
import inspect
from typing import Any, Dict, Optional

from loguru import logger

from src.tools._helpers import sanitize_error
from src.tools.browser.session import BrowserSession, has_browser_session, get_browser_session
from src.tools.browser.semantic import NaturalMatcher, RefMapper, SemanticSnapshotGenerator
from src.tools.browser.tools_snapshot import get_ref_mapper, store_ref_mapper


async def wait_for_page_stable(page, timeout: int = 5000):
    """等待页面在操作后达到稳定状态"""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass

    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass

    await asyncio.sleep(0.3)

    try:
        await page.wait_for_function(
            """() => {
                const loaders = document.querySelectorAll(
                    '.loading, .spinner, .skeleton, [aria-busy="true"], ' +
                    '.ant-spin, .el-loading-mask, .v-loading-mask'
                );
                for (const loader of loaders) {
                    if (loader.offsetParent !== null) return false;
                }
                return true;
            }""",
            timeout=2000,
        )
    except Exception:
        pass

    try:
        await page.wait_for_function(
            """() => {
                const iframes = document.querySelectorAll('iframe');
                for (const iframe of iframes) {
                    if (iframe.offsetParent === null) continue;
                    const src = iframe.getAttribute('src') || '';
                    if (!src || src === 'about:blank') return false;
                    try {
                        const doc = iframe.contentDocument;
                        if (doc && doc.readyState !== 'complete') return false;
                    } catch (e) { }
                }
                return true;
            }""",
            timeout=5000,
        )
    except Exception:
        pass

    try:
        iframe_count = await page.evaluate("() => document.querySelectorAll('iframe').length")
        if iframe_count > 0:
            await asyncio.sleep(0.5)
    except Exception:
        pass

    logger.debug("[wait_for_page_stable] 页面已稳定")


class PageOps:
    """页面操作类

    封装语义快照驱动的页面操作（click/fill/select/navigate/get_content），
    供 orchestrator 和工具调用。
    """

    def __init__(self, session: BrowserSession, session_id: str):
        self.session = session
        self.session_id = session_id

    async def navigate(self, url: str, wait_for: str = "load") -> Dict[str, Any]:
        """打开 URL"""
        if not self.session.is_running():
            await self.session.start()

        await self.session.page.goto(url, wait_until=wait_for, timeout=30000)
        title = await self.session.page.title()
        current_url = self.session.page.url

        SemanticSnapshotGenerator.invalidate_cache(url=current_url)

        logger.info("[PageOps] 导航成功")
        return {
            "success": True,
            "message": f"已打开: {title}",
            "url": current_url,
            "title": title,
        }

    async def take_snapshot(self, mode: str = "interactive") -> Dict[str, Any]:
        """获取语义快照"""
        generator = SemanticSnapshotGenerator(session_id=self.session_id)
        snapshot = await generator.generate(
            page=self.session.page,
            mode=mode,
        )

        if not snapshot.success:
            return {"success": False, "error": f"快照生成失败: {snapshot.error}"}

        store_ref_mapper(self.session_id, generator.get_ref_mapper())

        result = snapshot.to_dict()
        logger.info("[PageOps] 快照生成: 元素数={}", len(snapshot.interactive_elements))
        return result

    async def click(self, description: str, ref: Optional[str] = None) -> Dict[str, Any]:
        """点击元素"""
        ref_mapper = get_ref_mapper(self.session_id)
        if not ref_mapper:
            return {"success": False, "error": "未找到语义快照，请先调用 take_snapshot"}

        target_ref = ref
        target_label = description

        if not target_ref:
            matcher = NaturalMatcher(ref_mapper)
            match_result = matcher.match_click(description)
            if not match_result.success:
                return {"success": False, "error": match_result.error, "alternatives": match_result.alternatives}
            target_ref = match_result.ref
            target_label = match_result.label

        element = await ref_mapper.get_handle(target_ref)
        if not element:
            return {"success": False, "error": f"无法定位元素 ref={target_ref}"}

        if inspect.iscoroutine(element):
            return {"success": False, "error": f"内部错误：元素句柄异常 (ref={target_ref})"}

        try:
            await element.click(timeout=10000, force=True)
        except Exception as click_err:
            logger.warning(
                "element.click(force=True) 失败，尝试 JS 点击: type={}",
                type(click_err).__name__,
            )
            js_context = self.session.page
            elem_info = ref_mapper.get_by_ref(target_ref)
            if elem_info and elem_info.frame_url:
                frame = ref_mapper._frame_map.get(elem_info.frame_url)
                if frame:
                    js_context = frame
            await js_context.evaluate(f"""(selector) => {{
                const el = document.querySelector(selector);
                if (el) el.click();
            }}""", f'[data-ref="{target_ref}"]')

        await wait_for_page_stable(self.session.page)
        SemanticSnapshotGenerator.invalidate_cache(url=self.session.page.url)

        current_url = self.session.page.url
        title = await self.session.page.title()

        logger.info("[PageOps] 点击成功: ref={}", target_ref)
        return {
            "success": True,
            "message": f"已点击: {target_label}",
            "ref": target_ref,
            "label": target_label,
            "current_url": current_url,
            "page_title": title,
        }

    async def fill(self, field: str, value: str, ref: Optional[str] = None) -> Dict[str, Any]:
        """填写表单字段"""
        ref_mapper = get_ref_mapper(self.session_id)
        if not ref_mapper:
            return {"success": False, "error": "未找到语义快照，请先调用 take_snapshot"}

        target_ref = ref
        target_label = field

        if not target_ref:
            matcher = NaturalMatcher(ref_mapper)
            match_result = matcher.match_fill(field, value)
            if not match_result.success:
                return {"success": False, "error": match_result.error, "alternatives": match_result.alternatives}
            target_ref = match_result.ref
            target_label = match_result.label

        element = await ref_mapper.get_handle(target_ref)
        if not element:
            return {"success": False, "error": f"无法定位元素 ref={target_ref}"}

        if inspect.iscoroutine(element):
            return {"success": False, "error": f"内部错误：元素句柄异常 (ref={target_ref})"}

        try:
            await element.fill(value, timeout=10000, force=True)
        except Exception as fill_err:
            logger.warning(
                "element.fill(force=True) 失败，尝试 JS 填写: type={}",
                type(fill_err).__name__,
            )
            js_context = self.session.page
            elem_info = ref_mapper.get_by_ref(target_ref)
            if elem_info and elem_info.frame_url:
                frame = ref_mapper._frame_map.get(elem_info.frame_url)
                if frame:
                    js_context = frame
            await js_context.evaluate("""(args) => {
                const el = document.querySelector(args.selector);
                if (!el) return;
                el.focus();
                const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                )?.set || Object.getOwnPropertyDescriptor(
                    window.HTMLTextAreaElement.prototype, 'value'
                )?.set;
                if (nativeInputValueSetter) {
                    nativeInputValueSetter.call(el, args.value);
                } else {
                    el.value = args.value;
                }
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }""", {"selector": f'[data-ref="{target_ref}"]', "value": value})

        await asyncio.sleep(0.3)
        SemanticSnapshotGenerator.invalidate_cache(url=self.session.page.url)

        logger.info("[PageOps] 填写成功: ref={}", target_ref)
        return {
            "success": True,
            "message": f"已填写: {target_label}",
            "ref": target_ref,
            "label": target_label,
            "value": value,
        }

    async def select(self, field: str, option: str, ref: Optional[str] = None) -> Dict[str, Any]:
        """选择下拉选项"""
        ref_mapper = get_ref_mapper(self.session_id)
        if not ref_mapper:
            return {"success": False, "error": "未找到语义快照，请先调用 take_snapshot"}

        target_ref = ref
        target_label = field

        if not target_ref:
            matcher = NaturalMatcher(ref_mapper)
            match_result = matcher.match_select(field, option)
            if not match_result.success:
                return {"success": False, "error": match_result.error, "alternatives": match_result.alternatives}
            target_ref = match_result.ref
            target_label = match_result.label

        element = await ref_mapper.get_handle(target_ref)
        if not element:
            return {"success": False, "error": f"无法定位元素 ref={target_ref}"}

        if inspect.iscoroutine(element):
            return {"success": False, "error": f"内部错误：元素句柄异常 (ref={target_ref})"}

        await element.click(timeout=10000)
        await wait_for_page_stable(self.session.page, timeout=3000)

        try:
            await element.select_option(option, timeout=10000)
        except Exception:
            pass

        await asyncio.sleep(0.3)
        SemanticSnapshotGenerator.invalidate_cache(url=self.session.page.url)

        logger.info("[PageOps] 选择成功: ref={}", target_ref)
        return {
            "success": True,
            "message": f"已选择: {option}",
            "ref": target_ref,
            "field": target_label,
            "option": option,
        }

    async def get_content(self, format: str = "markdown", selector: Optional[str] = None) -> Dict[str, Any]:
        """获取页面内容"""
        try:
            if selector:
                if format == "text":
                    content = await self.session.page.inner_text(selector)
                elif format == "html":
                    content = await self.session.page.inner_html(selector)
                else:
                    html = await self.session.page.inner_html(selector)
                    content = self._html_to_markdown(html)
            else:
                if format == "text":
                    content = await self.session.page.inner_text("body")
                elif format == "html":
                    content = await self.session.page.content()
                else:
                    html = await self._extract_main_content(self.session.page)
                    content = self._html_to_markdown(html)

            title = await self.session.page.title()
            url = self.session.page.url

            max_len = 50000 if format == "markdown" else 10000
            truncated = len(content) > max_len

            result = {
                "success": True,
                "message": "获取页面内容成功",
                "url": url,
                "title": title,
                "format": format,
                "content": content[:max_len] if truncated else content,
                "content_length": len(content),
                "truncated": truncated,
            }
            if format == "markdown":
                result["markdown"] = result["content"]
            return result

        except Exception as e:
            return {
                "success": False,
                "error": sanitize_error(e, fallback="获取网页内容失败"),
            }

    async def take_screenshot(self, path: str = "./screenshot.png", full_page: bool = False) -> Dict[str, Any]:
        """截图"""
        await self.session.page.screenshot(path=path, full_page=full_page)
        return {"success": True, "screenshot_path": path}

    async def go_back(self) -> Dict[str, Any]:
        """后退"""
        await self.session.page.go_back()
        SemanticSnapshotGenerator.invalidate_cache(url=self.session.page.url)
        title = await self.session.page.title()
        return {"success": True, "url": self.session.page.url, "title": title}

    async def go_forward(self) -> Dict[str, Any]:
        """前进"""
        await self.session.page.go_forward()
        SemanticSnapshotGenerator.invalidate_cache(url=self.session.page.url)
        title = await self.session.page.title()
        return {"success": True, "url": self.session.page.url, "title": title}

    async def reload(self) -> Dict[str, Any]:
        """刷新"""
        await self.session.page.reload(wait_until="networkidle")
        SemanticSnapshotGenerator.invalidate_cache(url=self.session.page.url)
        title = await self.session.page.title()
        return {"success": True, "url": self.session.page.url, "title": title}

    async def _extract_main_content(self, page) -> str:
        content_selectors = [
            'article', '[role="main"]', 'main',
            '.post-content', '.article-content', '.entry-content',
            '.content', '#content', '.post', '.article',
        ]
        for selector in content_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    html = await element.inner_html()
                    if len(html) > 200:
                        return html
            except Exception:
                continue
        return await page.inner_html('body')

    def _html_to_markdown(self, html: str) -> str:
        try:
            from markdownify import markdownify as md
            import re

            markdown_content = md(
                html,
                heading_style="atx",
                bullets="-",
                strip=['script', 'style', 'nav', 'footer', 'header', 'aside'],
                escape_asterisks=False,
                escape_underscores=False,
            )
            markdown_content = re.sub(r'\n{3,}', '\n\n', markdown_content)
            if len(markdown_content) > 50000:
                markdown_content = markdown_content[:50000] + "\n\n... [内容已截断]"
            return markdown_content.strip()

        except ImportError:
            return self._simple_html_to_markdown(html)
        except Exception:
            return self._simple_html_to_markdown(html)

    def _simple_html_to_markdown(self, html: str) -> str:
        import re

        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<nav[^>]*>.*?</nav>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<footer[^>]*>.*?</footer>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<aside[^>]*>.*?</aside>', '', html, flags=re.DOTALL | re.IGNORECASE)

        html = re.sub(r'<h1[^>]*>(.*?)</h1>', r'\n# \1\n', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<h2[^>]*>(.*?)</h2>', r'\n## \1\n', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<h3[^>]*>(.*?)</h3>', r'\n### \1\n', html, flags=re.DOTALL | re.IGNORECASE)

        html = re.sub(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\1\n', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<br\s*/?>', r'\n', html, flags=re.IGNORECASE)
        html = re.sub(r'<[^>]+>', '', html)
        html = re.sub(r'\n{3,}', '\n\n', html)

        if len(html) > 50000:
            html = html[:50000] + "\n\n... [内容已截断]"
        return html.strip()
