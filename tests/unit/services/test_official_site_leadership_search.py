"""LLM 逐层导航搜索领导页单测。

验证意图：词表列不完几百种领导页叫法，长尾靠「导航栏交 LLM 挑选→逐层点击」
兜住；三层内必须能从 关于我们→学会概况 找到 现任领导 页，且预算/安全边界
（层数、点击数、跨域、不安全链接）不被突破。
"""

import pytest

from src.services.official_site_browser_collector import (
    BrowserNavigationTarget,
    BrowserPageState,
)
from src.services.official_site_leadership_search import (
    looks_like_leadership_page,
    search_leadership_pages_with_driver,
)


class _ScriptedDriver:
    """按 url 字典返回预设页面状态的假 driver。"""

    def __init__(self, pages: dict[str, BrowserPageState], links: dict[str, list]):
        self._pages = pages
        self._links = links
        self.activate_log = []
        self.visit_log = []  # open+activate 统一按序记录（text 或 URL）

    async def open(self, url: str) -> BrowserPageState:
        # 按 URL 精确匹配：先整串相等，再路径相等，最后才子串（最长的先）
        from urllib.parse import urlparse

        self.visit_log.append(f"open:{urlparse(url).path or url}")
        path = urlparse(url).path
        for key in sorted(self._pages, key=len, reverse=True):
            if key == url or (key.startswith("/") and key == path):
                return self._pages[key]
        for key in sorted(self._pages, key=len, reverse=True):
            if key in url:
                return self._pages[key]
        return self._pages[url]

    async def activate(self, target, previous_state):
        self.activate_log.append((previous_state.url, target.text))
        self.visit_log.append(f"click:{target.text}")
        href = target.href or ""
        # 最长 key 优先匹配，避免 "/about" 抢先命中 "/about/intro"
        for key in sorted(self._pages, key=len, reverse=True):
            if key in href or key in target.text:
                return self._pages[key]
        return None


def _state(url: str, title: str, content: str, nav: dict[str, str] | None = None):
    return BrowserPageState(
        url=url,
        title=title,
        content=content,
        navigation_targets=tuple(
            BrowserNavigationTarget(text=text, href=href, occurrence=0)
            for text, href in (nav or {}).items()
        ),
    )


class _PickFirstGateway:
    """LLM 按预设映射挑链接：每个页面状态返回固定下标列表。"""

    def __init__(self, picks_by_page: dict[str, list[int]]):
        self.picks_by_page = picks_by_page
        self.calls = 0

    async def chat(self, **kwargs):
        self.calls += 1
        user_content = kwargs["messages"][1]["content"]
        for page_key, picks in self.picks_by_page.items():
            if page_key in user_content:
                import json as _json
                return {"content": _json.dumps({"click": picks})}
        return {"content": '{"click":[]}'}


HOME = _state(
    "https://example.cn/", "首页", "欢迎访问。",
    {"关于我们": "/about", "新闻中心": "/news", "登录": "/login"},
)
ABOUT = _state(
    "https://example.cn/about", "关于我们", "本会简介。",
    {"学会概况": "/about/intro", "联系我们": "/contact"},
)
INTRO = _state(
    "https://example.cn/about/intro", "学会概况", "学会基本情况。",
    {"现任领导": "/about/leaders", "章程": "/about/charter"},
)
LEADERS = _state(
    "https://example.cn/about/leaders",
    "现任领导",
    "中国测试学会第X届理事会名单正文。" * 25
    + "会长：张会长\n秘书长：王建琪\n副会长：李副会长",
)
PAGES = {
    "example.cn/": HOME,
    "/about": ABOUT,
    "/about/intro": INTRO,
    "/about/leaders": LEADERS,
}


@pytest.mark.asyncio
async def test_finds_leadership_page_through_layers():
    """首页→关于我们→学会概况→现任领导，三层点击后命中秘书长页面。"""
    picks = {
        "首页": [0],        # 点「关于我们」
        "关于我们": [0],    # 点「学会概况」
        "学会概况": [0],    # 点「现任领导」
    }
    driver = _ScriptedDriver(PAGES, {})
    gateway = _PickFirstGateway(picks)

    found = await search_leadership_pages_with_driver(
        "https://example.cn/", "example.cn", driver, gateway,
        association_name="测试学会",
    )

    assert len(found) == 1
    assert "王建琪" in found[0].content
    assert str(found[0].url).endswith("/about/leaders")
    assert found[0].verified_official is True
    # 点了三层：关于我们→学会概况→现任领导（带 href 走 URL 直开）
    assert driver.visit_log == [
        "open:/", "open:/about", "open:/about/intro", "open:/about/leaders",
    ]


@pytest.mark.asyncio
async def test_entry_page_already_leadership():
    """小型官网首页直接挂领导班子 → 零点击即收录。"""
    entry = _state(
        "https://example.cn/", "首页",
        "中国测试学会领导班子名单正文。" * 25 + "领导班子：秘书长 王建琪、会长 张会长",
        {"新闻": "/news"},
    )
    driver = _ScriptedDriver({"example.cn/": entry}, {})
    found = await search_leadership_pages_with_driver(
        "https://example.cn/", "example.cn", driver, _PickFirstGateway({}),
    )
    assert len(found) == 1
    assert driver.activate_log == []


@pytest.mark.asyncio
async def test_llm_no_pick_falls_back_to_deterministic_terms():
    """LLM 空选时走词表权重兜底：关于我们→学会概况→现任领导 仍能命中。

    真机教训：人口学会等站 LLM 返回空导致点击 0 次直接结束。
    """
    driver = _ScriptedDriver(PAGES, {})
    gateway = _PickFirstGateway({})
    found = await search_leadership_pages_with_driver(
        "https://example.cn/", "example.cn", driver, gateway,
    )
    assert len(found) == 1
    assert "王建琪" in found[0].content
    # 词表兜底逐层点击：每层点优先级 top2（学会概况(85)先于联系我们(70)）
    assert driver.visit_log == [
        "open:/", "open:/about", "open:/about/intro", "open:/contact",
        "open:/about/leaders", "open:/about/charter",
    ]


@pytest.mark.asyncio
async def test_no_high_value_targets_stops_cleanly():
    """LLM 空选 + 词表也无高分目标（全新闻导航）→ 干净结束不点击。"""
    newsy = _state(
        "https://example.cn/", "首页", "欢迎。",
        {"新闻动态": "/news", "通知公告": "/notice", "登录": "/login"},
    )
    driver = _ScriptedDriver({"example.cn/": newsy}, {})
    found = await search_leadership_pages_with_driver(
        "https://example.cn/", "example.cn", driver, _PickFirstGateway({}),
    )
    assert found == []
    assert driver.activate_log == []


@pytest.mark.asyncio
async def test_depth_budget_stops_runaway_clicks():
    """最深 4 层封顶：链式页面无限深时点击次数不超预算。"""
    # 构造 5 层深的链条，每层 LLM 都让点第一个链接
    chain = {}
    nav = {}
    for level in range(6):
        url = f"https://example.cn/l{level}"
        chain[url] = _state(
            url, f"第{level}层", f"第{level}层内容。",
            {f"领导下一层{level + 1}": f"https://example.cn/l{level + 1}"},
        )
    driver = _ScriptedDriver(chain, nav)
    picks = {f"第{i}层": [0] for i in range(6)}
    found = await search_leadership_pages_with_driver(
        "https://example.cn/l0", "example.cn", driver,
        _PickFirstGateway(picks),
        max_total_clicks=3,
    )
    assert found == []
    assert len(driver.activate_log) <= 3  # 点击预算不被突破


@pytest.mark.asyncio
async def test_offdomain_page_ignored():
    """激活后跳到跨域页面 → 不收录、不继续展开。"""
    evil = _state("https://evil.com/", "外站", "秘书长：假人")
    driver = _ScriptedDriver({"example.cn/": HOME, "evil.com": evil}, {})
    gateway = _PickFirstGateway({"首页": [0]})
    # 把「关于我们」的 href 指到外站
    home = _state(
        "https://example.cn/", "首页", "欢迎。",
        {"关于我们": "https://evil.com/about"},
    )
    driver._pages["example.cn/"] = home

    found = await search_leadership_pages_with_driver(
        "https://example.cn/", "example.cn", driver, gateway,
    )
    assert found == []  # 跨域含"秘书长"也不收


@pytest.mark.asyncio
async def test_browser_drift_repositions_before_activate():
    """浏览器漂移失配时先回位再 activate（无 href 的 JS 路由目标）。

    真机教训（人口学会 cpaw.org.cn）：activate 在「当前 DOM」上按 text+href
    匹配元素；BFS 出队/同轮第 2 个候选点击把浏览器带到别的页后，在错误的
    页上匹配不到侧栏目标（如「现任领导」）会静默返回 None，8 次点击预算
    全部耗光。回位（重开目标页 URL）后必须仍能命中名单页。
    """

    class _PositionalDriver(_ScriptedDriver):
        """activate 模拟真实 DOM 匹配：页内目标（如侧栏子项）只在所属页可点；
        全局目标（来源页为 None，如顶部导航）在任何页都能点成功。"""

        def __init__(self, pages, click_map):
            super().__init__(pages, {})
            self._click_map = click_map  # {(来源页URL|None, 目标文字): 页面key}
            self.current_url = None
            self.mismatch_activate = 0

        async def open(self, url):
            self.current_url = url
            return await super().open(url)

        async def activate(self, target, previous_state):
            key = self._click_map.get((None, target.text))  # 全局导航项
            if key is None:
                if self.current_url != previous_state.url:
                    # 浏览器已被其他点击带走，此页的 DOM 里没有该目标
                    self.mismatch_activate += 1
                    return None
                key = self._click_map.get((previous_state.url, target.text))
                if key is None:
                    return None
            self.visit_log.append(f"click:{target.text}")
            state = self._pages[key]
            self.current_url = state.url
            return state

    home = _state(
        "https://example.cn/", "首页", "欢迎。",
        {"关于我们": "", "新闻": ""},  # JS 路由：无 href，必须走 activate
    )
    about = _state(
        "https://example.cn/about", "关于我们", "本会简介。",
        {"现任领导": ""},
    )
    news = _state("https://example.cn/news", "新闻", "新闻列表。")
    leaders = _state(
        "https://example.cn/about/leaders", "现任领导",
        "理事会名单正文。" * 40 + "秘书长：王建琪",
    )
    driver = _PositionalDriver(
        {
            "example.cn/": home,
            "/about": about,
            "/news": news,
            "/about/leaders": leaders,
        },
        click_map={
            ("https://example.cn/", "关于我们"): "/about",
            (None, "新闻"): "/news",  # 顶部导航：任何页都能点
            ("https://example.cn/about", "现任领导"): "/about/leaders",
        },
    )
    gateway = _PickFirstGateway({"首页": [0, 1], "关于我们": [0]})

    found = await search_leadership_pages_with_driver(
        "https://example.cn/", "example.cn", driver, gateway,
    )
    assert len(found) == 1
    assert "王建琪" in found[0].content
    # 回位后所有 activate 都发生在正确的页上，无失配浪费
    assert driver.mismatch_activate == 0


def test_secretary_binding_count():
    """绑定计数：名单页的「秘书长-姓名」表述计 1，新闻标题裸词计 0。

    真机教训：焊接协会首页新闻「秘书长工作会议」×3 曾把真名单页
    （绑定×1）挤到收录榜尾；冶金教育学会名单页是职务行+姓名行分离
    （「秘书长\n孙建林（兼）」），不带换行模式的正则计 0。
    """
    from src.services.official_site_leadership_search import (
        _secretary_binding_count,
    )

    assert _secretary_binding_count("会议选举俞培根为会长，秘书长为李连胜。") == 1
    assert _secretary_binding_count("会长：张三\n秘书长：王建琪\n副会长：李四") == 1
    assert _secretary_binding_count("秘书长\n\n孙建林（兼）") == 1
    assert _secretary_binding_count("秘书长\n孙建林") == 1
    # 新闻标题/会议通知类裸词不计数
    assert _secretary_binding_count("关于召开会长（理事长）、秘书长工作会的通知") == 0
    assert _secretary_binding_count("学会秘书处地址：北京市海淀区") == 0
    assert _secretary_binding_count("") == 0


def test_leadership_markers():
    """强标记命中即领导页；单一弱标记（会长单位/理事长新闻/页脚秘书处）不算。"""
    pad = "协会简介正文。" * 60  # 满足内容长度门槛（导航壳页面不算领导页）
    assert looks_like_leadership_page(pad + "现任秘书长：王建琪") is True
    assert looks_like_leadership_page(pad + "领导班子：会长张三、秘书长王建琪") is True
    assert looks_like_leadership_page(pad + "会长单位名录") is False
    # 真机教训：首页新闻标题「理事长出席…」曾把首页误判成领导页
    assert looks_like_leadership_page(pad + "理事长出席2026年会并致辞") is False
    # 真机教训：页脚「秘书处」单独出现（csmedu.org）曾把首页占满收录名额
    assert looks_like_leadership_page(pad + "地址：北京市… 秘书处 电话：010-…") is False
    assert looks_like_leadership_page("") is False
    # 真机教训：侧栏导航文字含「现任领导」的 235 字页面壳曾误判成领导页
    shell = "首页 时政要闻 学会消息 关于学会 分支机构 学会简介 学会章程 领导机构 历任会长 现任领导 信息公开"
    assert looks_like_leadership_page(shell) is False
    # 两个弱标记同时出现算
    assert looks_like_leadership_page(pad + "会长：张三 副会长：李四 理事若干") is True
