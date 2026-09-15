"""C3 决策 worker 测试矩阵（计划 §6 必测核心：输出校验/修复/预算/完成判定/supersede）。

真实测试 DB + 注入 fake 模型调用（不经 mock service——决策/预算/迁移全走真实表）。
"""

import json
import uuid
from dataclasses import replace

import pytest

from src.session_tasks import decisions as decisions_mod
from src.session_tasks import service
from src.session_tasks.constants import SessionTaskError

from tests.unit.session_tasks.conftest import build_spec, publish_task_helper

# ---------------------------------------------------------------------------
# 公共辅助
# ---------------------------------------------------------------------------

_batch_seq_by_assignment: dict = {}


def _device_dict(tenant_id, device_row):
    return {"id": device_row["id"], "tenant_id": tenant_id, "user_id": "user-1"}


def _publish_and_claim(tenant_id, device_row, binding, spec=None, runtime="rt-1"):
    published = publish_task_helper(tenant_id, binding, spec)
    claimed = service.claim_task(_device_dict(tenant_id, device_row), runtime)
    assert claimed is not None
    return published, claimed


def _batch_event(seq, batch_id, input_version, messages, binding_id):
    return {
        "local_seq": seq,
        "event_id": f"ev-{batch_id}-{seq}",
        "type": "batch",
        "payload": {
            "batch_id": batch_id,
            "input_version": input_version,
            "conversation_binding_id": binding_id,
            "messages": messages,
        },
    }


def _peer_msg(mid, text):
    return {"local_message_id": mid, "sender": "peer", "text": text, "source_evidence_ref": f"ev-{mid}"}


def _feed_batch(tenant_id, device_row, claimed, binding, input_version, messages):
    """喂一个 batch 事件并返回 batch_id（按 assignment 连续 local_seq，前缀 ACK 语义）。"""
    key = claimed["assignment_id"]
    _batch_seq_by_assignment[key] = _batch_seq_by_assignment.get(key, 0) + 1
    batch_id = str(uuid.uuid4())
    service.ingest_events(
        tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
        [_batch_event(_batch_seq_by_assignment[key], batch_id, input_version, messages, binding["conversation_binding_id"])],
    )
    return batch_id


def _submit_reply(tenant_id, device_row, claimed, binding, input_version, messages):
    """喂批次 + 建 reply 决策，返回 (batch_id, decision)。"""
    batch_id = _feed_batch(tenant_id, device_row, claimed, binding, input_version, messages)
    decision = service.create_decision(
        tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
        batch_id, "reply", input_version, claimed["control_epoch"], claimed["spec_revision"],
    )
    return batch_id, decision


def _fake_model(outputs):
    """顺序回放 fake 模型：outputs 为 content 字符串列表。"""
    calls = []

    def call(messages, max_tokens):
        calls.append({"messages": messages, "max_tokens": max_tokens})
        content = outputs[len(calls) - 1] if len(calls) <= len(outputs) else outputs[-1]
        return {"content": content, "usage": {"prompt_tokens": 10, "completion_tokens": 5}, "model": "test-model"}

    return call, calls


def _decision_row(tenant_id, decision_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM session_task_decisions WHERE tenant_id=%s AND id=%s", (tenant_id, decision_id))
        return dict(cursor.fetchone())


def _task_row(tenant_id, task_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM session_tasks WHERE tenant_id=%s AND id=%s", (tenant_id, task_id))
        return dict(cursor.fetchone())


def _cfg():
    from src.session_tasks.config import SessionTasksConfig

    return SessionTasksConfig(enabled=True)


@pytest.fixture(autouse=True)
def c3_env(monkeypatch):
    """注册场景钩子 + 放开决策层门控 + 屏蔽真实计费（预算账走真实表）。"""
    import src.weixin_conversation.config as wx_config
    import src.weixin_conversation.registration as registration

    import src.session_tasks.config as st_config

    monkeypatch.setattr(registration, "get_session_tasks_config", lambda: replace(_cfg(), enabled=True))
    monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: True)
    monkeypatch.setattr(st_config, "tenant_allowed", lambda tenant: True)  # 适配器许可链双开关检查
    assert registration.ensure_registered()

    cfg = _cfg()
    monkeypatch.setattr(decisions_mod, "get_session_tasks_config", lambda: cfg)
    monkeypatch.setattr(decisions_mod, "tenant_allowed", lambda tenant: True)

    billed = []
    monkeypatch.setattr(
        "src.services.session_record.record_background_llm_usage",
        lambda usage, **kw: billed.append({"usage": usage, **kw}),
    )
    monkeypatch.setattr(
        "src.services.billing.calculate_credit_cost_with_breakdown",
        lambda p, c, m, cached_input_tokens=0: (0.01, {}),
    )
    _batch_seq_by_assignment.clear()
    yield {"billed": billed, "cfg": cfg}
    registration.reset_registration()


# ---------------------------------------------------------------------------
# reply 决策：模型输出校验与修复
# ---------------------------------------------------------------------------


class TestReplyDecision:
    def test_valid_reply_freezes_text_and_settles_budget(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "在吗")])
        model, calls = _fake_model([json.dumps({"action": "reply", "reply_text": "您好，请问周五方便吗"}, ensure_ascii=False)])
        stats = decisions_mod.run_decision_tick(model_call=model)
        assert stats["processed"] == 1
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "ready" and row["action"] == "reply"
        assert row["reply_text_id"] is not None and row["reply_text_hash"]
        assert row["model_attempts"] == 1
        from src.db.database import get_db_connection
        from src.session_tasks.texts import load_text

        with get_db_connection() as conn:
            payload = load_text(conn, tenant_id, row["task_id"], row["reply_text_id"], expected_purpose="decision")
        assert payload["text"] == "您好，请问周五方便吗"
        with get_db_connection() as conn:
            settled, reserved = service.task_spend(conn, tenant_id, row["task_id"])
        assert float(settled) == 0.01 and float(reserved) == 0

    def test_invalid_output_repairs_once_then_human_required(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "在吗")])
        model, calls = _fake_model(["不是JSON", '{"action": "hack", "hack": 1}'])
        decisions_mod.run_decision_tick(model_call=model)
        assert len(calls) == 2  # 一次修复
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "failed" and row["failure_code"] == "invalid_model_output"
        assert _task_row(tenant_id, row["task_id"])["status"] == "human_required"

    def test_wait_action_no_send(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "先别回")])
        model, _ = _fake_model([json.dumps({"action": "wait", "wait_for": "peer"})])
        decisions_mod.run_decision_tick(model_call=model)
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "ready" and row["action"] == "wait"
        assert row["reply_text_id"] is None

    def test_reply_text_rejects_url_with_repair(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "发我链接")])
        model, calls = _fake_model([
            json.dumps({"action": "reply", "reply_text": "看这个 www.example.com"}),
            json.dumps({"action": "reply", "reply_text": "稍后我整理给您"}),
        ])
        decisions_mod.run_decision_tick(model_call=model)
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["action"] == "reply"  # 修复后通过

    def test_evidence_id_must_be_peer_message(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        batch_id = _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 1,
            [_peer_msg("m1", "好的"), _peer_msg("m2", "第二条对方消息")],
        )
        decision = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            batch_id, "reply", 1, claimed["control_epoch"], claimed["spec_revision"],
        )
        model, calls = _fake_model([
            json.dumps({"action": "reply", "reply_text": "收到", "evidence_message_ids": ["not-exist"]}),
            json.dumps({"action": "reply", "reply_text": "收到"}),
        ])
        decisions_mod.run_decision_tick(model_call=model)
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["action"] == "reply"


# ---------------------------------------------------------------------------
# opening：不调模型、一次性、有新入站即取消
# ---------------------------------------------------------------------------


class TestOpeningDecision:
    def test_opening_freezes_spec_text_without_model(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec(opening=True))
        decision = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            "opening", "opening", 0, claimed["control_epoch"], claimed["spec_revision"],
        )
        model, calls = _fake_model(["never"])
        decisions_mod.run_decision_tick(model_call=model)
        assert calls == []  # opening 不调模型
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "ready" and row["action"] == "reply" and row["model_attempts"] == 0
        from src.db.database import get_db_connection
        from src.session_tasks.texts import load_text

        with get_db_connection() as conn:
            payload = load_text(conn, tenant_id, row["task_id"], row["reply_text_id"], expected_purpose="decision")
        assert payload["text"] == "您好，想和您确认周五沟通时间"

    def test_opening_creation_rejected_after_inbound(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec(opening=True))
        _feed_batch(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "你好")])
        with pytest.raises(SessionTaskError):
            service.create_decision(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
                "opening", "opening", 0, claimed["control_epoch"], claimed["spec_revision"],
            )

    def test_opening_superseded_when_inbound_accepted(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec(opening=True))
        decision = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            "opening", "opening", 0, claimed["control_epoch"], claimed["spec_revision"],
        )
        decisions_mod.run_decision_tick(model_call=_fake_model(["x"])[0])
        _feed_batch(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "你好")])
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "superseded"


# ---------------------------------------------------------------------------
# 完成判定：rounds / peer_confirmed / judged
# ---------------------------------------------------------------------------


def _mark_link(tenant_id, task_id, decision_id, state="succeeded"):
    """直接落一条 execution_links + delivery 状态（绕过发送链，专测完成判定）。"""
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO desktop_automation_runs (tenant_id, occurrence_id, scenario_key, task_ref, revision_ref,
                user_id, state, fence_token, authorization_epoch, due_at)
            VALUES (%s, %s, 'weixin.conversation.v1', %s, 'spec', 'user-1', 'succeeded', 1, 1, CURRENT_TIMESTAMP)
            RETURNING id
            """,
            (tenant_id, uuid.uuid4(), task_id),
        )
        run_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO desktop_automation_deliveries (tenant_id, run_id, scenario_key, task_ref, revision_ref,
                user_id, position, operation, state)
            VALUES (%s, %s, 'weixin.conversation.v1', %s, 'spec', 'user-1', 1, 'weixin_message_send_v2', %s)
            RETURNING id
            """,
            (tenant_id, run_id, task_id, state),
        )
        delivery_id = cursor.fetchone()["id"]
        cursor.execute(
            """
            INSERT INTO session_task_execution_links (tenant_id, task_id, decision_id, occurrence_id, run_id, delivery_id)
            VALUES (%s, %s, %s, NULL, %s, %s)
            """,
            (tenant_id, task_id, decision_id, run_id, delivery_id),
        )
        conn.commit()


class TestCompletionRules:
    def test_rounds_completed_after_target_verified_sends(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("rounds"))
        for i in range(2):
            _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, i + 1, [_peer_msg(f"m{i}", "好")])
            model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": f"回复{i}"})])
            decisions_mod.run_decision_tick(model_call=model)
            _mark_link(tenant_id, claimed["task_id"], decision["decision_id"])
        decisions_mod.evaluate_tasks()
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "active"
        for _ in range(3):
            _mark_link(tenant_id, claimed["task_id"], str(uuid.uuid4()))
        decisions_mod.evaluate_tasks()
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "completed" and task["completion_reason"] == "rounds_reached"

    def test_rounds_failed_send_not_counted(self, tenant_id, device_row, verified_binding):
        """轮数不足不得误报完成：failed 发送不计数。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("rounds"))
        _mark_link(tenant_id, claimed["task_id"], str(uuid.uuid4()), state="failed")
        decisions_mod.evaluate_tasks()
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "active"

    def test_peer_confirmed_all_fields_and_quotes(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("peer_confirmed"))
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "我愿意周五沟通，时间定在周五14:00")]
        )
        done = {
            "action": "done",
            "peer_confirmation": {
                "values": [
                    {"key": "willing", "value": "yes", "message_ids": ["m1"], "quotes": ["我愿意周五沟通"]}
                ],
                "contradicted": False,
            },
        }
        model, _ = _fake_model([json.dumps(done, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["action"] == "done"
        assert json.loads(row["completion_evidence"])["peer_confirmed"] is True
        decisions_mod.evaluate_tasks()
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "completed" and task["completion_reason"] == "peer_confirmed"

    def test_peer_confirmed_unaccepted_value_waits(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("peer_confirmed"))
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "再想想")])
        done = {
            "action": "done",
            "peer_confirmation": {
                "values": [{"key": "willing", "value": "no", "message_ids": ["m1"], "quotes": ["再想想"]}],
                "contradicted": False,
            },
        }
        model, _ = _fake_model([json.dumps(done, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["action"] == "wait"  # value=no 不在 accepted_values → 继续
        decisions_mod.evaluate_tasks()
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "active"

    def test_peer_confirmed_fake_quote_rejected(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("peer_confirmed"))
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "原文如上")])
        done = {
            "action": "done",
            "peer_confirmation": {
                "values": [{"key": "willing", "value": "yes", "message_ids": ["m1"], "quotes": ["对方没说过的片段"]}],
                "contradicted": False,
            },
        }
        model, _ = _fake_model([json.dumps(done, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["action"] == "wait"

    def test_judged_review_agree_completes(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("judged"))
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "同意沟通，时间周五16:00")]
        )
        done = {
            "action": "done",
            "criterion_results": [
                {"criterion": "对方明确同意沟通", "message_ids": ["m1"], "quotes": ["同意沟通"], "reason": "原文"},
                {"criterion": "对方明确确认具体时间", "message_ids": ["m1"], "quotes": ["周五16:00"], "reason": "原文"},
            ],
        }
        model, _ = _fake_model([json.dumps(done, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        model2, _ = _fake_model([json.dumps({
            "agree": True,
            "criterion_checks": [
                {"criterion": "对方明确同意沟通", "satisfied": True, "message_ids": ["m1"], "note": "对方说《同意沟通》"},
                {"criterion": "对方明确确认具体时间", "satisfied": True, "message_ids": ["m1"], "note": "对方说《周五16:00》"},
            ],
        }, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model2)
        decisions_mod.evaluate_tasks()
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "completed" and task["completion_reason"] == "goal_judged"

    def test_judged_review_disagree_human_required(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("judged"))
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "再说吧")])
        done = {
            "action": "done",
            "criterion_results": [
                {"criterion": "对方明确同意沟通", "message_ids": ["m1"], "quotes": ["再说吧"], "reason": "x"},
                {"criterion": "对方明确确认具体时间", "message_ids": ["m1"], "quotes": ["再说吧"], "reason": "x"},
            ],
        }
        model, _ = _fake_model([json.dumps(done, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        model2, _ = _fake_model([json.dumps({
            "agree": False,
            "criterion_checks": [
                {"criterion": "对方明确同意沟通", "satisfied": False, "note": "无明确同意"},
                {"criterion": "对方明确确认具体时间", "satisfied": False, "note": "无时间"},
            ],
        }, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model2)
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "human_required"

    def test_unknown_send_blocks_task(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _mark_link(tenant_id, claimed["task_id"], str(uuid.uuid4()), state="unknown")
        decisions_mod.evaluate_tasks()
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "blocked" and task["blocked_reason"] == "unknown_send_result"


# ---------------------------------------------------------------------------
# supersede / 人工回复
# ---------------------------------------------------------------------------


class TestSupersedeAndManualIntervention:
    def test_new_batch_supersedes_pending_decision(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "在吗")])
        _feed_batch(tenant_id, device_row, claimed, verified_binding, 2, [_peer_msg("m2", "补充一句")])
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "superseded"

    def test_manual_reply_transfers_human(self, tenant_id, device_row, verified_binding):
        """排队期间人工回复：无法归属的 self 消息 → human_required + 决策作废。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "在吗")])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "自动回复"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        assert _decision_row(tenant_id, decision["decision_id"])["status"] == "ready"
        _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 2,
            [_peer_msg("m2", "继续"), {"local_message_id": "s1", "sender": "self", "text": "人工插入的回复", "source_evidence_ref": "e"}],
        )
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "superseded"
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "human_required"

    def test_own_send_echo_not_manual(self, tenant_id, device_row, verified_binding):
        """自动发送**成功**后的回显（正文一致且未超发送次数）不触发人工介入。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "在吗")])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "自动回复"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        # 实际发送成功（execution_links + delivery succeeded）
        _mark_link(tenant_id, claimed["task_id"], decision["decision_id"], state="succeeded")
        _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 2,
            [_peer_msg("m2", "继续"), {"local_message_id": "s1", "sender": "self", "text": "自动回复", "source_evidence_ref": "e"}],
        )
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "active"
        # 同正文再次由人工发送：超出成功发送次数 → 介入（历史正文不永久豁免）
        _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 3,
            [{"local_message_id": "s2", "sender": "self", "text": "自动回复", "source_evidence_ref": "e"}],
        )
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "human_required"

    def test_unsent_decision_text_is_manual(self, tenant_id, device_row, verified_binding):
        """未发送的决策正文不能作为回显归属：人工输入相同文字 → 介入（防漏报）。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "在吗")])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "自动回复"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        # 决策 ready 但从未发送（无 links / delivery failed / 取消）
        _mark_link(tenant_id, claimed["task_id"], decision["decision_id"], state="failed")
        _feed_batch(
            tenant_id, device_row, claimed, verified_binding, 2,
            [{"local_message_id": "s1", "sender": "self", "text": "自动回复", "source_evidence_ref": "e"}],
        )
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "human_required"


# ---------------------------------------------------------------------------
# 预算与并发槽位
# ---------------------------------------------------------------------------


class TestBudgetAndSlots:
    def test_tenant_balance_zero_blocks_task(self, tenant_id, device_row, verified_binding, monkeypatch):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "在吗")])
        monkeypatch.setattr(decisions_mod, "_tenant_balance_locked", lambda conn, t: 0.0)
        decisions_mod.run_decision_tick(model_call=_fake_model(["{}"])[0])
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "blocked" and task["blocked_reason"] == "INSUFFICIENT_TENANT_CREDIT"
        # 决策回到 pending（模型未调用），不产生模型费用
        assert _decision_row(tenant_id, decision["decision_id"])["status"] == "pending"

    def test_task_budget_exhausted_stops(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        from src.session_tasks.service import reserve_cost, settle_cost

        reserve_cost(tenant_id, uuid.UUID(claimed["task_id"]), "decision", "preset-1", 99.5)
        settle_cost(tenant_id, uuid.UUID(claimed["task_id"]), "decision", "preset-1", 99.5)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "在吗")])
        decisions_mod.run_decision_tick(model_call=_fake_model(["{}"])[0])
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "failed" and row["failure_code"] == "max_cost_units_exhausted"
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "stopped" and task["completion_reason"] == "max_cost_units_exhausted"

    def test_tenant_slot_cap_and_per_task_single_inflight(self, tenant_id, device_row, verified_binding):
        """每任务 ≤1 在飞（新批次会 supersede 旧决策，用双任务验证）；租户槽位 ≤ max。"""
        from src.weixin_conversation.bindings import create_binding

        # 第二个已验证绑定 + 第二个任务（同租户同设备，不同会话）
        account2 = str(uuid.uuid4())
        binding2 = create_binding(tenant_id, "user-1", str(device_row["id"]), account2, "group", label="会话2")
        from datetime import datetime, timedelta, timezone

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
        _, claimed1 = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, claimed2 = _publish_and_claim(tenant_id, device_row, binding2_full, runtime="rt-1")
        _submit_reply(tenant_id, device_row, claimed1, verified_binding, 1, [_peer_msg("m1", "在吗")])
        _submit_reply(tenant_id, device_row, claimed2, binding2_full, 1, [_peer_msg("m2", "在吗")])
        cfg1 = replace(_cfg(), max_decisions_per_tenant=1)
        first = decisions_mod.claim_pending_decisions(cfg1, decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"])
        assert len(first) == 1  # 租户槽位=1
        again = decisions_mod.claim_pending_decisions(cfg1, decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"])
        assert again == []
        # 槽位=2 时可再领另一任务
        decisions_mod.run_decision_tick(model_call=_fake_model([json.dumps({"action": "wait", "wait_for": "peer"})][0]) * 4)
        cfg2 = replace(_cfg(), max_decisions_per_tenant=2)
        rest = decisions_mod.claim_pending_decisions(cfg2, decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"])
        assert len(rest) <= 1  # 仅剩另一任务的 pending

    def test_max_decisions_boundary_rejects_second_decision(self, tenant_id, device_row, verified_binding):
        """额度临界双决策（必测矩阵）：max_decisions=1 时第二个模型调用决策被拒、任务 stopped。"""
        spec = build_spec()
        spec["limits"]["max_decisions"] = 1
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, spec)
        # 第一个决策正常消耗
        _, _ = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "在吗")])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "回复1"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        # 第二个决策（新批次）：额度已满 → stopped，不再调用模型
        _, decision2 = _submit_reply(tenant_id, device_row, claimed, verified_binding, 2, [_peer_msg("m2", "再问")])
        model2, calls2 = _fake_model(["never"])
        decisions_mod.run_decision_tick(model_call=model2)
        assert calls2 == [], "额度耗尽后不得再调模型"
        row = _decision_row(tenant_id, decision2["decision_id"])
        assert row["status"] == "failed" and row["failure_code"] == "max_decisions_exhausted"
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "stopped" and task["completion_reason"] == "max_decisions_exhausted"

    def test_judged_review_ready_then_peer_retracts_not_completed(self, tenant_id, device_row, verified_binding, monkeypatch):
        """P2-D：review ready(done) 后、评估执行前对方反悔（新批次）→ evaluate 的
        current 防线挡住，不得按旧证据 completed。正常时序下 tick 内 finalize 后
        立即评估会当拍完成（那是合法语义）；本用例模拟评估延迟的崩溃窗口。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("judged"))
        _, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "同意沟通，时间周五16:00")]
        )
        done = {
            "action": "done",
            "criterion_results": [
                {"criterion": "对方明确同意沟通", "message_ids": ["m1"], "quotes": ["同意沟通"], "reason": "原文"},
                {"criterion": "对方明确确认具体时间", "message_ids": ["m1"], "quotes": ["周五16:00"], "reason": "原文"},
            ],
        }
        model, _ = _fake_model([json.dumps(done, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        # 模拟评估延迟：tick 内暂停 evaluate，先让 review 落 ready(done)
        monkeypatch.setattr(decisions_mod, "evaluate_tasks", lambda **kw: 0)
        model2, _ = _fake_model([json.dumps({
            "agree": True,
            "criterion_checks": [
                {"criterion": "对方明确同意沟通", "satisfied": True, "message_ids": ["m1"], "note": "对方说《同意沟通》"},
                {"criterion": "对方明确确认具体时间", "satisfied": True, "message_ids": ["m1"], "note": "对方说《周五16:00》"},
            ],
        }, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model2)  # review ready(done)，评估被挂起
        monkeypatch.undo()
        # 对方反悔：v2 批次接纳 → 迟到评估必须被 current 防线挡住
        _feed_batch(tenant_id, device_row, claimed, verified_binding, 2, [_peer_msg("m2", "算了不来了")])
        decisions_mod.evaluate_tasks()
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "active", "对方反悔后不得按旧审核证据 completed"

    def test_judged_review_stale_after_new_message_not_completed(self, tenant_id, device_row, verified_binding):
        """judged 门禁（CR P1-1）：审核期间对方反悔（新批次接纳）→ 不得 completed。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("judged"))
        batch1, decision = _submit_reply(
            tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "同意沟通，时间周五16:00")]
        )
        done = {
            "action": "done",
            "criterion_results": [
                {"criterion": "对方明确同意沟通", "message_ids": ["m1"], "quotes": ["同意沟通"], "reason": "原文"},
                {"criterion": "对方明确确认具体时间", "message_ids": ["m1"], "quotes": ["周五16:00"], "reason": "原文"},
            ],
        }
        model, _ = _fake_model([json.dumps(done, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)  # reply done → review pending
        # 对方反悔：v2 批次接纳 → reply v1 与 review 全部 superseded
        _feed_batch(tenant_id, device_row, claimed, verified_binding, 2, [_peer_msg("m2", "算了不来了")])
        model2, calls2 = _fake_model([json.dumps({"agree": True, "criterion_checks": []})])
        decisions_mod.run_decision_tick(model_call=model2)  # review 已 superseded，不应被领取
        assert calls2 == [], "superseded 审核不得再调模型"
        decisions_mod.evaluate_tasks()
        task = _task_row(tenant_id, claimed["task_id"])
        assert task["status"] == "active", "对方反悔后不得按旧证据 completed"

    def test_stale_lease_reaped_to_human_required(self, tenant_id, device_row, verified_binding):
        from datetime import datetime, timedelta, timezone

        from src.db.database import get_db_connection

        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [_peer_msg("m1", "在吗")])
        decisions_mod.claim_pending_decisions(_cfg(), decisions_mod._now(), limit=5, scenario_keys=["weixin.conversation.v1"])
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_decisions SET lease_expires_at=%s WHERE tenant_id=%s AND id=%s",
                (datetime.now(timezone.utc) - timedelta(seconds=1000), tenant_id, decision["decision_id"]),
            )
            conn.commit()
        decisions_mod._reap_stale_decisions(_cfg(), decisions_mod._now())
        row = _decision_row(tenant_id, decision["decision_id"])
        assert row["status"] == "failed" and row["failure_code"] == "model_timeout_unconfirmed"
        assert _task_row(tenant_id, claimed["task_id"])["status"] == "human_required"
