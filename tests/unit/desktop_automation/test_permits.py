"""写动作许可测试（§5.2/R8/R9：全生命周期、全部拒绝原因、quota 回滚、过期清扫）"""

import uuid

import pytest

from src.db.database import get_db_connection
from src.desktop_automation import audit, quota, subjects
from src.local_tools import permits, repository
from src.local_tools.security import sha256_hex
from tests.unit.desktop_automation import harness
from tests.unit.desktop_automation.fakes import (
    FAKE_EVIDENCE_NAMESPACE,
    FakeScenarioAdapter,
    namespaced_evidence_ref,
)

pytestmark = pytest.mark.unit


def _authorize(tenant_id, ctx, **overrides):
    kwargs = dict(
        tenant_id=tenant_id,
        device_id=ctx["device_id"],
        invocation_id=ctx["invocation_id"],
        claim_token_hash=sha256_hex(ctx["claim_token"]),
        request_id=ctx["request_id"],
        target_version="tv-1",
        payload_hash=(ctx["delivery"].get("payload_hash")),
    )
    kwargs.update(overrides)
    return permits.write_authorize(**kwargs)


class TestLifecycle:
    def test_issue_and_consume(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        result = _authorize(tenant_id, ctx)
        assert result["permit_id"] and result["permit_token"] and result["deadline_at"]
        # 许可发放：delivery 置 may_have_started + invocation 回写 + quota 预留 + audit
        from src.desktop_automation import deliveries as da_deliveries

        delivery = da_deliveries.get_delivery(str(ctx["delivery"]["id"]), tenant_id)
        assert delivery["phase"] == "may_have_started"
        inv = repository.get_invocation(ctx["invocation_id"], tenant_id)
        assert inv["write_phase"] == "may_have_started"
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 1
        audits = audit.list_audits(tenant_id, aggregate_type="permit")
        assert any(a["kind"] == "permit_issued" for a in audits)

        # 结算：issued→consumed + quota 落账 reserved→used
        with get_db_connection() as conn:
            cursor = conn.cursor()
            assert permits.settle_permit_on_result(
                cursor, result["permit_id"], tenant_id,
                effect="applied", phase="verified",
            ) == "settled"
            # 幂等：二次结算返回 None，不重复落账
            assert permits.settle_permit_on_result(
                cursor, result["permit_id"], tenant_id,
                effect="applied", phase="verified",
            ) is None
            conn.commit()
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 1

    def test_settle_none_prepared_releases_quota(self, tenant_id):
        """R22：effect=none 且 phase=prepared（可信未执行）→ release 而非 settle"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        result = _authorize(tenant_id, ctx)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            assert permits.settle_permit_on_result(
                cursor, result["permit_id"], tenant_id,
                effect="none", phase="prepared",
            ) == "released"
            conn.commit()
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 0

    def test_permit_token_only_hash_stored(self, tenant_id):
        """token 明文只返回一次，库里只有 hash"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        result = _authorize(tenant_id, ctx)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT permit_token_hash FROM local_tool_operation_permits WHERE id = %s",
                (result["permit_id"],),
            )
            stored = cur.fetchone()["permit_token_hash"]
        assert stored == sha256_hex(result["permit_token"])
        assert result["permit_token"] != stored


class TestDenyPaths:
    def test_old_invocation_rejected_409(self, tenant_id):
        """R8：旧 invocation（business_kind NULL）调用 write-authorize 返回 409"""
        from src.local_tools.service import LocalInvocationService

        device_id = str(uuid.uuid4())
        invocation = LocalInvocationService().enqueue(
            tenant_id=tenant_id, user_id="owner-1", device_id=device_id,
            tool_name="boss_goto", arguments={"url": "x"},
        )
        token = "t" * 64
        repository.claim_next(device_id, tenant_id, sha256_hex(token), 300)
        repository.mark_started(str(invocation["id"]), tenant_id, sha256_hex(token))
        with pytest.raises(permits.PermitError) as e:
            permits.write_authorize(
                tenant_id=tenant_id, device_id=device_id,
                invocation_id=str(invocation["id"]),
                claim_token_hash=sha256_hex(token), request_id="r1",
                target_version=None, payload_hash=None,
            )
        assert e.value.code == "NOT_DESKTOP_AUTOMATION"
        assert e.value.http_status == 409

    def test_claim_mismatch_404(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx, claim_token_hash=sha256_hex("wrong" * 12))
        assert e.value.code == "CLAIM_MISMATCH" and e.value.http_status == 404

    def test_device_mismatch_403(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx, device_id=str(uuid.uuid4()))
        assert e.value.code == "DEVICE_MISMATCH" and e.value.http_status == 403

    def test_not_running_409(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        # 推回 queued 状态模拟未 started
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE local_tool_invocations SET state='claimed' WHERE id=%s",
                (ctx["invocation_id"],),
            )
            conn.commit()
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx)
        assert e.value.code == "INVOCATION_NOT_RUNNING" and e.value.http_status == 409

    def test_cancelled_409(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        repository.request_cancel(ctx["invocation_id"], tenant_id)
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx)
        assert e.value.code == "INVOCATION_CANCELLED"

    def test_lease_expired_409(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE local_tool_invocations SET lease_expires_at = NOW() - INTERVAL '1 hour' "
                "WHERE id=%s",
                (ctx["invocation_id"],),
            )
            conn.commit()
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx)
        assert e.value.code == "LEASE_EXPIRED"

    def test_deadline_exceeded_409(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        # invocation deadline_at 置为明确过去（避开客户端/DB 时钟微小偏差）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE local_tool_invocations SET deadline_at = NOW() - INTERVAL '1 hour' "
                "WHERE id=%s",
                (ctx["invocation_id"],),
            )
            conn.commit()
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx)
        assert e.value.code == "DEADLINE_EXCEEDED"

    def test_request_id_mismatch_409(self, tenant_id):
        ctx = harness.build_running_v2_invocation(tenant_id)
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx, request_id="not-the-request-id")
        assert e.value.code == "REQUEST_ID_MISMATCH"

    def test_payload_hash_mismatch_409(self, tenant_id):
        """许可绑定 payload_hash：请求值与 invocation 不一致拒绝（防替换载荷）"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx, payload_hash="deadbeef")
        assert e.value.code == "PAYLOAD_HASH_MISMATCH"

    def test_adapter_denied_403(self, tenant_id):
        """适配器拒绝许可签发（executor 预检放行、许可事务内拒绝——决策队列区分两次调用）"""
        from src.desktop_automation.adapters import AuthorizeDecision

        operations, payloads = harness.default_operations()
        adapter = FakeScenarioAdapter(
            operations=operations, payloads=payloads,
            authorize_decisions=[
                AuthorizeDecision(allowed=True),                 # 第 1 次：executor 预检
                AuthorizeDecision(allowed=False, reason="no-auth"),  # 第 2 次：许可事务
            ],
        )
        ctx = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx)
        assert e.value.code == "ADAPTER_DENIED" and e.value.http_status == 403

    def test_paused_task_rejects_permit(self, tenant_id):
        """暂停推进 epoch：许可事务锁定 subject 复验，拒绝新许可"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        subjects.pause_task(tenant_id, ctx["adapter"].scenario_key, "task-1")
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx)
        assert e.value.code == "TASK_NOT_ACTIVE"

    def test_duplicate_permit_409(self, tenant_id):
        """幂等拒绝重复许可：同 delivery 已有 issued → 409"""
        ctx = harness.build_running_v2_invocation(tenant_id)
        assert _authorize(tenant_id, ctx)["permit_id"]
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx)
        assert e.value.code == "PERMIT_ALREADY_ISSUED"


class TestQuotaRollback:
    def test_quota_exhausted_rolls_back_whole_tx(self, tenant_id):
        """额度不足：许可事务整体回滚——无 permit 行、delivery 不置 may_have_started"""
        from src.desktop_automation.adapters import AuthorizeDecision, QuotaScopeSpec

        # 第一条链路 limit=1：许可签发后 tenant/task 层各占用 1
        ctx1 = harness.build_running_v2_invocation(tenant_id, quota_limit=1)
        assert _authorize(tenant_id, ctx1)["permit_id"]
        # 第二条链路：预检决策无额度层级（直接放行），许可事务决策 tenant 层 limit=1
        # （该桶已被 ctx1 预留占满）→ 预留不足整体回滚
        operations, payloads = harness.default_operations()
        adapter = FakeScenarioAdapter(
            operations=operations, payloads=payloads,
            authorize_decisions=[
                AuthorizeDecision(allowed=True),  # executor 预检：无额度层级，放行
                AuthorizeDecision(allowed=True, quota_scopes=[
                    QuotaScopeSpec(scope_type="tenant", scope_id=tenant_id,
                                   limit_count=1, window_seconds=3600),
                ]),
            ],
        )
        ctx2 = harness.build_running_v2_invocation(tenant_id, adapter=adapter)
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx2)
        assert e.value.code == "QUOTA_EXCEEDED"
        from src.desktop_automation import deliveries as da_deliveries

        delivery = da_deliveries.get_delivery(str(ctx2["delivery"]["id"]), tenant_id)
        assert delivery["phase"] != "may_have_started"
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM local_tool_operation_permits WHERE tenant_id=%s "
                "AND delivery_id=%s",
                (tenant_id, str(ctx2["delivery"]["id"])),
            )
            assert cur.fetchone()["c"] == 0
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 1  # 仅 ctx1 的预留保留，无半预留


class TestExpireSweep:
    """R22：清扫置 expired 但不释放预留——遗弃预留随窗口翻页失效；迟到回执按 effect 结算"""

    @staticmethod
    def _force_expire(permit_id):
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE local_tool_operation_permits SET deadline = NOW() - INTERVAL '1 second' "
                "WHERE id=%s",
                (permit_id,),
            )
            conn.commit()

    def test_expired_permit_holds_quota_and_blocks_reauthorize(self, tenant_id):
        """已发送但回执丢失 → 清扫置 expired → 额度不恢复（同窗口再申请被拒，与 R19 联动）"""
        ctx = harness.build_running_v2_invocation(tenant_id, quota_limit=1)
        result = _authorize(tenant_id, ctx)
        self._force_expire(result["permit_id"])
        count = permits.expire_permits()
        assert count >= 1
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT state FROM local_tool_operation_permits WHERE id=%s",
                (result["permit_id"],),
            )
            assert cur.fetchone()["state"] == "expired"
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 1 and bucket["used_count"] == 0  # 预留保留
        audits = audit.list_audits(tenant_id, aggregate_type="permit")
        assert any(a["kind"] == "permit_expired" for a in audits)
        # 同窗口再申请（Runtime 重新请求许可）：额度仍被占用 → 拒绝
        with pytest.raises(permits.PermitError) as e:
            _authorize(tenant_id, ctx)
        assert e.value.code == "QUOTA_EXCEEDED"

    def test_expired_permit_late_result_settles_quota_once(self, tenant_id):
        """迟到 applied 回执到达 → 许可 expired 也能结算：reserved→used 恰好一次"""
        from src.local_tools import operation_result

        ctx = harness.build_running_v2_invocation(tenant_id)
        permit = _authorize(tenant_id, ctx)
        self._force_expire(permit["permit_id"])
        assert permits.expire_permits() >= 1

        def _late_result():
            return operation_result.apply_operation_result(
                tenant_id=tenant_id, device_id=ctx["device_id"],
                invocation_id=ctx["invocation_id"],
                claim_token_hash=sha256_hex(ctx["claim_token"]),
                request_id=ctx["request_id"],
                effect="applied", phase="verified",
                evidence_ref=namespaced_evidence_ref(
                    FAKE_EVIDENCE_NAMESPACE, ctx["request_id"], 1),
                permit_id=permit["permit_id"], permit_token=permit["permit_token"],
            )

        result = _late_result()
        assert result["state"] == "succeeded"
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 1
        # 迟到重复回执：不再结算（幂等恰好一次）
        dup = _late_result()
        assert dup["late"] is True
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["used_count"] == 1

    def test_expired_permit_none_prepared_receipt_releases_quota(self, tenant_id):
        """跨侧契约（R22①）：许可 expired → none+prepared+permit 身份回执（可信未执行，
        如 Runtime 锁内防御检查中止 PERMIT_UNAVAILABLE）→ 凭回执释放预留；
        R19 联动：释放即恢复额度，同窗口后续申请可过；释放幂等恰好一次"""
        from src.local_tools import operation_result

        ctx1 = harness.build_running_v2_invocation(tenant_id, quota_limit=1)
        permit = _authorize(tenant_id, ctx1)
        self._force_expire(permit["permit_id"])
        assert permits.expire_permits() >= 1  # 清扫置 expired，预留保留

        def _none_prepared_receipt():
            return operation_result.apply_operation_result(
                tenant_id=tenant_id, device_id=ctx1["device_id"],
                invocation_id=ctx1["invocation_id"],
                claim_token_hash=sha256_hex(ctx1["claim_token"]),
                request_id=ctx1["request_id"],
                effect="none", phase="prepared",
                permit_id=permit["permit_id"], permit_token=permit["permit_token"],
            )

        result = _none_prepared_receipt()
        assert result["acked"] is True and result["state"] == "failed"
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 0  # 预留已释放
        # 重复回执（late）：不重复释放
        dup = _none_prepared_receipt()
        assert dup["late"] is True
        bucket = harness.latest_bucket(tenant_id, "tenant", tenant_id)
        assert bucket["reserved_count"] == 0 and bucket["used_count"] == 0
        # 同窗口第二链路申请可过（R19：reserved+used < limit）
        ctx2 = harness.build_running_v2_invocation(tenant_id, quota_limit=1)
        assert _authorize(tenant_id, ctx2)["permit_id"]
