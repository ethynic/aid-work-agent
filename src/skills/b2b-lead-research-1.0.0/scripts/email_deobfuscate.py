#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官网邮箱反混淆抽取 —— 技能标准实现（纯 Python 标准库，无第三方依赖）。

背景 / 为什么要这个模块
--------------------------------------------------------------------
德国 / DACH 制造业官网大量使用 WordPress 的邮箱保护插件，把地址做 ROT13 后
塞进 ``data-enc-email`` 等自定义属性，页面里没有 ``mailto:`` 也没有明文：

    <a href="#" data-enc-email="vasb[ng]nsg-tebhc.qr">E-Mail</a>
                                  -> info@aft-group.de

连 ``[at]`` / ``[dot]`` 也可能一起被 ROT13 成 ``[ng]`` / ``[qbg]``。
只跑「mailto: + 纯文本正则」的抽取器会整批落空（实测 200 家德国制造企业里
43 家因此被误判为「无邮箱」）。

覆盖形态
--------------------------------------------------------------------
- ROT13：``data-enc-email`` / ``data-email`` / ``data-mail`` 等属性，含双编码
  ``[ng]`` / ``[qbg]``；也兼容只旋转字母、占位符仍是 ``[at]`` 的变体
- Cloudflare Email Protection：``data-cfemail="<hex>"`` 与
  ``/cdn-cgi/l/email-protection#<hex>``（XOR 解码）
- 占位符：``[at] (at) [ng] &#64; &#x40;`` / ``[dot] (dot) [qbg] &#46;``
- HTML 实体、JSON-LD / 内联 JSON 的 ``"email": "..."``
- 常规 ``mailto:``、HTML 源码与可见文本里的明文邮箱

导出
--------------------------------------------------------------------
normalize(raw)                 -> (addr | None, method)
extract_emails(html, url="")    -> {addr: (method, url)}   # 来源可回溯
deobfuscate_emails(html, url)   -> [addr, ...]             # 便捷接口

命令行
--------------------------------------------------------------------
python email_deobfuscate.py --selftest
python email_deobfuscate.py --html page.html [--url https://firma.de/impressum]
python email_deobfuscate.py --url https://firma.de/impressum
"""
from __future__ import annotations

import argparse
import codecs
import html as html_lib
import re
import sys
import urllib.request

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}")

# ROT13 后 [at] -> [ng]、[dot] -> [qbg]；德语 Klammeraffe / Punkt 一并兼容
AT_RE = re.compile(
    r"\[\s*(?:at|ng|ät|klammeraffe)\s*\]|\(\s*(?:at|ng)\s*\)"
    r"|&#0?64;|&#x0?40;|\s+at\s+", re.I)
DOT_RE = re.compile(
    r"\[\s*(?:dot|qbg|punkt)\s*\]|\(\s*(?:dot|qbg|punkt)\s*\)|&#0?46;", re.I)
OBF_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*(?:\[\s*(?:at|ng)\s*\]|\(\s*(?:at|ng)\s*\)|&#0?64;)\s*"
    r"([A-Za-z0-9\-]+(?:\s*(?:\[\s*(?:dot|qbg)\s*\]|\(\s*(?:dot|qbg)\s*\)|\.)\s*"
    r"[A-Za-z0-9\-]+)+)", re.I)

# TLD 白名单是关键的防假阳性闸门：ROT13 会把 `.de` 变成 `.qr`、把明文邮箱
# 变成一堆看似合法的伪地址。两个候选（plain / rot13）都必须过这个白名单。
KNOWN_TLDS = {
    "de", "com", "eu", "at", "ch", "li", "lu", "net", "org", "info", "biz",
    "cn", "jp", "us", "fr", "it", "nl", "pl", "es", "uk", "co", "io", "dk",
    "se", "no", "fi", "be", "cz", "sk", "hu", "ro", "tr", "in", "tw", "hk",
    "au", "ca", "br", "mx", "ru", "ua", "si", "hr", "gr", "pt", "ie", "kr",
    "sg", "my", "th", "vn", "id", "za", "ae", "sa", "il", "nz", "ar", "cl",
    "edu", "gov", "mil", "int", "name", "me", "tv", "cc", "ws", "su",
    "shop", "tech", "online", "site", "website", "cloud", "digital",
    "systems", "solutions", "group", "global", "world", "email", "ag",
    "gmbh", "kg", "engineering", "industries", "parts", "works",
}
BAD_DOMAINS = (
    "example.com", "example.org", "domain.com", "yourdomain", "sentry.io",
    "wixpress.com", "googleapis.com", "gstatic.com", "cloudflare.com",
    "jquery.com", "schema.org", "email.com", "fontawesome.com", "bootstrap",
    "w3.org", "adobe.com", "wordpress.org", "google.com", "facebook.com",
    "youtube.com", "instagram.com", "linkedin.com", "xing.com", "twitter.com",
    "vimeo.com", "typekit.net", "microsoft.com", "apple.com",
)
OBF_ATTRS = {
    "data-enc-email", "data-email", "data-mail", "data-mailto",
    "data-cfemail", "data-contact-email", "data-email-encoded",
    "data-e-mail", "data-enc-mail", "data-obfuscated-email",
    "data-email-address",
}


def _valid(addr: str) -> bool:
    """地址形态 + TLD 白名单 + 噪声域过滤。"""
    if not EMAIL_RE.fullmatch(addr or ""):
        return False
    if addr.rsplit(".", 1)[-1].lower() not in KNOWN_TLDS:
        return False
    dom = addr.rsplit("@", 1)[-1].lower()
    if any(b in dom for b in BAD_DOMAINS):
        return False
    if addr.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".css", ".js")):
        return False
    return True


def normalize(raw: str):
    """把一处邮箱文本还原成规范地址；无法确定时返回 (None, "")。

    返回 ``(addr, method)``，method ∈ {plain, rot13, cloudflare_hex, obfuscated}，
    调用方可把它写进来源字段，保证每个地址都能回溯到页面上的原始形态。
    """
    s = html_lib.unescape((raw or "").strip()).strip("'\"<>;,")
    if not s or len(s) > 200:
        return None, ""

    # Cloudflare email protection：首字节是 XOR key，其后逐字节异或
    if re.fullmatch(r"[0-9a-fA-F]{8,}", s) and len(s) % 2 == 0:
        try:
            b = bytes.fromhex(s)
            dec = "".join(chr(x ^ b[0]) for x in b[1:])
            dec = html_lib.unescape(dec).strip().lower()
            if _valid(dec):
                return dec, "cloudflare_hex"
        except Exception:  # noqa: BLE001
            pass
        return None, ""

    cands = ((s, "plain"), (codecs.decode(s, "rot_13"), "rot13"))
    for cand, method in cands:
        cleaned = AT_RE.sub("@", cand)
        cleaned = DOT_RE.sub(".", cleaned)
        cleaned = re.sub(r"\s+", "", cleaned).strip(".").lower()
        if _valid(cleaned):
            return cleaned, ("obfuscated" if method == "plain" and
                             ("@" not in s.replace(" ", "")) else method)
    return None, ""


def extract_emails(page_html: str, page_url: str = ""):
    """返回 ``{email: (method, page_url)}``；只收页面明示/可反混淆的地址。

    不猜测、不按公司名拼地址 —— 反混淆是「还原页面已给出的信息」，不是推断。
    """
    found: dict[str, tuple[str, str]] = {}

    def put(raw, source_method):
        addr, how = normalize(raw)
        if addr and addr not in found:
            found[addr] = (how or source_method, page_url)

    # 1) 标签属性：data-enc-email / data-cfemail / mailto / email-protection
    for tag in re.finditer(r"<[^>]+>", page_html or "", re.S):
        for am in re.finditer(
                r'([\w:\-]+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s>]+))', tag.group(0)):
            key = (am.group(1) or "").lower()
            val = html_lib.unescape(am.group(2) or am.group(3) or am.group(4) or "")
            if key in OBF_ATTRS or key.startswith(("data-enc", "data-email")):
                put(val, key)
            if key in ("href", "src", "action") and val.lower().startswith("mailto:"):
                put(val[7:].split("?")[0], "mailto")
            if "email-protection#" in val:
                put(val.split("email-protection#", 1)[1], "cloudflare")

    # 2) JSON-LD / 内联 JSON 里的 "email": "..."
    for m in re.finditer(r'["\']email["\']\s*:\s*["\']([^"\']{4,160})["\']',
                         page_html or "", re.I):
        put(m.group(1), "jsonld")

    # 3) HTML 源码与可见文本里的明文
    for m in EMAIL_RE.finditer(page_html or ""):
        put(m.group(0), "plain")
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", page_html or ""))
    for m in EMAIL_RE.finditer(text):
        put(m.group(0), "plain_text")
    for m in OBF_RE.finditer(text):
        put(m.group(0), "obfuscated_text")
    return found


def deobfuscate_emails(page_html: str, page_url: str = ""):
    """便捷接口：返回去重后的地址列表（供 extract_contacts 直接并入）。"""
    return list(extract_emails(page_html, page_url).keys())


def _selftest():
    cases = [
        # (原始文本, 期望地址, 期望方法关键词)
        ('vasb[ng]nsg-tebhc.qr', 'info@aft-group.de', 'rot13'),
        ('vasb[at]nsg-tebhc.qr', 'info@aft-group.de', 'rot13'),
        ('info@aft-group.de', 'info@aft-group.de', 'plain'),
        ('max.mustermann(at)firma.de', 'max.mustermann@firma.de', ''),
        ('vorname.nachname&#64;firma.de', 'vorname.nachname@firma.de', ''),
        ('a[dot]b@firma.de', 'a.b@firma.de', ''),
    ]
    html = ('<a href="#" data-enc-email="vasb[ng]nsg-tebhc.qr">Mail</a>'
            '<a href="mailto:vertrieb@firma.de?subject=Hi">Vertrieb</a>'
            '<span class="__cf_email__" data-cfemail="5e3b333f"></span>')
    ok = True
    for raw, want, method in cases:
        got, how = normalize(raw)
        flag = "OK " if got == want and (not method or method in how) else "FAIL"
        if flag == "FAIL":
            ok = False
        print(f"  [{flag}] {raw!r:42} -> {got!r:32} ({how})")
    found = extract_emails(html, "https://aft-group.de/impressum")
    print("  extract_emails:", {k: v[0] for k, v in found.items()})
    if "info@aft-group.de" not in found or "vertrieb@firma.de" not in found:
        ok = False
        print("  [FAIL] data-enc-email / mailto 未全部命中")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="官网邮箱反混淆抽取（技能标准实现）")
    ap.add_argument("--selftest", action="store_true", help="跑内置解码用例")
    ap.add_argument("--html", help="本地 HTML 文件路径")
    ap.add_argument("--url", help="页面 URL（仅用于标注来源；--html 时填）")
    ap.add_argument("--fetch", help="直接抓取该 URL 后抽取（标准库 urllib）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出地址与解码方式")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    page, url = "", a.url or ""
    if a.html:
        with open(a.html, "r", encoding="utf-8", errors="replace") as f:
            page = f.read()
    elif a.fetch:
        url = a.fetch
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            page = r.read().decode("utf-8", errors="replace")
    else:
        ap.print_help()
        return 2
    found = extract_emails(page, url)
    if a.json:
        import json
        print(json.dumps({k: {"method": v[0], "source": v[1]} for k, v in found.items()},
                         ensure_ascii=False, indent=2))
    else:
        for addr, (method, src) in sorted(found.items()):
            print(f"{addr}\t{method}\t{src}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
