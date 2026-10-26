#!/usr/bin/env python3
"""邮箱验证工具: 格式校验 + 域名 MX 记录检查 + 常见拼写错误纠正。

用法:
  python email_verify.py <email> [<email> ...]
  python email_verify.py --file emails.txt
  python email_verify.py --file emails.txt --json
  python email_verify.py --pattern "first.last@" --domain example.com --names "John Smith" "Jane Doe"

说明: 本工具只做本地校验, 不发送探测邮件。要获得真实可投递性结论,
需接入 Hunter.io / Snov.io / NeverBounce / ZeroBounce 等验证服务的 API。
"""

import argparse
import json
import random
import re
import socket
import sys
from pathlib import Path

# 与 site_probe.py / send_emails.py / lead_db.py 共用同一套职能判定口径
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from email_rules import assess_email, load_target_config
except ImportError as e:  # pragma: no cover
    raise SystemExit(f"[错误] 无法导入 email_rules.py：{e}\n"
                     f"请确认 scripts/ 下同时存在 email_rules.py 与 email_verify.py")

# 常见域名拼写错误 -> 正确拼写 (输入被当作域名时纠正)
DOMAIN_TYPOS = {
    "gmai.com": "gmail.com", "gmial.com": "gmail.com", "gmail.co": "gmail.com",
    "gmail.cm": "gmail.com", "hotmai.com": "hotmail.com", "hotmail.co": "hotmail.com",
    "outlok.com": "outlook.com", "outloo.com": "outlook.com", "yahooo.com": "yahoo.com",
    "yaho.com": "yahoo.com", "icloud.co": "icloud.com", "qq.co": "qq.com",
    "163.co": "163.com", "foxmai.com": "foxmail.com", "aliyawwork.com": "alibaba.com",
}

# 不适合冷邮件的角色邮箱 (仍可用于验证域名有效性)
ROLE_PREFIXES = {
    "info", "sales", "contact", "admin", "office", "support", "hello", "help",
    "enquiries", "inquiry", "enquiry", "marketing", "mail", "noreply", "no-reply",
    "postmaster", "abuse", "billing", "accounts", "hr", "jobs", "careers",
}

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "yahoo.com", "yahoo.co.jp", "icloud.com", "me.com", "aol.com",
    "protonmail.com", "proton.me", "qq.com", "163.com", "126.com",
    "foxmail.com", "sina.com", "sohu.com", "naver.com", "daum.net",
    "yandex.ru", "web.de", "gmx.de", "libero.it", "orange.fr", "wanadoo.fr",
    "t-online.de", "sfr.fr", "btinternet.com", "sky.com", "virginmedia.com",
    "rediffmail.com", "mail.ru", "bk.ru", "list.ru", "inbox.ru",
}

FREE_EMAIL_DOMAINS = PERSONAL_DOMAINS

def name_pattern_builders():
    """Return builders as (label, fn(first, last, initial) -> local-part template).

    Each builder returns the part before '@'; the domain is appended by the caller.
    Built explicitly rather than via str.format so malformed placeholders can never
    raise at runtime.
    """
    return [
        ("first.last", lambda fi, la, ini: f"{fi}.{la}"),
        ("firstlast", lambda fi, la, ini: f"{fi}{la}"),
        ("first", lambda fi, la, ini: fi),
        ("first_last", lambda fi, la, ini: f"{fi}_{la}"),
        ("last.first", lambda fi, la, ini: f"{la}.{fi}"),
        ("lastfirst", lambda fi, la, ini: f"{la}{fi}"),
        ("f.last", lambda fi, la, ini: f"{ini}.{la}"),
        ("flast", lambda fi, la, ini: f"{ini}{la}"),
        ("first-last", lambda fi, la, ini: f"{fi}-{la}"),
        ("last", lambda fi, la, ini: la),
        ("f", lambda fi, la, ini: ini),
    ]


def syntax_check(email):
    """Returns (ok, reason)."""
    email = (email or "").strip()
    if not email:
        return False, "empty"
    if " " in email:
        return False, "contains space"
    if not EMAIL_RE.match(email):
        # find the likely defect for a clearer message
        if email.count("@") != 1:
            return False, f"must contain exactly one '@' (found {email.count('@')})"
        local, _, domain = email.partition("@")
        if not local:
            return False, "empty local part before '@'"
        if "." not in domain:
            return False, f"domain '{domain}' has no dot / invalid TLD"
        if ".." in email:
            return False, "contains consecutive dots"
        return False, "does not match a valid email pattern"
    if ".." in email:
        return False, "contains consecutive dots"
    local = email.split("@")[0]
    if local.startswith(".") or local.endswith("."):
        return False, "local part starts or ends with a dot"
    return True, "ok"


def normalize_domain(domain):
    d = (domain or "").strip().lower()
    return DOMAIN_TYPOS.get(d, d)


def mx_check(domain):
    """Check MX (then A) records. Returns (has_mail, detail)."""
    domain = normalize_domain(domain)
    if not domain:
        return False, "empty domain"

    # Prefer dnspython if available for reliable MX lookup
    try:
        import dns.resolver  # type: ignore
        try:
            answers = dns.resolver.resolve(domain, "MX")
            if answers:
                mx = sorted(str(r.exchange).rstrip(".") for r in answers)[0]
                return True, f"MX -> {mx} ({len(answers)} records)"
        except Exception as exc:
            mx_err = str(exc)
    except ImportError:
        mx_err = "dnspython not installed"

    # Fallback: check if the domain resolves at all
    try:
        socket.setdefaulttimeout(6)
        socket.getaddrinfo(domain, None)
        return True, f"domain resolves (MX query unavailable: {mx_err})"
    except socket.gaierror as exc:
        return False, f"domain does not resolve: {exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"lookup failed: {exc}"


def classify(email, target_functions=None, exclude_functions=None):
    """Full single-address verification. Returns a dict."""
    ok, reason = syntax_check(email)
    result = {
        "email": email,
        "syntax_ok": ok,
        "syntax_reason": reason,
        "domain": "",
        "domain_normalized": "",
        "domain_typo_fixed": False,
        "mx_ok": False,
        "mx_detail": "",
        "local_part": "",
        "is_role": False,
        "is_free_provider": False,
        "function": "",
        "relevance": "",
        "risk": "unknown",
        "verdict": "",
    }
    if not ok:
        result["verdict"] = "INVALID"
        result["risk"] = "high"
        return result

    raw_local, _, domain = email.strip().partition("@")
    norm = normalize_domain(domain)
    result["local_part"] = raw_local.lower()
    result["domain"] = domain.lower()
    result["domain_normalized"] = norm
    result["domain_typo_fixed"] = norm != domain.lower()

    result["mx_ok"], result["mx_detail"] = mx_check(domain)
    result["is_role"] = raw_local.lower() in ROLE_PREFIXES
    result["is_free_provider"] = norm in FREE_EMAIL_DOMAINS

    # 职能与相关性（与全技能同一套口径）
    a = assess_email(email, target_functions, exclude_functions)
    result["function"] = a["function"]
    result["relevance"] = a["relevance"]
    result["function_reason"] = a["reason"]

    if not result["mx_ok"]:
        result["verdict"] = "UNDELIVERABLE (no MX)"
        result["risk"] = "high"
    elif a["relevance"] == "irrelevant":
        result["verdict"] = (f"IRRELEVANT FUNCTION ({a['function']}) — "
                             f"not a target for this product/service")
        result["risk"] = "high"
    elif result["domain_typo_fixed"]:
        result["verdict"] = "SUSPECT (domain typo, probably intended: " + norm + ")"
        result["risk"] = "high"
    elif a["relevance"] == "generic":
        result["verdict"] = (f"PUBLIC MAILBOX ({a['function']}) — valid domain, "
                             f"but no named owner; look for a relevant "
                             f"department or person")
        result["risk"] = "medium"
    elif a["relevance"] == "unknown":
        result["verdict"] = ("FUNCTION UNCLEAR — valid syntax and MX, but the "
                             "local part does not reveal a function; verify manually")
        result["risk"] = "medium"
    elif result["is_role"]:
        result["verdict"] = "ROLE ADDRESS (domain valid; use for domain verification only)"
        result["risk"] = "medium"
    elif result["is_free_provider"]:
        result["verdict"] = "FREE PROVIDER (valid syntax; higher spam risk; consider skipping)"
        result["risk"] = "medium"
    else:
        result["verdict"] = "Plausible (syntax ok, MX ok; still unverified deliverability)"
        result["risk"] = "low"
    return result


def slug(name):
    parts = [p for p in re.split(r"[\s\-_]+", (name or "").strip()) if p]
    return parts or [""]


def generate_patterns(names, domain):
    """Generate candidate addresses for given full names at a domain."""
    out = []
    for name in names:
        parts = slug(name)
        if not parts[0]:
            continue
        first = parts[0].lower()
        last = parts[-1].lower() if len(parts) > 1 else ""
        initial = first[:1]
        for label, fn in name_pattern_builders():
            local = fn(first, last, initial)
            if not local or "@" in local:
                continue
            if "." in local and (local.startswith(".") or local.endswith(".") or ".." in local):
                continue
            addr = f"{local}@{domain}"
            if addr not in out:
                out.append(addr)
    return out


def main():
    ap = argparse.ArgumentParser(description="Email format and MX verification")
    ap.add_argument("emails", nargs="*", help="email addresses to verify")
    ap.add_argument("--file", help="file with one email per line (# comments allowed)")
    ap.add_argument("--json", action="store_true", help="output JSON")
    ap.add_argument("--pattern", help="email pattern hint, e.g. first.last@")
    ap.add_argument("--domain", help="domain for pattern generation")
    ap.add_argument("--names", nargs="*", help="full names to generate candidates for")
    ap.add_argument("--campaign", help="campaign dir, reads contact_targets.json")
    ap.add_argument("--target-functions",
                    help="comma-separated relevant functions, overrides campaign")
    ap.add_argument("--exclude-functions",
                    help="comma-separated excluded functions, overrides campaign")
    args = ap.parse_args()

    tcfg = load_target_config(args.campaign) if args.campaign else {}
    targets = tcfg.get("target_functions")
    excludes = tcfg.get("exclude_functions")
    if args.target_functions:
        targets = [x.strip().lower() for x in args.target_functions.split(",")
                   if x.strip()]
    if args.exclude_functions:
        excludes = [x.strip().lower() for x in args.exclude_functions.split(",")
                    if x.strip()]

    if args.names and args.domain:
        domain = args.domain.lower()
        cands = generate_patterns(args.names, domain)
        if args.json:
            print(json.dumps({"domain": domain, "candidates": cands}, indent=2))
        else:
            print(f"# candidate addresses for {args.names} @ {domain}")
            print(f"# verify each with: python email_verify.py <address>\n")
            for c in cands:
                print(c)
        return 0

    emails = list(args.emails)
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    emails.append(line)
    if not emails:
        ap.print_help()
        return 1

    results = [classify(e, targets, excludes) for e in emails]

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0

    ok_count = 0
    for r in results:
        flag = "OK  " if r["verdict"].startswith("Plausible") else "WARN"
        if flag == "OK  ":
            ok_count += 1
        print(f"[{flag}] {r['email']}")
        print(f"        verdict : {r['verdict']}")
        print(f"        function: {r['function']}  relevance: {r['relevance']}")
        print(f"        domain  : {r['domain']}"
              + (f"  (typo -> {r['domain_normalized']})" if r["domain_typo_fixed"] else ""))
        print(f"        mx      : {r['mx_detail']}")
        if r["is_role"]:
            print(f"        note    : role address, not suitable for personalised outreach")
        if r["is_free_provider"]:
            print(f"        note    : free provider mailbox, elevated spam risk")
        print()

    print(f"summary: {ok_count}/{len(results)} plausible, "
          f"{len(results) - ok_count} need attention")
    relevant = sum(1 for r in results if r.get("relevance") == "relevant")
    print(f"valid targets (relevant function): {relevant}/{len(results)}")
    if relevant < len(results):
        print("Addresses that are not 'relevant' are not target contacts — "
              "either irrelevant functions (recruiting/finance/legal) or public "
              "mailboxes. Find a relevant department or person instead.")
    print("\nReminder: this checks syntax + MX only. True deliverability requires a")
    print("verification service (Hunter / Snov / NeverBounce / ZeroBounce).")
    print("Never send to addresses marked 'guessed' without such verification.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)