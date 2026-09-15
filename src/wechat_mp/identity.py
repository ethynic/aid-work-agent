"""公众号文章 URL 规范化与身份算法（设计 §5.4，WP3）。

三字段分离：
- original_url：收到的原始链接（不改动，仅入库留痕）
- fetch_url：实际抓取用（规范化为 https + 固定参数顺序，保留 chksm）
- external_id：规范身份
  - 短链 ``mp.weixin.qq.com/s/{token}`` → ``mp:s:{token}``
  - 长链 ``/s?__biz=..&mid=..&idx=..&sn=..`` → ``mp:q:{__biz}|{mid}|{idx}|{sn}``
    仅四参数进入身份；跟踪参数（scene/sessionid/clicktime/pass_ticket 等）一律剔除。

chksm 是四定位参数的签名参数、非用户跟踪参数：不进身份，但保留在 fetch_url
（部分链接缺 chksm 仍可访问，保留可提高抓取成功率，不影响撞键）。

拒收：非 mp.weixin.qq.com 域、/s 之外路径、短链 token 非法、长链缺定位参数。

别名解析：mp 文章页实测无 <link rel="canonical">（WP3 夹具确认），
权威来源是页面内 ``var msg_link = "..."``（HTML 转义 &amp; 需反转义）；
canonical 仅作 msg_link 缺失/非法时的兜底尝试。
**实测注意（WP3 两个真实夹具）**：msg_link 均回显**自身形态**而非对方形态
（短链页给短链、长链页给长链），短↔长跨形态别名目前缺页面证据；
别名收敛仅在页面给出对方形态证据时生效，否则按 unconfirmed 保守处理（WP5 实现注意）。
"""
from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit

MP_HOST = "mp.weixin.qq.com"
LONG_FORM_PARAMS = ("__biz", "mid", "idx", "sn")
# 保留进 fetch_url 但不进身份的签名参数
FETCH_ONLY_PARAMS = ("chksm",)

_SHORT_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{4,128}$")
_MSG_LINK_RE = re.compile(r"""var\s+msg_link\s*=\s*["']([^"']+)["']""")
_LINK_TAG_RE = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_REL_CANONICAL_RE = re.compile(r"""rel\s*=\s*["']canonical["']""", re.IGNORECASE)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


class URLIdentityError(ValueError):
    """URL 拒收：非 mp 域或缺定位参数，消息面向调用方可直接展示"""


@dataclass(frozen=True)
class URLIdentity:
    original_url: str
    fetch_url: str
    external_id: str
    form: str  # 'short' | 'long'


def normalize_url(raw_url: str) -> URLIdentity:
    """规范化公众号文章 URL，返回身份三元组；不合法抛 URLIdentityError。"""
    if not raw_url or not raw_url.strip():
        raise URLIdentityError("URL 为空")
    cleaned = html_module.unescape(raw_url.strip())

    if "://" not in cleaned:
        # 容错手动粘贴省略 scheme 的场景
        if cleaned.lower().startswith(MP_HOST):
            cleaned = "https://" + cleaned
        else:
            raise URLIdentityError(f"URL 缺少 scheme 且非 {MP_HOST} 链接")
    split = urlsplit(cleaned)
    if split.scheme not in ("http", "https"):
        raise URLIdentityError(f"不支持的 URL scheme: {split.scheme}")
    if split.username or split.password:
        raise URLIdentityError("URL 不允许携带 userinfo")
    host = (split.hostname or "").lower().rstrip(".")
    if host != MP_HOST:
        raise URLIdentityError(f"仅支持 {MP_HOST} 文章链接，收到: {host or '(无 host)'}")
    try:
        port = split.port
    except ValueError:
        raise URLIdentityError("端口非法") from None
    if port not in (None, 80, 443):
        raise URLIdentityError(f"非标准端口拒收: {port}")

    path = split.path
    query_pairs = parse_qsl(split.query, keep_blank_values=True)

    if path.startswith("/s/"):
        # 短链：/s/{token}，query（跟踪参数）一律不进身份与 fetch_url
        token = path[len("/s/"):]
        if not token or "/" in token or not _SHORT_TOKEN_RE.match(token):
            raise URLIdentityError("短链 token 非法")
        return URLIdentity(
            original_url=raw_url.strip(),
            fetch_url=f"https://{MP_HOST}/s/{token}",
            external_id=f"mp:s:{token}",
            form="short",
        )

    if path == "/s":
        params = dict(query_pairs)
        missing = [k for k in LONG_FORM_PARAMS if not params.get(k)]
        if missing:
            raise URLIdentityError(f"长链缺定位参数: {', '.join(missing)}")
        identity_params = [(k, params[k]) for k in LONG_FORM_PARAMS]
        fetch_params = list(identity_params)
        for k in FETCH_ONLY_PARAMS:
            if params.get(k):
                fetch_params.append((k, params[k]))
        external = "|".join(params[k] for k in LONG_FORM_PARAMS)
        return URLIdentity(
            original_url=raw_url.strip(),
            fetch_url=f"https://{MP_HOST}/s?{urlencode(fetch_params)}",
            external_id=f"mp:q:{external}",
            form="long",
        )

    raise URLIdentityError(f"不支持的 mp 路径: {path or '/'}（仅支持 /s/{'{token}'} 与 /s?__biz=.. 文章链接）")


def extract_alias_url(page_html: str) -> Optional[str]:
    """从文章页 HTML 提取另一形态链接（var msg_link 主来源，canonical 兜底）。

    优先级：msg_link 先试（实测 mp 页面无 canonical，msg_link 是权威来源），
    缺失/非法才尝试 canonical。
    返回规范化后的 URLIdentity；页面未给出可靠来源或来源本身不合法时返回 None
    （调用方据此保留 unconfirmed，不得仅凭任意 canonical 值合并）。
    """
    if not page_html:
        return None
    m = _MSG_LINK_RE.search(page_html)
    if m:
        try:
            return normalize_url(m.group(1))
        except URLIdentityError:
            pass  # msg_link 非法时继续尝试 canonical 兜底
    for tag in _LINK_TAG_RE.findall(page_html[:20000]):
        if _REL_CANONICAL_RE.search(tag):
            m = _HREF_RE.search(tag)
            if m:
                try:
                    return normalize_url(m.group(1))
                except URLIdentityError:
                    break  # canonical 不可信即放弃，不再尝试其它 canonical
    return None
