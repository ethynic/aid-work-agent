"""payload resolver 与双消费方 v2 契约测试（P1-D 交付物 1/3，R15/R17 + CR D1 修复）

覆盖：
- 两消费方（weixin.fixed_content.v1 / boss.chat_reply.v1）的 v2 操作描述字段集
  完全一致（字段名集合 diff 为空），无群/候选人专用字段；
- payload resolver：正常取冻结字节 / 错租户拒绝 / hash 篡改拒绝（不返回字节）；
- payload 端点：无 token 401、非法 UUID 404、跨设备 404、queued 拒绝（D1-P2-1 收紧）、
  终态 invocation 409、旧 invocation 409、非 v2 协议 409、场景绑定缺失 409
  （D1-P1-1 fail-closed）、payload_ref 缺失/场景不符/形态非法 422、hash 篡改 500+审计、
  nosniff 响应头。
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.db.database import get_db_connection
from src.desktop_automation import audit, executor, occurrences, payload_resolver, subjects
from src.desktop_automation.adapters import (
    AdapterContext,
    AdapterNotFoundError,
    TrustedAdapterRegistry,
)
from src.local_tools import repository
from src.local_tools.security import sha256_hex
from tests.unit.desktop_automation.contract_fakes import (
    BOSS_SCENARIO_KEY,
    BOSS_V2_OPERATION,
    FROZEN_PNG_BYTES,
    FROZEN_TEXT_BYTES,
    V2_OPERATION_FIELDS,
    WEIXIN_SCENARIO_KEY,
    WEIXIN_V2_OPERATION,
    make_boss_chat_reply_adapter,
    make_weixin_fixed_content_adapter,
)

# 两消费方操作描述禁止出现的场景专用字段（R15：中立协议无群/候选人词汇）
BANNED_FIELDS = {
    "group_name", "group_id", "wechat_group", "group_welcome",
    "candidate_id", "candidate_name", "resume_id", "job_id",
    "image_url", "file_path", "url", "path", "content",
}


def _ctx(tenant_id: str, scenario_key: str) -> AdapterContext:
    return AdapterContext(
        tenant_id=tenant_id, user_id="owner-1", scenario_key=scenario_key,
        task_ref="task-1", revision_ref="rev-1",
    )


@pytest.fixture(scope="module")
def client():
    from src.main import app

    return TestClient(app)


@pytest.fixture()
def weixin_adapter(tenant_id):
    adapter = make_weixin_fixed_content_adapter(allowed_tenant=tenant_id)
    TrustedAdapterRegistry.register(adapter)
    yield adapter
    TrustedAdapterRegistry.unregister(WEIXIN_SCENARIO_KEY)


class TestDualConsumerContract:
    """两消费方 v2 操作描述契约一致性（字段集 diff 为空）"""

    def test_operation_field_sets_identical(self, tenant_id):
        consumers = {
            WEIXIN_SCENARIO_KEY: make_weixin_fixed_content_adapter(allowed_tenant=tenant_id),
            BOSS_SCENARIO_KEY: make_boss_chat_reply_adapter(allowed_tenant=tenant_id),
        }
        args_by_consumer = {}
        deadline = datetime.now(timezone.utc) + timedelta(minutes=10)
        for scenario_key, adapter in consumers.items():
            TrustedAdapterRegistry.register(adapter)
            try:
                ops = adapter.compile_operations(_ctx(tenant_id, scenario_key), {})
                assert [op.operation for op in ops] == [adapter.operation]
                op = ops[0]
                args_by_consumer[scenario_key] = executor.build_v2_operation_arguments(
                    operation=op.operation,
                    provider_key=op.provider_key,
                    target_ref=op.target_ref,
                    target_handle=op.target_handle,
                    target_version=op.target_version,
                    payload_ref=op.payload_ref,
                    payload_hash=op.payload_hash,
                    request_id="req-contract-1",
                    delivery_id=str(uuid.uuid4()),
                    authorization_revision="rev-1",
                    authorization_epoch=1,
                    resource_key="rk-contract-1",
                    deadline_at=deadline,
                )
            finally:
                TrustedAdapterRegistry.unregister(scenario_key)

        wx_args = args_by_consumer[WEIXIN_SCENARIO_KEY]
        boss_args = args_by_consumer[BOSS_SCENARIO_KEY]
        # operation 名各归各消费方（_v2 后缀）
        assert wx_args["operation"] == WEIXIN_V2_OPERATION
        assert boss_args["operation"] == BOSS_V2_OPERATION
        # 字段名集合完全一致：双向 diff 为空，且恰为 R15 字段集
        assert set(wx_args) - set(boss_args) == set()
        assert set(boss_args) - set(wx_args) == set()
        assert set(wx_args) == set(V2_OPERATION_FIELDS)
        assert wx_args["protocol_version"] == boss_args["protocol_version"] == 2
        # 无群/候选人/URL/路径等场景专用字段
        assert not (set(wx_args) & BANNED_FIELDS)
        assert not (set(boss_args) & BANNED_FIELDS)
        # 正文不进操作描述：只有 payload_ref/hash 引用
        assert wx_args["payload_ref"] == f"da:{WEIXIN_SCENARIO_KEY}:frozen-1"
        assert boss_args["payload_ref"] == f"da:{BOSS_SCENARIO_KEY}:frozen-1"
        assert wx_args["payload_hash"] == hashlib.sha256(FROZEN_PNG_BYTES).hexdigest()
        assert boss_args["payload_hash"] == hashlib.sha256(FROZEN_TEXT_BYTES).hexdigest()


class TestResolvePayload:
    """resolver 直测：受控字节 / 错租户 / hash 篡改 / 形态与注册校验"""

    def test_resolve_returns_frozen_bytes_mime_hash(self, tenant_id, weixin_adapter):
        resolution = payload_resolver.resolve_payload(
            tenant_id, weixin_adapter.payload_ref, weixin_adapter.payload_hash,
            user_id="owner-1", task_ref="task-1", revision_ref="rev-1",
        )
        assert resolution.data == FROZEN_PNG_BYTES
        assert resolution.mime == "image/png"
        assert resolution.payload_hash == weixin_adapter.payload_hash
        # 元组解包契约：(bytes, mime, hash)
        data, mime, payload_hash = resolution
        assert (data, mime, payload_hash) == (FROZEN_PNG_BYTES, "image/png", weixin_adapter.payload_hash)
        # 适配器收到完整 ref 与正确租户上下文
        assert weixin_adapter.serve_calls[-1] == {
            "tenant_id": tenant_id, "payload_ref": weixin_adapter.payload_ref,
        }

    def test_resolve_expectation_none_still_returns_computed_hash(self, tenant_id, weixin_adapter):
        resolution = payload_resolver.resolve_payload(tenant_id, weixin_adapter.payload_ref)
        assert resolution.payload_hash == hashlib.sha256(FROZEN_PNG_BYTES).hexdigest()

    def test_resolve_wrong_tenant_refused(self, tenant_id, weixin_adapter):
        """错租户：适配器租户 ACL 拒绝（fail-closed，字节不泄露）"""
        with pytest.raises(PermissionError):
            payload_resolver.resolve_payload(
                f"da_test_other_{uuid.uuid4().hex[:8]}", weixin_adapter.payload_ref,
            )

    def test_resolve_hash_tampered_rejected(self, tenant_id, weixin_adapter):
        """hash 篡改：期望 hash 与字节不符 → PayloadHashMismatch，不返回字节"""
        with pytest.raises(payload_resolver.PayloadHashMismatch):
            payload_resolver.resolve_payload(
                tenant_id, weixin_adapter.payload_ref, expectation_hash="00" * 32,
            )

    def test_resolve_unknown_opaque_rejected(self, tenant_id, weixin_adapter):
        with pytest.raises(KeyError):
            payload_resolver.resolve_payload(
                tenant_id, f"da:{WEIXIN_SCENARIO_KEY}:tampered-opaque",
                expectation_hash=weixin_adapter.payload_hash,
            )

    def test_resolve_unregistered_scenario(self):
        with pytest.raises(AdapterNotFoundError):
            payload_resolver.resolve_payload(
                "da_test_ghost", "da:ghost.scenario.v1:any",
            )

    @pytest.mark.parametrize(
        "bad_ref",
        ["payload:2", "http://evil/x", "/etc/passwd", "da:", "da::opaque", "da:scenario:", "da::"],
    )
    def test_parse_payload_ref_invalid(self, bad_ref):
        with pytest.raises(payload_resolver.PayloadRefError):
            payload_resolver.parse_payload_ref(bad_ref)

    def test_parse_payload_ref_allows_colon_in_opaque(self):
        scenario, opaque = payload_resolver.parse_payload_ref("da:s.k:op:aque:1")
        assert scenario == "s.k" and opaque == "op:aque:1"

    def test_sniff_mime(self):
        assert payload_resolver.sniff_mime(b"\x89PNG\r\n\x1a\nxx") == "image/png"
        assert payload_resolver.sniff_mime(b"\xff\xd8\xffjfi") == "image/jpeg"
        assert payload_resolver.sniff_mime(b"GIF89a") == "image/gif"
        assert payload_resolver.sniff_mime(b"%PDF-1.7") == "application/pdf"
        assert payload_resolver.sniff_mime("中文文本".encode("utf-8")) == "text/plain; charset=utf-8"
        assert payload_resolver.sniff_mime(b"\x00\x01bin") == "application/octet-stream"


# ==================== payload 端点（设备 token + invocation scope）====================


def _register_device(tenant_id: str, user_id: str = "owner-1"):
    """直建设备行（token hash 已知），返回 (device_id, token)"""
    token = f"dev-{uuid.uuid4().hex}"
    device = repository.create_device(tenant_id, user_id, sha256_hex(token))
    return str(device["id"]), token


def _force_claimed(tenant_id: str, invocation_id: str) -> None:
    """直建 invocation 直置 claimed（绕过 claim_next 的 provider 过滤，聚焦端点校验链后段）"""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE local_tool_invocations SET state = 'claimed' WHERE id = %s AND tenant_id = %s",
            (invocation_id, tenant_id),
        )
        conn.commit()


def _dispatch_first_delivery(tenant_id, device_id, adapter):
    """发布场景 → 手动触发 → claim+prepare → 派发第 1 条（invocation=queued，归属 device）"""
    subjects.publish_revision(
        tenant_id, adapter.scenario_key, "task-1", "rev-1", "owner-1",
        revision_config={}, schedule_specs=[],
    )
    occurrences.accept_manual_trigger(
        tenant_id=tenant_id, scenario_key=adapter.scenario_key, task_ref="task-1",
        request_id=f"pr-{uuid.uuid4().hex[:8]}", user_id="owner-1", now=datetime.now(timezone.utc),
    )
    prepared = executor.claim_and_prepare_run(
        revision_config={}, device_id=device_id, tenant_id=tenant_id, lease_seconds=300,
    )
    assert prepared and prepared["prepared"] is True, prepared
    step = executor.execute_next_delivery(prepared["run"])
    assert step is not None, "应派发第 1 条 delivery"
    return step


def _claim_and_start(tenant_id, device_id, invocation_id):
    """模拟设备 claim + started（queued → running）"""
    from src.local_tools.security import generate_claim_token

    claim_token = generate_claim_token()
    claimed = repository.claim_next(device_id, tenant_id, sha256_hex(claim_token), 300)
    assert claimed is not None and str(claimed["id"]) == invocation_id
    started = repository.mark_started(invocation_id, tenant_id, sha256_hex(claim_token))
    assert started and started["state"] == "running"
    return claim_token


class TestPayloadEndpoint:
    """GET /api/local-tools/runtime/invocations/{id}/payload"""

    def test_happy_path_running_invocation(self, client, tenant_id, weixin_adapter):
        device_id, token = _register_device(tenant_id)
        runtime = {"Authorization": f"Bearer {token}"}
        step = _dispatch_first_delivery(tenant_id, device_id, weixin_adapter)
        invocation_id = step["invocation_id"]
        _claim_and_start(tenant_id, device_id, invocation_id)

        resp = client.get(f"/api/local-tools/runtime/invocations/{invocation_id}/payload",
                          headers=runtime)
        assert resp.status_code == 200, resp.text
        assert resp.content == FROZEN_PNG_BYTES
        assert resp.headers["x-payload-hash"] == weixin_adapter.payload_hash
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.headers["x-content-type-options"] == "nosniff"  # D1-P2-4
        # 适配器收到 invocation 租户上下文（不信请求体自报租户）
        assert weixin_adapter.serve_calls[-1]["tenant_id"] == tenant_id

    def test_queued_invocation_refused_409(self, client, tenant_id, weixin_adapter):
        """D1-P2-1 总工裁决收紧：queued 窗口无消费方，拒绝下载（claim 后才放行）"""
        device_id, token = _register_device(tenant_id)
        step = _dispatch_first_delivery(tenant_id, device_id, weixin_adapter)
        # executor 派发时的 payload 预载会调用 serve_payload 一次，记录基线计数
        preload_calls = len(weixin_adapter.serve_calls)
        resp = client.get(f"/api/local-tools/runtime/invocations/{step['invocation_id']}/payload",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "INVOCATION_NOT_CLAIMED"
        # 端点未再取字节
        assert len(weixin_adapter.serve_calls) == preload_calls

    def test_requires_device_token_401(self, client, tenant_id):
        resp = client.get(f"/api/local-tools/runtime/invocations/{uuid.uuid4()}/payload")
        assert resp.status_code == 401

    def test_invalid_uuid_404(self, client, tenant_id):
        device_id, token = _register_device(tenant_id)
        resp = client.get("/api/local-tools/runtime/invocations/not-a-uuid/payload",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404

    def test_cross_device_404(self, client, tenant_id, weixin_adapter):
        """同租户他设备：不区分存在性，统一 404"""
        device_a, token_a = _register_device(tenant_id)
        _, token_b = _register_device(tenant_id, user_id="owner-2")
        step = _dispatch_first_delivery(tenant_id, device_a, weixin_adapter)
        resp = client.get(f"/api/local-tools/runtime/invocations/{step['invocation_id']}/payload",
                          headers={"Authorization": f"Bearer {token_b}"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "INVOCATION_NOT_FOUND"
        # 字节未被取走
        assert all(c["tenant_id"] == tenant_id and c["payload_ref"] == weixin_adapter.payload_ref
                   for c in weixin_adapter.serve_calls)

    def test_terminal_invocation_refused_409(self, client, tenant_id, weixin_adapter):
        device_id, token = _register_device(tenant_id)
        step = _dispatch_first_delivery(tenant_id, device_id, weixin_adapter)
        invocation_id = step["invocation_id"]
        claim_token = _claim_and_start(tenant_id, device_id, invocation_id)
        repository.write_result(
            invocation_id, tenant_id, sha256_hex(claim_token), False, "EXEC_DONE", "done",
        )
        resp = client.get(f"/api/local-tools/runtime/invocations/{invocation_id}/payload",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "INVOCATION_TERMINATED"

    def test_old_invocation_409(self, client, tenant_id):
        """旧链路 invocation（business_kind NULL）：与 write-authorize 同口径 409"""
        device_id, token = _register_device(tenant_id)
        invocation_id = repository.create_invocation(
            tenant_id, "owner-1", device_id, "boss_goto", {"url": "x"},
        )
        resp = client.get(f"/api/local-tools/runtime/invocations/{invocation_id}/payload",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "NOT_DESKTOP_AUTOMATION"

    def test_not_v2_operation_409(self, client, tenant_id):
        """D1-P2-3：business_kind 正确但缺 protocol_version=2 → 409（write-authorize parity）"""
        device_id, token = _register_device(tenant_id)
        invocation_id = repository.create_invocation(
            tenant_id, "owner-1", device_id, WEIXIN_V2_OPERATION,
            {"operation": WEIXIN_V2_OPERATION},
            business_kind="desktop_automation",
            business_ref={"scenario_key": WEIXIN_SCENARIO_KEY, "task_ref": "task-1",
                          "delivery_id": str(uuid.uuid4())},
        )
        resp = client.get(f"/api/local-tools/runtime/invocations/{invocation_id}/payload",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"] == "NOT_V2_OPERATION"

    def test_scenario_binding_missing_409(self, client, tenant_id, weixin_adapter):
        """D1-P1-1：business_ref 缺 scenario_key/task_ref → 409（fail-closed，不允许跳过
        场景一致性校验继续 resolve）"""
        device_id, token = _register_device(tenant_id)
        payload_ref = f"da:{WEIXIN_SCENARIO_KEY}:frozen-1"
        cases = [
            {"task_ref": "task-1", "delivery_id": str(uuid.uuid4())},   # 缺 scenario_key
            {"scenario_key": WEIXIN_SCENARIO_KEY, "delivery_id": str(uuid.uuid4())},  # 缺 task_ref
            {"delivery_id": str(uuid.uuid4())},                          # 两者皆缺
        ]
        for business_ref in cases:
            invocation_id = repository.create_invocation(
                tenant_id, "owner-1", device_id, WEIXIN_V2_OPERATION,
                {"protocol_version": 2, "payload_ref": payload_ref,
                 "payload_hash": weixin_adapter.payload_hash},
                business_kind="desktop_automation", business_ref=business_ref,
            )
            _force_claimed(tenant_id, invocation_id)
            resp = client.get(f"/api/local-tools/runtime/invocations/{invocation_id}/payload",
                              headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 409, (business_ref, resp.text)
            assert resp.json()["detail"]["error"] == "SCENARIO_BINDING_MISSING"
        # 字节未被取走
        assert weixin_adapter.serve_calls == []

    def test_missing_payload_ref_422(self, client, tenant_id):
        device_id, token = _register_device(tenant_id)
        invocation_id = repository.create_invocation(
            tenant_id, "owner-1", device_id, WEIXIN_V2_OPERATION,
            {"protocol_version": 2, "request_id": "r-1"},
            business_kind="desktop_automation",
            business_ref={"scenario_key": WEIXIN_SCENARIO_KEY, "task_ref": "task-1",
                          "delivery_id": str(uuid.uuid4())},
        )
        _force_claimed(tenant_id, invocation_id)
        resp = client.get(f"/api/local-tools/runtime/invocations/{invocation_id}/payload",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PAYLOAD_REF_MISSING"

    def test_payload_ref_scenario_mismatch_422(self, client, tenant_id):
        """payload_ref 场景与 invocation 绑定场景不一致（防跨场景引用）"""
        device_id, token = _register_device(tenant_id)
        invocation_id = repository.create_invocation(
            tenant_id, "owner-1", device_id, BOSS_V2_OPERATION,
            {"protocol_version": 2, "payload_ref": f"da:{WEIXIN_SCENARIO_KEY}:frozen-1"},
            business_kind="desktop_automation",
            business_ref={"scenario_key": BOSS_SCENARIO_KEY, "task_ref": "task-1",
                          "delivery_id": str(uuid.uuid4())},
        )
        _force_claimed(tenant_id, invocation_id)
        resp = client.get(f"/api/local-tools/runtime/invocations/{invocation_id}/payload",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "PAYLOAD_REF_SCENARIO_MISMATCH"

    def test_invalid_payload_ref_422(self, client, tenant_id):
        device_id, token = _register_device(tenant_id)
        invocation_id = repository.create_invocation(
            tenant_id, "owner-1", device_id, WEIXIN_V2_OPERATION,
            {"protocol_version": 2, "payload_ref": "http://evil.example/x"},
            business_kind="desktop_automation",
            business_ref={"scenario_key": WEIXIN_SCENARIO_KEY, "task_ref": "task-1",
                          "delivery_id": str(uuid.uuid4())},
        )
        _force_claimed(tenant_id, invocation_id)
        resp = client.get(f"/api/local-tools/runtime/invocations/{invocation_id}/payload",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_PAYLOAD_REF"

    def test_hash_mismatch_500_with_audit(self, client, tenant_id, weixin_adapter):
        """invocation 凭据 hash 与适配器当前字节不符 → 500 拒绝 + 审计（不写正文）"""
        device_id, token = _register_device(tenant_id)
        step = _dispatch_first_delivery(tenant_id, device_id, weixin_adapter)
        invocation_id = step["invocation_id"]
        _claim_and_start(tenant_id, device_id, invocation_id)
        # 派发并领取后篡改适配器字节（模拟存储漂移）
        weixin_adapter.payload_bytes = b"tampered-bytes"
        resp = client.get(f"/api/local-tools/runtime/invocations/{invocation_id}/payload",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 500
        assert resp.json()["detail"]["error"] == "PAYLOAD_HASH_MISMATCH"
        assert b"tampered-bytes" not in resp.content  # 字节绝不下发
        audits = audit.list_audits(tenant_id, aggregate_type="invocation",
                                   aggregate_ref=invocation_id)
        mismatch = [a for a in audits if a["kind"] == "payload_hash_mismatch"]
        assert mismatch, "hash 不符必须留审计"
        detail = mismatch[0]["detail"]
        assert detail["payload_ref"] == f"da:{WEIXIN_SCENARIO_KEY}:frozen-1"
        assert detail["expected_hash"] == weixin_adapter.payload_hash
        assert detail["actual_hash"] == hashlib.sha256(b"tampered-bytes").hexdigest()
        # 审计只含哈希与引用，不含字节正文
        assert "tampered-bytes" not in str(mismatch[0])

    def test_unregistered_scenario_500(self, client, tenant_id):
        device_id, token = _register_device(tenant_id)
        invocation_id = repository.create_invocation(
            tenant_id, "owner-1", device_id, WEIXIN_V2_OPERATION,
            {"protocol_version": 2, "payload_ref": "da:ghost.scenario.v1:any"},
            business_kind="desktop_automation",
            business_ref={"scenario_key": "ghost.scenario.v1", "task_ref": "task-1",
                          "delivery_id": str(uuid.uuid4())},
        )
        _force_claimed(tenant_id, invocation_id)
        resp = client.get(f"/api/local-tools/runtime/invocations/{invocation_id}/payload",
                          headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 500
        assert resp.json()["detail"]["error"] == "SCENARIO_NOT_REGISTERED"
