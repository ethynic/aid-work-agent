"""AI获客业务数据查询 API 集成测试（真实 PG）

端点（全部只读 GET）：
- GET /api/ai-lead-gen/campaigns[/options]   获客活动列表 / 下拉
- GET /api/ai-lead-gen/leads                 线索公司列表
- GET /api/ai-lead-gen/contacts              联系人列表
- GET /api/ai-lead-gen/emails[/{id}]         邮件列表 / 详情
- GET /api/ai-lead-gen/replies[/{id}]        回复列表 / 详情

测试策略（参照 test_behavior_logs_api.py）：
- 租户上下文：patch TenantContextMiddleware._resolve_user_tenant 注入 request.state.tenant_id
- 数据：真实库直接 INSERT 五张 bs_ai_lead_gen_* 表，双租户行隔离 + 筛选/分页
- 表不存在时用幂等 DDL 兜底（与 deploy/init-postgres.sql 逐句一致）

DB 不可达时整模块 pytest.skip（init_db_pool session fixture）。
"""

import uuid

import pytest

pytestmark = pytest.mark.integration

from fastapi.testclient import TestClient
from unittest.mock import patch

from src.main import app


client = TestClient(app)

# 与 deploy/init-postgres.sql「bs_ai_lead_gen_*」节一致（幂等）
_DDL_BLOCK = """
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
);
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
);
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
);
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
);
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
);
CREATE INDEX IF NOT EXISTS idx_bs_ailg_campaigns_tenant ON bs_ai_lead_gen_campaigns(tenant_id);
"""


@pytest.fixture(scope="module", autouse=True)
def _ensure_tables(init_db_pool):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(_DDL_BLOCK)
        conn.commit()


@pytest.fixture
def env():
    """双租户：租户 A 1 个活动 + 线索 + 联系人 + 邮件 + 回复；租户 B 仅 1 个活动（跨租户隔离用）"""
    from src.db.database import get_db_connection
    from src.saas.db.tenant_db import TenantDB

    code = uuid.uuid4().hex[:6].upper()
    tenant_a = TenantDB.create(
        company_name=f"AI获客测试租户-A-{code}", tenant_code=f"LA{code}",
        contact_name="测试联系人", contact_phone="13800000000")
    tenant_b = TenantDB.create(
        company_name=f"AI获客测试租户-B-{code}", tenant_code=f"LB{code}",
        contact_name="测试联系人", contact_phone="13800000001")
    if not tenant_a or not tenant_b:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_a_id, tenant_b_id = tenant_a["tenant_id"], tenant_b["tenant_id"]
    marker = uuid.uuid4().hex[:8]

    inserted = {}
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO bs_ai_lead_gen_campaigns
               (campaign_name, tenant_id, user_id, brief, target_region, send_mode, status, daily_limit, reply_mailbox)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (f"欧美机械配件获客_{marker}", tenant_a_id, "ua_test", f"需求简报_{marker}",
             "美国,德国", "review", "completed", 20, "reply@test.com"))
        campaign_a = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO bs_ai_lead_gen_leads
               (campaign_id, tenant_id, user_id, company_name, country, city, website, industry,
                employees, founded, source, source_url, score, status, reject_reason, notes)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (campaign_a, tenant_a_id, "ua_test", f"Acme Corp_{marker}", "US", "New York",
             "https://acme.example.com", "机械制造", "50-100", "2005",
             "official_site", "https://acme.example.com/about", 85, "qualified", None, None))
        lead_a = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO bs_ai_lead_gen_leads
               (campaign_id, tenant_id, user_id, company_name, country, score, status, reject_reason)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (campaign_a, tenant_a_id, "ua_test", f"被拒公司_{marker}", "UK", 30, "rejected", "low_score"))
        lead_a_rejected = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO bs_ai_lead_gen_contacts
               (campaign_id, lead_id, tenant_id, user_id, name, title, email, email_source,
                email_type, email_function, email_relevance, confidence, language, phone)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (campaign_a, lead_a, tenant_a_id, "ua_test", "John Smith", "Procurement Manager",
             f"john_{marker}@acme.example.com", "official_site", "personal", "procurement",
             "relevant", "high", "en", "+1-555-0000"))
        contact_a = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO bs_ai_lead_gen_emails
               (campaign_id, contact_id, tenant_id, user_id, subject_a, subject_b, ab_group,
                body, status, message_id, sent_at, error, retry_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s) RETURNING id""",
            (campaign_a, contact_a, tenant_a_id, "ua_test", f"Partnership_{marker}", "Cooperation_{marker}",
             "A", "全文正文_开头_" + "长" * 300, "sent", f"<msg-{marker}@test.com>", None, 0))
        email_a = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO bs_ai_lead_gen_replies
               (campaign_id, contact_id, email_id, tenant_id, user_id, from_addr, subject,
                body, received_at, intent, intent_summary, notified, message_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s) RETURNING id""",
            (campaign_a, contact_a, email_a, tenant_a_id, "ua_test",
             f"john_{marker}@acme.example.com", f"Re: Partnership_{marker}",
             "回复原文_开头_" + "长" * 300, "interested", "有意向，索要报价单", True,
             f"<reply-{marker}@test.com>"))
        reply_a = cur.fetchone()["id"]
        cur.execute(
            """INSERT INTO bs_ai_lead_gen_campaigns (campaign_name, tenant_id, user_id, status)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (f"租户B活动_{marker}", tenant_b_id, "ub_test", "draft"))
        campaign_b = cur.fetchone()["id"]
        conn.commit()

    inserted = {
        "marker": marker,
        "tenant_a": tenant_a_id, "tenant_b": tenant_b_id,
        "campaign_a": campaign_a, "campaign_b": campaign_b,
        "lead_a": lead_a, "lead_a_rejected": lead_a_rejected,
        "contact_a": contact_a, "email_a": email_a, "reply_a": reply_a,
    }

    yield inserted

    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            for table in ("bs_ai_lead_gen_replies", "bs_ai_lead_gen_emails",
                          "bs_ai_lead_gen_contacts", "bs_ai_lead_gen_leads",
                          "bs_ai_lead_gen_campaigns"):
                cur.execute(f"DELETE FROM {table} WHERE tenant_id = ANY(%s)",
                            ([tenant_a_id, tenant_b_id],))
            conn.commit()
    except Exception:
        pass
    TenantDB.delete(tenant_a_id)
    TenantDB.delete(tenant_b_id)


def _patch_middleware_tenant(tenant_id):
    async def fake_resolve(self, request):
        return (tenant_id, "mw_user")

    return patch(
        "src.saas.middleware.TenantContextMiddleware._resolve_user_tenant",
        fake_resolve,
    )


def _patch_middleware_no_tenant():
    async def fake_resolve(self, request):
        return (None, "mw_user")

    return patch(
        "src.saas.middleware.TenantContextMiddleware._resolve_user_tenant",
        fake_resolve,
    )


def _get(path, **kwargs):
    with _patch_middleware_tenant(kwargs.pop("tenant")):
        return client.get(path, **kwargs)


class TestTenantGuard:
    """无租户上下文 / 租户隔离"""

    def test_no_tenant_context_returns_error(self):
        """漏 X-Tenant-Id（无租户上下文）必须报错，不允许查全库"""
        with _patch_middleware_no_tenant():
            response = client.get("/api/ai-lead-gen/campaigns")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "租户" in data["error"]

    def test_tenant_isolation_campaigns(self, env):
        """租户 A 只看到本租户活动，租户 B 活动不可见"""
        response = _get("/api/ai-lead-gen/campaigns", tenant=env["tenant_a"])
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        names = {item["campaign_name"] for item in data["data"]["items"]}
        assert f"欧美机械配件获客_{env['marker']}" in names
        assert f"租户B活动_{env['marker']}" not in names

    def test_tenant_isolation_detail(self, env):
        """租户 B 访问租户 A 的邮件详情，返回不存在"""
        response = _get(f"/api/ai-lead-gen/emails/{env['email_a']}", tenant=env["tenant_b"])
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "不存在" in data["error"]

    def test_tenant_isolation_reply_detail(self, env):
        response = _get(f"/api/ai-lead-gen/replies/{env['reply_a']}", tenant=env["tenant_b"])
        assert response.status_code == 200
        assert response.json()["success"] is False


class TestCampaigns:
    def test_list_and_fields(self, env):
        response = _get("/api/ai-lead-gen/campaigns", tenant=env["tenant_a"])
        data = response.json()["data"]
        assert data["total"] >= 1
        row = next(i for i in data["items"] if i["id"] == env["campaign_a"])
        for key in ("campaign_name", "brief", "target_region", "send_mode", "status",
                    "daily_limit", "reply_mailbox", "created_at"):
            assert key in row, f"缺少字段 {key}"
        assert row["target_region"] == "美国,德国"

    def test_filter_status(self, env):
        response = _get("/api/ai-lead-gen/campaigns?status=completed", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] >= 1
        response = _get("/api/ai-lead-gen/campaigns?status=sending", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 0

    def test_filter_send_mode(self, env):
        response = _get("/api/ai-lead-gen/campaigns?send_mode=auto", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 0
        response = _get("/api/ai-lead-gen/campaigns?send_mode=review", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] >= 1

    def test_filter_keyword_matches_brief(self, env):
        response = _get(f"/api/ai-lead-gen/campaigns?keyword={env['marker']}", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 1

    def test_invalid_status_rejected(self, env):
        response = _get("/api/ai-lead-gen/campaigns?status=hacked", tenant=env["tenant_a"])
        assert response.json()["success"] is False

    def test_options(self, env):
        response = _get("/api/ai-lead-gen/campaigns/options", tenant=env["tenant_a"])
        data = response.json()
        assert data["success"] is True
        ids = {i["id"] for i in data["data"]["items"]}
        assert env["campaign_a"] in ids
        assert env["campaign_b"] not in ids


class TestLeads:
    def test_list_with_join(self, env):
        response = _get(f"/api/ai-lead-gen/leads?campaign_id={env['campaign_a']}", tenant=env["tenant_a"])
        data = response.json()["data"]
        assert data["total"] == 2
        row = data["items"][0]
        assert row["campaign_name"] == f"欧美机械配件获客_{env['marker']}"

    def test_filter_status_and_keyword(self, env):
        response = _get("/api/ai-lead-gen/leads?status=rejected", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 1
        response = _get(f"/api/ai-lead-gen/leads?keyword=Acme Corp_{env['marker']}", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 1

    def test_invalid_status_rejected(self, env):
        response = _get("/api/ai-lead-gen/leads?status=whatever", tenant=env["tenant_a"])
        assert response.json()["success"] is False


class TestContacts:
    def test_list_with_company_join(self, env):
        response = _get(f"/api/ai-lead-gen/contacts?campaign_id={env['campaign_a']}", tenant=env["tenant_a"])
        data = response.json()["data"]
        assert data["total"] == 1
        row = data["items"][0]
        assert row["company_name"] == f"Acme Corp_{env['marker']}"
        assert row["email"] == f"john_{env['marker']}@acme.example.com"

    def test_filter_confidence(self, env):
        response = _get("/api/ai-lead-gen/contacts?confidence=high", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 1
        response = _get("/api/ai-lead-gen/contacts?confidence=low", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 0

    def test_filter_email_source(self, env):
        response = _get("/api/ai-lead-gen/contacts?email_source=guessed", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 0


class TestEmails:
    def test_list_body_preview_not_full(self, env):
        """列表只返回 body_preview（≤200 字符），不返回完整 body"""
        response = _get("/api/ai-lead-gen/emails", tenant=env["tenant_a"])
        data = response.json()["data"]
        assert data["total"] == 1
        row = data["items"][0]
        assert "body" not in row
        assert len(row["body_preview"]) <= 200
        assert row["contact_name"] == "John Smith"

    def test_filter_ab_group(self, env):
        response = _get("/api/ai-lead-gen/emails?ab_group=A", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 1
        response = _get("/api/ai-lead-gen/emails?ab_group=B", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 0

    def test_detail_full_body(self, env):
        """详情返回完整 body（超 200 字符）"""
        response = _get(f"/api/ai-lead-gen/emails/{env['email_a']}", tenant=env["tenant_a"])
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["body"]) > 200
        assert data["data"]["message_id"] == f"<msg-{env['marker']}@test.com>"

    def test_detail_not_found(self, env):
        response = _get("/api/ai-lead-gen/emails/99999999", tenant=env["tenant_a"])
        assert response.json()["success"] is False

    def test_invalid_status_rejected(self, env):
        response = _get("/api/ai-lead-gen/emails?status=nope", tenant=env["tenant_a"])
        assert response.json()["success"] is False


class TestReplies:
    def test_list_and_intent(self, env):
        response = _get("/api/ai-lead-gen/replies", tenant=env["tenant_a"])
        data = response.json()["data"]
        assert data["total"] == 1
        row = data["items"][0]
        assert row["intent"] == "interested"
        assert row["notified"] is True
        assert "body" not in row

    def test_filter_intent(self, env):
        response = _get("/api/ai-lead-gen/replies?intent=refused", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 0
        response = _get("/api/ai-lead-gen/replies?intent=interested", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 1

    def test_filter_notified(self, env):
        response = _get("/api/ai-lead-gen/replies?notified=true", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 1
        response = _get("/api/ai-lead-gen/replies?notified=false", tenant=env["tenant_a"])
        assert response.json()["data"]["total"] == 0

    def test_detail_full_body(self, env):
        response = _get(f"/api/ai-lead-gen/replies/{env['reply_a']}", tenant=env["tenant_a"])
        data = response.json()
        assert data["success"] is True
        assert len(data["data"]["body"]) > 200
        assert data["data"]["intent_summary"] == "有意向，索要报价单"

    def test_invalid_intent_rejected(self, env):
        response = _get("/api/ai-lead-gen/replies?intent=bad", tenant=env["tenant_a"])
        assert response.json()["success"] is False


class TestPagination:
    def test_pagination_meta(self, env):
        response = _get("/api/ai-lead-gen/leads?campaign_id={}&page=1&page_size=1".format(env["campaign_a"]),
                        tenant=env["tenant_a"])
        data = response.json()
        assert data["data"]["page"] == 1
        assert data["data"]["page_size"] == 1
        assert len(data["data"]["items"]) == 1
        assert data["data"]["total"] == 2

    def test_page2(self, env):
        response = _get("/api/ai-lead-gen/leads?campaign_id={}&page=2&page_size=1".format(env["campaign_a"]),
                        tenant=env["tenant_a"])
        data = response.json()
        assert data["data"]["page"] == 2
        assert len(data["data"]["items"]) == 1
