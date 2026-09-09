"""executor 假适配器 E2E 测试（宪章 §1：注册 subject/revision→手动 occurrence→run→
多条 delivery 顺序执行→一条 unknown 停后续→partial 聚合；含重验链与 payload hash 预载校验）"""

import uuid

import pytest

from src.db.database import get_db_connection
from src.desktop_automation import deliveries as da_deliveries
from src.desktop_automation import executor, occurrences, runs, subjects
from src.desktop_automation.adapters import TrustedAdapterRegistry
from src.local_tools import operation_result, permits, repository
from src.local_tools.security import sha256_hex
from tests.unit.desktop_automation import harness
from tests.unit.desktop_automation import harness
from tests.unit.desktop_automation.fakes import (
    FAKE_EVIDENCE_NAMESPACE,
    FakeScenarioAdapter,
    namespaced_evidence_ref,
)

pytestmark = pytest.mark.unit


def _claim_and_start(tenant_id, device_id, invocation_id):
    """模拟 Runtime 领取 + started，返回 claim token 明文"""
    token = uuid.uuid4().hex + uuid.uuid4().hex  # 64 字符
    repository.claim_next(device_id, tenant_id, sha256_hex(token), 300)
    repository.mark_started(invocation_id, tenant_id, sha256_hex(token))
    return token


def _report(tenant_id, device_id, step, token, *, effect, phase, permit=None,
            evidence_ref=None):
    return operation_result.apply_operation_result(
        tenant_id=tenant_id, device_id=device_id,
        invocation_id=step["invocation_id"], claim_token_hash=sha256_hex(token),
        request_id=step["request_id"], effect=effect, phase=phase,
        evidence_ref=evidence_ref,
        permit_id=permit["permit_id"] if permit else None,
        permit_token=permit["permit_token"] if permit else None,
    )


def _authorize(tenant_id, device_id, step, token):
    return permits.write_authorize(
        tenant_id=tenant_id, device_id=device_id,
        invocation_id=step["invocation_id"], claim_token_hash=sha256_hex(token),
        request_id=step["request_id"], target_version=None, payload_hash=None,
    )


class TestEndToEnd:
    def test_full_run_partial_aggregation(self, tenant_id):
        """3 条 delivery：#1 verified → #2 unknown → #3 skipped → run partial"""
        payloads = {"payload:1": b"one", "payload:2": b"two", "payload:3": b"three"}
        operations = [
            {"position": 1, "operation": "fake_op_one", "target_ref": "target-1"},
            {"position": 2, "operation": "fake_op_two", "target_ref": "target-2",
             "payload_ref": "payload:2"},
            {"position": 3, "operation": "fake_op_three", "target_ref": "target-3"},
        ]
        adapter = harness.setup_scenario(tenant_id, operations=operations, payloads=payloads)

        # 手动触发 → run（带授权快照 epoch）
        trigger = occurrences.accept_manual_trigger(
            tenant_id=tenant_id, scenario_key=adapter.scenario_key, task_ref="task-1",
            request_id="e2e-req-1", user_id="owner-1", now=harness.utcnow(),
        )
        assert trigger["created"] is True
        device_id = str(uuid.uuid4())

        # claim + prepare：3 条 delivery 落行（payload hash 预载校验通过）
        prepared = executor.claim_and_prepare_run(
            revision_config={}, device_id=device_id, tenant_id=tenant_id,
        )
        assert prepared["prepared"] is True
        run = prepared["run"]
        rows = da_deliveries.list_run_deliveries(str(run["id"]), tenant_id)
        assert [d["position"] for d in rows] == [1, 2, 3]
        assert all(d["state"] == "pending" for d in rows)

        # 第 1 条：v2 操作描述 + 顺序执行（上一条 applied+verified 才开始下一条）
        step1 = executor.execute_next_delivery(run)
        assert step1["delivery"]["position"] == 1
        args = repository.get_invocation(step1["invocation_id"], tenant_id)["arguments_json"]
        assert args["protocol_version"] == 2  # R15 v2 统一操作描述
        assert args["delivery_id"] == str(step1["delivery"]["id"])
        assert args["provider_key"] == FakeScenarioAdapter.PROVIDER_KEY
        assert args["request_id"] == step1["request_id"]
        assert args["target_handle"] == "handle:target-1"  # 适配器 resolve_target 短期句柄
        token1 = _claim_and_start(tenant_id, device_id, step1["invocation_id"])
        permit1 = _authorize(tenant_id, device_id, step1, token1)
        r1 = _report(tenant_id, device_id, step1, token1,
                     effect="applied", phase="verified", permit=permit1,
                     evidence_ref=namespaced_evidence_ref(
                         FAKE_EVIDENCE_NAMESPACE, step1["request_id"], 1))
        assert r1["state"] == "succeeded"
        assert r1["run_state"] is None  # 尚有后续条目

        # 第 2 条执行 → unknown → 停后续 + partial 聚合
        run = runs.get_run(str(run["id"]), tenant_id)
        step2 = executor.execute_next_delivery(run)
        assert step2["delivery"]["position"] == 2
        token2 = _claim_and_start(tenant_id, device_id, step2["invocation_id"])
        r2 = _report(tenant_id, device_id, step2, token2, effect="unknown", phase="unknown")
        assert r2["run_state"] == "partial"

        rows = da_deliveries.list_run_deliveries(str(run["id"]), tenant_id)
        by_pos = {d["position"]: d for d in rows}
        assert by_pos[1]["state"] == "succeeded"
        assert by_pos[2]["state"] == "unknown"
        assert by_pos[3]["state"] == "skipped"  # unknown 停后续
        final_run = runs.get_run(str(run["id"]), tenant_id)
        assert final_run["state"] == "partial"
        assert final_run["result_json"]["business"]["verdict"] == "fake-ok"  # 场景业务判定另存
        # run_finished outbox（确定性 dedupe 幂等）
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM desktop_automation_outbox "
                "WHERE tenant_id=%s AND kind='run_finished'",
                (tenant_id,),
            )
            assert cur.fetchone()["c"] == 1

        # 第 3 条不再派发（unknown 后 execute_next 返回 None）
        run = runs.get_run(str(run["id"]), tenant_id)
        assert executor.execute_next_delivery(run) is None


class TestClaimTenantIsolation:
    def test_claim_run_tenant_scoped(self, tenant_id):
        """T-P1-1① 回归：两个租户各有 pending run，租户过滤只领取本租户的 run"""
        other_tenant = f"da_test_{uuid.uuid4().hex[:12]}"
        try:
            harness.setup_scenario(tenant_id)
            harness.setup_scenario(other_tenant)
            from src.desktop_automation import occurrences, runs

            occurrences.accept_manual_trigger(
                tenant_id=tenant_id, scenario_key="fake-scenario", task_ref="task-1",
                request_id="claim-a", user_id="owner-1", now=harness.utcnow(),
            )
            occurrences.accept_manual_trigger(
                tenant_id=other_tenant, scenario_key="fake-scenario", task_ref="task-1",
                request_id="claim-b", user_id="owner-2", now=harness.utcnow(),
            )
            claimed = runs.claim_run(lease_seconds=60, limit=5, tenant_id=tenant_id)
            assert len(claimed) == 1
            assert claimed[0]["tenant_id"] == tenant_id  # 绝不领取 other_tenant 的 run
            # 另一租户过滤领取互证：两租户各有一个 pending run
            rest = runs.claim_run(lease_seconds=60, limit=5, tenant_id=other_tenant)
            assert len(rest) == 1 and rest[0]["tenant_id"] == other_tenant
        finally:
            from tests.unit.desktop_automation.conftest import cleanup_tenant

            cleanup_tenant(other_tenant)


class TestDirectedClaim:
    """R50：claim_run 定向领取（expected_run_id，FOR UPDATE SKIP LOCKED）"""

    def _two_pending_runs(self, tenant_id):
        harness.setup_scenario(tenant_id)
        from src.desktop_automation import occurrences

        r1 = occurrences.accept_manual_trigger(
            tenant_id=tenant_id, scenario_key="fake-scenario", task_ref="task-1",
            request_id="direct-1", user_id="owner-1", now=harness.utcnow(),
        )
        r2 = occurrences.accept_manual_trigger(
            tenant_id=tenant_id, scenario_key="fake-scenario", task_ref="task-1",
            request_id="direct-2", user_id="owner-1", now=harness.utcnow(),
        )
        run_ids = [str(get_occurrence_run(tenant_id, r["occurrence_id"])) for r in (r1, r2)]
        return run_ids

    def test_expected_run_id_claims_only_target(self, tenant_id):
        """定向领取只领目标 run：另一 pending run 不被触碰；已领/非目标返回空。"""
        run_a, run_b = self._two_pending_runs(tenant_id)
        claimed = runs.claim_run(
            lease_seconds=60, limit=5, tenant_id=tenant_id, expected_run_id=run_b,
        )
        assert len(claimed) == 1 and str(claimed[0]["id"]) == run_b
        assert claimed[0]["state"] == "running" and claimed[0]["fence_token"] == 1
        # 目标已被领：再次定向领取返回空（SKIP LOCKED 语义，不阻塞不误领）
        assert runs.claim_run(
            lease_seconds=60, limit=5, tenant_id=tenant_id, expected_run_id=run_b,
        ) == []
        # 未被领的 A 仍 pending，可照常定向领取
        again = runs.claim_run(
            lease_seconds=60, limit=5, tenant_id=tenant_id, expected_run_id=run_a,
        )
        assert len(again) == 1 and str(again[0]["id"]) == run_a

    def test_expected_run_id_respects_tenant_scope(self, tenant_id):
        """定向领取与租户过滤叠加：他租户的 run id 在本租户域内领不到。"""
        other_tenant = f"da_test_{uuid.uuid4().hex[:12]}"
        try:
            run_a, _ = self._two_pending_runs(tenant_id)
            harness.setup_scenario(other_tenant)
            assert runs.claim_run(
                lease_seconds=60, limit=5, tenant_id=other_tenant, expected_run_id=run_a,
            ) == []
            # 原租户内目标仍可领（未被他人触碰）
            claimed = runs.claim_run(
                lease_seconds=60, limit=5, tenant_id=tenant_id, expected_run_id=run_a,
            )
            assert len(claimed) == 1 and str(claimed[0]["id"]) == run_a
        finally:
            from tests.unit.desktop_automation.conftest import cleanup_tenant

            cleanup_tenant(other_tenant)


def get_occurrence_run(tenant_id: str, occurrence_id) -> str:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM desktop_automation_runs "
            "WHERE tenant_id = %s AND occurrence_id = %s",
            (tenant_id, str(occurrence_id)),
        )
        return cur.fetchone()["id"]


class TestRevalidation:
    def test_payload_hash_mismatch_aborts_run(self, tenant_id):
        """payload hash 预载校验失败：run 落终态，不派发任何 delivery"""
        payloads = {"payload:1": b"tampered-bytes"}
        operations = [
            {"position": 1, "operation": "fake_op_one", "target_ref": "target-1",
             "payload_ref": "payload:1", "payload_hash": "0" * 64},  # 故意不匹配
        ]
        harness.setup_scenario(tenant_id, operations=operations, payloads=payloads)
        occurrences.accept_manual_trigger(
            tenant_id=tenant_id, scenario_key="fake-scenario", task_ref="task-1",
            request_id="e2e-hash-1", user_id="owner-1", now=harness.utcnow(),
        )
        prepared = executor.claim_and_prepare_run(
            revision_config={}, device_id=str(uuid.uuid4()), tenant_id=tenant_id,
        )
        assert prepared["prepared"] is False
        assert prepared["reason"] == "payload_hash_mismatch"
        run = runs.get_run(str(prepared["run"]["id"]), tenant_id)
        assert run["state"] == "unknown"  # 无法证明未提交的资源问题按 unknown 口径

    def test_epoch_stale_aborts_run_as_cancelled(self, tenant_id):
        """授权 epoch 失效（暂停→恢复推进 epoch）：重验失败，run 未提交条目停止聚合 cancelled。

        T-P2-2/P2-8 重写：确定性断言——恢复后 task 重新 active 但 epoch 已 +2，
        run 携带旧快照 epoch → claim_and_prepare 必然领取该 run 并按
        authorization_epoch_stale 放弃（不再依赖前置手动 claim 的条件分支）。
        """
        payloads = {"payload:1": b"one"}
        operations = [{"position": 1, "operation": "fake_op_one", "target_ref": "target-1"}]
        harness.setup_scenario(tenant_id, operations=operations, payloads=payloads)
        trigger = occurrences.accept_manual_trigger(
            tenant_id=tenant_id, scenario_key="fake-scenario", task_ref="task-1",
            request_id="e2e-epoch-1", user_id="owner-1", now=harness.utcnow(),
        )
        assert trigger["created"] is True
        subjects.pause_task(tenant_id, "fake-scenario", "task-1")
        subjects.resume_task(tenant_id, "fake-scenario", "task-1")  # active，但 epoch 已推进

        prepared = executor.claim_and_prepare_run(
            revision_config={}, device_id=str(uuid.uuid4()), tenant_id=tenant_id,
        )
        assert prepared is not None, "应领取到 pending run"
        assert prepared["prepared"] is False
        assert prepared["reason"] == "authorization_epoch_stale"
        run = runs.get_run(str(prepared["run"]["id"]), tenant_id)
        assert run["state"] == "cancelled"
        rows = da_deliveries.list_run_deliveries(str(run["id"]), tenant_id)
        assert all(d["state"] == "skipped" for d in rows)  # 未提交条目停止

    def test_unregistered_scenario_fails_loud(self, tenant_id):
        """未注册场景 fail-loud（不静默降级）：executor/publish 的 require 路径"""
        from src.desktop_automation.adapters import AdapterNotFoundError

        TrustedAdapterRegistry.unregister("fake-scenario")
        with pytest.raises(AdapterNotFoundError):
            TrustedAdapterRegistry.require("fake-scenario")

class TestEnqueueIdempotency:
    def test_dedupe_reuse_returns_old_request_id(self, tenant_id):
        """P2-4：dedupe 幂等复用旧 invocation 时，request_id 取旧 arguments_json 值"""
        from src.local_tools.service import LocalInvocationService

        service = LocalInvocationService()
        device_id = str(uuid.uuid4())
        inv1 = service.enqueue(
            tenant_id=tenant_id, user_id="owner-1", device_id=device_id,
            tool_name="fake_op_one",
            arguments={"protocol_version": 2, "request_id": "req-old"},
            provider_key="fake-provider", business_kind="desktop_automation",
            business_ref={"delivery_id": "d1"}, dedupe_key="dedupe-1",
        )
        inv2 = service.enqueue(
            tenant_id=tenant_id, user_id="owner-1", device_id=device_id,
            tool_name="fake_op_one",
            arguments={"protocol_version": 2, "request_id": "req-new"},
            provider_key="fake-provider", business_kind="desktop_automation",
            business_ref={"delivery_id": "d1"}, dedupe_key="dedupe-1",
        )
        assert str(inv2["id"]) == str(inv1["id"])  # 同一行，不新建
        assert inv2["arguments_json"]["request_id"] == "req-old"  # 旧 request_id 为准
