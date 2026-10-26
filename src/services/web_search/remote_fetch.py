"""Official Tavily Extract and DeepSeek Fetch, independent of native retrieval."""

import asyncio
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from .fetch import MAX_TEXT_CHARS, _FetchError, _validate_url

FETCH_ENDPOINT = "https://api.deepseek.com/v1/tools/fetch"
TAVILY_ENDPOINT = "https://api.tavily.com/extract"
MAX_RESPONSE_BYTES = 6 * 1024 * 1024
PROVIDER = "deepseek_fetch"
PROVENANCE_NOTICE = (
    "内容由DeepSeek Fetch提供；提供商未返回最终跳转URL、源网页抓取时间、源文件大小或PDF页数。"
    "retrieved_at仅为本次API取回时刻；最终URL未验证、内容新鲜度未知，不保证PDF完整性。"
)
TAVILY_PROVENANCE_NOTICE = PROVENANCE_NOTICE.replace("DeepSeek Fetch", "Tavily Extract")


def _failure(code: str, requested_url: str | None = None, *, provider: str = PROVIDER) -> dict:
    result = {"success": False, "error": "远端公开文档读取未完成", "debug": code, "provider": provider}
    if requested_url is not None:
        result["requested_url"] = requested_url
    return result


def _first_key(api_keys) -> str:
    if not isinstance(api_keys, (list, tuple)):
        return ""
    for value in api_keys:
        if not isinstance(value, str):
            continue
        key = value.strip()
        if key and key.isascii() and all(32 < ord(character) < 127 for character in key):
            return key
    return ""


def _placeholder(title: str, content: str) -> bool:
    heading = title.strip().lower()
    beginning = content[:2000].lower()
    challenge = (
        "just a moment", "verify you are human", "checking your browser", "attention required!",
        "enable javascript and cookies", "人机验证", "安全验证",
    )
    sale = ("this domain is for sale", "domain is for sale", "buy this domain", "domain for sale", "此域名出售", "域名出售")
    if any(marker in heading for marker in challenge + sale):
        return True
    return len(content) < 5000 and any(marker in beginning for marker in challenge + sale)


async def _request_json(endpoint: str, body: dict, key: str, timeout_seconds: float,
                        requested_url: str, provider: str) -> tuple[dict | None, dict | None]:
    """Shared bounded transport; provider-specific response shapes stay below."""
    try:
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=timeout_seconds,
            ) as client:
                async with client.stream(
                    "POST", endpoint,
                    headers={"Authorization": f"Bearer {key}", "Accept-Encoding": "identity"},
                    json=body,
                ) as response:
                    if response.status_code != 200:
                        return None, _failure(f"provider_http_{response.status_code}", requested_url, provider=provider)
                    # Reject compressed bodies before httpx starts its decoder;
                    # a decoded-byte limit alone cannot bound a compression bomb.
                    if response.headers.get("content-encoding", "identity").strip().lower() not in ("identity", ""):
                        return None, _failure("unsupported_content_encoding", requested_url, provider=provider)
                    length = response.headers.get("content-length")
                    if length and length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
                        return None, _failure("response_too_large", requested_url, provider=provider)
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                            return None, _failure("response_too_large", requested_url, provider=provider)
                        data.extend(chunk)
                    payload = json.loads(data)
        if not isinstance(payload, dict) or "error" in payload:
            return None, _failure("invalid_response", requested_url, provider=provider)
        return payload, None
    except (TimeoutError, httpx.TimeoutException):
        return None, _failure("remote_timeout", requested_url, provider=provider)
    except httpx.RequestError:
        return None, _failure("network_failed", requested_url, provider=provider)
    except Exception:
        # Do not expose provider bodies, request headers, or raw exception text.
        return None, _failure("invalid_response", requested_url, provider=provider)


def _document_result(url: str, title, content, provider: str, notice: str) -> dict:
    if not isinstance(title, str) or not isinstance(content, str) or not content.strip():
        return _failure("invalid_response", url, provider=provider)
    if len(content) > MAX_TEXT_CHARS:
        return _failure("response_too_large", url, provider=provider)
    if _placeholder(title, content):
        return _failure("invalid_response", url, provider=provider)
    return {"success": True, "provider": provider, "results": [{
        "title": title, "url": url, "requested_url": url, "content": content,
        "provider": provider, "content_kind": "provider_fetch",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "final_url": None, "final_url_verified": False, "freshness": "unknown",
        "notice": notice,
    }], "count": 1}


async def fetch_deepseek_document(url: str, *, api_keys, timeout_seconds: float) -> dict:
    """Fetch once through DeepSeek; callers perform public-DNS admission."""
    try:
        _validate_url(url)
    except _FetchError as error:
        return _failure(error.code)
    key = _first_key(api_keys)
    if not key:
        return _failure("missing_deepseek_key", url)
    payload, failure = await _request_json(FETCH_ENDPOINT, {"url": url}, key, timeout_seconds, url, PROVIDER)
    if failure:
        return failure
    return _document_result(url, payload.get("title"), payload.get("content"), PROVIDER, PROVENANCE_NOTICE)


async def fetch_tavily_document(url: str, *, api_key: str, timeout_seconds: float) -> dict:
    """Extract the full supplied document once, without query/chunk filtering."""
    provider = "tavily_extract"
    try:
        _validate_url(url)
    except _FetchError as error:
        return _failure(error.code, provider=provider)
    key = _first_key([api_key])
    if not key:
        return _failure("missing_tavily_key", url, provider=provider)
    payload, failure = await _request_json(TAVILY_ENDPOINT, {
        "urls": [url], "extract_depth": "advanced", "format": "markdown",
        "timeout": min(30, timeout_seconds),
    }, key, timeout_seconds, url, provider)
    if failure:
        return failure
    results, failed = payload.get("results"), payload.get("failed_results", [])
    if not isinstance(results, list) or not isinstance(failed, list):
        return _failure("invalid_response", url, provider=provider)
    # Extract result URLs identify the requested document, not a verified final
    # redirect URL. Accept only trivial trailing-slash normalization.
    def matches(value):
        if not isinstance(value, str):
            return False
        try:
            actual, expected = urlsplit(value), urlsplit(url)
        except ValueError:
            return False
        return (actual.scheme == expected.scheme and actual.netloc == expected.netloc
            and actual.path.rstrip("/") == expected.path.rstrip("/")
            and actual.query == expected.query and actual.fragment == expected.fragment)

    if any(not isinstance(item, dict) or not matches(item.get("url")) for item in failed):
        return _failure("invalid_response", url, provider=provider)
    if not results:
        return _failure("extract_failed", url, provider=provider)
    if len(results) != 1 or failed or not isinstance(results[0], dict) or not matches(results[0].get("url")):
        return _failure("invalid_response", url, provider=provider)
    item = results[0]
    links = item.get("links", [])
    if not isinstance(links, list):
        return _failure("invalid_response", url, provider=provider)
    content = item.get("raw_content")
    if links:
        if not isinstance(content, str) or not content.strip():
            return _failure("invalid_response", url, provider=provider)
        for link in links:
            try:
                if isinstance(link, str) and link.startswith("mailto:"):
                    if (len(link) > 8192 or "@" not in urlsplit(link).path
                            or any(ord(c) <= 32 or ord(c) == 127 for c in link)):
                        raise _FetchError("invalid_url")
                else:
                    _validate_url(link)
            except (_FetchError, ValueError):
                return _failure("invalid_response", url, provider=provider)
        navigation = "\n\n## Provider-reported navigation links\n\n" + "\n".join(links)
        if len(content) + len(navigation) > MAX_TEXT_CHARS:
            return _failure("response_too_large", url, provider=provider)
        content += navigation
    title = item.get("title")
    result = _document_result(url, "" if title is None else title, content, provider, TAVILY_PROVENANCE_NOTICE)
    if result["success"]:
        result["results"][0]["links"] = links
    return result
