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
    "[class*='menu'] li, [class*='nav'] li, "
    "[class*='nav'] div, [class*='nav'] span, "
    "[class*='menu'] div, [class*='menu'] span, "
    "[class*='bar'] div, [class*='bar'] span, "
    "[class*='side'] div, [class*='side'] span, [class*='tab']"
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
    # 新闻/通知类噪声零分（同 page_collector.NEWS_NOISE_TERMS）——真机教训：
    # 「…相关负责人答记者问」新闻标题会拿满领导词权重，挤占采集预算。
    if any(
        (
            term in haystack
            if not term.isascii()
            else re.search(
                rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])",
                haystack,
            )
        )
        for term in (
            "通知", "新闻", "动态", "公告", "报道", "资讯", "快讯", "会议",
            "活动", "专题", "答记者问", "声明", "公示", "征集", "申报",
            "要闻", "媒体", "news", "notice", "announcement", "event", "press",
        )
    ):
        return 0
    # 2026-08 扩充：领导页是秘书长姓名第一优先来源，领导类词全部提到 120/105，
    # 学会镜像 + 机构泛化词 + 裸词兜底（与 HIGH_VALUE_LINK_TERMS 同步演进）。
    weighted_terms = (
        (120, ("秘书长", "现任领导", "学会领导", "领导简介", "现任负责人",
               "驻会负责人", "主要负责人", "负责人",
               "组织领导", "驻会领导", "领导集体", "领导班子", "领导机构",
               "协会领导", "leadership", "leader")),
        (105, ("理事长", "理事会", "会长", "秘书处", "秘书局",
               "办事机构", "职能部门", "内设机构", "部门设置", "工作机构",
               "机构设置", "协会设置", "学会设置", "组织架构", "组织机构",
               "organization", "organisational", "organizational",
               "secretariat", "council", "governance")),
        (85, ("协会简介", "协会介绍", "协会概况", "关于协会",
              "学会简介", "学会介绍", "学会概况", "关于学会",
              "关于我们", "关于",
              "章程", "概况", "简介",
              "about", "profile", "introduction", "overview")),
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
            # 单步路径且有同域 href 时，直接访问目标页面，避免每次重新加载首页。
            # 这显著减少了慢网站的加载时间和超时失败。
            direct_first = (
                len(path) == 1
                and path[0].href
                and not path[0].href.lower().startswith(
                    ("javascript:", "void(")
                )
                and "#" not in path[0].href
            )
            if direct_first:
                direct_entry = _normalized_same_domain_url(
                    path[0].href, normalized_entry, verified_domain
                )
            else:
                direct_entry = normalized_entry
            audit(
                stage="官网采集",
                kind="web_open",
                summary="重放官网入口",
                detail={"url": direct_entry},
            )
            state = await asyncio.wait_for(
                driver.open(direct_entry),
                timeout=max(0.001, path_deadline - monotonic()),
            )
            # 单步路径已直接打开目标页面，跳过 activate 循环直接进入 include。
            if direct_first:
                if state.fingerprint in visited_states:
                    continue
                visited_states.add(state.fingerprint)
                include(state)
                enqueue_from(state, path)
                continue
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

    # SPA 加载占位文案（出现在正文开头时视为页面未渲染完成，继续轮询）
    _LOADING_MARKERS = ("正在加载", "加载中", "请稍候", "载入中", "loading…", "loading...")

    @classmethod
    def _is_loading_placeholder(cls, content: str) -> bool:
        head = (content or "").strip()[:200].lower()
        return any(marker in head for marker in cls._LOADING_MARKERS)

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
            # 可见性策略：可见元素全收；不可见元素只收带 href 的锚点——
            # 悬浮菜单（hover 展开）的子项 display:none 但 DOM 里有 <a href>，
            # 带 href 的目标可走 URL 直开不需要点击可见（真机教训：冶金教育
            # 学会/人口学会官网导航全藏在悬浮菜单，targets=0/1 导致搜索空转）。
            # 可见目标排序在前再截断，隐藏锚点永不挤掉可见目标。
            snapshots = await locator.evaluate_all(
                """(elements, limit) => elements.map((element, index) => {
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    const visible = style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && rect.width > 0 && rect.height > 0;
                    // div/span 导航项必须是叶子（无子元素）——避开 nav 容器的
                    // 包装层；a/li 豁免（折叠子菜单的 li 含子 span 仍要捕获）。
                    const leafOk = element.tagName === 'A'
                        || element.tagName === 'LI'
                        || element.children.length === 0;
                    return {
                        index,
                        visible,
                        leafOk,
                        text: (element.innerText || element.textContent || ''),
                        href: element.getAttribute('href')
                    };
                }).filter((item) => item.leafOk
                    && (item.visible || (item.href && item.text.trim())))
                  .sort((a, b) => (b.visible ? 1 : 0) - (a.visible ? 1 : 0))
                  .slice(0, limit)""",
                MAX_NAVIGATION_TARGETS_PER_STATE,
            )
        else:
            visible_snapshots = []
            hidden_snapshots = []
            target_count = await locator.count()
            for index in range(target_count):
                element = locator.nth(index)
                visible = await element.is_visible()
                href = await element.get_attribute("href")
                if not visible and not href:
                    continue
                snapshot = {
                    "index": index,
                    "visible": visible,
                    "text": await element.inner_text(),
                    "href": href,
                }
                (visible_snapshots if visible else hidden_snapshots).append(snapshot)
                if (
                    len(visible_snapshots) >= MAX_NAVIGATION_TARGETS_PER_STATE
                    and len(hidden_snapshots) >= MAX_NAVIGATION_TARGETS_PER_STATE
                ):
                    break
            snapshots = (
                visible_snapshots[:MAX_NAVIGATION_TARGETS_PER_STATE]
                + hidden_snapshots[:MAX_NAVIGATION_TARGETS_PER_STATE]
            )
        for snapshot in snapshots:
            href = snapshot.get("href")
            href = str(href) if href is not None else None
            if not snapshot.get("visible") and not href:
                # 无 href 的不可见元素（纯按钮/li）无法直开也无法点击，跳过
                continue
            text = " ".join(str(snapshot.get("text") or "").split())[:500]
            if not text:
                continue
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
        poll_count = max(0, min(self._navigation_timeout_ms, 9_000) // 250)
        state = recovered_state or await self._state()
        for _attempt in range(poll_count):
            # 慢 SPA 等待窗口 9s；内容仍是加载占位（真机：冶金教育学会
            # 「正在加载系统资源」占位 + 1 个备案链接，菜单 8s 才渲染）时
            # 继续等菜单渲染
            if (
                state.content.strip()
                and state.navigation_targets
                and not self._is_loading_placeholder(state.content)
            ):
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
        # 点击被加载遮罩拦截时（真机：cpaw.org.cn 的 el-loading-mask 拦截
        # pointer events 10s 超时）改用 force 直发事件重试一次
        try:
            await locator.nth(match_index).click(
                timeout=min(self._navigation_timeout_ms, 5_000)
            )
        except Exception:
            await locator.nth(match_index).click(
                timeout=min(self._navigation_timeout_ms, 3_000), force=True
            )
        # 等加载遮罩消失（Element UI 的 el-loading-mask 覆盖期间内容未渲染）
        for mask_selector in (".el-loading-mask", "[class*='loading-mask']"):
            try:
                await self._page.wait_for_selector(
                    mask_selector, state="hidden",
                    timeout=min(self._navigation_timeout_ms, 5_000),
                )
            except Exception:
                pass
        try:
            await self._page.wait_for_load_state(
                "domcontentloaded",
                timeout=min(self._navigation_timeout_ms, 6_000),
            )
        except Exception:
            pass
        # Vue/React SPA 路由切换后内容是异步渲染的，需要等到 content 稳定
        # （连续两次 content 不再变化）才返回，避免拿到空白或半加载页面。
        poll_count = max(0, min(self._navigation_timeout_ms, 6_000) // 250)
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

    # 分页控件文本：精确匹配这些短文本的可见元素视为「下一页」按钮
    _PAGER_NEXT_TEXTS = ("下一页", "下页", "›", "»", ">", "next")

    async def flip_next(self, previous_state: BrowserPageState) -> BrowserPageState | None:
        """点击分页控件的「下一页」（领导名单分页，真机：人口学会现任领导
        第 3 页才是秘书长）。无分页控件、点击无效或已到末页返回 None。
        """
        try:
            clicked = await self._page.evaluate(
                """(texts) => {
                    const els = document.querySelectorAll(
                        'button, a, li, span, div, i'
                    );
                    for (const element of els) {
                        const style = window.getComputedStyle(element);
                        const rect = element.getBoundingClientRect();
                        if (style.visibility === 'hidden'
                            || style.display === 'none'
                            || rect.width <= 0 || rect.height <= 0) continue;
                        const cls = String(element.className || '');
                        // 类名匹配不要求叶子——Element UI 的 btn-next 内含 <i>
                        // 图标子元素、无文字，真机（人口学会翻页）曾因此漏点
                        if (/btn-next/.test(cls)
                            || /pagination[^ ]*next/i.test(cls)
                            || /(?:^|\\s)next(?:\\s|$)/i.test(cls)) {
                            element.click();
                            return true;
                        }
                        if (element.children.length > 0) continue;
                        const text = (element.innerText || '').trim();
                        if (texts.includes(text) && text.length <= 4) {
                            element.click();
                            return true;
                        }
                    }
                    return false;
                }""",
                list(self._PAGER_NEXT_TEXTS),
            )
        except Exception:
            return None
        if not clicked:
            return None
        await self._page.wait_for_timeout(800)
        # 等加载遮罩消失（与 activate 同款；慢 SPA 点击后遮罩覆盖期间内容未渲染）
        for mask_selector in (".el-loading-mask", "[class*='loading-mask']"):
            try:
                await self._page.wait_for_selector(
                    mask_selector, state="hidden",
                    timeout=min(self._navigation_timeout_ms, 5_000),
                )
            except Exception:
                pass
        state = await self._state()
        # 先等「内容开始变化」——慢 SPA 点击后渲染延迟 2~3s，若直接进入稳定
        # 轮询会把「尚未变化」误判为「已稳定」而放弃翻页（真机：人口学会
        # 现任领导第 2→3 页，庄亚儿在第 3 页曾被这样丢掉）
        change_polls = max(0, min(self._navigation_timeout_ms, 4_000) // 250)
        for _attempt in range(change_polls):
            if state.fingerprint != previous_state.fingerprint:
                break
            await self._page.wait_for_timeout(250)
            state = await self._state()
        if state.fingerprint == previous_state.fingerprint:
            return None
        # 再等内容稳定（防半加载）
        poll_count = max(0, min(self._navigation_timeout_ms, 3_000) // 250)
        for _attempt in range(poll_count):
            await self._page.wait_for_timeout(250)
            next_state = await self._state()
            if next_state.fingerprint == state.fingerprint:
                break
            state = next_state
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
    audit_callback: Callable[..., None] | None = None,
) -> list[VerifiedOfficialPage]:
    """复用常驻 CDP 浏览器(localhost:9222)采集官网，避免 launch 新 chromium。

    打包 exe 不带 chromium 二进制，``launch`` 会报 Executable doesn't exist。
    改为 ``connect_over_cdp`` 复用 ``ensure_wenxin_browser`` 拉起的有头 Chrome。
    ``headless`` 参数在 attach 模式下不生效（常驻 Chrome 为有头窗口），保留仅为兼容调用链。
    """
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
        # 复用 ensure_wenxin_browser 拉起的常驻 CDP 浏览器(localhost:9222)，
        # 避免 launch 新 chromium——打包 exe 不带 chromium 二进制会 launch 失败。
        browser = await playwright.chromium.connect_over_cdp("http://localhost:9222")
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
