from urllib.parse import urlparse

import pytest

from src.services.association_profile_extractor import VerifiedOfficialPage
from src.services.official_site_page_collector import (
    OfficialPageLink,
    OfficialPageSnapshot,
    collect_high_value_official_pages,
    discover_high_value_official_links,
)


pytestmark = pytest.mark.unit
DOMAIN = "www.caapa.org"


def snapshot(
    url="https://www.caapa.org/",
    title="首页",
    content="官网首页",
    links=None,
):
    return OfficialPageSnapshot(
        url=url,
        title=title,
        content=content,
        links=links or [],
        verified_official=True,
    )


def test_discovers_organizational_and_excludes_cross_domain_or_unrelated_links():
    page = snapshot(
        links=[
            OfficialPageLink(url="/About/Organizational.html", text="组织机构"),
            OfficialPageLink(url="https://www.caapa.org/About/1.html", text="协会简介"),
            OfficialPageLink(url="https://evil.example/About/1.html", text="协会简介"),
            OfficialPageLink(url="/news/123.html", text="行业新闻"),
            OfficialPageLink(url="/remember.html", text="历史回顾"),
        ]
    )

    links = discover_high_value_official_links([page], DOMAIN)

    assert "https://www.caapa.org/About/Organizational.html" in links
    assert "https://www.caapa.org/About/1.html" in links
    assert all("evil.example" not in url for url in links)
    assert all("/news/" not in url for url in links)
    assert all("remember" not in url for url in links)


def test_english_terms_require_real_ascii_word_boundaries():
    page = snapshot(
        links=[
            OfficialPageLink(url="/member2", text="member2"),
            OfficialPageLink(url="/team_member", text="team_member"),
            OfficialPageLink(url="/members-only", text="members only"),
        ]
    )
    links = discover_high_value_official_links([page], DOMAIN)
    assert links == ["https://www.caapa.org/members-only"]


def test_nonstandard_port_is_rejected_unless_verified_domain_declares_it():
    page = snapshot(
        links=[
            OfficialPageLink(
                url="https://www.caapa.org:8443/about",
                text="协会简介",
            )
        ]
    )
    assert discover_high_value_official_links([page], DOMAIN) == []
    assert discover_high_value_official_links(
        [page], "https://www.caapa.org:8443"
    ) == ["https://www.caapa.org:8443/about"]


def test_fragment_default_port_and_host_trailing_dot_are_deduplicated():
    page = snapshot(
        links=[
            OfficialPageLink(url="https://www.caapa.org:443/about#one", text="协会简介"),
            OfficialPageLink(url="https://www.caapa.org./about#two", text="协会介绍"),
            OfficialPageLink(url="/about", text="关于协会"),
        ]
    )
    links = discover_high_value_official_links([page], DOMAIN)
    assert links == ["https://www.caapa.org/about"]


def test_discovers_all_required_high_value_sections_in_chinese_and_english():
    expected_paths = {
        "/about": "协会简介",
        "/leadership": "组织领导",
        "/About/Organizational.html": "组织架构",
        "/branches": "分支机构",
        "/contact": "联系我们",
        "/membership": "会员",
    }
    page = snapshot(
        links=[
            OfficialPageLink(url=path, text=text)
            for path, text in expected_paths.items()
        ]
    )
    links = discover_high_value_official_links([page], DOMAIN)
    assert {urlparse(url).path for url in links} == set(expected_paths)


def test_discovers_society_and_leadership_word_variants():
    """学会镜像词 + 领导泛化词必须命中（人口学会/冶金教育学会等学会站点

    曾因词表只有「协会」前缀被整体拦截，导致秘书长姓名只能依赖文心错值）。
    """
    variants = {
        "/xhjj": "学会简介",
        "/gxueh": "关于学会",
        "/xhld": "学会领导",
        "/xrld": "现任领导",
        "/ldjj": "领导简介",
        "/lsz": "理事长",
        "/msc": "秘书处",
        "/bsjg": "办事机构",
        "/znbm": "职能部门",
        "/bmsz": "部门设置",
        "/nsjg": "内设机构",
        "/fzr": "负责人",
        "/lsh": "理事会",
        "/zc": "章程",
    }
    page = snapshot(
        links=[
            OfficialPageLink(url=path, text=text)
            for path, text in variants.items()
        ]
    )
    links = discover_high_value_official_links([page], DOMAIN)
    assert {urlparse(url).path for url in links} == set(variants)


@pytest.mark.asyncio
async def test_collection_deduplicates_and_honors_page_and_character_limits():
    seed = snapshot(
        content="首页",
        links=[
            OfficialPageLink(url="/about", text="协会简介"),
            OfficialPageLink(url="/about#leader", text="组织领导"),
            OfficialPageLink(url="/contact", text="联系我们"),
        ],
    )
    fetched_urls = []

    async def fetch(url):
        fetched_urls.append(url)
        if url.endswith("/about"):
            return snapshot(url=url, title="协会简介", content="完整短页面", links=[])
        return snapshot(url=url, title="联系我们", content="不会进入预算", links=[])

    pages = await collect_high_value_official_pages(
        [seed],
        DOMAIN,
        fetch,
        max_pages=2,
        max_total_chars=len(seed.content) + len("完整短页面"),
    )

    assert [str(page.url) for page in pages] == [
        "https://www.caapa.org/",
        "https://www.caapa.org/about",
    ]
    assert pages[1].content == "完整短页面"
    assert fetched_urls == ["https://www.caapa.org/about"]


@pytest.mark.asyncio
async def test_fetched_cross_domain_redirect_is_not_collected():
    seed = snapshot(
        links=[OfficialPageLink(url="/contact", text="联系我们")],
    )

    async def fetch(_url):
        return snapshot(
            url="https://evil.example/contact",
            title="伪造联系我们",
            content="不可信正文",
        )

    pages = await collect_high_value_official_pages([seed], DOMAIN, fetch)

    assert len(pages) == 1
    assert str(pages[0].url) == "https://www.caapa.org/"


@pytest.mark.asyncio
async def test_same_domain_redirect_uses_final_url_and_deduplicates_it():
    seed = snapshot(
        links=[
            OfficialPageLink(url="/old-contact", text="联系我们"),
            OfficialPageLink(url="/contact", text="联系方式"),
        ],
    )
    calls = []

    async def fetch(url):
        calls.append(url)
        return snapshot(
            url="https://www.caapa.org/contact",
            title="联系我们",
            content="完整联系页面",
        )

    pages = await collect_high_value_official_pages([seed], DOMAIN, fetch)
    assert [str(item.url) for item in pages] == [
        "https://www.caapa.org/",
        "https://www.caapa.org/contact",
    ]
    assert calls == ["https://www.caapa.org/old-contact"]


@pytest.mark.asyncio
async def test_over_budget_page_is_not_truncated_or_returned():
    seed = snapshot(
        content="首页",
        links=[OfficialPageLink(url="/members", text="会员")],
    )

    async def fetch(url):
        return snapshot(url=url, title="会员", content="完整正文超过预算")

    pages = await collect_high_value_official_pages(
        [seed], DOMAIN, fetch, max_total_chars=len(seed.content) + 2,
    )
    assert len(pages) == 1
    assert pages[0].content == "首页"
    assert all("完整正文" not in item.content for item in pages)
    assert all(isinstance(item, VerifiedOfficialPage) for item in pages)


@pytest.mark.asyncio
async def test_over_budget_seed_still_contributes_navigation_links():
    seed = snapshot(
        content="首页正文过长",
        links=[OfficialPageLink(url="/contact", text="联系我们")],
    )

    async def fetch(url):
        return snapshot(url=url, title="联系我们", content="短页")

    pages = await collect_high_value_official_pages(
        [seed], DOMAIN, fetch, max_total_chars=len("短页"),
    )
    assert [str(item.url) for item in pages] == [
        "https://www.caapa.org/contact",
    ]
    assert pages[0].content == "短页"


@pytest.mark.asyncio
async def test_rejected_pages_cannot_exceed_fetch_attempt_budget():
    seed = snapshot(
        links=[
            OfficialPageLink(url=f"/contact-{index}", text="联系我们")
            for index in range(10)
        ]
    )
    calls = []

    async def fetch(url):
        calls.append(url)
        return snapshot(
            url=f"https://evil.example/{len(calls)}",
            title="跨域",
            content="不可信正文",
        )

    pages = await collect_high_value_official_pages(
        [seed], DOMAIN, fetch, max_fetches=2,
    )
    assert len(pages) == 1
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_pages", "max_total_chars"),
    [
        (51, 20_000),
        (12, 20_001),
    ],
)
async def test_custom_limits_cannot_exceed_extractor_contract(
    max_pages, max_total_chars,
):
    async def fetch(_url):
        raise AssertionError("must reject before fetch")

    with pytest.raises(ValueError):
        await collect_high_value_official_pages(
            [snapshot()],
            DOMAIN,
            fetch,
            max_pages=max_pages,
            max_total_chars=max_total_chars,
        )
