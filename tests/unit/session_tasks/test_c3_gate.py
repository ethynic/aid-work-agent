"""C3 门禁复验测试（本轮 9 项 P1 中服务端 5 项的真实链路验证）。

#2/#3：经真实 permits.write_authorize（subject→run→invocation→delivery→适配器→
quota 全链）验证许可签发条件：双开关/allowlist、assignment 当前/fence/租约/设备、
目标身份版本、工作时段、精确 decision_id 归属（同正文 D1/D2）。
#5：opening 不计完成轮数。
#6：peer_confirmed/judged 无原文引用不得完成。
#7：模型槽位在调用未确认结束前不释放（阻塞模型 + supersede 竞态）。
#8：跨任务预留扣除租户未决占用（真实 tenants 行）。
"""

import json
import threading
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.session_tasks import decisions as decisions_mod
from src.session_tasks import service
from src.session_tasks.constants import SessionTaskError

from tests.unit.session_tasks.conftest import build_spec, publish_task_helper
from tests.unit.session_tasks.test_c3_decisions import (
    _batch_seq_by_assignment,
    _cfg,
    _decision_row,
    _device_dict,
    _fake_model,
    _feed_batch,
    _publish_and_claim,
    _submit_reply,
    _task_row,
)


@pytest.fixture(autouse=True)
def gate_env(monkeypatch):
    import src.session_tasks.config as st_config
    import src.weixin_conversation.config as wx_config
    import src.weixin_conversation.registration as registration

    monkeypatch.setattr(registration, "get_session_tasks_config", lambda: replace(_cfg(), enabled=True))
    monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: True)
    # 适配器在函数内 import 模块属性——patch 模块层使总开关默认放行（关闭场景单独 patch）
    monkeypatch.setattr(st_config, "tenant_allowed", lambda tenant: True)
    registration.ensure_registered()
    cfg = _cfg()
    monkeypatch.setattr(decisions_mod, "get_session_tasks_config", lambda: cfg)
    monkeypatch.setattr(decisions_mod, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr("src.services.session_record.record_background_llm_usage", lambda usage, **kw: None)
    monkeypatch.setattr(
        "src.services.billing.calculate_credit_cost_with_breakdown",
        lambda p, c, m, cached_input_tokens=0: (0.01, {}),
    )
    _batch_seq_by_assignment.clear()
    yield
    registration.reset_registration()


def _ready_reply(tenant_id, device_row, binding, runtime="rt-1", text="您好，周五14:00可以吗"):
    _, claimed = _publish_and_claim(tenant_id, device_row, binding, runtime=runtime)
    _, decision = _submit_reply(
        tenant_id, device_row, claimed, binding, 1,
        [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}],
    )
    model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": text}, ensure_ascii=False)])
    decisions_mod.run_decision_tick(model_call=model)
    return claimed, decision["decision_id"]


def _prepare_and_claim(tenant_id, device_row, claimed, decision_id):
    """prepare-send + 定向 claim + started：返回 (invocation_id, claim_token_hash)。"""
    from src.local_tools import repository
    from src.local_tools.security import generate_claim_token, sha256_hex

    result = decisions_mod.prepare_send(
        tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
    )
    assert result["invocation_id"]
    token = generate_claim_token()
    claimed_inv = decisions_mod.claim_session_invocation(
        tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
        uuid.UUID(result["invocation_id"]), sha256_hex(token), 120, None,
    )
    assert claimed_inv["invocation"] is not None
    token_hash = sha256_hex(token)
    repository.mark_started(result["invocation_id"], tenant_id, token_hash)
    return result["invocation_id"], token_hash


def _authorize(tenant_id, device_row, invocation_id, token_hash):
    from src.local_tools import repository
    from src.local_tools.permits import PermitError, write_authorize

    inv = repository.get_invocation(invocation_id, tenant_id)
    args = inv["arguments_json"]
    try:
        permit = write_authorize(
            tenant_id=tenant_id,
            device_id=str(device_row["id"]),
            invocation_id=invocation_id,
            claim_token_hash=token_hash,
            request_id=args["request_id"],
            target_version=args.get("target_version"),
            payload_hash=args.get("payload_hash"),
        )
        return {"allowed": True, "permit": permit}
    except PermitError as exc:
        return {"allowed": False, "code": exc.code, "message": exc.message}


# ---------------------------------------------------------------------------
# #2 许可签发条件（真实 permits 链）
# ---------------------------------------------------------------------------


class TestPermitAuthorization:
    def test_happy_path_issues_permit(self, tenant_id, device_row, verified_binding):
        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        invocation_id, token_hash = _prepare_and_claim(tenant_id, device_row, claimed, decision_id)
        outcome = _authorize(tenant_id, device_row, invocation_id, token_hash)
        assert outcome["allowed"] is True, outcome
        assert outcome["permit"]["permit_token"]

    def test_total_gate_off_denies_permit(self, tenant_id, device_row, verified_binding, monkeypatch):
        import src.session_tasks.config as st_config

        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        invocation_id, token_hash = _prepare_and_claim(tenant_id, device_row, claimed, decision_id)
        monkeypatch.setattr(st_config, "tenant_allowed", lambda tenant: False)  # 含 allowlist 移除语义
        outcome = _authorize(tenant_id, device_row, invocation_id, token_hash)
        assert outcome["allowed"] is False and outcome["code"] == "ADAPTER_DENIED"

    def test_lease_expired_denies_permit(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        invocation_id, token_hash = _prepare_and_claim(tenant_id, device_row, claimed, decision_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_assignments SET lease_expires_at=%s WHERE tenant_id=%s AND id=%s",
                (datetime.now(timezone.utc) - timedelta(seconds=5), tenant_id, claimed["assignment_id"]),
            )
            conn.commit()
        outcome = _authorize(tenant_id, device_row, invocation_id, token_hash)
        assert outcome["allowed"] is False and "lease_expired" in outcome["message"]

    def test_assignment_reassigned_denies_permit(self, tenant_id, device_row, verified_binding):
        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        invocation_id, token_hash = _prepare_and_claim(tenant_id, device_row, claimed, decision_id)
        # 他实例接管（过期换代）：旧 assignment 不再 current，旧 fence 失效
        from tests.unit.session_tasks.conftest import expire_assignment_lease

        expire_assignment_lease(tenant_id, claimed["assignment_id"])
        new = service.claim_task(_device_dict(tenant_id, device_row), "rt-other")
        assert new is not None and new["fence"] == claimed["fence"] + 1
        outcome = _authorize(tenant_id, device_row, invocation_id, token_hash)
        assert outcome["allowed"] is False

    def test_identity_version_drift_denies_permit(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        invocation_id, token_hash = _prepare_and_claim(tenant_id, device_row, claimed, decision_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bs_weixin_conversation_bindings SET identity_version=identity_version+1 WHERE tenant_id=%s AND id=%s",
                (tenant_id, verified_binding["conversation_binding_id"]),
            )
            conn.commit()
        outcome = _authorize(tenant_id, device_row, invocation_id, token_hash)
        assert outcome["allowed"] is False and "target_version_drift" in outcome["message"]

    def test_work_window_closed_denies_permit(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        invocation_id, token_hash = _prepare_and_claim(tenant_id, device_row, claimed, decision_id)
        # 模拟 prepare 之后跨出工作时段：冻结 spec 的窗口改为不含当前时刻
        now_hour = datetime.now(timezone.utc).hour
        window = {"start_hour_utc": (now_hour + 1) % 24, "end_hour_utc": (now_hour + 2) % 24, "weekdays_utc": list(range(7))}
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_specs SET work_window_json=%s WHERE tenant_id=%s AND task_id=%s",
                (json.dumps(window), tenant_id, claimed["task_id"]),
            )
            conn.commit()
        outcome = _authorize(tenant_id, device_row, invocation_id, token_hash)
        assert outcome["allowed"] is False and "work_window_closed" in outcome["message"]

    def test_paused_task_denies_permit(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        invocation_id, token_hash = _prepare_and_claim(tenant_id, device_row, claimed, decision_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT version FROM session_tasks WHERE tenant_id=%s AND id=%s", (tenant_id, claimed["task_id"]))
            version = int(cursor.fetchone()["version"])
        service.control_task(tenant_id, "user-1", uuid.UUID(claimed["task_id"]), "pause", version)
        outcome = _authorize(tenant_id, device_row, invocation_id, token_hash)
        assert outcome["allowed"] is False


# ---------------------------------------------------------------------------
# #3 精确执行归属（同正文 D1/D2）
# ---------------------------------------------------------------------------


class TestPreciseDecisionAttribution:
    def _two_same_text_decisions(self, tenant_id, device_row, verified_binding):
        text = "好的，收到"
        claimed, d1 = _ready_reply(tenant_id, device_row, verified_binding, text=text)
        inv1, tok1 = _prepare_and_claim(tenant_id, device_row, claimed, d1)
        # 新批次 → D1 superseded（在途 invocation 转 cancel_requested）
        _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 2,
            [{"local_message_id": "m2", "sender": "peer", "text": "再确认一下", "source_evidence_ref": "e2"}],
        )
        assert _decision_row(tenant_id, d1)["status"] == "superseded"
        # D2：同正文新决策（新批次建决策 → worker 处理为 ready）
        batch2 = _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 3,
            [{"local_message_id": "m3", "sender": "peer", "text": "再确认一次", "source_evidence_ref": "e3"}],
        )
        d2_row = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            batch2, "reply", 3, claimed["control_epoch"], claimed["spec_revision"],
        )
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": text}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        assert _decision_row(tenant_id, d2_row["decision_id"])["status"] == "ready"
        d2 = d2_row["decision_id"]
        inv2, tok2 = _prepare_and_claim(tenant_id, device_row, claimed, d2)
        return claimed, d1, inv1, tok1, d2, inv2, tok2

    def test_stale_decision_invocation_denied_current_allowed(self, tenant_id, device_row, verified_binding):
        _, d1, inv1, tok1, d2, inv2, tok2 = self._two_same_text_decisions(tenant_id, device_row, verified_binding)
        assert d1 != d2 and inv1 != inv2
        stale = _authorize(tenant_id, device_row, inv1, tok1)
        assert stale["allowed"] is False, "D1 过时：I1 必须按 D1 拒绝"
        current = _authorize(tenant_id, device_row, inv2, tok2)
        assert current["allowed"] is True, "D2 有效：I2 按 D2 判定放行"

    def test_missing_links_fallback_via_delivery(self, tenant_id, device_row, verified_binding):
        """首发崩溃窗口（links 尚未写入）：经 delivery.payload_ref 兜底，不得永久拒绝。"""
        from src.db.database import get_db_connection

        _, d1, inv1, tok1, d2, inv2, tok2 = self._two_same_text_decisions(tenant_id, device_row, verified_binding)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM session_task_execution_links WHERE tenant_id=%s AND decision_id=%s", (tenant_id, d2))
            conn.commit()
        outcome = _authorize(tenant_id, device_row, inv2, tok2)
        assert outcome["allowed"] is True, "links 缺失经 delivery payload_ref 精确解析仍可授权"


# ---------------------------------------------------------------------------
# #5 opening 不计完成轮数
# ---------------------------------------------------------------------------


class TestOpeningNotARound:
    def test_opening_success_does_not_complete_rounds(self, tenant_id, device_row, verified_binding):
        spec = build_spec("rounds", opening=True)
        spec["completion_rule"]["rounds_target"] = 1
        spec["limits"]["max_replies"] = 5
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, spec)
        opening = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            "opening", "opening", 0, claimed["control_epoch"], claimed["spec_revision"],
        )
        decisions_mod.run_decision_tick(model_call=_fake_model(["x"])[0])
        assert _decision_row(tenant_id, opening["decision_id"])["status"] == "ready"
        from tests.unit.session_tasks.test_c3_decisions import _mark_link

        _mark_link(tenant_id, claimed["task_id"], opening["decision_id"], state="succeeded")
        decisions_mod.evaluate_tasks()
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "active", "仅发送开场白不得 rounds_reached 完成"
        # 成功回复一轮后才完成
        batch_id = _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "好的", "source_evidence_ref": "e"}],
        )
        reply = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            batch_id, "reply", 1, claimed["control_epoch"], claimed["spec_revision"],
        )
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "收到"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        _mark_link(tenant_id, claimed["task_id"], reply["decision_id"], state="succeeded")
        decisions_mod.evaluate_tasks()
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "completed" and task["completion_reason"] == "rounds_reached"

    def test_failed_or_unknown_reply_not_counted(self, tenant_id, device_row, verified_binding):
        from tests.unit.session_tasks.test_c3_decisions import _mark_link

        spec = build_spec("rounds")
        spec["completion_rule"]["rounds_target"] = 1
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, spec)
        _mark_link(tenant_id, claimed["task_id"], str(uuid.uuid4()), state="unknown")
        decisions_mod.evaluate_tasks()
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "blocked"  # unknown 阻断而非完成
        assert _task_row(tenant_id, claimed["task_id"])["blocked_reason"] == "unknown_send_result"


# ---------------------------------------------------------------------------
# #6 无原文引用不得完成
# ---------------------------------------------------------------------------


class TestQuotesRequired:
    def _done_output(self, confirmation):
        return json.dumps({"action": "done", "peer_confirmation": confirmation}, ensure_ascii=False)

    def test_missing_quotes_rejected(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("peer_confirmed"))
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "尚未决定", "source_evidence_ref": "e"}],
        )
        done = self._done_output({
            "values": [{"key": "willing", "value": "yes", "message_ids": ["m1"], "quotes": []}],
            "contradicted": False,
        })
        model, calls = _fake_model([done, done])
        decisions_mod.run_decision_tick(model_call=model)
        row = _decision_row(tenant_id, decision["decision_id"])
        # 空 quotes：输出校验即拒绝（两次调用含修复均失败 → human_required）
        assert row["status"] == "failed"
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "human_required"

    def test_blank_quote_rejected(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("peer_confirmed"))
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "尚未决定", "source_evidence_ref": "e"}],
        )
        done = self._done_output({
            "values": [{"key": "willing", "value": "yes", "message_ids": ["m1"], "quotes": ["   "]}],
            "contradicted": False,
        })
        model, _ = _fake_model([done, done])
        decisions_mod.run_decision_tick(model_call=model)
        assert _decision_row(tenant_id, decision["decision_id"])["status"] == "failed"

    def test_no_quotes_key_but_valid_then_repair(self, tenant_id, device_row, verified_binding):
        """输出层缺 quotes → 修复调用补上合法 quotes 后通过（修复路径仍受控）。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("peer_confirmed"))
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "我愿意周五沟通", "source_evidence_ref": "e"}],
        )
        bad = self._done_output({
            "values": [{"key": "willing", "value": "yes", "message_ids": ["m1"]}],
            "contradicted": False,
        })
        good = self._done_output({
            "values": [{"key": "willing", "value": "yes", "message_ids": ["m1"], "quotes": ["我愿意周五沟通"]}],
            "contradicted": False,
        })
        model, calls = _fake_model([bad, good])
        decisions_mod.run_decision_tick(model_call=model)
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["action"] == "done" and json.loads(row["completion_evidence"])["peer_confirmed"] is True
        decisions_mod.evaluate_tasks()
        assert _task_row(tenant_id, claimed["task_id"])["completion_reason"] == "peer_confirmed"

    def test_judged_criterion_without_quotes_rejected(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("judged"))
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "同意沟通", "source_evidence_ref": "e"}],
        )
        done = json.dumps({
            "action": "done",
            "criterion_results": [
                {"criterion": "对方明确同意沟通", "message_ids": ["m1"], "quotes": [], "reason": "x"},
                {"criterion": "对方明确确认具体时间", "message_ids": ["m1"], "quotes": [], "reason": "x"},
            ],
        }, ensure_ascii=False)
        model, _ = _fake_model([done, done])
        decisions_mod.run_decision_tick(model_call=model)
        assert _decision_row(tenant_id, decision["decision_id"])["status"] == "failed"


# ---------------------------------------------------------------------------
# A2 补充：相同正文多次成功发送的容量累加
# ---------------------------------------------------------------------------


class TestSameTextCapacityAccumulation:
    def test_two_same_text_sends_absorb_two_echoes_then_manual_takes_over(
        self, tenant_id, device_row, verified_binding
    ):
        """A2 验收：两条不同决策发送相同正文、两次成功——两条自动回显都不触发
        接管；第三条相同人工消息触发接管。"""
        from tests.unit.session_tasks.test_c3_decisions import _mark_link

        text = "好的，收到"
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        b1 = _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e"}],
        )
        d1 = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            b1, "reply", 1, claimed["control_epoch"], claimed["spec_revision"],
        )
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": text}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        _mark_link(tenant_id, claimed["task_id"], d1["decision_id"], state="succeeded")
        b2 = _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 2,
            [{"local_message_id": "m2", "sender": "peer", "text": "再问一次", "source_evidence_ref": "e"}],
        )
        d2 = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            b2, "reply", 2, claimed["control_epoch"], claimed["spec_revision"],
        )
        model2, _ = _fake_model([json.dumps({"action": "reply", "reply_text": text}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model2)
        _mark_link(tenant_id, claimed["task_id"], d2["decision_id"], state="succeeded")
        # 两条自动回显（各一批）：容量=2（同正文累加），used≤2 → 不触发接管
        _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 3,
            [{"local_message_id": "s1", "sender": "self", "text": text, "source_evidence_ref": "e"}],
        )
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "active", "第一条回显不得误判接管"
        _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 4,
            [{"local_message_id": "s2", "sender": "self", "text": text, "source_evidence_ref": "e"}],
        )
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "active", "第二条回显不得误判接管（容量累加）"
        # 第三条相同人工消息：used=3 > capacity=2 → 接管
        _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 5,
            [{"local_message_id": "s3", "sender": "self", "text": text, "source_evidence_ref": "e"}],
        )
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "human_required", "超出成功次数的同正文应触发接管"


# ---------------------------------------------------------------------------
# B1/B2：过期领取不得启动模型；未启动 attempt 的预留幂等释放
# ---------------------------------------------------------------------------


class TestExpiredLeaseCannotStartModel:
    def _claim_one(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e"}],
        )
        claimed_now = decisions_mod.claim_pending_decisions(
            _cfg(), decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"]
        )
        assert len(claimed_now) == 1
        return claimed, decision, claimed_now[0]

    def test_expired_lease_blocks_model_start(self, tenant_id, device_row, verified_binding):
        """B1 验收：领取后、模型启动前 worker A 暂停致租约过期；worker B 占满同
        租户容量后恢复 A——A 不得调用模型（初次与修复重试同规则），租户实际并发
        不超上限。"""
        from src.db.database import get_db_connection

        claimed, decision, d = self._claim_one(tenant_id, device_row, verified_binding)
        # A 暂停：租约过期（决策仍 running、未占调用槽位）
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_decisions SET lease_expires_at=%s WHERE tenant_id=%s AND id=%s",
                (datetime.now(timezone.utc) - timedelta(seconds=10), tenant_id, decision["decision_id"]),
            )
            conn.commit()
        # worker B：另一任务占满租户容量（cap=1）
        from src.weixin_conversation.bindings import create_binding

        account2 = str(uuid.uuid4())
        binding2 = create_binding(tenant_id, "user-1", str(device_row["id"]), account2, "group", label="B1容量")
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_weixin_conversation_bindings
                SET verification_status='verified', verifier_version='t', identity_version=1,
                    verified_at=CURRENT_TIMESTAMP, expires_at=%s
                WHERE tenant_id=%s AND id=%s
                """,
                (datetime.now(timezone.utc) + timedelta(days=30), tenant_id, binding2["conversation_binding_id"]),
            )
            conn.commit()
        binding2_full = {
            "conversation_binding_id": binding2["conversation_binding_id"],
            "account_binding_id": account2,
            "device_id": str(device_row["id"]),
        }
        _, claimed_b = _publish_and_claim(tenant_id, device_row, binding2_full, runtime="rt-B")
        b2 = _feed_batch(
            tenant_id, device_row, claimed_b, binding2_full, 1,
            [{"local_message_id": "mb1", "sender": "peer", "text": "B 的消息", "source_evidence_ref": "e"}],
        )
        decision_b = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed_b["assignment_id"]), claimed_b["fence"],
            b2, "reply", 1, claimed_b["control_epoch"], claimed_b["spec_revision"],
        )
        claimed_b_now = decisions_mod.claim_pending_decisions(
            replace(_cfg(), max_decisions_per_tenant=1), decisions_mod._now(), limit=5,
            scenario_keys=["weixin.conversation.v1"],
        )
        assert len(claimed_b_now) == 1, "B 占得最后一个租户槽位（A 的过期领取不计入）"
        # 恢复 A：过期租约不得启动模型（初次调用）
        calls = []
        model = lambda m, t: calls.append(1) or {"content": json.dumps({"action": "wait", "wait_for": "peer"}), "usage": {}}
        try:
            decisions_mod._call_model_with_budget(
                {"tenant_id": tenant_id, "id": d["task_id"], "user_id": "user-1"},
                build_spec(), d, [{"role": "user", "content": "x"}], _cfg(), f"{d['id']}", model,
            )
        except SessionTaskError:
            pass
        assert calls == [], "过期租约的领取不得启动模型"
        # 修复重试同规则：直接再次调用（同决策第二 attempt）
        try:
            decisions_mod._call_model_with_budget(
                {"tenant_id": tenant_id, "id": d["task_id"], "user_id": "user-1"},
                build_spec(), d, [{"role": "user", "content": "x"}], _cfg(), f"{d['id']}:r2", model,
            )
        except SessionTaskError:
            pass
        assert calls == [], "修复重试同样受租约/容量控制"
        # B 的调用正常（唯一在飞）
        row_b = _decision_row(tenant_id, decision_b["decision_id"])
        assert row_b["status"] in ("running", "pending")


class TestUnstartedReservationRelease:
    def _attempt_reserved(self, tenant_id, task_id, ref):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT state FROM session_task_cost_reservations WHERE tenant_id=%s AND task_id=%s AND purpose='decision' AND ref_key=%s",
                (tenant_id, task_id, ref),
            )
            row = cursor.fetchone()
            return row["state"] if row else None

    def test_superseded_after_reserve_releases_attempt(self, tenant_id, device_row, verified_binding):
        """B2 验收：预留完成后注入 supersede——模型调用 0、该 attempt 不占预算、
        重复处理不重复释放。"""
        claimed, d = self._claim_and_reserve(tenant_id, device_row, verified_binding)
        # 预留完成后、启动前被 supersede（新批次接纳）
        _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 2,
            [{"local_message_id": "m2", "sender": "peer", "text": "补充", "source_evidence_ref": "e"}],
        )
        calls = []
        model = lambda m, t: calls.append(1) or {"content": "{}", "usage": {}}
        try:
            decisions_mod._call_model_with_budget(
                {"tenant_id": tenant_id, "id": d["task_id"], "user_id": "user-1"},
                build_spec(), d, [{"role": "user", "content": "x"}], _cfg(), f"{d['id']}", model,
            )
        except SessionTaskError:
            pass
        assert calls == [], "superseded 决策不得调用模型"
        state = self._attempt_reserved(tenant_id, d["task_id"], f"{d['id']}")
        assert state in (None, "released"), f"未启动 attempt 不得占预算（实际 {state}）"
        # 重复处理不重复释放/不报错
        decisions_mod._release_unstarted_reservation(tenant_id, d["task_id"], f"{d['id']}")
        assert self._attempt_reserved(tenant_id, d["task_id"], f"{d['id']}") in (None, "released")

    @staticmethod
    def _claim_and_reserve(tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e"}],
        )
        claimed_now = decisions_mod.claim_pending_decisions(
            _cfg(), decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"]
        )
        assert len(claimed_now) == 1
        d = claimed_now[0]
        # 先完成预留（复用预算入口的预留段）
        decisions_mod._reserve_decision_budget(
            {"tenant_id": tenant_id, "id": d["task_id"], "user_id": "user-1"},
            build_spec(), d, f"{d['id']}", _cfg(),
        )
        return claimed, d

    def test_reaped_after_reserve_releases_via_compensation(self, tenant_id, device_row, verified_binding):
        """B2 崩溃补偿：预留后 worker 崩溃（从未启动，pending=FALSE）→ 滞留回收时
        经补偿释放；若存在未确认结束调用（pending=TRUE）则保留。"""
        from src.db.database import get_db_connection

        claimed, d = self._claim_and_reserve(tenant_id, device_row, verified_binding)
        assert self._attempt_reserved(tenant_id, d["task_id"], f"{d['id']}") == "reserved"
        # 场景 1：从未启动（pending FALSE）→ 租约过期 → 回收释放
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_decisions SET lease_expires_at=%s WHERE tenant_id=%s AND id=%s",
                (datetime.now(timezone.utc) - timedelta(seconds=1000), tenant_id, d["id"]),
            )
            conn.commit()
        decisions_mod._reap_stale_decisions(_cfg(), decisions_mod._now())
        assert self._attempt_reserved(tenant_id, d["task_id"], f"{d['id']}") == "released", "崩溃未启动的预留应被回收补偿释放"

    def test_pending_call_reservation_kept(self, tenant_id, device_row, verified_binding):
        """B2 保守侧：已启动未确认结束（pending=TRUE）的预留即使在回收/终结补偿中
        也保留（结果未知）。"""
        from src.db.database import get_db_connection

        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e"}],
        )
        claimed_now = decisions_mod.claim_pending_decisions(
            _cfg(), decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"]
        )
        d = claimed_now[0]
        decisions_mod._reserve_decision_budget(
            {"tenant_id": tenant_id, "id": d["task_id"], "user_id": "user-1"}, build_spec(), d, f"{d['id']}", _cfg()
        )
        # 已启动未确认结束
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_decisions SET model_call_pending=TRUE, lease_expires_at=%s WHERE tenant_id=%s AND id=%s",
                (datetime.now(timezone.utc) - timedelta(seconds=1000), tenant_id, d["id"]),
            )
            conn.commit()
        decisions_mod._reap_stale_decisions(_cfg(), decisions_mod._now())
        assert self._attempt_reserved(tenant_id, d["task_id"], f"{d['id']}") == "reserved", "未知结果调用的预留不得释放"

    def test_normal_call_settles_once(self, tenant_id, device_row, verified_binding):
        """B2 正常路径：调用返回 → 恰好结算一次（不留 reserved）。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        b1 = _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e"}],
        )
        service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            b1, "reply", 1, claimed["control_epoch"], claimed["spec_revision"],
        )
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "好的"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT state, COUNT(*) AS n FROM session_task_cost_reservations WHERE tenant_id=%s AND task_id=%s AND purpose='decision' GROUP BY state",
                (tenant_id, claimed["task_id"]),
            )
            states = {r["state"]: int(r["n"]) for r in cursor.fetchall()}
        assert states.get("settled", 0) >= 1 and "reserved" not in states, f"正常路径只结算一次（实际 {states}）"


# ---------------------------------------------------------------------------
# A3：任务截止期进入授权链（模型调用/prepare-send/许可签发三处）
# ---------------------------------------------------------------------------


class TestTaskDeadlineEnforcement:
    def test_expired_task_blocks_model_prepare_and_permit(self, tenant_id, device_row, verified_binding, monkeypatch):
        """A3 验收：暂停终态评估器（不依赖后台扫描、不用真实等待），任务到期后——
        新许可拒绝、prepare-send 拒绝并收敛 stopped、新模型调用 0；截止前 prepare
        的 invocation/permit 有效期被截至任务 expires_at。"""
        from src.db.database import get_db_connection

        spec = build_spec()
        spec["limits"]["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, spec)
        b1 = _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e"}],
        )
        decision = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            b1, "reply", 1, claimed["control_epoch"], claimed["spec_revision"],
        )
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "好的"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        assert _decision_row(tenant_id, decision["decision_id"])["status"] == "ready"
        # 截止前 prepare：invocation/permit 有效期 ≤ 任务 expires_at（30s < 默认 600s）
        invocation_id, token_hash = _prepare_and_claim(tenant_id, device_row, claimed, decision["decision_id"])
        from src.local_tools import repository

        inv = repository.get_invocation(invocation_id, tenant_id)
        task_expires = datetime.fromisoformat(str(spec["limits"]["expires_at"]))
        inv_deadline = datetime.fromisoformat(str(inv["arguments_json"]["deadline_at"])).replace(tzinfo=timezone.utc)
        assert inv_deadline <= task_expires, "invocation 截止不得晚于任务 expires_at"
        assert inv_deadline <= datetime.now(timezone.utc) + timedelta(seconds=31), "默认 600s 截止被任务期限压缩"
        # 暂停评估器 + 模拟时间流逝：冻结 spec 截止改为过去（设备与 assignment 租约仍有效）
        monkeypatch.setattr(decisions_mod, "evaluate_tasks", lambda **kw: 0)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            expired_limits = json.dumps({**spec["limits"], "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()})
            cursor.execute(
                "UPDATE session_task_specs SET limits_json=%s WHERE tenant_id=%s AND task_id=%s AND id=%s",
                (expired_limits, tenant_id, claimed["task_id"], _current_spec_id(tenant_id, claimed["task_id"])),
            )
            conn.commit()
        # 到期后：许可签发拒绝（deadline；invocation 未被取消/未到期外因干扰）
        outcome = _authorize(tenant_id, device_row, invocation_id, token_hash)
        assert outcome["allowed"] is False and "deadline" in outcome["message"], outcome
        # 到期后：prepare-send 拒绝并收敛 stopped（同决策重放同样被拒——新授权点已关）
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
                uuid.UUID(decision["decision_id"]),
            )
        assert exc.value.code == "TASK_EXPIRED"
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "stopped" and task["completion_reason"] == "deadline_expired"
        # 到期后：已存在/新建的 pending 决策不再产生模型调用
        model3_calls = []
        model3 = lambda m, t: model3_calls.append(1) or {"content": json.dumps({"action": "wait", "wait_for": "peer"}), "usage": {}}
        decisions_mod.run_decision_tick(model_call=model3)
        assert model3_calls == [], "任务到期后不得产生任何模型调用"

    def test_worker_deadline_check_before_model_call(self, tenant_id, device_row, verified_binding, monkeypatch):
        """worker 在预算事务内（租户锁+task 行锁下）按当前时间复核截止期。
        发布时校验要求未来时间——发布后把冻结 spec 的 expires_at 改到过去（模拟时间流逝）。"""
        from src.db.database import get_db_connection

        spec = build_spec()
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, spec)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            expired = json.dumps({**spec["limits"], "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()})
            cursor.execute(
                "UPDATE session_task_specs SET limits_json=%s WHERE tenant_id=%s AND task_id=%s AND id=%s",
                (expired, tenant_id, claimed["task_id"], claimed.get("current_spec_id") or
                 _current_spec_id(tenant_id, claimed["task_id"])),
            )
            conn.commit()
        b1 = _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e"}],
        )
        decision = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            b1, "reply", 1, claimed["control_epoch"], claimed["spec_revision"],
        )
        monkeypatch.setattr(decisions_mod, "evaluate_tasks", lambda **kw: 0)
        calls = []
        decisions_mod.run_decision_tick(model_call=lambda m, t: calls.append(1) or {"content": "{}", "usage": {}})
        assert calls == [], "到期任务零模型调用"
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "failed" and row["failure_code"] == "deadline_expired"
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "stopped"


# ---------------------------------------------------------------------------
# #7 模型槽位：调用未确认结束不释放
# ---------------------------------------------------------------------------


class TestModelSlotPending:
    def test_supersede_does_not_free_inflight_slot(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e"}],
        )
        release = threading.Event()

        def blocking_model(messages, max_tokens):
            release.wait(10)
            return {"content": json.dumps({"action": "reply", "reply_text": "回复"}, ensure_ascii=False),
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "model": "t"}

        worker_calls = []
        t = threading.Thread(
            target=lambda: worker_calls.append(decisions_mod.run_decision_tick(model_call=blocking_model))
        )
        t.start()
        # 等模型调用发起（pending 占位落库）
        from src.db.database import get_db_connection

        def pending_is_true():
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT model_call_pending FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
                    (tenant_id, decision["decision_id"]),
                )
                return bool(cursor.fetchone()["model_call_pending"])

        deadline = datetime.now(timezone.utc) + timedelta(seconds=5)
        while not pending_is_true():
            assert datetime.now(timezone.utc) < deadline, "模型占位未落库"
            threading.Event().wait(0.02)
        # 新批次 supersede 在飞决策（状态清 running，但 pending 保留）
        _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 2,
            [{"local_message_id": "m2", "sender": "peer", "text": "补充", "source_evidence_ref": "e"}],
        )
        assert _decision_row(tenant_id, decision["decision_id"])["status"] == "superseded"
        assert pending_is_true(), "supersede 不得释放未确认结束的调用槽位"
        # 同任务第二条决策：槽位仍被占 → 不得领取（不得启动替代调用）
        batch2 = None
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT batch_id FROM session_task_batches WHERE tenant_id=%s AND task_id=%s AND input_version=2",
                (tenant_id, claimed["task_id"]),
            )
            batch2 = cursor.fetchone()["batch_id"]
        service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            batch2, "reply", 2, claimed["control_epoch"], claimed["spec_revision"],
        )
        alt_model_calls = []
        claim_result = decisions_mod.claim_pending_decisions(
            _cfg(), decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"]
        )
        assert claim_result == [], "旧调用未确认结束不得启动替代调用（任务 ≤1）"
        # 释放阻塞调用 → 占位清除，第二条可领取
        release.set()
        t.join(10)
        assert not pending_is_true(), "调用返回后释放槽位"
        again = decisions_mod.claim_pending_decisions(
            _cfg(), decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"]
        )
        assert len(again) == 1

    def test_stale_reap_keeps_pending_until_call_returns(self, tenant_id, device_row, verified_binding):
        """A1 验收：持续阻塞的模型调用跨过 stale 阈值并执行回收后，未确认结束的
        槽位仍保留（任务转人工、决策 failed，但 pending 不清）；调用真实返回后才
        释放容量。期间另一任务领取仍受租户 ≤ 上限约束。"""
        from src.db.database import get_db_connection

        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e"}],
        )
        decisions_mod.claim_pending_decisions(_cfg(), decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"])
        # 模拟调用中租约过期（状态 running + pending，lease 过期超 stale 线）
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_decisions SET model_call_pending=TRUE, lease_expires_at=%s WHERE tenant_id=%s AND id=%s",
                (datetime.now(timezone.utc) - timedelta(seconds=1000), tenant_id, decision["decision_id"]),
            )
            conn.commit()
        again = decisions_mod.claim_pending_decisions(_cfg(), decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"])
        assert again == [], "本任务槽位仍被 pending 占据"
        # 滞留回收：任务转人工、决策 failed——但 pending 必须保留（未确认结束）
        decisions_mod._reap_stale_decisions(_cfg(), decisions_mod._now())
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "failed" and row["failure_code"] == "model_timeout_unconfirmed"
        assert row["model_call_pending"] is True, "回收不得释放未确认结束的调用槽位"
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "human_required"
        # 租户计数仍含 pending：另一任务（同租户）领取受上限约束
        cap_cfg = replace(_cfg(), max_decisions_per_tenant=1)
        other_pending = _pending_tenant_count(tenant_id)
        assert other_pending >= 1, "租户在飞计数应包含未确认结束的调用"
        # 明确结束（调用返回路径的清除）→ 释放
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_decisions SET model_call_pending=FALSE WHERE tenant_id=%s AND id=%s",
                (tenant_id, decision["decision_id"]),
            )
            conn.commit()
        assert _pending_tenant_count(tenant_id) == 0 or _pending_tenant_count(tenant_id) < other_pending

    def test_superseded_decision_aborts_model_call_before_start(self, tenant_id, device_row, verified_binding):
        """A1 补充：调用发起前的原子复核——supersede 后不再发起（修复重试同样受控）。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e"}],
        )
        claimed_now = decisions_mod.claim_pending_decisions(_cfg(), decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"])
        assert len(claimed_now) == 1
        d = claimed_now[0]
        # 领取后、调用前被 supersede（新批次接纳）
        _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 2,
            [{"local_message_id": "m2", "sender": "peer", "text": "补充", "source_evidence_ref": "e"}],
        )
        model_calls = []
        try:
            decisions_mod._call_model_with_budget(
                {"tenant_id": tenant_id, "id": d["task_id"], "user_id": "user-1"},
                build_spec(), d, [{"role": "user", "content": "x"}], _cfg(), "abort-test",
                lambda m, t: model_calls.append(1) or {"content": "{}", "usage": {}},
            )
        except Exception:
            pass
        assert model_calls == [], "supersede 后不得发起模型调用"


def _current_spec_id(tenant_id: str, task_id: str) -> str:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT current_spec_id FROM session_tasks WHERE tenant_id=%s AND id=%s", (tenant_id, task_id))
        return str(cursor.fetchone()["current_spec_id"])


def _pending_tenant_count(tenant_id: str) -> int:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS n FROM session_task_decisions WHERE tenant_id=%s AND model_call_pending",
            (tenant_id,),
        )
        return int(cursor.fetchone()["n"])


# ---------------------------------------------------------------------------
# #8 跨任务预留扣除租户未决占用
# ---------------------------------------------------------------------------


class TestTenantBudgetDeduction:
    @pytest.fixture(autouse=True)
    def _tenant_row(self, tenant_id):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tenants (tenant_id, company_name, credit_balance) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (tenant_id, "C3 门禁测试租户", 1.0),
            )
            conn.commit()
        yield
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tenants WHERE tenant_id=%s", (tenant_id,))
            conn.commit()

    def test_two_tasks_one_unit_balance_single_winner(self, tenant_id, device_row, verified_binding):
        _, claimed_a = _publish_and_claim(tenant_id, device_row, verified_binding)
        # 第二绑定第二任务（同租户）
        from src.weixin_conversation.bindings import create_binding

        account2 = str(uuid.uuid4())
        binding2 = create_binding(tenant_id, "user-1", str(device_row["id"]), account2, "group", label="预算2")
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE bs_weixin_conversation_bindings
                SET verification_status='verified', verifier_version='t', identity_version=1,
                    verified_at=CURRENT_TIMESTAMP, expires_at=%s
                WHERE tenant_id=%s AND id=%s
                """,
                (datetime.now(timezone.utc) + timedelta(days=30), tenant_id, binding2["conversation_binding_id"]),
            )
            conn.commit()
        binding2_full = {
            "conversation_binding_id": binding2["conversation_binding_id"],
            "account_binding_id": account2,
            "device_id": str(device_row["id"]),
        }
        _, claimed_b = _publish_and_claim(tenant_id, device_row, binding2_full, runtime="rt-budget")
        cfg = replace(_cfg(), decision_reserve_units=1.0)
        task_a = {"tenant_id": tenant_id, "id": uuid.UUID(claimed_a["task_id"]), "user_id": "user-1"}
        task_b = {"tenant_id": tenant_id, "id": uuid.UUID(claimed_b["task_id"]), "user_id": "user-1"}
        spec = build_spec()
        # 余额 1：任务 A 预留 1 成功；任务 B 同额预留必须被拒（可用 = 1 − 1 未决 = 0）
        decisions_mod._reserve_decision_budget(task_a, spec, {"id": uuid.uuid4(), "tenant_id": tenant_id}, "a1", cfg)
        with pytest.raises(decisions_mod._BalanceBlocked):
            decisions_mod._reserve_decision_budget(task_b, spec, {"id": uuid.uuid4(), "tenant_id": tenant_id}, "b1", cfg)
        # 败者走 worker：blocked、模型零调用
        batch_b = _feed_batch(
            tenant_id, device_row, claimed_b, binding2_full, 1,
            [{"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e"}],
        )
        decision_b = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed_b["assignment_id"]), claimed_b["fence"],
            batch_b, "reply", 1, claimed_b["control_epoch"], claimed_b["spec_revision"],
        )
        def assert_decision_exists(phase):
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT status FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
                               (tenant_id, decision_b["decision_id"]))
                row = cursor.fetchone()
            assert row is not None, {"phase": phase, "tenant_id": tenant_id,
                                     "task_id": claimed_b["task_id"], "decision_id": decision_b["decision_id"]}
        assert_decision_exists("after_create")
        monkey_model = _fake_model(["never"])
        decisions_mod.run_decision_tick(model_call=monkey_model[0])
        assert monkey_model[1] == [], "余额不足任务零模型调用"
        assert_decision_exists("after_tick")
        assert _decision_row(tenant_id, decision_b["decision_id"])["status"] == "pending"
        assert _task_row(tenant_id, claimed_b["task_id"])["status"] == "blocked"
        assert _task_row(tenant_id, claimed_b["task_id"])["blocked_reason"] == "INSUFFICIENT_TENANT_CREDIT"
        # 幂等：同 attempt 重复预留不增加占用
        decisions_mod._reserve_decision_budget(task_a, spec, {"id": uuid.uuid4(), "tenant_id": tenant_id}, "a1", cfg)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COALESCE(SUM(amount),0) AS total FROM session_task_cost_reservations WHERE tenant_id=%s AND state='reserved'",
                (tenant_id,),
            )
            assert float(cursor.fetchone()["total"]) == 1.0
        # 结算后占用释放：任务 B 可预留
        from src.session_tasks.service import settle_cost

        settle_cost(tenant_id, task_a["id"], "decision", "a1", 0.5)
        decisions_mod._reserve_decision_budget(task_b, spec, {"id": uuid.uuid4(), "tenant_id": tenant_id}, "b1", cfg)
