"""desktop_automation + local_tools v2 API 集成测试（真实 DB + TestClient，不可用时 skip）

覆盖（R8 + R17 + 契约补充 #1/#2）：
- HTTP 流：v2 invocation claim（provider=行 provider_key）→ started → write-authorize 200
  → operation-result 200（幂等 ACK）→ run 聚合
- 错误码：旧 invocation 409、claim 不匹配 404、跨租户 404、无登录 401
- payload 端点：授权下载（X-Payload-Hash/Content-Type/冻结字节）、跨租户 404、
  hash 篡改 500+审计、无 token 401、非法 UUID 404
- deliveries 视图：属主 200 / 跨租户 404 / 未登录 401
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.unit.desktop_automation.fakes import (
    FAKE_EVIDENCE_NAMESPACE,
    namespaced_evidence_ref,
)

pytestmark = pytest.mark.integration

FAKE_PROVIDER_KEY = "fake-scenario"
FAKE_PROVIDER_ID = "ai.aidwork.fake-scenario"


@pytest.fixture(scope="module")
def fake_provider():
    """临时注册 fake provider（TRUSTED_PROVIDERS 测试形态；测后移除）"""
    from src.local_tools import catalog

    catalog.TRUSTED_PROVIDERS[FAKE_PROVIDER_KEY] = {
        "provider_id": FAKE_PROVIDER_ID,
        "min_provider_version": "1.0.0",
        "tools": ["fake_op_one", "fake_op_two", "boss_goto"],
    }
    yield FAKE_PROVIDER_KEY
    catalog.TRUSTED_PROVIDERS.pop(FAKE_PROVIDER_KEY, None)


@pytest.fixture(scope="module")
def fake_adapter():
    """注册假场景适配器（TrustedAdapterRegistry 进程内受信注册；测后注销）。

    payload_ref 采用 da:<scenario_key>:<opaque> 规范形态（R17），首条即带载荷，
    供 payload 端点测试使用。
    """
    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from tests.unit.desktop_automation.fakes import FakeScenarioAdapter

    adapter = FakeScenarioAdapter(
        operations=[
            {"position": 1, "operation": "fake_op_one", "target_ref": "target-1",
             "payload_ref": f"da:{FAKE_PROVIDER_KEY}:payload:1",
             "provider_key": FAKE_PROVIDER_KEY},
            {"position": 2, "operation": "fake_op_two", "target_ref": "target-2",
             "payload_ref": f"da:{FAKE_PROVIDER_KEY}:payload:2",
             "provider_key": FAKE_PROVIDER_KEY},
        ],
        payloads={f"da:{FAKE_PROVIDER_KEY}:payload:1": b"one",
                  f"da:{FAKE_PROVIDER_KEY}:payload:2": b"two"},
        quota_limit=10,
    )
    TrustedAdapterRegistry.register(adapter)
    yield adapter
    TrustedAdapterRegistry.unregister(FAKE_PROVIDER_KEY)


@pytest.fixture(scope="module")
def client():
    from src.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def tenants():
    from src.saas.db.tenant_db import TenantDB

    created = []
    for _ in range(2):
        code = f"D{uuid.uuid4().hex[:6].upper()}"
        tenant = TenantDB.create(
            company_name=f"桌面自动化测试-{code}",
            tenant_code=code,
            contact_name="测试",
            contact_phone="13800000000",
        )
        if not tenant:
            pytest.skip("无法创建测试租户（DB 不可用）")
        created.append(tenant["tenant_id"])

    yield created

    from src.db.database import get_db_connection
    from tests.unit.desktop_automation.conftest import DA_TABLES

    for tenant_id in created:
        TenantDB.delete(tenant_id)
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                for table in DA_TABLES:
                    cur.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
                cur.execute(
                    "DELETE FROM tokens WHERE user_id IN (SELECT user_id FROM users WHERE tenant_id = %s)",
                    (tenant_id,),
                )
                cur.execute("DELETE FROM users WHERE tenant_id = %s", (tenant_id,))
                cur.execute("DELETE FROM tenants WHERE tenant_id = %s", (tenant_id,))
                conn.commit()
        except Exception:
            pass


@pytest.fixture(scope="module")
def users(tenants):
    from src.api.auth import generate_token
    from src.db.models import UserDB

    result = []
    for i, tenant_id in enumerate(tenants):
        phone = f"198{uuid.uuid4().int % 10**8:08d}"
        user = UserDB.create(phone=phone, username=f"桌面自动化测试用户{i}", tenant_id=tenant_id)
        if not user:
            pytest.skip("无法创建测试用户（DB 不可用）")
        token = generate_token(user["user_id"])
        result.append({"user_id": user["user_id"], "tenant_id": tenant_id, "token": token})
    return result


def _pair_device(client, user):
    resp = client.post("/api/local-tools/pairing-tickets",
                       headers={"Authorization": f"Bearer {user['token']}"})
    assert resp.status_code == 200, resp.text
    code = resp.json()["code"]
    # 契约补充 #2 的 capabilities 形态：providers 数组 + provider_manifests + 旧 provider_id 兜底
    resp = client.post(
        "/api/local-tools/runtime/pair",
        json={
            "code": code,
            "name": "桌面自动化机",
            "platform": "windows",
            "runtime_version": "1.0.0",
            "capabilities": {
                "protocol_version": 2,
                "providers": [FAKE_PROVIDER_KEY],
                "provider_manifests": {
                    FAKE_PROVIDER_KEY: {"provider_id": FAKE_PROVIDER_ID, "protocol_version": 2},
                },
                "provider_id": FAKE_PROVIDER_ID,
            },
            "machine_fingerprint": "fp-" + uuid.uuid4().hex[:8],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["device_id"], body["device_token"]


def _setup_v2_invocation(tenant_id, user_id, device_id, adapter):
    """发布场景 → 手动触发 → claim+prepare → 派发第 1 条（返回执行现场）"""
    from src.desktop_automation import executor, occurrences, subjects

    subjects.publish_revision(
        tenant_id, adapter.scenario_key, "task-1", "rev-1", user_id,
        revision_config={}, schedule_specs=[],
    )
    occurrences.accept_manual_trigger(
        tenant_id=tenant_id, scenario_key=adapter.scenario_key, task_ref="task-1",
        request_id=f"it-{uuid.uuid4().hex[:8]}", user_id=user_id, now=_utcnow(),
    )
    prepared = executor.claim_and_prepare_run(
        revision_config={}, device_id=device_id, tenant_id=tenant_id,
    )
    assert prepared and prepared["prepared"] is True, prepared
    step = executor.execute_next_delivery(prepared["run"])
    assert step is not None
    return step


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


class TestV2RuntimeFlow:
    def test_claim_started_authorize_result_http_flow(
        self, client, users, fake_provider, fake_adapter
    ):
        user = users[0]
        device_id, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}
        step = _setup_v2_invocation(
            user["tenant_id"], user["user_id"], device_id, fake_adapter
        )

        # claim：返回 v2 invocation；provider=行 provider_key（契约补充 #1）
        resp = client.post("/api/local-tools/runtime/claim?wait=3", headers=runtime)
        assert resp.status_code == 200, resp.text
        claim = resp.json()
        assert claim["invocation_id"] == step["invocation_id"]
        assert claim["provider"] == FAKE_PROVIDER_KEY
        args = claim["arguments"]
        assert args["protocol_version"] == 2
        assert args["delivery_id"] == str(step["delivery"]["id"])
        claim_token = claim["claim_token"]

        # started
        resp = client.post(
            f"/api/local-tools/runtime/invocations/{step['invocation_id']}/started",
            json={"claim_token": claim_token}, headers=runtime,
        )
        assert resp.status_code == 200 and resp.json()["state"] == "running"

        # write-authorize：200 + permit（token 明文仅此一次）
        resp = client.post(
            f"/api/local-tools/runtime/invocations/{step['invocation_id']}/write-authorize",
            json={
                "claim_token": claim_token,
                "request_id": args["request_id"],
                "target_version": args["target_version"],
                "payload_hash": args.get("payload_hash"),
            },
            headers=runtime,
        )
        assert resp.status_code == 200, resp.text
        permit = resp.json()
        assert permit["permit_id"] and permit["permit_token"] and permit["deadline_at"]

        # operation-result：applied+verified+permit → succeeded
        resp = client.post(
            f"/api/local-tools/runtime/invocations/{step['invocation_id']}/operation-result",
            json={
                "claim_token": claim_token,
                "request_id": args["request_id"],
                "effect": "applied",
                "phase": "verified",
                "evidence_ref": namespaced_evidence_ref(
                    FAKE_EVIDENCE_NAMESPACE, args["request_id"], 1),
                "permit_id": permit["permit_id"],
                "permit_token": permit["permit_token"],
            },
            headers=runtime,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "succeeded" and body["effect"] == "applied"
        assert body["late"] is False

        # 重复回执：持久 ACK 幂等（late=True 不改判）
        resp = client.post(
            f"/api/local-tools/runtime/invocations/{step['invocation_id']}/operation-result",
            json={
                "claim_token": claim_token,
                "request_id": args["request_id"],
                "effect": "unknown",
                "phase": "unknown",
            },
            headers=runtime,
        )
        assert resp.status_code == 200
        assert resp.json()["late"] is True
        assert resp.json()["state"] == "succeeded"


class TestErrorCodes:
    def test_write_authorize_old_invocation_409(self, client, users, fake_provider):
        """R8：旧 invocation（business_kind NULL）调用 write-authorize → 409"""
        user = users[0]
        device_id, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}
        from src.local_tools import repository

        invocation_id = repository.create_invocation(
            user["tenant_id"], user["user_id"], device_id, "boss_goto", {"url": "x"},
        )
        resp = client.post("/api/local-tools/runtime/claim?wait=3", headers=runtime)
        token = resp.json()["claim_token"]
        client.post(
            f"/api/local-tools/runtime/invocations/{invocation_id}/started",
            json={"claim_token": token}, headers=runtime,
        )
        resp = client.post(
            f"/api/local-tools/runtime/invocations/{invocation_id}/write-authorize",
            json={"claim_token": token, "request_id": "r", "target_version": None,
                  "payload_hash": None},
            headers=runtime,
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "NOT_DESKTOP_AUTOMATION"

    def test_write_authorize_wrong_claim_404(self, client, users, fake_provider, fake_adapter):
        user = users[0]
        device_id, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}
        step = _setup_v2_invocation(user["tenant_id"], user["user_id"], device_id, fake_adapter)
        resp = client.post("/api/local-tools/runtime/claim?wait=3", headers=runtime)
        args = resp.json()["arguments"]
        client.post(
            f"/api/local-tools/runtime/invocations/{step['invocation_id']}/started",
            json={"claim_token": resp.json()["claim_token"]}, headers=runtime,
        )
        resp = client.post(
            f"/api/local-tools/runtime/invocations/{step['invocation_id']}/write-authorize",
            json={"claim_token": "bad" * 21, "request_id": args["request_id"],
                  "target_version": None, "payload_hash": None},
            headers=runtime,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "CLAIM_MISMATCH"

    def test_cross_tenant_authorize_404(self, client, users, fake_provider, fake_adapter):
        """跨租户：B 设备 token 对 A 租户 invocation 调 write-authorize → 404
        （租户取自设备身份，不信请求体）"""
        user_a, user_b = users[0], users[1]
        device_a, _ = _pair_device(client, user_a)
        _, token_b = _pair_device(client, user_b)
        step = _setup_v2_invocation(user_a["tenant_id"], user_a["user_id"], device_a, fake_adapter)
        # B 设备先领取自己的空队列（保证 A 的 invocation 不被 B 领走）
        resp = client.post("/api/local-tools/runtime/claim?wait=1",
                           headers={"Authorization": f"Bearer {token_b}"})
        assert resp.json().get("invocation") is None
        resp = client.post(
            f"/api/local-tools/runtime/invocations/{step['invocation_id']}/write-authorize",
            json={"claim_token": "x" * 64, "request_id": "r",
                  "target_version": None, "payload_hash": None},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404

    def test_invalid_uuid_path_404(self, client, users, fake_provider):
        """P2-6：非法 UUID 路径参数 → 404（不触发 DB DataError 500）"""
        user = users[0]
        _, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}
        for path in (
            "/api/local-tools/runtime/invocations/not-a-uuid/write-authorize",
            "/api/local-tools/runtime/invocations/not-a-uuid/operation-result",
        ):
            resp = client.post(
                path,
                json={"claim_token": "x" * 64, "request_id": "r", "effect": "none"},
                headers=runtime,
            )
            assert resp.status_code == 404, (path, resp.status_code)
        resp = client.get(
            "/api/desktop-automation/deliveries/not-a-uuid",
            headers={"Authorization": f"Bearer {user['token']}"},
        )
        assert resp.status_code == 404

    def test_runtime_endpoints_require_device_token(self, client, users):
        """无设备 token → 401"""
        user = users[0]
        invocation_id = str(uuid.uuid4())
        for path in (
            f"/api/local-tools/runtime/invocations/{invocation_id}/write-authorize",
            f"/api/local-tools/runtime/invocations/{invocation_id}/operation-result",
        ):
            resp = client.post(path, json={"claim_token": "x", "request_id": "r",
                                           "effect": "none"})
            assert resp.status_code == 401


class TestDeliveryView:
    def test_owner_view_and_cross_tenant_404(self, client, users, fake_provider, fake_adapter):
        user_a, user_b = users[0], users[1]
        device_id, _ = _pair_device(client, user_a)
        step = _setup_v2_invocation(user_a["tenant_id"], user_a["user_id"], device_id, fake_adapter)
        delivery_id = str(step["delivery"]["id"])

        # 未登录 → 401
        resp = client.get(f"/api/desktop-automation/deliveries/{delivery_id}")
        assert resp.status_code == 401

        # 属主可见（中立视图：不返回 payload 内容）
        resp = client.get(
            f"/api/desktop-automation/deliveries/{delivery_id}",
            headers={"Authorization": f"Bearer {user_a['token']}"},
        )
        assert resp.status_code == 200, resp.text
        view = resp.json()["delivery"]
        assert view["delivery_id"] == delivery_id
        assert view["operation"] == "fake_op_one"
        assert "payload_ref" in view  # 只有引用，无正文

        # 跨租户/无权 → 404（不区分存在性）
        resp = client.get(
            f"/api/desktop-automation/deliveries/{delivery_id}",
            headers={"Authorization": f"Bearer {user_b['token']}"},
        )
        assert resp.status_code == 404

    def test_non_owner_same_tenant_404(self, client, users, fake_provider, fake_adapter):
        """同租户非属主：默认可见范围（任务属主本人）外 → 404"""
        from src.api.auth import generate_token
        from src.db.models import UserDB

        user_a = users[0]
        device_id, _ = _pair_device(client, user_a)
        step = _setup_v2_invocation(user_a["tenant_id"], user_a["user_id"], device_id, fake_adapter)
        other = UserDB.create(phone=f"197{uuid.uuid4().int % 10**8:08d}",
                              username="同租户他人", tenant_id=user_a["tenant_id"])
        token = generate_token(other["user_id"])
        resp = client.get(
            f"/api/desktop-automation/deliveries/{step['delivery']['id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404


class TestPayloadEndpoint:
    """GET /api/local-tools/runtime/invocations/{id}/payload（R17 素材/载荷通道）"""

    def test_payload_http_flow(self, client, users, fake_provider, fake_adapter):
        """授权设备下载载荷：200 + X-Payload-Hash + 冻结字节 + Content-Type"""
        import hashlib as _hashlib

        user = users[0]
        device_id, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}
        step = _setup_v2_invocation(user["tenant_id"], user["user_id"], device_id, fake_adapter)

        # claim → started（payload 下载在未终态窗口内）
        resp = client.post("/api/local-tools/runtime/claim?wait=3", headers=runtime)
        assert resp.status_code == 200, resp.text
        claim = resp.json()
        assert claim["invocation_id"] == step["invocation_id"]
        assert claim["arguments"]["payload_ref"] == f"da:{FAKE_PROVIDER_KEY}:payload:1"
        client.post(
            f"/api/local-tools/runtime/invocations/{step['invocation_id']}/started",
            json={"claim_token": claim["claim_token"]}, headers=runtime,
        )

        resp = client.get(
            f"/api/local-tools/runtime/invocations/{step['invocation_id']}/payload",
            headers=runtime,
        )
        assert resp.status_code == 200, resp.text
        assert resp.content == b"one"
        assert resp.headers["x-payload-hash"] == _hashlib.sha256(b"one").hexdigest()
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.headers["x-content-type-options"] == "nosniff"

    def test_payload_cross_tenant_404(self, client, users, fake_provider, fake_adapter):
        """跨租户设备 token 对他租户 invocation 下载载荷 → 404（不区分存在性）"""
        user_a, user_b = users[0], users[1]
        device_a, _ = _pair_device(client, user_a)
        _, token_b = _pair_device(client, user_b)
        step = _setup_v2_invocation(user_a["tenant_id"], user_a["user_id"], device_a, fake_adapter)
        # B 设备先领取自己的空队列（保证 A 的 invocation 不被 B 领走）
        resp = client.post("/api/local-tools/runtime/claim?wait=1",
                           headers={"Authorization": f"Bearer {token_b}"})
        assert resp.json().get("invocation") is None
        resp = client.get(
            f"/api/local-tools/runtime/invocations/{step['invocation_id']}/payload",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert resp.status_code == 404

    def test_payload_hash_mismatch_500(self, client, users, fake_provider, fake_adapter):
        """invocation 凭据 hash 与适配器字节不符 → 500 拒绝（不下发字节）+ 审计"""
        from src.desktop_automation import audit as da_audit

        user = users[0]
        device_id, device_token = _pair_device(client, user)
        runtime = {"Authorization": f"Bearer {device_token}"}
        step = _setup_v2_invocation(user["tenant_id"], user["user_id"], device_id, fake_adapter)

        # claim → started（payload 下载须在已领取未终态窗口内）
        resp = client.post("/api/local-tools/runtime/claim?wait=3", headers=runtime)
        assert resp.status_code == 200, resp.text
        claim = resp.json()
        client.post(
            f"/api/local-tools/runtime/invocations/{step['invocation_id']}/started",
            json={"claim_token": claim["claim_token"]}, headers=runtime,
        )

        payload_ref = f"da:{FAKE_PROVIDER_KEY}:payload:1"
        original = fake_adapter.payloads[payload_ref]
        try:
            fake_adapter.payloads[payload_ref] = b"tampered"
            resp = client.get(
                f"/api/local-tools/runtime/invocations/{step['invocation_id']}/payload",
                headers=runtime,
            )
            assert resp.status_code == 500, resp.text
            assert resp.json()["detail"]["error"] == "PAYLOAD_HASH_MISMATCH"
            assert b"tampered" != resp.content
            audits = da_audit.list_audits(
                user["tenant_id"], aggregate_type="invocation",
                aggregate_ref=step["invocation_id"],
            )
            assert any(a["kind"] == "payload_hash_mismatch" for a in audits)
        finally:
            fake_adapter.payloads[payload_ref] = original

    def test_payload_no_token_401_and_invalid_uuid_404(self, client, users, fake_provider):
        user = users[0]
        _, device_token = _pair_device(client, user)
        resp = client.get(f"/api/local-tools/runtime/invocations/{uuid.uuid4()}/payload")
        assert resp.status_code == 401
        resp = client.get(
            "/api/local-tools/runtime/invocations/not-a-uuid/payload",
            headers={"Authorization": f"Bearer {device_token}"},
        )
        assert resp.status_code == 404
