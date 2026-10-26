"""DeepSeek 独立 Search API；固定官方端点，不调用模型或自动切源。"""

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from urllib.parse import urlsplit

import httpx
from loguru import logger

from ..domains import matches_domain, normalize_domains
from ..models import SearchRequest

SEARCH_ENDPOINT = "https://api.deepseek.com/v1/tools/search"
RECENCY_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _failure(message: str, code: str) -> Dict[str, Any]:
    # 原始错误正文、异常文本及请求头可能含密钥，不写入日志或回执。
    logger.warning("DeepSeek搜索未完成，code={code}", code=code)
    return {"success": False, "error": message, "debug": code, "provider": "deepseek"}


class DeepSeekProvider:
    def __init__(self, *, api_keys: List[str], timeout_seconds: float):
        self.api_key = api_keys[0] if api_keys else ""
        self.timeout_seconds = timeout_seconds

    async def search(self, request: SearchRequest) -> Dict[str, Any]:
        if request.topic != "general":
            return _failure("DeepSeek不支持news专用主题，请使用新闻关键词和time_range", "unsupported_topic")
        if not self.api_key or not self.api_key.strip():
            return _failure("未配置DeepSeek LLM密钥，请设置 DEEPSEEK_API_KEYS", "missing_deepseek_key")
        if request.time_range is not None and request.time_range not in RECENCY_DAYS:
            return _failure("搜索时间范围无效", "invalid_time_range")
        if request.search_depth not in {"basic", "advanced"}:
            return _failure("搜索深度无效", "invalid_search_depth")
        try:
            included = normalize_domains(request.include_domains)
            excluded = normalize_domains(request.exclude_domains)
        except (ValueError, UnicodeError, AttributeError):
            return _failure("搜索域名须为不含协议、端口或路径的域名", "invalid_domains")

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=RECENCY_DAYS[request.time_range]) if request.time_range else None
        query_parts = [request.keyword.strip()]
        if included:
            query_parts.append(" OR ".join(f"site:{domain}" for domain in included))
        query_parts.extend(f"-site:{domain}" for domain in excluded)
        if cutoff:
            # after 仅支持日期；放宽一日召回候选，返回前按带时区的发布时间核验。
            query_parts.append(f"after:{(cutoff - timedelta(days=1)).date().isoformat()}")
        query = " ".join(query_parts)
        if not request.keyword.strip():
            return _failure("请提供搜索关键词", "invalid_query")
        if len(query) > 128:
            return _failure("DeepSeek搜索词含域名和时间条件最多128字符，请缩短关键词或减少域名", "query_too_long")

        body = {
            "query": query,
            "count": 50 if included or excluded or cutoff else request.max_results,
            "need_content": request.search_depth == "advanced",
        }
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False) as client:
                    response = await client.post(
                        SEARCH_ENDPOINT, headers={"Authorization": f"Bearer {self.api_key}"}, json=body,
                    )
            if response.status_code != 200:
                status = response.status_code
                message = {
                    401: "DeepSeek搜索鉴权失败，请检查 DEEPSEEK_API_KEYS",
                    402: "DeepSeek搜索账号余额不足，请充值后重试",
                    403: "DeepSeek搜索访问被拒绝，请检查账号权限",
                    429: "DeepSeek搜索请求受限，请稍后重试",
                }.get(status, "DeepSeek搜索服务请求失败，请稍后重试")
                return _failure(message, f"http_{status}")
            payload = response.json()
        except (TimeoutError, httpx.TimeoutException):
            return _failure("DeepSeek搜索请求超时，请稍后重试", "timeout")
        except httpx.RequestError:
            return _failure("DeepSeek搜索网络请求失败，请稍后重试", "network_error")
        except ValueError:
            return _failure("DeepSeek搜索返回了无效响应", "invalid_response")

        if not isinstance(payload, dict) or "error" in payload:
            return _failure("DeepSeek搜索未返回有效结果，请检查账号权限或调整查询", "provider_error")
        items = payload.get("results")
        if not isinstance(items, list):
            return _failure("DeepSeek搜索响应缺少结构化结果", "invalid_response")

        results, seen = [], set()
        for item in items:
            if not isinstance(item, dict):
                return _failure("DeepSeek搜索返回了无效结果格式", "invalid_response")
            url = item.get("url")
            if not isinstance(url, str):
                return _failure("DeepSeek搜索返回了无效来源链接", "invalid_response")
            try:
                parsed = urlsplit(url)
                host = normalize_domains([parsed.hostname or ""])[0]
                parsed.port  # 畸形端口不得作为可引用来源。
            except (ValueError, UnicodeError, IndexError):
                return _failure("DeepSeek搜索返回了无效来源链接", "invalid_response")
            if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
                return _failure("DeepSeek搜索返回了无效来源链接", "invalid_response")
            if included and not matches_domain(host, included) or excluded and matches_domain(host, excluded):
                continue
            if cutoff:
                try:
                    published = datetime.fromisoformat(item.get("date") or "")
                except (TypeError, ValueError):
                    continue
                if published.tzinfo is None or not cutoff <= published <= now:
                    continue
            if url in seen:
                continue
            seen.add(url)
            title = item.get("title") or ""
            content_kind, content = "snippet", item.get("snippet") or ""
            if item.get("summary"):
                content_kind, content = "summary", item["summary"]
            if body["need_content"] and item.get("content"):
                content_kind, content = "full_text", item["content"]
            if not isinstance(title, str) or not isinstance(content, str):
                return _failure("DeepSeek搜索返回了无效内容格式", "invalid_response")
            score = item.get("rerank_score", 0)
            if not isinstance(score, (int, float)) or isinstance(score, bool) or not 0 <= score <= 3 or not math.isfinite(score):
                score = 0
            result = {"title": title, "url": url, "content": content, "score": score,
                      "provider": "deepseek", "content_kind": content_kind}
            if isinstance(item.get("date"), str):
                result["published_at"] = item["date"]
            if isinstance(item.get("site_name"), str):
                result["source"] = item["site_name"]
            authority = item.get("authority")
            if isinstance(authority, int) and not isinstance(authority, bool) and -1 <= authority <= 4:
                result["authority"] = authority
            results.append(result)

        returned = results[:request.max_results]
        filters = {"include_domains": included, "exclude_domains": excluded, "time_range": request.time_range}
        if cutoff:
            filters.update({"published_after": cutoff.isoformat(), "published_before": now.isoformat(),
                            "date_verified": True})
        logger.info("DeepSeek搜索成功，返回{count}条结果", count=len(returned))
        return {"success": True, "provider": "deepseek", "results": returned, "count": len(returned),
                "message": f"找到{len(returned)}条相关结果（DeepSeek搜索）",
                "truncated": len(results) > len(returned), "applied_filters": filters}
