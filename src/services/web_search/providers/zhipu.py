"""智谱独立 Web Search API；不经 LLM 生成，也不复用模型 base_url。"""

import asyncio
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx
from loguru import logger

from ..domains import normalize_domains as _normalize_domains, matches_domain as _matches_domain
from ..models import SearchRequest


SEARCH_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/web_search"
ENGINES = {"search_std", "search_pro", "search_pro_sogou", "search_pro_quark"}
RECENCY_FILTERS = {
    "day": "oneDay", "week": "oneWeek", "month": "oneMonth", "year": "oneYear",
}


def _failure(message: str, code: str) -> Dict[str, Any]:
    # 不输出供应商原始错误正文、异常文本或请求头，它们可能包含凭据。
    logger.warning("智谱搜索未完成，code={code}", code=code)
    return {"success": False, "error": message, "debug": code, "provider": "zhipu"}


async def search_zhipu(
    *,
    api_key: str,
    keyword: str,
    max_results: int,
    engine: str,
    search_depth: str,
    time_range: Optional[str],
    include_domains: Optional[List[str]],
    exclude_domains: Optional[List[str]],
    timeout_seconds: float,
) -> Dict[str, Any]:
    """返回与 web_search 兼容的结构化结果，域名约束在返回前再次校验。"""
    if engine not in ENGINES:
        return _failure("智谱搜索引擎配置无效", "invalid_engine")
    if len(keyword) > 70:
        return _failure("智谱搜索关键词最多70个字符，请拆成简短关键词搜索", "query_too_long")
    if time_range is not None and time_range not in RECENCY_FILTERS:
        return _failure("搜索时间范围无效", "invalid_time_range")
    try:
        included = _normalize_domains(include_domains)
        excluded = _normalize_domains(exclude_domains)
    except (ValueError, UnicodeError, AttributeError):
        return _failure("搜索域名须为不含协议、端口或路径的域名", "invalid_domains")

    # 多域名/排除域名没有对应原生字段，扩大候选后严格过滤，不拼接伪过滤语法。
    candidate_count = 50 if len(included) > 1 or excluded else max_results
    body = {
        "search_query": keyword,
        "search_engine": engine,
        "search_intent": False,
        "content_size": "high" if search_depth == "advanced" else "medium",
    }
    if engine != "search_pro_quark":
        body["count"] = (
            ((candidate_count + 9) // 10) * 10
            if engine == "search_pro_sogou" else candidate_count
        )
        if len(included) == 1:
            body["search_domain_filter"] = included[0]
    if time_range:
        body["search_recency_filter"] = RECENCY_FILTERS[time_range]

    try:
        # 总时限也覆盖响应读取；取消会退出 client context 并释放连接。
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(
                timeout=timeout_seconds, follow_redirects=False,
            ) as client:
                response = await client.post(
                    SEARCH_ENDPOINT,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=body,
                )
        if not 200 <= response.status_code < 300:
            status = response.status_code
            message = {
                401: "智谱搜索鉴权失败，请检查现有智谱 LLM 密钥",
                403: "智谱搜索访问被拒绝，请检查账号的搜索服务权限",
                429: "智谱搜索请求受限，请稍后重试",
            }.get(status, "智谱搜索服务请求失败，请稍后重试")
            return _failure(message, f"http_{status}")
        payload = response.json()
    except (TimeoutError, httpx.TimeoutException):
        return _failure("智谱搜索请求超时，请稍后重试", "timeout")
    except httpx.RequestError:
        return _failure("智谱搜索网络请求失败，请稍后重试", "network_error")
    except ValueError:
        return _failure("智谱搜索返回了无效响应", "invalid_response")

    if not isinstance(payload, dict) or "error" in payload:
        return _failure("智谱搜索未返回有效结果，请检查账号权限或调整查询", "provider_error")
    items = payload.get("search_result")
    if not isinstance(items, list):
        return _failure("智谱搜索响应缺少结构化结果", "invalid_response")

    results = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            return _failure("智谱搜索返回了无效结果格式", "invalid_response")
        url = item.get("link")
        if not isinstance(url, str):
            return _failure("智谱搜索返回了无效来源链接", "invalid_response")
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        except (ValueError, UnicodeError):
            return _failure("智谱搜索返回了无效来源链接", "invalid_response")
        if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return _failure("智谱搜索返回了无效来源链接", "invalid_response")
        if included and not _matches_domain(host, included):
            continue
        if excluded and _matches_domain(host, excluded):
            continue
        if url in seen:
            continue
        seen.add(url)
        result = {
            "title": item.get("title") or "",
            "url": url,
            "content": item.get("content") or "",
            "score": 0,  # 智谱不提供相关性分数，不能跨引擎比较此兼容缺省值。
            "provider": "zhipu",
            "content_kind": "snippet",
        }
        if isinstance(item.get("publish_date"), str):
            result["published_at"] = item["publish_date"]
        if isinstance(item.get("media"), str):
            result["source"] = item["media"]
        results.append(result)

    returned = results[:max_results]
    logger.info("智谱搜索成功，engine={engine}，返回{count}条结果", engine=engine, count=len(returned))
    return {
        "success": True,
        "provider": "zhipu",
        "search_engine": engine,
        "results": returned,
        "count": len(returned),
        "message": f"找到{len(returned)}条相关结果（智谱搜索）",
        "truncated": len(results) > len(returned),
        "applied_filters": {"include_domains": included, "exclude_domains": excluded, "time_range": time_range},
    }


class ZhipuProvider:
    def __init__(self, *, api_keys: List[str], engine: str, timeout_seconds: float):
        self.api_key = api_keys[0] if api_keys else ""
        self.engine = engine
        self.timeout_seconds = timeout_seconds

    async def search(self, request: SearchRequest) -> Dict[str, Any]:
        if request.topic != "general":
            return _failure("智谱不支持news专用主题，请使用新闻关键词和time_range", "unsupported_topic")
        if not self.api_key:
            return _failure("未配置智谱 LLM 密钥，请设置 ZHIPU_API_KEYS", "missing_zhipu_key")
        return await search_zhipu(
            api_key=self.api_key, keyword=request.keyword, max_results=request.max_results,
            engine=self.engine, search_depth=request.search_depth, time_range=request.time_range,
            include_domains=request.include_domains, exclude_domains=request.exclude_domains,
            timeout_seconds=self.timeout_seconds,
        )
