"""v2 operation-result 落账测试（幂等 ACK / 迟到 audit / effect-phase 落 attempt /
delivery 推进 / permit 消费与 quota 落账 / R27 证据判定链 / R28 迟到证据追加）"""

import threading
import uuid

import pytest

from src.desktop_automation import audit, quota, runs
from src.desktop_automation import attempts as da_attempts
from src.desktop_automation import deliveries as da_deliveries
from src.local_tools import operation_result, permits, repository
from src.local_tools.security import sha256_hex
from tests.unit.desktop_automation import harness
from tests.unit.desktop_automation.fakes import (
    FAKE_EVIDENCE_NAMESPACE,
    FakeScenarioAdapter,
    namespaced_evidence_ref,
)

pytestmark = pytest.mark.unit


class TestSubmittedReceipt:
    def test_executor_freezes_trusted_adapter_receipt_fields(self, tenant_id):
        adapter = _evidence_adapter()
        adapter.invocation_receipt_arguments = lambda ctx, target: {"receipt_mode": "submission", "receipt_context": "weixin_name"}
        ctx = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        assert ctx["arguments"]["receipt_mode"] == "submission"
        assert ctx["arguments"]["receipt_context"] == "weixin_name"

    @pytest.mark.parametrize("authorized", [False, True])
    def test_submitted_requires_frozen_scope_and_keeps_phase(self, tenant_id, authorized):
        from psycopg2.extras import Json
        from src.db.database import get_db_connection
        from src.desktop_automation.adapters import TrustedAdapterRegistry
        from src.weixin_conversation.adapters import WeixinConversationAdapter
        ctx = harness.build_running_v2_invocation(tenant_id)
        permit = _authorize(tenant_id, ctx)
        if authorized:
            TrustedAdapterRegistry.register(WeixinConversationAdapter())
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute("""UPDATE local_tool_invocations SET tool_name='weixin_message_send_v2',
                    execution_lane='session_task', arguments_json=arguments_json || %s,
                    business_ref=business_ref || %s WHERE tenant_id=%s AND id=%s""",
                    (Json({"receipt_mode": "submission", "receipt_context": "weixin_name"}),
                     Json({"scenario_key": "weixin.conversation.v1"}), tenant_id, ctx["invocation_id"]))
                conn.commit()
        ref = f"weixin-submission:{ctx['request_id']}:1"
        result = _apply(tenant_id, ctx, effect="applied", phase="submitted", permit=permit, evidence_ref=ref)
        delivery = da_deliveries.get_delivery(str(ctx["delivery"]["id"]), tenant_id)
        assert result["state"] == ("succeeded" if authorized else "unknown")
        assert delivery["phase"] == ("submitted" if authorized else "unknown")
        # Late evidence cannot reclassify either the successful command or unknown.
        duplicate = _apply(tenant_id, ctx, effect="applied", phase="submitted", permit=permit, evidence_ref=ref)
        assert duplicate["late"] and duplicate["state"] == result["state"]

    def test_submitted_without_permit_is_rejected(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        with pytest.raises(operation_result.OperationResultError) as error:
            _apply(tenant_id, ctx, effect="applied", phase="submitted", evidence_ref=f"weixin-submission:{ctx['request_id']}:1")
        assert error.value.code == "PERMIT_REQUIRED"


def _apply(tenant_id, ctx, *, effect, phase, permit=None, request_id=None, **kw):
    return operation_result.apply_operation_result(
        tenant_id=tenant_id,
        device_id=ctx["device_id"],
        invocation_id=ctx["invocation_id"],
        claim_token_hash=sha256_hex(ctx["claim_token"]),
        request_id=request_id or ctx["request_id"],
        effect=effect,
        phase=phase,
        permit_id=permit["permit_id"] if permit else None,
        permit_token=permit["permit_token"] if permit else None,
        **kw,
    )


def _authorize(tenant_id, ctx):
    return permits.write_authorize(
        tenant_id=tenant_id, device_id=ctx["device_id"],
        invocation_id=ctx["invocation_id"],
        claim_token_hash=sha256_hex(ctx["claim_token"]),
        request_id=ctx["request_id"],
        target_version="tv-1", payload_hash=ctx["delivery"].get("payload_hash"),
    )


def _evidence_adapter(**overrides):
    """注入 R27 证据命名空间校验的假适配器（默认 2 操作场景；命名空间格式校验，
    不强制编码本次 request_id——让「他操作证据」到达 INSERT 冲突绑定比对环节）"""
    operations, payloads = harness.default_operations()
    kwargs = dict(
        operations=operations, payloads=payloads, quota_limit=20,
        evidence_namespace=FAKE_EVIDENCE_NAMESPACE,
        evidence_require_request_id=False,
    )
    kwargs.update(overrides)
    return FakeScenarioAdapter(**kwargs)


def _three_op_spec():
    payloads = {"payload:1": b"one", "payload:2": b"two", "payload:3": b"three"}
    operations = [
        {"position": 1, "operation": "fake_op_one", "target_ref": "target-1"},
        {"position": 2, "operation": "fake_op_two", "target_ref": "target-2"},
        {"position": 3, "operation": "fake_op_three", "target_ref": "target-3"},
    ]
    return operations, payloads


def _evidence_rows(tenant_id, attempt_id=None):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT tenant_id, evidence_ref, invocation_id, attempt_id, device_id,
                   request_id, target_ref, payload_hash, effect, phase
            FROM desktop_automation_evidence
            WHERE tenant_id = %s AND (%s::uuid IS NULL OR attempt_id = %s::uuid)
            """,
            (tenant_id, attempt_id, attempt_id),
        )
        return [dict(r) for r in cur.fetchall()]


class TestHappyPath:
    def test_applied_verified_with_permit_full_chain(self, tenant_id):
        """applied+verified+permit：attempt 落结果、delivery succeeded、invocation succeeded、
        quota 落账 reserved→used、run 聚合推进"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        permit = _authorize(tenant_id, ctx)
        ref = namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 1)
        result = _apply(tenant_id, ctx, effect="applied", phase="verified",
                        permit=permit, evidence_ref=ref)
        assert result["acked"] is True and result["late"] is False
        assert result["state"] == "succeeded" and result["effect"] == "applied"

        attempt = da_attempts.get_attempt_by_invocation(ctx["invocation_id"], tenant_id)
        assert attempt["effect"] == "applied" and attempt["phase"] == "verified"
        assert attempt["evidence_ref"] == ref
        assert attempt["finished_at"] is not None
        delivery = da_deliveries.get_delivery(str(ctx["delivery"]["id"]), tenant_id)
        assert delivery["state"] == "succeeded"
        assert delivery["effect"] == "applied" and delivery["phase"] == "verified"
        inv = repository.get_invocation(ctx["invocation_id"], tenant_id)
        assert inv["state"] == "succeeded"
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 1
        # 第 2 条 delivery 仍 pending：run 未到终态（§5.4 全部终态才聚合）
        assert result["run_state"] is None

    def test_unknown_marks_delivery_unknown_run_unknown(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        result = _apply(tenant_id, ctx, effect="unknown", phase="unknown")
        assert result["state"] == "unknown"
        delivery = da_deliveries.get_delivery(str(ctx["delivery"]["id"]), tenant_id)
        assert delivery["state"] == "unknown" and delivery["effect"] == "unknown"
        assert result["run_state"] == "unknown"

    def test_applied_without_verified_evidence_maps_unknown(self, tenant_id):
        """applied 但无写后验证证据（phase=prepared）→ unknown（R10：applied 必须有证据才成功）"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        result = _apply(tenant_id, ctx, effect="applied", phase="prepared")
        assert result["state"] == "unknown"
        delivery = da_deliveries.get_delivery(str(ctx["delivery"]["id"]), tenant_id)
        assert delivery["state"] == "unknown"


class TestIdempotentAck:
    def test_duplicate_result_late_ack_no_rejudge(self, tenant_id):
        """持久 ACK 幂等：重复回执 2xx、late=True、不改判、不重复落账"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        permit = _authorize(tenant_id, ctx)
        ref = namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 1)
        r1 = _apply(tenant_id, ctx, effect="applied", phase="verified", permit=permit,
                    evidence_ref=ref)
        assert r1["late"] is False
        # 完全相同的重复回执（Runtime result outbox 重投场景）
        r2 = _apply(tenant_id, ctx, effect="applied", phase="verified", permit=permit,
                    evidence_ref=ref)
        assert r2["acked"] is True and r2["late"] is True
        assert r2["state"] == "succeeded"  # 原状态透传，不重新判定
        # 迟到的矛盾证据也只审计不改判
        r3 = _apply(tenant_id, ctx, effect="unknown", phase="unknown")
        assert r3["acked"] is True and r3["late"] is True
        assert r3["state"] == "succeeded"
        delivery = da_deliveries.get_delivery(str(ctx["delivery"]["id"]), tenant_id)
        assert delivery["state"] == "succeeded"  # 判定未被覆盖
        # 同操作幂等：重复回执不新增证据登记行
        assert len(_evidence_rows(tenant_id)) == 1
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(a["kind"] == "operation_result_late" for a in audits)

    def test_quota_settled_once_on_duplicate(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        permit = _authorize(tenant_id, ctx)
        ref = namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 1)
        _apply(tenant_id, ctx, effect="applied", phase="verified", permit=permit,
               evidence_ref=ref)
        _apply(tenant_id, ctx, effect="applied", phase="verified", permit=permit,
               evidence_ref=ref)
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["used_count"] == 1  # 重复回执不重复结算


class TestValidation:
    def test_invalid_effect_422(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        with pytest.raises(operation_result.OperationResultError) as e:
            _apply(tenant_id, ctx, effect="applied-but-wrong", phase="verified")
        assert e.value.code == "INVALID_EFFECT" and e.value.http_status == 422

    def test_invalid_phase_422(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        with pytest.raises(operation_result.OperationResultError) as e:
            _apply(tenant_id, ctx, effect="applied", phase="later")
        assert e.value.code == "INVALID_PHASE"

    def test_request_id_mismatch_409(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        with pytest.raises(operation_result.OperationResultError) as e:
            _apply(tenant_id, ctx, effect="none", phase=None, request_id="other-req")
        assert e.value.code == "REQUEST_ID_MISMATCH"

    def test_claim_mismatch_404(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        with pytest.raises(operation_result.OperationResultError) as e:
            operation_result.apply_operation_result(
                tenant_id=tenant_id, device_id=ctx["device_id"],
                invocation_id=ctx["invocation_id"],
                claim_token_hash=sha256_hex("bad" * 20),
                request_id=ctx["request_id"], effect="none", phase=None,
            )
        assert e.value.code == "CLAIM_MISMATCH" and e.value.http_status == 404

    def test_applied_verified_requires_permit(self, tenant_id):
        """受控写动作 applied+verified 必须携带有效 permit（设计 §3）"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        with pytest.raises(operation_result.OperationResultError) as e:
            _apply(tenant_id, ctx, effect="applied", phase="verified")
        assert e.value.code == "PERMIT_REQUIRED"

    def test_permit_binding_invalid_403(self, tenant_id):
        """permit_id 与 invocation 不绑定 / token 不匹配 → 403"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        other_permit = _authorize(tenant_id, ctx)
        # 换一个伪造 permit id
        with pytest.raises(operation_result.OperationResultError) as e:
            _apply(tenant_id, ctx, effect="applied", phase="verified",
                   permit={"permit_id": str(uuid.uuid4()), "permit_token": "x"})
        assert e.value.code == "PERMIT_BINDING_INVALID"
        # 真实 permit 但 token 错误
        with pytest.raises(operation_result.OperationResultError) as e:
            _apply(tenant_id, ctx, effect="applied", phase="verified",
                   permit={"permit_id": other_permit["permit_id"], "permit_token": "t" * 64})
        assert e.value.code == "PERMIT_BINDING_INVALID"


class TestUnknownStopsSubsequent:
    def test_unknown_delivery_skips_remaining_and_partial(self, tenant_id):
        """一条 unknown 停后续：3 条 delivery，#1 verified、#2 unknown → #3 skipped、run partial"""
        payloads = {"payload:1": b"one", "payload:2": b"two", "payload:3": b"three"}
        operations = [
            {"position": 1, "operation": "fake_op_one", "target_ref": "target-1"},
            {"position": 2, "operation": "fake_op_two", "target_ref": "target-2"},
            {"position": 3, "operation": "fake_op_three", "target_ref": "target-3"},
        ]
        ctx = harness.build_running_v2_invocation(
            tenant_id, operations=operations, payloads=payloads,
        )
        # 第 1 条：verified
        permit1 = _authorize(tenant_id, ctx)
        r1 = _apply(tenant_id, ctx, effect="applied", phase="verified", permit=permit1,
                    evidence_ref=namespaced_evidence_ref(
                        FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 1))
        assert r1["run_state"] is None  # 还有后续条目

        # 第 2 条执行 + unknown → 停后续 + partial
        from src.desktop_automation import executor

        run = runs.get_run(str(ctx["run"]["id"]), tenant_id)
        step2 = executor.execute_next_delivery(run)
        assert step2 is not None
        # 模拟设备领取第 2 条并回传 unknown
        token2 = "t" * 64
        repository.claim_next(ctx["device_id"], tenant_id, sha256_hex(token2), 300)
        repository.mark_started(step2["invocation_id"], tenant_id, sha256_hex(token2))
        r2 = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=ctx["device_id"],
            invocation_id=step2["invocation_id"],
            claim_token_hash=sha256_hex(token2),
            request_id=step2["request_id"], effect="unknown", phase="unknown",
        )
        assert r2["run_state"] == "partial"  # 1 成功 + 1 unknown → partial

        deliveries = da_deliveries.list_run_deliveries(str(ctx["run"]["id"]), tenant_id)
        by_pos = {d["position"]: d for d in deliveries}
        assert by_pos[1]["state"] == "succeeded"
        assert by_pos[2]["state"] == "unknown"
        assert by_pos[3]["state"] == "skipped"  # 后续停止
        run = runs.get_run(str(ctx["run"]["id"]), tenant_id)
        assert run["state"] == "partial"

class TestExpiredPermitLateResult:
    """R21：绑定校验通过即接纳，不看 permit 状态——过期许可的迟到 applied/verified/unknown
    一律落账 + audit permit_expired_late（原 403 PERMIT_EXPIRED 语义废除）；
    额度按 R22 迟到结算恰好一次"""

    @staticmethod
    def _force_expire(tenant_id, permit):
        from src.db.database import get_db_connection
        from src.local_tools import permits as permits_mod

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE local_tool_operation_permits SET deadline = NOW() - INTERVAL '1 hour' "
                "WHERE id = %s",
                (permit["permit_id"],),
            )
            conn.commit()
        assert permits_mod.expire_permits() >= 1

    def test_expired_permit_applied_unverified_recorded_unknown(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        permit = _authorize(tenant_id, ctx)
        self._force_expire(tenant_id, permit)
        # 迟到回执 applied 但无 verified 证据：接纳，attempt/delivery 收敛 unknown
        result = _apply(tenant_id, ctx, effect="applied", phase="prepared", permit=permit)
        assert result["acked"] is True and result["state"] == "unknown"
        delivery = da_deliveries.get_delivery(str(ctx["delivery"]["id"]), tenant_id)
        assert delivery["state"] == "unknown"
        attempt = da_attempts.get_attempt_by_invocation(ctx["invocation_id"], tenant_id)
        assert attempt["finished_at"] is not None  # 不悬挂
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(a["kind"] == "permit_expired_late" for a in audits)
        # R22：applied 保守结算（reserved→used，不再释放）
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 1

    def test_expired_permit_unknown_report_recorded(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        permit = _authorize(tenant_id, ctx)
        self._force_expire(tenant_id, permit)
        result = _apply(tenant_id, ctx, effect="unknown", phase="unknown", permit=permit)
        assert result["acked"] is True and result["state"] == "unknown"
        assert result["run_state"] == "unknown"
        # R22：unknown 同样保守结算恰好一次
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 1

    def test_expired_permit_verified_accepted_and_settled(self, tenant_id):
        """R21 推翻旧裁决：过期许可 + applied+verified（带绑定证据）→ 一律落账 succeeded，
        额度照常结算恰好一次"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        permit = _authorize(tenant_id, ctx)
        self._force_expire(tenant_id, permit)
        ref = namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 1)
        result = _apply(tenant_id, ctx, effect="applied", phase="verified", permit=permit,
                        evidence_ref=ref)
        assert result["acked"] is True and result["late"] is False
        assert result["state"] == "succeeded" and result["effect"] == "applied"
        delivery = da_deliveries.get_delivery(str(ctx["delivery"]["id"]), tenant_id)
        assert delivery["state"] == "succeeded"
        attempt = da_attempts.get_attempt_by_invocation(ctx["invocation_id"], tenant_id)
        assert attempt["phase"] == "verified" and attempt["evidence_ref"] == ref
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(a["kind"] == "permit_expired_late" for a in audits)
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 1


class TestVerifiedRequiresEvidence:
    """R27：applied+verified 必须有绑定证据——格式预检（非空/≤512）+ 适配器校验 +
    绑定交叉核对 + evidence 表仲裁；不满足 → delivery 收敛 unknown（机器证据）、
    停止后续条目、audit evidence_invalid"""

    def test_verified_without_evidence_converges_unknown_and_stops(self, tenant_id):
        """无 evidence_ref 的 verified → unknown 且后续停止（#2/#3 skipped）"""
        operations, payloads = _three_op_spec()
        ctx = harness.build_running_v2_invocation(
            tenant_id, operations=operations, payloads=payloads,
        )
        permit = _authorize(tenant_id, ctx)
        result = _apply(tenant_id, ctx, effect="applied", phase="verified", permit=permit)
        assert result["acked"] is True and result["state"] == "unknown"
        delivery = da_deliveries.get_delivery(str(ctx["delivery"]["id"]), tenant_id)
        assert delivery["state"] == "unknown"
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(
            a["kind"] == "evidence_invalid" and a["detail"].get("reason") == "empty"
            for a in audits
        )
        # 停止后续条目 + run 收敛 unknown（无成功 + 任一 unknown）
        rows = da_deliveries.list_run_deliveries(str(ctx["run"]["id"]), tenant_id)
        by_pos = {d["position"]: d for d in rows}
        assert by_pos[2]["state"] == "skipped" and by_pos[3]["state"] == "skipped"
        assert result["run_state"] == "unknown"

    def test_reused_evidence_ref_converges_unknown(self, tenant_id):
        """复用他操作已登记的 evidence_ref → 未知（R27：evidence 表冲突败者绑定比对，
        bound_to_other；适配器仅校验命名空间格式，使外来 ref 到达冲突比对环节）"""
        adapter = _evidence_adapter()
        ctx1 = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit1 = _authorize(tenant_id, ctx1)
        r1 = _apply(tenant_id, ctx1, effect="applied", phase="verified", permit=permit1,
                    evidence_ref=namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx1["request_id"], 1))
        assert r1["state"] == "succeeded"
        # 证据已由首落账登记，绑定 attempt1
        rows = _evidence_rows(tenant_id)
        assert len(rows) == 1 and str(rows[0]["attempt_id"]) == str(ctx1["attempt_id"])
        ctx2 = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit2 = _authorize(tenant_id, ctx2)
        r2 = _apply(tenant_id, ctx2, effect="applied", phase="verified", permit=permit2,
                    evidence_ref=namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx1["request_id"], 1))
        assert r2["acked"] is True and r2["state"] == "unknown"
        delivery2 = da_deliveries.get_delivery(str(ctx2["delivery"]["id"]), tenant_id)
        assert delivery2["state"] == "unknown"
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(
            a["kind"] == "evidence_invalid" and a["detail"].get("reason") == "bound_to_other"
            for a in audits
        )
        # 登记行仍只有一条（败者未落第二行）
        assert len(_evidence_rows(tenant_id)) == 1

    def test_oversized_evidence_ref_converges_unknown(self, tenant_id):
        """evidence_ref 超 512 字符 → unknown（too_long，格式预检）"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        permit = _authorize(tenant_id, ctx)
        result = _apply(tenant_id, ctx, effect="applied", phase="verified", permit=permit,
                        evidence_ref="x" * 513)
        assert result["state"] == "unknown"
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(
            a["kind"] == "evidence_invalid" and a["detail"].get("reason") == "too_long"
            for a in audits
        )


class TestEvidenceAdapterValidation:
    """R27 反例 ①：不存在的证据（乱串/错误命名空间/他 operation 的 request_id 编码）
    → 适配器 validate_evidence 拒绝 → unknown 且停止后续"""

    def test_garbage_evidence_ref_rejected_unknown_and_stops(self, tenant_id):
        operations, payloads = _three_op_spec()
        adapter = _evidence_adapter(operations=operations, payloads=payloads)
        ctx = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit = _authorize(tenant_id, ctx)
        result = _apply(tenant_id, ctx, effect="applied", phase="verified", permit=permit,
                        evidence_ref="not-an-evidence-at-all")
        assert result["acked"] is True and result["state"] == "unknown"
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(
            a["kind"] == "evidence_invalid" and a["detail"].get("reason") == "adapter_rejected"
            for a in audits
        )
        rows = da_deliveries.list_run_deliveries(str(ctx["run"]["id"]), tenant_id)
        by_pos = {d["position"]: d for d in rows}
        assert by_pos[1]["state"] == "unknown"
        assert by_pos[2]["state"] == "skipped" and by_pos[3]["state"] == "skipped"
        assert result["run_state"] == "unknown"
        # 未登记任何证据行
        assert _evidence_rows(tenant_id) == []

    def test_wrong_namespace_evidence_rejected(self, tenant_id):
        """格式合法但命名空间不属于本场景 → adapter_rejected"""
        adapter = _evidence_adapter()
        ctx = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit = _authorize(tenant_id, ctx)
        result = _apply(
            tenant_id, ctx, effect="applied", phase="verified", permit=permit,
            evidence_ref=namespaced_evidence_ref("other-ns", ctx["request_id"], 1),
        )
        assert result["state"] == "unknown"
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(
            a["kind"] == "evidence_invalid" and a["detail"].get("reason") == "adapter_rejected"
            for a in audits
        )

    def test_foreign_request_id_evidence_adapter_rejected(self, tenant_id):
        """R27「其他操作未登记证据」：require_request_id 模式下编码他 operation 的
        request_id → 适配器归属校验拒绝（adapter_rejected）"""
        adapter = _evidence_adapter(evidence_require_request_id=True)
        ctx1 = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit1 = _authorize(tenant_id, ctx1)
        r1 = _apply(
            tenant_id, ctx1, effect="applied", phase="verified", permit=permit1,
            evidence_ref=namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx1["request_id"], 1),
        )
        assert r1["state"] == "succeeded"
        # 证据行绑定 attempt1（编码本次 request_id 才可登记）
        rows = _evidence_rows(tenant_id)
        assert len(rows) == 1 and str(rows[0]["attempt_id"]) == str(ctx1["attempt_id"])

        ctx2 = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit2 = _authorize(tenant_id, ctx2)
        r2 = _apply(
            tenant_id, ctx2, effect="applied", phase="verified", permit=permit2,
            evidence_ref=namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx1["request_id"], 1),
        )
        assert r2["acked"] is True and r2["state"] == "unknown"
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(
            a["kind"] == "evidence_invalid" and a["detail"].get("reason") == "adapter_rejected"
            for a in audits
        )
        assert len(_evidence_rows(tenant_id)) == 1


class TestEvidenceBoundToOther:
    """R27 反例 ②：其他操作的证据——已登记形态与未登记并发形态"""

    def test_registered_other_operation_evidence_rejected(self, tenant_id):
        """已登记形态：attempt1 已登记 ref，attempt2 复用同一 ref → INSERT 冲突败者
        绑定比对 → bound_to_other → unknown"""
        adapter = _evidence_adapter()
        ctx1 = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit1 = _authorize(tenant_id, ctx1)
        shared_ref = namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx1["request_id"], 1)
        r1 = _apply(tenant_id, ctx1, effect="applied", phase="verified", permit=permit1,
                    evidence_ref=shared_ref)
        assert r1["state"] == "succeeded"
        assert len(_evidence_rows(tenant_id)) == 1

        ctx2 = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit2 = _authorize(tenant_id, ctx2)
        r2 = _apply(tenant_id, ctx2, effect="applied", phase="verified", permit=permit2,
                    evidence_ref=shared_ref)
        assert r2["acked"] is True and r2["state"] == "unknown"
        delivery2 = da_deliveries.get_delivery(str(ctx2["delivery"]["id"]), tenant_id)
        assert delivery2["state"] == "unknown"
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(
            a["kind"] == "evidence_invalid" and a["detail"].get("reason") == "bound_to_other"
            for a in audits
        )
        assert len(_evidence_rows(tenant_id)) == 1  # 败者不落第二行

    def test_concurrent_same_evidence_ref_exactly_one_succeeds(self, tenant_id):
        """未登记并发形态（5 连跑）：多线程两 attempt 同时提交同一 evidence_ref——
        DB 唯一索引仲裁恰一胜出（succeeded），败者绑定比对 bound_to_other 收敛
        unknown；两 attempt/delivery 均达终态"""
        adapter = _evidence_adapter()
        for _round in range(5):
            # 每轮：先建 ctx1 并即时授权（后续发布会推进 authorization_epoch，
            # 许可须在 epoch 快照有效期内签发），再建 ctx2；并发的是结果提交
            ctx1 = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
            permit1 = _authorize(tenant_id, ctx1)
            ctx2 = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
            permit2 = _authorize(tenant_id, ctx2)
            shared_ref = namespaced_evidence_ref(
                FAKE_EVIDENCE_NAMESPACE, ctx1["request_id"], 1
            )
            barrier = threading.Barrier(2)
            results: dict = {}

            def _submit(ctx, permit):
                barrier.wait(timeout=10)
                try:
                    results[ctx["invocation_id"]] = _apply(
                        tenant_id, ctx, effect="applied", phase="verified",
                        permit=permit, evidence_ref=shared_ref,
                    )
                except Exception as e:  # noqa: BLE001 断言阶段统一核对
                    results[ctx["invocation_id"]] = e

            threads = [
                threading.Thread(target=_submit, args=(ctx1, permit1)),
                threading.Thread(target=_submit, args=(ctx2, permit2)),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            r1 = results[ctx1["invocation_id"]]
            r2 = results[ctx2["invocation_id"]]
            assert isinstance(r1, dict) and isinstance(r2, dict), (r1, r2)
            assert sorted([r1["state"], r2["state"]]) == ["succeeded", "unknown"]
            # 两者 attempt/delivery 均终态（无悬挂）
            for ctx in (ctx1, ctx2):
                attempt = da_attempts.get_attempt_by_invocation(ctx["invocation_id"], tenant_id)
                assert attempt is not None and attempt["finished_at"] is not None
                delivery = da_deliveries.get_delivery(str(ctx["delivery"]["id"]), tenant_id)
                assert delivery["state"] in ("succeeded", "unknown")
            # 证据行恰一条（本轮 ref），绑定胜者 attempt
            round_rows = [r for r in _evidence_rows(tenant_id) if r["evidence_ref"] == shared_ref]
            assert len(round_rows) == 1
            bound_attempts = {str(ctx1["attempt_id"]), str(ctx2["attempt_id"])}
            assert str(round_rows[0]["attempt_id"]) in bound_attempts
            audits = audit.list_audits(tenant_id, aggregate_type="attempt")
            assert any(
                a["kind"] == "evidence_invalid"
                and a["detail"].get("reason") == "bound_to_other"
                for a in audits
            )


class TestEvidenceBindingCrossCheck:
    """R27 ③ 绑定交叉核对反例：delivery 行与 invocation 冻结描述绑定漂移
    （如 target_ref 被篡改）→ 即使证据命名空间合法也拒绝（mismatch）、零登记、停后续"""

    def test_tampered_delivery_target_ref_mismatch(self, tenant_id):
        from src.db.database import get_db_connection

        operations, payloads = _three_op_spec()
        adapter = _evidence_adapter(operations=operations, payloads=payloads)
        ctx = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit = _authorize(tenant_id, ctx)
        # 篡改 delivery 行 target_ref：与 invocation 冻结操作描述（args.target_ref）不一致
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE desktop_automation_deliveries SET target_ref = %s "
                "WHERE id = %s AND tenant_id = %s",
                ("tampered-target", str(ctx["delivery"]["id"]), tenant_id),
            )
            assert cur.rowcount == 1
            conn.commit()

        result = _apply(
            tenant_id, ctx, effect="applied", phase="verified", permit=permit,
            evidence_ref=namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 1),
        )
        assert result["acked"] is True and result["state"] == "unknown"
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(
            a["kind"] == "evidence_invalid" and a["detail"].get("reason") == "mismatch"
            for a in audits
        )
        # 证据零登记 + 停后续条目 + run 收敛 unknown
        assert _evidence_rows(tenant_id) == []
        rows = da_deliveries.list_run_deliveries(str(ctx["run"]["id"]), tenant_id)
        by_pos = {d["position"]: d for d in rows}
        assert by_pos[1]["state"] == "unknown"
        assert by_pos[2]["state"] == "skipped" and by_pos[3]["state"] == "skipped"
        assert result["run_state"] == "unknown"

    def test_tampered_delivery_payload_hash_mismatch(self, tenant_id):
        """payload_hash 绑定漂移同样拒绝（交叉核对覆盖 target_ref 与 payload_hash 两分量）"""
        from src.db.database import get_db_connection

        adapter = _evidence_adapter()
        ctx = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit = _authorize(tenant_id, ctx)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE desktop_automation_deliveries SET payload_hash = %s "
                "WHERE id = %s AND tenant_id = %s",
                ("deadbeef" * 8, str(ctx["delivery"]["id"]), tenant_id),
            )
            assert cur.rowcount == 1
            conn.commit()

        result = _apply(
            tenant_id, ctx, effect="applied", phase="verified", permit=permit,
            evidence_ref=namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 1),
        )
        assert result["state"] == "unknown"
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(
            a["kind"] == "evidence_invalid" and a["detail"].get("reason") == "mismatch"
            for a in audits
        )
        assert _evidence_rows(tenant_id) == []


class TestLateEvidenceAppend:
    """R28：attempt 已终态的迟到回执——完整绑定校验 + 证据持久追加 + 完整回执审计"""

    def test_unknown_then_late_verified_evidence_registered_and_bound(self, tenant_id):
        """反例 ③：先 unknown → 补 verified 证据回执 → ACK → 证据登记行可按
        tenant/attempt 查询且绑定正确；attempt 原始判定不变"""
        adapter = _evidence_adapter(evidence_require_request_id=True)
        ctx = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit = _authorize(tenant_id, ctx)
        r1 = _apply(tenant_id, ctx, effect="unknown", phase="unknown")
        assert r1["state"] == "unknown"
        assert _evidence_rows(tenant_id) == []

        late_ref = namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 7)
        r2 = _apply(
            tenant_id, ctx, effect="applied", phase="verified", permit=permit,
            evidence_ref=late_ref, safe_to_retry=True, code="LATE", message="迟到补证据",
        )
        assert r2["acked"] is True and r2["late"] is True
        assert r2["state"] == "unknown"  # 原始判定不覆盖

        rows = _evidence_rows(tenant_id, attempt_id=ctx["attempt_id"])
        assert len(rows) == 1
        row = rows[0]
        assert row["evidence_ref"] == late_ref
        assert str(row["invocation_id"]) == str(ctx["invocation_id"])
        assert row["request_id"] == ctx["request_id"]
        assert row["target_ref"] == ctx["delivery"]["target_ref"]
        assert row["payload_hash"] == ctx["delivery"]["payload_hash"]
        assert row["effect"] == "applied" and row["phase"] == "verified"

        # attempt 原始判定保留（unknown），delivery 不改判
        attempt = da_attempts.get_attempt_by_invocation(ctx["invocation_id"], tenant_id)
        assert attempt["effect"] == "unknown" and attempt["phase"] == "unknown"
        delivery = da_deliveries.get_delivery(str(ctx["delivery"]["id"]), tenant_id)
        assert delivery["state"] == "unknown"

        # 迟到审计持久化完整受控回执字段
        late_audits = [
            a for a in audit.list_audits(tenant_id, aggregate_type="attempt")
            if a["kind"] == "operation_result_late"
        ]
        assert late_audits
        detail = late_audits[0]["detail"]
        for field in ("evidence_ref", "permit_id", "request_id", "effect", "phase",
                      "safe_to_retry", "code", "message", "received_at"):
            assert field in detail, field
        assert detail["evidence_ref"] == late_ref
        assert detail["evidence_registered"] is True

    def test_late_receipt_permit_binding_mismatch_403(self, tenant_id):
        """反例 ④：绑定不匹配的迟到回执 → 403 PERMIT_BINDING_INVALID"""
        adapter = _evidence_adapter()
        ctx = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit = _authorize(tenant_id, ctx)
        r1 = _apply(
            tenant_id, ctx, effect="applied", phase="verified", permit=permit,
            evidence_ref=namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 1),
        )
        assert r1["state"] == "succeeded"

        # 伪造 permit id
        with pytest.raises(operation_result.OperationResultError) as e:
            _apply(tenant_id, ctx, effect="applied", phase="verified",
                   permit={"permit_id": str(uuid.uuid4()), "permit_token": "x" * 64},
                   evidence_ref=namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 2))
        assert e.value.code == "PERMIT_BINDING_INVALID"
        assert e.value.http_status == 403
        # 真实 permit 但 token 不匹配
        with pytest.raises(operation_result.OperationResultError) as e:
            _apply(tenant_id, ctx, effect="applied", phase="verified",
                   permit={"permit_id": permit["permit_id"], "permit_token": "t" * 64},
                   evidence_ref=namespaced_evidence_ref(FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 2))
        assert e.value.code == "PERMIT_BINDING_INVALID"

        # 原判定与证据登记不被迟到 403 回执影响
        attempt = da_attempts.get_attempt_by_invocation(ctx["invocation_id"], tenant_id)
        assert attempt["effect"] == "applied" and attempt["phase"] == "verified"
        rows = _evidence_rows(tenant_id)
        assert len(rows) == 1  # 仅首落账登记，403 回执未追加

    def test_late_invalid_evidence_not_registered_but_acked(self, tenant_id):
        """迟到回执携带乱串证据：登记被拒（audit evidence_invalid），但原判定不变、
        仍 ACK（对账留痕，不改判不悬挂）"""
        adapter = _evidence_adapter()
        ctx = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        permit = _authorize(tenant_id, ctx)
        r1 = _apply(tenant_id, ctx, effect="unknown", phase="unknown")
        assert r1["state"] == "unknown"

        r2 = _apply(tenant_id, ctx, effect="applied", phase="verified", permit=permit,
                    evidence_ref="garbage-late-evidence")
        assert r2["acked"] is True and r2["late"] is True
        assert _evidence_rows(tenant_id) == []
        audits = audit.list_audits(tenant_id, aggregate_type="attempt")
        assert any(
            a["kind"] == "evidence_invalid"
            and a["detail"].get("reason") == "adapter_rejected"
            and a["detail"].get("late") is True
            for a in audits
        )
        late = [a for a in audits if a["kind"] == "operation_result_late"]
        assert late and late[0]["detail"]["evidence_registered"] is False
