"""AI获客技能（ai-lead-gen-1.0.0）单元测试。

覆盖：lead_db 身份/去重/五表建表/联系人入库闸门、mailbox_resolver 三层解析、
send_emails 的 cfg 构建/结果回写/主题与限额纯函数。数据库一律 mock（FakeConn）。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parents[2] / "src" / "skills" / "ai-lead-gen-1.0.0" / "scripts"
sys.path.insert(0, str(SKILL_DIR))

import lead_db  # noqa: E402
import mailbox_resolver  # noqa: E402
import send_emails  # noqa: E402
from psycopg2.extras import NamedTupleCursor  # noqa: E402


# ---------- Fake DB harness ----------

class FakeCursor:
    def __init__(self):
        self.executed = []
        self.fetchall_queue = []
        self.fetchone_queue = []
        self.rowcount = 0
        # False 时模拟 RealDictCursor：行包装成字典（列名取 self.columns）
        self.tuple_mode = True
        self.columns = ["id"]

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def _wrap(self, row):
        if self.tuple_mode or row is None:
            return row
        return dict(zip(self.columns, row))

    def fetchall(self):
        rows = self.fetchall_queue.pop(0) if self.fetchall_queue else []
        return [self._wrap(r) for r in rows]

    def fetchone(self):
        row = self.fetchone_queue.pop(0) if self.fetchone_queue else None
        return self._wrap(row)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeConn:
    def __init__(self):
        self.cur = FakeCursor()
        self.commits = 0

    def cursor(self, cursor_factory=None):
        # 模拟平台 get_db_connection 语义：不传 factory = RealDictCursor（字典行），
        # 显式传 NamedTupleCursor = 元组行。技能脚本必须显式传 factory，
        # 否则下标/解包取值全部错乱（2026-10-27 线上 KeyError: 0 回归防线）
        self.cur.tuple_mode = cursor_factory is not None
        return self.cur

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def campaign_row(campaign_id=7):
    # get_campaign 列序：id, campaign_name, send_mode, status, daily_limit, reply_mailbox, brief
    return (campaign_id, "测试活动", "review", "draft", 25, None, "brief")


@pytest.fixture(autouse=True)
def _identity_env(monkeypatch):
    monkeypatch.setenv("AID_TENANT_ID", "tenant_test")
    monkeypatch.setenv("AID_USER_ID", "user_test")


# ---------- lead_db ----------

def test_get_identity_from_env():
    assert lead_db.get_identity() == ("tenant_test", "user_test")


def test_get_identity_missing_tenant_exits(monkeypatch):
    monkeypatch.delenv("AID_TENANT_ID", raising=False)
    monkeypatch.delenv("CURRENT_TENANT_ID", raising=False)
    with pytest.raises(SystemExit):
        lead_db.get_identity()


def test_norm_company_dedup_key():
    assert lead_db.norm_company("Acme Co., Ltd.") == lead_db.norm_company("acme")
    assert lead_db.norm_company("Acme, Inc.") == lead_db.norm_company("ACME")


def test_init_tables_creates_five_tables(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    lead_db.init_tables()
    tables = {sql.split("CREATE TABLE IF NOT EXISTS ")[1].split(" (")[0]
              for sql, _ in conn.cur.executed
              if sql.startswith("CREATE TABLE IF NOT EXISTS bs_ai_lead_gen")}
    assert tables == {
        "bs_ai_lead_gen_campaigns", "bs_ai_lead_gen_leads",
        "bs_ai_lead_gen_contacts", "bs_ai_lead_gen_emails",
        "bs_ai_lead_gen_replies",
    }
    # DDL 必须显式提交（get_db_connection 退出时无条件 rollback）
    assert conn.commits == 1


def test_add_lead_dedup_and_tenant_scope(monkeypatch, tmp_path):
    conn = FakeConn()
    conn.cur.fetchone_queue.append(campaign_row())   # get_campaign
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    f = tmp_path / "leads.json"
    f.write_text(json.dumps([
        {"company": "Acme Ltd", "website": "https://acme.com", "score": 80},
        {"company": "Acme Co., Ltd.", "website": "https://acme.com/"},  # 归一化后重复
        {"company": "Beta GmbH"},
    ]), encoding="utf-8")
    args = type("A", (), {"json_file": str(f), "campaign_id": 7})()
    assert lead_db.cmd_add_lead(args) == 0
    inserts = [(s, p) for s, p in conn.cur.executed if s.startswith("INSERT INTO bs_ai_lead_gen_leads")]
    assert len(inserts) == 2
    # 租户/用户身份随 INSERT 落库
    assert inserts[0][1][1] == "tenant_test" and inserts[0][1][2] == "user_test"
    # 写命令必须显式 commit，否则 get_db_connection 退出时回滚
    assert conn.commits == 1


def test_add_contact_gate_blocks_irrelevant_and_generic(monkeypatch, tmp_path):
    conn = FakeConn()
    conn.cur.fetchone_queue.append(campaign_row())   # get_campaign
    conn.cur.fetchall_queue.append([(1, "Acme Ltd")])  # leads 查询
    conn.cur.fetchall_queue.append([])               # 已有联系人 email
    conn.cur.fetchall_queue.append([])               # 每公司计数
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    monkeypatch.setattr(lead_db, "campaign_dir", lambda t, c: tmp_path)
    monkeypatch.setattr(lead_db, "load_target_config", lambda p: {})
    f = tmp_path / "contacts.json"
    f.write_text(json.dumps([
        {"name": "John", "email": "john@acme.com", "company": "Acme Ltd",
         "email_source": "official_site", "confidence": "high"},
        {"name": "Hr", "email": "hr@acme.com", "company": "Acme Ltd",
         "email_source": "official_site", "confidence": "high"},
        {"name": "Info", "email": "info@acme.com", "company": "Acme Ltd",
         "email_source": "official_site", "confidence": "high"},
    ]), encoding="utf-8")
    args = type("A", (), {"json_file": str(f), "campaign_id": 7})()
    assert lead_db.cmd_add_contact(args) == 0
    inserts = [s for s, _ in conn.cur.executed if s.startswith("INSERT INTO bs_ai_lead_gen_contacts")]
    assert len(inserts) == 1
    assert conn.commits == 1
    # 入库行的职能判定结果（列序：campaign_id, lead_id, tenant_id, user_id, name, title, email...）
    params = [p for s, p in conn.cur.executed if s.startswith("INSERT INTO bs_ai_lead_gen_contacts")][0]
    assert params[6] == "john@acme.com"


def test_add_contact_guessed_forced_low_confidence(monkeypatch, tmp_path):
    conn = FakeConn()
    conn.cur.fetchone_queue.append(campaign_row())
    conn.cur.fetchall_queue.append([(1, "Acme Ltd")])
    conn.cur.fetchall_queue.append([])
    conn.cur.fetchall_queue.append([])
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    monkeypatch.setattr(lead_db, "campaign_dir", lambda t, c: tmp_path)
    monkeypatch.setattr(lead_db, "load_target_config", lambda p: {})
    f = tmp_path / "contacts.json"
    f.write_text(json.dumps([
        {"name": "Guess", "email": "guess@acme.com", "company": "Acme Ltd",
         "email_source": "made_up", "confidence": "high"},
    ]), encoding="utf-8")
    args = type("A", (), {"json_file": str(f), "campaign_id": 7})()
    assert lead_db.cmd_add_contact(args) == 0
    insert_params = [p for s, p in conn.cur.executed
                     if s.startswith("INSERT INTO bs_ai_lead_gen_contacts")][0]
    # email_source 被纠正为 guessed，confidence 强制 low（列序见上一测试）
    assert insert_params[7] == "guessed"
    assert insert_params[11] == "low"


def test_set_send_mode_invalid_rejected(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    args = type("A", (), {"send_mode": "auto_send", "campaign_id": 7})()
    assert lead_db.cmd_set_send_mode(args) == 1
    assert not any(s.startswith("UPDATE") for s, _ in conn.cur.executed)
    assert conn.commits == 0  # 非法值不落库


def test_set_send_mode_valid_commits(monkeypatch):
    conn = FakeConn()
    conn.cur.fetchone_queue.append(campaign_row())  # get_campaign
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    args = type("A", (), {"send_mode": "auto", "campaign_id": 7})()
    assert lead_db.cmd_set_send_mode(args) == 0
    updates = [p for s, p in conn.cur.executed if s.startswith("UPDATE")]
    assert updates and updates[0][0] == "auto"
    assert conn.commits == 1


def test_cmd_list_dispatch_leads_and_contacts(monkeypatch):
    conn = FakeConn()
    conn.cur.fetchone_queue.append(campaign_row())
    conn.cur.fetchall_queue.append([("Acme Ltd", "US", "https://acme.com", "new", "80")])
    conn.cur.fetchone_queue.append(campaign_row())
    conn.cur.fetchall_queue.append([("Acme Ltd", "John", "CEO", "john@acme.com",
                                     "high", "relevant", "official_site")])
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    args = type("A", (), {"campaign_id": 7, "status": None})()
    assert lead_db.cmd_list(args, "leads") == 0
    assert lead_db.cmd_list(args, "contacts") == 0
    assert lead_db.cmd_list(args, "bogus") == 1


def test_get_campaign_cursor_semantics():
    # 回归 2026-10-27：平台默认 RealDictCursor 的行是字典（迭代产出列名），
    # get_campaign 的 dict(zip(cols,row)) 会把列名当值。所有调用方
    # （lead_db 各子命令、send_emails.main 的 campaign 读取）都必须传 NamedTupleCursor
    conn = FakeConn()
    conn.cur.columns = ["id", "campaign_name", "send_mode", "status",
                        "daily_limit", "reply_mailbox", "brief"]
    conn.cur.fetchone_queue.append(campaign_row())

    campaign = lead_db.get_campaign(
        conn.cursor(NamedTupleCursor), "tenant_test", 7)
    assert campaign["daily_limit"] == 25 and campaign["reply_mailbox"] is None

    # 错误路径对照：默认字典游标下列名被当值（这是必须传 factory 的原因）
    conn.cur.fetchone_queue.append(campaign_row())
    bad = lead_db.get_campaign(conn.cursor(), "tenant_test", 7)
    assert bad["daily_limit"] == "daily_limit"


def test_cmd_init_reads_returning_id_as_tuple(monkeypatch, tmp_path, capsys):
    # 回归 2026-10-27：平台默认 RealDictCursor 返回字典行，脚本未指定
    # NamedTupleCursor 时 fetchone()[0] 抛 KeyError: 0，campaign 建不出来
    conn = FakeConn()
    conn.cur.fetchone_queue.append((1,))   # 发件邮箱前置检查：租户层命中
    conn.cur.fetchone_queue.append((42,))
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    monkeypatch.setattr(lead_db, "campaign_dir", lambda t, c: tmp_path / "ws")
    args = type("A", (), {"name": "c", "brief_file": None, "target_region": "",
                          "send_mode": "review", "daily_limit": 20,
                          "reply_mailbox": None})()
    assert lead_db.cmd_init(args) == 0
    out = capsys.readouterr().out
    assert "campaign created: id=42" in out
    # INSERT 后必须显式 commit（get_db_connection 退出时无条件 rollback）
    assert conn.commits == 1


def test_cmd_init_fail_fast_without_mailbox(monkeypatch, capsys):
    # 2026-10-27 trace 实证：未配置发件邮箱时整个流程走完才在发送阶段暴露。
    # init 必须前置校验并拒绝创建（不 INSERT、不建目录）
    conn = FakeConn()
    conn.cur.fetchone_queue.append(None)   # 用户层无
    conn.cur.fetchone_queue.append(None)   # 租户层无
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    args = type("A", (), {"name": "c", "brief_file": None, "target_region": "",
                          "send_mode": "review", "daily_limit": 20,
                          "reply_mailbox": None})()
    assert lead_db.cmd_init(args) == 1
    out = capsys.readouterr().out
    assert "尚未配置发件邮箱" in out
    assert conn.commits == 0
    # INSERT 未执行（前两条是邮箱检查 SELECT）
    assert not any("INSERT INTO bs_ai_lead_gen_campaigns" in sql
                   for sql, _ in conn.cur.executed)


def test_add_email_draft_invalid_contact_lists_valid_ids(monkeypatch, tmp_path, capsys):
    # 2026-10-27 trace 实证：LLM 盲猜 contact_id 连续 not found 空转。
    # 报错必须回带本 campaign 有效 contact ID 清单
    conn = FakeConn()
    conn.cur.fetchone_queue.append(campaign_row(3))  # get_campaign 命中
    conn.cur.fetchone_queue.append(None)   # contact 未命中
    conn.cur.fetchall_queue.append([(5, "张三", "z@x.com"), (7, "李四", "l@y.com")])
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    body = tmp_path / "body.txt"
    body.write_text("hello", encoding="utf-8")
    args = type("A", (), {"campaign_id": 3, "contact_id": 99, "subject_a": "a",
                          "subject_b": "b", "body_file": str(body),
                          "ab_group": None})()
    assert lead_db.cmd_add_email_draft(args) == 1
    out = capsys.readouterr().out
    assert "not found" in out
    assert '"id": 5' in out and "z@x.com" in out
    assert conn.commits == 0


def test_show_campaign_group_counts_with_tuple_rows(monkeypatch, capsys):
    # GROUP BY 计数用 dict(fetchall())，要求行可按 (key, value) 对迭代（元组语义）
    conn = FakeConn()
    conn.cur.fetchone_queue.append(campaign_row())
    conn.cur.fetchall_queue.append([("new", 3), ("qualified", 1)])
    conn.cur.fetchall_queue.append([("draft", 2)])
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    args = type("A", (), {"campaign_id": 7})()
    assert lead_db.cmd_show_campaign(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["leads_by_status"] == {"new": 3, "qualified": 1}
    assert out["emails_by_status"] == {"draft": 2}
    assert out["campaign"]["id"] == 7


def test_set_status_matches_company_by_tuple_unpack(monkeypatch):
    # matched = [(i, n) for i, n in cur.fetchall()]：字典行会解包出列名而非值
    conn = FakeConn()
    conn.cur.fetchone_queue.append(campaign_row())
    conn.cur.fetchall_queue.append([(11, "Acme Ltd")])
    monkeypatch.setattr(lead_db, "get_conn", lambda: conn)
    args = type("A", (), {"campaign_id": 7, "company": "acme",
                          "status": "qualified", "reason": None})()
    assert lead_db.cmd_set_status(args) == 0
    updates = [p for s, p in conn.cur.executed if s.startswith("UPDATE")]
    assert updates and updates[0][-1] == 11
    assert conn.commits == 1


# ---------- mailbox_resolver ----------

MAILBOX_COLUMNS = 9  # email_address..imap_encryption


def _mailbox_row(addr="boss@corp.com", level_hint="user"):
    return (addr, "smtp.corp.com", 465, addr, "enc-password",
            "ssl", "imap.corp.com", 993, "ssl")


def test_resolve_mailbox_user_first(monkeypatch):
    conn = FakeConn()
    conn.cur.fetchone_queue.append(_mailbox_row())   # user 层命中
    monkeypatch.setattr(mailbox_resolver, "_conn", lambda: conn)
    monkeypatch.setattr(mailbox_resolver, "_decrypted_row",
                        lambda row: {"email_address": row[0], "smtp_server": row[1],
                                     "smtp_port": row[2], "smtp_user": row[3],
                                     "smtp_password": "decrypted",
                                     "smtp_encryption": row[5], "imap_server": row[6],
                                     "imap_port": row[7], "imap_encryption": row[8]})
    cfg = mailbox_resolver.resolve_mailbox("tenant_test", "user_test")
    assert cfg["level"] == "user"
    assert cfg["smtp_password"] == "decrypted"
    assert conn.cur.executed[0][0].startswith("SELECT")
    assert "user_email_settings" in conn.cur.executed[0][0]


def test_resolve_mailbox_tenant_fallback(monkeypatch):
    conn = FakeConn()
    conn.cur.fetchone_queue.append(None)             # user 层无配置
    conn.cur.fetchone_queue.append(_mailbox_row())   # 租户层命中
    monkeypatch.setattr(mailbox_resolver, "_conn", lambda: conn)
    monkeypatch.setattr(mailbox_resolver, "_decrypted_row",
                        lambda row: {"email_address": row[0], "smtp_server": row[1],
                                     "smtp_port": row[2], "smtp_user": row[3],
                                     "smtp_password": "decrypted",
                                     "smtp_encryption": row[5], "imap_server": row[6],
                                     "imap_port": row[7], "imap_encryption": row[8]})
    cfg = mailbox_resolver.resolve_mailbox("tenant_test", "user_test")
    assert cfg["level"] == "tenant"
    assert "tenant_email_settings" in conn.cur.executed[1][0]


def test_resolve_mailbox_none_when_unconfigured(monkeypatch):
    conn = FakeConn()
    conn.cur.fetchone_queue.append(None)
    conn.cur.fetchone_queue.append(None)
    monkeypatch.setattr(mailbox_resolver, "_conn", lambda: conn)
    monkeypatch.setattr(mailbox_resolver, "_decrypted_row", lambda row: {})
    assert mailbox_resolver.resolve_mailbox("tenant_test", "user_test") is None


def test_resolve_mailbox_requires_tenant(monkeypatch):
    monkeypatch.delenv("AID_TENANT_ID", raising=False)
    monkeypatch.delenv("CURRENT_TENANT_ID", raising=False)
    with pytest.raises(RuntimeError):
        mailbox_resolver.resolve_mailbox("", "user_test")


# ---------- send_emails ----------

def test_pick_subject_explicit_group_and_hash_fallback():
    item = {"subject_a": "A subject", "subject_b": "B subject", "ab_group": "B", "to": "x@y.com"}
    assert send_emails.pick_subject(item) == ("B subject", "B")
    item2 = {"subject_a": "A subject", "subject_b": "B subject", "to": "x@y.com"}
    s1, g1 = send_emails.pick_subject(item2)
    s2, g2 = send_emails.pick_subject(dict(item2))
    assert (s1, g1) == (s2, g2) and g1 in ("A", "B")
    legacy = send_emails.pick_subject({"subject": "only", "to": "x@y.com"})
    assert legacy == ("only", "none")


def test_rampup_cap_table():
    assert send_emails.rampup_cap(0) == 20
    assert send_emails.rampup_cap(49) == 20
    assert send_emails.rampup_cap(100) == 50
    assert send_emails.rampup_cap(499) == 100
    assert send_emails.rampup_cap(500) == 200


def test_build_cfg_from_mailbox_and_campaign(monkeypatch, tmp_path):
    cdir = tmp_path / "campaign"
    cdir.mkdir()
    (tmp_path / "sender_profile.json").write_text(
        json.dumps({"sender": {"from_name": "Acme Sales"}}), encoding="utf-8")
    campaign = {"daily_limit": 25, "reply_mailbox": None}
    mailbox = {"email_address": "boss@corp.com", "smtp_server": "smtp.corp.com",
               "smtp_port": 465, "smtp_user": "boss@corp.com",
               "smtp_password": "decrypted", "smtp_encryption": "ssl"}
    cfg = send_emails.build_cfg(campaign, mailbox, cdir)
    assert cfg["smtp_host"] == "smtp.corp.com"
    assert cfg["smtp_secure"] is True
    assert cfg["from_email"] == "boss@corp.com"
    assert cfg["from_name"] == "Acme Sales"
    assert cfg["daily_limit"] == 25
    assert cfg["reply_to"] == "boss@corp.com"  # campaign 未指定时回退发件邮箱
    assert cfg["smtp_password"] == "decrypted"


def test_build_cfg_reply_to_prefers_campaign(tmp_path):
    campaign = {"daily_limit": 20, "reply_mailbox": "reply@corp.com"}
    mailbox = {"email_address": "boss@corp.com", "smtp_server": "smtp.corp.com",
               "smtp_port": 465, "smtp_user": "boss@corp.com",
               "smtp_password": "decrypted", "smtp_encryption": "ssl"}
    cfg = send_emails.build_cfg(campaign, mailbox, tmp_path)
    assert cfg["reply_to"] == "reply@corp.com"


def test_record_result_writes_db(monkeypatch):
    conn = FakeConn()
    monkeypatch.setattr(send_emails, "get_conn", lambda: conn)
    send_emails.record_result("tenant_test", 42, "sent", message_id="<abc@corp.com>")
    sql, params = conn.cur.executed[0]
    assert "UPDATE bs_ai_lead_gen_emails" in sql
    # 租户过滤：UPDATE 必须限定 tenant_id，防止越租户回写
    assert "tenant_id = %s" in sql
    assert params[0] == "sent" and params[1] == "<abc@corp.com>"
    assert params[-2] == 42 and params[-1] == "tenant_test"
    assert conn.commits == 1


def test_record_result_swallows_db_errors(monkeypatch):
    def broken():
        raise RuntimeError("db down")
    monkeypatch.setattr(send_emails, "get_conn", broken)
    send_emails.record_result("tenant_test", 42, "failed", error="x")  # 不应抛异常


def test_connect_requires_password(monkeypatch):
    # 系统 .env 的 SMTP_PASSWORD 不得兜底（平台通知邮箱与本技能隔离）
    monkeypatch.setenv("SMTP_PASSWORD", "leaked-env-password")
    with pytest.raises(SystemExit):
        send_emails.connect({"smtp_host": "h", "smtp_port": 465,
                             "smtp_password": "", "from_email": "a@b.com"})


def test_build_cfg_encryption_none_plain(tmp_path):
    campaign = {"daily_limit": 20, "reply_mailbox": None}
    mailbox = {"email_address": "boss@corp.com", "smtp_server": "smtp.corp.com",
               "smtp_port": 25, "smtp_user": "boss@corp.com",
               "smtp_password": "decrypted", "smtp_encryption": "none"}
    cfg = send_emails.build_cfg(campaign, mailbox, tmp_path)
    assert cfg["smtp_secure"] is False
    assert cfg["smtp_starttls"] is False


def test_skill_md_declares_init_script():
    skill_md = (SKILL_DIR.parent / "SKILL.md").read_text(encoding="utf-8")
    assert "init_script: lead_db.py" in skill_md
    assert "name: ai-lead-gen" in skill_md
    assert "requires_tools: [web_search, web_fetch]" in skill_md


def test_subagent_md_loadable():
    sub = Path(__file__).resolve().parents[2] / "subagents" / "ai-lead-gen" / "SUBAGENT.md"
    text = sub.read_text(encoding="utf-8")
    assert text.startswith("---")
    # system_prompt 必须在 body（frontmatter 之外），不在 YAML 里
    parts = text.split("---", 2)
    assert len(parts) == 3
    assert "skills:" in parts[1] and "ai-lead-gen" in parts[1]
    assert "AI获客" in parts[2]
    assert "system_prompt:" not in parts[1]
