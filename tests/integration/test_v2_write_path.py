"""v2 写路径服务端契约测试（P1-C）：write-authorize → operation-result 与 Runtime 语义对齐。

覆盖（宪章 P1-C 交付物 6，参照 test_desktop_automation_api.py 的 fixture 模式）：
- Runtime 实际发送的 payload 形态（含 success/data/retryable 附加字段）服务端必须接受
- 幂等 ACK：重复回执 2xx late=True 不改判；迟到只 audit（operation_result_late）
- permit 过期收敛（R21）：绑定校验通过即接纳——expired permit + applied/may_have_started
  → unknown；expired permit + applied/verified（带绑定证据）→ 一律落账 succeeded +
  audit permit_expired_late + 额度迟到结算（R22）
- 额度只结算一次：authorize 预留 → operation-result 落账 reserved→used；重复回执不再结算
"""

import uuid
from datetime import datetime, timedelta, timezone

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
    """注册假场景适配器（模块级共享租户：R19 后 used 计入占用，额度须容纳全部用例
    的 authorize+settle——额度结算断言走 before/after 快照差分，不依赖小 limit）"""
    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from tests.unit.desktop_automation.fakes import FakeScenarioAdapter

    adapter = FakeScenarioAdapter(
        operations=[
            {"position": 1, "operation": "fake_op_one", "target_ref": "target-1",
             "provider_key": FAKE_PROVIDER_KEY},
            {"position": 2, "operation": "fake_op_two", "target_ref": "target-2",
             "payload_ref": "payload:2", "provider_key": FAKE_PROVIDER_KEY},
        ],
        payloads={"payload:1": b"one", "payload:2": b"two"},
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
def tenant():
    """单租户 + 单用户（本文件用例相互独立 task；测后物理清理）"""
    from src.saas.db.tenant_db import TenantDB

    code = f"V{uuid.uuid4().hex[:6].upper()}"
    tenant_row = TenantDB.create(
        company_name=f"v2写路径测试-{code}", tenant_code=code,
        contact_name="测试", contact_phone="13800000000",
    )
    if not tenant_row:
        pytest.skip("无法创建测试租户（DB 不可用）")
    tenant_id = tenant_row["tenant_id"]

    from src.api.auth import generate_token
    from src.db.models import UserDB

    phone = f"199{uuid.uuid4().int % 10**8:08d}"
    user = UserDB.create(phone=phone, username="v2写路径测试用户", tenant_id=tenant_id)
    if not user:
        pytest.skip("无法创建测试用户（DB 不可用）")
    token = generate_token(user["user_id"])

    yield {"tenant_id": tenant_id, "user_id": user["user_id"], "token": token}

    from src.db.database import get_db_connection
    from tests.unit.desktop_automation.conftest import DA_TABLES

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


def _pair_device(client, user):
    resp = client.post("/api/local-tools/pairing-tickets",
                       headers={"Authorization": f"Bearer {user['token']}"})
    assert resp.status_code == 200, resp.text
    code = resp.json()["code"]
    resp = client.post(
        "/api/local-tools/runtime/pair",
        json={
            "code": code,
            "name": "v2写路径机",
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


def _utcnow():
    return datetime.now(timezone.utc)


def _claim_started(client, runtime_headers, invocation_id):
    """claim 指定 invocation 并 started（返回 claim 回包）"""
    resp = client.post("/api/local-tools/runtime/claim?wait=3", headers=runtime_headers)
    assert resp.status_code == 200, resp.text
    claim = resp.json()
    assert claim["invocation_id"] == invocation_id
    resp = client.post(
        f"/api/local-tools/runtime/invocations/{invocation_id}/started",
        json={"claim_token": claim["claim_token"]}, headers=runtime_headers,
    )
    assert resp.status_code == 200, resp.text
    return claim


def _setup_step(tenant, device_id, adapter, task_ref):
    """发布场景 → 手动触发 → claim+prepare → 派发第 1 条（独立 task，互不影响）"""
    from src.desktop_automation import executor, occurrences, subjects

    subjects.publish_revision(
        tenant["tenant_id"], adapter.scenario_key, task_ref, "rev-1", tenant["user_id"],
        revision_config={}, schedule_specs=[],
    )
    occurrences.accept_manual_trigger(
        tenant_id=tenant["tenant_id"], scenario_key=adapter.scenario_key, task_ref=task_ref,
        request_id=f"vw-{uuid.uuid4().hex[:8]}", user_id=tenant["user_id"], now=_utcnow(),
    )
    prepared = executor.claim_and_prepare_run(
        revision_config={}, device_id=device_id, tenant_id=tenant["tenant_id"],
    )
    assert prepared and prepared["prepared"] is True, prepared
    step = executor.execute_next_delivery(prepared["run"])
    assert step is not None
    return step


def _runtime_payload(claim, args, effect, phase, permit=None, **extra):
    """Runtime 语义对齐 payload：服务端契约字段 + Runtime 实际会附带的 success/data/retryable
    附加字段（Pydantic 默认忽略 extras——此处同时验证该假设，否则 Runtime 回执全 422）"""
    payload = {
        "claim_token": claim["claim_token"],
        "request_id": args["request_id"],
        "effect": effect,
        "phase": phase,
        "safe_to_retry": False,
        # R27：命名空间证据（v2-fake-evidence:<本次 request_id>:<seq>）——
        # 模块级共享租户下按 invocation 的 request_id 天然唯一，防跨用例撞车
        "evidence_ref": namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, args["request_id"], 1),
        "success": effect == "applied" and phase == "verified",
        "code": "OK",
        "message": "fake v2 完成",
        "data": {"echo_tool": "fake", "request_id": args["request_id"]},
        "retryable": False,
    }
    if permit:
        payload["permit_id"] = permit["permit_id"]
        payload["permit_token"] = permit["permit_token"]
    payload.update(extra)
    return payload


def _authorize(client, runtime_headers, invocation_id, claim, args):
    resp = client.post(
        f"/api/local-tools/runtime/invocations/{invocation_id}/write-authorize",
        json={
            "claim_token": claim["claim_token"],
            "request_id": args["request_id"],
            "target_version": args.get("target_version"),
            "payload_hash": args.get("payload_hash"),
        },
        headers=runtime_headers,
    )
    assert resp.status_code == 200, resp.text
    permit = resp.json()
    assert permit["permit_id"] and permit["permit_token"] and permit["deadline_at"]
    return permit


def _post_result(client, runtime_headers, invocation_id, payload):
    return client.post(
        f"/api/local-tools/runtime/invocations/{invocation_id}/operation-result",
        json=payload, headers=runtime_headers,
    )


class TestIdempotentAckAndLateAudit:
    def test_duplicate_receipt_idempotent_and_late_audit(
        self, client, tenant, fake_provider, fake_adapter
    ):
        """幂等 ACK：重复回执 2xx late=True 不改判；迟到只 audit（operation_result_late）"""
        device_id, device_token = _pair_device(client, tenant)
        runtime = {"Authorization": f"Bearer {device_token}"}
        step = _setup_step(tenant, device_id, fake_adapter, "task-idem")
        invocation_id = step["invocation_id"]
        claim = _claim_started(client, runtime, invocation_id)
        args = claim["arguments"]
        permit = _authorize(client, runtime, invocation_id, claim, args)

        # 首次回执：applied+verified+permit → succeeded（附加字段不阻断）
        resp = _post_result(client, runtime, invocation_id,
                            _runtime_payload(claim, args, "applied", "verified", permit))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "succeeded" and body["effect"] == "applied"
        assert body["late"] is False
        assert "run_state" in body  # 响应契约含 run_state 字段（值语义归 runs 聚合测试管）

        # 重复回执（内容不同也不改判）：2xx + late=True，state 不变
        resp = _post_result(client, runtime, invocation_id,
                            _runtime_payload(claim, args, "unknown", "unknown"))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["late"] is True
        assert body["state"] == "succeeded" and body["effect"] == "applied"

        # 迟到只对账：audit 追加 operation_result_late（不改 attempt）
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS n FROM desktop_automation_audit_events "
                "WHERE tenant_id = %s AND kind = 'operation_result_late'",
                (tenant["tenant_id"],),
            )
            assert cur.fetchone()["n"] >= 1, "迟到回执必须落 operation_result_late 审计"


def _expire_permit_via_sweeper(tenant_id, permit_id):
    """走真实路径把许可置过期：deadline 先到（UPDATE 到过去）→ 清扫（state issued→expired，
    预留保留——R22 不释放）。模拟「清扫已跑、Runtime 迟到回执」的真实时序，不能只改 deadline。
    """
    from src.db.database import get_db_connection
    from src.local_tools import permits

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE local_tool_operation_permits SET deadline = %s "
            "WHERE id = %s AND tenant_id = %s AND state = 'issued'",
            (_utcnow() - timedelta(seconds=1), permit_id, tenant_id),
        )
        assert cur.rowcount == 1
        conn.commit()

    expired = permits.expire_permits(now=_utcnow())
    assert expired >= 1, "清扫必须把测试许可置为 expired"
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT state FROM local_tool_operation_permits WHERE id = %s AND tenant_id = %s",
            (permit_id, tenant_id),
        )
        assert cur.fetchone()["state"] == "expired"


class TestPermitExpiredConvergence:
    def test_expired_permit_unknown_result_recorded_not_dropped(
        self, client, tenant, fake_provider, fake_adapter
    ):
        """permit 过期 + applied/may_have_started → 收敛 unknown（不 403 丢弃 attempt）"""
        device_id, device_token = _pair_device(client, tenant)
        runtime = {"Authorization": f"Bearer {device_token}"}
        step = _setup_step(tenant, device_id, fake_adapter, "task-exp1")
        invocation_id = step["invocation_id"]
        claim = _claim_started(client, runtime, invocation_id)
        args = claim["arguments"]
        permit = _authorize(client, runtime, invocation_id, claim, args)
        _expire_permit_via_sweeper(tenant["tenant_id"], permit["permit_id"])

        resp = _post_result(client, runtime, invocation_id,
                            _runtime_payload(claim, args, "applied", "may_have_started", permit))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "unknown" and body["effect"] == "unknown", (
            "过期许可的 applied 无 verified 证据必须收敛 unknown，不得 403 让 attempt 悬挂"
        )

    def test_expired_permit_verified_result_accepted_and_settled(
        self, client, tenant, fake_provider, fake_adapter
    ):
        """R21 推翻旧 403 裁决：permit 过期 + applied/verified（带绑定证据）→ 接纳落账
        succeeded；R22：额度迟到结算 reserved→used；audit permit_expired_late"""
        device_id, device_token = _pair_device(client, tenant)
        runtime = {"Authorization": f"Bearer {device_token}"}
        step = _setup_step(tenant, device_id, fake_adapter, "task-exp2")
        invocation_id = step["invocation_id"]
        claim = _claim_started(client, runtime, invocation_id)
        args = claim["arguments"]
        permit = _authorize(client, runtime, invocation_id, claim, args)
        _expire_permit_via_sweeper(tenant["tenant_id"], permit["permit_id"])

        resp = _post_result(client, runtime, invocation_id,
                            _runtime_payload(claim, args, "applied", "verified", permit))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "succeeded" and body["effect"] == "applied"
        assert body["late"] is False

        # R22：过期许可的迟到结算恰好一次（permit → consumed）
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT state FROM local_tool_operation_permits "
                "WHERE id = %s AND tenant_id = %s",
                (permit["permit_id"], tenant["tenant_id"]),
            )
            assert cur.fetchone()["state"] == "consumed"
            cur.execute(
                "SELECT COUNT(*) AS n FROM desktop_automation_audit_events "
                "WHERE tenant_id = %s AND kind = 'permit_expired_late'",
                (tenant["tenant_id"],),
            )
            assert cur.fetchone()["n"] >= 1, "过期许可迟到回执必须落 permit_expired_late 审计"


class TestQuotaSettledOnce:
    def test_quota_reserved_then_settled_exactly_once(
        self, client, tenant, fake_provider, fake_adapter
    ):
        """额度只结算一次：authorize 预留 → result 落账 reserved→used；重复回执不再结算"""
        device_id, device_token = _pair_device(client, tenant)
        runtime = {"Authorization": f"Bearer {device_token}"}
        step = _setup_step(tenant, device_id, fake_adapter, "task-quota")
        invocation_id = step["invocation_id"]
        claim = _claim_started(client, runtime, invocation_id)
        args = claim["arguments"]

        from src.db.database import get_db_connection

        def quota_rows():
            """快照键为 (scope_type, scope_id)——task scope_id 含 task_ref，不同用例不共用桶"""
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT scope_type, scope_id, reserved_count, used_count "
                    "FROM desktop_automation_quota_buckets WHERE tenant_id = %s "
                    "ORDER BY scope_type, scope_id",
                    (tenant["tenant_id"],),
                )
                return {
                    (r["scope_type"], r["scope_id"]): (r["reserved_count"], r["used_count"])
                    for r in cur.fetchall()
                }

        def delta(snapshot_a, snapshot_b):
            d = {}
            for key in set(snapshot_a) | set(snapshot_b):
                a = snapshot_a.get(key, (0, 0))
                b = snapshot_b.get(key, (0, 0))
                diff = (b[0] - a[0], b[1] - a[1])
                if diff != (0, 0):
                    d[key] = diff
            return d

        before = quota_rows()
        permit = _authorize(client, runtime, invocation_id, claim, args)
        after_auth = quota_rows()
        # authorize 预留：tenant 桶 + 本用例 task 桶各恰好 reserved+1（used 不动）
        changed = delta(before, after_auth)
        assert set(changed) == {
            ("tenant", tenant["tenant_id"]),
            ("task", f"{fake_adapter.scenario_key}:task-quota"),
        }, f"改动桶集合不符: {changed}"
        assert all(v == (1, 0) for v in changed.values()), changed

        resp = _post_result(client, runtime, invocation_id,
                            _runtime_payload(claim, args, "applied", "verified", permit))
        assert resp.status_code == 200, resp.text
        after_result = quota_rows()
        # result 落账：两个桶 reserved-1 / used+1
        settled = delta(after_auth, after_result)
        assert all(v == (-1, 1) for v in settled.values()), settled

        # 重复回执（late）：额度不得再次结算
        resp = _post_result(client, runtime, invocation_id,
                            _runtime_payload(claim, args, "applied", "verified", permit))
        assert resp.status_code == 200 and resp.json()["late"] is True
        after_dup = quota_rows()
        assert after_dup == after_result, "重复回执不得重复结算额度"

        # 许可消费终态：consumed（幂等，不重复结算）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT state FROM local_tool_operation_permits "
                "WHERE id = %s AND tenant_id = %s",
                (permit["permit_id"], tenant["tenant_id"]),
            )
            assert cur.fetchone()["state"] == "consumed"


class TestLateEvidenceAppendContract:
    """R28：attempt 已终态的迟到回执——证据持久追加登记 + 完整回执字段审计；
    绑定不匹配的迟到回执 403 PERMIT_BINDING_INVALID（原始判定不变）"""

    def test_late_verified_evidence_appended_and_bound(
        self, client, tenant, fake_provider, fake_adapter
    ):
        """先 unknown → 补 verified 证据回执 → 2xx ACK → 证据登记行可按
        tenant/attempt 查询且绑定正确（invocation/request/effect/phase）"""
        device_id, device_token = _pair_device(client, tenant)
        runtime = {"Authorization": f"Bearer {device_token}"}
        step = _setup_step(tenant, device_id, fake_adapter, "task-late-ev")
        invocation_id = step["invocation_id"]
        claim = _claim_started(client, runtime, invocation_id)
        args = claim["arguments"]
        permit = _authorize(client, runtime, invocation_id, claim, args)

        # 首回执 unknown（无写后证据）→ attempt 终态 unknown
        resp = _post_result(client, runtime, invocation_id,
                            _runtime_payload(claim, args, "unknown", "unknown"))
        assert resp.status_code == 200, resp.text
        assert resp.json()["state"] == "unknown"

        # 迟到补 verified 证据回执：ACK + 证据登记，判定不被覆盖
        late_payload = _runtime_payload(claim, args, "applied", "verified", permit)
        resp = _post_result(client, runtime, invocation_id, late_payload)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["late"] is True and body["state"] == "unknown"

        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ev.evidence_ref, ev.attempt_id, ev.invocation_id, ev.request_id,
                       ev.target_ref, ev.payload_hash, ev.effect, ev.phase,
                       (SELECT invocation_id FROM desktop_automation_attempts a
                        WHERE a.id = ev.attempt_id) AS attempt_invocation
                FROM desktop_automation_evidence ev
                WHERE ev.tenant_id = %s AND ev.invocation_id = %s
                """,
                (tenant["tenant_id"], invocation_id),
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute(
                """
                SELECT detail FROM desktop_automation_audit_events
                WHERE tenant_id = %s AND kind = 'operation_result_late'
                  AND (detail->>'invocation_id') = %s
                ORDER BY id DESC LIMIT 1
                """,
                (tenant["tenant_id"], invocation_id),
            )
            late_audit = dict(cur.fetchone())

        assert len(rows) == 1, rows
        row = rows[0]
        assert row["evidence_ref"] == late_payload["evidence_ref"]
        assert str(row["attempt_invocation"]) == invocation_id  # 绑定本 invocation 的 attempt
        assert row["request_id"] == args["request_id"]
        assert row["target_ref"] == args["target_ref"]
        assert row["payload_hash"] == args.get("payload_hash")
        assert row["effect"] == "applied" and row["phase"] == "verified"
        # R28：迟到审计持久化完整受控回执字段
        detail = late_audit["detail"]
        for field in ("evidence_ref", "permit_id", "request_id", "effect", "phase",
                      "safe_to_retry", "code", "message", "received_at"):
            assert field in detail, field
        assert detail["evidence_ref"] == late_payload["evidence_ref"]
        assert detail["evidence_registered"] is True

    def test_late_receipt_wrong_permit_403(self, client, tenant, fake_provider, fake_adapter):
        """绑定不匹配的迟到回执 → 403 PERMIT_BINDING_INVALID（伪造 permit id / token 错）"""
        device_id, device_token = _pair_device(client, tenant)
        runtime = {"Authorization": f"Bearer {device_token}"}
        step = _setup_step(tenant, device_id, fake_adapter, "task-late-403")
        invocation_id = step["invocation_id"]
        claim = _claim_started(client, runtime, invocation_id)
        args = claim["arguments"]
        permit = _authorize(client, runtime, invocation_id, claim, args)

        resp = _post_result(client, runtime, invocation_id,
                            _runtime_payload(claim, args, "applied", "verified", permit))
        assert resp.status_code == 200 and resp.json()["state"] == "succeeded"

        # 伪造 permit id 的迟到回执 → 403
        resp = _post_result(client, runtime, invocation_id,
                            _runtime_payload(
                                claim, args, "applied", "verified",
                                permit={"permit_id": str(uuid.uuid4()), "permit_token": "f" * 64},
                            ))
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"]["error"] == "PERMIT_BINDING_INVALID"

        # 真实 permit 但 token 不匹配 → 403
        resp = _post_result(client, runtime, invocation_id,
                            _runtime_payload(
                                claim, args, "applied", "verified",
                                permit={"permit_id": permit["permit_id"], "permit_token": "t" * 64},
                            ))
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"] == "PERMIT_BINDING_INVALID"
