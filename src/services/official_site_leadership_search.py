"""LLM 逐层导航搜索协会领导页（秘书长姓名第一优先来源）。

背景：1 万家协会官网对「领导页」有几百种叫法，硬编码词表永远列不完。
本模块把当前页面的导航栏列表交给 LLM 判断「哪些链接最可能通向领导/
组织机构/负责人页面」，逐层点击（最多 4 层、10 次点击、300 秒），页面
内容出现领导标记（秘书长/秘书处/领导班子/理事长…）即收录为领导页，
交给上层 _extract_leadership 提取姓名。

与确定性采集的分工：collect_official_pages_with_playwright（词表驱动）
先跑；只有官网侧没拿到秘书长姓名时才启动本搜索（纠正文心的错值）。
"""

from __future__ import annotations

import asyncio
import json
import re
from time import monotonic
from typing import Callable

from src.services.association_profile_extractor import (
    VerifiedOfficialPage,
    _parse_json_object,
)
from src.services.official_site_browser_collector import (
    BrowserNavigationDriver,
    BrowserPageState,
    UNSAFE_NAVIGATION_ACTION_TERMS,
    _guard_main_frame_navigation,
)
from src.services.official_site_page_collector import _normalized_same_domain_url


# 判定「这一页是领导页」的强标记。会长/负责人/理事/理事长/秘书处单独出现
# 太常见（首页新闻标题「理事长出席…」、页脚「秘书处联系方式」都曾把普通页
# 误判成领导页并占满收录名额，真机教训），只作为弱标记。
LEADERSHIP_STRONG_MARKERS = (
    "秘书长",
    "领导班子",
    "驻会",
    "现任领导",
)
LEADERSHIP_WEAK_MARKERS = ("会长", "负责人", "理事", "理事长", "秘书处")

MAX_DEPTH = 4                # 最多点 4 层（入口→关于→概况→现任领导）
# 全程最多点击次数。真机教训（焊接协会）：「关于协会」悬浮菜单里 发展历程
# 排 LLM 候选第 3 位，每节点只点前 2 个永远轮不到它（秘书长只在发展历程）。
# 预算 8→10 且每节点取前 3 候选；点回已访问页会被 fingerprint 快速跳过。
MAX_TOTAL_CLICKS = 10
# 最多收录 5 个领导页交给提取器。真机教训（人口学会）：强标记密度并列时按
# 插入序取前 3，会把「秘书长在第 3 页」的翻页页挤出名额外；交给提取器的
# 页越多，翻页链尾部的名单页越不会丢（5 页 × ~3K 字 ≈ 提取成本可控）。
MAX_FOUND_PAGES = 5
MAX_PAGINATION_FLIPS = 5     # 命中领导页后最多翻 5 页（真机：人口学会现任领导第 3 页才是秘书长）
SEARCH_DEADLINE_SECONDS = 300.0  # 思考型 LLM（deepseek-v4-pro）单次 nav-pick 20~95s，
# 120s 只够 2-3 次选航就被掐死（真机：人口学会第 3 层点击全部被 deadline 丢弃）；
MAX_LLM_NAV_TARGETS = 60     # 送 LLM 的导航条目上限（防 token 爆炸）


def _strong_marker_count(content: str) -> int:
    if not content:
        return 0
    return sum(marker in content for marker in LEADERSHIP_STRONG_MARKERS)


# 秘书长-姓名绑定模式：名单页的标志性表述（「秘书长为李连胜」「秘书长：王建琪」，
# 以及职务行+姓名行分离的「秘书长\n孙建林（兼）」，真机：冶金教育学会）。
# 新闻标题只出现「秘书长工作会议」这类无绑定用法（真机：焊接协会首页新闻
# 秘书长×3 把真名单页挤到榜尾），绑定计数是比裸词计数锐利得多的排序信号。
_SECRETARY_BINDING_PATTERNS = (
    re.compile(r"秘书长[为：:]\s*[一-龥·]{2,4}"),
    re.compile(r"秘书长[ \t]*\n+[ \t]*[一-龥·（(][一-龥·()（）]{1,7}"),
)


def _secretary_binding_count(content: str) -> int:
    if not content:
        return 0
    return sum(len(pattern.findall(content)) for pattern in _SECRETARY_BINDING_PATTERNS)


# 名单页内容长度门槛：侧栏/导航文字本身就含「现任领导」等强标记，纯导航壳
# 页面（真机：人口学会关于页壳 235 字）会误判；真实名单页正文都较长。
MIN_LEADERSHIP_CONTENT_CHARS = 300


def looks_like_leadership_page(content: str) -> bool:
    """内容含强领导标记（秘书长/秘书处/领导班子/驻会/现任领导）即视为领导页。

    弱标记需 ≥2 个不同标记同时出现；子串重叠的标记去重计数（「理事」是
    「理事长」的子串，理事长单独出现不能算 2 个弱命中——真机教训：首页
    新闻标题「理事长出席…」曾把首页误判成领导页）。
    导航壳页面（正文 < 300 字，标记只来自侧栏菜单文字）不算。
    """
    if not content or len(content.strip()) < MIN_LEADERSHIP_CONTENT_CHARS:
        return False
    if any(marker in content for marker in LEADERSHIP_STRONG_MARKERS):
        return True
    matched_weak = [
        marker
        for marker in LEADERSHIP_WEAK_MARKERS
        if marker in content
    ]
    # 剔除作为其他已命中标记子串的标记
    distinct = [
        marker
        for marker in matched_weak
        if not any(
            marker != other and marker in other for other in matched_weak
        )
    ]
    return len(distinct) >= 2


def _is_safe_target(text: str) -> bool:
    target_text = text.lower()
    return not any(
        (
            term in target_text
            if not term.isascii()
            else re.search(
                rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])",
                target_text,
            )
        )
        for term in UNSAFE_NAVIGATION_ACTION_TERMS
    )


def _deduped_nav_targets(
    state: BrowserPageState, base_url: str, verified_domain: str
):
    """当前页的安全、去重导航目标（按 text 去重，href 保留首个）。

    跨域 href 直接剔除（真机教训：焊接协会首页 40+ 广告外链「查看详情」
    挤占候选列表，LLM 挑中后点击必被 route guard 拦截，浪费点击预算）。
    无 href（JS 路由）与同域/相对 href 保留。
    """
    seen: set[str] = set()
    targets = []
    for target in state.navigation_targets:
        if not _is_safe_target(target.text):
            continue
        if (
            target.href
            and not target.href.lower().startswith(("javascript:", "#", "void("))
            and _normalized_same_domain_url(
                target.href, base_url, verified_domain
            )
            is None
        ):
            continue
        if target.text in seen:
            continue
        seen.add(target.text)
        targets.append(target)
        if len(targets) >= MAX_LLM_NAV_TARGETS:
            break
    return targets


async def _llm_pick_navigation(
    gateway,
    association_name: str,
    page_title: str,
    targets,
) -> list[int]:
    """让 LLM 从导航列表里挑最可能通向领导页的链接，返回目标下标（按可能性排序）。"""
    lines = [
        f"{index}|{target.text[:50]}|{target.href or ''}"
        for index, target in enumerate(targets)
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "你在浏览一个协会/学会官网，目标是找到「协会领导/组织机构/"
                "负责人」页面（里面会有现任秘书长等负责人姓名）。"
                "下面给出当前页面的可选导航链接，判断哪些最可能逐步通向该页面。"
                "优先选文字含「领导/理事/秘书/组织/机构/关于/概况/简介/章程/"
                "负责人/历程/大事记/沿革」的链接——「发展历程/大事记」类页面"
                "常载换届记录，里面会有「选举XX为会长、秘书长为XX」的表述"
                "（真机：焊接协会秘书长只在发展历程页）；"
                "跳过新闻、通知、动态、下载、登录、业务办理类。"
                '只输出严格JSON：{"click":[链接序号,...]}，最多3个、按可能性从高到低；'
                '没有合适的输出 {"click":[]}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"协会：{association_name}\n当前页面：{page_title}\n"
                "可选导航（序号|文字|链接）：\n" + "\n".join(lines)
            ),
        },
    ]
    try:
        # max_tokens 必须给足：deepseek-v4-pro 带思考链，300 上限会被思考
        # 烧穿导致 content 为空（真机：60 目标列表时 300/800 都打满、空输出），
        # 逐层导航静默退化到词表兜底。按实际生成量计费，上限只是保险丝。
        response = await gateway.chat(
            messages=messages, temperature=0, max_tokens=4000
        )
        content = response.get("content") if isinstance(response, dict) else None
        if not isinstance(content, str):
            return []
        parsed = _parse_json_object(content)
        picks = parsed.get("click") if isinstance(parsed, dict) else None
        if not isinstance(picks, list):
            return []
        valid = []
        for pick in picks:
            if isinstance(pick, bool):
                continue
            try:
                index = int(pick)
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(targets) and index not in valid:
                valid.append(index)
        return valid[:3]
    except Exception:
        return []


async def search_leadership_pages_with_driver(
    entry_url: str,
    verified_domain: str,
    driver: BrowserNavigationDriver,
    gateway,
    *,
    association_name: str = "",
    max_depth: int = MAX_DEPTH,
    max_total_clicks: int = MAX_TOTAL_CLICKS,
    deadline_seconds: float = SEARCH_DEADLINE_SECONDS,
    audit_callback: Callable[..., None] | None = None,
) -> list[VerifiedOfficialPage]:
    """从官网入口逐层点击寻找领导页，返回内容含领导标记的页面列表。"""
    if max_depth < 1 or max_total_clicks < 1 or deadline_seconds < 1:
        raise ValueError("invalid leadership search limits")

    def audit(**event) -> None:
        if audit_callback is None:
            return
        try:
            audit_callback(**event)
        except Exception:
            pass

    deadline = monotonic() + deadline_seconds
    entry_state = await asyncio.wait_for(
        driver.open(entry_url), timeout=max(0.001, deadline - monotonic())
    )
    # 入口页本身就是领导页（小型官网首页直接挂领导班子）
    found: list[VerifiedOfficialPage] = []
    visited = {entry_state.fingerprint}
    # 浏览器当前所在页面。BFS 出队顺序与浏览器实际位置会失配：同一轮的
    # 第 2 个候选点击、或兄弟分支的点击会把浏览器带到别的页，而 activate
    # 是在「当前 DOM」上按 text+href 匹配元素——在错误的页上匹配不到目标
    # （如侧栏子项）会静默返回 None。真机教训（人口学会）：8 次点击预算
    # 全部耗在这种失配上，现任领导页明明可达却从未被点击。
    browser_state: BrowserPageState | None = entry_state

    def try_include(state: BrowserPageState) -> bool:
        normalized = _normalized_same_domain_url(
            state.url, entry_url, verified_domain
        )
        if normalized is None or not state.content.strip():
            return False
        if looks_like_leadership_page(state.content):
            found.append(
                VerifiedOfficialPage(
                    url=normalized,
                    title=state.title.strip() or normalized,
                    content=state.content.strip(),
                    verified_official=True,
                )
            )
            return True
        return False

    try_include(entry_state)

    async def paginate_hunt(state: BrowserPageState, depth: int) -> None:
        """命中领导页后翻页收录后续名单页（秘书长可能在任意一页）。

        driver 不支持 flip_next（测试假 driver/无分页站点）时静默跳过。
        """
        nonlocal browser_state

        flip = getattr(driver, "flip_next", None)
        if flip is None:
            return
        pager_state = state
        for _flip_index in range(MAX_PAGINATION_FLIPS):
            if monotonic() >= deadline:
                audit(
                    stage="领导页搜索",
                    kind="leadership_flip_stop",
                    summary=f"第{_flip_index + 1}次翻页前到时限",
                    detail={"url": pager_state.url},
                )
                return
            try:
                flipped = await asyncio.wait_for(
                    flip(pager_state), timeout=max(0.001, deadline - monotonic())
                )
            except Exception as exc:
                audit(
                    stage="领导页搜索",
                    kind="leadership_flip_stop",
                    summary=f"第{_flip_index + 1}次翻页异常 {type(exc).__name__}",
                    detail={"url": pager_state.url},
                )
                return
            if flipped is None or flipped.fingerprint == pager_state.fingerprint:
                audit(
                    stage="领导页搜索",
                    kind="leadership_flip_stop",
                    summary=(
                        f"第{_flip_index + 1}次翻页无变化/无控件"
                        f"（已翻{_flip_index}页）"
                    ),
                    detail={"url": pager_state.url},
                )
                return
            pager_state = flipped
            browser_state = pager_state
            if try_include(flipped):
                audit(
                    stage="领导页搜索",
                    kind="leadership_page_found",
                    summary=f"第{depth}层翻页命中：{flipped.title.strip()[:40]}",
                    detail={"url": flipped.url, "depth": depth, "paged": True},
                )

    # 首页命中时也翻页（小站首页即名单 + 分页）
    if found:
        await paginate_hunt(entry_state, 0)

    frontier: list[tuple[BrowserPageState, int]] = [(entry_state, 0)]
    clicks = 0

    async def reposition(state: BrowserPageState) -> BrowserPageState | None:
        """浏览器不在 state 页时重新 open 该页（词表采集「重放官网入口」同款）。

        在位时零开销（同一对象直接返回）；失配时多一次导航，换取 activate
        在正确的 DOM 上匹配目标元素。
        """
        nonlocal browser_state
        if browser_state is state:
            return state
        try:
            fresh = await asyncio.wait_for(
                driver.open(state.url),
                timeout=max(0.001, deadline - monotonic()),
            )
        except Exception:
            return None
        browser_state = fresh
        return fresh

    # 注意：停止条件不含「已找到 N 页」——真机教训（焊接协会）：首页新闻标题
    # 含「秘书长」的垃圾页先填满名额会中断搜索，真正的「关于协会→组织架构」
    # 悬浮菜单没机会被点。靠点击预算/层数/截止时间收口，返回时按强标记
    # 密度排序取前 MAX_FOUND_PAGES 页（真名单页密度远高于首页）。
    while (
        frontier
        and clicks < max_total_clicks
        and monotonic() < deadline
    ):
        current, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        targets = _deduped_nav_targets(current, current.url, verified_domain)
        if not targets:
            audit(
                stage="领导页搜索",
                kind="nav_probe",
                summary=f"「{current.title.strip()[:20]}」无可选导航",
            )
            continue
        picks = await _llm_pick_navigation(
            gateway, association_name, current.title, targets
        )
        pick_source = "llm"
        if not picks:
            # LLM 空选（长尾词表外/输出异常）时，用确定性词表权重兜底：
            # 取优先级最高的前 2 个导航目标，保证「关于我们→学会概况→现任领导」
            # 这类词表可命中的路径不因 LLM 抖动而中断。
            from src.services.official_site_browser_collector import (
                _target_business_priority,
            )

            ranked = sorted(
                range(len(targets)),
                key=lambda i: _target_business_priority(targets[i]),
                reverse=True,
            )
            picks = [
                i for i in ranked[:2] if _target_business_priority(targets[i]) > 0
            ]
            pick_source = "deterministic" if picks else "none"
        audit(
            stage="领导页搜索",
            kind="nav_probe",
            summary=(
                f"「{current.title.strip()[:20]}」 targets={len(targets)} "
                f"picks={picks} via={pick_source}"
            ),
        )
        if not picks:
            continue
        # 每节点点前 3 个候选（真机：焊接协会发展历程排第 3、点前 2 轮不到）
        for index in picks[:3]:
            if clicks >= max_total_clicks or monotonic() >= deadline:
                break
            target = targets[index]
            clicks += 1
            # 带 href 的目标直接 open URL（悬浮菜单子项不可见无法点击，
            # 但 URL 直开不受可见性限制，真机：冶金教育学会/人口学会）
            direct_url = None
            if target.href and not target.href.lower().startswith(
                ("javascript:", "void(")
            ) and "#" not in target.href:
                direct_url = _normalized_same_domain_url(
                    target.href, current.url, verified_domain
                )
            if direct_url is None:
                # activate 在「当前 DOM」上按 text+href 匹配元素；BFS 出队
                # 的页或同轮第 1 个候选的点击可能已把浏览器带到别处，在错误
                # 的页上匹配不到目标（如侧栏子项）会静默浪费点击预算——
                # 真机教训（人口学会）：8 次点击全耗光、现任领导页从未被点。
                # 先回位（词表采集「重放官网入口」同款）再点击。
                current = await reposition(current)
                if current is None:
                    break
            try:
                operation = (
                    driver.open(direct_url)
                    if direct_url is not None
                    else driver.activate(target, current)
                )
                next_state = await asyncio.wait_for(
                    operation,
                    timeout=max(0.001, deadline - monotonic()),
                )
            except Exception:
                continue
            if next_state is None or next_state.fingerprint in visited:
                continue
            visited.add(next_state.fingerprint)
            if (
                _normalized_same_domain_url(
                    next_state.url, entry_url, verified_domain
                )
                is None
            ):
                # 跨域跳转被 route guard 拦截时 URL 不变；仍校验一次域名
                continue
            browser_state = next_state
            if try_include(next_state):
                audit(
                    stage="领导页搜索",
                    kind="leadership_page_found",
                    summary=f"第{depth + 1}层命中：{next_state.title.strip()[:50]}",
                    detail={"url": next_state.url, "depth": depth + 1},
                )
                await paginate_hunt(next_state, depth + 1)
            # 无论是否命中都继续下钻——真机教训（焊接协会）：点「关于协会」
            # 悬浮菜单展开的状态本身含「秘书长」被收录，但真正的「组织架构」
            # 子项在它的下一层，命中即停就永远点不到。
            frontier.append((next_state, depth + 1))

    audit(
        stage="领导页搜索",
        kind="leadership_search_done",
        summary=f"点击{clicks}次，找到{len(found)}个领导页",
    )
    # 排序：秘书长-姓名绑定（「秘书长为/：X」）最优先——新闻页裸词计数
    # （标题「秘书长工作会议」×3）会压过真名单页（绑定×1）；其次裸词计数
    # 与泛强标记密度（真机：人口学会侧栏标记并列、焊接协会新闻标题刷词）。
    ranked = sorted(
        found,
        key=lambda page: (
            _secretary_binding_count(page.content),
            page.content.count("秘书长"),
            _strong_marker_count(page.content),
        ),
        reverse=True,
    )
    return ranked[:MAX_FOUND_PAGES]


async def search_leadership_pages_with_playwright(
    entry_url: str,
    verified_domain: str,
    gateway,
    *,
    association_name: str = "",
    navigation_timeout_ms: int = 10_000,
    audit_callback: Callable[..., None] | None = None,
) -> list[VerifiedOfficialPage]:
    """Playwright 实现：复用常驻 CDP 浏览器(9222)，跨域导航由 route guard 拦截。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
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
            from src.services.official_site_browser_collector import (
                PlaywrightNavigationDriver,
            )

            driver = PlaywrightNavigationDriver(
                page, navigation_timeout_ms=navigation_timeout_ms
            )
            return await search_leadership_pages_with_driver(
                entry_url,
                verified_domain,
                driver,
                gateway,
                association_name=association_name,
                audit_callback=audit_callback,
            )
        finally:
            await browser.close()
