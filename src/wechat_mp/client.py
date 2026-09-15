"""微信公众号 freepublish 接口客户端（WP9，设计 §5.2/§5.3，计划 WP9 节）。

职责边界：只做「凭据 → token → batchget 分页 / getarticle 详情」的 HTTP 封装与
错误分类；不做对账 diff、不入库（service.reconcile 负责），不碰回调通道。

凭据与缓存（设计 §5.3）：
- stable_token：POST /cgi-bin/stable_token（secret 走请求体，不进 URL）；
  Redis 缓存键含 tenant_id/config_id/secret 摘要（sha256 前 8 位，secret 变更
  自然换键），TTL = expires_in - 300s（下限 60s，不固定假设 7200-余量之外）；
  账号级（tenant+config+凭据）单飞刷新锁，防止并发强刷风暴；Redis 不可用时
  降级为不缓存直取（仅缓存失效，不影响正确性）。
- 请求遇 40001/42001/40014（token 失效）→ 删缓存刷一次、业务请求重试一次，
  仅一次（避免无限循环）。

HTTP 纪律（设计 §5.3，WP0/主控者 2026-09-15 实测口径）：
- timeout 15s；请求间隔 ≥200ms（实例级最小间隔）；
- 系统繁忙(-1)/45009 限流/瞬态网络错误 → 指数退避+抖动，最多 3 次尝试；
- 权限/参数/IP 白名单错误不盲重试，直接抛错；
- errcode 53600（文章不存在）作为独立信号（ArticleNotFoundError），缺失删除
  复核依赖它；40164 → IPWhitelistError，从 errmsg 解析出口 IP 提示加白名单。

日志纪律（安全约束）：access_token、secret、响应正文、httpx 异常原文一律不进
日志——只记请求路径（不含 query）、内部错误码与脱敏摘要（对齐 fetcher 风格）。
"""

from __future__ import annotations

import hashlib
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger

API_BASE_URL = "https://api.weixin.qq.com"
DEFAULT_TIMEOUT_SECONDS = 15.0
MIN_REQUEST_INTERVAL_SECONDS = 0.2  # 请求间隔下限（设计 §5.3）
BATCHGET_PAGE_SIZE = 20  # freepublish/batchget 单页上限
PAGINATION_BUDGET_SECONDS = 600  # batchget_all 单轮总超时预算
MAX_TRANSIENT_ATTEMPTS = 3  # 瞬时错误最大尝试次数（含首次）
TRANSIENT_BACKOFF_BASE_SECONDS = 0.5

# 瞬时可重试：系统繁忙 / api 限频
TRANSIENT_ERRCODES = {-1, 45009}
# token 失效：删缓存刷一次、业务请求重试一次
TOKEN_INVALID_ERRCODES = {40001, 42001, 40014}
ERRCODE_ARTICLE_NOT_FOUND = 53600  # freepublish/getarticle：文章不存在（缺失复核信号）
ERRCODE_IP_WHITELIST = 40164  # 出口 IP 不在白名单

TOKEN_TTL_SAFETY_SECONDS = 300  # token 缓存 TTL = expires_in - 300s
TOKEN_MIN_TTL_SECONDS = 60  # TTL 下限（expires_in 异常偏小时仍可短期复用）
SINGLEFLIGHT_LOCK_TTL_SECONDS = 15
SINGLEFLIGHT_WAIT_ROUNDS = 10  # 未抢到刷新锁时等缓存的最长轮数（×0.2s）

_IP_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


class WeChatMPAPIError(Exception):
    """微信接口错误（message 面向运维/用户可直接展示，不含凭据与响应原文）。"""

    def __init__(self, errcode: Optional[int], message: str, *, http_status: Optional[int] = None):
        super().__init__(message)
        self.errcode = errcode
        self.message = message
        self.http_status = http_status


class ArticleNotFoundError(WeChatMPAPIError):
    """freepublish 文章不存在（errcode 53600）——缺失删除复核的唯一删除信号。"""

    def __init__(self, message: str = "文章在公众号侧不存在（errcode=53600）"):
        super().__init__(ERRCODE_ARTICLE_NOT_FOUND, message)


class IPWhitelistError(WeChatMPAPIError):
    """出口 IP 未加入公众平台 IP 白名单（errcode 40164）。"""

    def __init__(self, ip: Optional[str]):
        self.ip = ip
        hint = (
            f"服务器出口 IP {ip} 未加入公众平台 IP 白名单，"
            "请在「设置与开发→基本配置→IP白名单」添加后重试"
            if ip
            else "服务器出口 IP 未加入公众平台 IP 白名单"
            "（请在「设置与开发→基本配置→IP白名单」添加出口 IP 后重试）"
        )
        super().__init__(ERRCODE_IP_WHITELIST, hint)


def is_truthy_flag(value: Any) -> bool:
    """宽松解析接口布尔标记（is_deleted 等）：bool/int/float/字符串均容忍。

    空值/未知类型一律 False（绝不把结构异常当删除信号，设计 §5.2.4）。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y")
    return False


def parse_whitelist_ip(errmsg: str) -> Optional[str]:
    """从 40164 errmsg 解析出口 IP（微信格式形如 'invalid ip, not in whitelist'）。

    errmsg 不整体外泄（含 hints/req_id 等），只取首个 IPv4 形态字符串。
    """
    if not errmsg:
        return None
    m = _IP_RE.search(errmsg)
    return m.group(1) if m else None


def friendly_api_error(e: Exception) -> str:
    """把客户端异常转成面向用户的脱敏 message（verify 端点与对账失败落库共用）。"""
    if isinstance(e, WeChatMPAPIError):
        return e.message
    # 未知异常（网络层等）：绝不透传 httpx 异常原文（可能含 URL/响应片段）
    return f"微信接口调用失败（{type(e).__name__}）"


@dataclass
class BatchgetScanResult:
    """batchget_all 全分页结果：消息级 (article_id, update_time) 列表 + 完整性元数据。"""

    messages: List[Dict[str, Any]] = field(default_factory=list)
    total_count: int = 0
    fetched: int = 0
    complete: bool = False  # 分页无失败/无结构异常/无重复页/无进展停滞/总数一致

    @property
    def reliable(self) -> bool:
        """本轮对账是否完整可靠（缺失删除迁移的唯一前提，设计 §5.2.4）。"""
        return self.complete and self.fetched == self.total_count


class WeChatMPAPIClient:
    """单配置（tenant+config+appid+secret）的 freepublish 接口客户端（同步实现）。

    调用方在异步路径中一律 asyncio.to_thread 包裹（对齐 service/fetcher 惯例）。
    http_client 可注入（测试用 httpx.MockTransport），sleep/monotonic 可注入
    （测试免真实等待）。
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        config_id: str,
        appid: str,
        secret: str,
        redis: Any = None,
        http_client: Optional[httpx.Client] = None,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ):
        self._tenant_id = tenant_id
        self._config_id = config_id
        self._appid = appid
        self._secret = secret
        if redis is None:
            from src.core.redis_client import redis_client

            redis = redis_client
        self._redis = redis
        self._http = http_client or httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)
        self._owns_http = http_client is None
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: Optional[float] = None  # 实例级 200ms 最小间隔

    # ==================== token ====================

    def _secret_digest(self) -> str:
        """secret 摘要（sha256 前 8 位）：缓存键含凭据版本语义，secret 变更即换键。"""
        return hashlib.sha256(self._secret.encode("utf-8")).hexdigest()[:8]

    def _token_cache_key(self) -> str:
        return (
            f"wechat_mp_api:token:{self._tenant_id}:{self._config_id}:{self._secret_digest()}"
        )

    def _token_lock_key(self) -> str:
        return (
            f"wechat_mp_api:token_lock:{self._tenant_id}:{self._config_id}:"
            f"{self._secret_digest()}"
        )

    def get_access_token(self) -> str:
        """取 access_token：Redis 缓存 → 未命中走账号级单飞刷新（设计 §5.3）。"""
        cache_key = self._token_cache_key()
        if self._redis.is_available():
            try:
                cached = self._redis.get(cache_key)
                if isinstance(cached, str) and cached:
                    return cached
            except Exception as e:  # noqa: BLE001 缓存读失败降级直取
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp token 缓存读取失败（降级直取）: {}", type(e).__name__
                )

        lock_key = self._token_lock_key()
        lock_value = uuid.uuid4().hex
        acquired = False
        if self._redis.is_available():
            try:
                acquired = self._redis.acquire_lock(lock_key, lock_value, SINGLEFLIGHT_LOCK_TTL_SECONDS)
            except Exception:  # noqa: BLE001 锁不可用降级直取
                acquired = False
            if not acquired:
                # 其他持有者正在刷新：短暂等待其写入缓存，超时后自行直取（有限退让）
                for _ in range(SINGLEFLIGHT_WAIT_ROUNDS):
                    self._sleep(MIN_REQUEST_INTERVAL_SECONDS)
                    try:
                        cached = self._redis.get(cache_key)
                    except Exception:  # noqa: BLE001
                        break
                    if isinstance(cached, str) and cached:
                        return cached
                logger.bind(module="wechat_mp").debug(
                    "wechat_mp token 单飞锁等待超时（降级自取）config_id={}", self._config_id
                )

        try:
            token, expires_in = self._fetch_stable_token()
            ttl = max(int(expires_in) - TOKEN_TTL_SAFETY_SECONDS, TOKEN_MIN_TTL_SECONDS)
            if self._redis.is_available():
                try:
                    self._redis.set(cache_key, token, ex=ttl)
                except Exception:  # noqa: BLE001 缓存写失败仅影响下次命中
                    logger.bind(module="wechat_mp").debug("wechat_mp token 缓存写入失败（忽略）")
            return token
        finally:
            if acquired:
                try:
                    self._redis.release_lock(lock_key, lock_value)
                except Exception:  # noqa: BLE001 释放失败靠 TTL 过期兜底
                    pass

    def invalidate_token_cache(self) -> None:
        """token 失效（40001/42001/40014）时删缓存（含旧 secret 摘要的防御性清理不需要：
        键含摘要，secret 变更后旧键自然失配，靠 TTL 过期）。"""
        if not self._redis.is_available():
            return
        try:
            self._redis.delete(self._token_cache_key())
        except Exception as e:  # noqa: BLE001
            logger.bind(module="wechat_mp").debug(
                "wechat_mp token 缓存删除失败（忽略）: {}", type(e).__name__
            )

    def _fetch_stable_token(self) -> Tuple[str, int]:
        """POST /cgi-bin/stable_token（secret 走请求体）。返回 (access_token, expires_in)。"""
        body = self._request(
            "/cgi-bin/stable_token",
            payload={
                "grant_type": "client_credential",
                "appid": self._appid,
                "secret": self._secret,
            },
            with_token=False,
        )
        token = body.get("access_token")
        expires_in = body.get("expires_in")
        if not isinstance(token, str) or not token or not isinstance(expires_in, int):
            # 结构异常不含正文外泄：只报缺哪些字段
            raise WeChatMPAPIError(None, "stable_token 响应结构异常（缺 access_token/expires_in）")
        return token, expires_in

    # ==================== 业务接口 ====================

    def batchget_page(self, offset: int, count: int = BATCHGET_PAGE_SIZE, no_content: int = 1) -> Dict[str, Any]:
        """freepublish/batchget 单页原始响应（verify 端点与全分页共用）。"""
        return self._request(
            "/cgi-bin/freepublish/batchget",
            payload={"offset": offset, "count": count, "no_content": no_content},
            with_token=True,
        )

    def batchget_all(self, *, no_content: int = 1) -> BatchgetScanResult:
        """freepublish/batchget 全分页遍历（no_content=1 只取元数据不取正文）。

        - offset 按返回 item_count 推进（设计 §5.3），单页 count=20
        - 重复 article_id / 无进展 / 总数漂移 → complete=False（不抛异常，由调用方
          决定缺失迁移门禁）；单轮总超时（600s 预算）同样只截断置 incomplete
        - API 失败 / 响应结构异常（缺 item/total_count、article_id 缺失、
          update_time 不可解析）→ 抛异常（对账 run 记 failed，不得误报 success）
        """
        result = BatchgetScanResult()
        seen_ids: set = set()
        offset = 0
        incomplete = False  # 负向事件只在置位，完整性最终统一判定
        deadline = self._monotonic() + PAGINATION_BUDGET_SECONDS
        while True:
            if self._monotonic() > deadline:
                logger.bind(module="wechat_mp").error(
                    "wechat_mp batchget 全分页超时截断（{}s 预算）config_id={}",
                    PAGINATION_BUDGET_SECONDS, self._config_id,
                )
                incomplete = True
                break
            body = self.batchget_page(offset, BATCHGET_PAGE_SIZE, no_content=no_content)
            total_count = body.get("total_count")
            item_count = body.get("item_count")
            items = body.get("item")
            if (
                not isinstance(total_count, int)
                or not isinstance(item_count, int)
                or not isinstance(items, list)
            ):
                raise WeChatMPAPIError(
                    None, "batchget 响应结构异常（缺 total_count/item_count/item）"
                )
            result.total_count = total_count
            if not items:
                if offset < total_count:
                    # 返回空页但总数未到：无进展，截断置 incomplete（宁可不删不可误删）
                    logger.bind(module="wechat_mp").warning(
                        "wechat_mp batchget 空页提前返回 offset={} total_count={} config_id={}",
                        offset, total_count, self._config_id,
                    )
                    incomplete = True
                break
            advanced = 0
            for item in items:
                if not isinstance(item, dict):
                    raise WeChatMPAPIError(None, "batchget item 结构异常（非对象）")
                article_id = item.get("article_id")
                if not isinstance(article_id, str) or not article_id:
                    raise WeChatMPAPIError(None, "batchget item 结构异常（缺 article_id）")
                update_time = item.get("update_time")
                if not isinstance(update_time, int):
                    # no_content=1 时 content.update_time 兜底（实测 item 顶层必有，
                    # 此处防御字段位置漂移）
                    update_time = (item.get("content") or {}).get("update_time")
                if not isinstance(update_time, int):
                    raise WeChatMPAPIError(
                        None, f"batchget item 结构异常（update_time 不可解析 article_id={article_id[:6]}...）"
                    )
                advanced += 1
                if article_id in seen_ids:
                    # 重复页：跳过重复项并置 incomplete（缺失门禁据此不迁移）
                    logger.bind(module="wechat_mp").warning(
                        "wechat_mp batchget 重复 article_id（置 incomplete）offset={} config_id={}",
                        offset, self._config_id,
                    )
                    incomplete = True
                    continue
                seen_ids.add(article_id)
                # no_content=1 时 news_item 子项仍保留 title/url 等元数据（主控者
                # 2026-09-15 实测）；取首子篇供 articles 行 original_url/title 预填，
                # 字段缺失/类型异常不致命（正文与结构校验在 getarticle 阶段）
                content = item.get("content") if isinstance(item.get("content"), dict) else {}
                news_items = content.get("news_item")
                first = (
                    news_items[0]
                    if isinstance(news_items, list) and news_items and isinstance(news_items[0], dict)
                    else {}
                )
                first_title = first.get("title")
                first_url = first.get("url")
                create_time = content.get("create_time")
                result.messages.append(
                    {
                        "article_id": article_id,
                        "update_time": update_time,
                        "create_time": create_time if isinstance(create_time, int) else None,
                        "first_title": first_title if isinstance(first_title, str) else None,
                        "first_url": first_url if isinstance(first_url, str) else None,
                    }
                )
            offset += item_count if item_count else advanced
            if item_count == 0:
                # 计数为 0 但给了条目：按实际条目数推进已在上行处理；这里防御死循环
                incomplete = True
                break
            if offset >= total_count:
                break

        result.fetched = len(result.messages)
        # 完整可靠 = 全程无负向事件 且 实取消息数与源总数一致（总数漂移视为不可靠）
        result.complete = (not incomplete) and result.fetched == result.total_count
        if not result.complete:
            logger.bind(module="wechat_mp").warning(
                "wechat_mp batchget 本轮不完整可靠 total={} fetched={} config_id={}",
                result.total_count, result.fetched, self._config_id,
            )
        return result

    def getarticle(self, article_id: str) -> Dict[str, Any]:
        """freepublish/getarticle 文章详情（news_item 含 content 富文本 HTML）。

        返回完整响应体（顶层 news_item/create_time/update_time）；errcode 53600
        抛 ArticleNotFoundError（缺失删除复核的唯一删除信号）。news_item 空数组/
        字段缺失等语义判定由调用方（service）负责——空数组绝不判删除。
        """
        return self._request(
            "/cgi-bin/freepublish/getarticle",
            payload={"article_id": article_id},
            with_token=True,
        )

    # ==================== HTTP 底座 ====================

    def _throttle(self) -> None:
        """实例级请求间隔 ≥200ms（设计 §5.3；单飞/token 刷新共用同一节奏）。"""
        if self._last_request_at is not None:
            elapsed = self._monotonic() - self._last_request_at
            if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
                self._sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        self._last_request_at = self._monotonic()

    def _request(
        self, path: str, *, payload: Dict[str, Any], with_token: bool
    ) -> Dict[str, Any]:
        """带错误分类与有限重试的 POST（日志只记路径+错误码，无 token/正文/异常原文）。

        - 瞬时（-1/45009/5xx/网络异常）→ 指数退避+抖动，最多 MAX_TRANSIENT_ATTEMPTS 次
        - token 失效（40001/42001/40014）→ 删缓存刷一次 token、请求重试一次（仅一次）
        - 权限/参数/IP 白名单等 → 立即抛错不盲重试
        """
        token_retried = False
        last_error: Optional[Exception] = None
        for attempt in range(MAX_TRANSIENT_ATTEMPTS):
            token = self.get_access_token() if with_token else ""
            url = f"{API_BASE_URL}{path}" + (f"?access_token={token}" if with_token else "")
            self._throttle()
            try:
                response = self._http.post(url, json=payload)
            except httpx.HTTPError as e:
                # 异常原文不进日志（可能含 URL/token）；只记异常类名
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp api 网络异常重试 path={} attempt={} exc={}", path, attempt, type(e).__name__
                )
                last_error = e
                self._sleep(self._transient_backoff(attempt))
                continue

            if response.status_code >= 500:
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp api 5xx 重试 path={} http={} attempt={}",
                    path, response.status_code, attempt,
                )
                last_error = WeChatMPAPIError(
                    None, f"微信服务暂时不可用（http {response.status_code}）",
                    http_status=response.status_code,
                )
                self._sleep(self._transient_backoff(attempt))
                continue
            if response.status_code != 200:
                raise WeChatMPAPIError(
                    None, f"微信接口请求失败（http {response.status_code}）",
                    http_status=response.status_code,
                )

            try:
                body = response.json()
            except ValueError:
                raise WeChatMPAPIError(None, "微信接口响应非 JSON（结构异常）") from None
            if not isinstance(body, dict):
                raise WeChatMPAPIError(None, "微信接口响应结构异常（非对象）")

            errcode = body.get("errcode")
            if errcode in (None, 0):
                return body

            errcode = int(errcode)
            if errcode in TOKEN_INVALID_ERRCODES and with_token and not token_retried:
                # token 失效：删缓存强刷一次，业务请求重试一次（设计 §5.3 仅一次）
                logger.bind(module="wechat_mp").info(
                    "wechat_mp token 失效刷新重试 path={} errcode={} config_id={}",
                    path, errcode, self._config_id,
                )
                token_retried = True
                self.invalidate_token_cache()
                continue

            if errcode in TRANSIENT_ERRCODES:
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp api 瞬时错误重试 path={} errcode={} attempt={} config_id={}",
                    path, errcode, attempt, self._config_id,
                )
                last_error = WeChatMPAPIError(
                    errcode,
                    "微信接口系统繁忙/限流" if errcode == -1 else "微信接口限频（45009），稍后重试",
                )
                self._sleep(self._transient_backoff(attempt))
                continue

            if errcode == ERRCODE_ARTICLE_NOT_FOUND:
                raise ArticleNotFoundError()
            if errcode == ERRCODE_IP_WHITELIST:
                # errmsg 仅用于解析出口 IP，不整体入日志/异常（含 req_id 等）
                raise IPWhitelistError(parse_whitelist_ip(str(body.get("errmsg") or "")))

            # 权限/参数等确定性错误：不盲重试
            logger.bind(module="wechat_mp").warning(
                "wechat_mp api 确定性错误 path={} errcode={} config_id={}", path, errcode, self._config_id
            )
            raise WeChatMPAPIError(errcode, self._describe_errcode(errcode))

        # 瞬时重试耗尽
        if isinstance(last_error, WeChatMPAPIError):
            raise last_error
        raise WeChatMPAPIError(None, "微信接口网络异常（重试后仍失败）")

    @staticmethod
    def _transient_backoff(attempt: int) -> float:
        """指数退避+抖动：0.5s×2^attempt，±20% 抖动（设计 §5.3）。"""
        base = TRANSIENT_BACKOFF_BASE_SECONDS * (2 ** attempt)
        return base * (0.8 + 0.4 * random.random())

    @staticmethod
    def _describe_errcode(errcode: int) -> str:
        """确定性错误的用户可读描述（不透传 errmsg 原文）。"""
        descriptions = {
            40001: "AppSecret 无效或已被重置（请核对基本配置）",
            40013: "AppID 无效（请核对公众号 AppID）",
            40125: "AppSecret 无效（请核对基本配置）",
            40014: "access_token 无效（将自动刷新重试一次）",
            42001: "access_token 已过期（将自动刷新重试一次）",
            48001: "公众号无「发布」接口权限（freepublish，需认证服务号）",
            43104: "appid 与公众号不匹配",
        }
        return descriptions.get(errcode, f"微信接口错误（errcode={errcode}）")

    def close(self) -> None:
        if self._owns_http:
            try:
                self._http.close()
            except Exception:  # noqa: BLE001
                pass
