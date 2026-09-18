"""CR 复审修复定向测试（B1.2–B1.4 统一审核 12 阻断项 + 6 非阻断）。

与 test_b12_generalization.py 分工：本文件只覆盖复审修复点——
- 阻断 1（P0）：operation-result 冻结锁序（delivery 先于 binding）+ delivery 缺失拒绝不 ACK；
- 阻断 4：Phase A 门禁 fail-closed（空 outcome/未知 outcome/缺字段/非法 deferred/
  gate-guard 配置不一致）+ 幂等重 prepare 仍检查阻断（非阻断 a）；
- 阻断 5/6/7：控制请求租约竞态（旧 worker 不得影响重领 worker）、迁移 False 真重试、
  十次失败站内告警（/notifications）且不与 human_required 通知唯一键冲突；
- 阻断 8：binding 锁后重验定位链（改绑窗口 → 异常升级）；
- 阻断 9：settle 结构化结果（anomaly_committed=补建保留+升级）；
- 阻断 2/3 + 非阻断 b：场景门控分派（微信关不挡其他场景）、claim 响应 scenario_key、
  能力不匹配跳过继续找兼容场景任务；
- 非阻断 e：permits 拒绝副作用只落受控码。
"""

import json
import uuid
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.session_tasks import control_requests as control_requests_mod
from src.session_tasks import decisions as decisions_mod
from src.session_tasks import service
from src.session_tasks.constants import SessionTaskError

from tests.unit.session_tasks.fake_guard_scenario import (
    SCENARIO_KEY as FAKE_KEY,
    FakeGuard,
    FakeGuardAdapter,
    build_fake_guard_descriptor,
    create_fake_tables,
    insert_fake_binding,
)
from tests.unit.session_tasks.test_b12_generalization import (
    _binding_row,
    _conn_holder,
    _control_requests_of,
    _links_of,
    _materialize_running,
    _publish_and_claim_fake,
    _publish_fake_task,
    _ready_reply_fake,
    _task_row,
    _update_binding,
)
from tests.unit.session_tasks.test_c3_decisions import (
    _cfg,
    _device_dict,
)


@pytest.fixture()
def da_tenant(tenant_id):
    """conftest tenant_id + 测后补清底座表行与 fake 表行（照 b12 主文件范式）。"""
    from src.db.database import get_db_connection

    tables = (
        "desktop_automation_attempts", "desktop_automation_evidence",
        "desktop_automation_deliveries", "desktop_automation_runs",
        "desktop_automation_occurrences", "desktop_automation_outbox",
        "desktop_automation_events", "desktop_automation_audit_events",
        "desktop_automation_quota_buckets", "local_tool_operation_permits",
        "local_tool_events", "local_tool_invocations",
        "session_task_fake_guard_rate_slots", "session_task_fake_guard_bindings",
        "session_task_control_requests", "session_task_notifications",
    )
    yield tenant_id
    with get_db_connection() as conn:
        for table in tables:
            try:
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            except Exception:  # noqa: BLE001
                conn.rollback()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM desktop_automation_subjects WHERE tenant_id=%s AND scenario_key=%s",
                (tenant_id, FAKE_KEY),
            )
            conn.commit()
        except Exception:  # noqa: BLE001
            conn.rollback()


@pytest.fixture()
def fake_scenario(da_tenant):
    from src.session_tasks import scenario_descriptor

    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        create_fake_tables(conn)
    descriptor = build_fake_guard_descriptor()
    scenario_descriptor.register_scenario(descriptor)
    FakeGuardAdapter.settle_should_raise = False
    FakeGuardAdapter.settle_anomaly_reason = None
    FakeGuardAdapter.settle_calls = []
    yield descriptor
    scenario_descriptor.unregister_descriptor(FAKE_KEY)
    FakeGuardAdapter.settle_should_raise = False
    FakeGuardAdapter.settle_anomaly_reason = None


@pytest.fixture(autouse=True)
def _cr_fix_env(monkeypatch):
    import src.session_tasks.config as st_config
    import src.weixin_conversation.config as wx_config
    import src.weixin_conversation.registration as registration

    monkeypatch.setattr(registration, "get_session_tasks_config", lambda: replace(_cfg(), enabled=True))
    monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: True)
    monkeypatch.setattr(st_config, "tenant_allowed", lambda tenant: True)
    registration.ensure_registered()
    monkeypatch.setattr(decisions_mod, "get_session_tasks_config", lambda: _cfg())
    monkeypatch.setattr(decisions_mod, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr("src.services.session_record.record_background_llm_usage", lambda usage, **kw: None)
    monkeypatch.setattr(
        "src.services.billing.calculate_credit_cost_with_breakdown",
        lambda p, c, m, cached_input_tokens=0: (0.01, {}),
    )
    yield


# ---------------------------------------------------------------------------
# 阻断 4 + 非阻断 a：Phase A fail-closed / 幂等重 prepare 安全阻断
# ---------------------------------------------------------------------------


class TestPhaseAGateFailClosed:
    def _register_with_gate(self, gate):
        from src.session_tasks import scenario_descriptor

        descriptor = build_fake_guard_descriptor()
        descriptor.send_eligibility_gate = gate
        scenario_descriptor.register_scenario(descriptor)
        return descriptor

    def _chain(self, tenant_id, device_row, binding):
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        return claimed, decision_id

    def _prepare(self, tenant_id, device_row, claimed, decision_id):
        return decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]),
            claimed["fence"], uuid.UUID(decision_id),
        )

    def test_empty_dict_outcome_fail_closed(self, da_tenant, device_row, fake_scenario):
        self._register_with_gate(lambda cursor, task, decision: {})
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id = self._chain(tenant_id, device_row, binding)
        with pytest.raises(SessionTaskError) as exc:
            self._prepare(tenant_id, device_row, claimed, decision_id)
        assert exc.value.code == "SEND_GATE_MALFORMED"
        assert _links_of(tenant_id, claimed["task_id"]) == []

    def test_unknown_outcome_fail_closed(self, da_tenant, device_row, fake_scenario):
        self._register_with_gate(lambda cursor, task, decision: {"postponed": True})
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id = self._chain(tenant_id, device_row, binding)
        with pytest.raises(SessionTaskError) as exc:
            self._prepare(tenant_id, device_row, claimed, decision_id)
        assert exc.value.code == "SEND_GATE_MALFORMED"
        assert _links_of(tenant_id, claimed["task_id"]) == []

    def test_eligible_missing_effective_count_fail_closed(self, da_tenant, device_row, fake_scenario):
        self._register_with_gate(lambda cursor, task, decision: {"eligible": True})
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id = self._chain(tenant_id, device_row, binding)
        with pytest.raises(SessionTaskError) as exc:
            self._prepare(tenant_id, device_row, claimed, decision_id)
        assert exc.value.code == "SEND_GATE_MALFORMED"

    @pytest.mark.parametrize("bad", [
        # V1.9 deferred：字段集缺失（server_now/deferred_until/reason/revision 缺）→ malformed
        {"deferred": True, "effective_count": 1, "retry_after_ms": 100},
        {"deferred": True, "effective_count": 1, "server_now": None, "deferred_until": None,
         "retry_after_ms": 0, "deferred_reason": "rate_interval", "response_revision": 1},
        {"deferred": True, "effective_count": 1, "retry_after_ms": -5, "server_now": None,
         "deferred_until": None, "deferred_reason": "rate_interval", "response_revision": 1},
    ])
    def test_malformed_deferred_fail_closed(self, da_tenant, device_row, fake_scenario, bad):
        self._register_with_gate(lambda cursor, task, decision: dict(bad))
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(
                conn, tenant_id, str(device_row["id"]), interval_seconds=3600
            )
        claimed, decision_id = self._chain(tenant_id, device_row, binding)
        with pytest.raises(SessionTaskError) as exc:
            self._prepare(tenant_id, device_row, claimed, decision_id)
        assert exc.value.code == "SEND_GATE_MALFORMED"

    def test_guard_without_gate_config_invalid(self, da_tenant, device_row, fake_scenario):
        from src.session_tasks import scenario_descriptor

        descriptor = build_fake_guard_descriptor()
        descriptor.send_eligibility_gate = None  # 只留 guard：配置不一致
        scenario_descriptor.register_scenario(descriptor)
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id = self._chain(tenant_id, device_row, binding)
        with pytest.raises(SessionTaskError) as exc:
            self._prepare(tenant_id, device_row, claimed, decision_id)
        assert exc.value.code == "SEND_GATE_CONFIG_INVALID"
        assert _links_of(tenant_id, claimed["task_id"]) == []

    def test_gate_without_guard_config_invalid(self, da_tenant, device_row, fake_scenario):
        from src.session_tasks import scenario_descriptor

        descriptor = build_fake_guard_descriptor()
        descriptor.binding_guard = None  # 只留 gate：无处落库计数，同样不一致
        scenario_descriptor.register_scenario(descriptor)
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id = self._chain(tenant_id, device_row, binding)
        with pytest.raises(SessionTaskError) as exc:
            self._prepare(tenant_id, device_row, claimed, decision_id)
        assert exc.value.code == "SEND_GATE_CONFIG_INVALID"

    def test_idempotent_reprepare_still_checks_blocked(self, da_tenant, device_row, fake_scenario):
        """非阻断 a：幂等重 prepare 跳过重复计数，但不跳过安全阻断。"""
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        first = self._prepare(tenant_id, device_row, claimed, decision_id)
        assert first["invocation_id"]
        count_before = int(_binding_row(tenant_id, binding["conversation_binding_id"])["rate_trigger_count"])
        _update_binding(
            tenant_id, binding["conversation_binding_id"],
            automation_blocked=True, automation_block_reason="fingerprint_mismatch",
        )
        with pytest.raises(SessionTaskError) as exc:
            self._prepare(tenant_id, device_row, claimed, decision_id)
        assert exc.value.code == "CONFLICT" and "同步阻断" in str(exc.value)
        assert int(_binding_row(tenant_id, binding["conversation_binding_id"])["rate_trigger_count"]) == count_before


# ---------------------------------------------------------------------------
# 阻断 5/6/7：控制请求租约竞态 / 真重试 / 站内告警
# ---------------------------------------------------------------------------


class TestControlRequestLeaseGuard:
    def _insert_request(self, tenant_id, task_id, control_epoch, block_epoch=1):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            inserted = control_requests_mod.insert_control_request(
                cursor, tenant_id, task_id,
                expected_control_epoch=control_epoch, expected_block_epoch=block_epoch,
                reason="fingerprint_mismatch", source_type="permit_denied", source_ref="test",
            )
            conn.commit()
        assert inserted is True
        return _control_requests_of(tenant_id)[0]

    def _expire_and_reclaim(self, tenant_id, old_row):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_control_requests SET processing_lease_expires_at = NOW() - INTERVAL '1 second' "
                "WHERE id=%s AND tenant_id=%s",
                (old_row["id"], tenant_id),
            )
            conn.commit()
        new_rows = control_requests_mod._claim_requests(10)
        assert len(new_rows) == 1 and str(new_rows[0]["id"]) == str(old_row["id"])
        return dict(new_rows[0]), dict(old_row)

    def test_stale_worker_retry_cannot_clobber_new_claim(self, da_tenant, device_row, fake_scenario):
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        task_id = _publish_fake_task(tenant_id, device_row, binding)
        task = _task_row(tenant_id, task_id)
        old_row = self._insert_request(tenant_id, task_id, int(task["control_epoch"]))
        new_row, old_snapshot = self._expire_and_reclaim(tenant_id, old_row)
        assert new_row["processing_owner"] != old_snapshot["processing_owner"]
        outcome = control_requests_mod._mark_retry(old_snapshot)
        assert outcome == "lease_lost"
        rows = _control_requests_of(tenant_id)
        assert rows[0]["status"] == "processing"
        assert rows[0]["processing_owner"] == new_row["processing_owner"]
        assert int(rows[0]["retry_count"]) == 0  # 旧租约 worker 不增加次数

    def test_stale_worker_failed_cannot_clobber_new_claim(self, da_tenant, device_row, fake_scenario):
        from src.db.database import get_db_connection

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        task_id = _publish_fake_task(tenant_id, device_row, binding)
        task = _task_row(tenant_id, task_id)
        old_row = self._insert_request(tenant_id, task_id, int(task["control_epoch"]))
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_control_requests SET retry_count=%s, "
                "processing_lease_expires_at = NOW() - INTERVAL '1 second' WHERE id=%s AND tenant_id=%s",
                (control_requests_mod.MAX_RETRIES - 1, old_row["id"], tenant_id),
            )
            conn.commit()
        new_row, old_snapshot = self._expire_and_reclaim(tenant_id, old_row)
        outcome = control_requests_mod._mark_retry(old_snapshot)
        assert outcome == "lease_lost"
        rows = _control_requests_of(tenant_id)
        assert rows[0]["status"] == "processing"  # 未被旧 worker 标 failed
        assert int(rows[0]["retry_count"]) == control_requests_mod.MAX_RETRIES - 1

    def test_migration_false_really_retries(self, da_tenant, device_row, fake_scenario, monkeypatch):
        """阻断 6：迁移返回 False → 受租约保护的 retry 转换真实推进。"""
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        task_id = _publish_fake_task(tenant_id, device_row, binding)
        task = _task_row(tenant_id, task_id)
        self._insert_request(tenant_id, task_id, int(task["control_epoch"]), block_epoch=0)
        claimed_rows = control_requests_mod._claim_requests(10)
        assert len(claimed_rows) == 1
        # _process_one 内是函数级 from .decisions import _apply_task_transition——
        # 打补丁须落 decisions 模块属性（调用时读取）
        monkeypatch.setattr(decisions_mod, "_apply_task_transition", lambda *a, **k: False)
        outcome = control_requests_mod._process_one(dict(claimed_rows[0]))
        assert outcome == "retried"
        rows = _control_requests_of(tenant_id)
        assert rows[0]["status"] == "pending"
        assert int(rows[0]["retry_count"]) == 1
        assert rows[0]["next_retry_at"] is not None

    def test_max_retries_failed_writes_notice_and_audit(self, da_tenant, device_row, fake_scenario):
        """阻断 7：十次失败 → failed + 脱敏审计 + /notifications 站内告警。"""
        from src.db.database import get_db_connection

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        task_id = _publish_fake_task(tenant_id, device_row, binding)
        task = _task_row(tenant_id, task_id)
        self._insert_request(tenant_id, task_id, int(task["control_epoch"]), block_epoch=0)
        claimed_rows = control_requests_mod._claim_requests(10)
        row = dict(claimed_rows[0])
        row["retry_count"] = control_requests_mod.MAX_RETRIES - 1
        outcome = control_requests_mod._mark_retry(row)
        assert outcome == "failed"
        rows = _control_requests_of(tenant_id)
        assert rows[0]["status"] == "failed"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM session_task_notifications WHERE tenant_id=%s AND task_id=%s",
                (tenant_id, task_id),
            )
            notices = [dict(r) for r in cursor.fetchall()]
            cursor.execute(
                "SELECT COUNT(*) AS n FROM desktop_automation_audit_events "
                "WHERE tenant_id=%s AND kind='control_request_failed'",
                (tenant_id,),
            )
            audit_n = int(cursor.fetchone()["n"])
        assert any(n["status"] == "failed" for n in notices), notices
        assert audit_n == 1

    def test_failed_notice_does_not_conflict_with_human_required_notice(
        self, da_tenant, device_row, fake_scenario
    ):
        """阻断 7：告警（expected epoch）与后续 human_required 通知（迁移后 epoch）不冲突。"""
        from src.db.database import get_db_connection
        from src.session_tasks.decisions import _transition_task_standalone

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        task_id = _publish_fake_task(tenant_id, device_row, binding)
        task = _task_row(tenant_id, task_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            control_requests_mod.record_failed_notice(
                conn, tenant_id, task_id, "控制迁移失败（测试）", int(task["control_epoch"])
            )
            conn.commit()
        assert _transition_task_standalone(tenant_id, uuid.UUID(task_id), "human_required", "x") is True
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, control_epoch FROM session_task_notifications "
                "WHERE tenant_id=%s AND task_id=%s ORDER BY control_epoch",
                (tenant_id, task_id),
            )
            notices = [dict(r) for r in cursor.fetchall()]
        assert [(n["status"], int(n["control_epoch"])) for n in notices] == [
            ("failed", int(task["control_epoch"])),
            ("human_required", int(task["control_epoch"]) + 1),
        ]


# ---------------------------------------------------------------------------
# 阻断 1（P0）/8/9：operation-result 冻结锁序 / 锁后重验 / 结构化结算
# ---------------------------------------------------------------------------


def _full_chain(tenant_id, device_row, binding):
    from src.local_tools.permits import write_authorize

    claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
    decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
    invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
    permit = write_authorize(
        tenant_id=tenant_id, device_id=str(device_row["id"]),
        invocation_id=invocation_id, claim_token_hash=token_hash,
        request_id=args["request_id"], target_version=args.get("target_version"),
        payload_hash=args.get("payload_hash"),
    )
    return claimed, decision_id, invocation_id, args, token_hash, permit


def _delivery_id_of(tenant_id, invocation_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT delivery_id FROM desktop_automation_attempts WHERE tenant_id=%s AND invocation_id=%s",
            (tenant_id, invocation_id),
        )
        return str(cursor.fetchone()["delivery_id"])


class TestOperationResultLockOrder:
    def test_delivery_missing_rejected_without_ack(self, da_tenant, device_row, fake_scenario):
        from src.db.database import get_db_connection
        from src.local_tools import operation_result
        from src.local_tools.operation_result import OperationResultError

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = _full_chain(
            tenant_id, device_row, binding
        )
        delivery_id = _delivery_id_of(tenant_id, invocation_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM desktop_automation_deliveries WHERE tenant_id=%s AND id=%s",
                (tenant_id, delivery_id),
            )
            conn.commit()
        with pytest.raises(OperationResultError) as exc:
            operation_result.apply_operation_result(
                tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
                claim_token_hash=token_hash,
                request_id=args["request_id"], effect="unknown", phase="unknown",
            )
        assert exc.value.code == "DELIVERY_NOT_FOUND"
        # 不 ACK：attempt/invocation 保持原状（可重投）
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT state, finished_at FROM local_tool_invocations WHERE tenant_id=%s AND id=%s",
                (tenant_id, invocation_id),
            )
            inv = cursor.fetchone()
            cursor.execute(
                "SELECT finished_at FROM desktop_automation_attempts WHERE tenant_id=%s AND invocation_id=%s",
                (tenant_id, invocation_id),
            )
            attempt = cursor.fetchone()
        assert inv["state"] == "running" and inv["finished_at"] is None
        assert attempt["finished_at"] is None

    def test_unknown_path_lock_order_delivery_before_binding(
        self, da_tenant, device_row, fake_scenario, monkeypatch
    ):
        """阻断 1（P0）：unknown 路径锁序断言——delivery 锁先于场景 binding 锁。"""
        from src.db.database import get_db_connection
        from src.desktop_automation import deliveries as da_deliveries
        from src.local_tools import operation_result

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = _full_chain(
            tenant_id, device_row, binding
        )
        order = []
        real_locate = operation_result._locate_and_lock_binding
        real_lock_delivery = da_deliveries.lock_delivery

        def spy_lock_delivery(cursor, delivery_id, tid):
            order.append("delivery")
            return real_lock_delivery(cursor, delivery_id, tid)

        def spy_locate(cursor, guard, tid, inv):
            real_guard_lock = guard.lock_binding

            def spy_lock_binding(cursor_, tid_, binding_id_):
                order.append("binding")
                return real_guard_lock(cursor_, tid_, binding_id_)

            return real_locate(cursor, SimpleNamespace(lock_binding=spy_lock_binding), tid, inv)

        monkeypatch.setattr(da_deliveries, "lock_delivery", spy_lock_delivery)
        monkeypatch.setattr(operation_result, "_locate_and_lock_binding", spy_locate)
        operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="unknown", phase="unknown",
        )
        assert "delivery" in order and "binding" in order
        assert order.index("delivery") < order.index("binding"), order

    def test_binding_revalidation_mismatch_escalates(self, da_tenant, device_row, fake_scenario):
        """阻断 8：无锁定位后、加锁前任务改绑 → 锁后重验不一致 → 异常升级。"""
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
            other_binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = _full_chain(
            tenant_id, device_row, binding
        )
        real_locate = operation_result._locate_and_lock_binding
        real_guard_lock = FakeGuard().lock_binding

        def rebinding_lock(cursor, tid, binding_id):
            # 模拟定位窗口内任务改绑：锁定旧行前，任务行 conversation_binding_id 已改指
            with get_db_connection() as c2:
                cur2 = c2.cursor()
                cur2.execute(
                    "UPDATE session_tasks SET conversation_binding_id=%s WHERE tenant_id=%s AND id=%s",
                    (other_binding["conversation_binding_id"], tid, claimed["task_id"]),
                )
                c2.commit()
            return real_guard_lock(cursor, tid, binding_id)

        def spy_locate(cursor, guard, tid, inv):
            return real_locate(
                cursor, SimpleNamespace(lock_binding=rebinding_lock), tid, inv
            )

        operation_result._locate_and_lock_binding = spy_locate
        try:
            result = operation_result.apply_operation_result(
                tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
                claim_token_hash=token_hash,
                request_id=args["request_id"], effect="applied", phase="submitted",
                evidence_ref=f"fake-submission:{args['request_id']}:1",
                permit_id=permit["permit_id"], permit_token=permit["permit_token"],
            )
        finally:
            operation_result._locate_and_lock_binding = real_locate
        # 回执照常 ACK + 重验不一致升级（控制请求 + 旧绑定阻断 + 审计）
        assert result["acked"] is True
        rows = _control_requests_of(tenant_id)
        assert len(rows) == 1 and rows[0]["source_type"] == "rate_settlement"
        # 升级阻断落在任务行当前（改绑后）绑定——与控制请求 epoch 复核同一绑定
        blocked = _binding_row(tenant_id, other_binding["conversation_binding_id"])
        assert blocked["automation_blocked"] is True
        assert int(blocked["automation_block_epoch"]) == 1

    def test_settle_anomaly_committed_keeps_backfill_and_escalates(self, da_tenant, device_row, fake_scenario):
        """阻断 9：anomaly_committed=补建行保留 + 升级（阻断+控制请求+审计）。"""
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = _full_chain(
            tenant_id, device_row, binding
        )
        FakeGuardAdapter.settle_anomaly_reason = "rate_ledger_anomaly"
        result = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="submitted",
            evidence_ref=f"fake-submission:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert result["acked"] is True
        # 补建行保留（与 settle 异常路径的关键差异）
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM session_task_fake_guard_rate_slots WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 1
        rows = _control_requests_of(tenant_id)
        assert len(rows) == 1 and rows[0]["source_type"] == "rate_settlement"
        assert _binding_row(tenant_id, binding["conversation_binding_id"])["automation_blocked"] is True


# ---------------------------------------------------------------------------
# 阻断 2/3 + 非阻断 b：场景门控分派 / claim scenario_key / 能力饥饿
# ---------------------------------------------------------------------------


class TestScenarioLifecycleGates:
    def test_weixin_gate_closed_does_not_block_fake_scenario(
        self, da_tenant, device_row, fake_scenario, monkeypatch
    ):
        """阻断 2：微信开关关闭时，fake 场景任务的 publish/claim 不受影响。"""
        import src.weixin_conversation.config as wx_config

        monkeypatch.setattr(wx_config, "scenario_enabled", lambda tenant: False)
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        task_id = _publish_fake_task(tenant_id, device_row, binding)  # publish 不被微信开关阻断
        claimed = service.claim_task(_device_dict(tenant_id, device_row), "rt-gate")
        assert claimed is not None and str(claimed["task_id"]) == str(task_id)
        # 阻断 3：claim 响应携带任务场景
        assert claimed["scenario_key"] == FAKE_KEY

    def test_fake_gate_closed_allows_draft_blocks_publish(self, da_tenant, device_row, fake_scenario, monkeypatch):
        """CR 三审 P1-7：fake 场景关闭 → 草稿仍可创建保存（draft_enabled 语义），
        发布 403（微信开关状态无关）。旧断言"关闭时 create-draft 403"已按审核
        意见作废；同语义用例另见 test_b12_cr3_fixes.TestDraftGateSemantics。"""
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        descriptor = fake_scenario
        monkeypatch.setattr(descriptor, "scenario_enabled", lambda tenant: False)
        from src.session_tasks.models import TaskDraftCreatePayload

        from tests.unit.session_tasks.conftest import build_spec

        payload = TaskDraftCreatePayload.model_validate({
            "scenario_key": FAKE_KEY,
            "device_id": str(device_row["id"]),
            "account_binding_id": binding["account_binding_id"],
            "conversation_binding_id": binding["conversation_binding_id"],
            "spec": build_spec(),
        })
        created = service.create_draft(tenant_id, "user-1", payload)
        assert _task_row(tenant_id, created["task_id"])["status"] == "draft"
        confirmation = service.issue_publish_confirmation(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
        )
        with pytest.raises(SessionTaskError) as exc:
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
                uuid.UUID(confirmation["confirmation_id"]),
            )
        assert exc.value.status_code == 403

    def test_claim_skips_incompatible_capability_task(self, da_tenant, device_row, fake_scenario):
        """非阻断 b：首个候选能力不匹配只跳过，继续领后面兼容场景任务。"""
        from src.db.database import get_db_connection

        tenant_id = da_tenant
        # 先建 weixin 任务（设备具备微信能力才能发布）——之后剥夺该能力再领取
        _, weixin_claimed = _publish_weixin_only(tenant_id, device_row)
        with _conn_holder() as conn:
            fake_binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        fake_task_id = _publish_fake_task(tenant_id, device_row, fake_binding)
        # 剥夺微信发送能力：weixin 候选在 claim 时能力不匹配
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT capabilities_json FROM local_tool_devices WHERE tenant_id=%s AND id=%s",
                (tenant_id, device_row["id"]),
            )
            caps = cursor.fetchone()["capabilities_json"] or {}
            caps["capabilities"] = [c for c in caps.get("capabilities", []) if c != "weixin_message_send_v2"]
            cursor.execute(
                "UPDATE local_tool_devices SET capabilities_json=%s WHERE tenant_id=%s AND id=%s",
                (json.dumps(caps), tenant_id, device_row["id"]),
            )
            conn.commit()
        claimed = service.claim_task(_device_dict(tenant_id, device_row), "rt-skip")
        # 不因首个候选 409：跳过微信候选领到 fake 任务（多场景不饥饿）
        assert claimed is not None and str(claimed["task_id"]) == str(fake_task_id)
        assert claimed["scenario_key"] == FAKE_KEY


def _publish_weixin_only(tenant_id, device_row):
    """发布（不领取）一个微信场景任务，返回 (task_id, None)。"""
    from src.db.database import get_db_connection
    from datetime import datetime, timedelta, timezone as _tz
    from src.weixin_conversation.bindings import create_binding
    from src.session_tasks.models import TaskDraftCreatePayload
    from tests.unit.session_tasks.conftest import build_spec

    account = str(uuid.uuid4())
    binding = create_binding(tenant_id, "user-1", str(device_row["id"]), account, "direct", label="微信任务")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE bs_weixin_conversation_bindings
            SET verification_status='verified', verifier_version='test-fixture',
                identity_version=1, verified_at=CURRENT_TIMESTAMP, expires_at=%s
            WHERE tenant_id=%s AND id=%s
            """,
            (datetime.now(_tz.utc) + timedelta(days=30), tenant_id, binding["conversation_binding_id"]),
        )
        conn.commit()
    payload = TaskDraftCreatePayload.model_validate({
        "device_id": str(device_row["id"]),
        "account_binding_id": account,
        "conversation_binding_id": binding["conversation_binding_id"],
        "spec": build_spec(),
    })
    created = service.create_draft(tenant_id, "user-1", payload)
    confirmation = service.issue_publish_confirmation(
        tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
    )
    service.publish_task(
        tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
        uuid.UUID(confirmation["confirmation_id"]),
    )
    return created["task_id"], None
