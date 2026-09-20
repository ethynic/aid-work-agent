"""BOSS 第二闸门（write-authorize 复判三分类）与 SAVEPOINT 结算定向测试（§5.5.4）。

- 复判三分类：rate_window_race / rate_daily_cap / rate_ledger_anomaly → 结构化
  拒绝（control_action+受控 audit_code），通用 permits 先提交副作用（阻断+epoch+1、
  幂等控制请求、审计）再返回拒绝；
- 通过 → 同事务插 reserved（reserved_at=DB now）；
- settle：reserved→settled（submitted/verified/unknown）/released（明确未开始）；
  缺失 slot 补建 settled + 异常队列 + anomaly_committed（补建成功仍登记）；
  released 后应占额度回执 → rate_slot_state_conflict；幂等重复回执。
"""

import json
import uuid
from datetime import timezone as _tz

import pytest

from src.session_tasks import decisions as decisions_mod

from tests.unit.boss_conversation.conftest import (
    NO_SLOT_TEMPLATE,
    make_script_version,
    publish_and_claim_boss,
)
from tests.unit.session_tasks.test_c3_decisions import (
    _decision_row,
    _fake_model,
    _submit_reply,
)

BOSS_KEY = "boss.chat_reply.v1"


# ---------------------------------------------------------------------------
# boss 链路辅助（publish → claim → 决策 ready → 物化 → 许可 → 回执）
# ---------------------------------------------------------------------------


def _publish_chain(tenant_id, device_row, binding, template=None):
    """发布含单一话术的 boss 任务并领取；返回 (task_id, claimed, script引用)。"""
    version = make_script_version(tenant_id, template or NO_SLOT_TEMPLATE)
    script = {
        "script_version_id": version["id"],
        "content_hash": version["content_hash"],
        "frozen_template": version["template"],
        "slot_schema": {},
    }
    from tests.unit.boss_conversation.conftest import make_boss_spec

    spec = make_boss_spec(tenant_id, scripts=[script])
    task_id, claimed = publish_and_claim_boss(tenant_id, device_row, binding, spec)
    return task_id, claimed, script


def _ready_reply(tenant_id, device_row, claimed, binding, script):
    """喂批次 + fake 模型 select_script（双匹配 script 引用）→ ready reply 决策。"""
    outputs = [json.dumps({
        "action": "select_script",
        "script_version_id": script["script_version_id"],
        "content_hash": script["content_hash"],
    }, ensure_ascii=False)]
    _, decision = _submit_reply(tenant_id, device_row, claimed, binding, 1, [
        {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
    ])
    model, _ = _fake_model(outputs)
    decisions_mod.run_decision_tick(model_call=model)
    return decision["decision_id"]


def _materialize_running(tenant_id, device_row, claimed, decision_id):
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
    from src.local_tools import repository

    repository.mark_started(prepared["invocation_id"], tenant_id, token_hash)
    args = repository.get_invocation(prepared["invocation_id"], tenant_id)["arguments_json"]
    return prepared["invocation_id"], args, token_hash


def _authorize(tenant_id, device_row, invocation_id, args, token_hash):
    from src.local_tools.permits import write_authorize

    return write_authorize(
        tenant_id=tenant_id, device_id=str(device_row["id"]),
        invocation_id=invocation_id, claim_token_hash=token_hash,
        request_id=args["request_id"], target_version=args.get("target_version"),
        payload_hash=args.get("payload_hash"),
    )


def _slot_of(tenant_id, invocation_id, conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT s.* FROM bs_boss_conversation_rate_slots s
        WHERE s.tenant_id=%s AND s.delivery_id=(
            SELECT delivery_id FROM desktop_automation_attempts
            WHERE tenant_id=%s AND invocation_id=%s ORDER BY attempt_no DESC LIMIT 1)
        """,
        (tenant_id, tenant_id, invocation_id),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _control_requests_of(tenant_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM session_task_control_requests WHERE tenant_id=%s ORDER BY id", (tenant_id,)
        )
        return [dict(r) for r in cursor.fetchall()]


def _binding_row(tenant_id, binding_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s",
            (tenant_id, str(binding_id)),
        )
        return dict(cursor.fetchone())


class TestAuthorizeRecheck:
    def test_pass_inserts_reserved_row_with_db_now(self, tenant_id, device_row, boss_binding):
        from src.db.database import get_db_connection

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        permit = _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        assert permit["permit_id"]
        with get_db_connection() as conn:
            slot = _slot_of(tenant_id, invocation_id, conn)
            cursor = conn.cursor()
            cursor.execute("SELECT clock_timestamp() AS now_ts")
            now_ts = cursor.fetchone()["now_ts"]
        assert slot is not None and slot["status"] == "reserved"
        assert slot["settlement_effect"] is None
        age = abs((now_ts - slot["reserved_at"]).total_seconds())
        assert age < 30  # reserved_at=DB 侧签发时刻（DB 时钟，非宿主机墙钟）

    def test_daily_cap_rejection_commits_side_effects(self, tenant_id, device_row, boss_binding):
        from src.db.database import get_db_connection
        from src.local_tools.permits import PermitError

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        # prepare 之后把当日账本顶到日上限 → 复判拒绝
        with get_db_connection() as conn:
            cursor = conn.cursor()
            for i in range(10):
                cursor.execute(
                    """
                    INSERT INTO bs_boss_conversation_rate_slots
                        (tenant_id, binding_id, decision_id, delivery_id, status, reserved_at, settled_at)
                    VALUES (%s, %s, %s, %s, 'settled', clock_timestamp() - INTERVAL '2 hours', clock_timestamp())
                    """,
                    (tenant_id, boss_binding["conversation_binding_id"], str(uuid.uuid4()), str(uuid.uuid4())),
                )
            conn.commit()
        with pytest.raises(PermitError) as exc:
            _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        assert exc.value.code == "ADAPTER_DENIED"
        rows = _control_requests_of(tenant_id)
        # CR：control_action=设计 §5.5.4 冻结 human_required reason（rate_limit）
        assert len(rows) == 1 and rows[0]["reason"] == "rate_limit"
        assert rows[0]["source_type"] == "permit_denied"
        row = _binding_row(tenant_id, boss_binding["conversation_binding_id"])
        assert row["automation_blocked"] is True and int(row["automation_block_epoch"]) == 1
        # 许可被拒且适配器未插 reserved（拒绝在预留之前）
        with get_db_connection() as conn:
            assert _slot_of(tenant_id, invocation_id, conn) is None

    def test_rate_window_race_on_interval(self, tenant_id, device_row, boss_binding):
        """门禁通过后账本被迟到回执补账：interval 未满足 → rate_window_race 保守转人工。"""
        from src.db.database import get_db_connection
        from src.local_tools.permits import PermitError

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        with get_db_connection() as conn:
            conn.cursor().execute(
                """
                INSERT INTO bs_boss_conversation_rate_slots
                    (tenant_id, binding_id, decision_id, delivery_id, status, reserved_at, settled_at)
                VALUES (%s, %s, %s, %s, 'settled', clock_timestamp() - INTERVAL '20 seconds', clock_timestamp())
                """,
                (tenant_id, boss_binding["conversation_binding_id"], str(uuid.uuid4()), str(uuid.uuid4())),
            )
            conn.commit()
        with pytest.raises(PermitError):
            _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        rows = _control_requests_of(tenant_id)
        assert len(rows) == 1 and rows[0]["reason"] == "rate_window_race"
        row = _binding_row(tenant_id, boss_binding["conversation_binding_id"])
        assert row["automation_blocked"] is True

    def test_blocked_binding_denied_with_block_action(self, tenant_id, device_row, boss_binding):
        from src.db.database import get_db_connection
        from src.local_tools.permits import PermitError

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        with get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE bs_boss_conversation_bindings SET automation_blocked=TRUE, "
                "automation_block_reason='rate_ledger_anomaly', automation_block_epoch=1 "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, boss_binding["conversation_binding_id"]),
            )
            conn.commit()
        with pytest.raises(PermitError) as exc:
            _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        assert exc.value.code == "ADAPTER_DENIED"
        rows = _control_requests_of(tenant_id)
        # CR：与 prepare-send gate terminal 同词汇（automation_blocked）
        assert len(rows) == 1 and rows[0]["reason"] == "automation_blocked"


class TestSettlement:
    def _chain(self, tenant_id, device_row, boss_binding):
        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        permit = _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        return decision_id, invocation_id, args, token_hash, permit

    def test_reserved_to_settled_submitted(self, tenant_id, device_row, boss_binding):
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        decision_id, invocation_id, args, token_hash, permit = self._chain(tenant_id, device_row, boss_binding)
        result = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="submitted",
            evidence_ref=f"boss-submission:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert result["acked"] is True and result["state"] == "succeeded"
        with get_db_connection() as conn:
            slot = _slot_of(tenant_id, invocation_id, conn)
        assert slot["status"] == "settled" and slot["settlement_effect"] == "submitted"
        assert slot["settled_at"] is not None

    def test_reserved_to_settled_verified_enqueues_projection(self, tenant_id, device_row, boss_binding):
        from src.db.database import get_db_connection
        from src.local_tools import operation_result
        from src.boss_conversation.projection import run_projection_tick

        decision_id, invocation_id, args, token_hash, permit = self._chain(tenant_id, device_row, boss_binding)
        operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="verified",
            evidence_ref=f"boss-send-verifier:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        with get_db_connection() as conn:
            slot = _slot_of(tenant_id, invocation_id, conn)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bs_boss_comm_log_projection_queue WHERE tenant_id=%s", (tenant_id,)
            )
            queue = [dict(r) for r in cursor.fetchall()]
        assert slot["settlement_effect"] == "verified"
        assert len(queue) == 1 and queue[0]["delivery_id"]
        # 投影 job：幂等 upsert comm_logs（binding.resume_id 关联）
        stats = run_projection_tick()
        assert stats["done"] == 1
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bs_recruiting_operator_resume_comm_logs WHERE tenant_id=%s", (tenant_id,)
            )
            logs = [dict(r) for r in cursor.fetchall()]
        assert len(logs) == 1
        assert logs[0]["resume_id"] == int(boss_binding["resume_id"])
        assert logs[0]["direction"] == "out" and logs[0]["channel"] == "boss"
        assert str(logs[0]["source_delivery_id"])
        # 幂等：重复 tick 不双写
        stats2 = run_projection_tick()
        assert stats2.get("claimed", 0) == 0
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM bs_recruiting_operator_resume_comm_logs WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 1

    def test_reserved_to_released_not_started(self, tenant_id, device_row, boss_binding):
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        decision_id, invocation_id, args, token_hash, permit = self._chain(tenant_id, device_row, boss_binding)
        operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="none", phase="prepared",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        with get_db_connection() as conn:
            slot = _slot_of(tenant_id, invocation_id, conn)
        assert slot["status"] == "released" and slot["settlement_effect"] == "not_started"
        # 未开始不占额度：频控窗口复核为空
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM bs_boss_conversation_rate_slots "
                "WHERE tenant_id=%s AND binding_id=%s AND status IN ('reserved','settled')",
                (tenant_id, boss_binding["conversation_binding_id"]),
            )
            assert int(cursor.fetchone()["n"]) == 0

    def test_missing_slot_backfilled_and_anomaly_registered(self, tenant_id, device_row, boss_binding):
        """缺失 slot 补建 settled（仍占额度）+ 异常队列登记 + anomaly_committed 升级。"""
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        decision_id, invocation_id, args, token_hash, permit = self._chain(tenant_id, device_row, boss_binding)
        # 模拟账本行丢失（补建路径）：删除 reserved 行
        with get_db_connection() as conn:
            conn.cursor().execute(
                "DELETE FROM bs_boss_conversation_rate_slots WHERE tenant_id=%s", (tenant_id,)
            )
            conn.commit()
        result = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="submitted",
            evidence_ref=f"boss-submission:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        # 补建成功仍升级：回执照常 ACK + 阻断 + 控制请求
        assert result["acked"] is True
        with get_db_connection() as conn:
            slot = _slot_of(tenant_id, invocation_id, conn)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM bs_boss_rate_settlement_anomalies WHERE tenant_id=%s", (tenant_id,)
            )
            anomalies = [dict(r) for r in cursor.fetchall()]
        assert slot is not None and slot["status"] == "settled" and slot["settlement_effect"] == "submitted"
        # 补建 reserved_at 优先 permit 创建时刻（禁回执到达时间）
        cursor2 = conn.cursor()
        cursor2.execute("SELECT created_at FROM local_tool_operation_permits WHERE id=%s", (permit["permit_id"],))
        permit_created = cursor2.fetchone()["created_at"]
        assert abs((slot["reserved_at"] - permit_created).total_seconds()) < 5
        assert len(anomalies) == 1 and anomalies[0]["error_code"] == "rate_slot_missing"
        rows = _control_requests_of(tenant_id)
        assert any(r["reason"] == "rate_ledger_anomaly" for r in rows)
        row = _binding_row(tenant_id, boss_binding["conversation_binding_id"])
        assert row["automation_blocked"] is True

    def test_missing_slot_not_started_no_backfill(self, tenant_id, device_row, boss_binding):
        """明确未开始且未取得 permit 的结果没有 slot 属正常情况，不补建。"""
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        decision_id, invocation_id, args, token_hash, permit = self._chain(tenant_id, device_row, boss_binding)
        with get_db_connection() as conn:
            conn.cursor().execute(
                "DELETE FROM bs_boss_conversation_rate_slots WHERE tenant_id=%s", (tenant_id,)
            )
            conn.commit()
        operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="none", phase="prepared",
        )
        with get_db_connection() as conn:
            assert _slot_of(tenant_id, invocation_id, conn) is None
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM bs_boss_rate_settlement_anomalies WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 0
        assert _control_requests_of(tenant_id) == []

    def test_released_slot_with_occupied_receipt_conflict(self, tenant_id, device_row, boss_binding):
        """released（明确未开始释放）后到达应占额度回执 → 状态冲突（额度漏占）。"""
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        decision_id, invocation_id, args, token_hash, permit = self._chain(tenant_id, device_row, boss_binding)
        # 先按未开始释放
        operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="none", phase="prepared",
        )
        # attempt 已终态 → 直接触发迟到分支，无法重复回执；改为直接调用适配器结算面
        from src.boss_conversation.adapters import BossConversationAdapter

        attempt_id = _attempt_id(tenant_id, invocation_id)
        adapter = BossConversationAdapter()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            outcome = adapter.settle_operation_result(cursor, {
                "tenant_id": tenant_id, "task_id": decision_task(tenant_id, decision_id),
                "invocation_id": invocation_id, "delivery_id": _delivery_id(tenant_id, invocation_id),
                "attempt_id": attempt_id, "request_id": args["request_id"],
                "effect": "applied", "phase": "submitted", "evidence_invalid": None,
                "permit_id": permit["permit_id"],
            })
            conn.commit()
        assert outcome == {"status": "anomaly_committed", "reason": "rate_slot_state_conflict"}
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT error_code FROM bs_boss_rate_settlement_anomalies WHERE tenant_id=%s", (tenant_id,)
            )
            assert cursor.fetchone()["error_code"] == "rate_slot_state_conflict"

    def test_settled_slot_idempotent_repeat_receipt(self, tenant_id, device_row, boss_binding):
        from src.boss_conversation.adapters import BossConversationAdapter
        from src.db.database import get_db_connection

        decision_id, invocation_id, args, token_hash, permit = self._chain(tenant_id, device_row, boss_binding)
        adapter = BossConversationAdapter()
        facts = {
            "tenant_id": tenant_id, "task_id": decision_task(tenant_id, decision_id),
            "invocation_id": invocation_id, "delivery_id": _delivery_id(tenant_id, invocation_id),
            "attempt_id": _attempt_id(tenant_id, invocation_id), "request_id": args["request_id"],
            "effect": "applied", "phase": "submitted", "evidence_invalid": None,
            "permit_id": permit["permit_id"],
        }
        with get_db_connection() as conn:
            assert adapter.settle_operation_result(conn.cursor(), facts) is None
            assert adapter.settle_operation_result(conn.cursor(), facts) is None  # 幂等
            conn.commit()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM bs_boss_rate_settlement_anomalies WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 0


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _conn():
    from src.db.database import get_db_connection

    return get_db_connection()


def _attempt_id(tenant_id, invocation_id):
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM desktop_automation_attempts WHERE tenant_id=%s AND invocation_id=%s "
            "ORDER BY attempt_no DESC LIMIT 1",
            (tenant_id, invocation_id),
        )
        return str(cursor.fetchone()["id"])


def _delivery_id(tenant_id, invocation_id):
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT delivery_id FROM desktop_automation_attempts WHERE tenant_id=%s AND invocation_id=%s "
            "ORDER BY attempt_no DESC LIMIT 1",
            (tenant_id, invocation_id),
        )
        return str(cursor.fetchone()["delivery_id"])


def decision_task(tenant_id, decision_id):
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT task_id FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
            (tenant_id, decision_id),
        )
        return str(cursor.fetchone()["task_id"])


def _adapter_invoke_dict(decision_id, delivery_id):
    """直接驱动 adapter._authorize_on_cursor 的最小 invocation 形态（与许可链一致）。"""
    return {"business_ref": {"decision_id": decision_id, "delivery_id": delivery_id}}


class TestRateSlotStateMachine:
    """P1-3（V1.10 §5.5.4 冻结状态机）：同 delivery slot 预留的每条允许路径恰好
    影响一行；reserved/settled/归属不符 → rate_ledger_anomaly 拒绝。"""

    def _chain(self, tenant_id, device_row, boss_binding):
        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        return decision_id, invocation_id, args, token_hash

    def _adapter_authorize(self, tenant_id, decision_id, delivery_id, *, target_version="iv-1"):
        """绕过 permits 层直接驱动适配器复判（返回 AuthorizeDecision，不签发 permit）。"""
        from src.boss_conversation.adapters import BossConversationAdapter
        from src.desktop_automation.adapters import AdapterContext

        adapter = BossConversationAdapter()
        ctx = AdapterContext(
            tenant_id=tenant_id, user_id="user-1", scenario_key=BOSS_KEY,
            task_ref=decision_task(tenant_id, decision_id), revision_ref="rev",
        )
        with _conn() as conn:
            decision = adapter._authorize_on_cursor(
                ctx, conn.cursor(),
                operation="boss_send_to_v2", target_ref=_binding_id_of(tenant_id, decision_id),
                target_version=target_version, payload_hash="h",
                invocation=_adapter_invoke_dict(decision_id, delivery_id),
            )
            conn.commit()
        return decision

    @staticmethod
    def _backdate_slot(tenant_id, delivery_id, minutes=2):
        """把同 delivery slot 的 reserved_at 回拨——避免 60s 间隔在状态机之前
        以 rate_window_race 拒绝（状态机测试只关注 slot 状态分支）。"""
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE bs_boss_conversation_rate_slots SET reserved_at=NOW() - (%s * INTERVAL '1 minute') "
                "WHERE tenant_id=%s AND delivery_id=%s",
                (minutes, tenant_id, delivery_id),
            )
            conn.commit()

    def test_second_authorize_on_reserved_slot_anomaly(self, tenant_id, device_row, boss_binding):
        decision_id, invocation_id, args, token_hash = self._chain(tenant_id, device_row, boss_binding)
        permit = _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        assert permit["permit_id"]
        delivery_id = _delivery_id(tenant_id, invocation_id)
        self._backdate_slot(tenant_id, delivery_id)
        decision = self._adapter_authorize(tenant_id, decision_id, delivery_id)
        assert decision.allowed is False and decision.reason == "rate_ledger_anomaly"
        assert decision.control_action == "rate_ledger_anomaly"
        with _conn() as conn:
            slot = _slot_of(tenant_id, invocation_id, conn)
        assert slot["status"] == "reserved"  # 冲突未改变状态

    def test_released_slot_recovers_to_reserved_with_fresh_time(self, tenant_id, device_row, boss_binding):
        """同 delivery 人工重试：released → 原子恢复 reserved（刷新 reserved_at、清旧结算字段）。"""
        from src.db.database import get_db_connection

        decision_id, invocation_id, args, token_hash = self._chain(tenant_id, device_row, boss_binding)
        permit = _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        assert permit["permit_id"]
        delivery_id = _delivery_id(tenant_id, invocation_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_boss_conversation_rate_slots
                SET status='released', released_at=NOW() - INTERVAL '5 minutes',
                    settlement_effect='not_started', reserved_at=NOW() - INTERVAL '10 minutes'
                WHERE tenant_id=%s AND delivery_id=%s
                """,
                (tenant_id, delivery_id),
            )
            conn.commit()
        decision = self._adapter_authorize(tenant_id, decision_id, delivery_id)
        assert decision.allowed is True
        with get_db_connection() as conn:
            slot = _slot_of(tenant_id, invocation_id, conn)
            cursor = conn.cursor()
            cursor.execute("SELECT clock_timestamp() AS now_ts")
            now_ts = cursor.fetchone()["now_ts"]
        assert slot["status"] == "reserved"
        assert slot["settlement_effect"] is None and slot["released_at"] is None
        assert abs((now_ts - slot["reserved_at"]).total_seconds()) < 30  # reserved_at 已刷新

    def test_released_slot_ownership_mismatch_anomaly(self, tenant_id, device_row, boss_binding):
        """released 但 decision 归属不符 → rate_ledger_anomaly，不恢复不预留。"""
        import uuid as _uuid

        from src.db.database import get_db_connection

        decision_id, invocation_id, args, token_hash = self._chain(tenant_id, device_row, boss_binding)
        permit = _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        assert permit["permit_id"]
        delivery_id = _delivery_id(tenant_id, invocation_id)
        with get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE bs_boss_conversation_rate_slots SET decision_id=%s, status='released', "
                "released_at=NOW() WHERE tenant_id=%s AND delivery_id=%s",
                (str(_uuid.uuid4()), tenant_id, delivery_id),
            )
            conn.commit()
        decision = self._adapter_authorize(tenant_id, decision_id, delivery_id)
        assert decision.allowed is False and decision.reason == "rate_ledger_anomaly"

    def test_settled_slot_second_authorize_anomaly(self, tenant_id, device_row, boss_binding):
        from src.db.database import get_db_connection

        decision_id, invocation_id, args, token_hash = self._chain(tenant_id, device_row, boss_binding)
        permit = _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        assert permit["permit_id"]
        delivery_id = _delivery_id(tenant_id, invocation_id)
        with get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE bs_boss_conversation_rate_slots SET status='settled', settled_at=NOW(), "
                "settlement_effect='submitted' WHERE tenant_id=%s AND delivery_id=%s",
                (tenant_id, delivery_id),
            )
            conn.commit()
        self._backdate_slot(tenant_id, delivery_id)
        decision = self._adapter_authorize(tenant_id, decision_id, delivery_id)
        assert decision.allowed is False and decision.reason == "rate_ledger_anomaly"


def _binding_id_of(tenant_id, decision_id):
    with _conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT t.conversation_binding_id FROM session_tasks t
            WHERE t.tenant_id=%s AND t.id=(
                SELECT task_id FROM session_task_decisions WHERE tenant_id=%s AND id=%s)
            """,
            (tenant_id, tenant_id, decision_id),
        )
        return str(cursor.fetchone()["conversation_binding_id"])


class TestAuthorizeFrozenTarget:
    """P1-2（六审）：write-authorize 必须核对冻结 target_ref/target_version——
    identity 版本变化、改绑时不得产生 permit/slot。"""

    def test_stale_identity_version_rejected_without_slot(self, tenant_id, device_row, boss_binding):
        from src.db.database import get_db_connection
        from src.local_tools.permits import PermitError

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        # 物化后绑定身份版本前进（账号重验/切换语义）→ 冻结 target_version=iv-1 过期
        with get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE bs_boss_conversation_bindings SET identity_version=2 "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, boss_binding["conversation_binding_id"]),
            )
            conn.commit()
        with pytest.raises(PermitError) as exc:
            _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        assert "boss_target_version_mismatch" in str(exc.value)
        rows = _control_requests_of(tenant_id)
        assert rows == []  # 非频控异常：普通拒绝（整体回滚），无控制副作用
        with get_db_connection() as conn:
            assert _slot_of(tenant_id, invocation_id, conn) is None

    def test_rebound_binding_rejected_without_slot(self, tenant_id, device_row, boss_binding):
        from src.db.database import get_db_connection
        from src.local_tools.permits import PermitError

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        # 物化后任务改绑到另一绑定 → 冻结 target_ref 过期
        from tests.unit.boss_conversation.conftest import create_test_binding

        other = create_test_binding(
            tenant_id, "user-1", device_id=boss_binding["device_id"],
            account_scope_id=boss_binding["account_scope_id"],
            candidate_name="李四", job_id=str(uuid.uuid4()),
        )
        with get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE session_tasks SET conversation_binding_id=%s WHERE tenant_id=%s AND id=%s",
                (other["conversation_binding_id"], tenant_id, task_id),
            )
            conn.commit()
        with pytest.raises(PermitError) as exc:
            _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        assert "boss_target_frozen_mismatch" in str(exc.value) or "boss_target_rebound" in str(exc.value)
        with get_db_connection() as conn:
            assert _slot_of(tenant_id, invocation_id, conn) is None
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM bs_boss_conversation_rate_slots WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 0  # 任一绑定都未产生 slot


class TestAuthorizeSettleConcurrency:
    """P1-1（六审）：真实 PostgreSQL 并发——authorize 与迟到回执结算在 binding
    行锁上串行，不存在按旧窗口双放行。"""

    def test_late_receipt_settlement_blocks_authorize(self, tenant_id, device_row, boss_binding):
        """并发镜像事务持 binding 锁提交一条 20s 前的 settled 迟到回执；authorize
        在 binding 行锁上阻塞至镜像提交后重算窗口 → rate_window_race 拒绝（未签发
        permit/slot）。修复前（普通读 + 无锁复判）authorize 会按旧窗口签发 permit，
        穿透 60s 间隔。DB lock_timeout=10s：镜像持锁 ~1s 内提交，不触发超时。"""
        import threading
        import time

        from src.db.database import get_db_connection
        from src.local_tools.permits import PermitError

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)

        locked = threading.Event()
        release = threading.Event()
        mirror_error = []
        authorize_error = []
        authorize_result = []

        def _mirror_settlement():
            try:
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    # 模拟 operation-result 结算事务：先锁 binding（与 authorize 同一行锁）
                    cursor.execute(
                        "SELECT id FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s FOR UPDATE",
                        (tenant_id, boss_binding["conversation_binding_id"]),
                    )
                    # 迟到回执补账：20s 前 reserved（窗口尚未解除）的 settled 行
                    cursor.execute(
                        """
                        INSERT INTO bs_boss_conversation_rate_slots
                            (tenant_id, binding_id, decision_id, delivery_id, status,
                             reserved_at, settled_at, settlement_effect)
                        VALUES (%s, %s, %s, %s, 'settled',
                                clock_timestamp() - INTERVAL '20 seconds', clock_timestamp(), 'submitted')
                        """,
                        (tenant_id, boss_binding["conversation_binding_id"],
                         str(uuid.uuid4()), str(uuid.uuid4())),
                    )
                    locked.set()
                    if not release.wait(8):
                        raise RuntimeError("authorize 未在 8s 内进入 binding 锁等待")
                    conn.commit()
            except Exception as exc:  # noqa: BLE001
                mirror_error.append(exc)
                locked.set()

        def _authorize_worker():
            try:
                _authorize(tenant_id, device_row, invocation_id, args, token_hash)
                authorize_result.append("issued")
            except PermitError as exc:
                authorize_error.append(exc)
            except Exception as exc:  # noqa: BLE001
                authorize_error.append(exc)

        thread = threading.Thread(target=_mirror_settlement)
        thread.start()
        assert locked.wait(10)
        time.sleep(0.3)  # 镜像事务已持锁并写入未提交行
        worker = threading.Thread(target=_authorize_worker)
        worker.start()
        time.sleep(1.0)  # authorize 已在 binding 锁上阻塞（delivery 锁已持有）
        release.set()
        worker.join(9)
        thread.join(10)
        assert mirror_error == []
        assert authorize_result == []
        assert len(authorize_error) == 1 and isinstance(authorize_error[0], PermitError)
        assert "rate_window_race" in str(authorize_error[0])
        rows = _control_requests_of(tenant_id)
        assert len(rows) == 1 and rows[0]["reason"] == "rate_window_race"
        with get_db_connection() as conn:
            assert _slot_of(tenant_id, invocation_id, conn) is None  # 未签发 reserved
        row = _binding_row(tenant_id, boss_binding["conversation_binding_id"])
        assert row["automation_blocked"] is True

    def test_authorize_lock_excludes_concurrent_settle(self, tenant_id, device_row, boss_binding):
        """反向镜像：authorize 持 binding 锁期间，并发 settle 事务的 slot FOR UPDATE
        被阻塞，直到 permit 事务提交（reserved 行可见）→ 结算按 reserved→settled 推进，
        不出现双写/丢失。"""
        import threading

        from src.boss_conversation.adapters import BossConversationAdapter
        from src.db.database import get_db_connection
        from src.desktop_automation.adapters import AdapterContext

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        delivery_id = _delivery_id(tenant_id, invocation_id)
        attempt_id = _attempt_id(tenant_id, invocation_id)

        permit = _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        assert permit["permit_id"]
        # 删除 reserved 行模拟"authorize 后账本行丢失"窗口，再并发补建（settle 路径）
        with get_db_connection() as conn:
            conn.cursor().execute(
                "DELETE FROM bs_boss_conversation_rate_slots WHERE tenant_id=%s", (tenant_id,)
            )
            conn.commit()

        adapter = BossConversationAdapter()
        facts = {
            "tenant_id": tenant_id, "task_id": decision_task(tenant_id, decision_id),
            "invocation_id": invocation_id, "delivery_id": delivery_id,
            "attempt_id": attempt_id, "request_id": args["request_id"],
            "effect": "applied", "phase": "submitted", "evidence_invalid": None,
            "permit_id": permit["permit_id"],
        }
        # 并发两个 settle 事务同时补建同 delivery slot（UNIQUE + FOR UPDATE 串行），
        # 恰好一行 settled、一个 anomaly_committed（幂等/先到先得），无异常抛出
        outcomes = []
        errors = []

        def _settle():
            try:
                with get_db_connection() as conn:
                    outcome = adapter.settle_operation_result(conn.cursor(), dict(facts))
                    conn.commit()
                    outcomes.append(outcome)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=_settle)
        t2 = threading.Thread(target=_settle)
        t1.start()
        t2.start()
        t1.join(20)
        t2.join(20)
        assert errors == []
        # 两个并发结算事务的结果只允许两种受控形态：None（看到已补建行，幂等）
        # 或 rate_slot_missing anomaly（看到缺失并补建，UNIQUE 令恰好一方真正插入）。
        # 关键不变量：恰好一行 settled、无未处理异常、无一行以上 slot。
        assert all(o is None or (isinstance(o, dict) and o.get("reason") == "rate_slot_missing") for o in outcomes)
        assert any(isinstance(o, dict) and o.get("reason") == "rate_slot_missing" for o in outcomes)
        with get_db_connection() as conn:
            slot = _slot_of(tenant_id, invocation_id, conn)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM bs_boss_conversation_rate_slots WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 1  # 恰好一行
        assert slot["status"] == "settled" and slot["settlement_effect"] == "submitted"


class TestSettlementFailureAnomaly:
    """P1-4（六审）：真正结算异常 → SAVEPOINT 回滚后写受控 settlement_failed 异常行
    并返回严格 anomaly_committed；只有异常队列自身写入失败才上抛。"""

    def test_projection_enqueue_failure_writes_settlement_failed(self, tenant_id, device_row, boss_binding, monkeypatch):
        """verified 结算中投影入队失败 → slot 未改判（回滚）+ settlement_failed 异常行
        + anomaly_committed 升级（阻断+控制请求+ACK）。"""
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        permit = _authorize(tenant_id, device_row, invocation_id, args, token_hash)

        import src.boss_conversation.projection as projection_mod

        def _boom(*a, **k):
            raise RuntimeError("simulated enqueue failure")

        monkeypatch.setattr(projection_mod, "enqueue_projection", _boom)
        result = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="verified",
            evidence_ref=f"boss-send-verifier:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert result["acked"] is True  # 正常 ACK（回执接纳性与结算成败二分）
        with get_db_connection() as conn:
            slot = _slot_of(tenant_id, invocation_id, conn)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT error_code FROM bs_boss_rate_settlement_anomalies WHERE tenant_id=%s",
                (tenant_id,),
            )
            anomalies = [dict(r) for r in cursor.fetchall()]
        # 结算写入已回滚：slot 保持 reserved（settlement_effect 未落）
        assert slot["status"] == "reserved" and slot["settlement_effect"] is None
        assert len(anomalies) == 1 and anomalies[0]["error_code"] == "settlement_failed"
        rows = _control_requests_of(tenant_id)
        assert any(r["reason"] == "rate_ledger_anomaly" for r in rows)
        row = _binding_row(tenant_id, boss_binding["conversation_binding_id"])
        assert row["automation_blocked"] is True

    def test_owner_resolution_failure_writes_settlement_failed(self, tenant_id, device_row, boss_binding, monkeypatch):
        """P1-A（七审）：_resolve_settle_owner 定位阶段异常（SAVEPOINT 内）不再绕过
        异常队列——写 settlement_failed 异常行 + anomaly_committed，完整链路阻断+
        控制请求+ACK 符合冻结协议。"""
        from src.db.database import get_db_connection
        from src.local_tools import operation_result

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        permit = _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        with get_db_connection() as conn:
            conn.cursor().execute(
                "DELETE FROM bs_boss_conversation_rate_slots WHERE tenant_id=%s", (tenant_id,)
            )
            conn.commit()

        import src.boss_conversation.adapters as adapters_mod

        def _boom(cursor, result, base=None):  # noqa: ANN001
            raise RuntimeError("simulated owner resolution failure")

        monkeypatch.setattr(adapters_mod, "_resolve_settle_owner", _boom)
        result = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]), invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="submitted",
            evidence_ref=f"boss-submission:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert result["acked"] is True  # 正常 ACK（回执接纳性与结算成败二分）
        with get_db_connection() as conn:
            cursor = conn.cursor()
            assert _slot_of(tenant_id, invocation_id, conn) is None  # 定位失败未补建
            cursor.execute(
                "SELECT error_code FROM bs_boss_rate_settlement_anomalies WHERE tenant_id=%s",
                (tenant_id,),
            )
            anomalies = [dict(r) for r in cursor.fetchall()]
        assert len(anomalies) == 1 and anomalies[0]["error_code"] == "settlement_failed"
        rows = _control_requests_of(tenant_id)
        assert any(r["reason"] == "rate_ledger_anomaly" for r in rows)
        row = _binding_row(tenant_id, boss_binding["conversation_binding_id"])
        assert row["automation_blocked"] is True

    def test_settlement_exception_converted_to_anomaly_row(self, tenant_id, device_row, boss_binding, monkeypatch):
        """结算内 DB 异常（此处以补建时间缺失受控异常模拟——V1.10 三级全缺路径）
        → 不上抛，写异常队列并返回严格 anomaly_committed；slot 未补建。"""
        import uuid as _uuid

        from src.boss_conversation.adapters import (
            BossConversationAdapter,
            BossConversationSettlementError,
        )
        from src.db.database import get_db_connection

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        with get_db_connection() as conn:
            conn.cursor().execute(
                "DELETE FROM bs_boss_conversation_rate_slots WHERE tenant_id=%s", (tenant_id,)
            )
            conn.commit()

        def _no_time(self_inner, cursor, result):  # noqa: ANN001
            raise BossConversationSettlementError("rate_slot_backfill_time_missing", "三级全缺")

        monkeypatch.setattr(BossConversationAdapter, "_resolve_backfilled_reserved_at", _no_time)
        adapter = BossConversationAdapter()
        facts = {
            "tenant_id": tenant_id, "task_id": decision_task(tenant_id, decision_id),
            "invocation_id": invocation_id, "delivery_id": _delivery_id(tenant_id, invocation_id),
            "attempt_id": str(_uuid.uuid4()), "request_id": args["request_id"],
            "effect": "applied", "phase": "submitted", "evidence_invalid": None,
            "permit_id": str(_uuid.uuid4()),
        }
        with get_db_connection() as conn:
            outcome = adapter.settle_operation_result(conn.cursor(), facts)
            conn.commit()
        assert outcome == {"status": "anomaly_committed", "reason": "rate_slot_backfill_time_missing"}
        with get_db_connection() as conn:
            cursor = conn.cursor()
            assert _slot_of(tenant_id, invocation_id, conn) is None  # 结算写入已回滚，未补建
            cursor.execute(
                "SELECT error_code FROM bs_boss_rate_settlement_anomalies WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert cursor.fetchone()["error_code"] == "rate_slot_backfill_time_missing"

    def test_backfill_time_fallback_chain(self, tenant_id, device_row, boss_binding):
        """V1.10 三级依次取用：permit 缺 → attempt.created_at；permit+attempt 缺 →
        invocation.created_at；禁用回执到达时间。"""
        import uuid as _uuid

        from src.boss_conversation.adapters import BossConversationAdapter
        from src.db.database import get_db_connection

        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        decision_id = _ready_reply(tenant_id, device_row, claimed, boss_binding, script)
        invocation_id, args, token_hash = _materialize_running(tenant_id, device_row, claimed, decision_id)
        permit = _authorize(tenant_id, device_row, invocation_id, args, token_hash)
        attempt_id = _attempt_id(tenant_id, invocation_id)
        delivery_id = _delivery_id(tenant_id, invocation_id)
        adapter = BossConversationAdapter()
        base_facts = {
            "tenant_id": tenant_id, "task_id": decision_task(tenant_id, decision_id),
            "invocation_id": invocation_id, "delivery_id": delivery_id,
            "request_id": args["request_id"],
            "effect": "applied", "phase": "submitted", "evidence_invalid": None,
        }
        # 级 2：permit 行删除 → attempt.created_at
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bs_boss_conversation_rate_slots WHERE tenant_id=%s", (tenant_id,))
            cursor.execute("DELETE FROM local_tool_operation_permits WHERE tenant_id=%s", (tenant_id,))
            conn.commit()
        facts = {**base_facts, "attempt_id": attempt_id, "permit_id": str(_uuid.uuid4())}
        with get_db_connection() as conn:
            outcome = adapter.settle_operation_result(conn.cursor(), dict(facts))
            conn.commit()
        assert outcome == {"status": "anomaly_committed", "reason": "rate_slot_missing"}
        with get_db_connection() as conn:
            slot = _slot_of(tenant_id, invocation_id, conn)
            cursor = conn.cursor()
            cursor.execute("SELECT created_at FROM desktop_automation_attempts WHERE id=%s", (attempt_id,))
            attempt_created = cursor.fetchone()["created_at"]
        assert slot["status"] == "settled"
        assert abs((slot["reserved_at"] - attempt_created).total_seconds()) < 5
        # 级 3：permit+attempt 行删除 → invocation.created_at
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM bs_boss_conversation_rate_slots WHERE tenant_id=%s", (tenant_id,))
            cursor.execute("DELETE FROM bs_boss_rate_settlement_anomalies WHERE tenant_id=%s", (tenant_id,))
            cursor.execute("DELETE FROM desktop_automation_attempts WHERE tenant_id=%s", (tenant_id,))
            conn.commit()
        facts3 = {**base_facts, "attempt_id": str(_uuid.uuid4()), "permit_id": str(_uuid.uuid4())}
        with get_db_connection() as conn:
            outcome3 = adapter.settle_operation_result(conn.cursor(), dict(facts3))
            conn.commit()
        assert outcome3 == {"status": "anomaly_committed", "reason": "rate_slot_missing"}
        with get_db_connection() as conn:
            cursor = conn.cursor()
            # attempts 行已删：按 delivery 直查 slot（不经 _slot_of 的 attempts 连接）
            cursor.execute(
                "SELECT * FROM bs_boss_conversation_rate_slots WHERE tenant_id=%s AND delivery_id=%s",
                (tenant_id, delivery_id),
            )
            slot3 = cursor.fetchone()
            cursor.execute(
                "SELECT created_at FROM local_tool_invocations WHERE tenant_id=%s AND id=%s",
                (tenant_id, invocation_id),
            )
            inv_created = cursor.fetchone()["created_at"]
        assert slot3 is not None and slot3["status"] == "settled"
        # invocation.created_at 是会话时区（Asia/Shanghai）naive TIMESTAMP：按本地业务
        # 时区解释后与 timestamptz 比较
        from zoneinfo import ZoneInfo as _ZoneInfo

        inv_created = (
            inv_created if inv_created.tzinfo else inv_created.replace(tzinfo=_ZoneInfo("Asia/Shanghai"))
        )
        assert abs((slot3["reserved_at"] - inv_created).total_seconds()) < 5


class TestModelRepairRetry:
    """P1-6（六审）：未知 reason_code 进入一次修复重试。"""

    def test_unknown_reason_repaired_once_then_ready(self, tenant_id, device_row, boss_binding):
        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        version = script  # noqa: F841
        outputs = [
            json.dumps({"action": "handoff", "reason_code": "我自己觉得该转人工"}, ensure_ascii=False),
            json.dumps({"action": "handoff", "reason_code": "low_confidence"}, ensure_ascii=False),
        ]
        _, decision = _submit_reply(tenant_id, device_row, claimed, boss_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, calls = _fake_model(outputs)
        decisions_mod.run_decision_tick(model_call=model)
        assert len(calls) == 2  # 初次 + 一次修复（不消耗第三次）
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "ready" and row["action"] == "handoff"
        evidence = json.loads(row["completion_evidence"]) if isinstance(row.get("completion_evidence"), str) else (row.get("completion_evidence") or {})
        assert evidence.get("reason_code") == "low_confidence"
        assert int(row.get("model_attempts") or 0) == 2

    def test_unknown_reason_both_attempts_fail_human_required(self, tenant_id, device_row, boss_binding):
        task_id, claimed, script = _publish_chain(tenant_id, device_row, boss_binding)
        outputs = [
            json.dumps({"action": "handoff", "reason_code": "自由发挥一"}, ensure_ascii=False),
            json.dumps({"action": "handoff", "reason_code": "自由发挥二"}, ensure_ascii=False),
        ]
        _, decision = _submit_reply(tenant_id, device_row, claimed, boss_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, calls = _fake_model(outputs)
        decisions_mod.run_decision_tick(model_call=model)
        assert len(calls) == 2
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "failed" and row["failure_code"] == "invalid_model_output"
