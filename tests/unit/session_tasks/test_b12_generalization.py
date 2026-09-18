"""B1.2 泛化收尾定向测试（设计 §4.2/§4.3/§5.5.2/§5.5.4/§5.5.5；计划 §5）。

覆盖：
- Phase A 场景门禁（fake.guard.v1）：eligible 计数落库 / deferred 200 判别联合
  （不物化 invocation）/ terminal → 完整 human_required 迁移 + 409；
- binding 同步阻断穿透：blocked 后 prepare-send 与 write-authorize 均拒绝；
- pending 控制请求同步阻断（Phase A 与 write-authorize 各一）；
- session_task_control_requests：幂等写入 + 处理器 applied/stale/retry/failed +
  统一 human_required 迁移（任务/subject/epoch/通知）；
- permits 结构化拒绝：control_action → 副作用（阻断+epoch+1、控制请求、审计）
  先 commit 再返回拒绝；微信普通 denied 仍 rollback 且零控制请求（显式断言）；
- operation-result SAVEPOINT 结算：正常结算 / settle 失败 → 回执照常 ACK +
  异常升级（审计+阻断+rate_settlement 控制请求）；binding 定位任务链路；
- envelope（§4.3）：场景分派校验 / PATCH 场景权威（不得切换，409）/
  微信 spec 错误路径保真（loc 加 spec 前缀，字段路径与基线一致）；
- 九处切换的微信零变化显式断言（binding_guard=None、结果等价、无控制请求行）。

微信行为/锁面基线由 test_characterization_weixin.py（25 用例）锁定，本文件不重复。
"""

import json
import uuid
from dataclasses import replace

import pytest

from src.session_tasks import control_requests as control_requests_mod
from src.session_tasks import decisions as decisions_mod
from src.session_tasks import service
from src.session_tasks.constants import SessionTaskError

from tests.unit.session_tasks.conftest import (
    build_spec,
    publish_task_helper,
)
from tests.unit.session_tasks.fake_guard_scenario import (
    OPERATION_MESSAGE_SEND,
    SCENARIO_KEY as FAKE_KEY,
    FakeGuardAdapter,
    build_fake_guard_descriptor,
    create_fake_tables,
    drop_fake_tables,
    insert_fake_binding,
)
from tests.unit.session_tasks.test_c3_decisions import (
    _batch_seq_by_assignment,
    _cfg,
    _device_dict,
    _fake_model,
    _feed_batch,
    _publish_and_claim,
    _submit_reply,
    _task_row,
)

# desktop_automation/local_tool 行族（链路产生；测后补清）
_DA_TABLES = (
    "desktop_automation_attempts",
    "desktop_automation_evidence",
    "desktop_automation_deliveries",
    "desktop_automation_runs",
    "desktop_automation_occurrences",
    "desktop_automation_outbox",
    "desktop_automation_events",
    "desktop_automation_audit_events",
    "desktop_automation_quota_buckets",
    "local_tool_operation_permits",
    "local_tool_events",
    "local_tool_invocations",
)


@pytest.fixture()
def da_tenant(tenant_id):
    """conftest tenant_id + 测后补清底座表行与 fake 表行。"""
    yield tenant_id
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        for table in _DA_TABLES:
            try:
                cursor = conn.cursor()
                cursor.execute(f"DELETE FROM {table} WHERE tenant_id = %s", (tenant_id,))
            except Exception:  # noqa: BLE001
                conn.rollback()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM session_task_fake_guard_bindings WHERE tenant_id=%s", (tenant_id,)
            )
            cursor.execute(
                "DELETE FROM session_task_fake_guard_rate_slots WHERE tenant_id=%s", (tenant_id,)
            )
            cursor.execute(
                "DELETE FROM desktop_automation_subjects WHERE tenant_id=%s AND scenario_key=%s",
                (tenant_id, FAKE_KEY),
            )
        except Exception:  # noqa: BLE001
            conn.rollback()
        conn.commit()


@pytest.fixture()
def fake_scenario(da_tenant):
    """注册 fake.guard.v1 描述器 + 建 fake 表；测后注销并复位失败注入。"""
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
def _b12_env(monkeypatch):
    """放开决策/许可链门控 + 屏蔽计费（照 characterization 范式）。"""
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
    _batch_seq_by_assignment.clear()
    yield


# ---------------------------------------------------------------------------
# fake 场景全链辅助（publish → claim → batch/decision → 决策 tick → prepare）
# ---------------------------------------------------------------------------


def _grant_fake_capability(tenant_id, device_id):
    """给测试设备追加 fake_send_v1 能力（fake 场景发布/领取的前置）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT capabilities_json FROM local_tool_devices WHERE tenant_id=%s AND id=%s",
            (tenant_id, device_id),
        )
        caps = cursor.fetchone()["capabilities_json"] or {}
        names = sorted(set((caps.get("capabilities") if isinstance(caps, dict) else []) or []) | {"fake_send_v1"})
        providers = caps.get("providers") if isinstance(caps, dict) else ["weixin"]
        cursor.execute(
            "UPDATE local_tool_devices SET capabilities_json=%s WHERE tenant_id=%s AND id=%s",
            (json.dumps({"providers": providers or ["weixin"], "capabilities": names}), tenant_id, device_id),
        )
        conn.commit()


def _publish_fake_task(tenant_id, device_row, fake_binding):
    from src.session_tasks.models import TaskDraftCreatePayload

    _grant_fake_capability(tenant_id, device_row["id"])
    payload = TaskDraftCreatePayload.model_validate({
        "scenario_key": FAKE_KEY,
        "device_id": str(device_row["id"]),
        "account_binding_id": fake_binding["account_binding_id"],
        "conversation_binding_id": fake_binding["conversation_binding_id"],
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


def _publish_and_claim_fake(tenant_id, device_row, fake_binding):
    task_id = _publish_fake_task(tenant_id, device_row, fake_binding)
    claimed = service.claim_task(_device_dict(tenant_id, device_row), "rt-fake")
    assert claimed is not None and str(claimed["task_id"]) == str(task_id)
    return claimed


def _ready_reply_fake(tenant_id, device_row, claimed, fake_binding):
    _, decision = _submit_reply(tenant_id, device_row, claimed, fake_binding, 1, [
        {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
    ])
    model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "您好，周五14:00可以吗"}, ensure_ascii=False)])
    decisions_mod.run_decision_tick(model_call=model)
    return decision["decision_id"]


def _binding_row(tenant_id, binding_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM session_task_fake_guard_bindings WHERE tenant_id=%s AND id=%s",
            (tenant_id, binding_id),
        )
        return dict(cursor.fetchone())


def _links_of(tenant_id, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT decision_id, invocation_id FROM session_task_execution_links WHERE tenant_id=%s AND task_id=%s",
            (tenant_id, task_id),
        )
        return [dict(r) for r in cursor.fetchall()]


def _control_requests_of(tenant_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM session_task_control_requests WHERE tenant_id=%s ORDER BY id",
            (tenant_id,),
        )
        return [dict(r) for r in cursor.fetchall()]


def _update_binding(tenant_id, binding_id, **sets):
    from src.db.database import get_db_connection

    clause = ", ".join(f"{k}=%s" for k in sets)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE session_task_fake_guard_bindings SET {clause} WHERE tenant_id=%s AND id=%s",
            (*sets.values(), tenant_id, binding_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Phase A 门禁（fake 场景）
# ---------------------------------------------------------------------------


class TestPrepareSendPhaseAGate:
    def test_eligible_persists_trigger_count_and_materializes(self, da_tenant, device_row, fake_scenario):
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        prepared = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        assert prepared["invocation_id"]
        row = _binding_row(tenant_id, binding["conversation_binding_id"])
        assert int(row["rate_trigger_count"]) == 1
        assert str(row["last_rate_decision_id"]) == str(decision_id)
        # Phase A eligible 后进入 Phase B：物化完成
        assert _links_of(tenant_id, claimed["task_id"])

    def test_deferred_returns_200_union_without_materialization(self, da_tenant, device_row, fake_scenario):
        from datetime import datetime, timedelta, timezone

        from src.db.database import get_db_connection

        tenant_id = da_tenant
        with get_db_connection() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]), interval_seconds=3600)
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        # 制造"60 秒前刚发送过"的窗口占用 → 间隔未满足 → deferred
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_fake_guard_bindings SET rate_trigger_count=1, "
                "last_reserved_at = clock_timestamp() - INTERVAL '30 seconds', "
                "rate_trigger_date = (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, binding["conversation_binding_id"]),
            )
            conn.commit()
        result = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        # 冻结 200 判别联合（B3 Runtime 按此契约；本阶段仅冻结形状）
        assert result["status"] == "deferred" and result["deferred"] is True
        assert result["invocation_id"] is None
        assert result["retry_after_ms"] > 0
        assert result["deferred_reason"] == "rate_interval"
        assert result["deferred_until"] > result["server_now"]
        assert isinstance(result["response_revision"], int)
        # 不物化 invocation；决策保持 ready
        assert _links_of(tenant_id, claimed["task_id"]) == []
        # deferred 也落触发计数（§5.5.2/§5.5.3）
        row = _binding_row(tenant_id, binding["conversation_binding_id"])
        assert int(row["rate_trigger_count"]) == 2  # 预置 1 + 本次触发（同 decision 首次）

    def test_terminal_daily_cap_migrates_human_required_and_409(self, da_tenant, device_row, fake_scenario):
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]), daily_cap=2)
        # 计数 2 = 日上限，且 rate_trigger_date 置为 DB 侧当日（跨日归一化不重置）
        with _conn_holder() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_fake_guard_bindings SET rate_trigger_count=2, "
                "rate_trigger_date=(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, binding["conversation_binding_id"]),
            )
            conn.commit()
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
            )
        assert exc.value.status_code == 409
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "human_required"
        # 完整迁移：epoch/seq 推进 + 站内通知 + subject 状态
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM desktop_automation_subjects WHERE tenant_id=%s AND scenario_key=%s AND kind='task' AND ref=%s",
                (tenant_id, FAKE_KEY, str(claimed["task_id"])),
            )
            assert cursor.fetchone()["status"] == "human_required"
            cursor.execute(
                "SELECT COUNT(*) AS n FROM session_task_notifications WHERE tenant_id=%s AND task_id=%s AND status='human_required'",
                (tenant_id, claimed["task_id"]),
            )
            assert int(cursor.fetchone()["n"]) == 1
        assert _links_of(tenant_id, claimed["task_id"]) == []

    def test_blocked_binding_penetrates_nothing(self, da_tenant, device_row, fake_scenario):
        """阻断穿透（§5.5.5）：binding blocked → Phase A 拒绝并转人工，不物化。"""
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        # 先领取，再模拟指纹异常阻断（blocked binding 不可领取——分配面即拒绝）
        _update_binding(
            tenant_id, binding["conversation_binding_id"],
            automation_blocked=True, automation_block_reason="fingerprint_mismatch",
        )
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
            )
        assert exc.value.status_code == 409 and "automation_blocked" in str(exc.value)
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "human_required"
        assert _links_of(tenant_id, claimed["task_id"]) == []

    def test_idempotent_reprepare_skips_gate(self, da_tenant, device_row, fake_scenario):
        """幂等重 prepare：已物化决策不再过门禁、不重复计数。"""
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        first = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        # 物化后把额度顶满：重 prepare 不得再触发门禁（否则 daily cap 会转人工）
        _update_binding(tenant_id, binding["conversation_binding_id"], rate_trigger_count=99)
        second = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        assert second["invocation_id"] == first["invocation_id"]
        row = _binding_row(tenant_id, binding["conversation_binding_id"])
        assert int(row["rate_trigger_count"]) == 99  # 未再 +1

    def test_pending_control_request_blocks_prepare_send(self, da_tenant, device_row, fake_scenario):
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        task = _task_row(tenant_id, claimed["task_id"])
        with _conn_holder() as conn:
            control_requests_mod.insert_control_request(
                conn.cursor(), tenant_id, claimed["task_id"],
                expected_control_epoch=int(task["control_epoch"]),
                expected_block_epoch=0,
                reason="rate_settlement_probe", source_type="rate_settlement", source_ref="probe",
            )
            conn.commit()
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
            )
        assert exc.value.code == "CONTROL_PENDING" and exc.value.status_code == 409


def _conn_holder():
    """小工具：返回 get_db_connection（避免上面表达式里误写 None conn）。"""
    from src.db.database import get_db_connection

    return get_db_connection()


# ---------------------------------------------------------------------------
# 控制请求：幂等写入 + 处理器
# ---------------------------------------------------------------------------


class TestControlRequests:
    def _insert(self, conn, tenant_id, task_id, epoch, block_epoch=0, reason="probe",
                source_type="permit_denied"):
        return control_requests_mod.insert_control_request(
            conn.cursor(), tenant_id, task_id,
            expected_control_epoch=epoch, expected_block_epoch=block_epoch,
            reason=reason, source_type=source_type, source_ref="probe",
        )

    def test_insert_idempotent_and_pending_check(self, tenant_id):
        from src.db.database import get_db_connection

        task_id = str(uuid.uuid4())
        with get_db_connection() as conn:
            assert self._insert(conn, tenant_id, task_id, 3) is True
            assert self._insert(conn, tenant_id, task_id, 3) is False  # 幂等
            assert self._insert(conn, tenant_id, task_id, 4) is True  # epoch 前进 → 新行
            conn.commit()
            cursor = conn.cursor()
            assert control_requests_mod.has_pending_control_request(cursor, tenant_id, task_id) is True
        rows = _control_requests_of(tenant_id)
        assert len(rows) == 2

    def test_processor_applies_human_required_migration(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        published = publish_task_helper(tenant_id, verified_binding)
        task_id = published["task_id"]
        task = _task_row(tenant_id, task_id)
        with get_db_connection() as conn:
            self._insert(conn, tenant_id, task_id, int(task["control_epoch"]), reason="rate_limit",
                         source_type="permit_denied")
            conn.commit()
        stats = control_requests_mod.run_control_request_tick()
        assert stats["applied"] == 1
        migrated = _task_row(tenant_id, task_id)
        assert migrated["status"] == "human_required"
        assert int(migrated["control_epoch"]) == int(task["control_epoch"]) + 1
        rows = _control_requests_of(tenant_id)
        assert rows[0]["status"] == "applied"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM session_task_notifications WHERE tenant_id=%s AND task_id=%s AND status='human_required'",
                (tenant_id, task_id),
            )
            assert int(cursor.fetchone()["n"]) == 1

    def test_processor_stale_on_control_epoch_mismatch(self, tenant_id):
        from src.db.database import get_db_connection

        task_id = str(uuid.uuid4())  # 任务不存在 → stale（不覆盖任何状态）
        with get_db_connection() as conn:
            self._insert(conn, tenant_id, task_id, 7, reason="probe")
            conn.commit()
        stats = control_requests_mod.run_control_request_tick()
        assert stats["stale"] == 1
        assert _control_requests_of(tenant_id)[0]["status"] == "stale"

    def test_processor_stale_on_newer_block_epoch(self, tenant_id, device_row, fake_scenario):
        """较新的阻断不得被旧请求覆盖：block epoch 不符 → stale。"""
        from src.db.database import get_db_connection

        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        task_id = _publish_fake_task(tenant_id, device_row, binding)
        task = _task_row(tenant_id, task_id)
        # 写请求（期望 block_epoch=1）
        with get_db_connection() as conn:
            self._insert(conn, tenant_id, task_id, int(task["control_epoch"]), block_epoch=1, reason="probe")
            conn.commit()
        # 处理器运行前 binding 发生更新的阻断（epoch=2）→ 旧请求 stale
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_fake_guard_bindings SET automation_blocked=TRUE, "
                "automation_block_reason='newer_block', automation_block_epoch=2 "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, binding["conversation_binding_id"]),
            )
            conn.commit()
        stats = control_requests_mod.run_control_request_tick()
        assert stats["stale"] == 1
        assert _task_row(tenant_id, task_id)["status"] == "active"  # 未被旧请求迁移

    def test_processor_retry_then_failed_with_audit(self, tenant_id, device_row, verified_binding, monkeypatch):
        """暂时失败退避重试；达上限 → failed + 脱敏审计（binding 阻断不受影响）。"""
        from src.db.database import get_db_connection
        from src.session_tasks import decisions as dec

        published = publish_task_helper(tenant_id, verified_binding)
        task_id = published["task_id"]
        task = _task_row(tenant_id, task_id)
        with get_db_connection() as conn:
            self._insert(conn, tenant_id, task_id, int(task["control_epoch"]), reason="probe")
            conn.commit()
        # 注入迁移失败（本轮）→ retry
        def _boom(*a, **kw):
            raise RuntimeError("injected migration failure")

        monkeypatch.setattr(dec, "_apply_task_transition", _boom)
        stats = control_requests_mod.run_control_request_tick()
        assert stats["retried"] == 1
        row = _control_requests_of(tenant_id)[0]
        assert row["status"] == "pending" and int(row["retry_count"]) == 1
        assert row["next_retry_at"] is not None
        # 重试到达上限 → failed + 审计
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_control_requests SET retry_count=%s, next_retry_at=NULL "
                "WHERE tenant_id=%s AND task_id=%s",
                (control_requests_mod.MAX_RETRIES - 1, tenant_id, task_id),
            )
            conn.commit()
        stats = control_requests_mod.run_control_request_tick()
        assert stats["failed"] == 1
        row = _control_requests_of(tenant_id)[0]
        assert row["status"] == "failed"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM desktop_automation_audit_events WHERE tenant_id=%s AND kind='control_request_failed'",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 1


# ---------------------------------------------------------------------------
# permits 结构化拒绝（fake control_action）+ 微信 rollback 显式断言
# ---------------------------------------------------------------------------


def _materialize_running(tenant_id, device_row, claimed, decision_id):
    from src.local_tools import repository
    from src.local_tools.security import generate_claim_token, sha256_hex

    prepared = decisions_mod.prepare_send(
        tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
    )
    assert prepared["invocation_id"]
    token = generate_claim_token()
    token_hash = sha256_hex(token)
    claimed_inv = decisions_mod.claim_session_invocation(
        tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
        uuid.UUID(prepared["invocation_id"]), token_hash, 120, None,
    )
    assert claimed_inv["invocation"] is not None
    repository.mark_started(prepared["invocation_id"], tenant_id, token_hash)
    args = repository.get_invocation(prepared["invocation_id"], tenant_id)["arguments_json"]
    return prepared["invocation_id"], args, token_hash


def _authorize_permit(tenant_id, device_row, invocation_id, args, token_hash):
    from src.local_tools.permits import write_authorize

    return write_authorize(
        tenant_id=tenant_id, device_id=str(device_row["id"]),
        invocation_id=invocation_id, claim_token_hash=token_hash,
        request_id=args["request_id"], target_version=args.get("target_version"),
        payload_hash=args.get("payload_hash"),
    )


class TestPermitDenialSemantics:
    def test_control_action_denial_commits_side_effects_before_403(self, da_tenant, device_row, fake_scenario):
        """control_action 拒绝：先提交（阻断+epoch+1、控制请求、审计）再返回拒绝。"""
        from src.local_tools.permits import PermitError, write_authorize

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]), daily_cap=2)
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        # Phase A eligible（计数 0→1）→ 物化 → claim/started
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        # prepare 之后把账本顶到日上限：write-authorize 复判（adapter 锁 binding 重算）拒绝
        _update_binding(tenant_id, binding["conversation_binding_id"], rate_trigger_count=2)
        with pytest.raises(PermitError) as exc:
            write_authorize(
                tenant_id=tenant_id, device_id=str(device_row["id"]),
                invocation_id=invocation_id, claim_token_hash=token_hash,
                request_id=args["request_id"], target_version=args.get("target_version"),
                payload_hash=args.get("payload_hash"),
            )
        assert exc.value.code == "ADAPTER_DENIED" and exc.value.http_status == 403
        # 副作用已提交（另一连接可见）
        rows = _control_requests_of(tenant_id)
        assert len(rows) == 1 and rows[0]["source_type"] == "permit_denied"
        # CR 非阻断 e：持久化的是受控 control_action（human_required），非自由文本
        assert rows[0]["reason"] == "human_required"
        binding_row = _binding_row(tenant_id, binding["conversation_binding_id"])
        assert binding_row["automation_blocked"] is True
        assert int(binding_row["automation_block_epoch"]) == 1
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM desktop_automation_audit_events WHERE tenant_id=%s AND kind='permit_denied_control'",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 1
            cursor.execute(
                "SELECT COUNT(*) AS n FROM local_tool_operation_permits WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 0  # 不签发

    def test_weixin_denied_rolls_back_without_control_request(self, da_tenant, device_row, verified_binding, monkeypatch):
        """显式断言（放行条件 5）：微信普通 denied 仍 rollback 且不产生控制请求。"""
        from src.db.database import get_db_connection
        from src.local_tools.permits import PermitError, write_authorize
        import src.weixin_conversation.config as wx_config

        tenant_id = da_tenant
        published, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "您好"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision["decision_id"])
        # 场景门控关闭 → weixin adapter 返回 allowed=False（control_action=None；
        # adapter 内部为函数级 `from .config import scenario_enabled`，打补丁须落 config 模块）
        monkeypatch.setattr(wx_config, "scenario_enabled", lambda tenant: False)
        with pytest.raises(PermitError) as exc:
            write_authorize(
                tenant_id=tenant_id, device_id=str(device_row["id"]),
                invocation_id=invocation_id, claim_token_hash=token_hash,
                request_id=args["request_id"], target_version=args.get("target_version"),
                payload_hash=args.get("payload_hash"),
            )
        assert exc.value.code == "ADAPTER_DENIED" and exc.value.http_status == 403
        # 回滚：无 permit、无控制请求行（零残留）
        assert _control_requests_of(tenant_id) == []
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM local_tool_operation_permits WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 0

    def test_weixin_prepare_send_result_and_lockface_equivalence(self, da_tenant, device_row, verified_binding):
        """显式断言（放行条件 5）：微信 prepare-send 结果和锁面等价——
        binding_guard=None、prepared/superseded 判别与基线一致、零控制请求、
        零场景门禁写操作。"""
        from src.db.database import get_db_connection
        from src.session_tasks.scenario_descriptor import require_descriptor

        descriptor = require_descriptor("weixin.conversation.v1")
        assert descriptor.binding_guard is None  # 微信不注册 guard（锁面不扩大）
        assert descriptor.send_eligibility_gate is None

        tenant_id = da_tenant
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "您好"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        prepared = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision["decision_id"])
        )
        assert set(prepared.keys()) == {"status", "invocation_id", "decision_id", "run_id"}
        assert prepared["status"] == "prepared"
        assert prepared["invocation_id"]
        # 幂等重入结果等价
        again = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision["decision_id"])
        )
        assert again["invocation_id"] == prepared["invocation_id"] and again.get("idempotent") is True
        assert _control_requests_of(tenant_id) == []

    def test_pending_control_request_blocks_write_authorize(self, da_tenant, device_row, verified_binding):
        from src.db.database import get_db_connection
        from src.local_tools.permits import PermitError, write_authorize

        tenant_id = da_tenant
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "您好"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision["decision_id"])
        task = _task_row(tenant_id, claimed["task_id"])
        with get_db_connection() as conn:
            control_requests_mod.insert_control_request(
                conn.cursor(), tenant_id, claimed["task_id"],
                expected_control_epoch=int(task["control_epoch"]), expected_block_epoch=0,
                reason="probe", source_type="permit_denied", source_ref="probe",
            )
            conn.commit()
        with pytest.raises(PermitError) as exc:
            write_authorize(
                tenant_id=tenant_id, device_id=str(device_row["id"]),
                invocation_id=invocation_id, claim_token_hash=token_hash,
                request_id=args["request_id"], target_version=args.get("target_version"),
                payload_hash=args.get("payload_hash"),
            )
        assert exc.value.code == "CONTROL_PENDING" and exc.value.http_status == 409


# ---------------------------------------------------------------------------
# operation-result SAVEPOINT 结算（fake 场景）
# ---------------------------------------------------------------------------


class TestSettlementSavepoint:
    def _full_chain(self, tenant_id, device_row, binding):
        claimed = _publish_and_claim_fake(tenant_id, device_row, binding)
        decision_id = _ready_reply_fake(tenant_id, device_row, claimed, binding)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        from src.local_tools.permits import write_authorize

        permit = write_authorize(
            tenant_id=tenant_id, device_id=str(device_row["id"]),
            invocation_id=invocation_id, claim_token_hash=token_hash,
            request_id=args["request_id"], target_version=args.get("target_version"),
            payload_hash=args.get("payload_hash"),
        )
        return claimed, decision_id, invocation_id, args, token_hash, permit

    def test_settle_success_records_slot(self, da_tenant, device_row, fake_scenario):
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = self._full_chain(
            tenant_id, device_row, binding
        )
        result = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="submitted",
            evidence_ref=f"fake-submission:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert result["acked"] is True and result["state"] == "succeeded"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM session_task_fake_guard_rate_slots WHERE tenant_id=%s AND delivery_id="
                "(SELECT delivery_id FROM desktop_automation_attempts WHERE tenant_id=%s AND invocation_id=%s)",
                (tenant_id, tenant_id, invocation_id),
            )
            assert cursor.fetchone()["status"] == "settled"

    def test_settle_failure_acks_receipt_and_escalates(self, da_tenant, device_row, fake_scenario):
        """结算失败：回执照常 ACK + 原始回执持久化 + 审计 + binding 阻断 +
        rate_settlement 控制请求；SAVEPOINT 内的账本写被回滚。"""
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = self._full_chain(
            tenant_id, device_row, binding
        )
        FakeGuardAdapter.settle_should_raise = True
        result = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="submitted",
            evidence_ref=f"fake-submission:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        # 正常 ACK（回执接纳性与结算成败正交）
        assert result["acked"] is True and result["state"] == "succeeded"
        # 账本写被 SAVEPOINT 回滚：slot 无行
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM session_task_fake_guard_rate_slots WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 0
        # 异常升级：控制请求 + binding 阻断 + 审计
        rows = _control_requests_of(tenant_id)
        assert len(rows) == 1
        assert rows[0]["source_type"] == "rate_settlement" and rows[0]["reason"] == "rate_ledger_anomaly"
        binding_row = _binding_row(tenant_id, binding["conversation_binding_id"])
        assert binding_row["automation_blocked"] is True
        assert int(binding_row["automation_block_epoch"]) == 1
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM desktop_automation_audit_events WHERE tenant_id=%s AND kind='rate_settlement_failed'",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 1

    def test_binding_location_from_business_ref_not_client_params(self, da_tenant, device_row, fake_scenario):
        """binding 定位：只从受信 business_ref.task_ref 链路定位；伪造参数不影响。"""
        from src.local_tools import operation_result

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        claimed, decision_id, invocation_id, args, token_hash, permit = self._full_chain(
            tenant_id, device_row, binding
        )
        FakeGuardAdapter.settle_calls.clear()
        operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="submitted",
            evidence_ref=f"fake-submission:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert len(FakeGuardAdapter.settle_calls) == 1
        facts = FakeGuardAdapter.settle_calls[0]
        # 结算事实中的 task_id 来自受信 business_ref 链路
        assert str(facts["task_id"]) == str(claimed["task_id"])
        assert str(facts["delivery_id"])


# ---------------------------------------------------------------------------
# envelope（§4.3）
# ---------------------------------------------------------------------------


class TestEnvelopeDispatch:
    def test_create_dispatches_by_scenario_and_persists_key(self, da_tenant, device_row, fake_scenario):
        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        task_id = _publish_fake_task(tenant_id, device_row, binding)
        assert _task_row(tenant_id, task_id)["scenario_key"] == FAKE_KEY

    def test_create_weixin_invalid_spec_keeps_field_paths(self, da_tenant, device_row, verified_binding):
        """微信错误路径保真：非法 spec → SpecValidationError，loc 加 spec 前缀
        （HTTP field_errors 与 B1.0 特征第 8 项同路径：spec.goal / spec.xxx）。"""
        from src.session_tasks.models import SpecValidationError, TaskDraftCreatePayload

        spec = build_spec()
        spec.pop("goal")
        payload = TaskDraftCreatePayload.model_validate({
            "device_id": str(device_row["id"]),
            "account_binding_id": verified_binding["account_binding_id"],
            "conversation_binding_id": verified_binding["conversation_binding_id"],
            "spec": spec,
        })
        with pytest.raises(SpecValidationError) as exc:
            service.create_draft(da_tenant, "user-1", payload)
        locs = [tuple(e["loc"]) for e in exc.value.errors()]
        assert ("spec", "goal") in locs

    def test_create_unregistered_scenario_rejected_400(self, da_tenant, device_row, verified_binding):
        from src.session_tasks.models import TaskDraftCreatePayload

        payload = TaskDraftCreatePayload.model_validate({
            "scenario_key": "no.such.scenario",
            "device_id": str(device_row["id"]),
            "account_binding_id": verified_binding["account_binding_id"],
            "conversation_binding_id": verified_binding["conversation_binding_id"],
            "spec": build_spec(),
        })
        with pytest.raises(SessionTaskError) as exc:
            service.create_draft(da_tenant, "user-1", payload)
        assert exc.value.status_code == 400

    def test_patch_cannot_switch_scenario(self, da_tenant, device_row, fake_scenario):
        from src.session_tasks.models import TaskDraftCreatePayload

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        _grant_fake_capability(tenant_id, device_row["id"])
        created = service.create_draft(tenant_id, "user-1", TaskDraftCreatePayload.model_validate({
            "scenario_key": FAKE_KEY,
            "device_id": str(device_row["id"]),
            "account_binding_id": binding["account_binding_id"],
            "conversation_binding_id": binding["conversation_binding_id"],
            "spec": build_spec(),
        }))
        created_version = created["version"]
        with pytest.raises(SessionTaskError) as exc:
            service.update_draft(
                tenant_id, "user-1", uuid.UUID(created["task_id"]), created_version, build_spec(),
                request_scenario_key="weixin.conversation.v1",
            )
        assert exc.value.status_code == 409
        # 同场景 PATCH 正常
        updated = service.update_draft(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), created_version, build_spec(),
            request_scenario_key=FAKE_KEY,
        )
        assert updated["status"] == "draft"

    def test_patch_weixin_without_scenario_key_unchanged(self, da_tenant, device_row, verified_binding):
        from src.session_tasks.models import TaskDraftCreatePayload

        created = service.create_draft(
            da_tenant, "user-1",
            TaskDraftCreatePayload.model_validate({
                "device_id": str(device_row["id"]),
                "account_binding_id": verified_binding["account_binding_id"],
                "conversation_binding_id": verified_binding["conversation_binding_id"],
                "spec": build_spec(),
            }),
        )
        updated = service.update_draft(
            da_tenant, "user-1", uuid.UUID(created["task_id"]), created["version"], build_spec()
        )
        assert updated["version"] == created["version"] + 1


# ---------------------------------------------------------------------------
# 九处 #1：场景能力拼接（claim 按候选任务场景）
# ---------------------------------------------------------------------------


class TestScenarioCapabilityStitching:
    def test_claim_rejected_when_scenario_send_capability_missing(self, da_tenant, device_row, fake_scenario):
        """设备缺 fake 场景发送能力 + 设备上有 fake 任务 → 409（按任务场景拼接）。"""
        from src.db.database import get_db_connection

        from src.session_tasks.constants import ERR_CAPABILITY_MISSING

        tenant_id = da_tenant
        with _conn_holder() as conn:
            binding = insert_fake_binding(conn, tenant_id, str(device_row["id"]))
        _publish_fake_task(tenant_id, device_row, binding)  # 授予能力并发布
        caps = ["session_task_v1", "session_observer_v1"]  # 随后移除 fake_send_v1
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE local_tool_devices SET capabilities_json=%s WHERE tenant_id=%s AND id=%s",
                (json.dumps({"providers": ["weixin"], "capabilities": caps}), tenant_id, device_row["id"]),
            )
            conn.commit()
        with pytest.raises(SessionTaskError) as exc:
            service.claim_task(_device_dict(tenant_id, device_row), "rt-cap")
        assert exc.value.code == ERR_CAPABILITY_MISSING
        assert "fake_send_v1" in str(exc.value)
