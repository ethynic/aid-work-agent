#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可移植的通用网页检索与抓取工具 —— WebSearch / WebFetch 的替代实现。

为什么需要这个脚本
------------------
智能体宿主（WorkBuddy / CodeBuddy 等）自带的 `WebSearch` / `WebFetch` 是平台内置工具，
实现封闭在宿主应用内，**无法打包分发**。换到其他智能体环境（或离线 / 内网场景）时，
这两件工具往往不存在，依赖它们的获客 / 检索流程就会整体失效。

本脚本是一个**通用**检索与抓取工具（不局限于找邮箱/电话），用公开可达的接口提供等价
能力，不依赖任何闭源服务。

核心特性
--------
1. 全球主流搜索引擎注册表：百度 / 搜狗 / 360 / Naver / Daum / Yahoo!Japan / Goo /
   Yandex / Seznam / Qwant / Parsijoo / Cốc Cốc / Swisscows / Startpage / Brave /
   Google / Bing / DuckDuckGo 等，按区域登记。
2. 智能路由（`select_engines`）：根据输入内容自动判断用哪个（哪些）引擎——
   - 文字/语种：中文→百度系；韩文→Naver/Daum；日文假名→Yahoo!Japan/Goo；
     西里尔→Yandex；其他（拉丁字母）→Google。
   - `site:<国别域名>` 提示：如 `site:.jp`→日本引擎、`site:.cn`→百度系、`site:.ru`→Yandex。
   - 始终在区域主引擎之后追加 Google / Bing / DDG 作为全球备份通道。
3. 任意引擎失败/被风控/返回无关内容都会自动降级到下一引擎，全部失败才报告错误。

噪音域名处理（分场景）
----------------------
- 搜官网（`--companies-only`）：硬过滤词典/百科/翻译等噪音域名，以及社媒/黄页/电商，
  只保留疑似企业官网的结果。
- 搜信息（默认）：噪音域名**不丢弃**，而是作为「备选」排在干净结果之后；当干净结果
  不足或完全没有时，噪音域名（如 Wikipedia / 百度百科 / 词典站）会被采用。

优先级提示：宿主自带 WebSearch / WebFetch 时应优先用宿主工具（结果更全、
不受本机网络代理影响）。本脚本是兜底通道。

安装依赖（最小化）：
```bash
pip install httpx beautifulsoup4 lxml
```

退出码：0 正常；2 网络全部失败；3 参数错误。
"""

import argparse
import json
import os
import re
import string
import sys
import time
import urllib.parse
from html import unescape

# 共享的格式化器：用于从模板中解析占位符，过滤掉未使用的 market 参数，
# 避免 str.format 因多余关键字参数抛出 KeyError。
_FMT = string.Formatter()

try:
    import httpx
except ImportError:
    sys.exit("[错误] 需要 httpx：pip install httpx")

try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit("[错误] 需要 beautifulsoup4：pip install beautifulsoup4 lxml")


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# 移动端 UA：百度移动端（m.baidu.com）风控画像与 PC 端不同，PC 被图形验证码拦截时
# 移动端往往仍可访问，因此作为 PC 端被风控时的降级通道。
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
             "Mobile/15E148 Safari/604.1")

CONNECT_TIMEOUT = 8.0          # 连接阶段短超时：不可达主机快速失败，避免单引擎拖死整次搜索
READ_TIMEOUT = 15.0             # 读取阶段：慢引擎/大页面容忍
TIMEOUT = READ_TIMEOUT
SEARCH_DEADLINE = 75.0          # 整次搜索的总预算（秒）；跨引擎尝试时在引擎间检查，避免病态挂起
# 同一引擎在「结果不相关（分词漂移）」时的最大重试次数。百度等结果非确定性，
# 一次漂移不代表该引擎无正确结果，重试（并轻微扰动 rn）常常能命中正确结果集。
# 实测约 1/3 请求会返回空/漂移页，4 次尝试可将命中率提升到 ~96%+。
RELEVANCE_RETRIES = 2
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

MOBILE_HEADERS = {
    "User-Agent": MOBILE_UA,
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 各通道限速（秒）/ 重试；避免触发风控
CHANNEL_POLICY = {
    "baidu": {"delay": 1.5, "retries": 1},
    "sogou": {"delay": 1.5, "retries": 1},
    "so": {"delay": 1.5, "retries": 1},
    "naver": {"delay": 1.5, "retries": 1},
    "daum": {"delay": 1.5, "retries": 1},
    "yahoojp": {"delay": 1.5, "retries": 1},
    "goo": {"delay": 1.5, "retries": 1},
    "yandex": {"delay": 1.5, "retries": 1},
    "seznam": {"delay": 1.5, "retries": 1},
    "qwant": {"delay": 1.5, "retries": 1},
    "parsijoo": {"delay": 1.5, "retries": 1},
    "coccoc": {"delay": 1.5, "retries": 1},
    "swisscows": {"delay": 1.5, "retries": 1},
    "startpage": {"delay": 1.5, "retries": 1},
    "brave": {"delay": 1.5, "retries": 1},
    "google": {"delay": 1.5, "retries": 1},
    "bing": {"delay": 1.0, "retries": 1},
    "ddg": {"delay": 1.5, "retries": 1},
}


# ---------------------------------------------------------------- 网络基础

# 渠道级代理：百度通道可经办公网出口代理执行（服务器 IP 被百度风控标记时使用）。
# 环境变量 WEBSEARCH_BAIDU_PROXY 形如 http://user:pass@host:port，未设置则直连。
# 其他引擎不受影响；代理连接失败自动回落直连并在 errors 中如实记录。
BAIDU_PROXY = os.environ.get("WEBSEARCH_BAIDU_PROXY", "").strip() or None
PROXY_CHANNELS = ("baidu", "baidu_m")

# 百度 cookie 预热缓存（进程级）：先访问首页获取 BAIDUID 再搜索，
# 无 cookie 裸搜会显著提高被风控概率（实测裸搜直接导 wappass 验证码页）。
_baidu_warm_cookies = None


def _channel_proxy(name):
    """返回该通道应使用的代理（未配置则 None=直连）。"""
    return BAIDU_PROXY if name in PROXY_CHANNELS else None


def _warm_baidu_cookies(verify=True, proxy=None):
    """预热百度 cookie（BAIDUID 等）。失败不抛异常，返回空 dict 走原行为。"""
    global _baidu_warm_cookies
    if _baidu_warm_cookies is not None:
        return _baidu_warm_cookies
    cookies = {}
    try:
        with _client(verify, proxy=proxy) as c:
            c.get("https://www.baidu.com/", headers=DEFAULT_HEADERS)
            cookies = dict(c.cookies)
    except Exception:
        cookies = {}
    _baidu_warm_cookies = cookies
    return _baidu_warm_cookies


def _client(verify=True, proxy=None, cookies=None):
    return httpx.Client(
        timeout=httpx.Timeout(connect=CONNECT_TIMEOUT, read=READ_TIMEOUT,
                              write=10.0, pool=CONNECT_TIMEOUT),
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
        verify=verify,
        http2=False,
        proxy=proxy,
        cookies=cookies or None,
    )


def fetch_raw(url, verify=True, headers=None, proxy=None, cookies=None):
    """抓取 URL 原始 HTML。失败抛异常由调用方处理。

    proxy/cookies 仅由调用方明确传入（当前仅百度搜索通道使用，
    避免把百度 cookie 带到无关站点）。
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    with _client(verify, proxy=proxy, cookies=cookies) as c:
        r = c.get(url, headers=headers or DEFAULT_HEADERS)
        r.raise_for_status()
        if not r.encoding or r.encoding.lower() == "iso-8859-1":
            r.encoding = r.apparent_encoding or "utf-8"
        try:
            return r.text
        except Exception:
            return r.content.decode("utf-8", errors="replace")


def _is_blocked(html):
    """检测搜索引擎是否返回了风控验证码 / 拦截页（百度安全验证、360/so 验证码等）。

    这类页面不是正常结果，继续在其上跑解析只会得到空结果或噪音；应快速判定为
    「被拦截」并降级到下一通道，而不是浪费重试次数在验证码页上。

    判定必须保守：仅当页面**确实是拦截页**时才返回 True，避免误伤正常大页面
    （正常结果页常含 `verify`/`验证` 等字样的 JS 或文案）。
    - 强信号：指向 wappass 验证码域名、标题含「百度安全验证」；
    - 弱信号：页面很短（<3KB）且含验证码字样——正常结果页通常十几 KB 以上。
    """
    if not html:
        return False
    # 真正的风控/验证码页一定是**短页面**（百度的 wappass 页约 1~3KB，
    # 360/so 的验证码页也极短）。正常结果页通常十几 KB 以上。
    # 因此只有「短页面 + 含验证码字样」才判定为拦截，避免大页面里偶发的
    # `wappass`/`验证` 等子串造成误判（移动端结果页会把验证码域名写进内嵌 JS）。
    if len(html) >= 5000:
        return False
    low = html.lower()
    markers = ("wappass.baidu.com", "百度安全验证", "安全验证",
               "验证", "captcha", "qcaptcha", "antispider")
    return any(m in html or m in low for m in markers)


def _resolve_redirect(url, verify=True):
    """解析搜索引擎的跳转链接（如百度 /link?url=、搜狗 link?url= 的 302）。

    直接用 href 会得到一堆指向搜索站的无意义 URL。这里用 HEAD 跟随重定向拿到
    最终地址；失败则原样返回。
    """
    if not url or not url.startswith("http"):
        return url
    try:
        with _client(verify) as c:
            r = c.head(url, follow_redirects=True)
            if r.url and str(r.url) != url:
                return str(r.url)
    except Exception:
        pass
    return url


# JS 重定向跟随：Cốc Cốc / Parsijoo 等首页会先返回一段设置 cookie 后
# window.location 跳转的脚本页，纯 HTTP 拿不到结果。此处提取跳转目标并跟随。
def _follow_js_redirect(html, base, verify=True, max_hops=2):
    import urllib.parse as _up
    # 只对「很小的重定向桩页」跟随（如 Cốc Cốc / Parsijoo 首页的 JS 跳转页，
    # 通常 < 1KB）。正常的几百 KB 结果页里也会含 window.location 赋值（普通 JS），
    # 不能误判为重定向，否则会把真实结果页又抓成别的页面导致解析为空。
    if html is None or len(html) > 5000:
        return html
    cur, cur_base, hops = html, base, 0
    while hops < max_hops:
        m = re.search(r"(?:window\.location(?:\.href)?|location\.href)\s*=\s*['\"]([^'\"]+)['\"]",
                      cur)
        if not m:
            m = re.search(r"<meta[^>]+http-equiv=['\"]refresh['\"][^>]+"
                          r"content=['\"][^'\"]*url=([^'\"]+)['\"]", cur, re.I)
        if not m:
            break
        target = m.group(1).strip()
        if target.startswith("//"):
            target = "https:" + target
        elif not target.startswith("http"):
            target = _up.urljoin(cur_base, target)
        try:
            cur = fetch_raw(target, verify=verify)
            cur_base = target
            hops += 1
        except Exception:
            break
    return cur if hops > 0 else html


# ---------------------------------------------------------------- 正文抽取

_DROP_TAGS = ("script", "style", "noscript", "iframe", "svg", "form", "nav", "footer")

# 部分站点（百度首页、部分 SPA）把 CSS 以文本形式留在 body 里，或以文本节点形式
# 输出 <style> 片段。直接交给 LLM 会浪费大量 token 且干扰摘要，故在正文抽取后清洗。
_CSS_BLOCK_RE = re.compile(r"<(?:style|script)[^>]*>.*?</(?:style|script)>",
                           re.S | re.I)


def _strip_css_noise(text):
    """剔除正文中的 CSS / 脚本残留。"""
    if not text:
        return text
    text = _CSS_BLOCK_RE.sub(" ", text)
    out = []
    for line in text.split("\n"):
        s = line.strip()
        # 典型 CSS 声明行：很长 + 含花括号/分号 + 含冒号
        if len(s) > 120 and ("{" in s or "}" in s) and (":" in s or ";" in s):
            continue
        out.append(line)
    return "\n".join(out)


def extract_text(html):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = main.get_text("\n", strip=True)
    text = _strip_css_noise(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def page_title(html):
    soup = BeautifulSoup(html, "lxml")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else ""


# 结构化抽取（可选特征，非搜索核心）
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.I)
EMAIL_NOISE = {"example.com", "domain.com", "yourdomain.com", "sentry.io"}
PERSON_CN_RE = re.compile(r"[\u4e00-\u9fa5]{2,4}(?:先生|女士|经理|总监|董事长|总经理)")

# 高精度号码模式（v2 增强）。
# 原版只认「电话/热线/Tel + 号码」这种标签前置格式，但大量官网把联系方式排成
# 「13701602974 芮女士」——号码在前、称谓在后，标签前置规则完全漏掉。
# 这里补充三类高精度模式，宁缺毋滥：
#   - 中国大陆手机号：1[3-9] 开头共 11 位（前后不能再接数字，避免截到长数字的一部分）
#   - 400/800 服务号：400-123-4567
#   - 带区号固话：021-33372666
MOBILE_RE = re.compile(r"(?<![\d\-])(1[3-9]\d{9})(?![\d\-])")
SERVICE_RE = re.compile(r"(?<![\d\-])((?:400|800)[\s\-]?\d{3}[\s\-]?\d{4})(?![\d\-])")
LANDLINE_RE = re.compile(r"(?<![\d\-])(0\d{2,3}[\s\-]?\d{7,8})(?![\d\-])")

# 号码后置称谓：「13701602974 芮女士」→ 人名线索
NAME_AFTER_NUM = re.compile(
    r"(?:先生|女士|经理|总监|董事长|总经理|老师|博士)\s*$")

# v2：掩码号码识别。企业信息平台（爱企查/水滴信用/企查查等）普遍对电话做部分打码，
# 但**不同页面的打码窗口不同**（一个掩后4位、一个掩中4位）。把多个掩码按位合并，
# 就能还原完整号码——本脚本负责抽掩码，pipeline.py 负责跨页合并。
# 掩码可能落在**中间**（139****7580）或**末尾**（1391680****），故星号后允许 0~4 位数字。
MASKED_PHONE_RE = re.compile(r"(?<![\d\-])(\d{3,}\*{2,}\d{0,4})(?![\d\-])")
MASKED_LANDLINE_RE = re.compile(r"(?<![\d\-])(0\d{1,2}\*{2,}\d{0,4})(?![\d\-])")


def extract_masked_phones(text):
    """抽取形如 139****7580 / 021-*****66 的掩码号码。

    返回 [{raw, pattern}]，pattern 中 '*' 表示未知位，其余为已知数字。
    """
    out = []
    for rx in (MASKED_PHONE_RE, MASKED_LANDLINE_RE):
        for m in rx.finditer(text):
            raw = m.group(1)
            pat = "".join("*" if c == "*" else c for c in raw)
            if pat not in [o["pattern"] for o in out]:
                out.append({"raw": raw, "pattern": pat})
    return out


def _split_name_from_number(line, num):
    """从号码所在行里取出紧跟其后的人名称谓（可能是「芮女士」）。

    返回形如「芮女士」的人名线索；取不到则返回 None。
    """
    idx = line.find(num)
    if idx < 0:
        return None
    tail = line[idx + len(num):].strip()
    m = re.match(r"([\u4e00-\u9fa5]{1,3})(?:先生|女士|经理|总监|老师|博士)", tail)
    return m.group(0) if m else None


def extract_contacts(html, url=""):
    """从页面抽取邮箱、电话、负责人称谓、备案信息。"""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n", strip=True)

    emails = []
    for a in soup.find_all("a", href=True):
        m = re.match(r"mailto:([^?]+)", a["href"], re.I)
        if m:
            emails.append(unescape(m.group(1)).strip())
    emails += EMAIL_RE.findall(html)
    seen, clean_emails = set(), []
    for e in emails:
        e = e.strip().strip(".").lower()
        if not e or "@" not in e or e.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".css")):
            continue
        if any(n in e for n in EMAIL_NOISE):
            continue
        if e not in seen:
            seen.add(e)
            clean_emails.append(e)

    tels = []
    for a in soup.find_all("a", href=True):
        if a["href"].lower().startswith("tel:"):
            v = unescape(a["href"][4:]).strip()
            if re.fullmatch(r"[+\d\s\-()]{7,25}", v):
                tels.append(v)
    scope = soup.find("footer") or soup
    label_re = re.compile(
        r"(?:电话|热线|联系方式|联系电话|Tel|TEL|Phone|Telephone|联系)"
        r"[^\d+\-]{0,12}([+\d][\d\s\-()]{6,24})")
    body_lines = text.splitlines()
    for line in scope.get_text("\n", strip=True).splitlines():
        if len(line) > 120:
            continue
        m = label_re.search(line)
        if m:
            tels.append(m.group(1))

    # v2：标签前置规则之外的「高精度号码模式」扫描（全页）
    # 「13701602974 芮女士」「13817140566 高先生」这类号码前置排版在官网很常见，
    # 只扫数字会引入噪音，故只认三种结构明确的完整号码格式。
    num_names = []
    for line in body_lines:
        if len(line) > 200:
            continue
        for rx in (MOBILE_RE, SERVICE_RE, LANDLINE_RE):
            for m in rx.finditer(line):
                tels.append(m.group(1))
                nm = _split_name_from_number(line, m.group(1))
                if nm:
                    num_names.append(nm)

    phones, pseen = [], set()
    for p in tels:
        p = re.sub(r"\s+", " ", p).strip(" -.")
        digits = re.sub(r"\D", "", p)
        if len(digits) < 7 or len(digits) > 15 or p in pseen:
            continue
        pseen.add(p)
        phones.append(p)

    persons = []
    persons.extend(num_names)          # 号码后置称谓（「芮女士」等）优先
    for m in PERSON_CN_RE.finditer(text):
        persons.append(m.group(0))
    en_titles = r"(?:CEO|CTO|COO|CFO|VP|Director|Manager|Head of|President|Founder|Owner)"
    for m in re.finditer(r"([A-Z][a-z]{1,20}\s+[A-Z][a-z]{1,20})[^\n]{0,20}" + en_titles,
                         text):
        persons.append(f"{m.group(1)} {m.group(0)[len(m.group(1)):]}".strip())

    meta = []
    for pat, key in [(r"[\u4e00-\u9fa5]{2,4}省[\u4e00-\u9fa5]{2,8}市", "地址"),
                     (r"(?:京|沪|粤|苏|浙|闽|鲁|川|渝|津)ICP[备证]?\d{0,6}号"
                      r"(?:-\d+)?", "ICP备案")]:
        mm = re.search(pat, text)
        if mm:
            meta.append(f"{key}: {mm.group(0)}")
    corp_suffix = r"(?:有限责任公司|股份有限公司|集团有限公司|有限公司)"
    raw_corp = re.findall(r"[\u4e00-\u9fa5]{2,14}" + corp_suffix, text)
    noise_prefix = re.compile(r".*(?:关于|通知|公告|意见|批复|公示|函|前述|"
                              r"我局|我司|本公司|该|上述|有关)")
    corp_candidates = []
    for c in raw_corp:
        if noise_prefix.match(c):
            c = noise_prefix.sub("", c)
        c = re.sub(r"^(?:关于|及|与|和|对|向|为|是|由|从|到|在)+", "", c)
        if len(c) >= 4 and c not in corp_candidates:
            corp_candidates.append(c)
    if corp_candidates:
        corp_candidates.sort(key=len, reverse=True)
        meta.append(f"主体名称: {corp_candidates[0]}")

    return {
        "url": url,
        "title": page_title(html),
        "emails": clean_emails,
        "phones": phones[:5],
        "masked_phones": extract_masked_phones(text),
        "persons": list(dict.fromkeys(persons))[:5],
        "meta": meta,
        "text_preview": text[:400],
        "_confidence": {
            "emails": "high" if clean_emails else "none",
            "phones": "high" if phones else "none",
            "masked_phones": "medium" if extract_masked_phones(text) else "none",
            "persons": "low",
            "meta": "medium",
        },
    }


def extract_contacts_from_text(text, url=""):
    """从纯文本（如搜索引擎摘要/snippet、列表卡片文本）抽取邮箱/电话/负责人/掩码号码。

    与 ``extract_contacts`` 不同，本函数不依赖 HTML 结构，直接对文本跑高精度模式，
    用于「搜索引擎快照优先」场景——摘要里往往就藏着电话/邮箱/人名，无需再抓目标页。
    """
    if not text:
        return {"url": url, "emails": [], "phones": [], "masked_phones": [],
                "persons": [], "meta": []}

    emails = []
    for e in EMAIL_RE.findall(text):
        e = e.strip().strip(".").lower()
        if not e or "@" not in e or any(n in e for n in EMAIL_NOISE):
            continue
        if e not in emails:
            emails.append(e)

    tels, num_names = [], []
    for line in text.splitlines():
        if len(line) > 200:
            continue
        for rx in (MOBILE_RE, SERVICE_RE, LANDLINE_RE):
            for m in rx.finditer(line):
                tels.append(m.group(1))
                nm = _split_name_from_number(line, m.group(1))
                if nm:
                    num_names.append(nm)
    # 单行过长也兜底扫一遍（摘要常是整段无换行）
    if len(text) <= 200:
        for rx in (MOBILE_RE, SERVICE_RE, LANDLINE_RE):
            for m in rx.finditer(text):
                if m.group(1) not in tels:
                    tels.append(m.group(1))

    phones, pseen = [], set()
    for p in tels:
        p = re.sub(r"\s+", " ", p).strip(" -.")
        digits = re.sub(r"\D", "", p)
        if len(digits) < 7 or len(digits) > 15 or p in pseen:
            continue
        pseen.add(p)
        phones.append(p)

    persons = list(dict.fromkeys(num_names))
    for m in PERSON_CN_RE.finditer(text):
        if m.group(0) not in persons:
            persons.append(m.group(0))
    persons = persons[:5]

    meta = []
    for pat, key in [(r"[\u4e00-\u9fa5]{2,4}省[\u4e00-\u9fa5]{2,8}市", "地址")]:
        mm = re.search(pat, text)
        if mm:
            meta.append(f"{key}: {mm.group(0)}")

    return {
        "url": url,
        "emails": emails,
        "phones": phones[:5],
        "masked_phones": extract_masked_phones(text),
        "persons": persons,
        "meta": meta,
    }


# ---------------------------------------------------------------- 搜索解析

def _clean_ddg(html, verify=True):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for node in soup.select("div.result, div.web-result"):
        a = node.select_one("a.result__a")
        if not a or not a.get("href"):
            continue
        url = a["href"]
        if "duckduckgo.com/l/?uddg=" in url:
            qs = urllib.parse.urlparse(url).query
            url = urllib.parse.parse_qs(qs).get("uddg", [url])[0]
        snippet = node.select_one(".result__snippet")
        out.append({
            "title": unescape(a.get_text(" ", strip=True)),
            "url": url,
            "snippet": unescape(snippet.get_text(" ", strip=True)) if snippet else "",
        })
    return out


def _clean_bing(html, verify=True):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for node in soup.select("li.b_algo"):
        a = node.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        p = node.select_one(".b_caption p") or node.select_one("p")
        out.append({
            "title": unescape(a.get_text(" ", strip=True)),
            "url": _unwrap_bing(a["href"]),
            "snippet": unescape(p.get_text(" ", strip=True)) if p else "",
        })
    return out


def _unwrap_bing(url):
    if "bing.com/ck/a" not in url:
        return url
    qs = urllib.parse.urlparse(url).query
    raw = urllib.parse.parse_qs(qs).get("u", [""])[0]
    if not raw.startswith("a1"):
        return url
    import base64
    b64 = raw[2:]
    b64 += "=" * (-len(b64) % 4)
    try:
        return base64.urlsafe_b64decode(b64).decode("utf-8", errors="replace")
    except Exception:
        return url


def _node_snippet(node, title_a=None):
    """从结果节点抽取摘要文本；优先专用摘要容器，退化到「整节点文本去掉标题」。

    百度/搜狗等页面结构频繁改版，专用 class（.c-abstract 等）经常失效导致摘要为空，
    进而让「快照优先」策略拿不到摘要里的电话/邮箱。退化策略：取整个结果节点文本，
    剔除标题链接占用的文本，剩余即摘要/描述内容（含联系方式所在文本）。
    """
    for sel in (".c-abstract", ".c-span9", ".content-right_8Zs40", ".c-color-text",
                ".text-layout", ".star-wiki", ".citeurl", ".res-desc", ".summary"):
        sn = node.select_one(sel)
        if sn:
            s = sn.get_text(" ", strip=True)
            if s:
                return s
    # 退化：整节点文本去掉标题
    text = node.get_text(" ", strip=True)
    if title_a:
        title_text = title_a.get_text(" ", strip=True)
        if title_text:
            text = text.replace(title_text, "", 1).strip()
    text = re.sub(r"^[\s·\|>\-–—]+", "", text).strip()
    return text


def _clean_baidu(html, verify=True):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for node in soup.select("div.result, div.c-container"):
        a = node.select_one("h3 a") or node.select_one("a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        if "baidu.com/link" in href:
            href = _resolve_redirect(href, verify=verify)
        if not href or not href.startswith("http") or "baidu.com" in href:
            continue
        snippet = _node_snippet(node, a)
        out.append({
            "title": unescape(a.get_text(" ", strip=True)),
            "url": href,
            "snippet": unescape(snippet),
        })
    return out


def _clean_mbaidu(html, verify=True):
    """解析百度移动端（m.baidu.com）搜索结果。

    移动端与 PC 端 DOM 结构不同：每条结果是一个带 ``rl-link-data-url`` 的
    ``article.coscc-card`` / ``div.c-result-content``，真实目标 URL 在
    ``rl-link-data-url``（部分为百度中转链接 m.baidu.com/from=...，仍可点击跳转）。
    PC 端被图形验证码（wappass）拦截时，移动端风控画像通常不同、仍可访问，因此作为降级通道。
    """
    soup = BeautifulSoup(html, "lxml")
    out = []
    seen = set()
    for node in soup.select("article, div.c-result-content, section.coscc-card, div.c-result"):
        url = (node.get("rl-link-data-url") or node.get("data-url")
               or node.get("rl-link-href") or "")
        if not url:
            continue
        url = urllib.parse.unquote(str(url).replace("&amp;", "&"))
        # 跳过站内搜索建议（/s?）与百度自身页面
        if url.startswith("/") or "baidu.com/s" in url or url.startswith("https://m.baidu.com/s"):
            continue
        title_node = (node.select_one(".c-title, .cosc-title, h3")
                      or node.select_one("[class*='title']"))
        title = title_node.get_text(" ", strip=True) if title_node else ""
        sn_node = node.select_one(".c-abstract, .abstract, [class*='abstract']")
        snippet = sn_node.get_text(" ", strip=True) if sn_node else ""
        if not title and not snippet:
            continue
        key = title or url
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "title": unescape(title),
            "url": url,
            "snippet": unescape(snippet),
        })
    return out


def _clean_sogou(html, verify=True):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for node in soup.select("div.rb, div.vrwrap, div.vr"):
        a = node.select_one("h3 a") or node.select_one("a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        if "sogou.com/link" in href:
            href = _resolve_redirect(href, verify=verify)
        if not href or not href.startswith("http") or "sogou.com" in href:
            continue
        snippet = _node_snippet(node, a)
        out.append({
            "title": unescape(a.get_text(" ", strip=True)),
            "url": href,
            "snippet": unescape(snippet),
        })
    if out:
        return out
    return _clean_generic(html, own_domains=("sogou.com",), verify=verify)


def _clean_so(html, verify=True):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for node in soup.select("div.res-list, div.result"):
        a = node.select_one("h3 a") or node.select_one("a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        if "so.com/link" in href or "so.com/rl" in href:
            href = _resolve_redirect(href, verify=verify)
        if not href or not href.startswith("http") or "so.com" in href:
            continue
        snippet = _node_snippet(node, a)
        out.append({
            "title": unescape(a.get_text(" ", strip=True)),
            "url": href,
            "snippet": unescape(snippet),
        })
    if out:
        return out
    return _clean_generic(html, own_domains=("so.com",), verify=verify)


def _clean_google(html, verify=True):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for node in soup.select("div.g, div.MjjYud, div.tF2Cxc"):
        a = node.select_one("a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        if href.startswith("/url?q="):
            href = urllib.parse.parse_qs(
                urllib.parse.urlparse(href).query).get("q", [""])[0] or href
        if not href.startswith("http"):
            continue
        if "google.com" in href:
            continue
        h3 = node.select_one("h3")
        title = h3.get_text(" ", strip=True) if h3 else a.get_text(" ", strip=True)
        sn = node.select_one("div.VwiC3b, div[data-sncf], .MUxGbd, .yDYNvb")
        snippet = sn.get_text(" ", strip=True) if sn else ""
        out.append({
            "title": unescape(title),
            "url": href,
            "snippet": unescape(snippet),
        })
    return out


def _clean_yandex(html, verify=True):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for node in soup.select("div.serp-item, li.serp-item"):
        a = node.select_one("a.link, a.title, h2 a")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        if not href.startswith("http"):
            continue
        if "yandex." in href:
            continue
        sn = node.select_one(".text-container, .snippet, .typo_type_text")
        snippet = sn.get_text(" ", strip=True) if sn else ""
        out.append({
            "title": unescape(a.get_text(" ", strip=True)),
            "url": href,
            "snippet": unescape(snippet),
        })
    return out


def _clean_naver(html, verify=True):
    """Naver 集成搜索页（nexearch）解析。

    真实结果就是页面里的外链 <a href="https://...">（非 naver 自有域），
    标题文本常带「새 창 열림」(新窗口) 后缀，需清理。没有稳定容器类名，
    直接抽取外链并去重最稳妥。
    """
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http") or "naver.com" in href:
            continue
        title = a.get_text(" ", strip=True)
        title = re.sub(r"\s*새\s*창\s*열림\s*$", "", title).strip()  # 去掉「新窗口」后缀
        title = re.sub(r"\s+", " ", title)
        if len(title) < 4:
            continue
        if href in seen:
            continue
        seen.add(href)
        out.append({"title": unescape(title), "url": href, "snippet": ""})
        if len(out) >= 20:
            break
    return out


def _clean_generic(html, own_domains=(), verify=True, max_n=12):
    """通用兜底解析：抽取页面中指向站外的 http 链接作为结果。

    用于那些 JS 较重、没有稳定结果容器的区域引擎（Daum / Yahoo!Japan / Goo /
    Seznam / Qwant / Parsijoo / Cốc Cốc / Swisscows / Startpage / Brave 等）。
    尽量抽取有意义的标题链接，噪音由上层 `_split_noise` 处理；解析为空时由
    `search` 自动降级到下一引擎，不会 crash。
    """
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    skip = (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
            "/img/", "/static/", "/assets/", "javascript:", "data:")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        low = href.lower()
        if any(d in low for d in own_domains):
            continue
        if any(s in low for s in skip):
            continue
        text = a.get_text(" ", strip=True)
        if len(text) < 3:
            continue
        if href in seen:
            continue
        seen.add(href)
        parent = a.parent
        sn = ""
        if parent:
            sn = parent.get_text(" ", strip=True).replace(text, "", 1).strip()
        out.append({
            "title": unescape(text),
            "url": href,
            "snippet": unescape(sn[:200]),
        })
        if len(out) >= max_n:
            break
    return out


# ---------------------------------------------------------------- 反分词漂移

# 公司/机构类实体后缀：命中即视为一个完整实体，整段加引号做短语检索，
# 强制搜索引擎把品牌名当作整体，而非切碎成「爱 / 定义 / 上海」分别匹配。
ENTITY_RE = re.compile(
    r"[\u4e00-\u9fa5（）()]{2,}"
    r"(?:科技有限公司|股份有限公司|有限责任公司|有限公司|集团公司|集团|"
    r"大学|学院|医院|银行|证券|保险|研究所|研究院)"
)

# 通用词（地名/行业/组织类型等）：区分度低，分词漂移时极易被「上海科技有限公司」
# 等无关结果命中。相关度过滤时仅作辅助覆盖项，不作为关键判别项。
GENERIC_CN = {
    "有限公司", "股份有限公司", "有限责任公司", "公司", "集团", "科技", "技术",
    "信息", "网络", "软件", "电子", "智能", "有限", "股份", "企业", "咨询",
    "服务", "发展", "控股", "实业", "文化", "传媒", "生物", "医疗", "教育",
    "能源", "环境", "汽车", "食品", "贸易", "进出口", "投资", "管理", "数据",
    "互联网", "通信", "光电", "新材料", "新能源", "环保", "建设", "工程",
    "物流", "供应链", "电子商务", "人工智能", "区块链", "上海", "北京", "广州",
    "深圳", "杭州", "南京", "成都", "武汉", "西安", "苏州", "天津", "重庆",
    "中国", "省", "市", "区",
}


def _build_engine_query(query, engine):
    """构造发往引擎的检索串。

    反漂移以「品牌核硬性过滤」为主（见 relevance_ok / _filter_relevant），**不做**引号/去括号改写——
    实测百度对「带全角括号的原文」匹配最好，任何加引号或去括号的改写反而触发分词漂移
    （"爱定义（上海）科技有限公司" 带引号会命中「爱分析」「上海艾炜」等无关公司）。
    仅 Google/Bing 保留 ``site:`` 操作符（它们原生支持），其余引擎剥离
    （它们不认 site:，会把它当关键词搜出垃圾）。
    """
    topic = _strip_site_operators(query)
    if engine in ("google", "bing"):
        site_ops = " ".join(re.findall(r"site:[^\s]*", query, re.I))
        if site_ops:
            return (site_ops + " " + topic).strip()
    return topic


ENTITY_SUFFIX_RE = re.compile(
    r"[（(][^）)]{0,12}[）)]"            # 地理括号（上海）
    r"|科技有限公司|股份有限公司|有限责任公司|有限公司|集团公司|集团"
    r"|大学|学院|医院|银行|证券|保险|研究所|研究院"
)


def _extract_brand(query):
    """从查询中抽取品牌核（实体的最小区分度 token）。

    例：「爱定义（上海）科技有限公司 朱炜」→ 实体「爱定义（上海）科技有限公司」
    → 去地理括号、去组织后缀 → 品牌核「爱定义」。品牌核作为相关度过滤的**硬性必含项**，
    人名（朱炜）等较弱信号仅作辅助——否则会像「上海艾炜贸易有限公司」那样因同样含
    「朱炜」股东而误判为相关。
    """
    m = ENTITY_RE.search(query)
    if not m:
        return None
    span = re.sub(r"[（(][^）)]{0,12}[）)]", "", m.group(0))
    brand = ENTITY_SUFFIX_RE.sub("", span).strip()
    brand = re.sub(r"^[一-龥]{2,}(?:省|市|区)", "", brand).strip()  # 去「上海市」等前缀
    return brand or None


def _query_terms(query):
    """把查询拆成 (关键术语, 通用术语, 品牌核)。

    - 关键术语：拉丁/数字 token，以及非通用词典的 2~4 字中文 token（通常是品牌名/人名）。
    - 通用术语：>=5 字的中文 token（多为「XX科技有限公司」这类组合）或落在 GENERIC_CN 的词。
    - 品牌核：从实体中抽取的最小区分度 token（如「爱定义」），相关度过滤的硬性必含项。
    """
    q_plain = _strip_site_operators(query)
    tokens = re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fa5]{2,}", q_plain)
    key, generic = [], []
    for t in tokens:
        if re.search(r"[\u4e00-\u9fa5]", t):
            if t in GENERIC_CN or len(t) >= 5:
                generic.append(t)
            else:
                key.append(t)
        else:
            key.append(t)
    brand = _extract_brand(q_plain)
    return list(dict.fromkeys(key)), list(dict.fromkeys(generic)), brand


def _coverage_ok(blob, terms, min_hit=0.5):
    if not terms:
        return True
    hit = sum(1 for t in terms if t.lower() in blob)
    return hit / len(terms) >= min_hit


def _blob_of(r):
    return f"{r.get('title', '')} {r.get('snippet', '')} {r.get('url', '')}".lower()


def _relevant_row(r, brand, key_terms, generic_terms):
    """单行结果是否相关。"""
    blob = _blob_of(r)
    if brand:
        # 硬性必含：品牌核必须出现（人名/通用词再匹配也不行）
        if brand.lower() not in blob:
            return False
        if generic_terms and not _coverage_ok(blob, generic_terms, 0.4):
            return False
        return True
    if key_terms:
        if not any(k.lower() in blob for k in key_terms):
            return False
        if generic_terms and not _coverage_ok(blob, generic_terms, 0.4):
            return False
        return True
    return True


def _filter_relevant(rows, brand, key_terms, generic_terms):
    """从结果中剔除漂移项：仅保留相关行。

    若过滤后为空（说明整批都漂移），返回 None 让调用方退回原批次，避免丢光结果。
    """
    kept = [r for r in rows if _relevant_row(r, brand, key_terms, generic_terms)]
    return kept or None


def relevance_ok(rows, query, key_terms=None, generic_terms=None, brand=None, min_hit=0.5):
    """校验结果与 query 是否相关（反分词漂移）。

    - 查询含品牌核：要求结果必须出现品牌核（硬性），否则即便通用词（上海/科技）全覆盖
      也判为不相关——这正是「爱定义」被切散误命中「爱分析/上海艾炜」类公司的根因。
    - 无品牌但有其它关键术语（人名/拉丁词）：要求命中任一关键术语。
    - 无关键术语：退回覆盖率判定（防代理/缓存污染注入无关结果）。
    """
    if not rows:
        return False
    if key_terms is None or generic_terms is None or brand is None:
        key_terms, generic_terms, brand = _query_terms(query)
    if not key_terms and not brand:
        words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fa5]{2,}", query)]
        if not words:
            return True
        for r in rows[:5]:
            if _coverage_ok(_blob_of(r), words, min_hit):
                return True
        return False
    for r in rows[:6]:
        if _relevant_row(r, brand, key_terms, generic_terms):
            return True
    return False


def _market_for(query):
    """按 query 语言选择 Bing/Google 市场参数。"""
    cjk = sum(1 for ch in query if "\u4e00" <= ch <= "\u9fff")
    if cjk and cjk / max(len(query), 1) >= 0.3:
        return {"cc": "cn", "setlang": "zh-CN", "hl": "zh-CN", "gl": "cn"}
    return {"cc": "us", "setlang": "en-US", "hl": "en", "gl": "us"}


# 各区域主流引擎（按国别/语种登记）。global 为国际通用备份。
REGION_ENGINES = {
    "cn": ["baidu", "sogou", "so"],        # 中国：百度 / 搜狗 / 360
    "kr": ["naver", "daum"],               # 韩国：Naver / Daum(Kakao)
    "jp": ["yahoojp", "goo"],              # 日本：Yahoo!Japan / Goo
    "ru": ["yandex"],                      # 俄罗斯及独联体：Yandex
    "cz": ["seznam"],                      # 捷克：Seznam
    "fr": ["qwant"],                       # 法国 / 欧洲：Qwant
    "ir": ["parsijoo"],                    # 伊朗：Parsijoo
    "vn": ["coccoc"],                      # 越南：Cốc Cốc
    "ch": ["swisscows"],                   # 瑞士：Swisscows
    "nl": ["startpage"],                   # 荷兰：Startpage（Google 代理）
}
# 无专属引擎的区域（德/英/美/西/意/巴/印…）默认走 global。
GLOBAL_ENGINES = ["bing", "google", "ddg"]

# `site:<域名>` 国别提示 → 区域键
TLD_TO_REGION = {
    "cn": "cn", "com.cn": "cn", "net.cn": "cn", "org.cn": "cn", "gov.cn": "cn",
    "kr": "kr", "co.kr": "kr", "or.kr": "kr", "ne.kr": "kr",
    "jp": "jp", "co.jp": "jp", "ne.jp": "jp", "or.jp": "jp", "go.jp": "jp",
    "ru": "ru", "su": "ru",
    "cz": "cz",
    "fr": "fr",
    "ir": "ir",
    "vn": "vn",
    "ch": "ch",
    "nl": "nl",
}

# 每引擎 URL 模板与解析器。模板只用各自需要的占位符；多余的 market kwargs 被忽略。
ENGINES = {
    "baidu": {
        # 用户实测稳定的「首页工具栏搜索」参数形态（tn=baiduhome_pg + rsv_dl=tb_click 等），
        # 让百度把请求识别为来自首页搜索框的真实搜索，而非脚本抓取，从而降低被风控概率。
        "url": ("https://www.baidu.com/s?wd={q}&rsv_spt=1&issp=1&f=8&rsv_bp=1&rsv_idx=2"
                "&ie=utf-8&rqlang=cn&tn=baiduhome_pg&rsv_enter=1&rsv_dl=tb_click"
                "&rsv_btype=t&rn={n}"),
        "parse": _clean_baidu,
        "note": "中国主流；结果链接自动还原真实 URL；偶有限流/验证码",
    },
    "baidu_m": {
        "url": "https://m.baidu.com/s?word={q}&rn={n}",
        "parse": _clean_mbaidu,
        "mobile": True,
        "note": "百度移动端；PC 端被图形验证码拦截时的降级通道（风控画像不同）",
    },
    "sogou": {
        "url": "https://www.sogou.com/web?query={q}",
        "parse": _clean_sogou,
        "note": "中国主流（腾讯）；结果链接自动还原",
    },
    "so": {
        "url": "https://www.so.com/s?q={q}",
        "parse": _clean_so,
        "note": "中国主流（360）；结果链接自动还原",
    },
    "naver": {
        "url": "https://search.naver.com/search.naver?query={q}",
        "parse": _clean_naver,
        "note": "韩国主流",
    },
    "daum": {
        "url": "https://search.daum.net/search?q={q}",
        "parse": lambda h, verify=True: _clean_generic(h, ("daum.net", "kakao.com"), verify),
        "note": "韩国主流（Kakao）；JS 较重，可能需降级",
    },
    "yahoojp": {
        "url": "https://search.yahoo.co.jp/search?p={q}",
        "parse": lambda h, verify=True: _clean_generic(h, ("yahoo.co.jp", "yahoo.jp"), verify),
        "note": "日本主流；JS 较重，可能需降级",
    },
    "goo": {
        "url": "https://search.goo.ne.jp/web?MT={q}",
        "parse": lambda h, verify=True: _clean_generic(h, ("goo.ne.jp",), verify),
        "note": "日本主流（NTT）；JS 较重，可能需降级",
    },
    "yandex": {
        "url": "https://yandex.com/search/?text={q}",
        "parse": _clean_yandex,
        "note": "俄语区主流；英文/其他亦可",
    },
    "seznam": {
        "url": "https://search.seznam.cz/?q={q}",
        "parse": lambda h, verify=True: _clean_generic(h, ("seznam.cz",), verify),
        "note": "捷克主流",
    },
    "qwant": {
        "url": "https://www.qwant.com/?q={q}&t=web",
        "parse": lambda h, verify=True: _clean_generic(h, ("qwant.com",), verify),
        "note": "法国/欧洲主流（隐私）；JS 较重，可能需降级",
    },
    "parsijoo": {
        "url": "https://parsijoo.com/search/?q={q}",
        "parse": lambda h, verify=True: _clean_generic(h, ("parsijoo.com",), verify),
        "note": "伊朗主流",
    },
    "coccoc": {
        "url": "https://coccoc.com/search?query={q}",
        "parse": lambda h, verify=True: _clean_generic(h, ("coccoc.com",), verify),
        "note": "越南主流",
    },
    "swisscows": {
        "url": "https://www.swisscows.com/?query={q}",
        "parse": lambda h, verify=True: _clean_generic(h, ("swisscows.com",), verify),
        "note": "瑞士主流（隐私）",
    },
    "startpage": {
        "url": "https://www.startpage.com/sp/search?query={q}",
        "parse": lambda h, verify=True: _clean_generic(h, ("startpage.com",), verify),
        "note": "荷兰主流（Google 结果代理，隐私）",
    },
    "brave": {
        "url": "https://search.brave.com/search?q={q}",
        "parse": lambda h, verify=True: _clean_generic(h, ("search.brave.com", "brave.com"), verify),
        "note": "国际主流（隐私）",
    },
    "google": {
        "url": "https://www.google.com/search?q={q}&num={n}&hl={hl}&gl={gl}",
        "parse": _clean_google,
        "note": "国际最全；可能被风控返回验证码页，失败自动降级",
    },
    "bing": {
        "url": ("https://www.bing.com/search?q={q}&count={n}"
                "&cc={cc}&setlang={setlang}&ensearch=1"),
        "parse": _clean_bing,
        "note": "国际通用；英文 query 需切市场否则返回词典页",
    },
    "ddg": {
        "url": "https://html.duckduckgo.com/html/?q={q}",
        "parse": _clean_ddg,
        "note": "国际（隐私）；偶有限流",
    },
}

# 词典/百科/翻译等站点：搜官网时是噪音；搜信息时作备选（见 _split_noise）。
NOISE_DOMAINS = (
    "dictionary.", "dict.", "/dict", "baike.baidu.com", "zhihu.com",
    "wikipedia.org", "wiktionary.org", "translate.google", "youdao.",
    "iciba.com", "cd(dict)", "dict.cn", "hujiang.com", "911cha.com",
    "911查询", "cambridge.org", "merriam-webster.com", "collinsdictionary.",
    "eudic.net", "zdic.net", "han Dian",
)

# 明显不是企业官网的域名（仅搜官网时过滤）
NON_COMPANY_DOMAINS = (
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "youtube.com", "pinterest.", "tiktok.", "reddit.com", "quora.",
    "amazon.", "ebay.", "alibaba.", "made-in-china.com", "1688.com",
    "globalsources.com", "yellowpages", "cylex", "dnb.com", "bloomberg.com",
    "indeed.com", "glassdoor", "crunchbase", "sohu.com", "163.com",
    "baijiahao", "toutiao", "csdn.net", "cnblogs.com", "sogou.com",
)


def _is_noise(url):
    return any(d in url.lower() for d in NOISE_DOMAINS)


def _split_noise(rows):
    """把结果拆成 (干净结果, 噪音域名结果) 两组，供分场景处理。"""
    clean = [r for r in rows if not _is_noise(r.get("url", ""))]
    noise = [r for r in rows if _is_noise(r.get("url", ""))]
    return clean, noise


def _detect_region(query):
    """根据输入内容判断区域：先看 site: 国别域名，再看文字/语种。"""
    # 1) site:<域名> 提示（最强信号）
    for m in re.finditer(r"site:([a-z0-9.\-]+)", query, re.I):
        host = m.group(1).lower().rstrip(".")
        tld = host.split(".")[-1]
        if tld in TLD_TO_REGION:
            return TLD_TO_REGION[tld]
    # 2) 文字/语种（注意：日文汉字也落在 CJK 区，故先判假名再判汉字）
    total = max(len(query), 1)
    cjk = sum(1 for ch in query if "\u4e00" <= ch <= "\u9fff")
    hangul = sum(1 for ch in query if "\uac00" <= ch <= "\ud7a3")
    kana = sum(1 for ch in query if "\u3040" <= ch <= "\u30ff")
    cyr = sum(1 for ch in query if "\u0400" <= ch <= "\u04ff")
    if kana >= 1:                      # 含假名（含片假名长音等）→ 日文，优先于汉字
        return "jp"
    if cjk / total >= 0.3:
        return "cn"
    if hangul / total >= 0.3:
        return "kr"
    if cyr / total >= 0.3:
        return "ru"
    return "global"


def select_engines(query, engine="auto"):
    """智能选择引擎顺序。

    - 显式指定：直接返回 [engine]（仅该引擎内部重试，不跨引擎降级）。
    - auto：按内容选区域主引擎，再追加 Google/Bing/DDG 全球备份（去重）。
    返回引擎名列表。
    """
    if engine != "auto":
        return [engine]
    region = _detect_region(query)
    primary = REGION_ENGINES.get(region, [])
    if not primary:
        primary = ["google"]
    backups = [e for e in GLOBAL_ENGINES if e not in primary]
    seen, order = set(), []
    for e in primary + backups:
        if e not in seen and e in ENGINES:
            seen.add(e)
            order.append(e)
    return order


def _strip_site_operators(query):
    """去掉 `site:` 操作符，保留真正的搜索主题。

    `site:.cz` / `site:linkedin.com/in` 这类语法只用于路由判断（决定用哪个区域引擎），
    非 Google/Bing 引擎不认识它，会把整串当关键词搜而返回垃圾；故发往引擎前剥离。
    """
    return re.sub(r"\bsite:[^\s]*", "", query).strip()


def search(query, limit=8, engine="auto", verify=True, companies_only=False):
    """通用搜索。auto 时智能选引擎，任一成功即返回；全部失败才报错。

    companies_only=True：搜官网，硬过滤噪音域名与社媒/黄页/电商域名。
    companies_only=False（默认）：搜信息，噪音域名作备选排在干净结果之后。
    """
    q_plain = _strip_site_operators(query)   # 用于相关性校验，不含 site: 噪声 token
    key_terms, generic_terms, brand = _query_terms(q_plain)   # 反分词漂移：拆分关键/通用/品牌
    order = select_engines(query, engine)
    # 百度 PC 端常因风控弹出图形验证码（wappass）：在其后紧跟移动端通道作为降级，
    # 移动端风控画像不同，通常仍可访问，从而避免「PC 被拦截就彻底无结果」。
    if "baidu" in order and "baidu_m" not in order:
        bi = order.index("baidu")
        order.insert(bi + 1, "baidu_m")
    market = _market_for(query)

    errors = []
    start = time.time()
    for name in order:
        # 整体截止：已尝试多个通道并耗尽预算则停止，避免死引擎长时间挂起
        if time.time() - start > SEARCH_DEADLINE:
            errors.append("整体超时：已尝试多个通道仍无结果，停止以避免长时间挂起")
            break
        spec = ENGINES.get(name)
        if not spec:
            errors.append(f"[错误] 未知引擎 {name}，可用: {', '.join(ENGINES)}")
            continue
        q_sent = urllib.parse.quote(_build_engine_query(query, name))
        policy = CHANNEL_POLICY.get(name, {"delay": 1.5, "retries": 1})
        # 渠道级代理：百度通道支持经办公网代理执行；代理连接失败自动回落直连
        ch_proxy = _channel_proxy(name)
        # 百度通道先预热 cookie（BAIDUID），无 cookie 裸搜显著提高被风控概率
        warm_cookies = None
        if name in PROXY_CHANNELS:
            warm_cookies = _warm_baidu_cookies(verify=verify, proxy=ch_proxy)
        # 总尝试 = 网络重试 + 分词漂移重试。漂移时重试同一引擎（并轻微扰动 rn），
        # 因为百度等结果非确定性，一次漂移不代表该引擎无正确结果。
        max_attempts = policy["retries"] + 1 + RELEVANCE_RETRIES
        last_status = None
        for attempt in range(max_attempts):
            if time.time() - start > SEARCH_DEADLINE:
                errors.append("整体超时：已尝试多个通道仍无结果，停止以避免长时间挂起")
                break
            # 轻微扰动结果数，促使搜索引擎返回不同结果集（对抗非确定性漂移）
            n_var = min(limit + attempt * 10, 50)
            # 仅传入模板真正用到的占位符，避免多余 market 参数（cc/hl/gl 等）触发 KeyError
            _needed = {fn for _, fn, _, _ in _FMT.parse(spec["url"]) if fn}
            _mkt = {k: v for k, v in market.items() if k in _needed}
            url = spec["url"].format(q=q_sent, n=n_var, **_mkt)
            try:
                # 移动端引擎（baidu_m）用移动 UA，否则用桌面 UA
                hdrs = MOBILE_HEADERS if spec.get("mobile") else DEFAULT_HEADERS
                try:
                    html = fetch_raw(url, verify=verify, headers=hdrs,
                                     proxy=ch_proxy, cookies=warm_cookies)
                except (httpx.ProxyError, httpx.ConnectError,
                        httpx.ConnectTimeout) as pe:
                    if not ch_proxy:
                        raise
                    # 代理不可达：如实记录后回落直连（本次搜索内不再走代理）
                    errors.append(f"{name}: 代理连接失败（{type(pe).__name__}），回落直连")
                    ch_proxy = None
                    html = fetch_raw(url, verify=verify, headers=hdrs,
                                     cookies=warm_cookies)
                # 跟随 JS 重定向（Cốc Cốc / Parsijoo 等）
                html = _follow_js_redirect(html, url, verify=verify)
                # 风控验证码/拦截页：不是正常结果，立即放弃本通道，转下一引擎/降级通道
                if _is_blocked(html):
                    errors.append(f"{name}: 命中风控验证码/拦截页，跳过（可能为本机 IP 被标记）")
                    break
                parsed = spec["parse"](html, verify=verify)
                clean, noise = _split_noise(parsed)
                # 反分词漂移：剔除只命中通用词、丢了品牌核的漂移结果
                filtered = _filter_relevant(clean, brand, key_terms, generic_terms)
                if filtered is not None:
                    clean = filtered
                if companies_only:
                    clean = [r for r in clean
                             if not any(d in r.get("url", "").lower()
                                        for d in NON_COMPANY_DOMAINS)]
                if clean and relevance_ok(clean, q_plain, key_terms, generic_terms, brand):
                    final = clean + (noise if not companies_only else [])
                    return {"query": query, "engine": name,
                            "results": final[:limit], "route": order}
                # 未命中：记录状态，进入重试（漂移时多试几次常能命中正确集）
                if not clean:
                    if parsed and relevance_ok(parsed, q_plain, key_terms, generic_terms, brand):
                        return {"query": query, "engine": name,
                                "results": parsed[:limit], "route": order}
                    last_status = f"{name}: 第{attempt+1}次未解析到有效结果（全为噪音/无关）"
                else:
                    last_status = f"{name}: 第{attempt+1}次结果不相关（疑似分词漂移），重试"
            except Exception as e:
                last_status = f"{name}: {type(e).__name__} {str(e)[:80]}"
                # 连接/代理/超时类错误立即放弃重试，避免死引擎拖时间
                if isinstance(e, (httpx.ConnectTimeout, httpx.ReadTimeout,
                                  httpx.ConnectError, httpx.ProxyError,
                                  httpx.RemoteProtocolError)):
                    errors.append(last_status)
                    break
            time.sleep(policy["delay"])
        else:
            # 内层循环正常结束（非 break）→ 记录最后一次状态，再试下一引擎
            if last_status:
                errors.append(last_status)

    return {"query": query, "engine": None, "results": [],
            "errors": errors, "route": order,
            "hint": ("所有通道均未返回可信结果。依次尝试："
                     "(1) 换关键词或换语言重试；"
                     "(2) 检查本机代理/网络，各搜索引擎被劫持时会返回无关内容；"
                     "(3) 改用 agent-browser 技能打开搜索引擎页面手动检索；"
                     "(4) 若目标站点已知，直接用 fetch 抓官网，不要依赖搜索")}


# ---------------------------------------------------------------- CLI

def cmd_search(a):
    res = search(a.query, limit=a.limit, engine=a.engine,
                 companies_only=a.companies_only)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["results"] else 2
    if not res["results"]:
        print(f"[未取到结果] {res.get('hint', '')}")
        for e in res.get("errors", []):
            print("  -", e)
        return 2
    route = " → ".join(res.get("route", []))
    print(f"# {res['query']}  (命中引擎: {res['engine']}, 路由: {route}, "
          f"{len(res['results'])} 条)\n")
    for i, r in enumerate(res["results"], 1):
        print(f"{i}. {r['title']}\n   {r['url']}")
        if r["snippet"]:
            print(f"   {r['snippet'][:220]}\n")
    return 0


def cmd_fetch(a):
    html = fetch_raw(a.url, verify=not a.insecure)
    text = extract_text(html)
    title = page_title(html)
    if a.json:
        print(json.dumps({"url": a.url, "title": title,
                          "text": text}, ensure_ascii=False, indent=2))
    else:
        print(f"# {title}\n# {a.url}\n")
        print(text)
    return 0


def cmd_extract(a):
    html = fetch_raw(a.url, verify=not a.insecure)
    d = extract_contacts(html, a.url)
    print(json.dumps(d, ensure_ascii=False, indent=2))
    return 0


def cmd_batch(a):
    with open(a.list, encoding="utf-8") as f:
        urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    results, ok, fail = [], 0, 0
    for i, u in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {u}", file=sys.stderr)
        try:
            html = fetch_raw(u, verify=not a.insecure)
            d = extract_contacts(html, u)
            if a.full_text:
                d["text"] = extract_text(html)[:8000]
            results.append(d)
            ok += 1
        except Exception as e:
            results.append({"url": u, "error": f"{type(e).__name__}: {str(e)[:120]}"})
            fail += 1
        time.sleep(a.delay)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n[完成] 成功 {ok}，失败 {fail}", file=sys.stderr)
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser(
        description="可移植的通用网页检索与抓取工具（WebSearch/WebFetch 替代实现）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("search", help="通用搜索网页（智能选引擎）")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--engine", default="auto",
                   help="搜索引擎；auto=按内容智能选（默认），或指定: " +
                        "/".join(ENGINES.keys()))
    p.add_argument("--companies-only", action="store_true",
                   help="搜官网：硬过滤社媒/黄页/电商等噪音域名；默认（搜信息）"
                        "噪音域名作为备选保留")
    p.add_argument("--json", action="store_true")
    p.add_argument("--insecure", action="store_true", help="跳过 TLS 校验（内网自签证书）")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("fetch", help="抓取页面并输出正文")
    p.add_argument("url")
    p.add_argument("--json", action="store_true")
    p.add_argument("--insecure", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("extract", help="抽取邮箱/电话/负责人等结构化字段（可选特征）")
    p.add_argument("url")
    p.add_argument("--insecure", action="store_true")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("batch", help="批量抓取 URL 列表")
    p.add_argument("list", help="文件，每行一个 URL")
    p.add_argument("--full-text", action="store_true", help="同时保留正文（前 8000 字）")
    p.add_argument("--delay", type=float, default=1.5, help="每条间隔秒数")
    p.add_argument("--insecure", action="store_true")
    p.set_defaults(func=cmd_batch)

    a = ap.parse_args()
    try:
        return a.func(a)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
