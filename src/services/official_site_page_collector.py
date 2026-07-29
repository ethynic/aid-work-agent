"""从已验证官网页面确定性发现并采集高价值内页。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import re
from typing import Literal
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

from pydantic import Field, HttpUrl

from src.services.association_profile_extractor import (
    MAX_PAGE_COUNT,
    MAX_TOTAL_CONTENT_CHARS,
    StrictModel,
    VerifiedOfficialPage,
)


DEFAULT_MAX_PAGES = 12
HIGH_VALUE_LINK_TERMS = (
    "协会简介",
    "协会介绍",
    "协会概况",
    "关于协会",
    "组织领导",
    "驻会领导",
    "领导集体",
    "领导班子",
    "领导机构",
    "协会领导",
    "组织架构",
    "组织机构",
    "机构设置",
    "秘书处",
    "分支机构",
    "专业委员会",
    "联系我们",
    "联系方式",
    "会员",
    "about",
    "profile",
    "introduction",
    "organization",
    "organisational",
    "organizational",
    "leadership",
    "leader",
    "contact",
    "member",
    "members",
    "membership",
    "branch",
    "committee",
)
UNSAFE_LINK_ACTION_TERMS = (
    "登录", "注册", "退出", "删除", "提交", "报名", "支付", "下载",
    "login", "register", "logout", "delete", "submit", "pay", "download",
)


class OfficialPageLink(StrictModel):
    url: str = Field(min_length=1, max_length=2_000)
    text: str = Field(default="", max_length=500)


class OfficialPageSnapshot(StrictModel):
    url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=200_000)
    links: list[OfficialPageLink] = Field(default_factory=list, max_length=2_000)
    verified_official: Literal[True]


PageFetcher = Callable[[str], Awaitable[OfficialPageSnapshot]]


def _normalized_same_domain_url(raw_url: str, base_url: str, verified_domain: str) -> str | None:
    absolute_url = urljoin(base_url, raw_url.strip())
    absolute_url, _fragment = urldefrag(absolute_url)
    parsed = urlparse(absolute_url)
    verified_host = urlparse(
        verified_domain if "://" in verified_domain else f"https://{verified_domain}"
    ).hostname
    verified_parsed = urlparse(
        verified_domain if "://" in verified_domain else f"https://{verified_domain}"
    )
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or not verified_host
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    host = parsed.hostname.lower().rstrip(".")
    verified_host = verified_host.lower().rstrip(".")
    if host != verified_host and not host.endswith(f".{verified_host}"):
        return None
    normalized_path = parsed.path or "/"
    try:
        port = parsed.port
        verified_port = verified_parsed.port
    except ValueError:
        return None
    if verified_port is None:
        if (
            (parsed.scheme.lower() == "http" and port not in {None, 80})
            or (parsed.scheme.lower() == "https" and port not in {None, 443})
        ):
            return None
    elif port != verified_port:
        return None
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    normalized_netloc = host if port is None or default_port else f"{host}:{port}"
    return urlunparse(
        (
            parsed.scheme.lower(),
            normalized_netloc,
            normalized_path,
            "",
            parsed.query,
            "",
        )
    )


def _link_priority(link: OfficialPageLink, normalized_url: str) -> int:
    haystack = f"{link.text} {urlparse(normalized_url).path}".lower()
    if any(term in haystack for term in UNSAFE_LINK_ACTION_TERMS):
        return 0
    return sum(
        1
        for term in HIGH_VALUE_LINK_TERMS
        if (
            term.lower() in haystack
            if not term.isascii()
            else re.search(
                rf"(?<![a-z0-9_]){re.escape(term.lower())}(?![a-z0-9_])",
                haystack,
            )
        )
    )


def is_high_value_official_navigation(text: str, target: str = "") -> bool:
    """判断导航文本或目标是否属于协会资料高价值栏目。"""
    return _link_priority(
        OfficialPageLink(url=target or "/", text=text),
        target or "/",
    ) > 0


def discover_high_value_official_links(
    pages: list[OfficialPageSnapshot],
    verified_domain: str,
    *,
    exclude_urls: set[str] | None = None,
) -> list[str]:
    """按栏目语义排序，返回去重后的同域高价值 URL。"""
    excluded = set(exclude_urls or ())
    candidates: dict[str, tuple[int, int]] = {}
    sequence = 0
    for page in pages:
        page_url = str(page.url)
        for link in page.links:
            normalized = _normalized_same_domain_url(link.url, page_url, verified_domain)
            if normalized is None or normalized in excluded:
                continue
            priority = _link_priority(link, normalized)
            if priority <= 0:
                continue
            previous = candidates.get(normalized)
            if previous is None or priority > previous[0]:
                candidates[normalized] = (priority, sequence)
            sequence += 1
    return [
        url
        for url, _score in sorted(
            candidates.items(),
            key=lambda item: (-item[1][0], item[1][1], item[0]),
        )
    ]


async def collect_high_value_official_pages(
    seed_pages: list[OfficialPageSnapshot],
    verified_domain: str,
    page_fetcher: PageFetcher,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_total_chars: int = MAX_TOTAL_CONTENT_CHARS,
    max_fetches: int | None = None,
) -> list[VerifiedOfficialPage]:
    """采集高价值内页，完整保留纳入预算的页面正文，不做逐页截断。"""
    if (
        not seed_pages
        or max_pages < 1
        or max_pages > MAX_PAGE_COUNT
        or max_total_chars < 1
        or max_total_chars > MAX_TOTAL_CONTENT_CHARS
    ):
        raise ValueError("invalid collector input or limits")
    if max_fetches is None:
        max_fetches = max_pages * 3
    if max_fetches < 0:
        raise ValueError("invalid fetch limit")

    collected: list[VerifiedOfficialPage] = []
    snapshots_to_scan: list[OfficialPageSnapshot] = []
    seen_urls: set[str] = set()
    total_chars = 0

    def include(
        snapshot: OfficialPageSnapshot, *, scan_when_over_budget: bool = False
    ) -> bool:
        nonlocal total_chars
        normalized = _normalized_same_domain_url(
            str(snapshot.url), str(snapshot.url), verified_domain
        )
        if normalized is None or normalized in seen_urls:
            return False
        seen_urls.add(normalized)
        if (
            len(collected) >= max_pages
            or total_chars + len(snapshot.content) > max_total_chars
        ):
            if scan_when_over_budget:
                snapshots_to_scan.append(snapshot)
            return False
        collected.append(
            VerifiedOfficialPage(
                url=normalized,
                title=snapshot.title,
                content=snapshot.content,
                verified_official=True,
            )
        )
        snapshots_to_scan.append(snapshot)
        total_chars += len(snapshot.content)
        return True

    for seed in seed_pages:
        include(seed, scan_when_over_budget=True)

    scanned_count = 0
    queued_urls: set[str] = set()
    queue: list[str] = []
    fetch_count = 0
    while len(collected) < max_pages and fetch_count < max_fetches:
        newly_scannable = snapshots_to_scan[scanned_count:]
        scanned_count = len(snapshots_to_scan)
        for url in discover_high_value_official_links(
            newly_scannable,
            verified_domain,
            exclude_urls=seen_urls | queued_urls,
        ):
            queued_urls.add(url)
            queue.append(url)
        if not queue:
            break

        target_url = queue.pop(0)
        if target_url in seen_urls:
            continue
        fetch_count += 1
        snapshot = await page_fetcher(target_url)
        normalized_fetched_url = _normalized_same_domain_url(
            str(snapshot.url), target_url, verified_domain
        )
        if normalized_fetched_url is None:
            continue
        include(snapshot)

    return collected
