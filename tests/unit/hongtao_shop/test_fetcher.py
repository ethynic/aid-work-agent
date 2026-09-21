"""hongtao_shop fetcher 单测：分页/total 核对/价格即弃/detail 提取/护栏（MockTransport）。"""

import json

import httpx
import pytest

from src.tenant_custom.hongtao_shop import fetcher
from src.tenant_custom.hongtao_shop.fetcher import (
    FetchError,
    ResponseTooLargeError,
    build_client,
    extract_detail,
    fetch_all,
    fetch_page,
    strip_price_fields,
)


@pytest.fixture()
def fast_sleep(monkeypatch):
    """重试/页间休眠清零，测试不等待。"""
    async def _no_sleep(seconds: float) -> None:
        return None
    monkeypatch.setattr(fetcher, "_sleep", _no_sleep)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _page(items, total, status=1):
    return httpx.Response(200, json={"status": status, "datalist": items, "total": total, "msg": ""})


def _product(pid, **overrides):
    item = {
        "id": pid,
        "name": f"TFZJ189001{pid}欧典米灰",
        "procode": "",
        "sellpoint": "通体大理石",
        "cid": "12",
        "bid": "1",
        "status": "1",
        "pic": f"https://oss/pic{pid}.jpg",
        "pics": [f"https://oss/p{pid}_1.jpg"],
        "detail": json.dumps([{"content": '<img src="https://oss/d1.jpg"/><img src="spacer.gif"/>'}]),
        "video": "",
        "stock": "1000",
        "sales": "9",
        "comment_score": "5.0",
        "comment_num": "2",
        "createtime": "1758432000",
        "market_price": "99.00",
        "sell_price": "79.00",
    }
    item.update(overrides)
    return item


async def test_fetch_all_pages_until_total(fast_sleep):
    """分页翻到 total：fetch_complete=True，条数=total，价格字段即弃。"""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(httpx.QueryParams(request.url.query)).get("pagenum", 1))
        calls.append(page)
        if page == 1:
            return _page([_product(i) for i in range(100)], total=150)
        return _page([_product(i) for i in range(100, 150)], total=150)

    async with _client(handler) as client:
        result = await fetch_all(client, fetcher.PRODUCT_ENDPOINT)
    assert calls == [1, 2]
    assert result.fetch_complete is True
    assert result.total_reported == 150
    assert len(result.items) == 150
    for item in result.items:
        assert "market_price" not in item and "sell_price" not in item
        # 非价格字段原样保留（status 原值供目录对账）
        assert item["status"] == "1"


async def test_fetch_all_empty_page_stops(fast_sleep):
    """空页兜底停止且视为完整（total=0 场景降级为分页耗尽）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(httpx.QueryParams(request.url.query)).get("pagenum", 1))
        if page == 1:
            return _page([_product(1)], total=0)
        return _page([], total=0)

    async with _client(handler) as client:
        result = await fetch_all(client, fetcher.PRODUCT_ENDPOINT)
    assert result.fetch_complete is True
    assert len(result.items) == 1


async def test_fetch_all_limit_truncates_not_complete(fast_sleep):
    """冒烟截断：fetch_complete=False（对账门禁据此不推进删除）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return _page([_product(i) for i in range(100)], total=500)

    async with _client(handler) as client:
        result = await fetch_all(client, fetcher.PRODUCT_ENDPOINT, limit=3)
    assert result.fetch_complete is False
    assert len(result.items) == 3


async def test_fetch_page_status_not_one_raises(fast_sleep):
    """status=0（参数错误）重试 3 次后抛 FetchError。"""
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return _page([], total=0, status=0)

    async with _client(handler) as client:
        with pytest.raises(FetchError, match="status!=1"):
            await fetch_page(client, fetcher.PRODUCT_ENDPOINT, 1)
    assert len(attempts) == fetcher.PAGE_RETRIES


async def test_fetch_page_response_too_large(fast_sleep):
    """响应体超 10MB 护栏即拒收（ResponseTooLargeError）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (fetcher.MAX_RESPONSE_BYTES + 1))

    async with _client(handler) as client:
        with pytest.raises(ResponseTooLargeError):
            await fetch_page(client, fetcher.PRODUCT_ENDPOINT, 1)


async def test_build_client_no_redirect():
    """follow_redirects=False：3xx 不跟随（域名硬编码白名单）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://evil.example/"})

    async with _client(handler) as client:
        request = client.build_request("GET", fetcher.BASE_URL)
        response = await client.send(request)
        assert response.status_code == 302
    client = build_client()
    assert client.follow_redirects is False
    await client.aclose()


def test_strip_price_fields():
    item = {"name": "x", "market_price": "99", "sell_price": "79"}
    assert strip_price_fields(item) == {"name": "x"}
    assert strip_price_fields({"name": "y"}) == {"name": "y"}


def test_extract_detail_uefitor_json_array():
    """detail 为 UEditor JSON 数组：提图（剔 spacer）+ 文字（剥标签、剔 URL）。"""
    detail = json.dumps(
        [
            {"content": '<p><img src="https://oss/a.jpg"/><img src="https://oss/spacer.gif"/></p>'},
            {"content": "<p>通体大理石 750x1500</p>"},
            {"content": "<p>详见 https://oss/page</p>"},
            "不是字典的块",
        ]
    )
    text, imgs = extract_detail(detail)
    assert imgs == ["https://oss/a.jpg"]
    assert "通体大理石 750x1500" in text
    assert "https://oss/page" not in text


def test_extract_detail_dedup_and_spacer():
    """重复 src 去重 + spacer 剔除（重复 URL 不得进 VL 解析与计费）。"""
    detail = json.dumps(
        [
            {"content": '<img src="https://oss/a.jpg"/><img src="https://oss/a.jpg"/><img src="https://oss/b.jpg"/>'},
            {"content": '<img src="https://oss/a.jpg"/><img src="https://oss/spacer.gif"/>'},
        ]
    )
    text_out, imgs = extract_detail(detail)
    assert imgs == ["https://oss/a.jpg", "https://oss/b.jpg"]
    assert text_out == ""


def test_extract_detail_invalid_input():
    assert extract_detail("") == ("", [])
    assert extract_detail("不是JSON") == ("", [])
    assert extract_detail('{"content": "对象非数组"}') == ("", [])
