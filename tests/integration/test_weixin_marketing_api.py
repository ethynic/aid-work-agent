"""weixin_marketing API 集成测试（真实 DB + TestClient，不可用时 skip）

覆盖（R46 API 契约）：
- automations CRUD 正常流：创建草稿/详情/列表（keyword/status/trigger_type/分页）/
  PUT draft（If-Match 与 expected_version 一致性、CAS 409）；
- validate：静态校验结果 + 未来触发预览，且无发送副作用（occurrence/run/invocation 零行）；
- publish / pause / resume / archive：版本 CAS、paused 发布 409（提示 resume）、
  适配器未注册 503；
- run：202 + occurrence 接纳；Idempotency-Key 三态（同 key 同 payload 幂等重放 /
  同 key 异 payload 409 / 无 key 正常）；
- runs 列表/详情（逐条 delivery 脱敏——正文不出现，仅引用/摘要/证据引用）/ cancel 202
  （首诊 changed=true、终态后 changed=false）；
- deliveries resolve / retry（confirm 语义 422、成功建新 attempt）；
- 错误码矩阵抽查：401/404（跨租户与非属主统一）/409/422/503；
- 测试后物理清理并核实零残留（DA 底座表 + weixin 业务表 + 幂等键表 + 租户/用户）。
"""

import json
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

PREFIX = "/api/weixin-marketing"

ALL_CLEANUP_TABLES = (
    # 底座表（依赖序，照 tests/unit/desktop_automation/conftest.py）
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
    # weixin 业务表
    "bs_weixin_marketing_audit_events",
    "bs_weixin_marketing_content_blocks",
    "bs_weixin_marketing_revisions",
    "bs_weixin_marketing_automations",
    "bs_weixin_marketing_group_bindings",
    "bs_weixin_marketing_account_bindings",
    "bs_weixin_marketing_assets",
    # API 层幂等键表（本工作包交付）
    "weixin_marketing_idempotency_keys",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _auth(user) -> dict:
    return {"Authorization": f"Bearer {user['token']}"}


def _count(table: str, tenant_id: str) -> int:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s", (tenant_id,))
        return int(cur.fetchone()["c"])


def _create_binding(tenant_id: str, user_id: str, *, state: str = "complete") -> str:
    """直建一对 account/group 绑定（verify 链路在 P3，测试直接落目标态）"""
    from src.db.database import get_db_connection

    account_id, group_id = str(uuid.uuid4()), str(uuid.uuid4())
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO bs_weixin_marketing_account_bindings
                (id, tenant_id, user_id, device_id, account_anchor_ref, session_epoch, status)
            VALUES (%s, %s, %s, %s, 'anchor-it', 1, 'active')
            """,
            (account_id, tenant_id, user_id, str(uuid.uuid4())),
        )
        cur.execute(
            """
            INSERT INTO bs_weixin_marketing_group_bindings
                (id, tenant_id, user_id, device_id, account_binding_id, label,
                 identity_evidence_ref, identity_version, state, verified_at)
            VALUES (%s, %s, %s, %s, %s, '集成测试群', 'ev-it', '1', %s, NOW())
            """,
            (group_id, tenant_id, user_id, str(uuid.uuid4()), account_id, state),
        )
        conn.commit()
    return group_id


def _create_payload(group_binding_id: str, *, trigger=None, blocks=None, name="API 集成测试"):
    if trigger is None:
        trigger = {
            "type": "once",
            "run_at": (utcnow() + timedelta(hours=2)).isoformat(),
            "timezone": "UTC",
        }
    if blocks is None:
        blocks = [
            {"type": "text", "text_content": "集成第一条内容"},
            {"type": "link", "url": "https://example.com/it"},
        ]
    return {"name": name, "trigger": trigger, "blocks": blocks,
            "group_binding_id": group_binding_id}


def _api_create(client, user, group_binding_id, **kw):
    resp = client.post(
        f"{PREFIX}/automations", headers=_auth(user),
        json=_create_payload(group_binding_id, **kw),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _api_publish(client, user, automation_id: str, version: int):
    resp = client.post(
        f"{PREFIX}/automations/{automation_id}/publish",
        headers=_auth(user), json={"expected_version": version},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _insert_pending_idempotency_row(
    tenant_id: str, user_id: str, route: str, key: str, digest: str,
    *, stale_minutes: int = None,
):
    """直插幂等键占位行（模拟崩溃/闪断残留；stale_minutes 回溯 created_at）"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        if stale_minutes is not None:
            cur.execute(
                """
                INSERT INTO weixin_marketing_idempotency_keys
                    (tenant_id, user_id, route, idempotency_key, request_digest,
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s,
                        NOW() - (%s * INTERVAL '1 minute'),
                        NOW() - (%s * INTERVAL '1 minute'))
                """,
                (tenant_id, user_id, route, key, digest, stale_minutes, stale_minutes),
            )
        else:
            cur.execute(
                """
                INSERT INTO weixin_marketing_idempotency_keys
                    (tenant_id, user_id, route, idempotency_key, request_digest)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (tenant_id, user_id, route, key, digest),
            )
        conn.commit()


# ==================== fixtures ====================


@pytest.fixture(scope="module")
def wxm_adapter():
    """注册受信 weixin 适配器（enabled=true 测试配置；测后注销）"""
    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from src.weixin_marketing.adapters import WeixinFixedContentAdapter
    from src.weixin_marketing.config import WeixinQuotaConfig, get_weixin_marketing_config

    config = replace(
        get_weixin_marketing_config(),
        enabled=True,
        time_triggers_enabled=True,
        max_blocks=20,
        min_interval_seconds=300,
        quotas=WeixinQuotaConfig(window_seconds=3600, tenant_limit=100, task_limit=30,
                                 target_limit=10, account_limit=60),
    )
    instance = WeixinFixedContentAdapter(config=config)
    TrustedAdapterRegistry.register(instance)
    yield instance
    TrustedAdapterRegistry.unregister(instance.scenario_key)


@pytest.fixture(scope="module")
def client():
    """TestClient + 测试态挂载 router（main.py 挂载由总工程师统一接线，见交接说明）"""
    from src.main import app
    from src.weixin_marketing import api as wxm_api

    if not any(getattr(r, "path", "").startswith(PREFIX) for r in app.routes):
        app.include_router(wxm_api.router)
    return TestClient(app)


@pytest.fixture(scope="module")
def tenants():
    from src.saas.db.tenant_db import TenantDB

    created = []
    for _ in range(2):
        code = f"W{uuid.uuid4().hex[:6].upper()}"
        tenant = TenantDB.create(
            company_name=f"微信营销测试-{code}",
            tenant_code=code,
            contact_name="测试",
            contact_phone="13800000000",
        )
        if not tenant:
            pytest.skip("无法创建测试租户（DB 不可用）")
        created.append(tenant["tenant_id"])

    yield created

    # 物理清理 + 零残留核实（失败即 fail，不静默）
    from src.db.database import get_db_connection

    problems = []
    for tenant_id in created:
        TenantDB.delete(tenant_id)
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
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
                        f"SELECT COUNT(*) AS c FROM {table} WHERE tenant_id = %s",
                        (tenant_id,),
                    )
                    if int(cur.fetchone()["c"]):
                        problems.append(f"{table} tenant={tenant_id} 残留")
        except Exception as e:  # noqa: BLE001
            problems.append(f"tenant={tenant_id} 清理异常: {e}")
    assert not problems, f"测试数据残留: {problems}"


@pytest.fixture(scope="module")
def users(tenants):
    from src.api.auth import generate_token
    from src.db.models import UserDB

    result = []
    for i, tenant_id in enumerate(tenants):
        phone = f"199{uuid.uuid4().int % 10**8:08d}"
        user = UserDB.create(phone=phone, username=f"微信营销测试用户{i}", tenant_id=tenant_id)
        if not user:
            pytest.skip("无法创建测试用户（DB 不可用）")
        token = generate_token(user["user_id"])
        result.append({"user_id": user["user_id"], "tenant_id": tenant_id, "token": token})
    return result


# ==================== 执行链驱动（底座直调，API 面为被测对象）====================


def _claim_and_prepare(tenant_id: str, revision_id: str, device_id: str, occurrence_id: str = None):
    from src.desktop_automation import executor
    from src.weixin_marketing.service import WeixinMarketingService

    revision_config = WeixinMarketingService().load_revision_config(tenant_id, revision_id)
    prepared = executor.claim_and_prepare_run(
        revision_config=revision_config, device_id=device_id,
        tenant_id=tenant_id, lease_seconds=300,
    )
    assert prepared and prepared["prepared"] is True, prepared
    if occurrence_id is not None:
        # 防串跑：领取到的必须是自己刚触发的 run（claim 按 tenant 领取最早 pending）
        assert str(prepared["run"]["occurrence_id"]) == str(occurrence_id), prepared["run"]
    return prepared


def _claim_and_start(tenant_id: str, invocation_id: str, device_id: str) -> str:
    from src.local_tools import repository
    from src.local_tools.security import generate_claim_token, sha256_hex

    claim_token = generate_claim_token()
    claimed = repository.claim_next(device_id, tenant_id, sha256_hex(claim_token), 300)
    assert claimed is not None and str(claimed["id"]) == invocation_id
    started = repository.mark_started(invocation_id, tenant_id, sha256_hex(claim_token))
    assert started is not None and started["state"] == "running"
    return claim_token


def _authorize_and_finish(
    tenant_id, device_id, invocation_id, claim_token, request_id,
    payload_hash, *, effect="applied", phase="verified", safe_to_retry=None,
):
    from src.local_tools import permits
    from src.local_tools.operation_result import apply_operation_result
    from src.local_tools.security import sha256_hex

    permit = permits.write_authorize(
        tenant_id=tenant_id, device_id=device_id, invocation_id=invocation_id,
        claim_token_hash=sha256_hex(claim_token), request_id=request_id,
        target_version="iv-1-se-1", payload_hash=payload_hash,
    )
    evidence_ref = None
    if effect == "applied" and phase == "verified":
        evidence_ref = f"weixin-evidence:{request_id}:1"
    result = apply_operation_result(
        tenant_id=tenant_id, device_id=device_id, invocation_id=invocation_id,
        claim_token_hash=sha256_hex(claim_token), request_id=request_id,
        effect=effect, phase=phase, evidence_ref=evidence_ref,
        safe_to_retry=safe_to_retry,
        permit_id=permit["permit_id"], permit_token=permit["permit_token"],
    )
    return permit, result


# ==================== automations CRUD ====================


class TestAutomationsCrud:
    def test_create_get_list_and_filters(self, client, users):
        user = users[0]
        tenant_id = user["tenant_id"]
        group_a = _create_binding(tenant_id, user["user_id"])
        group_b = _create_binding(tenant_id, user["user_id"])

        detail = _api_create(client, user, group_a, name="一次性任务")
        automation_id = str(detail["automation"]["id"])
        assert detail["automation"]["status"] == "draft"
        assert detail["automation"]["version"] == 1

        interval = {
            "type": "interval", "start_at": (utcnow() + timedelta(hours=1)).isoformat(),
            "interval_seconds": 3600, "timezone": "UTC",
        }
        detail_b = _api_create(client, user, group_b, trigger=interval, name="间隔任务")
        automation_b_id = str(detail_b["automation"]["id"])

        # 详情：配置 + 版本 + 草稿触发器
        resp = client.get(f"{PREFIX}/automations/{automation_id}", headers=_auth(user))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["automation"]["id"] == automation_id
        assert data["draft_trigger"]["type"] == "once"
        assert len(data["revisions"]) == 1
        assert data["recent_runs"] == []

        # 列表：keyword / status / trigger_type / 分页
        resp = client.get(
            f"{PREFIX}/automations", headers=_auth(user),
            params={"keyword": "一次性"},
        )
        assert resp.status_code == 200
        listed = resp.json()["data"]
        assert listed["total"] == 1
        assert listed["items"][0]["id"] == automation_id
        assert listed["items"][0]["name"] == "一次性任务"  # created_at DESC 字段齐备

        resp = client.get(
            f"{PREFIX}/automations", headers=_auth(user),
            params={"status": "draft", "trigger_type": "interval"},
        )
        assert resp.status_code == 200
        listed = resp.json()["data"]
        assert listed["total"] == 1
        assert listed["items"][0]["id"] == automation_b_id

        resp = client.get(
            f"{PREFIX}/automations", headers=_auth(user), params={"page_size": 1, "page": 2},
        )
        assert resp.status_code == 200
        listed = resp.json()["data"]
        assert listed["page_size"] == 1 and listed["page"] == 2
        assert len(listed["items"]) == 1 and listed["total"] == 2

    def test_create_semantic_422(self, client, users):
        user = users[0]
        group_id = _create_binding(user["tenant_id"], user["user_id"])
        url, headers = f"{PREFIX}/automations", _auth(user)

        cases = [
            # 换行正文（语义校验）
            {"name": "x", "trigger": {"type": "once", "run_at": "2030-01-01T00:00:00Z"},
             "blocks": [{"type": "text", "text_content": "第一行\n第二行"}],
             "group_binding_id": group_id},
            # 非 UUID 绑定
            {"name": "x", "trigger": {"type": "once", "run_at": "2030-01-01T00:00:00Z"},
             "blocks": [{"type": "text", "text_content": "内容"}],
             "group_binding_id": "not-a-uuid"},
            # 缺 name
            {"trigger": {"type": "once", "run_at": "2030-01-01T00:00:00Z"},
             "blocks": [{"type": "text", "text_content": "内容"}],
             "group_binding_id": group_id},
        ]
        for body in cases:
            resp = client.post(url, headers=headers, json=body)
            assert resp.status_code == 422, (resp.status_code, resp.text)
            body_json = resp.json()
            assert body_json["success"] is False
            assert body_json["code"] == "VALIDATION_FAILED"
            assert body_json["field_errors"], body_json

    def test_list_filter_validation_422(self, client, users):
        user = users[0]
        for params in ({"status": "bogus"}, {"trigger_type": "bogus"},
                       {"page": 0}, {"page_size": 101}):
            resp = client.get(f"{PREFIX}/automations", headers=_auth(user), params=params)
            assert resp.status_code == 422, params
            assert resp.json()["code"] == "VALIDATION_FAILED"

    def test_update_draft_if_match_and_cas_409(self, client, users):
        user = users[0]
        group_id = _create_binding(user["tenant_id"], user["user_id"])
        detail = _api_create(client, user, group_id)
        automation_id = str(detail["automation"]["id"])
        url, headers = f"{PREFIX}/automations/{automation_id}/draft", _auth(user)

        # 正常更新：expected_version=1 → version=2
        resp = client.put(url, headers=headers, json={
            "expected_version": 1,
            "blocks": [{"type": "text", "text_content": "更新后的内容"}],
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["automation"]["version"] == 2

        # CAS 409：stale version
        resp = client.put(url, headers=headers, json={"expected_version": 1})
        assert resp.status_code == 409
        assert resp.json()["code"] == "CONFLICT"

        # If-Match 与 expected_version 一致 → 200
        resp = client.put(url, headers={**headers, "If-Match": '"2"'},
                          json={"expected_version": 2, "name": "改名任务"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["automation"]["name"] == "改名任务"

        # If-Match 与 body 不一致 → 422
        resp = client.put(url, headers={**headers, "If-Match": '"9"'},
                          json={"expected_version": 3})
        assert resp.status_code == 422
        assert resp.json()["code"] == "VALIDATION_FAILED"

        # If-Match 不可解析 → 422
        resp = client.put(url, headers={**headers, "If-Match": "abc"},
                          json={"expected_version": 3})
        assert resp.status_code == 422

    def test_cross_tenant_and_bad_uuid_404(self, client, users):
        user_a, user_b = users
        group_id = _create_binding(user_a["tenant_id"], user_a["user_id"])
        detail = _api_create(client, user_a, group_id)
        automation_id = str(detail["automation"]["id"])

        # 跨租户读/写统一 404（不区分存在性）
        resp = client.get(f"{PREFIX}/automations/{automation_id}", headers=_auth(user_b))
        assert resp.status_code == 404
        assert resp.json()["code"] == "NOT_FOUND"
        resp = client.put(
            f"{PREFIX}/automations/{automation_id}/draft", headers=_auth(user_b),
            json={"expected_version": 1},
        )
        assert resp.status_code == 404

        # 非法 UUID 路径 → 404（不触发 DB DataError 500）
        resp = client.get(f"{PREFIX}/automations/not-a-uuid", headers=_auth(user_a))
        assert resp.status_code == 404

        # 未登录 → 401
        resp = client.get(f"{PREFIX}/automations")
        assert resp.status_code == 401


# ==================== validate（无发送副作用）====================


class TestValidate:
    def test_validate_reports_and_no_send_side_effects(self, client, users, wxm_adapter):
        user = users[0]
        tenant_id = user["tenant_id"]
        group_id = _create_binding(tenant_id, user["user_id"])
        interval = {
            "type": "interval", "start_at": (utcnow() + timedelta(hours=1)).isoformat(),
            "interval_seconds": 3600, "timezone": "UTC",
        }
        detail = _api_create(client, user, group_id, trigger=interval)
        automation_id = str(detail["automation"]["id"])

        resp = client.post(f"{PREFIX}/automations/{automation_id}/validate", headers=_auth(user))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["ok"] is True
        assert data["errors"] == []
        assert len(data["next_fires"]) == 5  # 未来 5 次预览

        # 无发送副作用：occurrence/run/invocation 零行
        for table in ("desktop_automation_occurrences", "desktop_automation_runs",
                      "local_tool_invocations"):
            assert _count(table, tenant_id) == 0, table

    def test_validate_binding_not_complete(self, client, users, wxm_adapter):
        user = users[0]
        pending_group = _create_binding(user["tenant_id"], user["user_id"], state="pending")
        detail = _api_create(client, user, pending_group)
        automation_id = str(detail["automation"]["id"])

        resp = client.post(f"{PREFIX}/automations/{automation_id}/validate", headers=_auth(user))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["ok"] is False
        assert any(e["field"] == "group_binding_id" for e in data["errors"])

    def test_validate_unknown_404(self, client, users):
        resp = client.post(
            f"{PREFIX}/automations/{uuid.uuid4()}/validate", headers=_auth(users[0])
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "NOT_FOUND"


# ==================== publish / pause / resume / archive ====================


class TestPublishLifecycle:
    def test_publish_pause_resume_archive(self, client, users, wxm_adapter):
        user = users[0]
        group_id = _create_binding(user["tenant_id"], user["user_id"])
        detail = _api_create(client, user, group_id)
        automation_id = str(detail["automation"]["id"])

        published = _api_publish(client, user, automation_id, version=1)
        assert published["revision_id"]
        assert published["authorization_epoch"] == 0  # 首发为 0；pause/resume 才递增
        version = published["version"]

        resp = client.get(f"{PREFIX}/automations/{automation_id}", headers=_auth(user))
        assert resp.json()["data"]["automation"]["status"] == "active"

        # 重复发布同 revision → 409（仅草稿可发布）
        resp = client.post(
            f"{PREFIX}/automations/{automation_id}/publish",
            headers=_auth(user), json={"expected_version": version},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "CONFLICT"

        # pause → 200；paused 下 publish → 409 且提示 resume
        resp = client.post(
            f"{PREFIX}/automations/{automation_id}/pause",
            headers=_auth(user), json={"expected_version": version, "reason": "测试暂停"},
        )
        assert resp.status_code == 200, resp.text
        paused = resp.json()["data"]
        assert paused["status"] == "paused" and paused["version"] == version + 1
        assert paused["authorization_epoch"] == 1  # 暂停递增授权 epoch（撤销许可）

        resp = client.post(
            f"{PREFIX}/automations/{automation_id}/publish",
            headers=_auth(user), json={"expected_version": version + 1},
        )
        assert resp.status_code == 409
        assert "resume" in resp.json()["error"]

        # resume → active；archive → archived；归档后不可再发布/暂停
        resp = client.post(
            f"{PREFIX}/automations/{automation_id}/resume",
            headers=_auth(user), json={"expected_version": version + 1},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "active"

        resp = client.post(
            f"{PREFIX}/automations/{automation_id}/archive",
            headers=_auth(user), json={"expected_version": version + 2},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "archived"

        resp = client.post(
            f"{PREFIX}/automations/{automation_id}/publish",
            headers=_auth(user), json={"expected_version": version + 3},
        )
        assert resp.status_code == 409
        resp = client.post(
            f"{PREFIX}/automations/{automation_id}/pause",
            headers=_auth(user), json={"expected_version": version + 3},
        )
        assert resp.status_code == 409

    def test_publish_stale_version_409(self, client, users, wxm_adapter):
        user = users[0]
        group_id = _create_binding(user["tenant_id"], user["user_id"])
        detail = _api_create(client, user, group_id)
        automation_id = str(detail["automation"]["id"])
        resp = client.post(
            f"{PREFIX}/automations/{automation_id}/publish",
            headers=_auth(user), json={"expected_version": 99},
        )
        assert resp.status_code == 409
        assert "版本冲突" in resp.json()["error"]

    def test_publish_adapter_unregistered_503(self, client, users, wxm_adapter):
        from src.desktop_automation.adapters import TrustedAdapterRegistry

        user = users[1]
        group_id = _create_binding(user["tenant_id"], user["user_id"])
        detail = _api_create(client, user, group_id)
        automation_id = str(detail["automation"]["id"])
        TrustedAdapterRegistry.unregister(wxm_adapter.scenario_key)
        try:
            resp = client.post(
                f"{PREFIX}/automations/{automation_id}/publish",
                headers=_auth(user), json={"expected_version": 1},
            )
            assert resp.status_code == 503, resp.text
            assert resp.json()["code"] == "ADAPTER_UNREGISTERED"
        finally:
            TrustedAdapterRegistry.register(wxm_adapter)


# ==================== 手动 run + runs 查询/取消 ====================


class TestManualRunAndRuns:
    def _published_automation(self, client, user, *, blocks=None):
        group_id = _create_binding(user["tenant_id"], user["user_id"])
        detail = _api_create(client, user, group_id, blocks=blocks)
        automation_id = str(detail["automation"]["id"])
        published = _api_publish(client, user, automation_id, version=1)
        return automation_id, published["revision_id"]

    def test_run_202_idempotency_three_states(self, client, users, wxm_adapter):
        user = users[0]
        tenant_id = user["tenant_id"]
        automation_id, _ = self._published_automation(client, user)
        url, headers = f"{PREFIX}/automations/{automation_id}/run", _auth(user)
        key = {"Idempotency-Key": f"it-run-{uuid.uuid4().hex[:12]}"}

        # 同 key 同 payload：幂等重放（同一 occurrence/run，DB 只有一行）
        first = client.post(url, headers={**headers, **key}, json={})
        assert first.status_code == 202, first.text
        occurrence_id = first.json()["data"]["occurrence_id"]
        assert occurrence_id
        assert first.json()["data"]["run_id"]  # run 在接纳事务内创建（202 返回 run_id）

        replay = client.post(url, headers={**headers, **key}, json={})
        assert replay.status_code == 202
        assert replay.json()["data"]["occurrence_id"] == occurrence_id
        assert replay.json() == first.json()
        assert _count("desktop_automation_occurrences", tenant_id) == 1

        # 同 key 异 payload → 409
        conflict = client.post(url, headers={**headers, **key}, json={"request_id": "other"})
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"

        # 无 key 正常执行（新 occurrence）
        plain = client.post(url, headers=headers, json={})
        assert plain.status_code == 202
        assert plain.json()["data"]["occurrence_id"] != occurrence_id
        assert _count("desktop_automation_occurrences", tenant_id) == 2

        # 幂等键长度非法 → 422
        bad = client.post(url, headers={**headers, "Idempotency-Key": "short"}, json={})
        assert bad.status_code == 422
        assert bad.json()["code"] == "IDEMPOTENCY_KEY_INVALID"

        # 收敛本用例的两个 run 至终态（防 pending 残留被后续用例的 claim 领走）
        from src.weixin_marketing.service import WeixinMarketingService

        for body in (first.json(), plain.json()):
            WeixinMarketingService().cancel_run(
                tenant_id, body["data"]["run_id"], user["user_id"]
            )

    def test_run_not_active_409(self, client, users, wxm_adapter):
        user = users[0]
        group_id = _create_binding(user["tenant_id"], user["user_id"])
        detail = _api_create(client, user, group_id)  # draft 未发布
        automation_id = str(detail["automation"]["id"])
        resp = client.post(f"{PREFIX}/automations/{automation_id}/run", headers=_auth(user), json={})
        assert resp.status_code == 409
        assert resp.json()["code"] == "CONFLICT"

    def test_run_tenant_allowlist_denied(self, client, users, wxm_adapter, monkeypatch):
        """CR-P1-2：allowlist 非空且租户不在列 → 409 TENANT_NOT_ALLOWED，
        零副作用（不接纳 occurrence）；门控移除后恢复正常"""
        from src.weixin_marketing import service as wxm_service
        from src.weixin_marketing.config import get_weixin_marketing_config

        user = users[0]
        automation_id, _ = self._published_automation(client, user)
        deny_cfg = replace(
            get_weixin_marketing_config(), tenant_allowlist=["tenant_not_in_list"]
        )
        monkeypatch.setattr(wxm_service, "get_weixin_marketing_config", lambda: deny_cfg)
        try:
            resp = client.post(
                f"{PREFIX}/automations/{automation_id}/run",
                headers=_auth(user), json={},
            )
            assert resp.status_code == 409, resp.text
            assert resp.json()["code"] == "TENANT_NOT_ALLOWED"
            # 零副作用：本自动化零 occurrence（按 task_ref 计数，防前序用例污染）
            from src.db.database import get_db_connection

            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT COUNT(*) AS c FROM desktop_automation_occurrences "
                    "WHERE tenant_id = %s AND task_ref = %s",
                    (user["tenant_id"], automation_id),
                )
                assert int(cur.fetchone()["c"]) == 0
        finally:
            monkeypatch.undo()

        resp = client.post(
            f"{PREFIX}/automations/{automation_id}/run", headers=_auth(user), json={},
        )
        assert resp.status_code == 202
        # 收敛本用例 run 至终态（防 pending 残留被后续用例的 claim 领走）
        from src.weixin_marketing.service import WeixinMarketingService

        WeixinMarketingService().cancel_run(
            user["tenant_id"], resp.json()["data"]["run_id"], user["user_id"]
        )

    def test_runs_list_detail_cancel(self, client, users, wxm_adapter):
        user = users[0]
        tenant_id = user["tenant_id"]
        automation_id, revision_id = self._published_automation(client, user)

        resp = client.post(f"{PREFIX}/automations/{automation_id}/run", headers=_auth(user), json={})
        assert resp.status_code == 202
        occurrence_id = resp.json()["data"]["occurrence_id"]

        # 执行驱动：occurrence → run + deliveries
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(tenant_id, revision_id, device_id, occurrence_id)
        run_id = str(prepared["run"]["id"])

        # 列表（automation_id 过滤 + state 过滤）
        resp = client.get(
            f"{PREFIX}/runs", headers=_auth(user),
            params={"automation_id": automation_id},
        )
        assert resp.status_code == 200
        listed = resp.json()["data"]
        assert listed["total"] == 1
        assert str(listed["items"][0]["id"]) == run_id
        resp = client.get(
            f"{PREFIX}/runs", headers=_auth(user), params={"state": "running"},
        )
        assert resp.json()["data"]["total"] == 1
        resp = client.get(
            f"{PREFIX}/runs", headers=_auth(user), params={"automation_id": "bad-uuid"},
        )
        assert resp.status_code == 422
        # P2-5：state 枚举校验（非法 422，与 automations 列表过滤一致）
        resp = client.get(
            f"{PREFIX}/runs", headers=_auth(user), params={"state": "bogus"},
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "VALIDATION_FAILED"
        assert any(e["field"] == "state" for e in resp.json()["field_errors"])

        # 详情：逐条 delivery 脱敏（无正文）+ 证据引用槽位
        resp = client.get(f"{PREFIX}/runs/{run_id}", headers=_auth(user))
        assert resp.status_code == 200, resp.text
        detail = resp.json()["data"]
        assert str(detail["run"]["id"]) == run_id
        assert len(detail["deliveries"]) == 2
        first_delivery = detail["deliveries"][0]
        assert first_delivery["payload_ref"].startswith("da:weixin.fixed_content.v1:")
        assert first_delivery["payload_hash"]
        assert first_delivery["latest_attempt"] is None
        import json as _json

        assert "集成第一条内容" not in _json.dumps(detail)  # 正文不泄露
        assert "example.com/it" not in _json.dumps(detail)

        # cancel：202 + changed；终态后再 cancel 幂等 changed=false
        resp = client.post(f"{PREFIX}/runs/{run_id}/cancel", headers=_auth(user))
        assert resp.status_code == 202, resp.text
        cancelled = resp.json()["data"]
        assert cancelled["changed"] is True
        assert cancelled["state"] == "cancelled"
        resp = client.post(f"{PREFIX}/runs/{run_id}/cancel", headers=_auth(user))
        assert resp.status_code == 202
        assert resp.json()["data"]["changed"] is False

        # 跨租户 run 详情 → 404
        resp = client.get(f"{PREFIX}/runs/{run_id}", headers=_auth(users[1]))
        assert resp.status_code == 404
        # 非法 UUID → 404
        resp = client.get(f"{PREFIX}/runs/not-a-uuid", headers=_auth(user))
        assert resp.status_code == 404


# ==================== deliveries resolve / retry ====================


class TestDeliveriesResolveRetry:
    def _drive_to_failed(self, client, user, wxm_adapter):
        tenant_id = user["tenant_id"]
        group_id = _create_binding(tenant_id, user["user_id"])
        detail = _api_create(
            client, user, group_id,
            blocks=[{"type": "text", "text_content": "唯一待失败内容"}],
        )
        automation_id = str(detail["automation"]["id"])
        published = _api_publish(client, user, automation_id, version=1)
        revision_id = published["revision_id"]

        resp = client.post(f"{PREFIX}/automations/{automation_id}/run", headers=_auth(user), json={})
        assert resp.status_code == 202
        occurrence_id = resp.json()["data"]["occurrence_id"]
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(tenant_id, revision_id, device_id, occurrence_id)
        from src.desktop_automation import executor

        stepped = executor.execute_next_delivery(prepared["run"])
        assert stepped is not None
        token = _claim_and_start(tenant_id, stepped["invocation_id"], device_id)
        _authorize_and_finish(
            tenant_id, device_id, stepped["invocation_id"], token, stepped["request_id"],
            stepped["delivery"]["payload_hash"], effect="none", phase="prepared",
            safe_to_retry=True,
        )
        return str(stepped["delivery"]["id"]), str(prepared["run"]["id"])

    def test_resolve_and_retry(self, client, users, wxm_adapter):
        user = users[0]
        tenant_id = user["tenant_id"]
        delivery_id, run_id = self._drive_to_failed(client, user, wxm_adapter)
        headers = _auth(user)

        # 失败态 + 最新 attempt 摘要（证据引用槽位在，值为空——失败无证据）
        resp = client.get(f"{PREFIX}/runs/{run_id}", headers=headers)
        delivery = resp.json()["data"]["deliveries"][0]
        assert delivery["state"] == "failed"
        assert delivery["latest_attempt"]["attempt_no"] == 1
        assert delivery["latest_attempt"]["evidence_ref"] is None

        # 人工结论：200，机器判定不被覆盖
        resp = client.post(
            f"{PREFIX}/deliveries/{delivery_id}/resolve", headers=headers,
            json={"verdict": "not_delivered", "note": "人工核对未见消息"},
        )
        assert resp.status_code == 200, resp.text
        resolved = resp.json()["data"]
        assert resolved["verdict"] == "not_delivered"
        assert resolved["machine_state"] == "failed"

        resp = client.get(f"{PREFIX}/runs/{run_id}", headers=headers)
        assert resp.json()["data"]["deliveries"][0]["state"] == "failed"

        # 重试：未显式 confirm → 422；confirm → 200 新 attempt（predecessor 链）
        resp = client.post(f"{PREFIX}/deliveries/{delivery_id}/retry", headers=headers, json={})
        assert resp.status_code == 422
        assert resp.json()["code"] == "VALIDATION_FAILED"

        resp = client.post(
            f"{PREFIX}/deliveries/{delivery_id}/retry", headers=headers,
            json={"confirm": True},
        )
        assert resp.status_code == 200, resp.text
        retried = resp.json()["data"]
        assert retried["attempt_no"] == 2
        assert retried["predecessor_attempt_id"]

        # 审计留痕（resolve + retry）
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT action FROM bs_weixin_marketing_audit_events "
                "WHERE tenant_id = %s AND action IN ('delivery_resolved', 'delivery_retried')",
                (tenant_id,),
            )
            actions = {row["action"] for row in cur.fetchall()}
        assert actions == {"delivery_resolved", "delivery_retried"}

    def test_retry_succeeded_delivery_409(self, client, users, wxm_adapter):
        user = users[0]
        tenant_id = user["tenant_id"]
        group_id = _create_binding(tenant_id, user["user_id"])
        detail = _api_create(
            client, user, group_id,
            blocks=[{"type": "text", "text_content": "唯一成功内容"}],
        )
        automation_id = str(detail["automation"]["id"])
        published = _api_publish(client, user, automation_id, version=1)

        resp = client.post(f"{PREFIX}/automations/{automation_id}/run", headers=_auth(user), json={})
        assert resp.status_code == 202
        occurrence_id = resp.json()["data"]["occurrence_id"]
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(
            tenant_id, published["revision_id"], device_id, occurrence_id
        )
        from src.desktop_automation import executor

        stepped = executor.execute_next_delivery(prepared["run"])
        token = _claim_and_start(tenant_id, stepped["invocation_id"], device_id)
        _, result = _authorize_and_finish(
            tenant_id, device_id, stepped["invocation_id"], token, stepped["request_id"],
            stepped["delivery"]["payload_hash"],
        )
        assert result["state"] == "succeeded"

        resp = client.post(
            f"{PREFIX}/deliveries/{stepped['delivery']['id']}/retry",
            headers=_auth(user), json={"confirm": True},
        )
        assert resp.status_code == 409
        assert resp.json()["code"] == "CONFLICT"

    def test_resolve_cross_tenant_404(self, client, users, wxm_adapter):
        user = users[0]
        delivery_id, _ = self._drive_to_failed(client, user, wxm_adapter)
        resp = client.post(
            f"{PREFIX}/deliveries/{delivery_id}/resolve", headers=_auth(users[1]),
            json={"verdict": "delivered"},
        )
        assert resp.status_code == 404
        assert resp.json()["code"] == "NOT_FOUND"


# ==================== 创建幂等三态 ====================


class TestCreateIdempotency:
    def test_three_states(self, client, users, wxm_adapter):
        user = users[0]
        tenant_id = user["tenant_id"]
        group_id = _create_binding(tenant_id, user["user_id"])
        url, headers = f"{PREFIX}/automations", _auth(user)
        key = {"Idempotency-Key": f"it-create-{uuid.uuid4().hex[:12]}"}
        body = _create_payload(group_id, name="幂等创建任务")

        first = client.post(url, headers={**headers, **key}, json=body)
        assert first.status_code == 200, first.text
        automation_id = first.json()["data"]["automation"]["id"]

        # 同 key 同 payload → 幂等重放（同 ID，DB 只有一行）
        replay = client.post(url, headers={**headers, **key}, json=body)
        assert replay.status_code == 200
        assert replay.json()["data"]["automation"]["id"] == automation_id
        assert replay.json() == first.json()

        resp = client.get(
            f"{PREFIX}/automations", headers=headers, params={"keyword": "幂等创建任务"},
        )
        assert resp.json()["data"]["total"] == 1

        # 同 key 异 payload → 409
        other = _create_payload(group_id, name="另一个任务")
        conflict = client.post(url, headers={**headers, **key}, json=other)
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"

        # 无 key → 正常创建第二个
        plain = client.post(url, headers=headers, json=other)
        assert plain.status_code == 200
        assert plain.json()["data"]["automation"]["id"] != automation_id

    def test_failed_request_releases_key(self, client, users, wxm_adapter):
        """校验失败释放占位：同 key 同 payload 修正后可重试成功"""
        user = users[0]
        group_id = _create_binding(user["tenant_id"], user["user_id"])
        url, headers = f"{PREFIX}/automations", _auth(user)
        key = {"Idempotency-Key": f"it-fix-{uuid.uuid4().hex[:12]}"}

        bad = _create_payload(group_id, name="失败后修复")
        bad["blocks"] = [{"type": "text", "text_content": "带\n换行"}]
        resp = client.post(url, headers={**headers, **key}, json=bad)
        assert resp.status_code == 422

        good = _create_payload(group_id, name="失败后修复")
        resp = client.post(url, headers={**headers, **key}, json=good)
        assert resp.status_code == 200, resp.text


# ==================== 幂等加固（CR-P1-1 / CR-P1-3 / T-P1 / T-P2）====================


class TestIdempotencyHardening:
    def test_same_key_same_body_different_automation_409(self, client, users, wxm_adapter):
        """CR-P1-1：digest 并入请求路径——同 key 同 body 打不同 automation
        不得重放原响应，一律 409；同 key 同路径重放仍幂等"""
        user = users[0]
        g1 = _create_binding(user["tenant_id"], user["user_id"])
        g2 = _create_binding(user["tenant_id"], user["user_id"])
        a1 = _api_create(client, user, g1, name="路径隔离甲")
        a2 = _api_create(client, user, g2, name="路径隔离乙")
        id1, id2 = str(a1["automation"]["id"]), str(a2["automation"]["id"])
        key = f"it-path-{uuid.uuid4().hex[:12]}"
        headers = {**_auth(user), "Idempotency-Key": key}
        body = {"expected_version": 1}

        first = client.post(
            f"{PREFIX}/automations/{id1}/publish", headers=headers, json=body
        )
        assert first.status_code == 200, first.text

        second = client.post(
            f"{PREFIX}/automations/{id2}/publish", headers=headers, json=body
        )
        assert second.status_code == 409, second.text
        assert second.json()["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"
        # 甲的发布响应未被误重放到乙：乙仍草稿
        resp = client.get(f"{PREFIX}/automations/{id2}", headers=_auth(user))
        assert resp.json()["data"]["automation"]["status"] == "draft"

        # 同 key 同路径仍幂等重放
        replay = client.post(
            f"{PREFIX}/automations/{id1}/publish", headers=headers, json=body
        )
        assert replay.status_code == 200
        assert replay.json() == first.json()

    def test_stale_pending_placeholder_taken_over(self, client, users, wxm_adapter):
        """CR-P1-3 + R51：崩溃残留的过期 pending 占位（>10 分钟）且 **digest 匹配**
        才可接管——同 key 同 payload 正常执行（非 409），接管后可正常重放；
        digest 不符（同 key 异 payload 的残留）→ 409 PAYLOAD_CONFLICT，不覆盖执行。"""
        from src.weixin_marketing.api import _ROUTE_CREATE, _digest_payload

        user = users[0]
        tenant_id = user["tenant_id"]
        group_id = _create_binding(tenant_id, user["user_id"])
        body = _create_payload(group_id, name="过期占位接管")  # 固定 body（run_at 不再生）

        # 异 digest 残留（旧 key 曾用于不同 payload 后崩溃）：不得接管
        key = f"it-stale-{uuid.uuid4().hex[:12]}"
        _insert_pending_idempotency_row(
            tenant_id, user["user_id"], _ROUTE_CREATE, key,
            "stale-digest-from-crash", stale_minutes=15,
        )
        conflict = client.post(
            f"{PREFIX}/automations",
            headers={**_auth(user), "Idempotency-Key": key},
            json=body,
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["code"] == "IDEMPOTENCY_PAYLOAD_CONFLICT"
        resp = client.get(
            f"{PREFIX}/automations", headers=_auth(user), params={"keyword": "过期占位接管"},
        )
        assert resp.json()["data"]["total"] == 0  # 未执行

        # 同 digest 残留（崩溃前 digest 已落）：接管执行
        key2 = f"it-stale-{uuid.uuid4().hex[:12]}"
        digest = _digest_payload({"path": f"{PREFIX}/automations", "body": body})
        _insert_pending_idempotency_row(
            tenant_id, user["user_id"], _ROUTE_CREATE, key2, digest, stale_minutes=15,
        )
        taken_over = client.post(
            f"{PREFIX}/automations",
            headers={**_auth(user), "Idempotency-Key": key2},
            json=body,
        )
        assert taken_over.status_code == 200, taken_over.text
        automation_id = taken_over.json()["data"]["automation"]["id"]

        # 接管后（时间戳已刷新）同 key 同 payload 走正常重放
        replay = client.post(
            f"{PREFIX}/automations",
            headers={**_auth(user), "Idempotency-Key": key2},
            json=body,
        )
        assert replay.status_code == 200
        assert replay.json()["data"]["automation"]["id"] == automation_id

    def test_fresh_pending_placeholder_in_progress(self, client, users, wxm_adapter):
        """T-P1：新鲜 pending 占位（崩溃窗口内，response_json NULL）→
        同 key 同 payload → 409 IDEMPOTENCY_IN_PROGRESS，且不执行业务"""
        from src.weixin_marketing.api import _ROUTE_CREATE, _digest_payload

        user = users[0]
        group_id = _create_binding(user["tenant_id"], user["user_id"])
        key = f"it-fresh-{uuid.uuid4().hex[:12]}"
        body = _create_payload(group_id, name="新鲜占位")
        digest = _digest_payload({"path": f"{PREFIX}/automations", "body": body})
        _insert_pending_idempotency_row(
            user["tenant_id"], user["user_id"], _ROUTE_CREATE, key, digest
        )

        resp = client.post(
            f"{PREFIX}/automations",
            headers={**_auth(user), "Idempotency-Key": key}, json=body,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"
        # 占位未放行执行：自动化零创建
        resp = client.get(
            f"{PREFIX}/automations", headers=_auth(user), params={"keyword": "新鲜占位"},
        )
        assert resp.json()["data"]["total"] == 0

    def test_digest_key_order_insensitive(self, client, users, wxm_adapter):
        """T-P2：同 key + 键序打乱（顶层与嵌套）的等价 JSON → 200 幂等重放
        （digest 按 sort_keys 规范化，键序差异不得误判异 payload）"""
        user = users[0]
        group_id = _create_binding(user["tenant_id"], user["user_id"])
        key = f"it-order-{uuid.uuid4().hex[:12]}"
        body = _create_payload(group_id, name="键序无关")

        first = client.post(
            f"{PREFIX}/automations",
            headers={**_auth(user), "Idempotency-Key": key}, json=body,
        )
        assert first.status_code == 200, first.text
        automation_id = first.json()["data"]["automation"]["id"]

        shuffled = {
            "blocks": [dict(reversed(list(block.items()))) for block in body["blocks"]],
            "group_binding_id": body["group_binding_id"],
            "trigger": dict(reversed(list(body["trigger"].items()))),
            "name": body["name"],
        }
        replay = client.post(
            f"{PREFIX}/automations",
            headers={
                **_auth(user), "Idempotency-Key": key,
                "Content-Type": "application/json",
            },
            content=json.dumps(shuffled, ensure_ascii=False),
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["data"]["automation"]["id"] == automation_id
        # 仍只有一个自动化（重放未重复执行）
        resp = client.get(
            f"{PREFIX}/automations", headers=_auth(user), params={"keyword": "键序无关"},
        )
        assert resp.json()["data"]["total"] == 1

    def test_crash_between_business_and_idempotency_record_rolls_back(
        self, client, users, wxm_adapter, monkeypatch
    ):
        """R51 故障注入①：业务写入与幂等完成记录之间注入崩溃（write_on 抛出）→
        同一事务整体回滚——零残留资源（无 automation 行），占位随失败路径释放，
        重试干净执行；重放返回原结果。"""
        from src.weixin_marketing import api as wxm_api

        user = users[0]
        tenant_id = user["tenant_id"]
        group_id = _create_binding(tenant_id, user["user_id"])
        url, headers = f"{PREFIX}/automations", _auth(user)
        key = {"Idempotency-Key": f"it-crash-{uuid.uuid4().hex[:12]}"}
        body = _create_payload(group_id, name="崩溃注入创建")

        def exploding_write_on(self, cursor, *, status_code, data):
            raise RuntimeError("simulated crash between business writes and idempotency record")

        monkeypatch.setattr(wxm_api._IdempotencyFinalizer, "write_on", exploding_write_on)
        crashed = client.post(url, headers={**headers, **key}, json=body)
        monkeypatch.undo()
        assert crashed.status_code == 500, crashed.text
        assert crashed.json()["code"] == "INTERNAL_ERROR"

        # 回滚无残留资源：自动化零创建；幂等占位已释放（可干净重试）
        resp = client.get(
            f"{PREFIX}/automations", headers=headers, params={"keyword": "崩溃注入创建"},
        )
        assert resp.json()["data"]["total"] == 0
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM weixin_marketing_idempotency_keys "
                "WHERE tenant_id = %s AND idempotency_key = %s",
                (tenant_id, key["Idempotency-Key"]),
            )
            assert int(cur.fetchone()["c"]) == 0  # 失败路径已 abandon

        # 重试干净执行 + 重放返回原结果
        first = client.post(url, headers={**headers, **key}, json=body)
        assert first.status_code == 200, first.text
        automation_id = first.json()["data"]["automation"]["id"]
        replay = client.post(url, headers={**headers, **key}, json=body)
        assert replay.status_code == 200 and replay.json() == first.json()

    def test_business_committed_without_response_saved_unreachable(
        self, client, users, wxm_adapter, monkeypatch
    ):
        """R51 故障注入②（文档化断言）：「业务已提交而响应未保存」在本设计下不可达。

        选型证明：_IdempotencyFinalizer.write_on 在服务层业务事务内、commit 前执行——
        注入其失败必然连带业务写入一并回滚（本用例断言 occurrence/run/审计零行）；
        若两段提交（业务先 commit、响应后补写），该注入点失败会留下已创建资源，
        本断言即失败。同 key 重试命中 manual 触发键去重（request_id 恒取 key），
        干净重执行后恰好一个 occurrence。"""
        from src.desktop_automation.constants import manual_trigger_key
        from src.weixin_marketing import api as wxm_api

        user = users[0]
        tenant_id = user["tenant_id"]
        automation_id, _ = TestManualRunAndRuns()._published_automation(client, user)
        url, headers = (
            f"{PREFIX}/automations/{automation_id}/run", _auth(user),
        )
        key = f"it-run-crash-{uuid.uuid4().hex[:12]}"

        def exploding_write_on(self, cursor, *, status_code, data):
            raise RuntimeError("simulated crash before commit")

        monkeypatch.setattr(wxm_api._IdempotencyFinalizer, "write_on", exploding_write_on)
        crashed = client.post(url, headers={**headers, "Idempotency-Key": key}, json={})
        monkeypatch.undo()
        assert crashed.status_code == 500, crashed.text

        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM desktop_automation_occurrences "
                "WHERE tenant_id = %s AND task_ref = %s",
                (tenant_id, automation_id),
            )
            assert int(cur.fetchone()["c"]) == 0  # 接纳整体回滚（与幂等记录同事务）
            cur.execute(
                "SELECT COUNT(*) AS c FROM desktop_automation_runs "
                "WHERE tenant_id = %s AND task_ref = %s",
                (tenant_id, automation_id),
            )
            assert int(cur.fetchone()["c"]) == 0
            cur.execute(
                "SELECT COUNT(*) AS c FROM bs_weixin_marketing_audit_events "
                "WHERE tenant_id = %s AND automation_id = %s "
                "AND action = 'manual_run_requested'",
                (tenant_id, str(automation_id)),
            )
            assert int(cur.fetchone()["c"]) == 0

        # 重试干净执行：request_id 恒取 key（manual 触发键同源），恰好一个 occurrence
        first = client.post(url, headers={**headers, "Idempotency-Key": key}, json={})
        assert first.status_code == 202, first.text
        replay = client.post(url, headers={**headers, "Idempotency-Key": key}, json={})
        assert replay.status_code == 202 and replay.json() == first.json()
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT trigger_key FROM desktop_automation_occurrences "
                "WHERE tenant_id = %s AND task_ref = %s",
                (tenant_id, automation_id),
            )
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["trigger_key"] == manual_trigger_key(key)
        # 收敛 run 至终态（防 pending 残留被后续用例领取）
        from src.weixin_marketing.service import WeixinMarketingService

        WeixinMarketingService().cancel_run(
            tenant_id, first.json()["data"]["run_id"], user["user_id"]
        )
