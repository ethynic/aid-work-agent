#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发件域名健康检测 —— 发送冷邮件前必须先跑一遍。

为什么需要这个脚本
------------------
SPF / DKIM / DMARC / MX / PTR 配置错了，邮件不会给你任何提示，
只是安静地进垃圾箱。等你发现"打开率 < 10%"再去查，往往已经发了几百封，
域名信誉也一起赔进去了。这个脚本把配置问题在发信前暴露出来。

检查项
------
| 项 | 查什么 | 失败意味着 |
|---|---|---|
| SPF | 记录是否存在、有无 -all、查询次数是否超限 | 认证失败，被降权或拒收 |
| DKIM | _domainkey 下的 selector、密钥长度 | 无法验证邮件真实性 |
| DMARC | p 策略、是否 strict 对齐、rua 是否可收信 | 域名与邮件未绑定，易被仿冒 |
| MX | 记录是否存在 | 子域退信无法接收，退信被静默丢弃 |
| PTR | IP 反解域名（需 --smtp-ip） | FCrDNS 不一致，常被直接拒收 |
| 黑名单 | 域名 + IP 的名单命中（--blacklist） | 已在名单上，发多少都会进垃圾箱 |

用法
----
```bash
python domain_check.py outreach.yourdomain.com
python domain_check.py outreach.yourdomain.com --smtp-ip 1.2.3.4
python domain_check.py outreach.yourdomain.com --blacklist
python domain_check.py outreach.yourdomain.com --json
```

退出码：0 = 全部通过；1 = 有警告但可发；2 = 有失败项，发送前必须修。
"""

import argparse
import json
import re
import socket
import struct
import sys
import urllib.parse

# 仅标准库，不强制依赖 dnspython。内网环境下这点很重要。
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False


OK, WARN, FAIL, UNKNOWN = "通过", "警告", "失败", "未知"

# SPF 机制前缀 —— 每次 include/redirect/exp 都会消耗一次 DNS 查询
SPF_MECHANISMS = ("include:", "a:", "mx:", "ptr:", "exists:",
                  "redirect=")

# 黑名单服务。仅做基础查询，命中即需人工复核（存在误报）。
BLACKLIST_HOSTS = {
    "Spamhaus DBL": "dbl.spamhaus.org",
    "Barracuda": "b.barracudacentral.org",
    "SpamCop": "bl.spamcop.net",
    "Sorbs": "dnsbl.sorbs.net",
    "URIBL": "multi.uribl.com",
    "SISA": "sisa.msn.com",
}


# Windows nslookup 默认只等 2 秒，国内网络下经常不够。
# 记录到模块级，供 CLI 的 --dns-timeout 覆盖。
DNS_TIMEOUT = 20.0


PUBLIC_RESOLVERS = ["223.5.5.5", "8.8.8.8", "1.1.1.1"]

_DNS_TYPES = {"A": 1, "NS": 2, "CNAME": 5, "MX": 15, "TXT": 16, "PTR": 12}
_DNS_CLASS_IN = 1


def _run_nslookup(server, qtype, name, timeout):
    """对单个 resolver 跑一次 nslookup，返回 (记录列表, 错误)。

    错误约定（记录存在与否 vs 查询故障必须严格区分）：
      - ''          干净地没查到（NODATA），公共 resolver 权威，可采信为"记录确不存在"
      - 'NXDOMAIN'  名字不存在
      - 其他字符串   查询故障（超时 / 解析异常等），应归为"无法判定"
    """
    import subprocess
    wait = max(1, int(timeout))
    cmd = ["nslookup", "-timeout=" + str(wait), "-type=" + qtype, name]
    if server:
        cmd.append(server)
    try:
        raw = subprocess.run(cmd, capture_output=True,
                             timeout=timeout + 5).stdout
    except subprocess.TimeoutExpired:
        return [], "nslookup 超时"
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:60]}"
    # Windows nslookup 输出是 GBK/本地代码页，用 errors="replace" 防解码崩溃。
    out = raw.decode("utf-8", errors="replace")
    if "timed out" in out.lower() or "超时" in out:
        return [], "nslookup 等待超时"
    if re.search(r"(non-existent domain|找不到|can't find)", out, re.I):
        return [], "NXDOMAIN"
    recs = _parse_nslookup(out)
    return recs, ""


def _nslookup_doh(qtype, name, timeout):
    """DNS over HTTPS 回落（需要 httpx）。"""
    if not HAS_HTTPX:
        return [], "未安装 httpx，无法启用 DoH 回落"
    url = (f"https://dns.google/resolve?name={urllib.parse.quote(name)}"
           f"&type={qtype}")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url)
            r.raise_for_status()
            data = r.json()
        if data.get("Status") == 3:      # NXDOMAIN
            return [], "NXDOMAIN"
        if data.get("Status") != 0:
            return [], f"DoH 状态码 {data.get('Status')}"
        recs = [str(a.get("data", "")).strip().strip('"')
                for a in (data.get("Answer", []) or [])
                if str(a.get("data", "")).strip()]
        return recs, "" if recs else ""
    except Exception as e:
        return [], f"DoH {type(e).__name__}"


def _skip_name(data, off):
    """跳过 DNS 报文里的 NAME 字段（处理顶层压缩指针），返回名字后的偏移。"""
    while off < len(data):
        length = data[off]
        if length & 0xC0 == 0xC0:          # 指针
            return off + 2
        if length == 0:
            return off + 1
        if length & 0xC0:                  # 其它 2 字节类型
            return off + 2
        off += 1 + length
    return off


def _read_name(data, off):
    """读取 NAME（支持压缩指针），返回 (结束偏移, 名字字符串)。

    若名字以指针起始，结束偏移取指针之后的位置（而非跳转目标），
    调用方才能正确地继续读取后续字段。
    """
    labels = []
    p = off
    end_off = None
    jumped = False
    while p < len(data):
        length = data[p]
        if length & 0xC0 == 0xC0:
            ptr = struct.unpack(">H", data[p:p + 2])[0] & 0x3FFF
            if not jumped:
                end_off = p + 2
                jumped = True
            p = ptr
            continue
        if length == 0:
            if not jumped:
                end_off = p + 1
            break
        if length & 0xC0:
            if not jumped:
                end_off = p + 2
            break
        labels.append(data[p + 1:p + 1 + length].decode("ascii", "replace"))
        p += 1 + length
    return (end_off if end_off is not None else p), ".".join(labels)


def _dns_query_udp(name, qtype, resolver, timeout=8):
    """纯 stdlib UDP DNS 查询，不依赖 dnspython，也不解析 nslookup 文本输出。

    返回 (记录字符串列表, 错误)。错误约定（记录存在与否 vs 查询故障严格区分）：
      - ''          干净无记录（NODATA），可采信为"记录确不存在"
      - 'NXDOMAIN'  名字不存在
      - 其他        查询故障（超时 / 解析异常等），应归为"无法判定"
    """
    try:
        qt = _DNS_TYPES[qtype.upper()]
    except KeyError:
        return [], f"不支持的类型 {qtype}"
    txid = 0x3412
    pkt = struct.pack(">HHHHHH", txid, 0x0100, 1, 0, 0, 0)
    for label in [l for l in name.split(".") if l]:
        pkt += bytes([len(label)]) + label.encode("ascii")
    pkt += b"\x00" + struct.pack(">HH", qt, _DNS_CLASS_IN)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        s.sendto(pkt, (resolver, 53))
        data, _ = s.recvfrom(4096)
        s.close()
    except socket.timeout:
        return [], "DNS 超时"
    except Exception as e:
        return [], f"{type(e).__name__}: {str(e)[:50]}"
    if len(data) < 12:
        return [], "响应过短"
    rid, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", data[:12])
    rcode = flags & 0x000F
    if rid != txid:
        return [], "事务ID不匹配"
    if rcode == 3:
        return [], "NXDOMAIN"
    if rcode == 2:
        return [], "SERVFAIL"
    off = 12
    for _ in range(qd):
        off = _skip_name(data, off) + 4       # 跳过问题段（QNAME + QTYPE + QCLASS）
    recs = []
    for _ in range(an):
        off, _ = _read_name(data, off)        # 答案名（常为 0xC00C 指针）
        rtype, rclass, ttl, rdlen = struct.unpack(">HHIH", data[off:off + 10])
        rs = off + 10
        rdata = data[rs:rs + rdlen]
        off = rs + rdlen
        if rtype == _DNS_TYPES["TXT"]:
            p = 0
            while p < len(rdata):
                ln = rdata[p]
                recs.append(rdata[p + 1:p + 1 + ln].decode("utf-8", "replace"))
                p += 1 + ln
        elif rtype == _DNS_TYPES["MX"]:
            pref = struct.unpack(">H", rdata[:2])[0]
            _, host = _read_name(data, rs + 2)
            recs.append(f"{pref} {host}")
        elif rtype == _DNS_TYPES["A"]:
            recs.append(".".join(str(b) for b in rdata[:4]))
        elif rtype == _DNS_TYPES["CNAME"]:
            _, cname = _read_name(data, rs)
            recs.append(cname)
    return recs, ""


def _nslookup(qtype, name, timeout=None):
    """两级解析：UDP 公共 resolver（直连权威，最可靠）→ nslookup/DoH 兜底。

    设计动机：办公/内网常把系统 DNS 指向企业自建服务器，这类服务器对公网
    域名经常返回陈旧的 SOA（NODATA）或干脆不给权威记录，把"查不到"误判成
    "域名没配"。本机测试 archiproducts.com.cn 时就因此把 SPF/MX 误报成
    "查询失败"。纯 UDP 直连公共权威 resolver 可彻底绕过该问题，且不再依赖
    易碎的 nslookup 文本解析（GBK 乱码、解析器 IP 误当记录等）。

    判定优先级：拿到记录 > NXDOMAIN/NODATA（确定没记录）> 查询故障（无法判定）。
    只有全部通道都故障，才返回"无法判定"，绝不把故障写成"记录缺失"。
    """
    timeout = timeout or DNS_TIMEOUT
    records = None
    no_record = False
    faults = []

    # ① UDP 公共 resolver（首选，直连权威）
    for srv in PUBLIC_RESOLVERS:
        recs, err = _dns_query_udp(name, qtype, srv, timeout=min(timeout, 8))
        if err == "NXDOMAIN":
            return [], "NXDOMAIN（域名/名字不存在）"
        if err:
            faults.append(f"UDP{srv}: {err}")
            continue
        if recs:
            records = recs if records is None else records + recs
        else:
            no_record = True

    if records:
        seen, out = set(), []
        for r in records:
            if r not in seen:
                seen.add(r)
                out.append(r)
        return out, None

    # ② nslookup 兜底（企业内网 DNS，可能陈旧，仅供参考）
    recs, err = _run_nslookup(None, qtype, name, timeout)
    if recs:
        return recs, None
    if err == "NXDOMAIN":
        return [], "NXDOMAIN（域名/名字不存在）"
    if err:
        faults.append(f"系统: {err}")

    # ③ DoH 兜底
    recs, err = _nslookup_doh(qtype, name, timeout)
    if recs:
        return recs, None
    if err == "NXDOMAIN":
        return [], "NXDOMAIN（域名/名字不存在）"
    if err:
        faults.append(f"DoH: {err}")

    if no_record and not records:
        return [], None                # 公共 resolver 权威确认无此记录
    if faults:
        return [], "；".join(faults)
    return [], None                    # 兜底：无故障也无记录                    # 兜底：无故障也无记录


def _parse_nslookup(text):
    """解析 nslookup 输出。

    必须处理三种实际存在的格式差异，否则会大面积误判"未配置"：

    1. **语言差异**：中文 Windows 输出「名称: / 地址: / 非权威应答」，
       英文版是 "Name: / Address: / Non-authoritative answer"。不能依赖这些词。

    2. **TXT 折行**：长 TXT 会折成两行，形如
           google.com  text =
               "v=spf1 include:_spf.google.com ~all"
       按行直取只会拿到空壳行，必须先拼接。

    3. **MX 嵌套解析**：中文版的 MX 结果先展开成 A 记录
           smtp.google.com  internet address = 173.194.203.26
       真正的 MX 优先级行反而被折叠。中文环境应直接采信"能解析出主机名即有 MX"。
    """
    # 1) 折行 TXT 拼接
    #注意中间可能夹空行（实测输出为 `xxx  text =\n\n\t"v=DKIM1..."`），
    # 所以 \s* 必须能跨多个换行，否则拼接失败、整条 TXT 被丢掉。
    joined = re.sub(r"(\S+)\s+text\s*=\s*\n\s*", r"\1 ", text)
    joined = re.sub(r"\n\s*\n", "\n", joined)

    recs, seen = [], set()
    in_answer = False
    server_ip_pending = False          # "服务器:" 行紧随的 Address 是解析器自身 IP

    def add(s):
        s = s.strip()
        if s and s not in seen:
            seen.add(s)
            recs.append(s)

    for raw_line in joined.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("***"):
            continue
        low = line.lower()

        # 2) 跳过头部（DNS 服务器自身信息行）
        #    注意：`服务器:/Server:` 行紧随的 `Address:` 是解析器自身 IP，
        #    必须跳过 —— 否则公共 resolver（如 223.5.5.5）的公网 IP 会被误当成
        #    域名的 A 记录。中文版 nslookup 没有"非权威应答"行，应答直接跟在
        #    服务器地址后，所以不能只靠该标记判断边界。
        if re.match(r"^(server|服务器)\s*[:：]", low):
            server_ip_pending = True
            continue
        if re.match(r"^(address|地址)\s*[:：]", low):
            m = re.search(r"[:：]\s*(\d+\.\d+\.\d+\.\d+)", line)
            ip = m.group(1) if m else ""
            if server_ip_pending:           # 这是解析器自身 IP，跳过
                server_ip_pending = False
                continue
            if ip and not (ip.startswith(("192.168.", "10.", "127."))
                           or ip.endswith(".1") or ip.startswith("172.1")):
                in_answer = True
                add(ip)
            continue
        if re.search(r"(非权威应答|权威应答|non-authoritative|"
                     r"authoritative answer)", low) or low.startswith("answer:"):
            in_answer = True
            continue

        # 2.5) 记录本体。中文 Windows 的 MX 行既不是 `10 mail.x.com.`
        #      也不是 `10 mail.x.com`，而是：
        #      `google.com  MX preference = 10, mail exchanger = smtp.google.com`
        m = re.match(r"^\S+\s+MX\s+preference\s*=\s*(\d+)\s*,\s*"
                     r"mail exchanger\s*=\s*(\S+)", line, re.I)
        if m:
            add(f"{m.group(1)} {m.group(2)}")
            in_answer = True
            continue

        # 3) TXT 记录本体。两种形态都要认：
        #    英文版拼接后： `google.com text = "v=spf1 ..."`
        #    中文版（无 text 关键字）： `google.com "cisco-ci-domain-verification=..."`
        m = re.match(r'^(\S+)\s+text\s*=\s*(.*)$', line, re.I)
        if m:
            add(m.group(2).strip().strip('"'))
            continue
        m = re.match(r'^\S+\s+"(.+)"\s*$', line)
        if m and "=" in m.group(1):
            add(m.group(1).strip())
            continue

        # 4) MX 展开行：`host internet address = 1.2.3.4`
        m = re.match(r"^(\S+)\s+(?:internet address|主机(?:地址)?)\s*[=:]\s*"
                     r"(\d+\.\d+\.\d+\.\d+)$", line, re.I)
        if m:
            add(f"{m.group(1)} {m.group(2)}")     # 采信为 MX 存在的证据
            continue

        # 5) MX 标准行 `10 mail.example.com.`
        if re.match(r"^\d+\s+\S+", line):
            add(line)
            continue

        # 6) A / CNAME
        #    中文版形如 `名称:    google.com` + `Address:  142.251.46.78`
        if re.match(r"^(\S+)\s+(?:internet address|主机(?:地址)?|别名|alias)\s*"
                    r"[=:]\s*", line, re.I):
            add(line)
            in_answer = True
            continue
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", line):
            add(line)
            in_answer = True
            continue
        # 中文版 CNAME 行：`别名  google.com ->  mail.x.com`
        if re.match(r"^别名\b", line) or "->" in line:
            add(line)
            in_answer = True
            continue
        # 中文版「名称: xxx」出现在头部之后即为目标域自身
        if in_answer and re.match(r"^名称\s*[:：]", line):
            continue

    return recs


def _txt(name):
    """取该域名的 TXT 记录（SPF/DMARC/DKIM 都是 TXT）。

    返回 (记录列表, 错误或 None)。**记录列表为空且错误为 None 才代表
    "该记录确实不存在"**；有错误则说明是查询失败，两者必须区分。
    """
    recs, err = _nslookup("TXT", name)
    # 只滤掉明显的服务器/头部残行，不能按长度过滤 ——
    # DKIM 吊销记录 `v=DKIM1; k=rsa; p=` 只有 19 个字符，
    # 按长度筛会把这类"存在但已失效"的记录误当成"未配置"。
    recs = [r for r in recs if len(r) >= 6 and " " not in r[:1]]
    return recs, err


def _unknown(item, err):
    """DNS 查询失败时的统一返回：标记为『无法判定』而非『失败』。"""
    # 域名本身不存在（NXDOMAIN）是确定的事实，直接判失败，
    # 不该混进"无法判定"里 —— 那会让用户以为是网络问题反复重试。
    if err and "NXDOMAIN" in err:
        return ("失败", "域名不存在（NXDOMAIN）—— 请确认域名拼写是否正确", {})
    return ("未知", f"无法查询 DNS —— {err}。此项未能判定，"
                     f"不能据此断定记录缺失", {"dns_error": err})


# --------------------------------------------------------------- 检查项

def check_spf(domain):
    """SPF 检查。重点看 -all 与查询次数。"""
    recs, err = _txt(domain)
    spf = [r for r in recs if "v=spf1" in r]
    if not spf:
        if err:
            return _unknown("SPF", err)
        return ("失败", "未配置 SPF。收件方无法确认你是否授权该服务器发信", {})
    if len(spf) > 1:
        return ("失败", f"存在 {len(spf)} 条 SPF 记录 —— 重复定义会导致 "
                        f"permerror，SPF 直接失效。应合并为一条",
                {"records": spf})
    rec = spf[0]
    info = {"record": rec}

    # 查询次数（RFC 7208 上限 10）
    lookups = sum(rec.lower().count(m) for m in SPF_MECHANISMS)
    info["lookups"] = lookups
    issues = []
    if lookups > 10:
        issues.append(f"DNS 查询 {lookups} 次，超过 RFC 7208 上限 10，会被判 permerror")

    # 结尾策略
    if "-all" not in rec.lower():
        if "~all" in rec.lower():
            # ~all 软失败在大企业是常见做法（Google 官方就是 ~all），
            # 冷邮件场景下仍建议 -all，但不应判为失败。
            return ("警告", "结尾是 ~all（软失败）。冷邮件建议改为 -all 更严格，"
                            "但大企业常用 ~all，可接受",
                    info)
        if "?all" in rec.lower():
            issues.append("结尾是 ?all（中性），几乎无保护作用，应改为 -all")
        else:
            issues.append("结尾缺少 -all/~all/?all，无处置策略")

    if issues:
        return ("失败", "；".join(issues), info)
    return ("通过", f"存在，硬失败(-all)，DNS 查询 {lookups} 次（上限 10）", info)


def _dkim_name(domain, s):
    """兼容两种输入：只给 selector（s1 / 20230601._domainkey）
    或给完整主机名（s1._domainkey.x.com）。

    服务商后台展示的常是完整主机名，用户直接复制过来是常态；
    反过来也会只复制 `_domainkey` 前缀那一段。这两种都要认对。

    判据必须是"是否已经含 _domainkey 段"，而不能只看有没有点 ——
    实测 `20230601._domainkey` 含点但不含完整主机名，
    若按"含点即视为完整"处理，会拼出
    `20230601._domainkey._domainkey.google.com` 这种永不存在的名字。
    """
    s = s.strip().rstrip(".")
    low = s.lower()
    if "_domainkey" in low:
        # 已含 _domainkey 段：若已以本域名结尾就是完整主机名，否则补域名
        if low.endswith(domain.lower()):
            return s
        # 已含至少两个点 → 是别的域下的完整主机名，直接用，不补域名
        return s if s.count(".") >= 2 else f"{s}.{domain}"
    return f"{s}._domainkey.{domain}"


def _scan_dkim(domain, selectors=None):
    """扫描 DKIM 记录。返回 (记录列表, DNS 错误列表)。"""
    found, dns_errs = [], []
    if not selectors:
        return found, dns_errs
    for s in selectors:
        name = _dkim_name(domain, s)
        recs, e = _txt(name)
        if e and "NXDOMAIN" not in e:
            dns_errs.append(e)
        for r in recs:
            if "p=" in r:
                # p= 为空表示该密钥已被吊销（revoked），记录存在但不可用
                revoked = re.search(r"p=\s*[\"']?\s*[\"']?\s*;?", r)
                found.append({"selector": s, "record": r, "name": name,
                              "revoked": bool(revoked and
                                              not re.search(r"p=\s*[A-Za-z0-9+/=]{10,}",
                                                            r))})
    return found, dns_errs


def check_dkim(domain, selectors=None):
    """DKIM 检查。

    selector 需从邮件服务商后台获取；本脚本接受两种写法 —— 只给 selector
    （如 `s1`）或直接给完整主机名（如 `s1._domainkey.example.com`）。

    selector 由各家服务商生成，**无法可靠自动枚举**。不提供时只做最小探测
    并明确提示，而不是逐个试十几个前缀 —— 实测那样既慢又会被 DNS 超时淹没，
    最后还是给不出可信结论。
    """
    if not selectors:
        found, dns_errs = _scan_dkim(domain, ["default"])
        if not found:
            found, dns_errs = _scan_dkim(domain, ["selector1"])
        probe_note = ("未提供 --selectors，仅探测了 default / selector1 两个前缀。"
                      "请从邮件服务商后台取真实 selector 后用 --selectors 重跑本项")
    else:
        found, dns_errs = _scan_dkim(domain, selectors)
        probe_note = ""

    if not found:
        # nslookup 偶发 2 秒超时导致误判，重试一次再下结论。
        found2, dns_errs2 = _scan_dkim(domain, selectors or ["default"])
        if found2:
            return ("通过", f"发现 {len(found2)} 个 selector："
                            f"{', '.join(f['selector'] for f in found2[:4])}"
                            f"（首次查询超时，重试后确认）",
                    {"selectors": [f["selector"] for f in found2],
                     "records": found2[:3], "first_error": dns_errs[:1]})
        if dns_errs or dns_errs2:
            return _unknown("DKIM", (dns_errs + dns_errs2)[0])
        return ("失败", "未发现 DKIM 记录。DKIM 才是把邮件与域名真正绑定的记录，"
                        "缺它无法通过大部分收件方验证。"
                        + probe_note,
                {"hint": "需用服务商提供的 selector 与公钥，selector 不能自己编"})

    # 密钥长度
    info = {"selectors": [f["selector"] for f in found],
            "count": len(found), "records": found[:3]}
    for f in found:
        m = re.search(r"p=([A-Za-z0-9+/=]+)", f["record"])
        if m:
            try:
                import base64
                der = base64.b64decode(m.group(1))
                bits = (len(der) - 34) * 8     # 减去 SubjectPublicKeyInfo 开销，得模数近似位长
                f["bits"] = bits
            except Exception:
                f["bits"] = None

    revoked = [f["selector"] for f in found if f.get("revoked")]
    if revoked:
        info["revoked"] = revoked
        return ("失败", f"selector {', '.join(revoked)} 的 p= 为空 —— "
                        f"该 DKIM 密钥已被吊销，发信无法通过验证。"
                        f"需在服务商后台重新生成并更新 DNS",
                info)

    sizes = [f.get("bits") for f in found if f.get("bits")]
    if sizes and max(sizes) < 2048:
        return ("警告", f"密钥长度约 {max(sizes)} 位，弱于推荐的 2048 位", info)
    msg = f"发现 {len(found)} 个 selector：{', '.join(info['selectors'][:4])}"
    if sizes:
        msg += f"（{max(sizes)} 位）"
    if probe_note:
        msg += "。" + probe_note
    return ("通过", msg, info)


def check_dmarc(domain):
    """DMARC 检查。冷邮件重点看 p 策略是否长期停在 none。"""
    name = f"_dmarc.{domain}"
    recs, err = _txt(name)
    dm = [r for r in recs if "v=DMARC1" in r]
    if not dm:
        if err and "NXDOMAIN" not in err:
            return _unknown("DMARC", err)
        return ("失败", "未配置 DMARC。DMARC 决定 SPF/DKIM 都失败时怎么处置，"
                        "也是防止他人仿冒你域名发信的主要手段", {})
    if len(dm) > 1:
        return ("失败", f"存在 {len(dm)} 条 DMARC 记录 —— 同一 _dmarc 名下只能有 1 条，"
                        f"重复会导致接收方策略不确定（可能取 p=none 而让防伪造失效）。"
                        f"应合并为 1 条",
                {"records": dm})
    rec = dm[0]
    info = {"record": rec}
    pm = re.search(r"[;\s]p=(\w+)", rec, re.I)
    policy = pm.group(1).lower() if pm else "none"
    info["policy"] = policy

    issues = []
    notes = []
    if policy == "none":
        notes.append("p=none 为观察期配置。确认 rua 报告无异常来源后，"
                     "应依次收紧到 quarantine、reject")
    rua = re.search(r"rua=mailto:([^\s;]+)", rec, re.I)
    if not rua:
        issues.append("缺少 rua=，收不到认证失败报告，等于无法发现冒用发信")
    else:
        info["rua"] = rua.group(1)
    adkim = re.search(r"adkim=([sr])", rec, re.I)
    aspf = re.search(r"aspf=([sr])", rec, re.I)
    info["alignment"] = {"adkim": adkim.group(1) if adkim else "(默认 relaxed)",
                         "aspf": aspf.group(1) if aspf else "(默认 relaxed)"}

    if issues:
        return ("失败", "；".join(issues), info)
    detail = f"p={policy}"
    if policy == "none":
        return ("警告", f"{detail}（观察期）。建议观察满 2 周后再收紧", info)
    if adkim and aspf and adkim.group(1).lower() == "s" \
            and aspf.group(1).lower() == "s":
        return ("通过", f"{detail}，strict 对齐", info)
    return ("通过", detail + ("；relaxed 对齐，防伪造能力较弱" if not notes else ""), info)


def check_mx(domain):
    """MX 检查。子域缺 MX 会让退信被静默丢弃。"""
    recs, err = _nslookup("MX", domain)
    # 中文版 nslookup 会把 MX 展开成 A 记录（`smtp.x.com internet address = 1.2.3.4`），
    # 标准版是 `10 mail.x.com.`。两种都算"有 MX"。
    mx = [r for r in recs if re.match(r"^\d+\s+\S+", r)
or re.search(r"(internet address|主机)", r, re.I)]
    if not mx:
        # nslookup 偶发 "DNS request timed out"（默认只等 2 秒），
        # 此时既可能是真的没配，也可能是超时。重试一次再下结论。
        recs2, err2 = _nslookup("MX", domain)
        mx = [r for r in recs2
              if re.match(r"^\d+\s+\S+", r)
              or re.search(r"(internet address|主机)", r, re.I)]
        if mx:
            return ("通过", f"存在，{len(mx)} 条记录"
                            f"（首次查询超时，重试后确认）",
                    {"records": mx[:3], "first_error": err})
        err = err2 or err
        if err and "NXDOMAIN" not in err:
            return _unknown("MX", err)
        return ("失败", "未配置 MX。退信与投诉通知无处接收，"
                        "bounce@ 地址不存在的域名退信会被直接丢弃", {})
    return ("通过", f"存在，{len(mx)} 条记录", {"records": mx[:3]})


def check_a(domain):
    """A 记录（仅提示，子域可能用 CNAME）。"""
    recs, err = _nslookup("A", domain)
    if recs:
        return ("通过", f"解析记录 {len(recs)} 条", {"records": recs[:2]})
    recs2, _ = _nslookup("CNAME", domain)
    if recs2:
        return ("通过", f"CNAME 指向 {recs2[0][:60]}", {})
    if err and "NXDOMAIN" not in err:
        return _unknown("A/CNAME", err)
    return ("警告", "无 A/CNAME 记录。若这是发送子域且邮件走外部服务，属正常", {})


def check_ptr(ip):
    """PTR / FCrDNS 检查。收件方核对 IP 反解是否等于 HELO 声明域。"""
    try:
        host = socket.gethostbyaddr(ip)[0]
    except Exception as e:
        return ("警告", f"无法反解该 IP：{type(e).__name__}",
                {"ip": ip})
    info = {"ip": ip, "ptr": host}
    # 正向确认：反解出的域名能否解析回该 IP
    try:
        fwd = socket.gethostbyname(host)
        info["forward"] = fwd
        if fwd == ip:
            return ("通过", f"反解为 {host}，正向一致（FCrDNS 通过）", info)
        return ("警告", f"反解为 {host}，但正向解析到 {fwd} —— "
                        f"FCrDNS 不一致，易被拒收", info)
    except Exception:
        return ("警告", f"反解为 {host}，但正向解析失败", info)


def check_blacklist(domain, ip=None):
    """黑名单查询。命中即需人工复核（存在误报）。

    判定不能只靠『有无 A 记录』：部分名单（如 SISA）对任意子域都返回通配 A
    记录（公网 IP），URIBL 在拒绝公开解析器查询时会回 127.0.0.1 + 『Query
    Refused』。两者都不是真实命中。正确做法：仅当 A 落在 127.0.0.0/8（RBL 标准
    回码段）且 TXT 未声明拒绝时，才计为命中。
    """
    targets = [(domain, "域名")]
    if ip:
        targets.append((ip, "IP"))
    hits, errs, refused = [], [], []
    for value, kind in targets:
        rev = ".".join(reversed(value.split(".")))
        for name, host in BLACKLIST_HOSTS.items():
            q = f"{rev}.{host}"
            arecs, aerr = _nslookup("A", q)
            trecs, terr = _nslookup("TXT", q)
            if aerr and terr:
                errs.append(f"{name}: {aerr}")
                continue
            # 拒绝公开查询（未授权解析器）：不算命中，单独标注
            if any("refused" in (t or "").lower() for t in trecs):
                refused.append(name)
                continue
            # 真实命中：A 落在 127.0.0.0/8（RBL 标准回码段）
            listed = [a for a in arecs if re.match(r"^127\.\d+\.\d+\.\d+$", a)]
            if listed:
                hits.append({"list": name, "kind": kind, "value": value,
                             "code": listed[0]})
    info = {"hits": hits, "queried": len(BLACKLIST_HOSTS) * len(targets),
            "refused": refused}
    if hits:
        return ("失败", f"命中 {len(hits)} 个黑名单："
                        + "、".join(f"{h['list']}({h['kind']},{h['code']})"
                                    for h in hits), info)
    if refused:
        return ("警告",
                f"{len(refused)} 个名单拒绝公开查询（"
                + "、".join(refused)
                + "），无法据此认定未命中，建议换授权 DNS 或用在线工具复核",
                info)
    if errs:
        return ("警告", f"部分名单查询失败（{len(errs)} 项），未能完整确认",
                {"errors": errs[:5]})
    return ("通过", "未发现黑名单记录（已排除通配/拒查误报）", info)


# --------------------------------------------------------------- 输出

ICON = {OK: "通过", WARN: "警告", FAIL: "失败", UNKNOWN: "未知"}


def run(domain, selectors=None, smtp_ip=None, blacklist=False):
    checks = []

    checks.append(("SPF", check_spf(domain)))
    checks.append(("DKIM", check_dkim(domain, selectors)))
    checks.append(("DMARC", check_dmarc(domain)))
    checks.append(("MX", check_mx(domain)))
    checks.append(("A/CNAME", check_a(domain)))
    if smtp_ip:
        checks.append(("PTR", check_ptr(smtp_ip)))
    if blacklist:
        checks.append(("黑名单", check_blacklist(domain, smtp_ip)))

    report = {"domain": domain, "smtp_ip": smtp_ip,
              "items": [{"name": n, "status": s, "message": m, "info": i}
                        for n, (s, m, i) in checks]}
    n_fail = sum(1 for _, (s, _, _) in checks if s == FAIL)
    n_warn = sum(1 for _, (s, _, _) in checks if s == WARN)
    n_unknown = sum(1 for _, (s, _, _) in checks if s == UNKNOWN)
    report["summary"] = {"fail": n_fail, "warn": n_warn,
                         "unknown": n_unknown,
                         "pass": len(checks) - n_fail - n_warn - n_unknown}
    return report


def format_report(rep):
    lines = [f"\n域名诊断：{rep['domain']}"]
    if rep.get("smtp_ip"):
        lines.append(f"发信服务器：{rep['smtp_ip']}")
    lines.append("=" * 60)
    for it in rep["items"]:
        lines.append(f"[{ICON[it['status']]}] {it['name']:<8}  {it['message']}")
    s = rep["summary"]
    lines.append("=" * 60)
    total = len(rep["items"])
    if s.get("unknown"):
        lines.append(f"[注意] {s['unknown']}/{total} 项因 DNS 查询失败未能判定 "
                     f"—— 请检查网络/代理后重跑，不要据此认为记录缺失")
    if s["fail"]:
        lines.append(f"结论：{s['fail']} 项失败 / {s['warn']} 项警告 "
                     f"—— 发送前必须修复失败项")
    elif s.get("unknown") and s["pass"] == 0:
        # 全部未判定时绝不能说"达标" —— 那等于什么都没查就放行发信。
        lines.append("结论：无法判定 —— 所有检查项都因 DNS 查询失败而未得出结果，"
                     "此时不得据此认为域名配置达标。请修复网络后重跑。")
    elif s["warn"]:
        lines.append(f"结论：无失败项，{s['warn']} 项警告 —— 可以发送，建议择机优化")
    else:
        lines.append("结论：全部通过，域名配置达标")
    lines.append("\n详细配置步骤见 references/domain-warming.md")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description="发件域名健康检测（SPF/DKIM/DMARC/MX/PTR/黑名单）")
    ap.add_argument("domain", help="发件域名或子域，如 outreach.yourdomain.com")
    ap.add_argument("--selectors", nargs="*", default=None,
                    help="DKIM selector（s1 或 s1._domainkey.example.com 均可）")
    ap.add_argument("--smtp-ip", default=None,
                    help="发信服务器 IP，用于检查 PTR/FCrDNS")
    ap.add_argument("--blacklist", action="store_true", help="查询黑名单")
    ap.add_argument("--dns-timeout", type=int, default=20,
                    help="单次 DNS 查询等待秒数，默认 20。网络慢时可调大")
    ap.add_argument("--json", action="store_true",
                    help="输出 JSON；该模式正常完成恒 exit 0，是否达标看 summary.fail")
    a = ap.parse_args()

    global DNS_TIMEOUT
    DNS_TIMEOUT = max(1, a.dns_timeout)

    d = a.domain.strip().lower().replace("https://", "").replace("http://", "")
    d = d.rstrip("/").split("/")[0]
    if "@" in d:
        d = d.split("@")[-1]
    if not d or "." not in d:
        print("[错误] 域名格式不正确", file=sys.stderr)
        return 3

    try:
        rep = run(d, a.selectors, a.smtp_ip, a.blacklist)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[失败] {type(e).__name__}: {e}", file=sys.stderr)
        return 2

    print(json.dumps(rep, ensure_ascii=False, indent=2) if a.json
          else format_report(rep))
    s = rep["summary"]
    # --json 的消费方是 LLM（skill_execute），结论看 JSON 的 summary；
    # 检测完成即 exit 0，避免非零退出码被投影层误记为 failed（tr_runner_3a12186 案）。
    if a.json:
        return 0
    if s["fail"]:
        return 2
    # 全部未判定时退出码不能是 0，否则脚本调用方会误以为域名已达标。
    if s["pass"] == 0 and s.get("unknown"):
        return 2
    return 1 if (s["warn"] or s.get("unknown")) else 0


if __name__ == "__main__":
    sys.exit(main())