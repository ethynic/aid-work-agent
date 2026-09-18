"""B1.2 五路并发死锁与阻断穿透测试（设计 §5.5.5 无死锁论证 / 计划 §5.7）。

真实 PostgreSQL、独立连接（每线程独立取池连接）、库级 lock_timeout=5s /
statement_timeout=20s（测后复位；无 ALTER 权限时记录并降级为仅死锁码断言）；
五路并发对同一 task/binding 运行 N 轮：

1. 许可 write-authorize（subject → run → invocation → delivery →(guard)binding）
2. 回执 operation-result（invocation → attempt → delivery →(guard)binding → 结算）
3. 暂停 control_task（subject → task）
4. 绑定失效（模拟指纹事件；fake: subject→task→binding 阻断+控制请求；微信: 身份漂移）
5. 频控结算（第二 decision 的回执结算，SAVEPOINT 路径）

断言：无死锁（40P01）、无锁超时（55P03）、无语句超时（57014）、无线程悬挂、
无协议外异常；同一 delivery 至多一个许可（无 permit 穿透）；fake 路径阻断后
prepare-send/write-authorize 拒绝（阻断穿透）；微信路径零控制请求且失效后拒绝。

时间口径统一 DB 侧（DB 时钟比宿主机快 ~5.8s），不做宿主机墙钟细粒度比较。
"""
import json
import threading
import time
import uuid
from dataclasses import replace
from urllib.parse import urlsplit

import pytest

pytestmark = [pytest.mark.integration]

from src.session_tasks import control_requests as control_requests_mod
from src.session_tasks import decisions as decisions_mod
from src.session_tasks import service
from src.session_tasks.constants import SessionTaskError

from tests.unit.session_tasks.conftest import build_spec
from tests.unit.session_tasks.fake_guard_scenario import (
    SCENARIO_KEY as FAKE_KEY,
    FakeGuardAdapter,
    build_fake_guard_descriptor,
    create_fake_tables,
    insert_fake_binding,
)
from tests.unit.session_tasks.test_c3_decisions import (
    _batch_seq_by_assignment,
    _cfg,
    _device_dict,
    _fake_model,
    _feed_batch,
    _submit_reply,
)

STORM_TIMEOUT_SECONDS = 90
DEADLOCK_CODES = {"40P01", "55P03", "57014"}


# ---------------------------------------------------------------------------
# 环境
# ---------------------------------------------------------------------------


def _db_name():
    url = __import__("os").getenv("DATABASE_URL", "")
    return (urlsplit(url).path or "").lstrip("/") or None


@pytest.fixture(scope="module", autouse=True)
def _module_env():
    from src.db.database import get_db_connection, get_postgres_pool, init_postgres_pool

    if get_postgres_pool() is None:
        init_postgres_pool()
    with get_db_connection() as conn:
        from src.session_tasks.init_tables import init_session_task_tables
        from src.weixin_conversation.init_tables import init_weixin_conversation_tables

        init_session_task_tables(conn)
        init_weixin_conversation_tables(conn)
        create_fake_tables(conn)
    yield


@pytest.fixture()
def storm_env(monkeypatch):
    """门控放开（进程内模块属性，线程可见）+ 库级超时 + 场景注册。"""
    import src.session_tasks.config as st_config
    import src.session_tasks.service as service_mod
    import src.weixin_conversation.config as wx_config
    import src.weixin_conversation.registration as registration
    from src.db.database import close_postgres_pool, get_db_connection, get_postgres_pool, init_postgres_pool

    monkeypatch.setattr(registration, "get_session_tasks_config", lambda: replace(_cfg(), enabled=True))
    monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: True)
    monkeypatch.setattr(wx_config, "scenario_enabled", lambda tenant: True)
    # service 模块在 import 时绑定 config 符号（from .config import ...）——须补 patch
    # service 自身命名空间（照 tests/unit/session_tasks/conftest._gate_open 范式）
    monkeypatch.setattr(st_config, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr(service_mod, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr(service_mod, "get_session_tasks_config", lambda: _cfg())
    monkeypatch.setattr(decisions_mod, "get_session_tasks_config", lambda: _cfg())
    monkeypatch.setattr(decisions_mod, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr("src.services.session_record.record_background_llm_usage", lambda usage, **kw: None)
    monkeypatch.setattr(
        "src.services.billing.calculate_credit_cost_with_breakdown",
        lambda p, c, m, cached_input_tokens=0: (0.01, {}),
    )
    registration.ensure_registered()
    from src.session_tasks import scenario_descriptor

    scenario_descriptor.register_scenario(build_fake_guard_descriptor())
    FakeGuardAdapter.settle_should_raise = False

    dbname = _db_name()
    applied = False
    if dbname:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(f"ALTER DATABASE {dbname} SET lock_timeout = '5s'")
                cursor.execute(f"ALTER DATABASE {dbname} SET statement_timeout = '20s'")
                conn.commit()
            applied = True
        except Exception as exc:  # noqa: BLE001 无 ALTER 权限：记录并降级（仍有死锁码断言）
            print(f"[five-way] 库级超时设置失败（降级为仅死锁码断言）: {exc}")
        if applied:  # 回收池使全部连接带新设置
            try:
                close_postgres_pool()
            except Exception:  # noqa: BLE001
                pass
            init_postgres_pool()
    yield
    scenario_descriptor.unregister_descriptor(FAKE_KEY)
    if applied and dbname:
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(f"ALTER DATABASE {dbname} RESET lock_timeout")
                cursor.execute(f"ALTER DATABASE {dbname} RESET statement_timeout")
                conn.commit()
            try:
                close_postgres_pool()
            except Exception:  # noqa: BLE001
                pass
            init_postgres_pool()
        except Exception as exc:  # noqa: BLE001
            print(f"[five-way] 库级超时复位失败（需手工 RESET）: {exc}")


@pytest.fixture()
def tenant_id():
    value = f"st5w_{uuid.uuid4().hex[:12]}"
    yield value
    _cleanup_tenant(value)


def _cleanup_tenant(tenant_id):
    from src.db.database import get_db_connection

    tables = (
        "chat_records", "session_task_decision_attempts", "session_tasks_idempotency_keys",
        "session_task_control_requests", "session_task_cost_reservations",
        "session_task_execution_links", "session_task_decisions", "session_task_events",
        "session_task_messages", "session_task_batches", "session_task_confirmations",
        "session_task_specs", "session_task_assignments", "session_task_texts", "session_tasks",
        "bs_weixin_conversation_bindings", "session_task_fake_guard_bindings",
        "session_task_fake_guard_rate_slots",
        "desktop_automation_attempts", "desktop_automation_evidence",
        "desktop_automation_deliveries", "desktop_automation_runs",
        "desktop_automation_occurrences", "desktop_automation_outbox",
        "desktop_automation_events", "desktop_automation_audit_events",
        "desktop_automation_quota_buckets", "local_tool_operation_permits",
        "local_tool_events", "local_tool_invocations",
    )
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
                "DELETE FROM desktop_automation_subjects WHERE tenant_id=%s AND scenario_key IN ('weixin.conversation.v1', %s)",
                (tenant_id, FAKE_KEY),
            )
            cursor.execute("DELETE FROM local_tool_devices WHERE tenant_id = %s", (tenant_id,))
        except Exception:  # noqa: BLE001
            conn.rollback()
        conn.commit()


# ---------------------------------------------------------------------------
# 链路构建
# ---------------------------------------------------------------------------


def _fresh_conn():
    from src.db.database import get_db_connection

    return get_db_connection()


def _device_row(tenant_id, capabilities_extra=()):
    from src.local_tools.repository import create_device

    return create_device(
        tenant_id, "user-1", token_hash=uuid.uuid4().hex, name="st5w-device",
        capabilities={
            "providers": ["weixin"],
            "capabilities": ["session_task_v1", "session_observer_v1", "weixin_message_send_v2", *capabilities_extra],
        },
    )


def _weixin_verified_binding(tenant_id, device_id):
    from datetime import datetime, timedelta, timezone

    from src.db.database import get_db_connection
    from src.weixin_conversation.bindings import create_binding

    account_binding_id = str(uuid.uuid4())
    binding = create_binding(tenant_id, "user-1", device_id, account_binding_id, "direct", label="五路并发会话")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # 名称定位上下文形态（verifier_version=current-login-name-v1）：真实微信
        # 链路的 submitted 回执策略（receipt_mode/context）只在名称上下文绑定上
        # 冻结进 invocation——风暴断言"至少一次回执成功"依赖该形态
        cursor.execute(
            """
            UPDATE bs_weixin_conversation_bindings
            SET verification_status='resolved', verifier_version='current-login-name-v1',
                identity_version=1, verified_at=CURRENT_TIMESTAMP, expires_at=%s
            WHERE tenant_id=%s AND id=%s
            """,
            (datetime.now(timezone.utc) + timedelta(days=30), tenant_id, binding["conversation_binding_id"]),
        )
        conn.commit()
    return {
        "conversation_binding_id": binding["conversation_binding_id"],
        "account_binding_id": account_binding_id,
        "device_id": device_id,
    }


def _create_task(tenant_id, device_row, binding, scenario_key):
    from src.session_tasks.models import TaskDraftCreatePayload

    payload = TaskDraftCreatePayload.model_validate({
        "scenario_key": scenario_key,
        "device_id": str(device_row["id"]),
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
    return created["task_id"]


def _ready_reply(tenant_id, device_row, claimed, binding, input_version):
    _, decision = _submit_reply(tenant_id, device_row, claimed, binding, input_version, [
        {"local_message_id": f"m-{uuid.uuid4().hex[:6]}", "sender": "peer", "text": "在吗",
         "source_evidence_ref": "e1"}
    ])
    model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "您好，周五14:00可以吗"}, ensure_ascii=False)])
    decisions_mod.run_decision_tick(model_call=model)
    return decision["decision_id"]


def _materialize(tenant_id, device_row, claimed, decision_id, *, permit=False):
    """prepare → 定向领取 → started（→ 许可签发）；返回 (invocation_id, args, token_hash, permit)。"""
    from src.local_tools import repository
    from src.local_tools.permits import write_authorize
    from src.local_tools.security import generate_claim_token, sha256_hex

    prepared = decisions_mod.prepare_send(
        tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
    )
    assert prepared.get("invocation_id"), f"prepare 未物化: {prepared}"
    token = generate_claim_token()
    token_hash = sha256_hex(token)
    claimed_inv = decisions_mod.claim_session_invocation(
        tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
        uuid.UUID(prepared["invocation_id"]), token_hash, 300, None,
    )
    assert claimed_inv["invocation"] is not None
    repository.mark_started(prepared["invocation_id"], tenant_id, token_hash)
    args = repository.get_invocation(prepared["invocation_id"], tenant_id)["arguments_json"]
    permit_row = None
    if permit:
        permit_row = write_authorize(
            tenant_id=tenant_id, device_id=str(device_row["id"]),
            invocation_id=prepared["invocation_id"], claim_token_hash=token_hash,
            request_id=args["request_id"], target_version=args.get("target_version"),
            payload_hash=args.get("payload_hash"),
        )
    return prepared["invocation_id"], args, token_hash, permit_row


def _task_version(tenant_id, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT version FROM session_tasks WHERE tenant_id=%s AND id=%s", (tenant_id, task_id))
        return int(cursor.fetchone()["version"])


def _task_status(tenant_id, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM session_tasks WHERE tenant_id=%s AND id=%s", (tenant_id, task_id))
        return cursor.fetchone()["status"]


# ---------------------------------------------------------------------------
# 绑定失效（模拟指纹事件消费）
# ---------------------------------------------------------------------------


def _invalidate_binding_fake(tenant_id, binding_id):
    """设计 §5.5.5 绑定失效行：无锁枚举 → 每任务独立事务 subject → task → binding
    阻断 + 幂等控制请求（多项按 task_id 稳定排序）。"""
    from src.session_tasks.scenario_descriptor import get_descriptor

    guard = get_descriptor(FAKE_KEY).binding_guard
    with _fresh_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM session_tasks WHERE tenant_id=%s AND conversation_binding_id=%s ORDER BY id",
            (tenant_id, binding_id),
        )
        task_ids = [str(r["id"]) for r in cursor.fetchall()]
    epochs = []
    for task_id in sorted(task_ids):
        with _fresh_conn() as conn:  # 每项独立事务
            cursor = conn.cursor()
            cursor.execute(
                "SELECT scenario_key FROM session_tasks WHERE tenant_id=%s AND id=%s",
                (tenant_id, task_id),
            )
            scenario_key = cursor.fetchone()["scenario_key"]
            cursor.execute(
                "SELECT id FROM desktop_automation_subjects WHERE tenant_id=%s AND scenario_key=%s AND kind='task' AND ref=%s FOR UPDATE",
                (tenant_id, scenario_key, task_id),
            )
            cursor.execute(
                "SELECT id, tenant_id, control_epoch, conversation_binding_id FROM session_tasks WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (tenant_id, task_id),
            )
            task_row = dict(cursor.fetchone())
            guard.lock_binding(cursor, tenant_id, str(task_row["conversation_binding_id"]))
            new_epoch = guard.block_binding(cursor, task_row, "fingerprint_mismatch")
            control_requests_mod.insert_control_request(
                cursor, tenant_id, task_row["id"],
                expected_control_epoch=int(task_row["control_epoch"]),
                expected_block_epoch=int(new_epoch),
                reason="fingerprint_mismatch", source_type="permit_denied",
                source_ref="fingerprint_event",
            )
            conn.commit()
            epochs.append(new_epoch)
    return epochs


def _invalidate_binding_weixin(tenant_id, binding_id):
    """微信路径绑定失效：身份漂移（identity_version+1 → 旧 target_version 失配拒发）。"""
    with _fresh_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE bs_weixin_conversation_bindings SET identity_version=identity_version+1 "
            "WHERE tenant_id=%s AND id=%s",
            (tenant_id, binding_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# 五路 storm
# ---------------------------------------------------------------------------


def _run_storm(tenant_id, *, fake: bool):
    from src.local_tools import operation_result

    scenario_key = FAKE_KEY if fake else "weixin.conversation.v1"
    device = _device_row(tenant_id, ("fake_send_v1",) if fake else ())
    if fake:
        with _fresh_conn() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device["id"]), daily_cap=10)
    else:
        binding = _weixin_verified_binding(tenant_id, str(device["id"]))
    task_id = _create_task(tenant_id, device, binding, scenario_key)
    claimed = service.claim_task(_device_dict(tenant_id, device), "rt-5w")
    assert claimed is not None and str(claimed["task_id"]) == str(task_id)
    # 续租：风暴构造（决策/物化）不测租约过期语义，续到 10 分钟隔离 DB 抖动
    with _fresh_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session_task_assignments SET lease_expires_at = NOW() + INTERVAL '10 minutes' "
            "WHERE tenant_id=%s AND task_id=%s AND is_current=TRUE",
            (tenant_id, task_id),
        )
        conn.commit()
    # 决策 A：物化 + 领取 + started + 预签许可（频控结算流）
    decision_a = _ready_reply(tenant_id, device, claimed, binding, 1)
    inv_a, args_a, token_a, permit_a = _materialize(tenant_id, device, claimed, decision_a, permit=True)
    # 决策 B：第二个入站批次（input_version=2；A 已物化在途，supersede 只作废决策
    # 不回收在途 invocation）——B 链不预签许可，留给并发许可流
    decision_b = _ready_reply(tenant_id, device, claimed, binding, 2)
    inv_b, args_b, token_b, _ = _materialize(tenant_id, device, claimed, decision_b, permit=False)

    invalidate = _invalidate_binding_fake if fake else _invalidate_binding_weixin
    errors = {}

    def _run(name, fn):
        try:
            fn()
            errors[name] = None
        except Exception as exc:  # noqa: BLE001 汇总后统一断言
            errors[name] = exc

    barrier = threading.Barrier(5)

    # CR 非阻断 c：每线程记录精确结果（成功/协议内异常码），测后逐线程断言
    def t_permit():
        barrier.wait()
        from src.local_tools.permits import write_authorize

        write_authorize(
            tenant_id=tenant_id, device_id=str(device["id"]),
            invocation_id=inv_b, claim_token_hash=token_b,
            request_id=args_b["request_id"], target_version=args_b.get("target_version"),
            payload_hash=args_b.get("payload_hash"),
        )

    def t_result_a():
        barrier.wait()
        operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device["id"]), invocation_id=inv_a,
            claim_token_hash=token_a,
            request_id=args_a["request_id"], effect="applied", phase="submitted",
            evidence_ref=(
                f"fake-submission:{args_a['request_id']}:1" if fake
                else f"weixin-submission:{args_a['request_id']}:1"
            ),
            permit_id=permit_a["permit_id"], permit_token=permit_a["permit_token"],
        )

    def t_pause():
        barrier.wait()
        service.control_task(tenant_id, "user-1", uuid.UUID(task_id), "pause", _task_version(tenant_id, task_id))

    def t_invalidate():
        barrier.wait()
        invalidate(tenant_id, binding["conversation_binding_id"])

    def t_result_b():
        barrier.wait()
        operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device["id"]), invocation_id=inv_b,
            claim_token_hash=token_b,
            request_id=args_b["request_id"], effect="unknown", phase="unknown",
        )

    threads = [
        threading.Thread(target=_run, args=("permit", t_permit), name="permit"),
        threading.Thread(target=_run, args=("result_a", t_result_a), name="result_a"),
        threading.Thread(target=_run, args=("pause", t_pause), name="pause"),
        threading.Thread(target=_run, args=("invalidate", t_invalidate), name="invalidate"),
        threading.Thread(target=_run, args=("result_b", t_result_b), name="result_b"),
    ]
    for t in threads:
        t.daemon = True
        t.start()
    deadline = time.monotonic() + STORM_TIMEOUT_SECONDS
    for t in threads:
        t.join(timeout=max(1, int(deadline - time.monotonic())))
    hung = [t.name for t in threads if t.is_alive()]
    return {"errors": errors, "hung": hung, "task_id": task_id, "binding": binding,
            "inv_a": inv_a, "inv_b": inv_b, "device": device, "claimed": claimed,
            "token_b": token_b, "args_b": args_b}


def _permits_of(tenant_id):
    with _fresh_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, delivery_id, state FROM local_tool_operation_permits WHERE tenant_id=%s",
            (tenant_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def _assert_delivery_permit_unique(permits):
    counts = {}
    for p in permits:
        counts[p["delivery_id"]] = counts.get(p["delivery_id"], 0) + 1
    assert all(c <= 1 for c in counts.values()), f"同 delivery 多许可（穿透）: {counts}"


def _assert_no_lock_anomalies(errors, hung):
    from src.local_tools.operation_result import OperationResultError
    from src.local_tools.permits import PermitError

    assert not hung, f"五路并发线程悬挂（超 {STORM_TIMEOUT_SECONDS}s）: {hung}"
    for name, exc in errors.items():
        if exc is None:
            continue
        code = getattr(exc, "pgcode", None)
        assert code not in DEADLOCK_CODES, f"{name} 死锁/超时（pgcode={code}）: {exc}"
        assert isinstance(exc, (PermitError, SessionTaskError, OperationResultError)), (
            f"{name} 协议外异常（锁序/描述器接线破坏）: {type(exc).__name__}: {exc}"
        )


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


class TestFiveWayConcurrency:
    @pytest.mark.parametrize("round_no", [1, 2, 3])
    def test_fake_guard_path_five_way_rounds(self, storm_env, tenant_id, round_no):
        """fake 场景（binding_guard + SAVEPOINT 结算）五路并发 ×3 轮。"""
        outcome = _run_storm(tenant_id, fake=True)
        _assert_no_lock_anomalies(outcome["errors"], outcome["hung"])
        _assert_delivery_permit_unique(_permits_of(tenant_id))
        # CR 非阻断 c：每线程精确结果 + 至少一次回执成功 + 终态断言（覆盖 unknown/none）
        _assert_thread_outcomes(tenant_id, outcome)
        # 绑定失效已落地：阻断 + block epoch 前进 + 控制请求
        binding_row = _fake_binding_row(tenant_id, outcome["binding"]["conversation_binding_id"])
        assert binding_row["automation_blocked"] is True
        assert int(binding_row["automation_block_epoch"]) >= 1
        assert _control_request_count(tenant_id) >= 1
        # CR 三审 P2-2 + 四审补齐7：rate-slot 断言绑定 result_a（沿
        # invocation→attempt.delivery_id→slot 精确到行，防 result_b 掩盖）
        with _fresh_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM session_task_fake_guard_rate_slots WHERE tenant_id=%s AND delivery_id="
                "(SELECT delivery_id FROM desktop_automation_attempts WHERE tenant_id=%s AND invocation_id=%s)",
                (tenant_id, tenant_id, outcome["inv_a"]),
            )
            slot_row = cursor.fetchone()
        assert slot_row is not None, "result_a 对应的 rate slot 未落账"
        assert slot_row["status"] == "settled", slot_row
        # 阻断穿透：阻断后 binding 上的一切新发送面均拒绝
        _assert_blocked_penetration(tenant_id, outcome)

    @pytest.mark.parametrize("round_no", [1, 2])
    def test_weixin_path_five_way_rounds(self, storm_env, tenant_id, round_no):
        """微信路径（guard=None，锁面与基线一致）五路并发 ×2 轮。"""
        outcome = _run_storm(tenant_id, fake=False)
        _assert_no_lock_anomalies(outcome["errors"], outcome["hung"])
        _assert_delivery_permit_unique(_permits_of(tenant_id))
        _assert_thread_outcomes(tenant_id, outcome)
        # 微信绑定失效已落地（identity_version 精确 +1）
        with _fresh_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT identity_version FROM bs_weixin_conversation_bindings WHERE tenant_id=%s AND id=%s",
                (tenant_id, outcome["binding"]["conversation_binding_id"]),
            )
            assert int(cursor.fetchone()["identity_version"]) == 2
        # 微信永远零控制请求（guard=None 路径不产生任何控制副作用）
        assert _control_request_count(tenant_id) == 0
        # 微信路径失效后 write-authorize 拒绝（绑定身份漂移/任务状态变化）
        _assert_weixin_invalidated_write_authorize_rejects(tenant_id, outcome)


_THREAD_ALLOWED_CODES = {
    # 竞态下协议内允许的精确异常码（CR 非阻断 c：不得出现任意业务异常）。
    # CR 三审 P2-2：result_a/result_b 必须成功——五路里没有线程会删
    # attempt/delivery，回执接纳不依赖 permit/pause/invalidate 的输赢。
    "permit": {None, "INVOCATION_NOT_RUNNING", "ADAPTER_DENIED", "TASK_NOT_ACTIVE",
               "CONTROL_PENDING", "PERMIT_BINDING_INVALID", "TASK_EXPIRED"},
    "result_a": {None},
    "pause": {None, "CONFLICT", "ERR_STALE_ASSIGNMENT"},
    "invalidate": {None, "CONFLICT"},
    "result_b": {None},
}
_THREAD_NAMES = ("permit", "result_a", "pause", "invalidate", "result_b")


def _assert_thread_outcomes(tenant_id, outcome):
    """CR 非阻断 c：至少一次回执成功（result_a 必须成功）；其余线程只允许协议内
    精确异常码；attempt/delivery/rate-slot 覆盖 unknown/none 终态。"""
    from src.db.database import get_db_connection
    from src.local_tools.operation_result import OperationResultError
    from src.local_tools.permits import PermitError
    from src.session_tasks.constants import SessionTaskError

    errors = outcome["errors"]
    # 五线程都有结果记录（不得静默丢线程）
    assert set(errors.keys()) == set(_THREAD_NAMES), f"线程结果记录不全: {errors.keys()}"
    exc_types = {"permit": PermitError, "pause": SessionTaskError,
                 "invalidate": SessionTaskError, "result_b": OperationResultError}
    for name, allowed in _THREAD_ALLOWED_CODES.items():
        exc = errors.get(name)
        if exc is None:
            continue
        assert isinstance(exc, exc_types[name]), f"{name} 异常类型越界: {exc!r}"
        code = getattr(exc, "code", None)
        assert code in allowed, f"{name} 协议外异常码: {code} ({exc!r})"
    # 至少一次回执成功（result_a：预签许可的 submitted 回执必须被接纳）
    assert errors.get("result_a") is None, f"submitted 回执必须成功: {errors.get('result_a')!r}"
    inv_a, inv_b = outcome["inv_a"], outcome["inv_b"]
    with _fresh_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, effect, phase, finished_at FROM desktop_automation_attempts WHERE tenant_id=%s AND invocation_id IN (%s,%s)",
            (tenant_id, inv_a, inv_b),
        )
        attempts = {str(r["id"]): dict(r) for r in cursor.fetchall()}
        cursor.execute(
            "SELECT id, state, effect FROM local_tool_invocations WHERE tenant_id=%s AND id IN (%s,%s)",
            (tenant_id, inv_a, inv_b),
        )
        invs = {str(r["id"]): dict(r) for r in cursor.fetchall()}
    # result_a 落账：submitted 提交事实被持久化（invocation succeeded + attempt
    # 保留 applied 上报值）
    assert invs[inv_a]["state"] == "succeeded", invs[inv_a]
    submitted_rows = [a for a in attempts.values() if a["effect"] == "applied"]
    assert submitted_rows, "至少一条 attempt 保留 applied 上报"
    # inv_b（unknown/none 路径覆盖）：回执成功则收敛 unknown，不得悬挂中间态
    assert invs[inv_b]["state"] in ("unknown", "failed", "succeeded"), invs[inv_b]
    if errors.get("result_b") is None:
        assert invs[inv_b]["state"] == "unknown", f"unknown 回执应收敛 unknown: {invs[inv_b]}"


def _control_request_count(tenant_id):
    with _fresh_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS n FROM session_task_control_requests WHERE tenant_id=%s",
            (tenant_id,),
        )
        return int(cursor.fetchone()["n"])


def _fake_binding_row(tenant_id, binding_id):
    with _fresh_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM session_task_fake_guard_bindings WHERE tenant_id=%s AND id=%s",
            (tenant_id, binding_id),
        )
        return dict(cursor.fetchone())


def _assert_blocked_penetration(tenant_id, outcome):
    """阻断穿透：阻断后同一 binding 上的新发送面全部拒绝（Phase A / 状态机）。

    任务若仍 active：新决策 prepare-send 必须 409（automation_blocked 或
    CONTROL_PENDING）；任务若已被暂停/迁移：prepare-send 同样 409（状态机拒绝）
    ——无论哪一路先到，blocked binding 上不允许新的可执行发送单元。
    """
    device = outcome["device"]
    claimed = outcome["claimed"]
    binding = outcome["binding"]
    task_id = outcome["task_id"]
    if _task_status(tenant_id, task_id) != "active":
        with pytest.raises(SessionTaskError) as exc:
            decision_id = _ready_reply(tenant_id, device, claimed, binding, 3)
            decisions_mod.prepare_send(
                tenant_id, device["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
                uuid.UUID(decision_id),
            )
        assert exc.value.status_code == 409
        return
    decision_id = _ready_reply(tenant_id, device, claimed, binding, 3)
    with pytest.raises(SessionTaskError) as exc:
        decisions_mod.prepare_send(
            tenant_id, device["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            uuid.UUID(decision_id),
        )
    assert exc.value.status_code == 409
    assert (
        "automation_blocked" in str(exc.value)
        or exc.value.code == "CONTROL_PENDING"
        or "任务状态不可发送" in str(exc.value)
    )


def _assert_weixin_invalidated_write_authorize_rejects(tenant_id, outcome):
    """微信绑定失效后：旧 invocation 的 write-authorize 拒绝（身份漂移/状态变化）。"""
    from src.local_tools import repository
    from src.local_tools.permits import PermitError, write_authorize

    device = outcome["device"]
    inv = repository.get_invocation(outcome["inv_b"], tenant_id)
    if inv is None or inv["state"] != "running":
        return  # 已被并发回执终态化：无许可面可验（许可唯一性已单独断言）
    try:
        write_authorize(
            tenant_id=tenant_id, device_id=str(device["id"]),
            invocation_id=outcome["inv_b"], claim_token_hash=outcome["token_b"],
            request_id=inv["arguments_json"]["request_id"],
            target_version=inv["arguments_json"].get("target_version"),
            payload_hash=inv["arguments_json"].get("payload_hash"),
        )
        raise AssertionError("微信绑定失效后 write-authorize 仍签发许可（阻断穿透）")
    except PermitError:
        pass
