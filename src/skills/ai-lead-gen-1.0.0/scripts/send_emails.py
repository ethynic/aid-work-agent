#!/usr/bin/env python3
"""冷邮件群发引擎 (SMTP)。

内置能力:
  - 每域日限额 (默认 3 封/24h)
  - 全局日限额与 ramp-up 提示
  - 永久退订黑名单 (unsubscribed.csv) 与抑制列表
  - 随机化延迟, 避免固定节奏被判定为机器行为
  - 退信分类: 硬退信自动退订, 软退信退避重试
  - 失败不中断整批 (除黑名单类错误外)
  - 发送日志 CSV

用法 (本系统版: SMTP 凭据经邮箱三层解析自动获取, 不再使用 cfg.json):
  # 0) 由 lead_db.py export-send-queue 生成队列文件后:
  # 1) 干跑, 不实际发送 —— 必须先跑这一步
  python send_emails.py --campaign-id ID --dry-run

  # 2) 正式发送 (先只发 3 封测试)
  python send_emails.py --campaign-id ID --limit 3

  # 3) 继续发送剩余
  python send_emails.py --campaign-id ID

队列文件由 lead_db.py export-send-queue 从 bs_ai_lead_gen_emails 拉取
approved 状态草稿生成 (logs/send_queue.json), 每项含 email_id 用于发送
结果回写数据库。也可用 --queue-file 显式指定其他队列文件。

  主题行规则:
    - 必写 subject_a / subject_b 两版, 各 <=35 字符, 只差一个变量
    - 公司简称不写进主题行, 放签名 (与姓名/职位/联系方式一起)
    - ab_group=A|B 在写草稿时交替分配; 缺省时按收件人地址哈希稳定分流
    - 兼容旧格式: 只有 "subject" 时按单版发送, 不参与 A/B 统计

退订名单: <tenant>/lead-gen/unsubscribed.csv (email,date,reason) ——
租户级共享, 跨 campaign 生效, 脚本自动读写。
"""

import argparse
import csv
import email.utils
import hashlib
import json
import random
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 硬退信 (邮箱无效) -> 立即永久退订
HARD_BOUNCE_CODES = ["550", "551", "553", "556", "5.1.1", "5.1.2", "5.1.3",
                     "5.1.10", "5.2.1", "user unknown", "does not exist",
                     "not found", "invalid recipient", "recipient not found"]
# 退信类黑名单 -> 立即停止全部发送
BLACKLIST_CODES = ["5.7.1", "blacklist", "blacklisted", "blocked", "reputation"]
# 软退信 (临时) -> 可重试
SOFT_BOUNCE_CODES = ["450", "451", "452", "421", "4.2.0", "4.2.1", "try again",
                     "temporarily", "rate limit", "too many"]

MIN_INTERVAL_SEC = 30
MAX_RETRY = 2

# Role mailboxes: valid for domain verification, poor for personalised outreach.
ROLE_PREFIXES = {
    "info", "sales", "contact", "admin", "office", "support", "hello", "help",
    "enquiries", "inquiry", "enquiry", "marketing", "mail", "noreply", "no-reply",
    "postmaster", "abuse", "billing", "accounts", "hr", "jobs", "careers",
}

# 职能相关性判定与 site_probe.py 共用同一套口径（email_rules.py）。
# 招聘/财务/法务/公关等无关职能与公开窗口地址，在发送前直接拦下 ——
# 它们不是「与当前产品对应的负责人或部门邮箱」，发过去只会是无效触达。
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from email_rules import assess_email, load_target_config
    from mailbox_resolver import resolve_mailbox_or_die
    from lead_db import (campaign_dir, get_conn, get_identity, get_campaign,
                         NamedTupleCursor)
except ImportError as e:  # pragma: no cover
    raise SystemExit(f"[错误] 无法导入同目录脚本：{e}\n"
                     f"请确认 scripts/ 下同时存在 email_rules.py / mailbox_resolver.py / lead_db.py 与 send_emails.py")

# Wording that commonly trips spam filters. Checked in subject + body.
SPAM_TRIGGER_WORDS = [
    "100%", "free", "guarantee", "risk-free", "act now", "urgent",
    "limited time", "last chance", "no obligation", "click here",
    "$$$", "!!!", "best price", "lowest price", "cheap", "winner",
]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_json(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("emails") or data.get("items") or [data]
    return data


def pick_subject(item):
    """Resolve which subject line to send, and its A/B group.

    Priority:
      1. subject_a + subject_b with an explicit ab_group -> honour the assignment
         made at draft time (the plan stays reviewable and stable across resumed runs)
      2. subject_a + subject_b without ab_group -> deterministic hash split on the
         recipient address (stable across runs, roughly balanced, no RNG)
      3. a legacy single "subject" -> sent as-is, not counted as an A/B test

    Returns (subject, group) where group is "A", "B" or "none".
    """
    a = (item.get("subject_a") or "").strip()
    b = (item.get("subject_b") or "").strip()
    if a and b:
        group = (item.get("ab_group") or "").strip().upper()
        if group not in ("A", "B"):
            addr = (item.get("to") or "").strip().lower()
            digest = hashlib.sha1(addr.encode("utf-8")).digest()
            group = "A" if digest[0] % 2 == 0 else "B"
        return (a if group == "A" else b), group
    if a or b:
        return (a or b), "none"
    return (item.get("subject") or "").strip(), "none"


def log_entry(item, status, error=""):
    """One send_log row. Carries ab_group so results can be split later."""
    return {
        "timestamp": now_iso(),
        "to": (item.get("to") or "").strip().lower(),
        "company": item.get("company", ""),
        "subject": item.get("subject", ""),
        "ab_group": item.get("_ab", ""),
        "status": status,
        "error": error,
        "email_source": item.get("email_source", ""),
        "confidence": item.get("confidence", ""),
    }


def load_unsubscribed(campaign_dir):
    path = Path(campaign_dir) / "unsubscribed.csv"
    if not path.exists():
        return {}
    out = {}
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("email"):
                out[row["email"].strip().lower()] = row
    return out


def save_unsubscribed(campaign_dir, data):
    path = Path(campaign_dir) / "unsubscribed.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["email", "date", "reason"])
        w.writeheader()
        for row in data.values():
            w.writerow({"email": row["email"], "date": row.get("date", ""),
                        "reason": row.get("reason", "")})
    print(f"unsubscribed list saved: {len(data)} entries -> {path}")


def load_log(campaign_dir):
    path = Path(campaign_dir) / "logs" / "send_log.csv"
    rows = []
    if path.exists():
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    return path, rows


def save_log(log_path, rows):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["timestamp", "to", "company", "subject", "ab_group", "status",
            "error", "email_source", "confidence"]
    with open(log_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def domain_of(addr):
    return (addr or "").strip().lower().rpartition("@")[2]


def classify_bounce(code, msg):
    text = f"{code} {msg}".lower()
    for k in BLACKLIST_CODES:
        if k in text:
            return "blacklist"
    for k in HARD_BOUNCE_CODES:
        if k in text:
            return "hard"
    for k in SOFT_BOUNCE_CODES:
        if k in text:
            return "soft"
    return "unknown"


# Warm-up ramp. The input is the LIFETIME number of successful sends from this
# domain, not today's count: domain reputation accumulates over time, so a new
# day must not reset the allowance. A fresh domain therefore ramps:
#
#   cumulative sent    daily cap
#   0 - 49             20
#   50 - 149           50
#   150 - 499          100
#   500+               200   <- production baseline
#
# With a dedicated IP and a clean record you may deliberately raise
# daily_limit to 300-500; the script only warns, it does not block.
RAMP_TABLE = [
    (50, 20),
    (150, 50),
    (500, 100),
]
RAMP_PRODUCTION = 200
RAMP_MIN_DAILY = 20


def rampup_cap(total):
    """Recommended daily cap for a domain that has sent `total` emails so far."""
    for threshold, cap in RAMP_TABLE:
        if total < threshold:
            return cap
    return RAMP_PRODUCTION


def build_message(item, cfg):
    msg = EmailMessage()
    from_addr = cfg["from_email"]
    msg["From"] = f"{cfg.get('from_name', '')} <{from_addr}>".strip()
    to_name = item.get("to_name", "").strip()
    msg["To"] = f"{to_name} <{item['to']}>" if to_name else item["to"]
    msg["Subject"] = item["subject"]
    msg["Reply-To"] = cfg.get("reply_to", from_addr)
    msg["Message-ID"] = email.utils.make_msgid(domain=domain_of(from_addr))
    msg["Date"] = email.utils.formatdate(localtime=True)

    body = item["body"].replace("\r\n", "\n")

    # Compliance footer: required in most jurisdictions.
    footer_parts = []
    unsub = cfg.get("unsubscribe_url")
    if unsub:
        footer_parts.append(f'To stop receiving these emails, reply "unsubscribe" '
                            f'or use {unsub}')
    addr = cfg.get("postal_address")
    if addr:
        footer_parts.append(addr)
    if footer_parts:
        body = body.rstrip() + "\n\n---\n" + "\n".join(footer_parts)

    msg.set_content(body)
    return msg


def connect(cfg):
    host = cfg["smtp_host"]
    port = int(cfg.get("smtp_port", 587))
    # 密码只经邮箱三层解析在内存中解密获得（mailbox_resolver），
    # 禁止回退到系统 .env（SMTP_*）——那是平台级通知邮箱，本技能不得使用。
    password = cfg.get("smtp_password") or ""
    if not password:
        raise SystemExit("SMTP password missing: 解析到的邮箱配置缺少密码，"
                         "请到「邮箱设置」重新保存")

    if cfg.get("smtp_secure"):
        ctx = ssl.create_default_context()
        server = smtplib.SMTP_SSL(host, port, timeout=30, context=ctx)
    elif cfg.get("smtp_starttls"):
        server = smtplib.SMTP(host, port, timeout=30)
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
    else:
        # encryption=none：明文连接（部分内网/自建网关无 TLS），不加 STARTTLS
        server = smtplib.SMTP(host, port, timeout=30)
        server.ehlo()
    user = cfg.get("smtp_user") or cfg["from_email"]
    if user:
        server.login(user, password)
    return server


def build_cfg(campaign, mailbox, cdir):
    """组装发送 cfg：SMTP 凭据来自三层解析（内存解密，不落盘不打印），
    发件身份（from_name/退订说明/通信地址）来自租户级 sender_profile.json，
    日限额来自 campaign 行（不再来自 cfg.json）。"""
    from sender_profile import load_profile
    profile = load_profile(Path(cdir).parent / "sender_profile.json") or {}
    sender = profile.get("sender") or {}
    enc = (mailbox.get("smtp_encryption") or "ssl").strip().lower()
    return {
        "smtp_host": mailbox["smtp_server"],
        "smtp_port": mailbox["smtp_port"],
        "smtp_user": mailbox.get("smtp_user") or "",
        "smtp_password": mailbox["smtp_password"],
        "smtp_secure": enc == "ssl",
        "smtp_starttls": enc == "tls",
        "from_email": mailbox["email_address"],
        "from_name": sender.get("from_name", ""),
        # Reply-To 是回信监控的归属锚点：优先 campaign 指定，缺省=发件邮箱（同源）
        "reply_to": campaign.get("reply_mailbox") or mailbox["email_address"],
        "unsubscribe_url": sender.get("unsubscribe_url", ""),
        "postal_address": sender.get("postal_address", ""),
        "daily_limit": int(campaign.get("daily_limit") or 20),
        "per_domain_daily_limit": 3,
        "delay_seconds": 90,
        "random_delay_extra": 60,
    }


def record_result(tenant_id, email_id, status, message_id=None, error=None):
    """发送结果回写 bs_ai_lead_gen_emails（尽力而为，失败不阻断发送主流程）。

    status 取值与 lead_db.VALID_EMAIL_STATUS 对齐：
    sent / failed / bounced / draft（draft 用于把拦截项退回，避免重复进队列）。
    """
    try:
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE bs_ai_lead_gen_emails SET status = %s, "
                "message_id = COALESCE(%s, message_id), "
                "error = COALESCE(%s, error), "
                "sent_at = CASE WHEN %s = 'sent' THEN CURRENT_TIMESTAMP ELSE sent_at END, "
                "retry_count = CASE WHEN %s = 'failed' THEN retry_count + 1 ELSE retry_count END, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s AND tenant_id = %s",
                (status, message_id, error, status, status, email_id, tenant_id),
            )
            # get_db_connection 退出时无条件 rollback，必须显式提交
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: 发送结果回写数据库失败 email_id={email_id}: {exc}")


def cmd_dry_run(items, cfg, unsubscribed, log_rows=None, target_config=None):
    print("=" * 72)
    print("DRY RUN — no email will be sent. Review every item below.")
    print("=" * 72)

    tcfg = target_config or {}
    targets = tcfg.get("target_functions")
    excludes = tcfg.get("exclude_functions")

    lifetime_sent = count_sent(log_rows or [])
    domains = {}
    groups = {}
    for i, item in enumerate(items, 1):
        to = item.get("to", "").strip().lower()
        d = domain_of(to)
        domains[d] = domains.get(d, 0) + 1
        flags = []
        if to in unsubscribed:
            flags.append("SKIP: on unsubscribe list")
        if item.get("confidence") == "low":
            flags.append("BLOCK: low confidence — verify address before sending, "
                         "or switch channel to the website contact form")
        if item.get("email_source") == "guessed":
            flags.append("WARN: guessed address — verify before sending")
        if item.get("confidence") not in ("high", "medium", "low"):
            flags.append("BLOCK: confidence field missing or invalid")

        # 职能相关性闸门：不是「与产品对应的负责人/部门邮箱」的一律拦下
        a = assess_email(to, targets, excludes)
        if a["relevance"] == "irrelevant":
            flags.append(f"BLOCK: irrelevant function — 职能 `{a['function']}` "
                         f"与本次推销无关（{a['reason']}）；请改找对口负责人/部门邮箱")
        elif a["relevance"] == "generic":
            flags.append(f"WARN: public mailbox — `{a['function']}` 是公开联系窗口，"
                         f"无具体负责人；建议改用官网表单或换路径找对口邮箱")
        elif a["relevance"] == "unknown":
            flags.append(f"WARN: function unclear — {a['reason']}；需人工确认是否对口")
        # 联系人表里显式标注的 relevance 与自动判定冲突时，取更严的一档
        declared = (item.get("email_relevance") or "").strip().lower()
        if declared and declared != a["relevance"] and declared == "irrelevant":
            flags.append("BLOCK: contact record marks this address irrelevant "
                         f"({item.get('email_function', '')})")

        subj, ab = pick_subject(item)
        groups[ab] = groups.get(ab, 0) + 1
        sa = (item.get("subject_a") or "").strip()
        sb = (item.get("subject_b") or "").strip()
        if not subj:
            flags.append("BLOCK: no subject")
        if not item.get("body", "").strip():
            flags.append("BLOCK: no body")

        if sa or sb:
            if not (sa and sb):
                flags.append("WARN: only one subject variant — A/B needs both A and B")
            elif sa.lower() == sb.lower():
                flags.append("WARN: subject A and B are identical — nothing to test")
            for label, s in (("A", sa), ("B", sb)):
                if s.lower().startswith(("re:", "fwd:", "fw:")):
                    flags.append(f"BLOCK: subject {label} mimics a reply "
                                 "(CAN-SPAM violation)")
                if len(s) > 60:
                    flags.append(f"WARN: subject {label} longer than 60 chars")
                elif len(s) > 35:
                    flags.append(f"WARN: subject {label} is {len(s)} chars "
                                 "(guideline: <=35)")
            if sa and sb and abs(len(sa) - len(sb)) > 8:
                flags.append("WARN: A/B length gap > 8 chars — length becomes a "
                             "second variable")
        else:
            if subj.lower().startswith(("re:", "fwd:", "fw:")):
                flags.append("BLOCK: subject mimics a reply (CAN-SPAM violation)")
            if len(subj) > 60:
                flags.append("WARN: subject longer than 60 chars")
            flags.append("INFO: single subject — this email is outside the A/B test")

        hits = [w for w in SPAM_TRIGGER_WORDS
                if w in f"{sa} {sb} {subj} {item.get('body', '')}".lower()]
        if hits:
            flags.append(f"WARN: spam-trigger wording: {hits}")
        if to.split("@")[0].lower() in ROLE_PREFIXES:
            flags.append("WARN: role address — personalise outreach to a named person instead")

        print(f"\n--- [{i}/{len(items)}] {item.get('company','')} ---")
        print(f"To      : {item.get('to_name','')} <{to}>")
        if sa or sb:
            print(f"A/B grp : {ab}")
            print(f"Subject A: {sa}")
            print(f"Subject B: {sb}")
            print(f"  -> sends  : {subj}")
        else:
            print(f"Subject : {subj}")
        print(f"Source  : {item.get('email_source','?')} / {item.get('confidence','?')}")
        if flags:
            for f in flags:
                print(f"  {f}")
        print("Body:")
        for line in item.get("body", "").split("\n"):
            print(f"    {line}")

    per_domain_limit = cfg.get("per_domain_daily_limit", 3)
    over = {d: c for d, c in domains.items() if c > per_domain_limit}
    cap = plan_capacity(items, log_rows or [],
                        int(cfg.get("daily_limit", 50)), per_domain_limit)
    base_delay = max(MIN_INTERVAL_SEC, int(cfg.get("delay_seconds", 90)))
    extra = int(cfg.get("random_delay_extra", 60))
    avg_delay = base_delay + extra / 2
    est_minutes = int(len(items) * avg_delay / 60)

    print("\n" + "=" * 72)
    print(f"total emails   : {len(items)}")
    print(f"unique domains : {len(domains)}")
    print(f"daily_limit    : {cfg.get('daily_limit', 'unset')}")
    print(f"per-domain cap : {per_domain_limit}")
    print(f"lifetime sent  : {lifetime_sent} (drives the warm-up ramp)")
    print(f"warmup cap     : ~{rampup_cap(lifetime_sent)}/day at {lifetime_sent} "
          f"cumulative sends")
    print(f"sendable today : ~{cap['room']} (per-domain cap is the binding limit)")
    if cap["shortfall"]:
        print(f"cannot send today: {cap['shortfall']} emails — "
              f"{len(cap['capped'])} recipient domains hit the "
              f"{per_domain_limit}/24h cap")
    print(f"avg gap        : ~{avg_delay:.0f}s  ->  est. run time "
          f"~{est_minutes // 60}h{est_minutes % 60:02d}m for {len(items)} emails")
    if est_minutes > 240:
        print("note: this run exceeds 4h. Use --max-minutes to split it, or "
              "lower delay_seconds (trade-off: faster = riskier for filters).")
    if groups.get("A") or groups.get("B"):
        print(f"A/B split      : A={groups.get('A', 0)}  B={groups.get('B', 0)}  "
              f"no-A/B={groups.get('none', 0)}")
    if over:
        print(f"\nEXCEEDS per-domain cap (will be split across days): {over}")
    else:
        print("per-domain limits OK")
    print("\nReminder: verify legal requirements in references/email-compliance.md")
    print("before sending, and get explicit user approval.")


def count_sent(rows):
    """Lifetime count of successfully sent emails (drives the warm-up ramp)."""
    return sum(1 for r in (rows or []) if r.get("status") == "sent")


def plan_capacity(queue, log_rows, daily_limit, per_domain_limit):
    """Real sendable volume for today, after the per-domain cap is applied.

    The per-domain cap (3/day) is usually the binding constraint, not
    daily_limit: sending 200 emails needs ~67 distinct recipient domains.
    Returns the room left per domain plus the domains that cannot be
    completed today.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    domain_today = {}
    for r in log_rows or []:
        if r.get("status") == "sent" and r.get("timestamp", "").startswith(today):
            d = domain_of(r.get("to", ""))
            domain_today[d] = domain_today.get(d, 0) + 1

    queued = {}
    for item in queue:
        d = domain_of(item.get("to", ""))
        queued[d] = queued.get(d, 0) + 1

    room = 0
    capped = {}
    for d, c in queued.items():
        left = max(0, per_domain_limit - domain_today.get(d, 0))
        if left < c:
            capped[d] = c - left
        room += min(c, left)

    return {
        "domain_today": domain_today,
        "queued": queued,
        "room": min(room, daily_limit),
        "capped": capped,
        "shortfall": max(0, len(queue) - min(room, daily_limit)),
    }


def main():
    ap = argparse.ArgumentParser(description="SMTP cold email sender with throttling")
    ap.add_argument("--campaign-id", required=True, help="获客活动 id（bs_ai_lead_gen_campaigns.id）")
    ap.add_argument("--queue-file",
                    help="发送队列 JSON（默认 <campaign>/logs/send_queue.json，"
                         "由 lead_db.py export-send-queue 生成，每项含 email_id）")
    ap.add_argument("--dry-run", action="store_true", help="preview only, send nothing")
    ap.add_argument("--limit", type=int, help="max emails this run")
    ap.add_argument("--max-minutes", type=int,
                    help="stop cleanly after this many minutes (resumable: "
                         "already-sent emails are skipped next run)")
    args = ap.parse_args()

    tenant_id, _user_id = get_identity()
    try:
        campaign_id = int(args.campaign_id)
    except ValueError:
        raise SystemExit(f"--campaign-id 必须是数字 id，收到：{args.campaign_id}")

    cdir = campaign_dir(tenant_id, campaign_id)
    tenant_leadgen_dir = Path(cdir).parent  # 退订名单租户级共享，跨 campaign 生效

    # SMTP 凭据三层解析（用户个人 -> 租户兜底 -> 报错），密码只在内存中解密
    mailbox = resolve_mailbox_or_die()
    print(f"send mailbox: {mailbox['email_address']} (level={mailbox['level']})")

    queue_file = Path(args.queue_file) if args.queue_file else cdir / "logs" / "send_queue.json"
    if not queue_file.exists():
        raise SystemExit(f"队列文件不存在：{queue_file}\n"
                         "请先执行 python lead_db.py export-send-queue --campaign-id "
                         f"{campaign_id} 生成")
    items = load_json(queue_file)
    for item in items:
        if not item.get("email_id"):
            raise SystemExit("队列项缺少 email_id —— 队列必须由 lead_db.py "
                             "export-send-queue 生成（含 email_id 供结果回写）")

    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        campaign = get_campaign(cur, tenant_id, campaign_id)
    cfg = build_cfg(campaign, mailbox, cdir)

    unsubscribed = load_unsubscribed(tenant_leadgen_dir)
    log_path, log_rows = load_log(cdir)

    already_sent = {r["to"].lower() for r in log_rows if r.get("status") == "sent"}

    queue = []
    for item in items:
        to = item.get("to", "").strip().lower()
        if to and to in unsubscribed:
            print(f"skip (unsubscribed): {to}")
            continue
        if to in already_sent:
            print(f"skip (already sent): {to}")
            continue
        queue.append(item)

    # 对口职能口径：campaign 的 contact_targets.json
    tcfg = load_target_config(cdir)
    targets = tcfg.get("target_functions")
    excludes = tcfg.get("exclude_functions")
    if tcfg.get("product_or_service"):
        print(f"product/service: {tcfg['product_or_service']}")
    print(f"target functions: {targets or '(no restriction)'}")

    if args.dry_run:
        cmd_dry_run(queue, cfg, unsubscribed, log_rows, tcfg)
        return 0

    if not queue:
        print("nothing to send")
        return 0

    if args.limit:
        queue = queue[: args.limit]

    daily_limit = cfg["daily_limit"]
    per_domain_limit = cfg["per_domain_daily_limit"]
    base_delay = max(MIN_INTERVAL_SEC, int(cfg.get("delay_seconds", 90)))
    extra = int(cfg.get("random_delay_extra", 60))

    today = datetime.now(timezone.utc).date().isoformat()
    sent_today = sum(1 for r in log_rows
                     if r.get("status") == "sent" and r.get("timestamp", "").startswith(today))
    lifetime_sent = count_sent(log_rows)
    domain_today = {}
    for r in log_rows:
        if r.get("status") == "sent" and r.get("timestamp", "").startswith(today):
            d = domain_of(r.get("to", ""))
            domain_today[d] = domain_today.get(d, 0) + 1

    print(f"queue: {len(queue)} emails | already sent today: {sent_today} | "
          f"daily cap: {daily_limit}")

    # The warm-up ramp is driven by LIFETIME sends, not today's count: a new
    # calendar day must not reset the allowance, otherwise the ramp never
    # climbs and a warming domain is stuck at 20/day forever.
    recommended = rampup_cap(lifetime_sent)
    print(f"warm-up ramp   : {lifetime_sent} lifetime sends -> "
          f"~{recommended}/day recommended")
    if daily_limit > recommended:
        print(f"note: daily_limit {daily_limit} exceeds warmup recommendation "
              f"~{recommended} for a new/low-reputation domain")
    if daily_limit < 20:
        print("ERROR: refusing to send. A warming domain must start at <= 20/day.")
        print("       If this domain has a long clean history, raise daily_limit "
              "on the campaign deliberately.")
        return 1

    # Capacity preview: the per-domain cap usually binds before daily_limit.
    cap = plan_capacity(queue, log_rows, daily_limit, per_domain_limit)
    avg_delay = base_delay + extra / 2
    est_minutes = int(min(len(queue), cap["room"]) * avg_delay / 60)
    print(f"sendable today : ~{cap['room']} (per-domain cap binds first; "
          f"{len(cap['capped'])} domains already at cap)")
    print(f"est. run time  : ~{est_minutes // 60}h{est_minutes % 60:02d}m "
          f"(~{avg_delay:.0f}s average gap)")
    if args.max_minutes:
        print(f"time budget    : {args.max_minutes} min (stops cleanly, "
              f"resumable — sent emails are skipped next run)")
    if cap["shortfall"]:
        print(f"note: {cap['shortfall']} emails cannot be sent today because of "
              f"the {per_domain_limit}/24h per-domain cap. They stay in the "
              f"queue and go out on a later day.")

    try:
        server = connect(cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: SMTP connection failed: {exc}")
        return 1

    deadline = (time.monotonic() + args.max_minutes * 60
                if args.max_minutes else None)
    print("connected. starting send...\n")
    sent = failed = bounced = 0
    stop_all = False

    try:
        for i, item in enumerate(queue, 1):
            to = item["to"].strip().lower()
            d = domain_of(to)
            subj, ab = pick_subject(item)
            item = dict(item, subject=subj, _ab=ab)
            email_id = item.get("email_id")

            # 职能相关性硬闸门 —— 与干跑同一套口径。
            # 招聘/财务/法务/公关等无关职能邮箱直接跳过，不占用发送配额。
            a = assess_email(to, targets, excludes)
            declared = (item.get("email_relevance") or "").strip().lower()
            if a["relevance"] == "irrelevant" or declared == "irrelevant":
                reason = (f"function={a['function']} irrelevant to this outreach"
                          if a["relevance"] == "irrelevant"
                          else "marked irrelevant on the contact record")
                print(f"[{i}] skip {to} ({reason}) — not a target for this product")
                log_rows.append(log_entry(item, "skipped_irrelevant_function",
                                          reason[:200]))
                # 退回 draft 并记录原因，避免下次 export-send-queue 再次进入队列
                record_result(tenant_id, email_id, "draft",
                              error=f"skipped: {reason}"[:200])
                continue

            if sent_today + sent >= daily_limit:
                print(f"daily limit reached ({daily_limit}); stopping. "
                      f"Remaining {len(queue) - i + 1} can resume next run.")
                break
            if deadline and time.monotonic() > deadline:
                print(f"time budget reached ({args.max_minutes} min); stopping "
                      f"cleanly. Remaining {len(queue) - i + 1} can resume next run.")
                break
            if domain_today.get(d, 0) >= per_domain_limit:
                print(f"[{i}] skip {to} (per-domain cap {per_domain_limit} reached today)")
                log_rows.append(log_entry(item, "skipped_domain_cap"))
                # 保持 approved 不动：下次导出自然重新排队，改日再发
                continue

            msg = build_message(item, cfg)
            attempt = 0
            success = False
            while attempt <= MAX_RETRY and not success:
                try:
                    server.send_message(msg)
                    success = True
                    sent += 1
                    sent_today += 1
                    domain_today[d] = domain_today.get(d, 0) + 1
                    message_id = str(msg["Message-ID"] or "")
                    print(f"[{i}] sent -> {to}")
                    log_rows.append(log_entry(item, "sent"))
                    record_result(tenant_id, email_id, "sent", message_id=message_id)
                except smtplib.SMTPRecipientsRefused as exc:
                    detail = str(exc)
                    kind = classify_bounce("", detail)
                    log_rows.append(log_entry(item, f"bounce_{kind}", detail[:200]))
                    if kind in ("hard", "blacklist"):
                        bounced += 1
                        unsubscribed[to] = {"email": to, "date": today, "reason": f"bounce_{kind}"}
                        print(f"[{i}] HARD BOUNCE -> {to}; added to unsubscribe list")
                        record_result(tenant_id, email_id, "bounced",
                                      error=f"bounce_{kind}: {detail}"[:200])
                        if kind == "blacklist":
                            stop_all = True
                            print("     BLACKLIST error: stopping all sends to protect "
                                  "sending reputation.")
                    else:
                        failed += 1
                        record_result(tenant_id, email_id, "failed", error=detail[:200])
                    success = True  # recipients refused: move on
                except (smtplib.SMTPSenderRefused, smtplib.SMTPResponseException) as exc:
                    code = str(getattr(exc, "smtp_code", ""))
                    msg_text = str(getattr(exc, "smtp_error", exc))
                    kind = classify_bounce(code, msg_text)
                    if kind == "blacklist":
                        stop_all = True
                        print(f"[{i}] BLACKLIST error ({code} {msg_text}); stopping all sends.")
                        record_result(tenant_id, email_id, "bounced",
                                      error=f"blacklist: {code} {msg_text}"[:200])
                        break
                    attempt += 1
                    if attempt > MAX_RETRY:
                        failed += 1
                        log_rows.append(log_entry(item, "failed", f"{code} {msg_text}"[:200]))
                        print(f"[{i}] FAILED -> {to}: {code} {msg_text}")
                        record_result(tenant_id, email_id, "failed", error=f"{code} {msg_text}"[:200])
                    else:
                        wait = 10 * (2 ** (attempt - 1))
                        print(f"[{i}] retry in {wait}s -> {to} ({code})")
                        time.sleep(wait)
                except smtplib.SMTPServerDisconnected as exc:
                    # 长跑中断线：重连后重试当前邮件，避免断线把整批毒化为 failed
                    try:
                        server.quit()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        server = connect(cfg)
                    except Exception as recon_exc:  # noqa: BLE001
                        failed += 1
                        log_rows.append(log_entry(item, "failed",
                                                  f"reconnect failed: {recon_exc}"[:200]))
                        print(f"[{i}] ERROR -> {to}: reconnect failed: {recon_exc}")
                        record_result(tenant_id, email_id, "failed",
                                      error=f"reconnect failed: {recon_exc}"[:200])
                        success = True
                        continue
                    attempt += 1
                    if attempt > MAX_RETRY:
                        failed += 1
                        log_rows.append(log_entry(item, "failed", str(exc)[:200]))
                        print(f"[{i}] FAILED -> {to}: {exc}")
                        record_result(tenant_id, email_id, "failed", error=str(exc)[:200])
                    else:
                        print(f"[{i}] SMTP disconnected, reconnected; "
                              f"retry {attempt}/{MAX_RETRY} -> {to}")
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    log_rows.append(log_entry(item, "failed", str(exc)[:200]))
                    print(f"[{i}] ERROR -> {to}: {exc}")
                    record_result(tenant_id, email_id, "failed", error=str(exc)[:200])
                    success = True

            save_log(log_path, log_rows)
            if unsubscribed:
                save_unsubscribed(tenant_leadgen_dir, unsubscribed)
            if stop_all:
                print("\nstopped early due to blacklist signal.")
                break

            if i < len(queue):
                delay = base_delay + random.randint(0, extra)
                print(f"    sleeping {delay}s before next send")
                time.sleep(delay)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass
        save_log(log_path, log_rows)

    print("\n" + "=" * 72)
    print(f"sent   : {sent}")
    print(f"failed : {failed}")
    print(f"bounced: {bounced}")
    print(f"log    : {log_path}")
    if bounced:
        print(f"unsub  : {len(unsubscribed)} entries in "
              f"{tenant_leadgen_dir}/unsubscribed.csv")
    print("\nNext: monitor replies for 48h. Bounce rate > 5% -> pause and diagnose.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)