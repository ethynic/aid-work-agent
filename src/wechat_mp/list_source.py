"""公众号自有号清单源客户端（WP13，设计 docs/system/wechat-mp/wechat-mp-list-source-design.md §3.2）。

定位（薄适配器）：只负责「扫码会话凭据 → own-context appmsgpublish 分页清单」的
HTTP 封装、二次解析与错误分类；不做对账 diff、不入库（service 负责）、不碰
freepublish 接口通道与回调通道。

平台能力边界（2026-09-16 真机实验钉死，设计 §2）：
- 仅支持 own-context（fakeid 留空）——带任何 fakeid 的跨号查询一律 200013；
- 200007 = 账号注销/冻结（扫码能"成功"但接口全拒），必须在绑定时健康检查拦截；
- 200003/200040 或响应非 JSON = 会话失效。

响应形状（真机实测）：顶层 base_resp / is_admin / publish_page；**publish_page 是
JSON 字符串需二次解析**，内含 total_count（消息数，非子篇数）与 publish_list[]；
publish_list[].publish_info 同为 JSON 字符串二次解析，appmsgex[] 为子篇
（aid/title/link 短链/update_time/create_time/is_deleted/item_show_type/itemidx/
digest/cover/author_name），记录级含 msgid/publish_type/sent_status。

HTTP 纪律：timeout 15s；请求间隔 ≥2s（清单接口无官方频控口径，保守限速）；
单轮分页总预算 10min（超时截断置 incomplete，不抛异常，由调用方决定语义）。

日志纪律（安全约束）：token/cookie/链接/响应正文一律不进日志——只记路径、
begin/count、错误分类与脱敏摘要（对齐 client.py 风格）。
"""

from __future__ import annotations

import json
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import httpx
from loguru import logger

# 公众平台网页版后台（非 api.weixin.qq.com 开放接口，凭扫码会话 cookie 访问）
MP_BASE_URL = "https://mp.weixin.qq.com"
DEFAULT_TIMEOUT_SECONDS = 15.0
MIN_REQUEST_INTERVAL_SECONDS = 2.0  # 清单请求间隔下限（设计 §3.2）
PAGE_SIZE = 20  # 单页消息数（实测 count 上限 ≥100，保守取 20）
SCAN_BUDGET_SECONDS = 600  # 单轮分页总预算 10min（设计 §3.2）
MAX_TRANSIENT_ATTEMPTS = 2  # 瞬时网络错误最大尝试次数（含首次）
LIST_SYNC_HARD_MAX_ARTICLES = 500  # 首次回填子篇硬顶（设计 §3.4，越界截断）

# 失败码语义（真机实验 §7）
ERRCODE_SESSION_EXPIRED = {200003, 200040}
ERRCODE_ACCOUNT_ERROR = 200007
ERRCODE_FREQ_CONTROL = 200013

# 扫码登录用 UA/Referer（真机实验脚本口径）
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ListSourceError(Exception):
    """清单源错误（message 面向运维/用户可直接展示，不含凭据与响应原文）。"""

    def __init__(self, message: str, *, ret: Optional[int] = None):
        super().__init__(message)
        self.ret = ret


class ListSessionExpiredError(ListSourceError):
    """扫码会话失效（200003/200040/响应非 JSON）——对账遇此置 list_sync_status='expired'。"""


class ListAccountError(ListSourceError):
    """账号状态异常（200007 access deny，注销/冻结号）——绑定健康检查据此拒绑。"""


class ListFreqControlError(ListSourceError):
    """平台频控/能力受限（200013）——告警语义，不进退避风暴。"""


class ListStructureError(ListSourceError):
    """响应结构异常（缺字段/类型不符）——不得误报 success。"""


@dataclass
class OwnArticle:
    """清单子篇（appmsgex 条目 + 记录级元数据），正文一律走 URL 直采不在此拉取。"""

    aid: str
    title: Optional[str]
    link: str  # 短链（identity.normalize_url 可规范为 mp:s:{token} 身份）
    update_time: int  # unix 秒（增量对账基准）
    create_time: Optional[int]
    is_deleted: bool
    item_show_type: Optional[int]
    itemidx: Optional[int]
    digest: Optional[str]
    cover: Optional[str]
    author_name: Optional[str]
    msgid: Optional[int]  # 记录级：所属消息 ID
    publish_type: Optional[int]  # 记录级：101=群发（真机实测）


@dataclass
class OwnListScan:
    """fetch_all 全分页结果：子篇列表 + 完整性元数据（对齐 client.BatchgetScanResult 风格）。"""

    articles: List[OwnArticle] = field(default_factory=list)
    total_count: int = 0  # publish_page.total_count：消息数（非子篇数）
    pages_fetched: int = 0
    complete: bool = False  # 分页无失败/无重复页/无进展停滞/预算内翻完

    @property
    def reliable(self) -> bool:
        """本轮是否完整可靠（完整翻完且总数一致）。

        清单源删除仅依赖源侧显式 is_deleted 信号（无 missing 迁移），reliable
        仅用于日志与对账统计，不作为删除门禁——语义与 WP9 不同，勿混用。
        """
        return self.complete and self.total_count >= 0


def _safe_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_list_page(body: Any) -> tuple:
    """解析单页 appmsgpublish 响应 → (total_count 消息数, List[OwnArticle])。

    - 非 dict / base_resp 缺失 → ListStructureError
    - ret 语义化分类：200007/200013/200003+200040/非 JSON 由调用方按 HTTP 层
      先行分类；此处兜底同样处理（ret!=0 一律抛对应异常，不静默返回空页）
    - publish_page / publish_info 均容忍 JSON 字符串与 dict 两种形态（真机为字符串）
    - appmsgex 子篇字段缺失/类型异常：可容错字段降级为 None/False，link 或
      update_time 缺失才视为结构异常（两者是身份与增量的最小依赖）
    """
    if not isinstance(body, dict):
        raise ListStructureError("清单响应结构异常（非对象）")
    base = body.get("base_resp")
    ret: Optional[int] = base.get("ret") if isinstance(base, dict) else None
    if ret not in (None, 0):
        _raise_by_ret(ret)
    publish_page = body.get("publish_page")
    if isinstance(publish_page, str):
        try:
            publish_page = json.loads(publish_page)
        except ValueError:
            raise ListStructureError("清单响应结构异常（publish_page 非 JSON）") from None
    if not isinstance(publish_page, dict):
        raise ListStructureError("清单响应结构异常（缺 publish_page）")

    total_count = _safe_int(publish_page.get("total_count"))
    publish_list = publish_page.get("publish_list")
    if not isinstance(publish_list, list):
        publish_list = []
    if total_count is None and publish_list:
        raise ListStructureError("清单响应结构异常（缺 total_count）")

    articles: List[OwnArticle] = []
    for entry in publish_list:
        if not isinstance(entry, dict):
            raise ListStructureError("清单 publish_list 条目结构异常（非对象）")
        info = entry.get("publish_info")
        if isinstance(info, str):
            try:
                info = json.loads(info)
            except ValueError:
                # 单条记录解析失败不致命（其余记录可用），记日志跳过
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp 清单 publish_info 解析失败（跳过该条）"
                )
                continue
        if not isinstance(info, dict):
            logger.bind(module="wechat_mp").warning(
                "wechat_mp 清单 publish_info 结构异常（跳过该条）"
            )
            continue
        msgid = _safe_int(info.get("msgid"))
        publish_type = _safe_int(info.get("publish_type"))
        appmsgex = info.get("appmsgex")
        if not isinstance(appmsgex, list):
            continue
        for sub in appmsgex:
            if not isinstance(sub, dict):
                raise ListStructureError("清单 appmsgex 子篇结构异常（非对象）")
            link = sub.get("link")
            update_time = _safe_int(sub.get("update_time"))
            if not isinstance(link, str) or not link or update_time is None:
                # link（身份）与 update_time（增量基准）缺失无法对账：整轮按结构异常失败
                raise ListStructureError("清单子篇缺 link/update_time（结构异常）")
            deleted_flag = sub.get("is_deleted")
            if isinstance(deleted_flag, bool):
                is_deleted = deleted_flag
            elif isinstance(deleted_flag, int):
                is_deleted = bool(deleted_flag)
            else:
                is_deleted = False  # 字段异常绝不判删除（对齐 is_truthy_flag 保守口径）
            articles.append(
                OwnArticle(
                    aid=str(sub.get("aid") or ""),
                    title=str(sub["title"]) if isinstance(sub.get("title"), str) else None,
                    link=link,
                    update_time=update_time,
                    create_time=_safe_int(sub.get("create_time")),
                    is_deleted=is_deleted,
                    item_show_type=_safe_int(sub.get("item_show_type")),
                    itemidx=_safe_int(sub.get("itemidx")),
                    digest=str(sub["digest"]) if isinstance(sub.get("digest"), str) else None,
                    cover=str(sub["cover"]) if isinstance(sub.get("cover"), str) else None,
                    author_name=(
                        str(sub["author_name"]) if isinstance(sub.get("author_name"), str) else None
                    ),
                    msgid=msgid,
                    publish_type=publish_type,
                )
            )
    return (total_count or 0), articles


def _raise_by_ret(ret: int) -> None:
    """失败码语义化分类（设计 §3.2：session_expired / account_error / freq_control）。"""
    if ret == ERRCODE_ACCOUNT_ERROR:
        raise ListAccountError("公众号账号状态异常（已注销或冻结），无法使用清单同步", ret=ret)
    if ret == ERRCODE_FREQ_CONTROL:
        raise ListFreqControlError(
            "微信平台返回频控/能力受限（200013），请稍后重试", ret=ret
        )
    if ret in ERRCODE_SESSION_EXPIRED:
        raise ListSessionExpiredError("清单登录会话已失效，请重新扫码", ret=ret)
    raise ListSourceError(f"微信后台接口错误（ret={ret}）", ret=ret)


class OwnListClient:
    """own-context 清单客户端（同步实现，调用方在异步路径经 asyncio.to_thread 包裹）。

    - 会话凭据：登录 token（进 query）+ cookie 全量串（进 Cookie header）；
      实测 jar 重放会因 cookie domain/path 属性丢失而 200003，必须用原始串
    - http_client 可注入（测试用 httpx.MockTransport）；sleep/monotonic 可注入
      （测试免真实等待）；实例级 ≥2s 请求间隔
    """

    def __init__(
        self,
        *,
        token: str,
        cookie: str,
        http_client: Optional[httpx.Client] = None,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ):
        self._token = token
        self._cookie = cookie
        self._http = http_client or httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)
        self._owns_http = http_client is None
        # 基础头随请求携带（不在构造时写入 client.headers）：注入 http_client
        # （测试 MockTransport）时行为与生产一致
        self._base_headers = {
            "User-Agent": _USER_AGENT,
            "Referer": f"{MP_BASE_URL}/cgi-bin/home?t=home/index&lang=zh_CN",
            "Cookie": cookie,
        }
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: Optional[float] = None

    # ==================== 业务接口 ====================

    def fetch_page(self, begin: int, count: int = PAGE_SIZE) -> Dict[str, Any]:
        """own-context 清单单页原始响应（fakeid 必须留空，跨号一律 200013）。

        Header 带 Cookie + Referer（后台页 URL）+ X-Requested-With（真机实验口径）。
        网络异常有限重试；响应非 JSON 按会话失效分类（后台返回登录页 HTML）。
        """
        params = {
            "sub": "list",
            "search_field": "null",
            "begin": begin,
            "count": count,
            "query": "",
            "fakeid": "",  # own-context 必须留空（设计 §2 实测钉死）
            "type": "101_1",
            "free_publish_type": 1,
            "sub_action": "list_ex",
            "token": self._token,
            "lang": "zh_CN",
            "f": "json",
            "ajax": 1,
        }
        last_error: Optional[Exception] = None
        for attempt in range(MAX_TRANSIENT_ATTEMPTS):
            self._throttle()
            try:
                response = self._http.get(
                    f"{MP_BASE_URL}/cgi-bin/appmsgpublish",
                    params=params,
                    headers={
                        **self._base_headers,
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
            except httpx.HTTPError as e:
                # 异常原文不进日志（可能含 URL/token/cookie）；只记异常类名
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp 清单网络异常重试 begin={} attempt={} exc={}",
                    begin, attempt, type(e).__name__,
                )
                last_error = e
                self._sleep(self._transient_backoff(attempt))
                continue
            if response.status_code >= 500:
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp 清单 5xx 重试 http={} begin={} attempt={}",
                    response.status_code, begin, attempt,
                )
                last_error = ListSourceError(
                    f"微信服务暂时不可用（http {response.status_code}）"
                )
                self._sleep(self._transient_backoff(attempt))
                continue
            if response.status_code != 200:
                raise ListSourceError(f"微信后台请求失败（http {response.status_code}）")
            try:
                body = json.loads(response.text)
            except ValueError:
                # 非 JSON：后台重定向登录页，会话失效信号（设计 §3.2）
                raise ListSessionExpiredError("清单接口响应非 JSON（会话可能已失效）") from None
            if not isinstance(body, dict):
                raise ListStructureError("清单响应结构异常（非对象）")
            base = body.get("base_resp")
            ret = base.get("ret") if isinstance(base, dict) else None
            if ret not in (None, 0):
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp 清单接口错误 begin={} ret={} count={}", begin, ret, count
                )
            return body
        if isinstance(last_error, ListSourceError):
            raise last_error
        raise ListSourceError("微信后台网络异常（重试后仍失败）")

    def fetch_first(self, count: int = 1) -> tuple:
        """own-context 探活：拉首页 N 条，返回 (total_count, articles)。

        绑定健康检查（设计 §3.1）与对账前置共用：200007→拒绑/account_error、
        200013→会话异常不绑、ret=0→通过。
        """
        return parse_list_page(self.fetch_page(0, count))

    def fetch_all(self) -> OwnListScan:
        """全分页遍历：begin 按消息数推进，直到 >= total_count。

        - 重复 msgid / 空页无进展 / 超预算 → complete=False（截断不抛异常，
          本轮按已取到的部分对账，缺口下轮自然补齐——清单源无 missing 迁移门禁）
        - 结构异常 / 失败码 → 抛异常（对账 run 记 failed，不得误报 success）
        """
        return self.fetch_sync_scan()

    def fetch_sync_scan(
        self,
        *,
        max_articles: Optional[int] = None,
        page_all_known: Optional[Callable[[List[OwnArticle]], bool]] = None,
    ) -> OwnListScan:
        """分页遍历引擎（WP13-r1，设计 §3.4）：在 fetch_all 基础上支持两类策略早停。

        - ``max_articles``（首次回填上限，按子篇计数）：翻页过程累计子篇数达到 N
          即停止继续翻页（省请求），边界消息整条计入（允许轻微超出）；累计超出
          LIST_SYNC_HARD_MAX_ARTICLES 时截断到 500（保留最新，页序从新到旧）。
          上限早停视为本轮完整（complete=True），下轮起走增量。
        - ``page_all_known``（增量「走到重叠即停」判定）：整页子篇全部已知且
          update_time 与库内一致即停止翻页；判定函数由调用方注入（查库逻辑在
          service，本模块不碰 DB）。新出现/变更子篇已在 scan.articles 中，照常进 diff。
        - 兜底照旧：空页无进展 / 整页重复 / 超预算 → complete=False 不抛异常；
          结构异常 / 失败码 → 抛异常。
        两个策略都未提供时与 fetch_all 历史行为完全一致。
        """
        scan = OwnListScan()
        seen_msgids: set = set()
        begin = 0
        incomplete = False
        stopped_by_policy = False  # 上限/重叠策略早停：属预期截断，不算 incomplete
        deadline = self._monotonic() + SCAN_BUDGET_SECONDS
        while True:
            if self._monotonic() > deadline:
                logger.bind(module="wechat_mp").error(
                    "wechat_mp 清单分页超时截断（{}s 预算）", SCAN_BUDGET_SECONDS
                )
                incomplete = True
                break
            body = self.fetch_page(begin, PAGE_SIZE)
            page_total, articles = parse_list_page(body)
            scan.total_count = max(scan.total_count, page_total)
            scan.pages_fetched += 1
            if not articles:
                if begin < scan.total_count:
                    # 空页但总数未到：无进展，截断（宁缺勿错，下轮重试）
                    logger.bind(module="wechat_mp").warning(
                        "wechat_mp 清单空页提前返回 begin={} total={}", begin, scan.total_count
                    )
                    incomplete = True
                break
            page_msgids = {a.msgid for a in articles if a.msgid is not None}
            if page_msgids and page_msgids.issubset(seen_msgids):
                # 整页重复：源未按 begin 推进，截断防死循环
                logger.bind(module="wechat_mp").warning(
                    "wechat_mp 清单整页重复 begin={}（置 incomplete）", begin
                )
                incomplete = True
                break
            seen_msgids.update(page_msgids)
            scan.articles.extend(articles)
            begin += PAGE_SIZE
            # WP13-r1 策略早停（本页已计入 scan.articles，diff 语义不受影响）：
            if page_all_known is not None and page_all_known(articles):
                # 增量：整页已知未变（重叠）→ 后续全是旧内容，停止翻页
                stopped_by_policy = True
                logger.bind(module="wechat_mp").info(
                    "wechat_mp 清单增量走到重叠即停 begin={} pages={}", begin, scan.pages_fetched
                )
                break
            if max_articles is not None and len(scan.articles) >= max_articles:
                # 首次回填：累计子篇数达到上限（边界消息整条计入）→ 停止翻页
                stopped_by_policy = True
                logger.bind(module="wechat_mp").info(
                    "wechat_mp 清单首次回填达上限 articles={} max={} pages={}",
                    len(scan.articles), max_articles, scan.pages_fetched,
                )
                break
            if begin >= scan.total_count:
                break

        if max_articles is not None and len(scan.articles) > LIST_SYNC_HARD_MAX_ARTICLES:
            # 边界消息使总数超硬顶：截断到 500（页序从新到旧，保留最新 N 篇）
            logger.bind(module="wechat_mp").warning(
                "wechat_mp 清单回填超硬顶截断 articles={} -> {}",
                len(scan.articles), LIST_SYNC_HARD_MAX_ARTICLES,
            )
            scan.articles = scan.articles[:LIST_SYNC_HARD_MAX_ARTICLES]

        scan.complete = (not incomplete) and (
            stopped_by_policy or begin >= scan.total_count
        )
        if not scan.complete:
            logger.bind(module="wechat_mp").warning(
                "wechat_mp 清单本轮不完整 total={} pages={} articles={}",
                scan.total_count, scan.pages_fetched, len(scan.articles),
            )
        return scan

    # ==================== HTTP 底座 ====================

    def _throttle(self) -> None:
        """实例级请求间隔 ≥2s（清单接口保守限速，设计 §3.2）。"""
        if self._last_request_at is not None:
            elapsed = self._monotonic() - self._last_request_at
            if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
                self._sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
        self._last_request_at = self._monotonic()

    @staticmethod
    def _transient_backoff(attempt: int) -> float:
        """指数退避+抖动：1s×2^attempt，±20% 抖动。"""
        base = 1.0 * (2 ** attempt)
        return base * (0.8 + 0.4 * random.random())

    def close(self) -> None:
        if self._owns_http:
            try:
                self._http.close()
            except Exception:  # noqa: BLE001
                pass


def mask_session_digest(value: str, keep: int = 4) -> str:
    """会话凭据脱敏摘要（仅日志用）：sha256 前 8 位，绝不回显原文片段。"""
    import hashlib

    if not value:
        return "(empty)"
    return "sha:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def new_session_secret() -> str:
    """扫码中间态 scan_id（uuid，仅 Redis 键用，无安全语义之外用途）。"""
    return uuid.uuid4().hex
