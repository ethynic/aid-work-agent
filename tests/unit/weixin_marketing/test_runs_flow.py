"""执行链 E2E 与 resolve/retry 测试（R48 逐条账本 / 取消 / 手动 run / R45 重试链）

经真实底座执行链：manual trigger → claim_and_prepare → execute_next_delivery →
设备 claim/started → write_authorize（许可+配额）→ operation-result（证据链）→
advance_run 聚合。
"""

import uuid
from datetime import timedelta
from typing import Any, Dict

import pytest

from src.desktop_automation import executor
from src.desktop_automation import runs as da_runs
from src.local_tools import permits, repository
from src.local_tools.operation_result import apply_operation_result
from src.local_tools.security import generate_claim_token, sha256_hex
from src.weixin_marketing.service import ConflictError, WeixinValidationError
from tests.unit.weixin_marketing.conftest import (
    create_and_publish,
    manual_run_pending,
    utcnow,
)

pytestmark = pytest.mark.unit


def _claim_and_prepare(service, tenant_id, revision_id, device_id):
    revision_config = service.load_revision_config(tenant_id, revision_id)
    prepared = executor.claim_and_prepare_run(
        revision_config=revision_config, device_id=device_id,
        tenant_id=tenant_id, lease_seconds=300,
    )
    assert prepared is not None and prepared["prepared"] is True, prepared
    return prepared


def _claim_and_start(tenant_id, invocation_id, device_id):
    claim_token = generate_claim_token()
    claimed = repository.claim_next(device_id, tenant_id, sha256_hex(claim_token), 300)
    assert claimed is not None and str(claimed["id"]) == invocation_id
    started = repository.mark_started(invocation_id, tenant_id, sha256_hex(claim_token))
    assert started is not None and started["state"] == "running"
    return claim_token


def _authorize_and_finish(
    tenant_id, device_id, invocation_id, claim_token, request_id,
    target_version, payload_hash, *, effect="applied", phase="verified",
    safe_to_retry=None,
):
    """write-authorize → operation-result（evidence 按场景命名空间编码）"""
    permit = permits.write_authorize(
        tenant_id=tenant_id, device_id=device_id, invocation_id=invocation_id,
        claim_token_hash=sha256_hex(claim_token), request_id=request_id,
        target_version=target_version, payload_hash=payload_hash,
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


class TestManualRunExecution:
    def test_full_chain_two_deliveries_succeeded(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(service, tenant_id, group_id)
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(service, tenant_id, revision_id, device_id)
        run = prepared["run"]
        assert run["revision_ref"] == revision_id
        assert len(prepared["delivery_ids"]) == 2

        # 第 1 条
        stepped1 = executor.execute_next_delivery(run)
        assert stepped1 is not None
        token1 = _claim_and_start(tenant_id, stepped1["invocation_id"], device_id)
        permit1, result1 = _authorize_and_finish(
            tenant_id, device_id, stepped1["invocation_id"], token1, stepped1["request_id"],
            target_version="iv-7-se-3", payload_hash=stepped1["delivery"]["payload_hash"],
        )
        assert result1["state"] == "succeeded"
        # 第 2 条
        stepped2 = executor.execute_next_delivery(run)
        assert stepped2 is not None
        token2 = _claim_and_start(tenant_id, stepped2["invocation_id"], device_id)
        _, result2 = _authorize_and_finish(
            tenant_id, device_id, stepped2["invocation_id"], token2, stepped2["request_id"],
            target_version="iv-7-se-3", payload_hash=stepped2["delivery"]["payload_hash"],
        )
        assert result2["state"] == "succeeded"
        assert result2["run_state"] == "succeeded"
        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        assert [d["state"] for d in detail["deliveries"]] == ["succeeded", "succeeded"]

    def test_ledger_first_success_second_unknown_third_skipped(self, service, tenant_id, bindings, adapter):
        """R48 验收：第 1 成功、第 2 unknown、第 3 不发（skipped），run=partial；
        恢复（重扫/推进）不重放第 1。"""
        _, group_id = bindings
        blocks = [
            {"type": "text", "text_content": "第 1 条"},
            {"type": "text", "text_content": "第 2 条"},
            {"type": "text", "text_content": "第 3 条"},
        ]
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id, blocks=blocks
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(service, tenant_id, revision_id, device_id)
        run = prepared["run"]

        stepped1 = executor.execute_next_delivery(run)
        token1 = _claim_and_start(tenant_id, stepped1["invocation_id"], device_id)
        _, result1 = _authorize_and_finish(
            tenant_id, device_id, stepped1["invocation_id"], token1, stepped1["request_id"],
            target_version="iv-7-se-3", payload_hash=stepped1["delivery"]["payload_hash"],
        )
        assert result1["run_state"] is None  # 未到终态

        stepped2 = executor.execute_next_delivery(run)
        token2 = _claim_and_start(tenant_id, stepped2["invocation_id"], device_id)
        _, result2 = _authorize_and_finish(
            tenant_id, device_id, stepped2["invocation_id"], token2, stepped2["request_id"],
            target_version="iv-7-se-3", payload_hash=stepped2["delivery"]["payload_hash"],
            effect="unknown", phase="unknown",
        )
        assert result2["run_state"] == "partial"  # unknown 停后续 + 已有成功 → partial

        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        assert [d["state"] for d in detail["deliveries"]] == ["succeeded", "unknown", "skipped"]
        # 第 3 条无 invocation（未派发即停）
        assert executor.execute_next_delivery(run) is None
        # 已成功第 1 条不被重放：再次驱动无新 invocation
        from src.desktop_automation import deliveries as da_deliveries

        assert da_deliveries.next_executable_delivery(run_id, tenant_id) is None

    def test_quota_limit_blocks_second_permit(self, service, tenant_id, bindings, wx_config, adapter):
        """配额映射接入底座：task 限额 1 → 第二条 delivery 许可被拒（429/409 语义）"""
        from dataclasses import replace

        from src.desktop_automation.adapters import TrustedAdapterRegistry
        from src.weixin_marketing.adapters import WeixinFixedContentAdapter
        from src.weixin_marketing.config import WeixinQuotaConfig

        tight = replace(
            wx_config,
            quotas=WeixinQuotaConfig(window_seconds=3600, tenant_limit=100, task_limit=1,
                                     target_limit=10, account_limit=60),
        )
        tight_adapter = WeixinFixedContentAdapter(config=tight)
        TrustedAdapterRegistry.register(tight_adapter)
        try:
            _, group_id = bindings
            automation_id, revision_id, _ = create_and_publish(service, tenant_id, group_id)
            _, run_id = manual_run_pending(service, tenant_id, automation_id)
            device_id = str(uuid.uuid4())
            prepared = _claim_and_prepare(service, tenant_id, revision_id, device_id)
            run = prepared["run"]

            stepped1 = executor.execute_next_delivery(run)
            token1 = _claim_and_start(tenant_id, stepped1["invocation_id"], device_id)
            _, result1 = _authorize_and_finish(
                tenant_id, device_id, stepped1["invocation_id"], token1, stepped1["request_id"],
                target_version="iv-7-se-3", payload_hash=stepped1["delivery"]["payload_hash"],
            )
            assert result1["state"] == "succeeded"  # 首条 settle（R19：used 占用额度）

            stepped2 = executor.execute_next_delivery(run)
            assert stepped2 is not None  # 预检只读桶：settle 前桶可能未见 → 派发后许可拦截
            token2 = _claim_and_start(tenant_id, stepped2["invocation_id"], device_id)
            with pytest.raises(permits.PermitError) as exc_info:
                permits.write_authorize(
                    tenant_id=tenant_id, device_id=device_id,
                    invocation_id=stepped2["invocation_id"],
                    claim_token_hash=sha256_hex(token2), request_id=stepped2["request_id"],
                    target_version="iv-7-se-3",
                    payload_hash=stepped2["delivery"]["payload_hash"],
                )
            assert exc_info.value.code == "QUOTA_EXCEEDED"
        finally:
            TrustedAdapterRegistry.register(adapter)  # 还原本用例 fixture 适配器（同 key）


class TestManualRunIdempotent:
    def test_duplicate_request_id_single_run(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(service, tenant_id, group_id)
        request_id = f"req-{uuid.uuid4().hex[:8]}"
        first = service.manual_run(tenant_id, automation_id, "owner-1", request_id=request_id, now=utcnow())
        second = service.manual_run(tenant_id, automation_id, "owner-1", request_id=request_id, now=utcnow())
        assert first["created"] is True and second["created"] is False
        assert first["run_id"] == second["run_id"]
        assert first["occurrence_id"] == second["occurrence_id"]

    def test_manual_run_and_time_trigger_independent(self, service, tenant_id, bindings, adapter):
        """手动 run 与时间触发互不干扰（R48）：手动 run 完成后，时间槽接纳生成
        独立 occurrence/run（skip_overlap 下未完成 run 会按策略跳过，先收敛手动 run）"""
        from src.desktop_automation import occurrences as da_occurrences

        _, group_id = bindings
        run_at = utcnow() + timedelta(minutes=30)
        blocks = [{"type": "text", "text_content": "手动内容"}]
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id, blocks=blocks, trigger={
                "type": "once", "run_at": run_at.isoformat(), "timezone": "UTC",
            }
        )
        _, manual_run_id = manual_run_pending(service, tenant_id, automation_id)
        # 手动 run 走完整执行链至终态
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(service, tenant_id, revision_id, device_id)
        stepped = executor.execute_next_delivery(prepared["run"])
        token = _claim_and_start(tenant_id, stepped["invocation_id"], device_id)
        _, result = _authorize_and_finish(
            tenant_id, device_id, stepped["invocation_id"], token, stepped["request_id"],
            target_version="iv-7-se-3", payload_hash=stepped["delivery"]["payload_hash"],
        )
        assert result["run_state"] == "succeeded"
        # 时间槽到期接纳：生成第二个独立 run（互不干扰）
        da_occurrences.scan_and_accept_time_slots(run_at + timedelta(seconds=5), limit=100)
        runs = service.list_runs(tenant_id, "owner-1", automation_id=automation_id)
        assert runs["total"] == 2
        assert manual_run_id in [str(r["id"]) for r in runs["items"]]
        # 时间 run 的 occurrence 触发键为 time: 前缀（与 manual: 前缀不同）
        other = [str(r["id"]) for r in runs["items"] if str(r["id"]) != manual_run_id]
        time_run = da_runs.get_run(other[0], tenant_id)
        occurrence = da_occurrences.get_occurrence(str(time_run["occurrence_id"]), tenant_id)
        assert occurrence["trigger_kind"] == "time"


class TestCancelRun:
    def _prepare_two_delivery_run(self, service, tenant_id, group_id, adapter):
        automation_id, revision_id, _ = create_and_publish(service, tenant_id, group_id)
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(service, tenant_id, revision_id, device_id)
        return automation_id, run_id, prepared["run"], device_id

    def test_cancel_before_any_dispatch(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, run_id, _, _ = self._prepare_two_delivery_run(service, tenant_id, group_id, adapter)
        result = service.cancel_run(tenant_id, run_id, "owner-1")
        assert result["state"] == "cancelled"
        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        assert [d["state"] for d in detail["deliveries"]] == ["skipped", "skipped"]

    def test_cancel_after_started_is_partial(self, service, tenant_id, bindings, adapter):
        """已提交条目照实回收：第 1 条 may_have_started 后取消 → partial + 剩余 skipped"""
        _, group_id = bindings
        automation_id, run_id, run, device_id = self._prepare_two_delivery_run(
            service, tenant_id, group_id, adapter
        )
        stepped = executor.execute_next_delivery(run)
        token = _claim_and_start(tenant_id, stepped["invocation_id"], device_id)
        # 只签许可（may_have_started），结果未回
        permits.write_authorize(
            tenant_id=tenant_id, device_id=device_id, invocation_id=stepped["invocation_id"],
            claim_token_hash=sha256_hex(token), request_id=stepped["request_id"],
            target_version="iv-7-se-3", payload_hash=stepped["delivery"]["payload_hash"],
        )
        result = service.cancel_run(tenant_id, run_id, "owner-1")
        assert result["state"] == "partial"
        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        # 在途条目保持 dispatched/may_have_started（迟到结果仍可落账）；剩余 skipped
        assert [d["state"] for d in detail["deliveries"]] == ["dispatched", "skipped"]
        assert detail["deliveries"][0]["phase"] == "may_have_started"
        # 幂等：再次取消不变化
        again = service.cancel_run(tenant_id, run_id, "owner-1")
        assert again["changed"] is False

    def test_cancel_cross_owner_404(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, run_id, _, _ = self._prepare_two_delivery_run(service, tenant_id, group_id, adapter)
        with pytest.raises(Exception):
            service.cancel_run(tenant_id, run_id, "intruder")

    def test_cancel_dispatched_queued_invocation_never_claimable(
        self, service, tenant_id, bindings, adapter
    ):
        """R49 中间窗口②：queued invocation 被取消后设备 claim 不到——cancel_run 将
        其置 cancelled（终态，effect=none），claim_next 只取 queued。"""
        _, group_id = bindings
        automation_id, run_id, run, device_id = self._prepare_two_delivery_run(
            service, tenant_id, group_id, adapter
        )
        stepped = executor.execute_next_delivery(run)
        from src.local_tools import repository as lt_repository

        before = lt_repository.get_invocation(str(stepped["invocation_id"]), tenant_id)
        assert before["state"] == "queued"
        service.cancel_run(tenant_id, run_id, "owner-1")
        after = lt_repository.get_invocation(str(stepped["invocation_id"]), tenant_id)
        assert after["state"] == "cancelled" and after["effect"] == "none"
        # 设备永远领不到已取消 invocation
        token = generate_claim_token()
        assert lt_repository.claim_next(device_id, tenant_id, sha256_hex(token), 300) is None

    def test_cancel_then_write_authorize_409(
        self, service, tenant_id, bindings, adapter
    ):
        """R49 中间窗口①：invocation 已派发未获许可（claimed/running）→ 取消 →
        write_authorize 409（invocation 已 cancel_requested + run 已终态双保险）。"""
        _, group_id = bindings
        automation_id, run_id, run, device_id = self._prepare_two_delivery_run(
            service, tenant_id, group_id, adapter
        )
        stepped = executor.execute_next_delivery(run)
        token = _claim_and_start(tenant_id, str(stepped["invocation_id"]), device_id)
        service.cancel_run(tenant_id, run_id, "owner-1")
        from src.local_tools import repository as lt_repository

        assert lt_repository.get_invocation(str(stepped["invocation_id"]), tenant_id)[
            "state"
        ] == "cancel_requested"
        with pytest.raises(permits.PermitError) as exc_info:
            permits.write_authorize(
                tenant_id=tenant_id, device_id=device_id,
                invocation_id=str(stepped["invocation_id"]),
                claim_token_hash=sha256_hex(token), request_id=stepped["request_id"],
                target_version="iv-7-se-3",
                payload_hash=stepped["delivery"]["payload_hash"],
            )
        assert exc_info.value.http_status == 409

    def test_write_authorize_rejected_when_run_terminal(
        self, service, tenant_id, bindings, adapter
    ):
        """R49 run 复验独立锚：run 被外部路径收敛终态（模拟租约回收/取消并发）而
        invocation 仍 running → write_authorize 409 RUN_NOT_ACTIVE（delivery 从未终态，
        非人工重试链重开形态）。"""
        _, group_id = bindings
        automation_id, run_id, run, device_id = self._prepare_two_delivery_run(
            service, tenant_id, group_id, adapter
        )
        stepped = executor.execute_next_delivery(run)
        token = _claim_and_start(tenant_id, str(stepped["invocation_id"]), device_id)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:  # 模拟并发终态（不触碰 invocation）
            cur = conn.cursor()
            cur.execute(
                "UPDATE desktop_automation_runs SET state = 'unknown', finished_at = NOW() "
                "WHERE tenant_id = %s AND id = %s",
                (tenant_id, str(run_id)),
            )
            conn.commit()
        with pytest.raises(permits.PermitError) as exc_info:
            permits.write_authorize(
                tenant_id=tenant_id, device_id=device_id,
                invocation_id=str(stepped["invocation_id"]),
                claim_token_hash=sha256_hex(token), request_id=stepped["request_id"],
                target_version="iv-7-se-3",
                payload_hash=stepped["delivery"]["payload_hash"],
            )
        assert exc_info.value.code == "RUN_NOT_ACTIVE"
        assert exc_info.value.http_status == 409


class TestCancelPauseConcurrency:
    """CR-P1-1：pause（_cancel_open_runs_on）× cancel_run 并发——run→deliveries→
    invocations 严格同序持锁，无 AB-BA 死锁窗口"""

    def test_pause_and_cancel_run_concurrent_no_deadlock(
        self, service, tenant_id, bindings, adapter
    ):
        _, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id,
            blocks=[
                {"type": "text", "text_content": "第 1 条"},
                {"type": "text", "text_content": "第 2 条"},
            ],
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(service, tenant_id, revision_id, device_id)
        stepped = executor.execute_next_delivery(prepared["run"])
        _claim_and_start(tenant_id, stepped["invocation_id"], device_id)  # running 在途

        automation = service.get_automation_detail(tenant_id, automation_id, "owner-1")[
            "automation"
        ]
        import threading

        from src.weixin_marketing.models import VersionedActionInput

        barrier = threading.Barrier(2)
        outcomes: Dict[str, Any] = {"pause": None, "cancel": None, "errors": []}

        def pauser():
            try:
                barrier.wait(timeout=10)
                outcomes["pause"] = service.pause(
                    tenant_id, automation_id, "owner-1",
                    VersionedActionInput(expected_version=automation["version"]),
                )
            except Exception as e:  # noqa: BLE001
                outcomes["errors"].append(("pause", repr(e)))

        def canceller():
            try:
                barrier.wait(timeout=10)
                outcomes["cancel"] = service.cancel_run(tenant_id, run_id, "owner-1")
            except Exception as e:  # noqa: BLE001
                outcomes["errors"].append(("cancel", repr(e)))

        threads = [threading.Thread(target=pauser), threading.Thread(target=canceller)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not any(t.is_alive() for t in threads), "死锁：线程未在超时内结束"
        assert not outcomes["errors"], outcomes["errors"]
        assert outcomes["pause"] is not None and outcomes["cancel"] is not None

        # 终局一致：run 终态 partial（在途条目照实回收 §5.4，两路径聚合口径相同），
        # delivery 1 保持 dispatched（迟到结果仍可落账）、剩余 skipped；pause 恰一次生效
        run = da_runs.get_run(run_id, tenant_id)
        assert run["state"] == "partial"
        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        assert [d["state"] for d in detail["deliveries"]] == ["dispatched", "skipped"]
        assert outcomes["pause"]["status"] == "paused"
        assert outcomes["pause"]["version"] == automation["version"] + 1
        # 取消侧无论竞速次序均收敛同一终态（changed 真假取决于次序，但无异常不改判）
        assert outcomes["cancel"]["state"] == "partial"


class TestResolveAndRetry:
    def _drive_to_failed(self, service, tenant_id, group_id, adapter, *, safe_to_retry=True):
        """1 条内容失败（effect none → failed）终态 run；返回 (automation, run, stepped, device_id)。
        safe_to_retry 默认 True（机器安全路径的完整回执形态——复审三轮：none+prepared
        必须叠加显式 safe_to_retry=true 才算机器证明从未开始）。"""
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "唯一内容"}]
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(service, tenant_id, revision_id, device_id)
        stepped = executor.execute_next_delivery(prepared["run"])
        token = _claim_and_start(tenant_id, stepped["invocation_id"], device_id)
        _authorize_and_finish(
            tenant_id, device_id, stepped["invocation_id"], token, stepped["request_id"],
            target_version="iv-7-se-3", payload_hash=stepped["delivery"]["payload_hash"],
            effect="none", phase="prepared", safe_to_retry=safe_to_retry,
        )
        return automation_id, run_id, stepped, device_id

    def test_resolve_records_audit_without_touching_machine_state(
        self, service, tenant_id, bindings, adapter
    ):
        _, group_id = bindings
        automation_id, run_id, stepped, _ = self._drive_to_failed(service, tenant_id, group_id, adapter)
        delivery = stepped["delivery"]
        before = service.get_run_detail(tenant_id, run_id, "owner-1")["deliveries"][0]
        from src.weixin_marketing.models import DeliveryResolveInput

        result = service.resolve_delivery(
            tenant_id, str(delivery["id"]), "owner-1",
            DeliveryResolveInput(verdict="not_delivered", note="人工核对未见消息"),
        )
        assert result["machine_state"] == "failed"
        after = service.get_run_detail(tenant_id, run_id, "owner-1")["deliveries"][0]
        assert after["state"] == before["state"] and after["effect"] == before["effect"]
        # 审计留痕
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT action, details_redacted FROM bs_weixin_marketing_audit_events "
                "WHERE tenant_id = %s AND action = 'delivery_resolved'",
                (tenant_id,),
            )
            rows = cur.fetchall()
        assert len(rows) == 1
        assert rows[0]["details_redacted"]["verdict"] == "not_delivered"

    def test_retry_chain_creates_attempt_with_predecessor(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, run_id, stepped, _ = self._drive_to_failed(service, tenant_id, group_id, adapter)
        delivery = stepped["delivery"]
        result = service.retry_delivery(tenant_id, str(delivery["id"]), "owner-1", confirm=True)
        assert result["attempt_no"] == 2
        assert result["predecessor_attempt_id"] == stepped["attempt_id"]
        # 新 attempt 挂在原 delivery，predecessor 链正确
        from src.desktop_automation import attempts as da_attempts

        attempts = da_attempts.list_delivery_attempts(str(delivery["id"]), tenant_id)
        assert [a["attempt_no"] for a in attempts] == [1, 2]
        assert str(attempts[1]["predecessor_attempt_id"]) == str(attempts[0]["id"])
        assert attempts[1]["invocation_id"] != attempts[0]["invocation_id"]
        # delivery 回到 dispatched（等待新结果）
        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        assert detail["deliveries"][0]["state"] == "dispatched"

    def test_retry_requires_confirm(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, run_id, stepped, _ = self._drive_to_failed(service, tenant_id, group_id, adapter)
        with pytest.raises(WeixinValidationError):
            service.retry_delivery(tenant_id, str(stepped["delivery"]["id"]), "owner-1", confirm=False)

    def _drive_to_unknown(self, service, tenant_id, group_id, adapter):
        """1 条内容未知效果（effect=unknown）终态 run；返回 (automation, run, stepped, device_id)"""
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "未知内容"}]
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(service, tenant_id, revision_id, device_id)
        stepped = executor.execute_next_delivery(prepared["run"])
        token = _claim_and_start(tenant_id, stepped["invocation_id"], device_id)
        stepped["claim_token"] = token
        _authorize_and_finish(
            tenant_id, device_id, stepped["invocation_id"], token, stepped["request_id"],
            target_version="iv-7-se-3", payload_hash=stepped["delivery"]["payload_hash"],
            effect="unknown", phase="unknown",
        )
        return automation_id, run_id, stepped, device_id

    def test_retry_unknown_without_decision_rejected(
        self, service, tenant_id, bindings, adapter
    ):
        """R52 分支②：未知效果（末次 attempt 非 none+prepared）无人工决定 →
        409 RETRY_EVIDENCE_REQUIRED（仅 confirm 不够）。"""
        _, group_id = bindings
        from src.weixin_marketing.service import RetryEvidenceRequiredError

        automation_id, run_id, stepped, _ = self._drive_to_unknown(
            service, tenant_id, group_id, adapter
        )
        delivery = stepped["delivery"]
        assert service.get_run_detail(tenant_id, run_id, "owner-1")["deliveries"][0][
            "state"
        ] == "unknown"
        with pytest.raises(RetryEvidenceRequiredError):
            service.retry_delivery(tenant_id, str(delivery["id"]), "owner-1", confirm=True)

    def test_retry_unknown_decision_without_stop_confirmation_rejected(
        self, service, tenant_id, bindings, adapter
    ):
        """复审三轮点名的拒绝条件：云端 unknown、人工已确认未发送（resolve
        decision=confirmed_not_sent），但旧进程/旧 invocation 无受信停止确认 →
        拒绝重试（人工确认不能证明旧 Runtime 已停止，存在重复发送风险）。"""
        _, group_id = bindings
        from src.weixin_marketing.service import RetryEvidenceRequiredError
        from src.weixin_marketing.models import DeliveryResolveInput

        automation_id, run_id, stepped, _ = self._drive_to_unknown(
            service, tenant_id, group_id, adapter
        )
        delivery = stepped["delivery"]
        resolved = service.resolve_delivery(
            tenant_id, str(delivery["id"]), "owner-1",
            DeliveryResolveInput(
                verdict="not_delivered", decision="confirmed_not_sent",
                note="人工核对群内未见消息，确认未发送",
            ),
        )
        assert resolved["decision"] == "confirmed_not_sent"
        with pytest.raises(RetryEvidenceRequiredError) as exc_info:
            service.retry_delivery(tenant_id, str(delivery["id"]), "owner-1", confirm=True)
        assert "停止确认" in str(exc_info.value)

    def test_retry_unknown_with_decision_and_stop_confirmation_succeeds(
        self, service, tenant_id, bindings, adapter
    ):
        """R52 收紧后未知效果重试的完整双证据路径：人工决定 + 绑定原
        invocation/request 的设备通道迟到停止确认（operation_result_late：
        effect=none/phase=prepared/safe_to_retry=true）→ retry 放行。"""
        _, group_id = bindings
        from src.weixin_marketing.models import DeliveryResolveInput

        automation_id, run_id, stepped, device_id = self._drive_to_unknown(
            service, tenant_id, group_id, adapter
        )
        delivery = stepped["delivery"]
        service.resolve_delivery(
            tenant_id, str(delivery["id"]), "owner-1",
            DeliveryResolveInput(
                verdict="not_delivered", decision="confirmed_not_sent",
                note="人工核对群内未见消息，确认未发送",
            ),
        )
        # 旧 Runtime 以原 claim/request 身份补发停止确认（迟到更正回执）
        late = apply_operation_result(
            tenant_id=tenant_id, device_id=device_id,
            invocation_id=stepped["invocation_id"],
            claim_token_hash=sha256_hex(stepped["claim_token"]),
            request_id=stepped["request_id"],
            effect="none", phase="prepared", safe_to_retry=True,
        )
        assert late["acked"] is True and late["late"] is True
        retry = service.retry_delivery(tenant_id, str(delivery["id"]), "owner-1", confirm=True)
        assert retry["attempt_no"] == 2
        assert retry["predecessor_attempt_id"] == stepped["attempt_id"]

    def test_retry_machine_safe_requires_safe_to_retry_true(
        self, service, tenant_id, bindings, adapter
    ):
        """复审三轮：机器安全分支必须叠加显式 safe_to_retry=true——
        none+prepared 但 safe_to_retry=False → 不算机器证明，走未知效果证据门槛
        （无决定无停止确认 → 409）。"""
        _, group_id = bindings
        from src.weixin_marketing.service import RetryEvidenceRequiredError

        automation_id, run_id, stepped, _ = self._drive_to_failed(
            service, tenant_id, group_id, adapter, safe_to_retry=False
        )
        with pytest.raises(RetryEvidenceRequiredError):
            service.retry_delivery(
                tenant_id, str(stepped["delivery"]["id"]), "owner-1", confirm=True
            )

    def test_retry_machine_safe_safe_to_retry_null_not_machine_proof(
        self, service, tenant_id, bindings, adapter
    ):
        """safe_to_retry=NULL（设备未判定）同样不得直接放行——False/NULL 均需
        走未知效果双证据门槛。"""
        _, group_id = bindings
        from src.weixin_marketing.service import RetryEvidenceRequiredError

        automation_id, run_id, stepped, _ = self._drive_to_failed(
            service, tenant_id, group_id, adapter, safe_to_retry=None
        )
        with pytest.raises(RetryEvidenceRequiredError):
            service.retry_delivery(
                tenant_id, str(stepped["delivery"]["id"]), "owner-1", confirm=True
            )

    def test_resolve_decision_requires_note(
        self, service, tenant_id, bindings, adapter
    ):
        """R52：decision=confirmed_not_sent 必须附带说明（422）。"""
        _, group_id = bindings
        from pydantic import ValidationError as PydanticValidationError

        from src.weixin_marketing.models import DeliveryResolveInput

        automation_id, run_id, stepped, _ = self._drive_to_unknown(
            service, tenant_id, group_id, adapter
        )
        with pytest.raises(PydanticValidationError):
            DeliveryResolveInput(verdict="not_delivered", decision="confirmed_not_sent")

    def test_retry_failed_none_prepared_confirm_only(
        self, service, tenant_id, bindings, adapter
    ):
        """R52 分支①：机器安全路径——末次 attempt effect=none 且 phase=prepared
        （从未开始）→ 无需 resolve 决定，confirm 即可建新 attempt。"""
        _, group_id = bindings
        automation_id, run_id, stepped, _ = self._drive_to_failed(
            service, tenant_id, group_id, adapter
        )
        delivery = stepped["delivery"]
        from src.desktop_automation import attempts as da_attempts

        attempts = da_attempts.list_delivery_attempts(str(delivery["id"]), tenant_id)
        assert attempts[-1]["effect"] == "none" and attempts[-1]["phase"] == "prepared"
        # 未做任何 resolve 决定，仅 confirm → 放行
        retry = service.retry_delivery(tenant_id, str(delivery["id"]), "owner-1", confirm=True)
        assert retry["attempt_no"] == 2

    def test_retry_rebuilds_when_dedupe_key_reuses_dead_invocation(
        self, service, tenant_id, bindings, adapter
    ):
        """P2-5：同键残留死 invocation（cancelled，模拟上轮重试崩溃孤儿）→ retry
        不绑死键——换新 request_id 重建新 invocation（键 per-attempt 递增），
        新链路正常执行至终态。"""
        _, group_id = bindings
        automation_id, run_id, stepped, device_id = self._drive_to_failed(
            service, tenant_id, group_id, adapter
        )
        delivery = stepped["delivery"]
        # 制造残留：同键（a:2）enqueue 后取消（queued→cancelled 终态）
        from src.local_tools.service import LocalInvocationService

        residual_args = dict(
            repository.get_invocation(str(stepped["invocation_id"]), tenant_id)[
                "arguments_json"
            ]
        )
        residual_args["request_id"] = f"residual-{uuid.uuid4().hex[:8]}"
        residual = LocalInvocationService().enqueue(
            tenant_id=tenant_id, user_id="owner-1",
            device_id=device_id, tool_name=residual_args["operation"],
            arguments=residual_args, provider_key=residual_args.get("provider_key"),
            business_kind="desktop_automation",
            business_ref={"delivery_id": str(delivery["id"]), "run_id": str(run_id),
                          "scenario_key": "weixin.fixed_content.v1",
                          "task_ref": automation_id},
            dedupe_key=f"delivery:{delivery['id']}:a:2",
        )
        assert repository.request_cancel(str(residual["id"]), tenant_id) is True
        assert repository.get_invocation(str(residual["id"]), tenant_id)["state"] == "cancelled"

        # retry：不绑入死键——新建 queued invocation（键递增 a:3），attempt 链正常
        retry = service.retry_delivery(tenant_id, str(delivery["id"]), "owner-1", confirm=True)
        assert retry["attempt_no"] == 2
        assert str(retry["invocation_id"]) != str(residual["id"])
        new_invocation = repository.get_invocation(str(retry["invocation_id"]), tenant_id)
        assert new_invocation["state"] == "queued"
        assert new_invocation["dedupe_key"] == f"delivery:{delivery['id']}:a:3"

        # 新链路可正常执行至终态（claim→start→authorize→verified）
        token = _claim_and_start(tenant_id, str(retry["invocation_id"]), device_id)
        _authorize_and_finish(
            tenant_id, device_id, str(retry["invocation_id"]), token, retry["request_id"],
            target_version="iv-7-se-3", payload_hash=delivery["payload_hash"],
        )
        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        assert detail["deliveries"][0]["state"] == "succeeded"

    def test_retry_rejected_while_permit_issued(self, service, tenant_id, bindings, adapter):
        """存在未消费许可 → 409（R45 前置校验）"""
        _, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "唯一内容"}]
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(service, tenant_id, revision_id, device_id)
        stepped = executor.execute_next_delivery(prepared["run"])
        token = _claim_and_start(tenant_id, stepped["invocation_id"], device_id)
        permits.write_authorize(
            tenant_id=tenant_id, device_id=device_id, invocation_id=stepped["invocation_id"],
            claim_token_hash=sha256_hex(token), request_id=stepped["request_id"],
            target_version="iv-7-se-3", payload_hash=stepped["delivery"]["payload_hash"],
        )
        with pytest.raises(ConflictError):
            service.retry_delivery(tenant_id, str(stepped["delivery"]["id"]), "owner-1", confirm=True)

    def test_retry_rejected_when_invocation_not_terminal(self, service, tenant_id, bindings, adapter):
        """原 invocation 仍在 running（无许可）→ 409"""
        _, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "唯一内容"}]
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(service, tenant_id, revision_id, device_id)
        stepped = executor.execute_next_delivery(prepared["run"])
        _claim_and_start(tenant_id, stepped["invocation_id"], device_id)  # running，未回结果
        with pytest.raises(ConflictError):
            service.retry_delivery(tenant_id, str(stepped["delivery"]["id"]), "owner-1", confirm=True)

    def test_retry_rejected_for_succeeded_delivery(self, service, tenant_id, bindings, adapter):
        _, group_id = bindings
        automation_id, revision_id, _ = create_and_publish(
            service, tenant_id, group_id, blocks=[{"type": "text", "text_content": "唯一内容"}]
        )
        _, run_id = manual_run_pending(service, tenant_id, automation_id)
        device_id = str(uuid.uuid4())
        prepared = _claim_and_prepare(service, tenant_id, revision_id, device_id)
        stepped = executor.execute_next_delivery(prepared["run"])
        token = _claim_and_start(tenant_id, stepped["invocation_id"], device_id)
        _authorize_and_finish(
            tenant_id, device_id, stepped["invocation_id"], token, stepped["request_id"],
            target_version="iv-7-se-3", payload_hash=stepped["delivery"]["payload_hash"],
        )
        with pytest.raises(ConflictError):
            service.retry_delivery(tenant_id, str(stepped["delivery"]["id"]), "owner-1", confirm=True)

    def test_retry_e2e_second_execution_succeeds(self, service, tenant_id, bindings, adapter):
        """必修 D①：重试→新 invocation→write_authorize 成功→第二轮执行至终态
        （必修 A 的回归锚：consumed 许可不阻断重试链新签发）"""
        _, group_id = bindings
        automation_id, run_id, stepped, device_id = self._drive_to_failed(
            service, tenant_id, group_id, adapter
        )
        delivery = stepped["delivery"]
        retry = service.retry_delivery(tenant_id, str(delivery["id"]), "owner-1", confirm=True)
        assert retry["attempt_no"] == 2 and retry["request_id"]

        # 新 invocation：设备领取并启动
        new_invocation_id = retry["invocation_id"]
        token = _claim_and_start(tenant_id, new_invocation_id, device_id)
        # 新签发成功（attempt 1 的许可已 consumed，不再阻断）
        permit = permits.write_authorize(
            tenant_id=tenant_id, device_id=device_id, invocation_id=new_invocation_id,
            claim_token_hash=sha256_hex(token), request_id=retry["request_id"],
            target_version="iv-7-se-3", payload_hash=delivery["payload_hash"],
        )
        assert permit["permit_id"]
        # 第二轮执行至终态：applied+verified（证据按本次 request_id 编码）
        result = apply_operation_result(
            tenant_id=tenant_id, device_id=device_id, invocation_id=new_invocation_id,
            claim_token_hash=sha256_hex(token), request_id=retry["request_id"],
            effect="applied", phase="verified",
            evidence_ref=f"weixin-evidence:{retry['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert result["state"] == "succeeded"
        detail = service.get_run_detail(tenant_id, run_id, "owner-1")
        assert detail["deliveries"][0]["state"] == "succeeded"
        # P2-7（登记不改）：重试成功后 run 保持原始终态 failed——不重聚合（逐条账本
        # 诚实语义：run 终态记录当时的聚合结论，后续人工重试结果由 delivery/attempt
        # 层承载；finish_run 的 state NOT IN terminal 条件保证不改判）
        assert da_runs.get_run(run_id, tenant_id)["state"] == "failed"
        # 证据登记行绑定新 attempt
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM desktop_automation_evidence "
                "WHERE tenant_id = %s AND evidence_ref = %s",
                (tenant_id, f"weixin-evidence:{retry['request_id']}:1"),
            )
            assert cur.fetchone()["c"] == 1

    def test_concurrent_retry_exactly_one_wins(self, service, tenant_id, bindings, adapter):
        """必修 B：并发双击重试恰一成功（行锁串行 + 条件回置 + UNIQUE 仲裁）"""
        import threading

        _, group_id = bindings
        automation_id, run_id, stepped, device_id = self._drive_to_failed(
            service, tenant_id, group_id, adapter
        )
        delivery_id = str(stepped["delivery"]["id"])
        barrier = threading.Barrier(2)
        outcomes = {"ok": 0, "conflict": 0, "errors": []}

        def worker():
            try:
                barrier.wait(timeout=10)
                service.retry_delivery(tenant_id, delivery_id, "owner-1", confirm=True)
                outcomes["ok"] += 1
            except ConflictError:
                outcomes["conflict"] += 1
            except Exception as e:  # noqa: BLE001
                outcomes["errors"].append(repr(e))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not outcomes["errors"], outcomes["errors"]
        assert outcomes["ok"] == 1 and outcomes["conflict"] == 1
        # 恰一 attempt_no=2；delivery 在途
        from src.desktop_automation import attempts as da_attempts
        from src.desktop_automation import deliveries as da_deliveries

        attempts = da_attempts.list_delivery_attempts(delivery_id, tenant_id)
        assert [a["attempt_no"] for a in attempts] == [1, 2]
        assert da_deliveries.get_delivery(delivery_id, tenant_id)["state"] == "dispatched"
