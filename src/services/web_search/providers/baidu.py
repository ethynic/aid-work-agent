"""百度 V2 独立网页搜索；只检索，不调用生成式回答或自动回退。"""

import asyncio
import math
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx
from loguru import logger

from ..domains import normalize_domains, matches_domain
from ..models import SearchRequest

SEARCH_ENDPOINT = "https://qianfan.baidubce.com/v2/ai_search/web_search"


def _failure(message: str, code: str) -> Dict[str, Any]:
    # 供应商错误正文、异常内容及请求头均不得进入日志或工具回执。
    logger.warning("百度搜索未完成，code={code}", code=code)
    return {"success": False, "error": message, "debug": code, "provider": "baidu"}


async def search_baidu(
    *, api_key: str, keyword: str, max_results: int,
    time_range: Optional[str], include_domains: Optional[List[str]],
    exclude_domains: Optional[List[str]], timeout_seconds: float,
) -> Dict[str, Any]:
    if time_range is not None and time_range not in {"day", "week", "month", "year"}:
        return _failure("搜索时间范围无效", "invalid_time_range")
    if time_range == "day":
        return _failure("百度搜索暂不支持精确最近24小时，请使用week/month/year或调整查询", "unsupported_time_range")
    try:
        included, excluded = normalize_domains(include_domains), normalize_domains(exclude_domains)
    except (ValueError, UnicodeError, AttributeError):
        return _failure("搜索域名须为不含协议、端口或路径的域名", "invalid_domains")
    if len(included) > 20:
        return _failure("百度搜索最多支持20个限定域名，请分批查询", "too_many_domains")

    body: Dict[str, Any] = {
        "messages": [{"role": "user", "content": keyword}],
        "search_source": "baidu_search_v2", "edition": "standard",
        "resource_type_filter": [{"type": "web", "top_k": max_results}],
    }
    search_filter: Dict[str, Any] = {}
    if included:
        search_filter["match"] = {"site": included}
    if excluded:
        body["block_websites"] = excluded
    if time_range:
        body["search_recency_filter"] = time_range
    if search_filter:
        body["search_filter"] = search_filter

    try:
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
                response = await client.post(SEARCH_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"}, json=body)
        if not 200 <= response.status_code < 300:
            status = response.status_code
            message = {
                401: "百度搜索鉴权失败，请检查 BAIDU_API_KEY",
                403: "百度搜索访问被拒绝，请检查搜索服务权限",
                429: "百度搜索请求受限，请稍后重试",
            }.get(status, "百度搜索服务请求失败，请稍后重试")
            return _failure(message, f"http_{status}")
        payload = response.json()
    except (TimeoutError, httpx.TimeoutException):
        return _failure("百度搜索请求超时，请稍后重试", "timeout")
    except httpx.RequestError:
        return _failure("百度搜索网络请求失败，请稍后重试", "network_error")
    except ValueError:
        return _failure("百度搜索返回了无效响应", "invalid_response")

    if not isinstance(payload, dict) or "error" in payload or payload.get("code") not in (None, "", "0", 0):
        return _failure("百度搜索未返回有效结果，请检查服务权限或调整查询", "provider_error")
    items = payload.get("references")
    if not isinstance(items, list):
        return _failure("百度搜索响应缺少结构化结果", "invalid_response")

    results, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            return _failure("百度搜索返回了无效结果格式", "invalid_response")
        if item.get("type") not in (None, "web"):
            continue
        url = item.get("url")
        if not isinstance(url, str):
            return _failure("百度搜索返回了无效来源链接", "invalid_response")
        try:
            parsed = urlsplit(url)
            host = normalize_domains([parsed.hostname or ""])[0]
            parsed.port  # 非法端口拒绝，不将畸形URL作为来源。
        except (ValueError, UnicodeError, IndexError):
            return _failure("百度搜索返回了无效来源链接", "invalid_response")
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
            return _failure("百度搜索返回了无效来源链接", "invalid_response")
        if included and not matches_domain(host, included) or excluded and matches_domain(host, excluded):
            continue
        if url in seen:
            continue
        seen.add(url)
        title = item.get("title") or ""
        content = item.get("snippet") or item.get("content") or ""
        if not isinstance(title, str) or not isinstance(content, str):
            return _failure("百度搜索返回了无效摘要格式", "invalid_response")
        score = item.get("rerank_score", 0)
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score) or not 0 <= score <= 1:
            score = 0
        result = {"title": title, "url": url, "content": content, "score": score,
                  "provider": "baidu", "content_kind": "snippet"}
        if isinstance(item.get("date"), str):
            result["published_at"] = item["date"]
        if isinstance(item.get("website"), str):
            result["source"] = item["website"]
        results.append(result)

    returned = results[:max_results]
    logger.info("百度搜索成功，返回{count}条结果", count=len(returned))
    return {"success": True, "provider": "baidu", "search_engine": "baidu_search_v2",
            "search_edition": "standard", "results": returned, "count": len(returned),
            "message": f"找到{len(returned)}条相关结果（百度搜索）", "truncated": len(results) > len(returned),
            "applied_filters": {"include_domains": included, "exclude_domains": excluded, "time_range": time_range}}


class BaiduProvider:
    def __init__(self, *, api_key: str, timeout_seconds: float):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def search(self, request: SearchRequest) -> Dict[str, Any]:
        if request.topic != "general":
            return _failure("百度不支持news专用主题，请使用新闻关键词和time_range", "unsupported_topic")
        if not self.api_key or not self.api_key.strip():
            return _failure("未配置百度搜索密钥，请设置 BAIDU_API_KEY", "missing_baidu_key")
        return await search_baidu(
            api_key=self.api_key, keyword=request.keyword, max_results=request.max_results,
            time_range=request.time_range, include_domains=request.include_domains,
            exclude_domains=request.exclude_domains, timeout_seconds=self.timeout_seconds,
        )
