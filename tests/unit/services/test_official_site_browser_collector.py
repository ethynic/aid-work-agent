import asyncio
import inspect
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.services.official_site_browser_collector as browser_collector_module
from src.services.association_profile_extractor import VerifiedOfficialPage
from src.services.official_site_browser_collector import (
    MAX_NAVIGATION_TARGETS_PER_STATE,
    NAVIGATION_SELECTOR,
    BrowserNavigationTarget,
    BrowserPageState,
    _guard_main_frame_navigation,
    collect_official_pages_with_browser_driver,
    collect_official_pages_with_playwright,
)


pytestmark = pytest.mark.unit
ENTRY = "https://www.example.org/"
DOMAIN = "www.example.org"


def target(text, href=None, occurrence=0, locator_index=None):
    return BrowserNavigationTarget(
        text=text,
        href=href,
        occurrence=occurrence,
        locator_index=locator_index,
    )


def test_locator_index_is_not_part_of_navigation_identity():
    first = target("协会领导", "javascript:void(0)", locator_index=3)
    moved = target("协会领导", "javascript:void(0)", locator_index=303)

    assert first.identity == moved.identity


def state(url, title, content, targets=()):
    return BrowserPageState(
        url=url,
        title=title,
        content=content,
        navigation_targets=tuple(targets),
    )


def test_clickable_selector_and_public_wrapper_have_no_site_specific_config():
    for selector in (
        "a",
        "button",
        "[role='button']",
        "[onclick]",
        "nav li",
        "[class*='menu'] li",
        "[class*='nav'] li",
    ):
        assert selector in NAVIGATION_SELECTOR
    signature = inspect.signature(collect_official_pages_with_playwright)
    assert signature.parameters["headless"].default is inspect.Parameter.empty
    source = inspect.getsource(collect_official_pages_with_playwright).lower()
    assert "crra" not in source
    assert "caapa" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_headless", [None, 0, 1, "false"])
async def test_playwright_wrapper_requires_explicit_boolean_headless(invalid_headless):
    with pytest.raises(ValueError, match="explicit boolean"):
        await collect_official_pages_with_playwright(
            ENTRY,
            DOMAIN,
            headless=invalid_headless,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_timeout", [0, -1, True, 1.5])
async def test_playwright_wrapper_rejects_invalid_navigation_timeout(invalid_timeout):
    with pytest.raises(ValueError, match="positive integer"):
        await collect_official_pages_with_playwright(
            ENTRY,
            DOMAIN,
            headless=True,
            navigation_timeout_ms=invalid_timeout,
        )


@pytest.mark.parametrize("invalid_timeout", [0, -1, True, 1.5])
def test_playwright_driver_rejects_invalid_navigation_timeout(invalid_timeout):
    with pytest.raises(ValueError, match="positive integer"):
        browser_collector_module.PlaywrightNavigationDriver(
            object(),
            navigation_timeout_ms=invalid_timeout,
        )


def test_page_fingerprint_detects_url_body_or_navigation_changes():
    base = state(ENTRY, "首页", "正文", ())
    assert state(f"{ENTRY}about", "首页", "正文", ()).fingerprint != base.fingerprint
    assert state(ENTRY, "首页", "另一正文", ()).fingerprint != base.fingerprint
    assert state(
        ENTRY, "首页", "正文", (target("关于协会"),)
    ).fingerprint != base.fingerprint


class FakeBrowserDriver:
    def __init__(self, states, transitions):
        self.states = states
        self.transitions = transitions
        self.current_key = "entry"
        self.open_count = 0
        self.activation_count = 0
        self.activated = []

    async def open(self, url):
        self.open_count += 1
        if url == ENTRY:
            self.current_key = "entry"
        else:
            matching_keys = [
                key for key, page_state in self.states.items()
                if page_state.url == url
            ]
            if not matching_keys:
                raise AssertionError(f"unexpected direct URL: {url}")
            self.current_key = matching_keys[0]
        return self.states[self.current_key]

    async def activate(self, navigation_target, previous_state):
        self.activation_count += 1
        self.activated.append(navigation_target.identity)
        next_key = self.transitions.get(
            (self.current_key, navigation_target.identity)
        )
        if next_key is None:
            return None
        self.current_key = next_key
        return self.states[next_key]


def js_navigation_fixture():
    about_menu = target("关于协会")
    intro = target("协会介绍")
    leaders = target("协会领导")
    structure = target("机构设置")
    contact = target("联系我们")
    members = target("会员服务", "/members")
    duplicate_intro = target("协会介绍", occurrence=1)
    external = target("协会领导", "https://evil.example/leaders")
    unchanged = target("联系我们", "javascript:void(0)", occurrence=1)
    news = target("行业新闻", "/news")
    states = {
        "entry": state(
            ENTRY,
            "协会首页",
            "协会首页正文",
            (
                about_menu,
                contact,
                members,
                external,
                unchanged,
                news,
            ),
        ),
        "about_menu": state(
            ENTRY,
            "协会首页",
            "协会首页正文",
            (about_menu, intro, duplicate_intro, leaders, structure),
        ),
        "intro": state(
            "https://www.example.org/app/about",
            "协会介绍",
            "本协会创立于2001年。",
        ),
        "leaders": state(
            "https://www.example.org/app/leaders",
            "协会领导",
            "本届负责人为李会长和刘秘书长。",
        ),
        "structure": state(
            "https://www.example.org/app/structure",
            "机构设置",
            "内部设三个工作部门。",
        ),
        "contact": state(
            "https://www.example.org/app/contact",
            "联系我们",
            "来访地点位于循环产业园。",
        ),
        "members": state(
            "https://www.example.org/members",
            "会员服务",
            "当前会员规模767家。",
        ),
    }
    transitions = {
        ("entry", about_menu.identity): "about_menu",
        ("about_menu", intro.identity): "intro",
        ("about_menu", duplicate_intro.identity): "intro",
        ("about_menu", leaders.identity): "leaders",
        ("about_menu", structure.identity): "structure",
        ("entry", contact.identity): "contact",
        ("entry", members.identity): "members",
    }
    return FakeBrowserDriver(states, transitions)


@pytest.mark.asyncio
async def test_automatically_clicks_js_navigation_and_supports_normal_href():
    driver = js_navigation_fixture()

    pages = await collect_official_pages_with_browser_driver(
        ENTRY,
        DOMAIN,
        driver,
        max_navigation_attempts=20,
    )

    urls = {str(page.url) for page in pages}
    combined_content = "\n".join(page.content for page in pages)
    assert {
        ENTRY,
        "https://www.example.org/app/about",
        "https://www.example.org/app/leaders",
        "https://www.example.org/app/structure",
        "https://www.example.org/app/contact",
        "https://www.example.org/members",
    } <= urls
    assert "本协会创立于2001年。" in combined_content
    assert "本届负责人为李会长和刘秘书长。" in combined_content
    assert "内部设三个工作部门。" in combined_content
    assert "来访地点位于循环产业园。" in combined_content
    assert all(isinstance(page, VerifiedOfficialPage) for page in pages)
    assert driver.open_count > 1


@pytest.mark.asyncio
async def test_audit_callback_keeps_urls_in_encrypted_detail_not_summary():
    events = []
    await collect_official_pages_with_browser_driver(
        ENTRY,
        DOMAIN,
        js_navigation_fixture(),
        max_pages=2,
        max_navigation_attempts=2,
        audit_callback=lambda **event: events.append(event),
    )
    assert any(event["kind"] == "web_page" for event in events)
    assert any(
        isinstance(event.get("detail"), dict)
        and event["detail"].get("url", "").startswith("https://")
        for event in events
    )
    assert all("https://" not in event["summary"] for event in events)


@pytest.mark.asyncio
async def test_nested_leadership_globally_preempts_low_value_root_paths():
    driver = js_navigation_fixture()

    pages = await collect_official_pages_with_browser_driver(
        ENTRY,
        DOMAIN,
        driver,
        max_pages=4,
        max_navigation_attempts=20,
    )

    urls = [str(page.url) for page in pages]
    assert "https://www.example.org/app/leaders" in urls
    assert "https://www.example.org/app/structure" in urls
    assert "https://www.example.org/members" not in urls


@pytest.mark.asyncio
async def test_direct_root_leadership_path_remains_first_business_page():
    leadership = target("协会领导", "/leaders")
    about = target("协会介绍", "/about")
    members = target("会员服务", "/members")
    driver = FakeBrowserDriver(
        {
            "entry": state(ENTRY, "首页", "首页正文", (members, about, leadership)),
            "leaders": state(f"{ENTRY}leaders", "协会领导", "会长张三"),
            "about": state(f"{ENTRY}about", "协会介绍", "协会简介"),
            "members": state(f"{ENTRY}members", "会员服务", "会员信息"),
        },
        {
            ("entry", leadership.identity): "leaders",
            ("entry", about.identity): "about",
            ("entry", members.identity): "members",
        },
    )

    pages = await collect_official_pages_with_browser_driver(
        ENTRY, DOMAIN, driver, max_pages=2,
    )

    assert [str(page.url) for page in pages] == [ENTRY, f"{ENTRY}leaders"]


@pytest.mark.asyncio
async def test_same_domain_normal_href_navigates_directly_in_current_page():
    about = target("协会介绍", "/about.html")
    driver = FakeBrowserDriver(
        {
            "entry": state(ENTRY, "首页", "首页正文", (about,)),
            "about": state(
                f"{ENTRY}about.html",
                "协会介绍",
                "协会组织机构和领导信息",
            ),
        },
        {},
    )

    pages = await collect_official_pages_with_browser_driver(
        ENTRY, DOMAIN, driver, max_pages=2,
    )

    assert [str(page.url) for page in pages] == [ENTRY, f"{ENTRY}about.html"]
    assert driver.activation_count == 0


@pytest.mark.asyncio
async def test_fragment_href_still_uses_driver_activation():
    fragment = target("组织机构", "#organization")
    driver = FakeBrowserDriver(
        {
            "entry": state(ENTRY, "首页", "首页正文", (fragment,)),
            "organization": state(
                ENTRY,
                "组织机构",
                "组织机构详细内容",
            ),
        },
        {("entry", fragment.identity): "organization"},
    )

    pages = await collect_official_pages_with_browser_driver(
        ENTRY, DOMAIN, driver,
    )

    assert driver.activation_count == 1
    assert "组织机构详细内容" in pages[0].content


@pytest.mark.asyncio
async def test_absolute_spa_fragment_href_still_uses_driver_activation():
    fragment = target("组织领导", f"{ENTRY}#/leadership")
    driver = FakeBrowserDriver(
        {
            "entry": state(ENTRY, "首页", "首页正文", (fragment,)),
            "leadership": state(
                ENTRY,
                "组织领导",
                "会长和秘书长信息",
            ),
        },
        {("entry", fragment.identity): "leadership"},
    )

    pages = await collect_official_pages_with_browser_driver(
        ENTRY, DOMAIN, driver,
    )

    assert driver.activation_count == 1
    assert "会长和秘书长信息" in pages[0].content


@pytest.mark.asyncio
async def test_hanging_replayed_path_returns_partial_pages_within_deadline(
    monkeypatch,
):
    navigation_target = target("协会领导", "/leaders")

    class HangingReplayDriver:
        def __init__(self):
            self.opens = 0
            self.replay_cancelled = False

        async def open(self, _url):
            self.opens += 1
            if self.opens == 1:
                return state(ENTRY, "首页", "首页正文", (navigation_target,))
            try:
                await asyncio.Event().wait()
            finally:
                self.replay_cancelled = True

        async def activate(self, _target, _state):
            raise AssertionError("hanging replay must time out before activation")

    driver = HangingReplayDriver()
    monkeypatch.setattr(browser_collector_module, "MAX_SINGLE_PATH_SECONDS", 0.01)
    monkeypatch.setattr(browser_collector_module, "MAX_COLLECTION_SECONDS", 0.02)

    pages = await collect_official_pages_with_browser_driver(
        ENTRY, DOMAIN, driver,
    )

    assert [str(page.url) for page in pages] == [ENTRY]
    assert driver.replay_cancelled is True


@pytest.mark.asyncio
async def test_hanging_initial_open_is_bounded_by_total_collection_deadline(
    monkeypatch,
):
    class HangingEntryDriver:
        def __init__(self):
            self.cancelled = False

        async def open(self, _url):
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled = True

        async def activate(self, _target, _state):
            raise AssertionError("entry never loaded")

    driver = HangingEntryDriver()
    monkeypatch.setattr(browser_collector_module, "MAX_SINGLE_PATH_SECONDS", 0.02)
    monkeypatch.setattr(browser_collector_module, "MAX_COLLECTION_SECONDS", 0.01)

    with pytest.raises(TimeoutError):
        await collect_official_pages_with_browser_driver(ENTRY, DOMAIN, driver)

    assert driver.cancelled is True


@pytest.mark.asyncio
async def test_cross_domain_unrelated_unchanged_and_duplicate_targets_are_safe():
    driver = js_navigation_fixture()

    pages = await collect_official_pages_with_browser_driver(
        ENTRY,
        DOMAIN,
        driver,
        max_navigation_attempts=20,
    )

    assert all("evil.example" not in str(page.url) for page in pages)
    assert all("/news" not in str(page.url) for page in pages)
    assert (
        "协会领导",
        "https://evil.example/leaders",
        0,
    ) not in driver.activated
    assert ("行业新闻", "/news", 0) not in driver.activated
    assert len([page for page in pages if str(page.url).endswith("/app/about")]) == 1


@pytest.mark.asyncio
async def test_high_value_words_do_not_authorize_action_buttons():
    dangerous = (
        target("会员登录"),
        target("会员注册"),
        target("删除会员"),
        target("Download member list"),
    )
    driver = FakeBrowserDriver(
        {"entry": state(ENTRY, "首页", "首页", dangerous)},
        {},
    )
    pages = await collect_official_pages_with_browser_driver(
        ENTRY, DOMAIN, driver,
    )
    assert [str(page.url) for page in pages] == [ENTRY]
    assert driver.activated == []


@pytest.mark.asyncio
async def test_javascript_click_that_navigates_cross_domain_is_rejected():
    js_target = target("协会介绍", "javascript:void(0)")
    driver = FakeBrowserDriver(
        {
            "entry": state(ENTRY, "首页", "首页", (js_target,)),
            "evil": state(
                "https://evil.example/about", "伪官网", "不可信正文", ()
            ),
        },
        {("entry", js_target.identity): "evil"},
    )
    pages = await collect_official_pages_with_browser_driver(
        ENTRY, DOMAIN, driver,
    )
    assert [str(page.url) for page in pages] == [ENTRY]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "is_navigation", "is_main_frame", "expected"),
    [
        ("https://evil.example/about", True, True, "abort"),
        ("https://www.example.org/about", True, True, "continue"),
        ("https://cdn.example.net/app.js", False, True, "continue"),
        ("https://evil.example/frame", True, False, "abort"),
    ],
)
async def test_playwright_request_guard_blocks_cross_domain_navigation(
    url, is_navigation, is_main_frame, expected,
):
    calls = []

    class Route:
        async def abort(self):
            calls.append("abort")

        async def continue_(self):
            calls.append("continue")

    frame = SimpleNamespace(
        parent_frame=None if is_main_frame else object(),
    )
    request = SimpleNamespace(
        url=url,
        frame=frame,
        is_navigation_request=lambda: is_navigation,
    )
    await _guard_main_frame_navigation(
        Route(),
        request,
        entry_url=ENTRY,
        verified_domain=DOMAIN,
    )
    assert calls == [expected]


@pytest.mark.asyncio
async def test_navigation_attempt_and_page_budgets_stop_collection():
    driver = js_navigation_fixture()

    attempt_limited_pages = await collect_official_pages_with_browser_driver(
        ENTRY,
        DOMAIN,
        driver,
        max_navigation_attempts=1,
        max_total_chars=100,
    )

    assert len(attempt_limited_pages) == 1
    assert driver.activation_count == 1

    page_limited_driver = js_navigation_fixture()
    page_limited_pages = await collect_official_pages_with_browser_driver(
        ENTRY,
        DOMAIN,
        page_limited_driver,
        max_pages=2,
        max_navigation_attempts=20,
    )
    assert len(page_limited_pages) == 2
    # The nested leadership path replays its parent menu before collecting
    # the second page.
    assert page_limited_driver.activation_count == 3
    assert str(page_limited_pages[1].url).endswith("/app/leaders")


@pytest.mark.asyncio
async def test_total_character_budget_keeps_entry_complete_and_rejects_overflow():
    driver = js_navigation_fixture()
    entry_length = len(driver.states["entry"].content)

    pages = await collect_official_pages_with_browser_driver(
        ENTRY,
        DOMAIN,
        driver,
        max_total_chars=entry_length,
        max_navigation_attempts=5,
    )

    assert len(pages) == 1
    assert pages[0].content == driver.states["entry"].content
    assert driver.activation_count == 0


@pytest.mark.asyncio
async def test_spa_same_url_merges_distinct_body_and_replays_parent_path():
    menu = target("关于协会")
    details = target("协会概况")
    entry = state(ENTRY, "首页", "首页正文", (menu,))
    expanded = state(ENTRY, "首页", "首页正文", (menu, details))
    detail = state(ENTRY, "协会概况", "协会成立于2001年", ())
    driver = FakeBrowserDriver(
        {"entry": entry, "expanded": expanded, "detail": detail},
        {
            ("entry", menu.identity): "expanded",
            ("expanded", details.identity): "detail",
        },
    )

    pages = await collect_official_pages_with_browser_driver(
        ENTRY, DOMAIN, driver, max_navigation_attempts=5,
    )

    assert len(pages) == 1
    assert pages[0].content == "首页正文\n\n协会成立于2001年"
    assert driver.open_count == 3
    assert driver.activated.count(menu.identity) == 2


@pytest.mark.asyncio
async def test_playwright_wrapper_passes_headless_and_always_closes(monkeypatch):
    launched = []

    class FakeBrowser:
        def __init__(self):
            self.closed = False

        async def new_page(self):
            raise RuntimeError("page failed")

        async def close(self):
            self.closed = True

    browser = FakeBrowser()

    class FakePlaywright:
        def __init__(self):
            self.chromium = SimpleNamespace(launch=self.launch)

        async def launch(self, **kwargs):
            launched.append(kwargs)
            return browser

    class FakeContext:
        async def __aenter__(self):
            return FakePlaywright()

        async def __aexit__(self, *_args):
            return None

    fake_async_api = SimpleNamespace(async_playwright=lambda: FakeContext())
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_api)

    with pytest.raises(RuntimeError, match="page failed"):
        await collect_official_pages_with_playwright(
            ENTRY, DOMAIN, headless=False,
        )
    assert launched == [{"headless": False}]
    assert browser.closed is True


@pytest.mark.asyncio
async def test_playwright_context_route_handler_uses_single_route_argument(monkeypatch):
    route_calls = []

    class FakeRoute:
        def __init__(self):
            self.request = SimpleNamespace(
                url="https://evil.example/about",
                frame=SimpleNamespace(parent_frame=None),
                is_navigation_request=lambda: True,
            )

        async def abort(self):
            route_calls.append("abort")

        async def continue_(self):
            route_calls.append("continue")

    class FakePageContext:
        async def route(self, pattern, handler):
            assert pattern == "**/*"
            await handler(FakeRoute())

    page = SimpleNamespace(context=FakePageContext())

    class FakeBrowser:
        def __init__(self):
            self.closed = False

        async def new_page(self):
            return page

        async def close(self):
            self.closed = True

    browser = FakeBrowser()
    playwright = SimpleNamespace(
        chromium=SimpleNamespace(
            launch=AsyncMock(return_value=browser),
        )
    )

    class FakeContext:
        async def __aenter__(self):
            return playwright

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setitem(
        sys.modules,
        "playwright.async_api",
        SimpleNamespace(async_playwright=lambda: FakeContext()),
    )
    collect = AsyncMock(return_value=[])
    monkeypatch.setattr(
        browser_collector_module,
        "collect_official_pages_with_browser_driver",
        collect,
    )

    result = await collect_official_pages_with_playwright(
        ENTRY, DOMAIN, headless=True,
    )

    assert result == []
    assert route_calls == ["abort"]
    assert browser.closed is True
    collect.assert_awaited_once()


@pytest.mark.asyncio
async def test_playwright_open_waits_for_delayed_spa_content_and_navigation():
    waits = []

    class FakePage:
        async def goto(self, url, **kwargs):
            assert url == ENTRY
            assert kwargs["wait_until"] == "domcontentloaded"

        async def wait_for_timeout(self, milliseconds):
            waits.append(milliseconds)

    delayed_target = target("协会介绍")
    states = [
        state(ENTRY, "首页", "", ()),
        state(ENTRY, "首页", "壳页面", ()),
        state(ENTRY, "首页", "SPA最终正文", (delayed_target,)),
    ]
    driver = browser_collector_module.PlaywrightNavigationDriver(
        FakePage(), navigation_timeout_ms=1_000,
    )
    driver._state = AsyncMock(side_effect=states)

    result = await driver.open(ENTRY)

    assert result.content == "SPA最终正文"
    assert result.navigation_targets == (delayed_target,)
    assert waits == [250, 250]
    assert driver._state.await_count == 3


@pytest.mark.asyncio
async def test_playwright_open_recovers_timeout_when_same_page_dom_is_readable():
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    class FakePage:
        async def goto(self, *_args, **_kwargs):
            raise PlaywrightTimeoutError("DOMContentLoaded timed out")

        async def wait_for_timeout(self, _milliseconds):
            raise AssertionError("readable state with navigation must return")

    readable = state(
        ENTRY,
        "协会首页",
        "协会正文已经可读",
        (target("协会介绍", "/about.html"),),
    )
    driver = browser_collector_module.PlaywrightNavigationDriver(
        FakePage(), navigation_timeout_ms=1_000,
    )
    driver._state = AsyncMock(return_value=readable)

    assert await driver.open(ENTRY) == readable
    driver._state.assert_awaited_once()


@pytest.mark.asyncio
async def test_playwright_open_timeout_comparison_ignores_fragment_and_trailing_slash():
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    class FakePage:
        async def goto(self, *_args, **_kwargs):
            raise PlaywrightTimeoutError("DOMContentLoaded timed out")

        async def wait_for_timeout(self, _milliseconds):
            raise AssertionError("readable state with navigation must return")

    readable = state(
        f"{ENTRY}#leadership",
        "协会首页",
        "协会正文已经可读",
        (target("协会领导", "#leadership"),),
    )
    driver = browser_collector_module.PlaywrightNavigationDriver(FakePage())
    driver._state = AsyncMock(return_value=readable)

    assert await driver.open(ENTRY.rstrip("/")) == readable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "recovery",
    [
        state(ENTRY, "协会首页", "", ()),
        state("https://www.example.org/other", "其他页", "正文", ()),
        state("https://evil.example/", "跨域页", "正文", ()),
        RuntimeError("DOM cannot be read"),
    ],
)
async def test_playwright_open_preserves_timeout_when_dom_cannot_prove_success(
    recovery,
):
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError

    class FakePage:
        async def goto(self, *_args, **_kwargs):
            raise PlaywrightTimeoutError("DOMContentLoaded timed out")

    driver = browser_collector_module.PlaywrightNavigationDriver(FakePage())
    if isinstance(recovery, Exception):
        driver._state = AsyncMock(side_effect=recovery)
    else:
        driver._state = AsyncMock(return_value=recovery)

    with pytest.raises(PlaywrightTimeoutError, match="DOMContentLoaded"):
        await driver.open(ENTRY)


@pytest.mark.asyncio
async def test_playwright_open_does_not_swallow_non_timeout_navigation_error():
    class FakePage:
        async def goto(self, *_args, **_kwargs):
            raise ConnectionError("DNS lookup failed")

    driver = browser_collector_module.PlaywrightNavigationDriver(FakePage())
    driver._state = AsyncMock()

    with pytest.raises(ConnectionError, match="DNS lookup"):
        await driver.open(ENTRY)
    driver._state.assert_not_awaited()


@pytest.mark.asyncio
async def test_playwright_open_preserves_error_when_lazy_import_is_unavailable(
    monkeypatch,
):
    class FakePage:
        async def goto(self, *_args, **_kwargs):
            raise ConnectionError("DNS lookup failed")

    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    driver = browser_collector_module.PlaywrightNavigationDriver(FakePage())
    driver._state = AsyncMock()

    with pytest.raises(ConnectionError, match="DNS lookup"):
        await driver.open(ENTRY)
    driver._state.assert_not_awaited()


@pytest.mark.asyncio
async def test_sub_250ms_open_timeout_does_not_overshoot_with_fixed_sleep():
    class FakePage:
        async def goto(self, *_args, **_kwargs):
            return None

        async def wait_for_timeout(self, _milliseconds):
            raise AssertionError("must not sleep past a sub-250ms budget")

    initial = state(ENTRY, "首页", "", ())
    driver = browser_collector_module.PlaywrightNavigationDriver(
        FakePage(), navigation_timeout_ms=100,
    )
    driver._state = AsyncMock(return_value=initial)
    assert await driver.open(ENTRY) == initial
    driver._state.assert_awaited_once()


@pytest.mark.asyncio
async def test_sub_250ms_activate_observes_immediate_state_without_sleep():
    navigation_target = target("协会介绍")
    previous = state(ENTRY, "首页", "旧正文", (navigation_target,))
    changed = state(ENTRY, "首页", "新正文", ())

    class FakeElement:
        async def is_visible(self):
            return True

        async def inner_text(self):
            return navigation_target.text

        async def get_attribute(self, _name):
            return navigation_target.href

        async def click(self, **_kwargs):
            return None

    class FakeLocator:
        async def count(self):
            return 1

        def nth(self, _index):
            return FakeElement()

    class FakePage:
        def locator(self, _selector):
            return FakeLocator()

        async def wait_for_load_state(self, **_kwargs):
            return None

        async def wait_for_timeout(self, _milliseconds):
            raise AssertionError("must not sleep past a sub-250ms budget")

    driver = browser_collector_module.PlaywrightNavigationDriver(
        FakePage(), navigation_timeout_ms=100,
    )
    driver._state = AsyncMock(return_value=changed)
    assert await driver.activate(navigation_target, previous) == changed
    driver._state.assert_awaited_once()


@pytest.mark.asyncio
async def test_playwright_state_caps_navigation_candidates():
    class FakeElement:
        async def is_visible(self):
            return True

        async def inner_text(self):
            return "协会介绍"

        async def get_attribute(self, _name):
            return None

    class FakeLocator:
        async def count(self):
            return MAX_NAVIGATION_TARGETS_PER_STATE + 500

        def nth(self, _index):
            return FakeElement()

        async def inner_text(self):
            return "正文"

    class FakePage:
        url = ENTRY

        def locator(self, _selector):
            return FakeLocator()

        async def title(self):
            return "首页"

    from src.services.official_site_browser_collector import PlaywrightNavigationDriver

    page_state = await PlaywrightNavigationDriver(FakePage())._state()
    assert len(page_state.navigation_targets) == MAX_NAVIGATION_TARGETS_PER_STATE


@pytest.mark.asyncio
async def test_fake_locator_hidden_items_do_not_consume_visible_target_limit():
    hidden_count = MAX_NAVIGATION_TARGETS_PER_STATE + 5

    class FakeElement:
        def __init__(self, index):
            self.index = index

        async def is_visible(self):
            return self.index >= hidden_count

        async def inner_text(self):
            return "协会领导"

        async def get_attribute(self, _name):
            return f"/leaders/{self.index}"

    class FakeLocator:
        async def count(self):
            return hidden_count + 2

        def nth(self, index):
            return FakeElement(index)

        async def inner_text(self):
            return "正文"

    class FakePage:
        url = ENTRY

        def locator(self, _selector):
            return FakeLocator()

        async def title(self):
            return "首页"

    page_state = await browser_collector_module.PlaywrightNavigationDriver(
        FakePage()
    )._state()

    assert [item.locator_index for item in page_state.navigation_targets] == [
        hidden_count,
        hidden_count + 1,
    ]


@pytest.mark.asyncio
async def test_playwright_state_batches_dom_navigation_snapshot():
    calls = []

    class FakeLocator:
        async def evaluate_all(self, _script, limit):
            assert _script.index(".filter(") < _script.index(".slice(")
            calls.append(limit)
            return [{
                "index": 303,
                "visible": True,
                "text": "  协会   领导 ",
                "href": "/leaders",
            }]

        async def inner_text(self):
            return "页面正文"

    class FakePage:
        url = ENTRY

        def locator(self, _selector):
            return FakeLocator()

        async def title(self):
            return "首页"

    page_state = await browser_collector_module.PlaywrightNavigationDriver(
        FakePage()
    )._state()

    assert calls == [MAX_NAVIGATION_TARGETS_PER_STATE]
    assert page_state.navigation_targets == (
        target("协会 领导", "/leaders", locator_index=303),
    )


@pytest.mark.asyncio
async def test_activate_preserves_original_locator_index_after_visible_filtering():
    clicked = []
    navigation_target = target(
        "组织机构",
        "javascript:void(0)",
        locator_index=303,
    )
    previous = state(ENTRY, "首页", "旧正文", (navigation_target,))
    changed = state(ENTRY, "组织机构", "新正文")

    class FakeElement:
        async def click(self, **_kwargs):
            clicked.append(303)

    class FakeLocator:
        async def evaluate_all(self, script, limit):
            assert script.index(".filter(") < script.index(".slice(")
            assert limit == MAX_NAVIGATION_TARGETS_PER_STATE
            return [{
                "index": 303,
                "visible": True,
                "text": "组织机构",
                "href": "javascript:void(0)",
            }]

        def nth(self, index):
            assert index == 303
            return FakeElement()

    class FakePage:
        def locator(self, _selector):
            return FakeLocator()

        async def wait_for_load_state(self, **_kwargs):
            return None

    driver = browser_collector_module.PlaywrightNavigationDriver(FakePage())
    driver._state = AsyncMock(return_value=changed)

    assert await driver.activate(navigation_target, previous) == changed
    assert clicked == [303]
