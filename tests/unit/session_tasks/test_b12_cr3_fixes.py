"""CR 三审修复定向测试（8 P1 + 4 P2，逐项对应审核意见）。

- P1-1 控制请求幂等键纳入 expected_block_epoch（唯一索引）+ 同代双代请求处理；
- P1-2 binding 缺失/归属不符 → 拒绝不 ACK（无受信升级对象）；
- P1-3 settle 未知返回值 → SAVEPOINT 回滚 + 按结算异常升级；
- P1-5 effective_count 严格（bool/负数/0 fail-closed）；
- P1-6 deferred 全字段强类型 + DB 侧时间一致性（不用应用机墙钟）；
- P1-7 场景关闭仍可建草稿、publish 阻断；
- P1-8 绑定 API resolver 成员缺失/未实现 → 受控 400；
- P2-2 五路并发线程全记录 + result_a/result_b 必须成功 + rate-slot settled
  （在 tests/integration/test_session_task_five_way_concurrency.py 内）；
- P2-4 scheduler 决策 worker 注册失败不阻断控制 worker。
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
    fake_send_eligibility_gate,
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
from tests.unit.session_tasks.test_c3_decisions import _cfg, _device_dict


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
def _cr3_env(monkeypatch):
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


# ---------------------------------------------------------------------------
# P1-1：幂等键纳入 expected_block_epoch
# ---------------------------------------------------------------------------


class TestControlRequestIdempotencyKey:
    def test_same_epoch_new_block_epoch_creates_second_row(self, da_tenant, device_row, fake_scenario):
        from src.db.database import get_db_connection

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        task_id = _publish_fake_task(tenant_id, device_row, binding)
        epoch = int(_task_row(tenant_id, task_id)["control_epoch"])
        with get_db_connection() as conn:
            cursor = conn.cursor()
            first = control_requests_mod.insert_control_request(
                cursor, tenant_id, task_id, expected_control_epoch=epoch,
                expected_block_epoch=1, reason="fingerprint_mismatch",
                source_type="permit_denied", source_ref="t1",
            )
            second = control_requests_mod.insert_control_request(
                cursor, tenant_id, task_id, expected_control_epoch=epoch,
                expected_block_epoch=2, reason="fingerprint_mismatch",
                source_type="permit_denied", source_ref="t2",
            )
            dup = control_requests_mod.insert_control_request(
                cursor, tenant_id, task_id, expected_control_epoch=epoch,
                expected_block_epoch=2, reason="fingerprint_mismatch",
                source_type="permit_denied", source_ref="t3",
            )
            conn.commit()
        assert first is True and second is True and dup is False
        rows = _control_requests_of(tenant_id)
        assert sorted(int(r["expected_block_epoch"]) for r in rows) == [1, 2]

    def test_processor_stales_old_epoch_and_applies_new(self, da_tenant, device_row, fake_scenario):
        """同代两请求（block epoch 1/2）：epoch1 stale、epoch2 applied → 任务转人工。"""
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        task_id = _publish_fake_task(tenant_id, device_row, binding)
        epoch = int(_task_row(tenant_id, task_id)["control_epoch"])
        # 把 binding 推到 block epoch 2（模拟第二次异常已发生）
        _update_binding(
            tenant_id, binding["conversation_binding_id"],
            automation_blocked=True, automation_block_reason="fingerprint_mismatch",
            automation_block_epoch=2,
        )
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            for be in (1, 2):
                control_requests_mod.insert_control_request(
                    cursor, tenant_id, task_id, expected_control_epoch=epoch,
                    expected_block_epoch=be, reason="fingerprint_mismatch",
                    source_type="permit_denied", source_ref=f"t{be}",
                )
            conn.commit()
        control_requests_mod.run_control_request_tick(limit=10)
        rows = _control_requests_of(tenant_id)
        by_epoch = {int(r["expected_block_epoch"]): r["status"] for r in rows}
        assert by_epoch[1] == "stale"
        assert by_epoch[2] == "applied"
        assert _task_row(tenant_id, task_id)["status"] == "human_required"


# ---------------------------------------------------------------------------
# P1-2：binding 缺失 / 归属不符 → 拒绝不 ACK
# ---------------------------------------------------------------------------


class TestBindingLocationRejection:
    def test_binding_deleted_rejected_without_ack(self, da_tenant, device_row, fake_scenario):
        from src.db.database import get_db_connection
        from src.local_tools import operation_result
        from src.local_tools.operation_result import OperationResultError

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = _full_chain(
            tenant_id, device_row, binding
        )
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM session_task_fake_guard_bindings WHERE tenant_id=%s AND id=%s",
                (tenant_id, binding["conversation_binding_id"]),
            )
            conn.commit()
        with pytest.raises(OperationResultError) as exc:
            operation_result.apply_operation_result(
                tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
                claim_token_hash=token_hash,
                request_id=args["request_id"], effect="applied", phase="submitted",
                evidence_ref=f"fake-submission:{args['request_id']}:1",
                permit_id=permit["permit_id"], permit_token=permit["permit_token"],
            )
        assert exc.value.code == "BINDING_LOCATION_FAILED"
        # 不 ACK：attempt 未终结、无控制请求（无可升级对象）
        assert _control_requests_of(tenant_id) == []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT finished_at FROM desktop_automation_attempts WHERE tenant_id=%s AND invocation_id=%s",
                (tenant_id, invocation_id),
            )
            assert cursor.fetchone()["finished_at"] is None

    def test_guard_wrong_ownership_row_rejected(self, da_tenant, device_row, fake_scenario, monkeypatch):
        """guard 返回归属不符行（伪造 tenant）→ 拒绝不 ACK。"""
        from src.db.database import get_db_connection
        from src.local_tools import operation_result
        from src.local_tools.operation_result import OperationResultError

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = _full_chain(
            tenant_id, device_row, binding
        )
        real_guard = FakeGuard()

        def lying_lock(cursor, tid, binding_id):
            row = real_guard.lock_binding(cursor, tid, binding_id)
            if row is not None:
                row = dict(row)
                row["tenant_id"] = "other-tenant"  # 伪造归属
            return row

        import src.local_tools.operation_result as or_mod

        real_locate = or_mod._locate_and_lock_binding

        def lying_locate(cursor, guard, tid, inv):
            return real_locate(
                cursor, SimpleNamespace(lock_binding=lying_lock), tid, inv
            )

        monkeypatch.setattr(or_mod, "_locate_and_lock_binding", lying_locate)
        with pytest.raises(OperationResultError) as exc:
            operation_result.apply_operation_result(
                tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
                claim_token_hash=token_hash,
                request_id=args["request_id"], effect="applied", phase="submitted",
                evidence_ref=f"fake-submission:{args['request_id']}:1",
                permit_id=permit["permit_id"], permit_token=permit["permit_token"],
            )
        assert exc.value.code == "BINDING_LOCATION_FAILED"
        assert _control_requests_of(tenant_id) == []


# ---------------------------------------------------------------------------
# P1-3：settle 未知返回值 fail-closed
# ---------------------------------------------------------------------------


class TestSettleUnknownValueFailClosed:
    @pytest.mark.parametrize("garbage", [
        {"status": "ok"},                                 # 拼错的状态
        "done",                                           # 非 dict
        {"status": "anomaly_committed", "reason": ""},    # 空受控码
    ])
    def test_unknown_return_rolls_back_and_escalates(
        self, da_tenant, device_row, fake_scenario, garbage
    ):
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = _full_chain(
            tenant_id, device_row, binding
        )

        def garbage_settle(self, cursor, result):
            # 补齐6：先产生真实写入（探针 slot），再返回非法值——
            # "slot 为零"才能证明 SAVEPOINT 真回滚了写入
            cursor.execute(
                """
                INSERT INTO session_task_fake_guard_rate_slots (tenant_id, delivery_id, status)
                VALUES (%s, %s, 'reserved')
                ON CONFLICT (tenant_id, delivery_id) DO NOTHING
                """,
                (result["tenant_id"], result["delivery_id"]),
            )
            return garbage

        real_settle = FakeGuardAdapter.settle_operation_result
        FakeGuardAdapter.settle_operation_result = garbage_settle
        try:
            result = operation_result.apply_operation_result(
                tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
                claim_token_hash=token_hash,
                request_id=args["request_id"], effect="applied", phase="submitted",
                evidence_ref=f"fake-submission:{args['request_id']}:1",
                permit_id=permit["permit_id"], permit_token=permit["permit_token"],
            )
        finally:
            FakeGuardAdapter.settle_operation_result = real_settle
        assert result["acked"] is True
        # 未知返回值：SAVEPOINT 回滚 → slot 无行；升级 → 控制请求 + 阻断
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM session_task_fake_guard_rate_slots WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 0
        rows = _control_requests_of(tenant_id)
        assert len(rows) == 1 and rows[0]["source_type"] == "rate_settlement"
        assert _binding_row(tenant_id, binding["conversation_binding_id"])["automation_blocked"] is True


# ---------------------------------------------------------------------------
# P1-5/P1-6：effective_count 严格 + deferred 全字段强校验
# ---------------------------------------------------------------------------


class TestGateStrictValidation:
    @pytest.mark.parametrize("bad_count", [True, False, 0, -1, "1"])
    def test_non_positive_or_bool_effective_count_fail_closed(
        self, da_tenant, device_row, fake_scenario, bad_count
    ):
        from src.session_tasks import scenario_descriptor

        descriptor = build_fake_guard_descriptor()
        descriptor.send_eligibility_gate = (
            lambda cursor, task, decision: {"eligible": True, "effective_count": bad_count}
        )
        scenario_descriptor.register_scenario(descriptor)
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]),
                claimed["fence"], uuid.UUID(decision_id),
            )
        assert exc.value.code == "SEND_GATE_MALFORMED"

    @pytest.mark.parametrize("patch_fields", [
        {"server_now": None},
        {"deferred_reason": ""},
        {"response_revision": "rev-1"},
        {"retry_after_ms": 10 ** 9},  # retry 声明与实际差值远超 ±2000ms 容差
    ])
    def test_deferred_missing_or_inconsistent_fields_fail_closed(
        self, da_tenant, device_row, fake_scenario, patch_fields
    ):
        from src.session_tasks import scenario_descriptor

        def bad_gate(cursor, task, decision):
            outcome = fake_send_eligibility_gate(cursor, task, decision)
            if outcome.get("deferred") is True:
                outcome.update(patch_fields)
            return outcome

        descriptor = build_fake_guard_descriptor()
        descriptor.send_eligibility_gate = bad_gate
        scenario_descriptor.register_scenario(descriptor)
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(
                conn, tenant_id, str(device_row["id"]), interval_seconds=3600
            )
        # 预置 last_reserved_at（否则首次计数无间隔语义，gate 直接 eligible）
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_fake_guard_bindings SET last_reserved_at = clock_timestamp() "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, binding["conversation_binding_id"]),
            )
            conn.commit()
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]),
                claimed["fence"], uuid.UUID(decision_id),
            )
        assert exc.value.code == "SEND_GATE_MALFORMED"


# ---------------------------------------------------------------------------
# P1-7：场景关闭仍可建草稿、publish 阻断
# ---------------------------------------------------------------------------


class TestDraftGateSemantics:
    def test_fake_gate_closed_allows_draft_blocks_publish(self, da_tenant, device_row, fake_scenario):
        from src.session_tasks.models import TaskDraftCreatePayload
        from tests.unit.session_tasks.conftest import build_spec

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        fake_scenario.scenario_enabled = lambda tenant: False
        payload = TaskDraftCreatePayload.model_validate({
            "scenario_key": FAKE_KEY,
            "device_id": str(device_row["id"]),
            "account_binding_id": binding["account_binding_id"],
            "conversation_binding_id": binding["conversation_binding_id"],
            "spec": build_spec(),
        })
        # 场景关闭：草稿可存（draft_enabled 语义；B1.2 前 create-draft 无执行门控）
        created = service.create_draft(tenant_id, "user-1", payload)
        assert _task_row(tenant_id, created["task_id"])["status"] == "draft"
        # 发布阻断（场景门控按任务行场景分派）
        confirmation = service.issue_publish_confirmation(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
        )
        with pytest.raises(SessionTaskError) as exc:
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
                uuid.UUID(confirmation["confirmation_id"]),
            )
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# P1-8：绑定 API resolver 成员缺失/未实现 → 受控 400
# ---------------------------------------------------------------------------


class TestBindingApiScenarioDispatch:
    def test_unknown_scenario_fail_closed_400(self):
        from src.session_tasks.api import _scenario_binding_resolver

        with pytest.raises(SessionTaskError) as exc:
            _scenario_binding_resolver("no.such.scenario")
        assert exc.value.status_code == 400

    def test_boss_skeleton_resolver_raises_controlled(self):
        """B1.2 绑定 API 按 scenario_key 路由到 boss resolver（B1.3 骨架断言
        fail-closed 拒绝；B2 真实现后改为锁定路由与成员可达）。"""
        from src.boss_conversation.descriptor import build_boss_descriptor
        from src.session_tasks.api import _scenario_binding_resolver
        from src.session_tasks.scenario_descriptor import (
            register_scenario,
            unregister_descriptor,
        )

        descriptor = build_boss_descriptor()
        register_scenario(descriptor)
        try:
            resolver = _scenario_binding_resolver("boss.chat_reply.v1")
            assert resolver is descriptor.binding_resolver
            # B2：list_bindings/create_binding 成员真实可达（create 的通用路由形态
            # 缺 job/candidate 语义 → 受控 400，不猜测语义）
            from src.session_tasks.constants import SessionTaskError

            assert resolver.list_bindings("t", "u", str(__import__("uuid").uuid4()), 10) == []
            with pytest.raises(SessionTaskError):
                resolver.create_binding("t", "u", "d", str(__import__("uuid").uuid4()), "", "")
        finally:
            unregister_descriptor("boss.chat_reply.v1")


# ---------------------------------------------------------------------------
# P2-4：scheduler 决策 worker 注册失败不阻断控制 worker
# ---------------------------------------------------------------------------


class TestSchedulerFailureDomain:
    def test_decision_registration_failure_does_not_block_control_worker(self, monkeypatch):
        from src.scheduler import manager as scheduler_manager

        added = []

        class _StubScheduler:
            def add_job(self, fn, *args, **kwargs):
                added.append(kwargs.get("id"))

            def get_jobs(self):
                return []

        import src.session_tasks.config as st_config
        import src.session_tasks.control_requests as cr_mod

        monkeypatch.setattr(st_config, "get_session_tasks_config", lambda: replace(_cfg(), enabled=True))
        monkeypatch.setattr(
            scheduler_manager, "_ensure_any_session_scenario_registered", lambda: True
        )
        monkeypatch.setattr(cr_mod, "run_control_request_tick", lambda **kw: {})
        monkeypatch.setattr(
            "src.session_tasks.decisions.run_decision_tick", lambda **kw: {}, raising=False
        )

        mgr = scheduler_manager.ScheduledTaskManager.__new__(scheduler_manager.ScheduledTaskManager)
        mgr._scheduler = _StubScheduler()
        mgr._jobs = {}

        def patched_add_job(fn, *args, **kwargs):
            if kwargs.get("id") == "job_system_session_task_decisions":
                raise RuntimeError("决策 worker 注册爆炸（注入）")
            added.append(kwargs.get("id"))

        mgr._scheduler.add_job = patched_add_job
        mgr._register_system_jobs()

        assert "job_system_session_task_control_requests" in added, added
        assert "job_system_session_task_decisions" not in added, added
