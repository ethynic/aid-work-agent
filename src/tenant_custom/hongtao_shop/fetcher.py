"""宏陶商城对外接口拉取（fetcher）。

设计 §4/§5；接口形态照雏形 scripts/hongtao_product_ingest.py 2026-09-21 实测：
- 双接口（商品 getShopProduct / 论坛 getLuntan）均为无鉴权 GET，参数 pagenum/pernum
  （pernum 最大 100），响应 {status:1, datalist:[...], total:N}；数值字段也返回字符串；
- detail 为 UEditor 富文本 **JSON 数组**（非 HTML 字符串）：逐 block 取 content HTML，
  src= 正则提图 URL，剥标签取文字。

护栏：follow_redirects=False（域名硬编码白名单，不跟随跳转）、响应体 10MB 上限
（超限即拒收，不截断半包）、固定 UA、页间 0.3s、单页重试 3 次退避。

价格字段彻底排除（客户要求）：market_price/sell_price 解析后即弃，
不进正文、不进 metadata、不进任何缓存表——本模块是价格字段的唯一入口。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

import httpx

logger = logging.getLogger("hongtao_shop.fetcher")

BASE_URL = "https://hongtaoshop.gzfenxiao.com/"
PRODUCT_ENDPOINT = "/ApiExternal/getShopProduct"
FORUM_ENDPOINT = "/ApiExternal/getLuntan"

PERNUM = 100
PAGE_SLEEP_SECONDS = 0.3
PAGE_RETRIES = 3
PAGE_RETRY_BASE_DELAY = 2.0
MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10MB 响应体护栏（超限即拒收）
USER_AGENT = "aid-kb-ingest/1.0"
REQUEST_TIMEOUT_SECONDS = 30.0

# 客户要求彻底排除的价格字段（含 products 缓存表 DDL 亦不设列，三重防线）
PRICE_FIELDS = ("market_price", "sell_price")


class FetchError(RuntimeError):
    """拉取失败（status!=1 / 网络 / 超限）。"""


class ResponseTooLargeError(FetchError):
    """响应体超过 10MB 护栏。"""


@dataclass
class FetchResult:
    """一次全量分页拉取的结果（fetch_complete/total_reported 供对账门禁）。"""

    items: List[dict] = field(default_factory=list)
    fetch_complete: bool = False
    total_reported: int = 0


async def _sleep(seconds: float) -> None:
    """页间/重试休眠（独立函数便于测试替换）。"""
    await asyncio.sleep(seconds)


def _s(value: Any) -> str:
    """接口返回值统一规整为去空白字符串（该接口数值字段也返回字符串）。"""
    return str(value).strip() if value is not None else ""


def strip_price_fields(item: dict) -> dict:
    """价格字段即弃：解析后立刻 pop，后续任何结构不可见。"""
    for key in PRICE_FIELDS:
        item.pop(key, None)
    return item


def extract_detail(detail_raw: str) -> Tuple[str, List[str]]:
    """detail 为 UEditor 富文本 JSON 数组：提取文字与图片 URL。

    实测宏陶商品 detail 内容基本全是 <img>（无 alt 文本），文字极少；
    图片 URL 与 pics 轮播图不完全重合（详情长图另有一批），一并保留；
    剔除加载占位图（spacer.gif）。
    """
    text_parts, img_urls = [], []
    try:
        blocks = json.loads(detail_raw) if detail_raw else []
    except (TypeError, ValueError):
        return "", []
    if not isinstance(blocks, list):
        return "", []
    for block in blocks:
        html = (block or {}).get("content") or "" if isinstance(block, dict) else ""
        img_urls.extend(re.findall(r'src=["\']([^"\']+)["\']', html))
        txt = re.sub(r"<[^>]+>", " ", html)
        txt = re.sub(r"https?://\S+", " ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt:
            text_parts.append(txt)
    # 剔除加载占位图 + 保序去重（设计 §2「去重、剔除 spacer」；重复 URL 若不去重
    # 会在 VL 解析与按张计费中重复出现——双倍费用）
    seen, deduped = set(), []
    for u in img_urls:
        if "spacer.gif" in u or u in seen:
            continue
        seen.add(u)
        deduped.append(u)
    return " ".join(text_parts), deduped


async def _read_body_limited(response: httpx.Response) -> bytes:
    """流式读响应体，超过 10MB 护栏即拒收（不落半包）。"""
    chunks, total = [], 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ResponseTooLargeError(
                f"响应体超过 {MAX_RESPONSE_BYTES // (1024 * 1024)}MB 护栏，拒收"
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_page(client: httpx.AsyncClient, endpoint: str, page: int) -> dict:
    """拉取单页并校验 status==1；失败重试 PAGE_RETRIES 次（退避递增）。"""
    url = f"{BASE_URL}?s={endpoint}&pagenum={page}&pernum={PERNUM}"
    last_err: Optional[Exception] = None
    for attempt in range(1, PAGE_RETRIES + 1):
        try:
            request = client.build_request(
                "GET", url, headers={"User-Agent": USER_AGENT}
            )
            response = await client.send(request, stream=True)
            try:
                if response.status_code != 200:
                    raise FetchError(f"HTTP {response.status_code}")
                body = await _read_body_limited(response)
            finally:
                await response.aclose()
            data = json.loads(body.decode("utf-8"))
            if data.get("status") != 1:
                raise FetchError(f"接口 status!=1: {data.get('msg')}")
            return data
        except ResponseTooLargeError:
            # 超限拒收不重试（重试同一超大响应没有意义）
            raise
        except Exception as e:  # noqa: BLE001 重试兜底
            last_err = e
            if attempt < PAGE_RETRIES:
                await _sleep(PAGE_RETRY_BASE_DELAY * attempt)
    raise FetchError(f"拉取 {endpoint} 第 {page} 页失败（已重试 {PAGE_RETRIES} 次）: {last_err}")


async def fetch_all(
    client: httpx.AsyncClient, endpoint: str, limit: Optional[int] = None
) -> FetchResult:
    """全量分页拉取：翻到 total 或空页；价格字段即弃。

    limit 仅用于冒烟截断（截断时 fetch_complete=False，对账门禁据此不推进删除）。
    """
    result = FetchResult()
    page = 1
    while True:
        data = await fetch_page(client, endpoint, page)
        batch = data.get("datalist") or []
        if not isinstance(batch, list):
            raise FetchError(f"{endpoint} 第 {page} 页 datalist 非列表")
        if endpoint == PRODUCT_ENDPOINT:
            batch = [strip_price_fields(dict(item)) for item in batch if isinstance(item, dict)]
        else:
            batch = [dict(item) for item in batch if isinstance(item, dict)]
        result.items.extend(batch)
        result.total_reported = int(data.get("total") or 0)
        logger.info(
            "hongtao_shop fetch %s page=%s got=%s total=%s",
            endpoint, page, len(batch), result.total_reported,
        )
        if not batch or (result.total_reported and len(result.items) >= result.total_reported):
            result.fetch_complete = True
            break
        if limit is not None and len(result.items) >= limit:
            break  # 冒烟截断：不算完整抓取
        page += 1
        await _sleep(PAGE_SLEEP_SECONDS)
    if limit is not None:
        result.items = result.items[:limit]
    return result


async def fetch_products(
    client: httpx.AsyncClient, limit: Optional[int] = None
) -> FetchResult:
    """商品接口全量拉取（status 原值保留供目录对账；入库过滤在 service 层）。"""
    return await fetch_all(client, PRODUCT_ENDPOINT, limit=limit)


async def fetch_posts(
    client: httpx.AsyncClient, limit: Optional[int] = None
) -> FetchResult:
    """论坛接口全量拉取。"""
    return await fetch_all(client, FORUM_ENDPOINT, limit=limit)


def build_client() -> httpx.AsyncClient:
    """构造受护栏约束的 client（follow_redirects=False：域名硬编码白名单，不跟随跳转）。"""
    return httpx.AsyncClient(
        follow_redirects=False,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    )
