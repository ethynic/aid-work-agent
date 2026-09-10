"""P4-B webhook/事件源/内部事件示例 API 集成测试（真实 DB + TestClient）

覆盖（R57/R58）：签名 webhook HTTP 语义（202/401/403/404/413/422/429、envelope、
持久接纳后 202、重复事件原结果）、event-sources 列表/创建（凭据掩码、不回旧密钥）、
rotate-key（属主限定 403、旧新并行窗）、example-orders 示例链。
"""

import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PREFIX = "/api/weixin-marketing"

ALL_CLEANUP_TABLES = (
    "desktop_automation_attempts",
    "desktop_automation_evidence",
    "desktop_automation_deliveries",
    "desktop_automation_runs",
    "desktop_automation_occurrences",
    "desktop_automation_outbox",
    "desktop_automation_events",
    "desktop_automation_event_sources",
    "desktop_automation_schedules",
    "desktop_automation_subjects",
    "desktop_automation_audit_events",
    "desktop_automation_quota_buckets",
    "local_tool_operation_permits",
    "local_tool_events",
    "local_tool_invocations",
    "local_tool_devices",
    "bs_weixin_marketing_audit_events",
    "bs_weixin_marketing_content_blocks",
    "bs_weixin_marketing_revisions",
    "bs_weixin_marketing_automations",
    "bs_weixin_marketing_group_bindings",
    "bs_weixin_marketing_account_bindings",
    "bs_weixin_marketing_assets",
    "weixin_marketing_idempotency_keys",
    # P4-B 表（nonce 无 tenant 列，单独按 source 子查询清理）
    "weixin_marketing_example_orders",
    "weixin_marketing_event_payloads",
    "weixin_marketing_event_source_keys",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


@pytest.fixture(scope="module")
def client():
    import os
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent.parent / ".env")
    os.environ.setdefault("RPA_SECRET_KEY", "test-wxm-event-source-key-32bytes")

    from src.main import app
    from src.weixin_marketing import api as wxm_api

    if not any(getattr(r, "path", "").startswith(PREFIX) for r in app.routes):
        app.include_router(wxm_api.router)
    return TestClient(app)


@pytest.fixture(scope="module")
def tenant():
    from src.saas.db.tenant_db import TenantDB

    code = f"W{uuid.uuid4().hex[:6].upper()}"
    created = TenantDB.create(
        company_name=f"微信事件源测试-{code}", tenant_code=code,
        contact_name="测试", contact_phone="13800000000",
    )
    if not created:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = created["tenant_id"]
    yield tenant_id
    from src.db.database import get_db_connection

    TenantDB.delete(tenant_id)
    problems = []
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM weixin_marketing_webhook_nonces WHERE source_id IN "
                "(SELECT id FROM desktop_automation_event_sources WHERE tenant_id = %s)",
                (tenant_id,),
            )
            for table in ALL_CLEANUP_TABLES:
                cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            cur.execute(
                "DELETE FROM tokens WHERE user_id IN "
                "(SELECT user_id FROM users WHERE tenant_id = %s)",
                (tenant_id,),
            )
            cur.execute("DELETE FROM users WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
            conn.commit()
            for table in (*ALL_CLEANUP_TABLES, "users", "tenants"):
                cur.execute(
                    f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s", (tenant_id,)
                )
                if int(cur.fetchone()["c"]):
                    problems.append(table)
    except Exception as e:  # noqa: BLE001
        problems.append(str(e))
    assert not problems, f"测试数据残留: {problems}"


@pytest.fixture(scope="module")
def owner(tenant):
    from src.api.auth import generate_token
    from src.db.models import UserDB

    phone = f"198{uuid.uuid4().int % 10**8:08d}"
    user = UserDB.create(phone=phone, username="事件源属主", tenant_id=tenant)
    if not user:
        pytest.skip("无法创建测试用户")
    return {
        "user_id": user["user_id"], "tenant_id": tenant,
        "token": generate_token(user["user_id"]),
    }


@pytest.fixture(scope="module")
def stranger(tenant):
    from src.api.auth import generate_token
    from src.db.models import UserDB

    phone = f"197{uuid.uuid4().int % 10**8:08d}"
    user = UserDB.create(phone=phone, username="同租户非属主", tenant_id=tenant)
    if not user:
        pytest.skip("无法创建测试用户")
    return {
        "user_id": user["user_id"], "tenant_id": tenant,
        "token": generate_token(user["user_id"]),
    }


def _sign(secret, ts, nonce, body: bytes) -> str:
    import hashlib
    import hmac

    message = f"{ts}.{nonce}.".encode() + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def _post_webhook(client, source_id, body: dict, *, secret=None, ts=None,
                  nonce=None, key_id=None, signature=None, raw_body=None):
    ts = ts or str(int(time.time()))
    nonce = nonce or f"n-{uuid.uuid4().hex[:10]}"
    raw = raw_body if raw_body is not None else json.dumps(body).encode()
    headers = {
        "X-WX-Timestamp": ts,
        "X-WX-Nonce": nonce,
        "X-WX-Signature": (
            signature if signature is not None
            else _sign(secret or "wrong-secret", ts, nonce, raw)
        ),
    }
    if key_id:
        headers["X-WX-Key-Id"] = key_id
    return client.post(f"{PREFIX}/webhooks/{source_id}", content=raw, headers=headers)


def _create_webhook_source(client, owner, *, allowed=None):
    resp = client.post(
        f"{PREFIX}/event-sources",
        headers=_auth(owner),
        json={
            "source_ref": f"hook-{uuid.uuid4().hex[:8]}",
            "source_type": "webhook",
            "allowed_event_types": allowed or ["order.completed"],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["secret"], "创建响应应一次性返回明文 secret"
    return data


class TestWebhookEndpoint:
    def test_valid_event_accepted_202(self, client, tenant, owner):
        created = _create_webhook_source(client, owner)
        resp = _post_webhook(
            client, created["source"]["id"],
            {"event_id": "ev-http-1", "event_type": "order.completed", "n": 1},
            secret=created["secret"], key_id=created["key_id"],
        )
        assert resp.status_code == 202
        data = resp.json()["data"]
        assert data["duplicate"] is False and data["event_id"]

    def test_duplicate_returns_original_202(self, client, tenant, owner):
        created = _create_webhook_source(client, owner)
        body = {"event_id": "ev-http-dup", "event_type": "order.completed"}
        r1 = _post_webhook(client, created["source"]["id"], body,
                           secret=created["secret"], nonce="n-1")
        r2 = _post_webhook(client, created["source"]["id"], body,
                           secret=created["secret"], nonce="n-2")
        assert r1.status_code == r2.status_code == 202
        assert r2.json()["data"]["duplicate"] is True
        assert r2.json()["data"]["event_id"] == r1.json()["data"]["event_id"]

    def test_missing_signature_401(self, client, tenant, owner):
        created = _create_webhook_source(client, owner)
        resp = _post_webhook(
            client, created["source"]["id"], {"event_id": "ev-x"},
            secret=created["secret"], signature="",
        )
        assert resp.status_code == 401
        assert resp.json()["code"] == "INVALID_SIGNATURE"

    def test_wrong_signature_401(self, client, tenant, owner):
        created = _create_webhook_source(client, owner)
        resp = _post_webhook(
            client, created["source"]["id"], {"event_id": "ev-x"},
        )
        assert resp.status_code == 401 and resp.json()["code"] == "INVALID_SIGNATURE"

    def test_expired_timestamp_403(self, client, tenant, owner):
        created = _create_webhook_source(client, owner)
        old_ts = str(int((utcnow() - timedelta(minutes=6)).timestamp()))
        resp = _post_webhook(
            client, created["source"]["id"], {"event_id": "ev-old"},
            secret=created["secret"], ts=old_ts,
        )
        assert resp.status_code == 403
        assert resp.json()["code"] == "TIMESTAMP_OUT_OF_WINDOW"

    def test_nonce_replay_403(self, client, tenant, owner):
        created = _create_webhook_source(client, owner)
        body = {"event_id": "ev-replay", "event_type": "order.completed"}
        kwargs = dict(secret=created["secret"], nonce="n-replay-http")
        r1 = _post_webhook(client, created["source"]["id"], body, **kwargs)
        r2 = _post_webhook(client, created["source"]["id"], body, **kwargs)
        assert r1.status_code == 202
        assert r2.status_code == 403 and r2.json()["code"] == "NONCE_REPLAYED"

    def test_unknown_source_404(self, client, tenant, owner):
        resp = _post_webhook(
            client, str(uuid.uuid4()), {"event_id": "ev-404"},
            secret="any", signature="x" * 64,
        )
        assert resp.status_code == 404

    def test_tenant_forgery_422(self, client, tenant, owner):
        created = _create_webhook_source(client, owner)
        resp = _post_webhook(
            client, created["source"]["id"],
            {"event_id": "ev-forge", "tenant_id": "someone-else"},
            secret=created["secret"],
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "IDENTITY_FIELD_FORBIDDEN"

    def test_event_type_not_allowed_422(self, client, tenant, owner):
        created = _create_webhook_source(client, owner)
        resp = _post_webhook(
            client, created["source"]["id"],
            {"event_id": "ev-type", "event_type": "not.allowed"},
            secret=created["secret"],
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "EVENT_TYPE_NOT_ALLOWED"

    def test_oversized_body_413(self, client, tenant, owner):
        created = _create_webhook_source(client, owner)
        big = {"event_id": "ev-big", "event_type": "order.completed",
               "pad": "x" * (300 * 1024)}
        resp = _post_webhook(
            client, created["source"]["id"], big, secret=created["secret"],
        )
        assert resp.status_code == 413
        assert resp.json()["code"] == "PAYLOAD_TOO_LARGE"

    def test_rate_limited_429(self, client, tenant, owner):
        """洪峰限流：打满 per-minute 上限后第 N+1 次 429（限流器按 config 上限）"""
        from src.weixin_marketing import event_sources as wxm_sources
        from src.weixin_marketing.config import get_weixin_marketing_config

        created = _create_webhook_source(client, owner)
        limit = get_weixin_marketing_config().webhook_rate_limit_per_minute
        limiter = wxm_sources.WebhookRateLimiter(max_per_minute=3)
        original = wxm_sources.rate_limiter_for
        wxm_sources.rate_limiter_for = lambda cfg: limiter
        try:
            statuses = []
            for i in range(4):
                resp = _post_webhook(
                    client, created["source"]["id"],
                    {"event_id": f"ev-rl-{i}", "event_type": "order.completed"},
                    secret=created["secret"], nonce=f"n-rl-{i}",
                )
                statuses.append(resp.status_code)
            assert statuses[:3] == [202, 202, 202]
            assert statuses[3] == 429 and resp.json()["code"] == "RATE_LIMITED"
        finally:
            wxm_sources.rate_limiter_for = original
            # 排空限流窗避免影响后续用例（全局单例内存桶按 source_id 隔离，无需处理）

    def test_rotation_window_old_and_new_both_valid(
        self, client, tenant, owner
    ):
        created = _create_webhook_source(client, owner)
        rotate = client.post(
            f"{PREFIX}/event-sources/{created['source']['id']}/rotate-key",
            headers=_auth(owner),
        )
        assert rotate.status_code == 200, rotate.text
        new_data = rotate.json()["data"]
        assert new_data["secret"] and new_data["key_version"] == 2
        body = {"event_id": "ev-rot", "event_type": "order.completed"}
        r_old = _post_webhook(
            client, created["source"]["id"], body,
            secret=created["secret"], key_id=created["key_id"], nonce="n-rot-old",
        )
        r_new = _post_webhook(
            client, created["source"]["id"], body,
            secret=new_data["secret"], key_id=new_data["key_id"], nonce="n-rot-new",
        )
        assert r_old.status_code == 202
        assert r_new.status_code == 202 and r_new.json()["data"]["duplicate"] is True


class TestEventSourcesApi:
    def test_list_masks_credentials(self, client, tenant, owner):
        created = _create_webhook_source(client, owner)
        resp = client.get(f"{PREFIX}/event-sources", headers=_auth(owner))
        assert resp.status_code == 200
        payload = json.dumps(resp.json(), ensure_ascii=False)
        assert created["secret"] not in payload, "列表绝不回明文 secret"
        items = resp.json()["data"]["items"]
        mine = next(i for i in items if i["id"] == created["source"]["id"])
        assert mine["keys"][0]["key_id"] == created["key_id"]
        assert mine["keys"][0]["status"] == "active"
        assert "encrypted_secret" not in payload

    def test_create_validation_422(self, client, tenant, owner):
        resp = client.post(
            f"{PREFIX}/event-sources", headers=_auth(owner),
            json={"source_ref": "", "source_type": "webhook"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "VALIDATION_FAILED"

    def test_create_internal_type(self, client, tenant, owner):
        resp = client.post(
            f"{PREFIX}/event-sources", headers=_auth(owner),
            json={
                "source_ref": f"int-{uuid.uuid4().hex[:8]}",
                "source_type": "internal",
                "allowed_event_types": ["example.order_completed"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["secret"] is None and data["key_id"] is None

    def test_rotate_by_non_creator_403(self, client, tenant, owner, stranger):
        created = _create_webhook_source(client, owner)
        resp = client.post(
            f"{PREFIX}/event-sources/{created['source']['id']}/rotate-key",
            headers=_auth(stranger),
        )
        assert resp.status_code == 403

    def test_rotate_unknown_404(self, client, tenant, owner):
        resp = client.post(
            f"{PREFIX}/event-sources/{uuid.uuid4()}/rotate-key",
            headers=_auth(owner),
        )
        assert resp.status_code == 404

    def test_create_webhook_over_internal_ref_409_no_mutation(
        self, client, tenant, owner, stranger
    ):
        """P1-1 复审（复审复现路径）：B 用 A 的 internal 源 ref 创建 webhook →
        409 SOURCE_REF_CONFLICT，原 internal 源行/密钥零变化。"""
        ref = f"int-{uuid.uuid4().hex[:8]}"
        created = client.post(
            f"{PREFIX}/event-sources", headers=_auth(owner),
            json={
                "source_ref": ref, "source_type": "internal",
                "allowed_event_types": ["example.order_completed"],
            },
        )
        assert created.status_code == 200
        original_id = created.json()["data"]["source"]["id"]

        hijack = client.post(
            f"{PREFIX}/event-sources", headers=_auth(stranger),
            json={"source_ref": ref, "source_type": "webhook"},
        )
        assert hijack.status_code == 409
        body = hijack.json()
        assert body["code"] == "SOURCE_REF_CONFLICT"
        assert "已存在" in body["error"]

        # 原行零变化：类型仍 internal、无密钥行、白名单不变
        resp = client.get(f"{PREFIX}/event-sources", headers=_auth(owner))
        mine = next(i for i in resp.json()["data"]["items"] if i["id"] == original_id)
        assert mine["source_type"] == "internal"
        assert mine["keys"] == []
        assert mine["allowed_event_types"] == ["example.order_completed"]

    def test_create_duplicate_ref_same_user_409(self, client, tenant, owner):
        """同用户重复创建同 ref → 409（纯 INSERT，无 upsert 复用）"""
        ref = f"hook-{uuid.uuid4().hex[:8]}"
        r1 = client.post(
            f"{PREFIX}/event-sources", headers=_auth(owner),
            json={"source_ref": ref, "source_type": "webhook"},
        )
        assert r1.status_code == 200
        r2 = client.post(
            f"{PREFIX}/event-sources", headers=_auth(owner),
            json={"source_ref": ref, "source_type": "webhook"},
        )
        assert r2.status_code == 409
        assert r2.json()["code"] == "SOURCE_REF_CONFLICT"
        # 源仍只有一条、密钥仍只有 v1 一行（第二次创建未注入新密钥）
        resp = client.get(f"{PREFIX}/event-sources", headers=_auth(owner))
        rows = [i for i in resp.json()["data"]["items"] if i["source_ref"] == ref]
        assert len(rows) == 1
        assert [k["key_version"] for k in rows[0]["keys"]] == [1]

    def test_create_distinct_ref_unaffected(self, client, tenant, owner):
        """正常不同 ref 创建不受冲突改动影响（200 + secret 一次性返回）"""
        resp = client.post(
            f"{PREFIX}/event-sources", headers=_auth(owner),
            json={"source_ref": f"hook-{uuid.uuid4().hex[:8]}", "source_type": "webhook"},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["secret"]

    def test_unauthenticated_401(self, client, tenant, owner):
        resp = client.get(f"{PREFIX}/event-sources")
        assert resp.status_code == 401

    def test_cross_tenant_hidden(self, client, tenant, owner):
        """跨租户源不可见（列表只回本租户；直接猜 ID 走 rotate 404/403）"""
        from src.api.auth import generate_token
        from src.db.models import UserDB
        from src.saas.db.tenant_db import TenantDB

        code = f"W{uuid.uuid4().hex[:6].upper()}"
        other = TenantDB.create(
            company_name=f"他租户-{code}", tenant_code=code,
            contact_name="t", contact_phone="13900000000",
        )
        assert other, "他租户创建失败"
        other_user = UserDB.create(
            phone=f"196{uuid.uuid4().int % 10**8:08d}",
            username="他租户用户", tenant_id=other["tenant_id"],
        )
        try:
            created = _create_webhook_source(client, owner)
            token = generate_token(other_user["user_id"])
            resp = client.get(
                f"{PREFIX}/event-sources",
                headers={"Authorization": f"Bearer {token}"},
            )
            ids = [i["id"] for i in resp.json()["data"]["items"]]
            assert created["source"]["id"] not in ids
        finally:
            from src.db.database import get_db_connection

            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "DELETE FROM weixin_marketing_webhook_nonces WHERE source_id IN "
                    "(SELECT id FROM desktop_automation_event_sources WHERE tenant_id = %s)",
                    (other["tenant_id"],),
                )
                cur.execute(
                    "DELETE FROM desktop_automation_event_sources WHERE tenant_id = %s",
                    (other["tenant_id"],),
                )
                cur.execute(
                    "DELETE FROM tokens WHERE user_id = %s", (other_user["user_id"],)
                )
                cur.execute("DELETE FROM users WHERE user_id = %s", (other_user["user_id"],))
                conn.commit()
            TenantDB.delete(other["tenant_id"])


class TestExampleOrdersApi:
    def test_create_and_complete_flow(self, client, tenant, owner):
        create = client.post(
            f"{PREFIX}/example-orders", headers=_auth(owner), json={"label": "订单A"},
        )
        assert create.status_code == 200, create.text
        order_id = create.json()["data"]["order_id"]

        detail = client.get(
            f"{PREFIX}/example-orders/{order_id}", headers=_auth(owner)
        )
        assert detail.status_code == 200
        assert detail.json()["data"]["status"] == "pending"

        complete = client.post(
            f"{PREFIX}/example-orders/{order_id}/complete", headers=_auth(owner)
        )
        assert complete.status_code == 200
        assert complete.json()["data"]["status"] == "completed"
        assert complete.json()["data"]["enqueued"] is True

        again = client.post(
            f"{PREFIX}/example-orders/{order_id}/complete", headers=_auth(owner)
        )
        assert again.json()["data"]["enqueued"] is False

    def test_unknown_order_404(self, client, tenant, owner):
        resp = client.post(
            f"{PREFIX}/example-orders/{uuid.uuid4()}/complete", headers=_auth(owner)
        )
        assert resp.status_code == 404
