#!/usr/bin/env python3
"""AI获客 —— 线索/联系人/邮件草稿数据库管理工具（PostgreSQL 版）。

原包为 CSV 落盘（workspace/campaigns/<name>/leads.csv 等），移植到本系统后
改为直接读写 bs_ai_lead_gen_* 五表，闸门与去重逻辑与原包保持同一口径。

身份来源：AID_TENANT_ID / AID_USER_ID 环境变量（skill_executor 注入的可信
上下文，不信任 LLM 传参）。缺少身份或无库连接时 fail-fast。

子命令:
  init --name N --brief-file F --target-region R [--send-mode review|auto]
  set-target-config --campaign-id ID --json-file F
  add-lead --campaign-id ID --json-file F
  add-contact --campaign-id ID --json-file F
  set-status --campaign-id ID --company C --status S [reason]
  add-email-draft --campaign-id ID --contact-id CID --subject-a SA --subject-b SB --body-file F [--ab-group A|B]
  set-email-status --campaign-id ID --email-id EID --status S [--message-id M] [--error E]
  export-send-queue --campaign-id ID [--limit N]
  set-send-mode --campaign-id ID review|auto
  bind-scheduler --campaign-id ID --task-id TID    # 绑定每日循环定时任务并切 auto
  unbind-scheduler --campaign-id ID [--cancel]     # 暂停(或取消)任务并切回 review
  daily-summary --campaign-id ID [--date D] [--notify]  # 当日汇总 JSON（--notify 发站内信）
  score --campaign-id ID
  export --campaign-id ID
  list-leads --campaign-id ID [--status S]
  list-contacts --campaign-id ID
  show-campaign --campaign-id ID
"""

import argparse
import json
import os
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# 同目录工具共用同一套职能判定口径（原包 email_rules.py，原样移植）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from email_rules import assess_email, load_target_config  # noqa: E402

# 平台 get_db_connection 默认 RealDictCursor（字典行），本脚本按元组语义取值
# （下标/解包/dict(zip)），游标必须显式指定 NamedTupleCursor
from psycopg2.extras import NamedTupleCursor  # noqa: E402

VALID_STATUS = {"new", "qualified", "rejected", "contacted", "replied", "bounced"}
LEAD_STATUS = {"new", "qualified", "rejected", "contacted"}
VALID_SOURCE = {"official_site", "directory", "verified_pattern", "guessed", "manual"}
VALID_CONFIDENCE = {"high", "medium", "low"}
VALID_RELEVANCE = {"relevant", "generic", "unknown", "irrelevant"}
VALID_SEND_MODE = {"review", "auto"}
VALID_EMAIL_STATUS = {"draft", "approved", "sending", "sent", "failed", "bounced"}
VALID_CAMPAIGN_STATUS = {"draft", "searching", "drafting", "awaiting_review",
                         "sending", "paused", "completed", "failed"}


# ---------- 身份与环境 ----------

def get_identity():
    tenant_id = (os.environ.get("AID_TENANT_ID")
                 or os.environ.get("CURRENT_TENANT_ID") or "").strip()
    user_id = (os.environ.get("AID_USER_ID")
               or os.environ.get("CURRENT_USER_ID") or "").strip()
    if not tenant_id or not user_id:
        print("ERROR: 缺少可信执行身份（AID_TENANT_ID / AID_USER_ID），"
              "本工具必须经 skill_execute 在智能体执行上下文中调用")
        sys.exit(2)
    return tenant_id, user_id


def get_conn():
    from src.db.database import (
        get_db_connection as _get_conn,
        get_postgres_pool,
        init_postgres_pool,
    )
    if get_postgres_pool() is None:
        init_postgres_pool()
    return _get_conn()


def campaign_dir(tenant_id, campaign_id):
    from src.core.storage import get_tenant_storage_dir
    return Path(get_tenant_storage_dir(tenant_id, "lead-gen")) / f"campaign_{campaign_id}"


def init_tables():
    """五张业务表幂等建表（SKILL.md init_script: lead_db.py，SkillLoader 启动时调用）。

    外键完整性由应用层保证（database_dev.md 禁止数据库外键）。
    """
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bs_ai_lead_gen_campaigns (
                id SERIAL PRIMARY KEY,
                campaign_name TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                user_id TEXT,
                brief TEXT,
                target_region TEXT,
                send_mode TEXT DEFAULT 'review',
                status TEXT DEFAULT 'draft',
                daily_limit INT DEFAULT 20,
                scheduler_task_id TEXT,
                reply_mailbox TEXT,
                send_mailbox_level TEXT,
                product_refs TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bs_ailg_campaigns_tenant "
                    "ON bs_ai_lead_gen_campaigns(tenant_id)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bs_ai_lead_gen_leads (
                id SERIAL PRIMARY KEY,
                campaign_id INT NOT NULL,
                tenant_id TEXT NOT NULL,
                user_id TEXT,
                company_name TEXT,
                country TEXT,
                city TEXT,
                website TEXT,
                industry TEXT,
                employees TEXT,
                founded TEXT,
                source TEXT,
                source_url TEXT,
                score INT,
                status TEXT DEFAULT 'new',
                reject_reason TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bs_ailg_leads_campaign "
                    "ON bs_ai_lead_gen_leads(campaign_id)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bs_ai_lead_gen_contacts (
                id SERIAL PRIMARY KEY,
                campaign_id INT NOT NULL,
                lead_id INT,
                tenant_id TEXT NOT NULL,
                user_id TEXT,
                name TEXT,
                title TEXT,
                email TEXT,
                email_source TEXT,
                email_type TEXT,
                email_function TEXT,
                email_relevance TEXT,
                confidence TEXT,
                language TEXT,
                phone TEXT,
                linkedin TEXT,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bs_ailg_contacts_campaign "
                    "ON bs_ai_lead_gen_contacts(campaign_id)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bs_ai_lead_gen_emails (
                id SERIAL PRIMARY KEY,
                campaign_id INT NOT NULL,
                contact_id INT,
                tenant_id TEXT NOT NULL,
                user_id TEXT,
                subject_a TEXT,
                subject_b TEXT,
                ab_group TEXT,
                body TEXT,
                status TEXT DEFAULT 'draft',
                message_id TEXT,
                sent_at TIMESTAMP,
                error TEXT,
                retry_count INT DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bs_ailg_emails_campaign "
                    "ON bs_ai_lead_gen_emails(campaign_id)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bs_ai_lead_gen_replies (
                id SERIAL PRIMARY KEY,
                campaign_id INT NOT NULL,
                contact_id INT,
                email_id INT,
                tenant_id TEXT NOT NULL,
                user_id TEXT,
                from_addr TEXT,
                subject TEXT,
                body TEXT,
                received_at TIMESTAMP,
                intent TEXT,
                intent_summary TEXT,
                notified BOOLEAN DEFAULT FALSE,
                message_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_bs_ailg_replies_campaign "
                    "ON bs_ai_lead_gen_replies(campaign_id)")
        # 回复监控幂等去重锚点（P2）：并发轮询下按 (tenant, message_id) 唯一拦截重复入库
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_bs_ailg_replies_tenant_message "
                    "ON bs_ai_lead_gen_replies(tenant_id, message_id) "
                    "WHERE message_id IS NOT NULL")
        # get_db_connection 退出时无条件 rollback，DDL 必须显式提交
        conn.commit()


def load_json_entries(json_file):
    data = json.loads(Path(json_file).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("leads") or data.get("contacts") or data.get("items") or [data]
    if not isinstance(data, list):
        raise SystemExit(f"{json_file}: expected a JSON array or an object with a list field")
    return data


def norm_company(name):
    """公司名归一化去重键（与原包一致：法务后缀/标点/变音符折叠）。"""
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name.strip().lower())
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    for suffix in [
        "co., ltd.", "co., ltd", "co ltd", "co.,ltd.", "coltd", "company limited",
        "co., inc.", "co., inc", "inc.", "inc", "ltd.", "ltd", "limited",
        "corp.", "corp", "corporation", "gmbh", "ag", "sa", "sas", "srl", "s.r.l.",
        "bv", "b.v.", "oy", "ab", "as", "plc", "llc", "pte ltd", "pty ltd",
        "group", "holding", "holdings", "the",
    ]:
        if n.endswith(" " + suffix):
            n = n[: -(len(suffix) + 1)]
    for ch in [",", ".", "'", "&", " ", "-"]:
        n = n.replace(ch, "")
    return n


# ---------- campaign ----------

def get_campaign(cur, tenant_id, campaign_id):
    cur.execute(
        "SELECT id, campaign_name, send_mode, status, daily_limit, reply_mailbox, brief, "
        "user_id, scheduler_task_id "
        "FROM bs_ai_lead_gen_campaigns WHERE id = %s AND tenant_id = %s",
        (campaign_id, tenant_id),
    )
    row = cur.fetchone()
    if not row:
        print(f"ERROR: campaign {campaign_id} not found in tenant {tenant_id}")
        sys.exit(1)
    cols = ["id", "campaign_name", "send_mode", "status", "daily_limit",
            "reply_mailbox", "brief", "user_id", "scheduler_task_id"]
    return dict(zip(cols, row))


def _mailbox_configured(cur, tenant_id, user_id):
    """发件邮箱三层解析的存在性检查（与 mailbox_resolver 同口径，只查不解密）。"""
    if user_id:
        cur.execute(
            "SELECT 1 FROM user_email_settings "
            "WHERE user_id = %s AND status = 'active'", (user_id,))
        if cur.fetchone():
            return True
    cur.execute(
        "SELECT 1 FROM tenant_email_settings "
        "WHERE tenant_id = %s AND status = 'active'", (tenant_id,))
    return cur.fetchone() is not None


def cmd_init(args):
    tenant_id, user_id = get_identity()
    # 发件邮箱前置校验（fail-fast）：2026-10-27 trace 实证，未配置发件邮箱时
    # 整个 campaign 走完检索/撰写流程到发送阶段才暴露，白白烧掉数十轮迭代。
    # 这里在 campaign 创建前就拦下，先把邮箱配好再开工。
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        if not _mailbox_configured(cur, tenant_id, user_id):
            print("ERROR: 尚未配置发件邮箱，暂不创建 campaign（避免整个流程走完才在发送阶段失败）。")
            print("  请先在「邮箱设置」配置个人邮箱，或联系租户管理员在企业设置页配置租户邮箱，")
            print("  配置完成后重新执行本命令。")
            return 1
    brief = Path(args.brief_file).read_text(encoding="utf-8") if args.brief_file else ""
    send_mode = args.send_mode
    if send_mode not in VALID_SEND_MODE:
        print(f"ERROR: send_mode 必须是 {'/'.join(sorted(VALID_SEND_MODE))}")
        return 1
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        cur.execute(
            """
            INSERT INTO bs_ai_lead_gen_campaigns
                (campaign_name, tenant_id, user_id, brief, target_region,
                 send_mode, status, daily_limit, reply_mailbox)
            VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s, %s)
            RETURNING id
            """,
            (args.name, tenant_id, user_id, brief, args.target_region or "",
             send_mode, args.daily_limit, args.reply_mailbox or ""),
        )
        campaign_id = cur.fetchone()[0]
        conn.commit()
    d = campaign_dir(tenant_id, campaign_id)
    for sub in ("intel", "emails", "logs", "probe"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    if brief:
        (d / "brief.md").write_text(brief, encoding="utf-8")
    print(f"campaign created: id={campaign_id}")
    print(f"workspace: {d}")
    return 0


def cmd_set_target_config(args):
    tenant_id, _ = get_identity()
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        get_campaign(cur, tenant_id, args.campaign_id)
    cfg = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    d = campaign_dir(tenant_id, args.campaign_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "contact_targets.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"target config saved -> {d / 'contact_targets.json'}")
    return 0


def cmd_set_send_mode(args):
    tenant_id, _ = get_identity()
    if args.send_mode not in VALID_SEND_MODE:
        print(f"ERROR: send_mode 必须是 {'/'.join(sorted(VALID_SEND_MODE))}")
        return 1
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        get_campaign(cur, tenant_id, args.campaign_id)
        cur.execute(
            "UPDATE bs_ai_lead_gen_campaigns SET send_mode = %s, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND tenant_id = %s",
            (args.send_mode, args.campaign_id, tenant_id),
        )
        conn.commit()
    print(f"send_mode -> {args.send_mode}")
    return 0


def cmd_show_campaign(args):
    tenant_id, _ = get_identity()
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        c = get_campaign(cur, tenant_id, args.campaign_id)
        cur.execute(
            "SELECT status, COUNT(*) FROM bs_ai_lead_gen_leads "
            "WHERE campaign_id = %s GROUP BY status", (args.campaign_id,))
        lead_counts = dict(cur.fetchall())
        cur.execute(
            "SELECT status, COUNT(*) FROM bs_ai_lead_gen_emails "
            "WHERE campaign_id = %s GROUP BY status", (args.campaign_id,))
        email_counts = dict(cur.fetchall())
    print(json.dumps({
        "campaign": {k: (v.isoformat() if isinstance(v, datetime) else v)
                     for k, v in c.items()},
        "leads_by_status": lead_counts,
        "emails_by_status": email_counts,
    }, ensure_ascii=False, indent=2, default=str))
    return 0


# ---------- 定时任务绑定（P2 auto 模式） ----------

def _get_scheduler_task(cur, tenant_id, user_id, task_id):
    """读取 scheduled_tasks 行并校验归属（租户 + 用户双匹配，防止绑错别人的任务）。"""
    cur.execute(
        "SELECT task_id, tenant_id, user_id, status FROM scheduled_tasks "
        "WHERE task_id = %s", (task_id,))
    row = cur.fetchone()
    if not row:
        print(f"ERROR: scheduled task {task_id} not found（先用 create_scheduled_task 工具创建）")
        sys.exit(1)
    task = dict(zip(["task_id", "tenant_id", "user_id", "status"], row))
    if (task["tenant_id"] or "") != (tenant_id or ""):
        print("ERROR: scheduled task 不属于当前租户，拒绝绑定")
        sys.exit(1)
    if user_id and task["user_id"] and task["user_id"] != user_id:
        print("ERROR: scheduled task 不属于当前用户，拒绝绑定")
        sys.exit(1)
    return task


def cmd_bind_scheduler(args):
    """绑定每日循环定时任务：回写 scheduler_task_id 并切 send_mode=auto。

    paused 任务绑定时顺带激活，否则 send_mode=auto 但任务静默不跑。
    """
    tenant_id, user_id = get_identity()
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        campaign = get_campaign(cur, tenant_id, args.campaign_id)
        task = _get_scheduler_task(cur, tenant_id, user_id, args.task_id)
        if task["status"] not in ("active", "paused"):
            print(f"ERROR: scheduled task status={task['status']}，仅 active/paused 可绑定")
            return 1
        if task["status"] == "paused":
            cur.execute(
                "UPDATE scheduled_tasks SET status = 'active', "
                "updated_at = %s WHERE task_id = %s",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), args.task_id),
            )
        cur.execute(
            "UPDATE bs_ai_lead_gen_campaigns SET scheduler_task_id = %s, "
            "send_mode = 'auto', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND tenant_id = %s",
            (args.task_id, args.campaign_id, tenant_id),
        )
        conn.commit()
    print(json.dumps({"campaign_id": args.campaign_id,
                      "scheduler_task_id": args.task_id,
                      "send_mode": "auto"}, ensure_ascii=False))
    return 0


def cmd_unbind_scheduler(args):
    """解绑定时任务并切回 review：默认暂停任务（保留关联可再绑定），
    --cancel 时任务置 cancelled 并清除关联。"""
    tenant_id, user_id = get_identity()
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        campaign = get_campaign(cur, tenant_id, args.campaign_id)
        task_id = campaign.get("scheduler_task_id")
        if not task_id:
            print("campaign 未绑定定时任务")
            return 1
        cur.execute(
            "SELECT tenant_id, user_id FROM scheduled_tasks WHERE task_id = %s",
            (task_id,))
        row = cur.fetchone()
        if not row or (row[0] or "") != (tenant_id or ""):
            print(f"ERROR: 绑定的定时任务 {task_id} 不属于当前租户，拒绝操作")
            return 1
        if user_id and row[1] and row[1] != user_id:
            # 与 bind 同口径：同租户内也不能操作他人名下的定时任务
            print(f"ERROR: 绑定的定时任务 {task_id} 不属于当前用户，拒绝操作")
            return 1
        new_task_status = "cancelled" if args.cancel else "paused"
        cur.execute(
            "UPDATE scheduled_tasks SET status = %s, updated_at = %s "
            "WHERE task_id = %s",
            (new_task_status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id),
        )
        cur.execute(
            "UPDATE bs_ai_lead_gen_campaigns SET "
            "scheduler_task_id = CASE WHEN %s THEN NULL ELSE scheduler_task_id END, "
            "send_mode = 'review', updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND tenant_id = %s",
            (bool(args.cancel), args.campaign_id, tenant_id),
        )
        conn.commit()
    print(json.dumps({"campaign_id": args.campaign_id,
                      "scheduler_task_id": task_id,
                      "task_status": new_task_status,
                      "send_mode": "review"}, ensure_ascii=False))
    return 0


# ---------- 每日汇总与通知（P2） ----------

def aggregate_daily_summary(cur, campaign_id, date_str):
    """聚合某日获客产出（date_str=YYYY-MM-DD）。只读，返回 dict。"""
    def _count(sql, params):
        cur.execute(sql, params)
        row = cur.fetchone()
        return int(row[0]) if row else 0

    return {
        "summary_date": date_str,
        "new_leads": _count(
            "SELECT COUNT(*) FROM bs_ai_lead_gen_leads "
            "WHERE campaign_id = %s AND created_at::date = %s::date",
            (campaign_id, date_str)),
        "new_contacts": _count(
            "SELECT COUNT(*) FROM bs_ai_lead_gen_contacts "
            "WHERE campaign_id = %s AND created_at::date = %s::date",
            (campaign_id, date_str)),
        "emails_sent": _count(
            "SELECT COUNT(*) FROM bs_ai_lead_gen_emails "
            "WHERE campaign_id = %s AND status = 'sent' "
            "AND COALESCE(sent_at, updated_at)::date = %s::date",
            (campaign_id, date_str)),
        "emails_failed": _count(
            "SELECT COUNT(*) FROM bs_ai_lead_gen_emails "
            "WHERE campaign_id = %s AND status = 'failed' "
            "AND COALESCE(sent_at, updated_at)::date = %s::date",
            (campaign_id, date_str)),
        "emails_bounced": _count(
            "SELECT COUNT(*) FROM bs_ai_lead_gen_emails "
            "WHERE campaign_id = %s AND status = 'bounced' "
            "AND COALESCE(sent_at, updated_at)::date = %s::date",
            (campaign_id, date_str)),
        "new_replies": _count(
            "SELECT COUNT(*) FROM bs_ai_lead_gen_replies "
            "WHERE campaign_id = %s AND received_at::date = %s::date",
            (campaign_id, date_str)),
        "interested_replies": _count(
            "SELECT COUNT(*) FROM bs_ai_lead_gen_replies "
            "WHERE campaign_id = %s AND received_at::date = %s::date "
            "AND intent IN ('interested', 'info_request')",
            (campaign_id, date_str)),
        "awaiting_review": _count(
            "SELECT COUNT(*) FROM bs_ai_lead_gen_emails "
            "WHERE campaign_id = %s AND status = 'draft'",
            (campaign_id,)),
    }


def publish_leadgen_notification(tenant_id, user_id, event_type, parameters,
                                 campaign_id, event_key, ttl_seconds=7 * 86400):
    """发布 AI获客站内信通知（src/notifications 事件机制，投递复用既有偏好配置）。

    惰性导入：lead_db.py 是 SKILL.md init_script（SkillLoader 启动时 import），
    通知模块只能在调用期加载。写入失败只告警不抛出（通知是增强不是闸门）。
    """
    try:
        from datetime import timedelta
        from src.notifications.events import publish_in_tx
        from src.notifications.contracts import Subject

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT clock_timestamp() AS now")
            now = cur.fetchone()["now"]
            ref = publish_in_tx(
                cur,
                subject=Subject(user_id=user_id, tenant_id=tenant_id or None),
                producer="ai_lead_gen",
                event_type=event_type,
                event_key=event_key,
                resource_ref={"type": "leadgen_campaign", "id": str(campaign_id)},
                parameters=parameters,
                occurred_at=now,
                accepted_at=now,
                expires_at=now + timedelta(seconds=ttl_seconds),
                category="task",
                priority=0,
                policy_key="accepted_before_enabled",
            )
            conn.commit()
            return ref
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: 通知发布失败（不影响业务数据）: {exc}")
        return None


def cmd_daily_summary(args):
    tenant_id, user_id = get_identity()
    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        campaign = get_campaign(cur, tenant_id, args.campaign_id)
        summary = aggregate_daily_summary(cur, args.campaign_id, date_str)
    summary["campaign_id"] = args.campaign_id
    summary["campaign_name"] = campaign["campaign_name"]

    if args.notify:
        recipient = campaign.get("user_id") or user_id
        params = {
            "campaign_name": (campaign["campaign_name"] or "")[:128] or str(args.campaign_id),
            "summary_date": date_str,
            "new_leads": summary["new_leads"],
            "new_contacts": summary["new_contacts"],
            "emails_sent": summary["emails_sent"],
            "emails_failed": summary["emails_failed"],
            "emails_bounced": summary["emails_bounced"],
            "new_replies": summary["new_replies"],
            "interested_replies": summary["interested_replies"],
            "awaiting_review": summary["awaiting_review"],
        }
        ref = publish_leadgen_notification(
            tenant_id, recipient, "leadgen.daily_summary", params,
            args.campaign_id,
            event_key=f"leadgen:daily:{args.campaign_id}:{date_str}",
            ttl_seconds=30 * 86400,
        )
        summary["notification_ref"] = ref
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


# ---------- leads ----------

def cmd_add_lead(args):
    tenant_id, user_id = get_identity()
    entries = load_json_entries(args.json_file)
    added = skipped = 0
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        get_campaign(cur, tenant_id, args.campaign_id)
        cur.execute(
            "SELECT company_name, website FROM bs_ai_lead_gen_leads "
            "WHERE campaign_id = %s", (args.campaign_id,))
        rows = cur.fetchall()
        seen = {norm_company(r[0]) for r in rows}
        seen_site = {(r[1] or "").lower().rstrip("/") for r in rows if r[1]}
        for entry in entries:
            company = (entry.get("company") or "").strip()
            if not company:
                skipped += 1
                continue
            key = norm_company(company)
            site = (entry.get("website") or "").lower().rstrip("/")
            if key in seen or (site and site in seen_site):
                skipped += 1
                continue
            status = entry.get("status", "new")
            if status not in LEAD_STATUS:
                status = "new"
            score = entry.get("score")
            cur.execute(
                """
                INSERT INTO bs_ai_lead_gen_leads
                    (campaign_id, tenant_id, user_id, company_name, country, city,
                     website, industry, employees, founded, source, source_url,
                     score, status, reject_reason, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (args.campaign_id, tenant_id, user_id, company,
                 entry.get("country", ""), entry.get("city", ""),
                 entry.get("website", ""), entry.get("industry", ""),
                 entry.get("employees", ""), entry.get("founded", ""),
                 entry.get("source", ""), entry.get("source_url", ""),
                 int(score) if str(score).isdigit() else None,
                 status, entry.get("reason", ""), entry.get("notes", "")),
            )
            seen.add(key)
            if site:
                seen_site.add(site)
            added += 1
        total = len(seen)
        conn.commit()
    print(f"leads: +{added} added, {skipped} skipped (duplicate), total {total}")
    return 0


def cmd_set_status(args):
    tenant_id, _ = get_identity()
    if args.status not in LEAD_STATUS:
        print(f"ERROR: status 必须是 {'/'.join(sorted(LEAD_STATUS))}")
        return 1
    target = norm_company(args.company)
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        get_campaign(cur, tenant_id, args.campaign_id)
        cur.execute(
            "SELECT id, company_name FROM bs_ai_lead_gen_leads WHERE campaign_id = %s",
            (args.campaign_id,))
        matched = [(i, n) for i, n in cur.fetchall() if norm_company(n) == target]
        if not matched:
            print(f"company not found: {args.company}")
            return 1
        for lead_id, name in matched:
            cur.execute(
                "UPDATE bs_ai_lead_gen_leads SET status = %s, "
                "reject_reason = COALESCE(%s, reject_reason), updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s",
                (args.status, args.reason, lead_id),
            )
        conn.commit()
    print(f"{args.company} -> {args.status}" + (f" ({args.reason})" if args.reason else ""))
    return 0


# ---------- contacts ----------

def cmd_add_contact(args):
    tenant_id, user_id = get_identity()
    entries = load_json_entries(args.json_file)
    added = skipped = 0
    warnings, rejected = [], []
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        get_campaign(cur, tenant_id, args.campaign_id)
        cur.execute(
            "SELECT id, company_name FROM bs_ai_lead_gen_leads WHERE campaign_id = %s",
            (args.campaign_id,))
        lead_rows = cur.fetchall()
        lead_by_key = {norm_company(n): i for i, n in lead_rows}
        cur.execute(
            "SELECT email FROM bs_ai_lead_gen_contacts WHERE campaign_id = %s",
            (args.campaign_id,))
        seen = {(r[0] or "").lower() for r in cur.fetchall() if r[0]}
        # 每公司 2 个联系人配额：现有合格联系人也计入
        cur.execute(
            "SELECT lead_id, COUNT(*) FROM bs_ai_lead_gen_contacts "
            "WHERE campaign_id = %s GROUP BY lead_id", (args.campaign_id,))
        per_company = dict(cur.fetchall())

        tcfg = load_target_config(str(campaign_dir(tenant_id, args.campaign_id)))
        targets = tcfg.get("target_functions")
        excludes = tcfg.get("exclude_functions")

        for entry in entries:
            email = (entry.get("email") or "").strip().lower()
            name = (entry.get("name") or "").strip()
            company = (entry.get("company") or "").strip()
            if not name and not email:
                skipped += 1
                continue
            if email and email in seen:
                skipped += 1
                continue

            # 职能相关性判定 —— 与 email_rules.py 同一口径
            relevance = (entry.get("email_relevance") or "").strip().lower()
            function = (entry.get("email_function") or "").strip()
            if email:
                a = assess_email(email, targets, excludes)
                function = function or a["function"]
                declared = relevance
                if not declared:
                    relevance = a["relevance"]
                elif declared == "irrelevant" or a["relevance"] == "irrelevant":
                    relevance = "irrelevant"
            if relevance and relevance not in VALID_RELEVANCE:
                relevance = "unknown"

            # 无关职能 / 公开窗口不作为联系人入库（原包铁律）
            if relevance == "irrelevant":
                rejected.append(f"  ! {email or '(no email)'} [{function or '?'}] "
                                f"职能与本次推销无关，已拒收")
                continue
            if relevance == "generic":
                rejected.append(f"  ! {email or '(no email)'} [{function or '?'}] "
                                f"是公开联系窗口，无具体负责人，不入联系人表")
                continue

            # 关联 lead：按公司名匹配，找不到自动补建 lead 行
            lead_id = lead_by_key.get(norm_company(company))
            if lead_id is None:
                if not company:
                    skipped += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO bs_ai_lead_gen_leads
                        (campaign_id, tenant_id, user_id, company_name, country, status)
                    VALUES (%s,%s,%s,%s,%s,'new') RETURNING id
                    """,
                    (args.campaign_id, tenant_id, user_id, company,
                     entry.get("country", "")),
                )
                lead_id = cur.fetchone()[0]
                lead_by_key[norm_company(company)] = lead_id

            src = entry.get("email_source") or ""
            if src not in VALID_SOURCE:
                warnings.append(f"  ! invalid email_source '{src}' for {name or email} -> set to 'guessed'")
                src = "guessed"
            conf = entry.get("confidence") or ""
            if conf not in VALID_CONFIDENCE:
                conf = "medium"
            if src == "guessed":
                conf = "low"

            # 每公司最多 2 个联系人（relevant 优先挤占）
            if per_company.get(lead_id, 0) >= 2:
                warnings.append(f"  ! {company}: already has 2 contacts, skipped {name or email}")
                continue

            cur.execute(
                """
                INSERT INTO bs_ai_lead_gen_contacts
                    (campaign_id, lead_id, tenant_id, user_id, name, title, email,
                     email_source, email_type, email_function, email_relevance,
                     confidence, phone, linkedin, language, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (args.campaign_id, lead_id, tenant_id, user_id, name,
                 entry.get("title", ""), email, src, entry.get("email_type", ""),
                 function, relevance or "unknown", conf, entry.get("phone", ""),
                 entry.get("linkedin", ""), entry.get("language", ""),
                 entry.get("notes", "")),
            )
            if email:
                seen.add(email)
            per_company[lead_id] = per_company.get(lead_id, 0) + 1
            added += 1
        conn.commit()
    print(f"contacts: +{added} added, {skipped} skipped (duplicate)")
    for w in warnings:
        print(w)
    for r in rejected:
        print(r)
    if rejected:
        print("  -> 这些公司可能仍需按路径 2->5 继续找对口邮箱；全部无果才判 no_email")
    return 0


# ---------- emails ----------

def _pick_ab_group(email_addr, explicit):
    if explicit in ("A", "B"):
        return explicit
    import hashlib
    digest = hashlib.sha1(email_addr.strip().lower().encode("utf-8")).digest()
    return "A" if digest[0] % 2 == 0 else "B"


def cmd_add_email_draft(args):
    tenant_id, user_id = get_identity()
    body = Path(args.body_file).read_text(encoding="utf-8")
    if not body.strip():
        print("ERROR: 邮件正文为空")
        return 1
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        get_campaign(cur, tenant_id, args.campaign_id)
        cur.execute(
            "SELECT id, email, name FROM bs_ai_lead_gen_contacts "
            "WHERE id = %s AND campaign_id = %s",
            (args.contact_id, args.campaign_id))
        row = cur.fetchone()
        if not row:
            # 2026-10-27 trace 实证：LLM 盲猜 contact_id 1--10 连续 not found 空转。
            # 报错必须回带本 campaign 有效 contact ID，供下一轮直接取用，禁止再猜。
            cur.execute(
                "SELECT id, name, email FROM bs_ai_lead_gen_contacts "
                "WHERE campaign_id = %s ORDER BY id LIMIT 20",
                (args.campaign_id,))
            valid = [{"id": r[0], "name": r[1], "email": r[2]}
                     for r in cur.fetchall()]
            print(f"ERROR: contact {args.contact_id} not found in campaign {args.campaign_id}")
            if valid:
                print("valid contact ids (勿猜测，直接从中选用):")
                print(json.dumps(valid, ensure_ascii=False))
            else:
                print("campaign has no contacts yet; run add-contact first")
            return 1
        contact_id, email, name = row
        ab_group = _pick_ab_group(email, args.ab_group)
        cur.execute(
            """
            INSERT INTO bs_ai_lead_gen_emails
                (campaign_id, contact_id, tenant_id, user_id, subject_a, subject_b,
                 body, ab_group, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'draft') RETURNING id
            """,
            (args.campaign_id, contact_id, tenant_id, user_id,
             args.subject_a, args.subject_b, body, ab_group),
        )
        email_id = cur.fetchone()[0]
        conn.commit()
    print(json.dumps({"email_id": email_id, "contact": name, "to": email,
                      "ab_group": ab_group}, ensure_ascii=False))
    return 0


def cmd_set_email_status(args):
    tenant_id, _ = get_identity()
    if args.status not in VALID_EMAIL_STATUS:
        print(f"ERROR: status 必须是 {'/'.join(sorted(VALID_EMAIL_STATUS))}")
        return 1
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        get_campaign(cur, tenant_id, args.campaign_id)
        cur.execute(
            "UPDATE bs_ai_lead_gen_emails SET status = %s, "
            "message_id = COALESCE(%s, message_id), "
            "error = COALESCE(%s, error), "
            "sent_at = CASE WHEN %s = 'sent' THEN CURRENT_TIMESTAMP ELSE sent_at END, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE id = %s AND campaign_id = %s",
            (args.status, args.message_id, args.error, args.status,
             args.email_id, args.campaign_id),
        )
        if cur.rowcount == 0:
            print(f"ERROR: email {args.email_id} not found")
            return 1
        conn.commit()
    print(f"email {args.email_id} -> {args.status}")
    return 0


def cmd_export_send_queue(args):
    """从 emails 表拉取 approved 草稿生成发送队列 JSON（供 send_emails.py 消费）。"""
    tenant_id, user_id = get_identity()
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        c = get_campaign(cur, tenant_id, args.campaign_id)
        cur.execute(
            """
            SELECT e.id, e.subject_a, e.subject_b, e.body, e.ab_group,
                   ct.email, ct.name, ct.title,
                   l.company_name, l.country,
                   ct.email_source, ct.confidence, ct.email_relevance, ct.email_function
            FROM bs_ai_lead_gen_emails e
            JOIN bs_ai_lead_gen_contacts ct ON ct.id = e.contact_id
            JOIN bs_ai_lead_gen_leads l ON l.id = ct.lead_id
            WHERE e.campaign_id = %s AND e.status = 'approved'
            ORDER BY e.id
            LIMIT %s
            """,
            (args.campaign_id, args.limit or 1000),
        )
        rows = cur.fetchall()
    if not rows:
        print("no approved drafts in queue")
        return 0
    items = []
    for (email_id, sa, sb, body, ab, to, name, title,
         company, country, source, conf, relevance, function) in rows:
        items.append({
            "email_id": email_id,
            "to": to, "to_name": name or "", "title": title or "",
            "company": company or "", "country": country or "",
            "subject_a": sa or "", "subject_b": sb or "",
            "ab_group": ab or "", "body": body or "",
            "email_source": source or "", "confidence": conf or "",
            "email_relevance": relevance or "", "email_function": function or "",
        })
    d = campaign_dir(tenant_id, args.campaign_id)
    (d / "logs").mkdir(parents=True, exist_ok=True)
    out = d / "logs" / "send_queue.json"
    out.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"queue_file": str(out), "count": len(items),
                      "send_mode": c["send_mode"]}, ensure_ascii=False))
    return 0


# ---------- 统计与导出 ----------

def cmd_score(args):
    tenant_id, _ = get_identity()
    from collections import Counter
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        get_campaign(cur, tenant_id, args.campaign_id)
        cur.execute(
            "SELECT status, score, website FROM bs_ai_lead_gen_leads "
            "WHERE campaign_id = %s", (args.campaign_id,))
        leads = cur.fetchall()
        cur.execute(
            "SELECT confidence, email_source, email_relevance FROM bs_ai_lead_gen_contacts "
            "WHERE campaign_id = %s", (args.campaign_id,))
        contacts = cur.fetchall()

    print("=== leads by status ===")
    for k, v in Counter(r[0] for r in leads).most_common():
        print(f"  {k:12} {v}")
    scored = sorted(int(r[1]) for r in leads if r[0] == "qualified" and r[1] is not None)
    if scored:
        print(f"\n=== qualified score distribution (n={len(scored)}) ===")
        print(f"  min={scored[0]}  median={scored[len(scored)//2]}  max={scored[-1]}")
    no_site = [r for r in leads if r[0] == "qualified" and not r[2]]
    if no_site:
        print(f"\n  ! {len(no_site)} qualified leads have no website -- fill in before step 8")

    print(f"\n=== contacts (n={len(contacts)}) ===")
    print("  by confidence:", dict(Counter(r[0] for r in contacts)))
    print("  by source:    ", dict(Counter(r[1] for r in contacts)))
    print("  by relevance: ", dict(Counter((r[2] or "(unset)") for r in contacts)))
    valid = [r for r in contacts
             if r[2] == "relevant" and r[0] in ("high", "medium") and r[1] != "guessed"]
    print(f"  VALID TARGET emails: {len(valid)}")
    not_valid = [r for r in contacts
                 if r[2] in ("generic", "unknown") or r[1] == "guessed"]
    if not_valid:
        print(f"  ! {len(not_valid)} contacts are not valid targets "
              "(public mailbox / unclear function / guessed) -- "
              "find a relevant department or person for them")
    return 0


def cmd_export(args):
    tenant_id, _ = get_identity()
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        get_campaign(cur, tenant_id, args.campaign_id)
        cur.execute(
            "SELECT company_name, country, website, score, source, status "
            "FROM bs_ai_lead_gen_leads WHERE campaign_id = %s", (args.campaign_id,))
        leads = cur.fetchall()
        cur.execute(
            """
            SELECT l.company_name, ct.name, ct.title, ct.email,
                   ct.email_source, ct.confidence
            FROM bs_ai_lead_gen_contacts ct
            JOIN bs_ai_lead_gen_leads l ON l.id = ct.lead_id
            WHERE ct.campaign_id = %s
            """, (args.campaign_id,))
        contacts = cur.fetchall()

    contact_by_company = {}
    for c in contacts:
        contact_by_company.setdefault(c[0], []).append(c)
    lines = ["# Campaign summary", "",
             f"generated: {len(leads)} leads / {len(contacts)} contacts", ""]
    from collections import Counter
    lines.append("## leads by status")
    for k, v in Counter(r[5] for r in leads).most_common():
        lines.append(f"- {k or '(blank)'}: {v}")
    lines.append("")
    lines.append("## qualified leads with contacts")
    for (company, country, website, score, source, status) in leads:
        if status not in ("qualified", "contacted", "replied"):
            continue
        lines.append(f"\n### {company} ({country})")
        lines.append(f"- site: {website or 'MISSING'}")
        lines.append(f"- score: {score}  source: {source}")
        people = contact_by_company.get(company, [])
        if people:
            for p in people:
                lines.append(f"- {p[1]} / {p[2]} / {p[3]} [{p[4]}, {p[5]}]")
        else:
            lines.append("- NO CONTACTS")
    print("\n".join(lines))
    return 0


def cmd_list(args, what):
    if what not in ("leads", "contacts"):
        print(json.dumps({"error": f"未知列表类型: {what}（可选 leads/contacts）"},
                         ensure_ascii=False))
        return 1
    tenant_id, _ = get_identity()
    with get_conn() as conn, conn.cursor(cursor_factory=NamedTupleCursor) as cur:
        get_campaign(cur, tenant_id, args.campaign_id)
        if what == "leads":
            sql = ("SELECT company_name, country, website, status, COALESCE(score::text,'') "
                   "FROM bs_ai_lead_gen_leads WHERE campaign_id = %s")
            params = [args.campaign_id]
            if args.status:
                sql += " AND status = %s"
                params.append(args.status)
            cur.execute(sql, params)
            rows = cur.fetchall()
            for r in rows:
                print(f"{(r[0] or '')[:40]:42} {(r[1] or '')[:16]:18} "
                      f"{(r[2] or '')[:40]:42} {r[3]:10} {r[4]}")
            print(f"\ntotal: {len(rows)}")
        else:
            cur.execute(
                """
                SELECT l.company_name, ct.name, ct.title, ct.email, ct.confidence,
                       ct.email_relevance, ct.email_source
                FROM bs_ai_lead_gen_contacts ct
                JOIN bs_ai_lead_gen_leads l ON l.id = ct.lead_id
                WHERE ct.campaign_id = %s
                """, (args.campaign_id,))
            rows = cur.fetchall()
            for r in rows:
                print(f"{(r[0] or '')[:30]:32} {(r[1] or '')[:22]:24} "
                      f"{(r[2] or '')[:26]:28} {(r[3] or '')[:36]:38} "
                      f"{r[4]:8} {r[5]:10} {r[6]}")
            print(f"\ntotal: {len(rows)}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="AI获客 线索数据库管理（PostgreSQL）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.add_argument("--name", required=True)
    p.add_argument("--brief-file")
    p.add_argument("--target-region", default="")
    p.add_argument("--send-mode", default="review")
    p.add_argument("--daily-limit", type=int, default=20)
    p.add_argument("--reply-mailbox", default="")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("set-target-config")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("--json-file", required=True)
    p.set_defaults(func=cmd_set_target_config)

    p = sub.add_parser("set-send-mode")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("send_mode")
    p.set_defaults(func=cmd_set_send_mode)

    p = sub.add_parser("bind-scheduler")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("--task-id", required=True)
    p.set_defaults(func=cmd_bind_scheduler)

    p = sub.add_parser("unbind-scheduler")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("--cancel", action="store_true",
                   help="任务置 cancelled 并清除关联（缺省仅暂停）")
    p.set_defaults(func=cmd_unbind_scheduler)

    p = sub.add_parser("daily-summary")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("--date", default=None, help="YYYY-MM-DD，缺省今天")
    p.add_argument("--notify", action="store_true", help="同时发布站内信汇总通知")
    p.set_defaults(func=cmd_daily_summary)

    p = sub.add_parser("show-campaign")
    p.add_argument("--campaign-id", required=True, type=int)
    p.set_defaults(func=cmd_show_campaign)

    p = sub.add_parser("add-lead")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("--json-file", required=True)
    p.set_defaults(func=cmd_add_lead)

    p = sub.add_parser("set-status")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("--company", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--reason", default=None)
    p.set_defaults(func=cmd_set_status)

    p = sub.add_parser("add-contact")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("--json-file", required=True)
    p.set_defaults(func=cmd_add_contact)

    p = sub.add_parser("add-email-draft")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("--contact-id", required=True, type=int)
    p.add_argument("--subject-a", default="")
    p.add_argument("--subject-b", default="")
    p.add_argument("--body-file", required=True)
    p.add_argument("--ab-group", default=None)
    p.set_defaults(func=cmd_add_email_draft)

    p = sub.add_parser("set-email-status")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("--email-id", required=True, type=int)
    p.add_argument("--status", required=True)
    p.add_argument("--message-id", default=None)
    p.add_argument("--error", default=None)
    p.set_defaults(func=cmd_set_email_status)

    p = sub.add_parser("export-send-queue")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_export_send_queue)

    p = sub.add_parser("score")
    p.add_argument("--campaign-id", required=True, type=int)
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("export")
    p.add_argument("--campaign-id", required=True, type=int)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("list-leads")
    p.add_argument("--campaign-id", required=True, type=int)
    p.add_argument("--status", default=None)
    p.set_defaults(func=lambda a: cmd_list(a, "leads"))

    p = sub.add_parser("list-contacts")
    p.add_argument("--campaign-id", required=True, type=int)
    p.set_defaults(func=lambda a: cmd_list(a, "contacts"))

    args = parser.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
