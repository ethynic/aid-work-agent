"""CR 四审修复定向测试（P0-1 + P1-2 + P1-3 + 补齐 9/10）。

- P0-1：交叉改绑 A→B / B→A 双事务并发——定位 SAVEPOINT 释放旧锁 + 升级段
  按 binding id 排序重锁 + 二次确认，无 AB-BA 死锁，覆盖旧 ID 大于/小于新 ID；
- P1-2：deferred datetime 必须 aware（双 naive / 单侧 naive 一律拒绝）；
- P1-3：guard=None 场景 settle 异常 → 回滚主事务不 ACK（无升级通道）；
- 补齐9：真并发双 block + 控制请求（独立连接 + barrier，无丢失更新）；
- 补齐10：scenario_key 在定位窗口变化 → 重验不一致异常升级。
（P1-4 V1.9 对齐、补齐5/6/7/8 分见各自文件。五审顺带：交叉改绑并发证明力
（第二 barrier 强制互持旧锁）、受控码全匹配（尾换行拒绝）、双 block 去 task 预锁。）
"""
import json
import threading
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
def _cr4_env(monkeypatch):
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
# P0-1：交叉改绑双事务（SAVEPOINT 释放旧锁 + 排序重锁）
# ---------------------------------------------------------------------------


def _insert_fake_binding_with_id(conn, binding_id, tenant_id, device_id):
    """直插指定 id 的 fake 绑定（P0-1 需可控 id 排序：旧 ID 大于/小于新 ID）。"""
    account_binding_id = str(uuid.uuid4())
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO session_task_fake_guard_bindings
            (id, tenant_id, user_id, device_id, account_binding_id, daily_cap, interval_seconds)
        VALUES (%s, %s, 'user-1', %s, %s, 10, 0)
        """,
        (binding_id, tenant_id, device_id, account_binding_id),
    )
    conn.commit()
    return {"conversation_binding_id": str(binding_id),
            "account_binding_id": account_binding_id, "device_id": device_id}


class TestCrossRebindingLockOrder:
    def test_cross_rebinding_two_transactions_no_deadlock(
        self, da_tenant, device_row, fake_scenario, monkeypatch
    ):
        from src.db.database import get_db_connection
        from src.local_tools import operation_result
        from src.local_tools.operation_result import OperationResultError
        from src.local_tools.repository import create_device
        from src.session_tasks.models import TaskDraftCreatePayload
        from tests.unit.session_tasks.conftest import build_spec

        tenant_id = da_tenant
        from src.db.database import get_db_connection as _g

        def _grant_fake_capability(device_id):
            with _g() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT capabilities_json FROM local_tool_devices WHERE tenant_id=%s AND id=%s",
                    (tenant_id, device_id),
                )
                caps = cursor.fetchone()["capabilities_json"] or {}
                names = sorted(set(caps.get("capabilities") or []) | {"fake_send_v1"})
                cursor.execute(
                    "UPDATE local_tool_devices SET capabilities_json=%s WHERE tenant_id=%s AND id=%s",
                    (json.dumps({"providers": caps.get("providers") or ["weixin"], "capabilities": names}),
                     tenant_id, device_id),
                )
                conn.commit()

        _grant_fake_capability(str(device_row["id"]))
        devices = [device_row, create_device(
            tenant_id, "user-1", token_hash=uuid.uuid4().hex, name="cr4-device-2",
            capabilities={"providers": ["weixin"], "capabilities": [
                "session_task_v1", "session_observer_v1", "weixin_message_send_v2", "fake_send_v1"]},
        )]
        # 显式 id：u_lo < u_hi；task1 旧=u_lo 新=u_hi（旧<新），task2 旧=u_hi 新=u_lo（旧>新）
        u_a, u_b = sorted([str(uuid.uuid4()), str(uuid.uuid4())])
        with get_db_connection() as conn:
            binding_lo = _insert_fake_binding_with_id(conn, u_a, tenant_id, str(devices[0]["id"]))
            binding_hi = _insert_fake_binding_with_id(conn, u_b, tenant_id, str(devices[1]["id"]))
        chains = []
        for dev, binding, rt in ((devices[0], binding_lo, "rt-x1"), (devices[1], binding_hi, "rt-x2")):
            payload = TaskDraftCreatePayload.model_validate({
                "scenario_key": FAKE_KEY, "device_id": str(dev["id"]),
                "account_binding_id": binding["account_binding_id"],
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
            claimed = service.claim_task(_device_dict(tenant_id, dev), rt)
            assert claimed is not None and str(claimed["task_id"]) == str(created["task_id"])
            decision_id = _ready_reply_fake(tenant_id, dev, claimed, binding)
            invocation_id, args, token_hash = _materialize_running(
                tenant_id, dev, claimed, decision_id
            )
            from src.local_tools.permits import write_authorize

            permit = write_authorize(
                tenant_id=tenant_id, device_id=str(dev["id"]),
                invocation_id=invocation_id, claim_token_hash=token_hash,
                request_id=args["request_id"], target_version=args.get("target_version"),
                payload_hash=args.get("payload_hash"),
            )
            chains.append({"task_id": created["task_id"], "invocation_id": invocation_id,
                           "args": args, "token_hash": token_hash, "permit": permit})

        task1_old, task1_new = u_a, u_b  # 旧 ID 小于新 ID（u_a < u_b）
        task2_old, task2_new = u_b, u_a  # 旧 ID 大于新 ID（u_b > u_a）

        real_locate = operation_result._locate_and_lock_binding
        real_guard_lock = FakeGuard().lock_binding
        tls = threading.local()
        barrier = threading.Barrier(2)
        lock_barrier = threading.Barrier(2)

        def crossed_lock(cursor, tid, binding_id):
            rebind_to = getattr(tls, "rebind_to", None)
            if rebind_to is not None and not getattr(tls, "rebound", False):
                # 仅首次定位锁（tls.rebound 置位后，升级段排序重锁不再进入 barrier）：
                # 独立连接完成改绑 → 取得旧 binding 行锁 → 等第二 barrier。强制两
                # 事务在重验前互持相反旧锁——没有此 barrier 时两事务可能错峰，旧
                # 错误实现（不释放旧锁）也能串行通过，测试失去并发证明力。
                tls.rebound = True
                with get_db_connection() as c2:
                    cur2 = c2.cursor()
                    cur2.execute(
                        "UPDATE session_tasks SET conversation_binding_id=%s "
                        "WHERE tenant_id=%s AND id=%s",
                        (rebind_to, tid, tls.task_id),
                    )
                    c2.commit()
                # 事务级有限 lock_timeout：旧实现（mismatch 不回滚保存点、旧锁
                # 不释放）下升级重锁最多等 3s，不必等满 join 的 60s；新实现
                # ROLLBACK TO SAVEPOINT 会连同撤销本 SET LOCAL，无影响
                cursor.execute("SET LOCAL lock_timeout = '3s'")
                row = real_guard_lock(cursor, tid, binding_id)  # 确保旧 binding 锁已取得
                lock_barrier.wait(30)  # 互持相反旧锁后会齐，再进入重验/排序重锁
                return row
            return real_guard_lock(cursor, tid, binding_id)

        def crossed_locate(cursor, guard, tid, inv):
            return real_locate(cursor, SimpleNamespace(lock_binding=crossed_lock), tid, inv)

        # monkeypatch 自动还原（模块级函数与 guard 实例方法，泄漏会污染后续定位用例）
        monkeypatch.setattr(operation_result, "_locate_and_lock_binding", crossed_locate)
        guard_obj = fake_scenario.binding_guard
        monkeypatch.setattr(guard_obj, "lock_binding", crossed_lock)
        outcomes = {}

        def run(idx, task_id, new_id, chain):
            tls.task_id = task_id
            tls.rebind_to = new_id
            tls.rebound = False
            barrier.wait()
            try:
                result = operation_result.apply_operation_result(
                    tenant_id=tenant_id, device_id=str(devices[idx]["id"]),
                    invocation_id=chain["invocation_id"], claim_token_hash=chain["token_hash"],
                    request_id=chain["args"]["request_id"], effect="applied", phase="submitted",
                    evidence_ref=f"fake-submission:{chain['args']['request_id']}:1",
                    permit_id=chain["permit"]["permit_id"],
                    permit_token=chain["permit"]["permit_token"],
                )
                outcomes[idx] = {"acked": result.get("acked")}
            except OperationResultError as exc:
                outcomes[idx] = {"rejected": exc.code}

        t1 = threading.Thread(target=run, args=(0, chains[0]["task_id"], task1_new, chains[0]))
        t2 = threading.Thread(target=run, args=(1, chains[1]["task_id"], task2_new, chains[1]))
        t1.start(); t2.start()
        t1.join(60); t2.join(60)
        assert not t1.is_alive() and not t2.is_alive(), "交叉改绑双事务悬挂（AB-BA 死锁）"
        for idx in (0, 1):
            o = outcomes.get(idx)
            assert o is not None, f"线程 {idx} 无结果"
            if "rejected" in o:
                assert o["rejected"] == "BINDING_LOCATION_FAILED", o
            else:
                assert o["acked"] is True, o
                rows = [r for r in _control_requests_of(tenant_id)
                        if str(r["task_id"]) == str(chains[idx]["task_id"])
                        and r["source_type"] == "rate_settlement"]
                assert len(rows) == 1, rows


# ---------------------------------------------------------------------------
# P1-2：naive datetime 拒绝（双 naive / 单侧 naive）
# ---------------------------------------------------------------------------


class TestDeferredAwareDatetime:
    @pytest.mark.parametrize("naive_side", ["both", "server_now", "deferred_until"])
    def test_naive_datetime_rejected(self, da_tenant, device_row, fake_scenario, naive_side):
        from datetime import datetime, timedelta, timezone

        from src.session_tasks import scenario_descriptor

        def naive_gate(cursor, task, decision):
            outcome = fake_send_eligibility_gate(cursor, task, decision)
            if outcome.get("deferred") is True:
                now_utc = datetime.now(timezone.utc)
                if naive_side in ("both", "server_now"):
                    outcome["server_now"] = now_utc.replace(tzinfo=None)  # naive
                else:
                    outcome["server_now"] = now_utc  # 保持 aware
                if naive_side in ("both", "deferred_until"):
                    outcome["deferred_until"] = (
                        now_utc + timedelta(minutes=10)
                    ).replace(tzinfo=None)  # naive
                else:
                    outcome["deferred_until"] = now_utc + timedelta(minutes=10)
            return outcome

        descriptor = build_fake_guard_descriptor()
        descriptor.send_eligibility_gate = naive_gate
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
# P1-3：guard=None settlement 异常 → 回滚主事务不 ACK
# ---------------------------------------------------------------------------


class TestSettlementGuardlessEscalation:
    def test_settle_error_without_guard_rolls_back_and_rejects(self, da_tenant, device_row, fake_scenario):
        from src.db.database import get_db_connection
        from src.local_tools import operation_result
        from src.local_tools.operation_result import OperationResultError

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = _full_chain(
            tenant_id, device_row, binding
        )
        # 场景 gate/guard 均无（weixin 形态）：prepare 绕过扩展；settle 抛异常
        fake_scenario.send_eligibility_gate = None
        fake_scenario.binding_guard = None
        FakeGuardAdapter.settle_should_raise = True
        with pytest.raises(OperationResultError) as exc:
            operation_result.apply_operation_result(
                tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
                claim_token_hash=token_hash,
                request_id=args["request_id"], effect="applied", phase="submitted",
                evidence_ref=f"fake-submission:{args['request_id']}:1",
                permit_id=permit["permit_id"], permit_token=permit["permit_token"],
            )
        assert exc.value.code == "SETTLEMENT_ESCALATION_UNAVAILABLE"
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
        assert _control_requests_of(tenant_id) == []
        assert _binding_row(tenant_id, binding["conversation_binding_id"])["automation_blocked"] is False


# ---------------------------------------------------------------------------
# 补齐9：真并发双 block + 控制请求（独立连接 + barrier）
# ---------------------------------------------------------------------------


class TestConcurrentDoubleBlock:
    def test_concurrent_block_and_insert_no_lost_epoch(self, da_tenant, device_row, fake_scenario):
        from src.db.database import get_db_connection

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        task_id = _publish_fake_task(tenant_id, device_row, binding)
        task = _task_row(tenant_id, task_id)
        guard = fake_scenario.binding_guard
        results = {}
        barrier = threading.Barrier(2)
        read_barrier = threading.Barrier(2)

        def worker(idx):
            barrier.wait()  # 先会齐再取锁（不得持锁等 barrier——假死锁）
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    # 普通读取（无 FOR UPDATE）：预锁同一 task 行会使两 worker 在
                    # 进入 binding 竞争前已串行，失去并发性
                    cursor.execute(
                        "SELECT id, tenant_id, control_epoch, conversation_binding_id "
                        "FROM session_tasks WHERE tenant_id=%s AND id=%s",
                        (tenant_id, task_id),
                    )
                    task_row = cursor.fetchone()
                    read_barrier.wait()  # 普通读不持行锁，会齐后两连接真正竞争 binding 行锁
                    guard.lock_binding(cursor, tenant_id, binding["conversation_binding_id"])
                    epoch = guard.block_binding(cursor, dict(task_row), "fingerprint_mismatch")
                    control_requests_mod.insert_control_request(
                        cursor, tenant_id, task_row["id"],
                        expected_control_epoch=int(task_row["control_epoch"]),
                        expected_block_epoch=int(epoch),
                        reason="fingerprint_mismatch", source_type="permit_denied",
                        source_ref=f"concurrent-{idx}",
                    )
                    conn.commit()
                results[idx] = {"epoch": int(epoch)}
            except Exception as exc:  # noqa: BLE001
                results[idx] = {"error": repr(exc)}

        t1 = threading.Thread(target=worker, args=(0,))
        t2 = threading.Thread(target=worker, args=(1,))
        t1.start(); t2.start()
        t1.join(30); t2.join(30)
        assert all("error" not in results.get(i, {"error": 1}) for i in (0, 1)), results
        epochs = sorted(results[i]["epoch"] for i in (0, 1))
        assert epochs == [1, 2], f"block epoch 必须单调不丢（无丢失更新）: {results}"
        rows = _control_requests_of(tenant_id)
        assert sorted(int(r["expected_block_epoch"]) for r in rows) == [1, 2]


# ---------------------------------------------------------------------------
# 补齐10：scenario_key 在定位窗口变化 → 异常升级
# ---------------------------------------------------------------------------


class TestScenarioWindowChange:
    def test_scenario_key_change_escalates(self, da_tenant, device_row, fake_scenario):
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = _full_chain(
            tenant_id, device_row, binding
        )
        real_locate = operation_result._locate_and_lock_binding
        real_guard_lock = FakeGuard().lock_binding

        def scenario_flip_lock(cursor, tid, binding_id):
            # 定位首锁后、重读前：任务行 scenario_key 漂移
            with get_db_connection() as c2:
                cur2 = c2.cursor()
                cur2.execute(
                    "UPDATE session_tasks SET scenario_key='weixin.conversation.v1' "
                    "WHERE tenant_id=%s AND id=%s",
                    (tid, claimed["task_id"]),
                )
                c2.commit()
            return real_guard_lock(cursor, tid, binding_id)

        def locate_with_flip(cursor, guard, tid, inv):
            return real_locate(cursor, SimpleNamespace(lock_binding=scenario_flip_lock), tid, inv)

        operation_result._locate_and_lock_binding = locate_with_flip
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
        # 重验不一致（scenario 漂移）→ 异常升级：ACK + rate_settlement 控制请求
        assert result["acked"] is True
        rows = _control_requests_of(tenant_id)
        assert len(rows) == 1 and rows[0]["source_type"] == "rate_settlement"


# ---------------------------------------------------------------------------
# 五审顺带：受控码校验必须全匹配（re.match+^$ 接受尾换行 → fullmatch）
# ---------------------------------------------------------------------------


class TestControlledCodeFullmatch:
    def test_terminal_reason_trailing_newline_rejected(self):
        """terminal reason 尾换行必须拒绝（否则畸形码持久化/比对漂移）。"""
        from src.session_tasks.decisions import _is_controlled_code, _validate_gate_outcome_v19

        assert _is_controlled_code("rate_limit") is True
        assert _is_controlled_code("rate_limit\n") is False
        with pytest.raises(SessionTaskError) as exc:
            _validate_gate_outcome_v19({"terminal": "human_required", "reason": "rate_limit\n"})
        assert exc.value.code == "SEND_GATE_MALFORMED"

    def test_settlement_reason_trailing_newline_rejected(self):
        """settle anomaly reason 尾换行必须拒绝（按未知返回值 fail-closed 升级）。"""
        from src.local_tools.operation_result import _is_controlled_code_local

        assert _is_controlled_code_local("rate_limit") is True
        assert _is_controlled_code_local("rate_limit\n") is False
