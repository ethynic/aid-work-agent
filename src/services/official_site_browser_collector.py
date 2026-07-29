"""通过可点击导航自动采集协会官网高价值页面。"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
from typing import Protocol

from src.services.association_profile_extractor import (
    MAX_PAGE_COUNT,
    MAX_TOTAL_CONTENT_CHARS,
    VerifiedOfficialPage,
)
from src.services.official_site_page_collector import (
    DEFAULT_MAX_PAGES,
    _normalized_same_domain_url,
    is_high_value_official_navigation,
)


NAVIGATION_SELECTOR = (
    "a, button, [role='button'], [onclick], nav li, "
    "[class*='menu'] li, [class*='nav'] li"
)
MAX_NAVIGATION_TARGETS_PER_STATE = 200
UNSAFE_NAVIGATION_ACTION_TERMS = (
    "登录",
    "注册",
    "退出",
    "删除",
    "提交",
    "报名",
    "支付",
    "下载",
    "login",
    "register",
    "logout",
    "delete",
    "submit",
    "pay",
    "download",
)


@dataclass(frozen=True)
class BrowserNavigationTarget:
    text: str
    href: str | None
    occurrence: int

    @property
    def identity(self) -> tuple[str, str, int]:
        return (self.text, self.href or "", self.occurrence)


@dataclass(frozen=True)
class BrowserPageState:
    url: str
    title: str
    content: str
    navigation_targets: tuple[BrowserNavigationTarget, ...]

    @property
    def fingerprint(self) -> str:
        navigation = "\n".join(
            repr(target.identity) for target in self.navigation_targets
        )
        payload = f"{self.url}\n{self.content}\n{navigation}".encode("utf-8")
        return sha256(payload).hexdigest()


class BrowserNavigationDriver(Protocol):
    async def open(self, url: str) -> BrowserPageState: ...

    async def activate(
        self,
        target: BrowserNavigationTarget,
        previous_state: BrowserPageState,
    ) -> BrowserPageState | None: ...


def _target_is_safe_and_relevant(
    target: BrowserNavigationTarget,
    base_url: str,
    verified_domain: str,
) -> bool:
    target_text = target.text.lower()
    if any(
        (
            term in target_text
            if not term.isascii()
            else re.search(
                rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])",
                target_text,
            )
        )
        for term in UNSAFE_NAVIGATION_ACTION_TERMS
    ):
        return False
    if not is_high_value_official_navigation(target.text, target.href or ""):
        return False
    if target.href and not target.href.lower().startswith(
        ("javascript:", "#", "void(")
    ):
        return (
            _normalized_same_domain_url(target.href, base_url, verified_domain)
            is not None
        )
    return True


async def collect_official_pages_with_browser_driver(
    entry_url: str,
    verified_domain: str,
    driver: BrowserNavigationDriver,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_total_chars: int = MAX_TOTAL_CONTENT_CHARS,
    max_navigation_attempts: int = 36,
) -> list[VerifiedOfficialPage]:
    """从入口自动嗅探并点击高价值导航，输出提取器可直接消费的页面。"""
    if (
        max_pages < 1
        or max_pages > MAX_PAGE_COUNT
        or max_total_chars < 1
        or max_total_chars > MAX_TOTAL_CONTENT_CHARS
        or max_navigation_attempts < 0
    ):
        raise ValueError("invalid browser collector limits")
    normalized_entry = _normalized_same_domain_url(
        entry_url, entry_url, verified_domain
    )
    if normalized_entry is None:
        raise ValueError("entry URL is outside verified domain")

    entry_state = await driver.open(normalized_entry)
    if (
        _normalized_same_domain_url(entry_state.url, normalized_entry, verified_domain)
        is None
        or not entry_state.content.strip()
    ):
        raise ValueError("browser entry page is not trusted or empty")

    page_order: list[str] = []
    page_titles: dict[str, str] = {}
    page_contents: dict[str, str] = {}
    total_chars = 0

    def include(state: BrowserPageState) -> None:
        nonlocal total_chars
        normalized = _normalized_same_domain_url(
            state.url, normalized_entry, verified_domain
        )
        content = state.content.strip()
        if normalized is None or not content:
            return
        existing = page_contents.get(normalized)
        if existing is not None:
            if content in existing:
                return
            merged = f"{existing}\n\n{content}"
            added_chars = len(merged) - len(existing)
            if total_chars + added_chars > max_total_chars:
                return
            page_contents[normalized] = merged
            total_chars += added_chars
            return
        if len(page_order) >= max_pages or total_chars + len(content) > max_total_chars:
            return
        page_order.append(normalized)
        page_titles[normalized] = state.title.strip() or normalized
        page_contents[normalized] = content
        total_chars += len(content)

    include(entry_state)
    visited_states = {entry_state.fingerprint}
    queued_paths: set[tuple[tuple[str, str, int], ...]] = set()
    queue: list[tuple[BrowserNavigationTarget, ...]] = []

    def enqueue_from(
        state: BrowserPageState,
        path: tuple[BrowserNavigationTarget, ...],
    ) -> None:
        path_identities = {item.identity for item in path}
        for target in state.navigation_targets:
            if target.identity in path_identities:
                continue
            if not _target_is_safe_and_relevant(
                target, state.url, verified_domain
            ):
                continue
            next_path = path + (target,)
            identity = tuple(item.identity for item in next_path)
            if identity not in queued_paths:
                queued_paths.add(identity)
                queue.append(next_path)

    enqueue_from(entry_state, ())
    attempts = 0
    while (
        queue
        and attempts < max_navigation_attempts
        and len(page_order) < max_pages
        and total_chars < max_total_chars
    ):
        path = queue.pop(0)
        try:
            state = await driver.open(normalized_entry)
            path_succeeded = True
            for target in path:
                if attempts >= max_navigation_attempts:
                    path_succeeded = False
                    break
                attempts += 1
                next_state = await driver.activate(target, state)
                if next_state is None or next_state.fingerprint == state.fingerprint:
                    path_succeeded = False
                    break
                if (
                    _normalized_same_domain_url(
                        next_state.url, state.url, verified_domain
                    )
                    is None
                ):
                    path_succeeded = False
                    break
                state = next_state
            if not path_succeeded or state.fingerprint in visited_states:
                continue
            visited_states.add(state.fingerprint)
            include(state)
            enqueue_from(state, path)
        except Exception:
            # 单条导航失败不扩大信任边界，也不阻断其他独立栏目。
            continue

    return [
        VerifiedOfficialPage(
            url=url,
            title=page_titles[url],
            content=page_contents[url],
            verified_official=True,
        )
        for url in page_order
    ]


class PlaywrightNavigationDriver:
    """Browser driver 的 Playwright 实现；浏览器生命周期由调用方管理。"""

    def __init__(self, page, *, navigation_timeout_ms: int = 8_000):
        if (
            not isinstance(navigation_timeout_ms, int)
            or isinstance(navigation_timeout_ms, bool)
            or navigation_timeout_ms < 1
        ):
            raise ValueError("navigation_timeout_ms must be a positive integer")
        self._page = page
        self._navigation_timeout_ms = navigation_timeout_ms

    async def _state(self) -> BrowserPageState:
        locator = self._page.locator(NAVIGATION_SELECTOR)
        targets: list[BrowserNavigationTarget] = []
        occurrences: dict[tuple[str, str], int] = {}
        target_count = min(
            await locator.count(),
            MAX_NAVIGATION_TARGETS_PER_STATE,
        )
        for index in range(target_count):
            element = locator.nth(index)
            if not await element.is_visible():
                continue
            text = " ".join((await element.inner_text()).split())[:500]
            if not text:
                continue
            href = await element.get_attribute("href")
            key = (text, href or "")
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            targets.append(
                BrowserNavigationTarget(
                    text=text,
                    href=href,
                    occurrence=occurrence,
                )
            )
        return BrowserPageState(
            url=self._page.url,
            title=await self._page.title(),
            content=await self._page.locator("body").inner_text(),
            navigation_targets=tuple(targets),
        )

    async def open(self, url: str) -> BrowserPageState:
        await self._page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self._navigation_timeout_ms,
        )
        poll_count = max(0, min(self._navigation_timeout_ms, 3_000) // 250)
        state = await self._state()
        for _attempt in range(poll_count):
            if state.content.strip() and state.navigation_targets:
                return state
            await self._page.wait_for_timeout(250)
            state = await self._state()
        return state

    async def activate(
        self,
        target: BrowserNavigationTarget,
        previous_state: BrowserPageState,
    ) -> BrowserPageState | None:
        locator = self._page.locator(NAVIGATION_SELECTOR)
        matches = []
        for index in range(await locator.count()):
            element = locator.nth(index)
            if not await element.is_visible():
                continue
            text = " ".join((await element.inner_text()).split())[:500]
            href = await element.get_attribute("href")
            if text == target.text and (href or "") == (target.href or ""):
                matches.append(element)
        if target.occurrence >= len(matches):
            return None
        await matches[target.occurrence].click(timeout=self._navigation_timeout_ms)
        try:
            await self._page.wait_for_load_state(
                "domcontentloaded",
                timeout=min(self._navigation_timeout_ms, 3_000),
            )
        except Exception:
            pass
        poll_count = max(0, min(self._navigation_timeout_ms, 3_000) // 250)
        state = await self._state()
        if state.fingerprint != previous_state.fingerprint:
            return state
        for _attempt in range(poll_count):
            await self._page.wait_for_timeout(250)
            state = await self._state()
            if state.fingerprint != previous_state.fingerprint:
                return state
        return None


async def _guard_main_frame_navigation(
    route,
    request,
    *,
    entry_url: str,
    verified_domain: str,
) -> None:
    """在请求发出前阻断跨域导航，包括尚未创建 frame 的 popup 请求。"""
    if (
        request.is_navigation_request()
        and _normalized_same_domain_url(
            request.url,
            entry_url,
            verified_domain,
        )
        is None
    ):
        await route.abort()
        return
    await route.continue_()


async def collect_official_pages_with_playwright(
    entry_url: str,
    verified_domain: str,
    *,
    headless: bool,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_total_chars: int = MAX_TOTAL_CONTENT_CHARS,
    max_navigation_attempts: int = 36,
    navigation_timeout_ms: int = 8_000,
) -> list[VerifiedOfficialPage]:
    """启动 Playwright 自动点击采集；真机风控回退应显式传 ``headless=False``。"""
    if not isinstance(headless, bool):
        raise ValueError("headless must be an explicit boolean")
    if (
        not isinstance(navigation_timeout_ms, int)
        or isinstance(navigation_timeout_ms, bool)
        or navigation_timeout_ms < 1
    ):
        raise ValueError("navigation_timeout_ms must be a positive integer")
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        try:
            page = await browser.new_page()
            async def guard_navigation(route):
                await _guard_main_frame_navigation(
                    route,
                    route.request,
                    entry_url=entry_url,
                    verified_domain=verified_domain,
                )

            await page.context.route("**/*", guard_navigation)
            driver = PlaywrightNavigationDriver(
                page,
                navigation_timeout_ms=navigation_timeout_ms,
            )
            return await collect_official_pages_with_browser_driver(
                entry_url,
                verified_domain,
                driver,
                max_pages=max_pages,
                max_total_chars=max_total_chars,
                max_navigation_attempts=max_navigation_attempts,
            )
        finally:
            await browser.close()
