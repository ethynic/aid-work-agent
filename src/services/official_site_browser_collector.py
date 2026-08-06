"""通过可点击导航自动采集协会官网高价值页面。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from hashlib import sha256
import re
from time import monotonic
from typing import Callable, Protocol

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
MAX_COLLECTION_SECONDS = 90.0
MAX_SINGLE_PATH_SECONDS = 20.0
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
    locator_index: int | None = None

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


def _target_business_priority(target: BrowserNavigationTarget) -> int:
    haystack = f"{target.text} {target.href or ''}".lower()
    weighted_terms = (
        (100, ("组织领导", "驻会领导", "领导集体", "领导班子", "领导机构", "协会领导",
               "leadership", "leader")),
        (90, ("协会设置", "组织架构", "组织机构", "机构设置", "秘书处",
              "organization", "organisational", "organizational")),
        (80, ("协会简介", "协会介绍", "协会概况", "关于协会",
              "about", "profile", "introduction")),
        (70, ("联系我们", "联系方式", "contact")),
        (60, ("分支机构", "专业委员会", "branch", "committee")),
        (20, ("会员", "member", "members", "membership")),
    )
    return max(
        (
            weight
            for weight, terms in weighted_terms
            if any(term in haystack for term in terms)
        ),
        default=0,
    )


async def collect_official_pages_with_browser_driver(
    entry_url: str,
    verified_domain: str,
    driver: BrowserNavigationDriver,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_total_chars: int = MAX_TOTAL_CONTENT_CHARS,
    max_navigation_attempts: int = 36,
    audit_callback: Callable[..., None] | None = None,
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

    def audit(**event) -> None:
        if audit_callback is None:
            return
        try:
            audit_callback(**event)
        except Exception:
            pass

    collection_deadline = monotonic() + MAX_COLLECTION_SECONDS
    entry_deadline = min(
        collection_deadline,
        monotonic() + MAX_SINGLE_PATH_SECONDS,
    )
    audit(
        stage="官网采集",
        kind="web_open",
        summary="打开官网入口",
        detail={"url": normalized_entry},
    )
    entry_state = await asyncio.wait_for(
        driver.open(normalized_entry),
        timeout=max(0.001, entry_deadline - monotonic()),
    )
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
        audit(
            stage="官网采集",
            kind="web_page",
            summary=f"已读取 {page_titles[normalized]}",
            detail={
                "url": normalized,
                "title": page_titles[normalized],
                "content": content,
            },
        )

    include(entry_state)
    visited_states = {entry_state.fingerprint}
    queued_paths: set[tuple[tuple[str, str, int], ...]] = set()
    queue: list[tuple[BrowserNavigationTarget, ...]] = []

    def enqueue_from(
        state: BrowserPageState,
        path: tuple[BrowserNavigationTarget, ...],
    ) -> None:
        path_identities = {item.identity for item in path}
        ordered_targets = sorted(
            state.navigation_targets,
            key=_target_business_priority,
            reverse=True,
        )
        for target in ordered_targets:
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
        # Newly discovered nested leadership/organization paths must compete
        # globally with root paths instead of waiting behind the root FIFO.
        queue.sort(
            key=lambda candidate: (
                _target_business_priority(candidate[-1]),
                -len(candidate),
            ),
            reverse=True,
        )

    enqueue_from(entry_state, ())
    attempts = 0
    while (
        queue
        and attempts < max_navigation_attempts
        and len(page_order) < max_pages
        and total_chars < max_total_chars
        and monotonic() < collection_deadline
    ):
        path = queue.pop(0)
        path_deadline = min(
            collection_deadline,
            monotonic() + MAX_SINGLE_PATH_SECONDS,
        )
        try:
            audit(
                stage="官网采集",
                kind="web_open",
                summary="重放官网入口",
                detail={"url": normalized_entry},
            )
            state = await asyncio.wait_for(
                driver.open(normalized_entry),
                timeout=max(0.001, path_deadline - monotonic()),
            )
            path_succeeded = True
            for target in path:
                if (
                    attempts >= max_navigation_attempts
                    or monotonic() >= path_deadline
                ):
                    path_succeeded = False
                    break
                attempts += 1
                direct_url = None
                if target.href and not target.href.lower().startswith(
                    ("javascript:", "void(")
                ) and "#" not in target.href:
                    direct_url = _normalized_same_domain_url(
                        target.href,
                        state.url,
                        verified_domain,
                    )
                operation = (
                    driver.open(direct_url)
                    if direct_url is not None
                    else driver.activate(target, state)
                )
                audit(
                    stage="官网采集",
                    kind="web_open",
                    summary=f"打开导航：{target.text}",
                    detail={
                        "url": direct_url,
                        "navigation_text": target.text,
                    },
                )
                next_state = await asyncio.wait_for(
                    operation,
                    timeout=max(0.001, path_deadline - monotonic()),
                )
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
        if hasattr(locator, "evaluate_all"):
            snapshots = await locator.evaluate_all(
                """(elements, limit) => elements.map((element, index) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    const visible = style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                    return {
                        index,
                        visible,
                        text: (element.innerText || element.textContent || ''),
                        href: element.getAttribute('href')
                    };
                }).filter((item) => item.visible).slice(0, limit)""",
                MAX_NAVIGATION_TARGETS_PER_STATE,
            )
        else:
            snapshots = []
            target_count = await locator.count()
            for index in range(target_count):
                element = locator.nth(index)
                visible = await element.is_visible()
                if not visible:
                    continue
                snapshots.append({
                    "index": index,
                    "visible": True,
                    "text": await element.inner_text(),
                    "href": await element.get_attribute("href"),
                })
                if len(snapshots) >= MAX_NAVIGATION_TARGETS_PER_STATE:
                    break
        for snapshot in snapshots:
            if not snapshot.get("visible"):
                continue
            text = " ".join(str(snapshot.get("text") or "").split())[:500]
            if not text:
                continue
            href = snapshot.get("href")
            href = str(href) if href is not None else None
            key = (text, href or "")
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            targets.append(
                BrowserNavigationTarget(
                    text=text,
                    href=href,
                    occurrence=occurrence,
                    locator_index=int(snapshot["index"]),
                )
            )
        return BrowserPageState(
            url=self._page.url,
            title=await self._page.title(),
            content=await self._page.locator("body").inner_text(),
            navigation_targets=tuple(targets),
        )

    async def open(self, url: str) -> BrowserPageState:
        recovered_state = None
        try:
            await self._page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self._navigation_timeout_ms,
            )
        except Exception as exc:
            # Lazy import keeps the deterministic collector usable without
            # Playwright installed while distinguishing navigation timeouts
            # from DNS, TLS and connection failures.
            try:
                from playwright.async_api import (
                    TimeoutError as PlaywrightTimeoutError,
                )
            except ImportError:
                raise exc

            if not isinstance(exc, PlaywrightTimeoutError):
                raise
            try:
                candidate_state = await self._state()
            except Exception:
                raise exc
            requested_url = url.split("#", 1)[0].rstrip("/")
            loaded_url = candidate_state.url.split("#", 1)[0].rstrip("/")
            if requested_url != loaded_url or not candidate_state.content.strip():
                raise exc
            recovered_state = candidate_state
        poll_count = max(0, min(self._navigation_timeout_ms, 3_000) // 250)
        state = recovered_state or await self._state()
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
        if hasattr(locator, "evaluate_all"):
            snapshots = await locator.evaluate_all(
                """(elements, limit) => elements.map((element, index) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return {
                        index,
                        visible: style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && rect.width > 0 && rect.height > 0,
                        text: (element.innerText || element.textContent || ''),
                        href: element.getAttribute('href')
                    };
                }).filter((item) => item.visible).slice(0, limit)""",
                MAX_NAVIGATION_TARGETS_PER_STATE,
            )
        else:
            snapshots = []
            target_count = await locator.count()
            for index in range(target_count):
                element = locator.nth(index)
                visible = await element.is_visible()
                if not visible:
                    continue
                snapshots.append({
                    "index": index,
                    "visible": True,
                    "text": await element.inner_text(),
                    "href": await element.get_attribute("href"),
                })
                if len(snapshots) >= MAX_NAVIGATION_TARGETS_PER_STATE:
                    break
        matches = []
        for index, snapshot in enumerate(snapshots):
            if not snapshot.get("visible"):
                continue
            text = " ".join(str(snapshot.get("text") or "").split())[:500]
            href = snapshot.get("href")
            if text == target.text and (href or "") == (target.href or ""):
                matches.append(int(snapshot["index"]))
        if target.occurrence >= len(matches):
            return None
        match_index = (
            target.locator_index
            if target.locator_index in matches
            else matches[target.occurrence]
        )
        await locator.nth(match_index).click(
            timeout=self._navigation_timeout_ms
        )
        try:
            await self._page.wait_for_load_state(
                "domcontentloaded",
                timeout=min(self._navigation_timeout_ms, 3_000),
            )
        except Exception:
            pass
        # Vue/React SPA 路由切换后内容是异步渲染的，需要等到 content 稳定
        # （连续两次 content 不再变化）才返回，避免拿到空白或半加载页面。
        poll_count = max(0, min(self._navigation_timeout_ms, 3_000) // 250)
        state = await self._state()
        if state.fingerprint != previous_state.fingerprint:
            # fingerprint 已变，但 SPA 内容可能还在加载；额外等待内容稳定。
            for _attempt in range(poll_count):
                await self._page.wait_for_timeout(250)
                next_state = await self._state()
                if next_state.fingerprint == state.fingerprint:
                    break
                state = next_state
            return state
        for _attempt in range(poll_count):
            await self._page.wait_for_timeout(250)
            state = await self._state()
            if state.fingerprint != previous_state.fingerprint:
                break
        if state.fingerprint == previous_state.fingerprint:
            return None
        # 内容稳定检查
        for _attempt in range(poll_count):
            await self._page.wait_for_timeout(250)
            next_state = await self._state()
            if next_state.fingerprint == state.fingerprint:
                break
            state = next_state
        return state


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
    audit_callback: Callable[..., None] | None = None,
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
                audit_callback=audit_callback,
            )
        finally:
            await browser.close()
