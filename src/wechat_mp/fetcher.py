"""mp 文章页抓取（WP3，设计 §3 fetch_by_url 管道 / §13.2 实测结论）。

职责边界：只做「URL → 抓取 → 页面定性」，不做限速编排（调用方 service 负责），
本模块仅提供实例级最小间隔控制（默认 ≥1s）。

安全约束（高风险 SSRF 面）：
- 仅允许 mp.weixin.qq.com；重定向逐跳校验 host，禁内网/回环/IP 字面量/userinfo
- 浏览器 UA、无登录态（不带 cookie）、timeout 15s、单页 ≤8MB（流式截断）
- 响应正文与 httpx 异常详情不直接写日志，只记错误类别与脱敏摘要

页面定性多信号（设计 §13.2 删除实测结论）：
- deleted：专用错误页 DOM + js_content 缺失 + 明确删除文案 **三者共同命中**
  （防止正文引用该句误删）
- risk_blocked：验证页/环境异常（secitptpage/verify 等），**绝不判删除**
- fetch_failed：其他一切抓取失败（网络/非 200/页面过大/结构不识别）
"""
from __future__ import annotations

import ipaddress
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional
from urllib.parse import urljoin, urlsplit

import httpx
from loguru import logger

from src.wechat_mp.identity import MP_HOST, URLIdentityError, normalize_url

DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_REDIRECTS = 5
MIN_REQUEST_INTERVAL_SECONDS = 1.0
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

STATUS_OK = "ok"
STATUS_DELETED = "deleted"
STATUS_RISK_BLOCKED = "risk_blocked"
STATUS_FETCH_FAILED = "fetch_failed"

_REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# 明确删除文案（设计 §13.2：须与错误页 DOM、js_content 缺失共同命中）
DELETED_PHRASES = (
    "该内容已被发布者删除",
    "此内容已被发布者删除",
    "该内容已被删除",
    "此内容因违规无法查看",
    "该内容因违规无法查看",
)
# 错误页专用 DOM 结构信号（新老两套样式）
_ERROR_DOM_MARKERS = (
    "weui-msg__icon-area",   # 新样式 weui-msg 错误页
    "icon_msg warn",         # 旧样式 page_msg 错误页
    "page_msg minipage",
    'id="js_error"',         # 部分错误页容器
)
# 验证页/环境异常结构信号（WP3 真实夹具 verify_page_real.html：PAGE_MID='mmbizwap:secitptpage/verify.html'）
_VERIFY_STRUCTURAL_MARKERS = (
    "secitptpage/verify",
    "mmbizwap:secitptpage",
    "poc_token",
)
# 验证页通用文案：单独出现不足以判 risk_blocked（正常文章正文可能讨论风控），
# 只在结构信号同现且无 js_content 时作为佐证（CR P1-2）
_VERIFY_TEXT_MARKERS = (
    "环境异常",
    "完成验证后即可继续访问",
    "访问过于频繁",
)
_VERIFY_MARKERS = _VERIFY_STRUCTURAL_MARKERS + _VERIFY_TEXT_MARKERS
_JS_CONTENT_MARKER = 'id="js_content"'


class PageTooLargeError(Exception):
    """单页超过 MAX_PAGE_BYTES（流式截断中止）"""


@dataclass
class FetchResult:
    status: str                       # ok/deleted/risk_blocked/fetch_failed
    html: Optional[str] = None
    final_url: Optional[str] = None
    http_status: Optional[int] = None
    error: Optional[str] = None       # 脱敏后的错误类别摘要
    evidence: Dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def classify_page(html: str, http_status: Optional[int] = None) -> FetchResult:
    """页面定性判别（删除多信号，单拎函数便于测试与复核任务复用）。

    返回结构化结果：status + 各信号证据字段。网络层失败不走这里（fetch 直接返回）。
    """
    evidence: Dict[str, object] = {
        "http_status": http_status,
        "has_js_content": _JS_CONTENT_MARKER in html,
        "error_page_dom": any(m in html for m in _ERROR_DOM_MARKERS),
        "deleted_phrase": any(p in html for p in DELETED_PHRASES),
        "verify_page": any(m in html for m in _VERIFY_MARKERS),
        "verify_structural": any(m in html for m in _VERIFY_STRUCTURAL_MARKERS),
    }
    if http_status is not None and http_status != 200:
        return FetchResult(
            status=STATUS_FETCH_FAILED, http_status=http_status,
            error=f"http_{http_status}", evidence=evidence,
        )
    # deleted 分支在 verify 之前：删除三信号（错误页 DOM + 文案 + 无 js_content）
    # 比验证页结构更特异；真实验证页无错误页 DOM，两分支实际互斥。
    # 若未来出现同现页面，判 deleted 并在 evidence.verify_* 保留验证证据供审计。
    if (
        not evidence["has_js_content"]
        and evidence["error_page_dom"]
        and evidence["deleted_phrase"]
    ):
        return FetchResult(status=STATUS_DELETED, http_status=http_status,
                           html=html, evidence=evidence)
    # risk_blocked 前提：无 js_content + 结构信号（通用风控文案只在结构信号同现时生效，
    # 防止正文讨论"环境异常"的正常文章被误判而永久重试——CR P1-2）
    if (
        not evidence["has_js_content"]
        and evidence["verify_structural"]
    ):
        return FetchResult(status=STATUS_RISK_BLOCKED, http_status=http_status,
                           html=html, evidence=evidence)
    if evidence["has_js_content"]:
        return FetchResult(status=STATUS_OK, http_status=http_status,
                           html=html, evidence=evidence)
    return FetchResult(
        status=STATUS_FETCH_FAILED, http_status=http_status, html=html,
        error="unrecognized_page_structure", evidence=evidence,
    )


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private or ip.is_loopback or ip.is_reserved
        or ip.is_link_local or ip.is_multicast or ip.is_unspecified
    )


def _resolve_all_public(hostname: str) -> bool:
    """DNS 解析校验：所有解析结果必须为公网 IP（防 DNS 重绑定到内网）。"""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        return False
    addrs = {info[4][0] for info in infos}
    return bool(addrs) and all(_is_public_ip(a) for a in addrs)


def _validate_hop_url(url: str, resolver=_resolve_all_public) -> Optional[str]:
    """校验单跳 URL；合法返回 None，否则返回拒收原因（脱敏）。

    resolver 可注入（测试隔离真实 DNS）；默认全量解析校验（防 DNS 重绑定）。
    """
    split = urlsplit(url)
    if split.scheme not in ("http", "https"):
        return f"scheme_{split.scheme or 'empty'}"
    if split.username or split.password:
        return "userinfo_not_allowed"
    host = (split.hostname or "").lower().rstrip(".")
    if host != MP_HOST:
        return "host_not_allowed"
    try:
        port = split.port
    except ValueError:
        return "port_invalid"
    if port not in (None, 80, 443):
        return "port_not_allowed"
    try:
        ipaddress.ip_address(host)
        return "ip_literal_not_allowed"
    except ValueError:
        pass
    if not resolver(host):
        return "dns_not_public"
    return None


class MPArticleFetcher:
    """mp 文章页抓取器：SSRF 防护 + 实例级最小间隔（同步实现）。

    同步阻塞：异步入口（API/worker）须用 asyncio.to_thread 包裹调用。
    测试可注入 ``client``（httpx.Client 兼容对象）替换真实网络，
    注入 ``resolver``（hostname → bool）隔离真实 DNS。
    """

    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_PAGE_BYTES,
        min_interval: float = MIN_REQUEST_INTERVAL_SECONDS,
        client: Optional[httpx.Client] = None,
        resolver=None,
    ):
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._min_interval = min_interval
        self._client = client
        self._owns_client = client is None
        self._resolver = resolver or _resolve_all_public
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                follow_redirects=False,  # 重定向逐跳手动校验
                headers={"User-Agent": BROWSER_UA},
            )
        return self._client

    def _throttle(self) -> None:
        with self._lock:
            # 循环睡眠至 deadline：Windows 定时器粒度下 time.sleep 可能提前返回
            deadline = self._last_request_at + self._min_interval
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(remaining)
            self._last_request_at = time.monotonic()

    def fetch(self, url: str) -> FetchResult:
        """抓取并定性一个 mp 文章 URL。任何异常都收敛为 FetchResult，不抛出。"""
        try:
            identity = normalize_url(url)
        except URLIdentityError as exc:
            return FetchResult(status=STATUS_FETCH_FAILED,
                               error=f"url_rejected:{exc}",
                               evidence={"reason": "url_rejected"})
        current = identity.fetch_url
        try:
            return self._fetch_with_redirects(current)
        except httpx.TimeoutException:
            return FetchResult(status=STATUS_FETCH_FAILED, error="timeout",
                               evidence={"reason": "timeout"})
        except httpx.HTTPError as exc:
            return FetchResult(status=STATUS_FETCH_FAILED,
                               error=f"network:{type(exc).__name__}",
                               evidence={"reason": "network_error"})
        except Exception as exc:  # noqa: BLE001 抓取管道不抛出，统一收敛
            logger.opt(exception=True).error(
                f"后端日志：wechat_mp 抓取异常: {type(exc).__name__}"
            )
            return FetchResult(status=STATUS_FETCH_FAILED,
                               error=f"internal:{type(exc).__name__}",
                               evidence={"reason": "internal_error"})

    def _fetch_with_redirects(self, url: str) -> FetchResult:
        client = self._get_client()
        redirect_chain = []
        for _ in range(MAX_REDIRECTS + 1):
            reject = _validate_hop_url(url, resolver=self._resolver)
            if reject:
                return FetchResult(status=STATUS_FETCH_FAILED,
                                   error=f"ssrf_blocked:{reject}",
                                   evidence={"reason": "ssrf_blocked",
                                             "redirect_chain": redirect_chain})
            self._throttle()
            try:
                html_or_none, http_status, location = self._request_once(client, url)
            except PageTooLargeError:
                return FetchResult(status=STATUS_FETCH_FAILED, error="page_too_large",
                                   evidence={"reason": "page_too_large",
                                             "max_bytes": self._max_bytes,
                                             "redirect_chain": redirect_chain})
            if location is not None:
                redirect_chain.append(http_status)
                url = urljoin(url, location)
                continue
            if html_or_none is None:
                # 非 200 且无 Location（_request_once 已读取响应头/状态）
                result = classify_page("", http_status)
                result.evidence["redirect_chain"] = redirect_chain
                return result
            result = classify_page(html_or_none, http_status)
            result.final_url = url
            result.evidence["redirect_chain"] = redirect_chain
            return result
        return FetchResult(status=STATUS_FETCH_FAILED, error="too_many_redirects",
                           evidence={"reason": "too_many_redirects",
                                     "redirect_chain": redirect_chain})

    def _request_once(self, client: httpx.Client, url: str):
        """单跳请求。返回 (html|None, http_status, location|None)；超限抛 PageTooLargeError。"""
        with client.stream("GET", url) as resp:
            if resp.status_code in _REDIRECT_STATUSES:
                return None, resp.status_code, resp.headers.get("location")
            if resp.status_code != 200:
                # 不读 body（无上限读取有内存风险且非 200 定性只用状态码）；
                # stream 上下文退出即释放连接
                return None, resp.status_code, None
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > self._max_bytes:
                raise PageTooLargeError()
            chunks = []
            total = 0
            for chunk in resp.iter_bytes(chunk_size=65536):
                total += len(chunk)
                if total > self._max_bytes:
                    raise PageTooLargeError()
                chunks.append(chunk)
        html = b"".join(chunks).decode("utf-8", errors="replace")
        return html, resp.status_code, None
