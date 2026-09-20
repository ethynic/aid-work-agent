"""fake provider 端到端一条龙（B2，计划 §8）：

观察（批次 ingest）→ 决策（fake 模型 fill_slots）→ 确定性渲染（peer+resume 槽位）
→ prepare-send Phase A 门禁（eligible 计数落库）→ Phase B/C 物化 → write-authorize
（复判通过插 reserved）→ 发送回执（submitted/双证据接纳）→ SAVEPOINT 结算
（settled）→ 投影（verified 入队 → 后台 job upsert comm_logs）。

另覆盖：superseded 后旧 decision 不再触发（不重复计数/不重复物化）、幂等重 prepare
（不跳过阻断检查、不重复计数）、effective_count 与 reserved slot 的精确对应。
"""

import json
import uuid

import pytest

from src.session_tasks import decisions as decisions_mod

from tests.unit.boss_conversation.conftest import (
    SLOT_TEMPLATE,
    make_script_version,
    publish_and_claim_boss,
)
from tests.unit.session_tasks.test_c3_decisions import (
    _fake_model,
    _feed_batch,
    _submit_reply,
)


def _e2e_binding_spec(tenant_id):
    """slot 话术版本 + boss spec（peer 槽位 expected_time + resume 槽位 company）。"""
    version = make_script_version(
        tenant_id, SLOT_TEMPLATE,
        slot_schema={
            "expected_time": {"required": True, "description": "候选人方便的时间"},
            "company": {"required": True, "description": "我方公司"},
        },
    )
    script = {
        "script_version_id": version["id"],
        "content_hash": version["content_hash"],
        "frozen_template": version["template"],
        "slot_schema": {
            "expected_time": {"required": True, "description": "候选人方便的时间"},
            "company": {"required": True, "description": "我方公司"},
        },
    }
    spec = {
        "goal": "与候选人确认周五下午沟通意向",
        "completion_rule": {"mode": "rounds", "rounds_target": 2},
        "reply_policy": {
            "style": "简洁礼貌",
            "allowed_facts": ["可选时段为周五14:00或16:00"],
            "forbidden_commitments": ["薪资承诺"],
        },
        "limits": {
            "max_replies": 10, "max_decisions": 20, "max_cost_units": 100,
            "expires_at": "2099-01-01T00:00:00Z", "peer_wait_timeout_seconds": 86400,
        },
        "scripts": [script],
        "slot_evidence_sources": {
            "expected_time": {"source": "peer_message"},
            "company": {"source": "resume_field", "field": "current_company"},
        },
    }
    return spec, script


def _fill_slots_output(script):
    return json.dumps({
        "action": "fill_slots",
        "script_version_id": script["script_version_id"],
        "content_hash": script["content_hash"],
        "slot_values": {"expected_time": "周五16:00"},
        "evidence_message_ids": ["m1"],
    }, ensure_ascii=False)


def _binding_row(tenant_id, bid):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bs_boss_conversation_bindings WHERE tenant_id=%s AND id=%s",
            (tenant_id, str(bid)),
        )
        return dict(cursor.fetchone())


class TestFakeEndToEnd:
    def test_full_chain_reply_to_projection(self, tenant_id, device_row, boss_binding):
        from src.db.database import get_db_connection
        from src.boss_conversation.projection import run_projection_tick
        from src.local_tools import operation_result
        from src.local_tools.permits import write_authorize
        from src.local_tools.repository import mark_started
        from src.local_tools.security import generate_claim_token, sha256_hex
        from src.session_tasks import service

        spec, script = _e2e_binding_spec(tenant_id)
        task_id, claimed = publish_and_claim_boss(tenant_id, device_row, boss_binding, spec)

        # 1) 观察 → 批次 ingest + 建 reply 决策
        _, decision = _submit_reply(tenant_id, device_row, claimed, boss_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "我周五16:00有空",
             "source_evidence_ref": "e1"}
        ])

        # 2) 决策（fake 模型 fill_slots）→ 确定性渲染归一化 reply
        model, calls = _fake_model([_fill_slots_output(script)])
        decisions_mod.run_decision_tick(model_call=model)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, action, reply_text_hash FROM session_task_decisions "
                "WHERE tenant_id=%s AND id=%s",
                (tenant_id, decision["decision_id"]),
            )
            drow = dict(cursor.fetchone())
        assert drow["status"] == "ready" and drow["action"] == "reply"

        # 渲染正文经 payload 服务取回（peer 槽位 + 服务端 resume 槽位逐字替换）
        from src.desktop_automation.adapters import AdapterContext
        from src.boss_conversation.adapters import BossConversationAdapter

        adapter = BossConversationAdapter()
        payload_ref = f"da:boss.chat_reply.v1:boss-reply:{decision['decision_id']}"
        # prepare 之前正文已冻结；此处直接读决策文本验证渲染结果
        from src.session_tasks.texts import load_text

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT reply_text_id FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
                (tenant_id, decision["decision_id"]),
            )
            text_row = cursor.fetchone()
            payload = load_text(conn, tenant_id, task_id, text_row["reply_text_id"], expected_purpose="decision")
        assert payload["text"] == "您好，周五16:00 方便沟通吗？我目前在字节跳动的招聘团队。"

        # 3) prepare-send Phase A 门禁（eligible 计数落库）→ Phase B/C 物化
        prepared = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            uuid.UUID(decision["decision_id"]),
        )
        assert prepared["status"] == "prepared" and prepared["invocation_id"]
        row = _binding_row(tenant_id, boss_binding["conversation_binding_id"])
        assert int(row["rate_trigger_count"]) == 1
        assert str(row["last_rate_decision_id"]) == decision["decision_id"]

        # 4) claim invocation + write-authorize（复判通过插 reserved）
        token = generate_claim_token()
        token_hash = sha256_hex(token)
        decisions_mod.claim_session_invocation(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            uuid.UUID(prepared["invocation_id"]), token_hash, 120, None,
        )
        mark_started(prepared["invocation_id"], tenant_id, token_hash)
        from src.local_tools import repository

        args = repository.get_invocation(prepared["invocation_id"], tenant_id)["arguments_json"]
        assert args["receipt_mode"] == "submission" and args["receipt_context"] == "boss_reply"
        permit = write_authorize(
            tenant_id=tenant_id, device_id=str(device_row["id"]),
            invocation_id=prepared["invocation_id"], claim_token_hash=token_hash,
            request_id=args["request_id"], target_version=args.get("target_version"),
            payload_hash=args.get("payload_hash"),
        )
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT s.* FROM bs_boss_conversation_rate_slots s
                WHERE s.tenant_id=%s AND s.delivery_id=(
                    SELECT delivery_id FROM desktop_automation_attempts
                    WHERE tenant_id=%s AND invocation_id=%s ORDER BY attempt_no DESC LIMIT 1)
                """,
                (tenant_id, tenant_id, prepared["invocation_id"]),
            )
            slot = dict(cursor.fetchone())
        assert slot["status"] == "reserved" and str(slot["decision_id"]) == decision["decision_id"]

        # 5) 发送回执（submitted + 提交证据）→ SAVEPOINT 结算 settled
        result = operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]),
            invocation_id=prepared["invocation_id"], claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="submitted",
            evidence_ref=f"boss-submission:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        assert result["acked"] is True and result["state"] == "succeeded"
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status, settlement_effect FROM bs_boss_conversation_rate_slots WHERE id=%s",
                (slot["id"],),
            )
            settled = dict(cursor.fetchone())
        assert settled == {"status": "settled", "settlement_effect": "submitted"}

        # 6) 投影（submitted 不入队；verified 才入队——本链路 submitted 只结算）
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM bs_boss_comm_log_projection_queue WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 0

    def test_verified_receipt_projection_powers_comm_log(self, tenant_id, device_row, boss_binding):
        """verified 回执 → 投影队列 → 后台 job 幂等 upsert（resume_id 关联）。"""
        from src.db.database import get_db_connection
        from src.boss_conversation.projection import run_projection_tick
        from src.local_tools import operation_result, repository
        from src.local_tools.permits import write_authorize
        from src.local_tools.security import generate_claim_token, sha256_hex

        spec, script = _e2e_binding_spec(tenant_id)
        from tests.unit.boss_conversation.conftest import publish_and_claim_boss

        task_id, claimed = publish_and_claim_boss(tenant_id, device_row, boss_binding, spec)
        _, decision = _submit_reply(tenant_id, device_row, claimed, boss_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "我周五16:00有空", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([_fill_slots_output(script)])
        decisions_mod.run_decision_tick(model_call=model)
        prepared = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            uuid.UUID(decision["decision_id"]),
        )
        token = generate_claim_token()
        token_hash = sha256_hex(token)
        decisions_mod.claim_session_invocation(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            uuid.UUID(prepared["invocation_id"]), token_hash, 120, None,
        )
        repository.mark_started(prepared["invocation_id"], tenant_id, token_hash)
        args = repository.get_invocation(prepared["invocation_id"], tenant_id)["arguments_json"]
        permit = write_authorize(
            tenant_id=tenant_id, device_id=str(device_row["id"]),
            invocation_id=prepared["invocation_id"], claim_token_hash=token_hash,
            request_id=args["request_id"], target_version=args.get("target_version"),
            payload_hash=args.get("payload_hash"),
        )
        operation_result.apply_operation_result(
            tenant_id=tenant_id, device_id=str(device_row["id"]),
            invocation_id=prepared["invocation_id"], claim_token_hash=token_hash,
            request_id=args["request_id"], effect="applied", phase="verified",
            evidence_ref=f"boss-send-verifier:{args['request_id']}:1",
            permit_id=permit["permit_id"], permit_token=permit["permit_token"],
        )
        stats = run_projection_tick()
        assert stats["done"] == 1
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT resume_id, direction, channel, source_delivery_id, source_message_id "
                "FROM bs_recruiting_operator_resume_comm_logs WHERE tenant_id=%s",
                (tenant_id,),
            )
            logs = [dict(r) for r in cursor.fetchall()]
        assert len(logs) == 1
        assert logs[0]["resume_id"] == int(boss_binding["resume_id"])
        assert logs[0]["direction"] == "out" and logs[0]["channel"] == "boss"
        # 幂等重放（重复 tick）不双写
        stats2 = run_projection_tick()
        assert stats2["claimed"] == 0
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM bs_recruiting_operator_resume_comm_logs WHERE tenant_id=%s",
                (tenant_id,),
            )
            assert int(cursor.fetchone()["n"]) == 1


class TestSupersedeAndIdempotentPrepare:
    def test_superseded_decision_no_longer_triggers(self, tenant_id, device_row, boss_binding):
        """supersede 后旧 decision 不再触发：不重复计数、不重复物化（设计 §5.5.3
        冻结不变量的定向测试）。"""
        from src.db.database import get_db_connection
        from src.session_tasks import service

        spec, script = _e2e_binding_spec(tenant_id)
        from tests.unit.boss_conversation.conftest import publish_and_claim_boss

        task_id, claimed = publish_and_claim_boss(tenant_id, device_row, boss_binding, spec)
        bid = boss_binding["conversation_binding_id"]
        # 第一轮：旧批次决策 ready → prepare eligible（计数 1）
        _, old_decision = _submit_reply(tenant_id, device_row, claimed, boss_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗，我周五16:00有空", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([_fill_slots_output(script)])
        decisions_mod.run_decision_tick(model_call=model)
        prepared = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            uuid.UUID(old_decision["decision_id"]),
        )
        assert prepared["status"] == "prepared"
        assert int(_binding_row(tenant_id, bid)["rate_trigger_count"]) == 1
        # 新批次接纳 → 旧 decision superseded（supersede_decisions_on_batch 由 ingest 调）
        _feed_batch(tenant_id, device_row, claimed, boss_binding, 2, [
            {"local_message_id": "m2", "sender": "peer", "text": "改到周六行吗", "source_evidence_ref": "e2"}
        ])
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT status FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
                (tenant_id, old_decision["decision_id"]),
            )
            assert cursor.fetchone()["status"] == "superseded"
        # 旧 decision 再 prepare → superseded 200 判别，不进 gate、不物化
        result = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            uuid.UUID(old_decision["decision_id"]),
        )
        assert result["status"] == "superseded"
        assert int(_binding_row(tenant_id, bid)["rate_trigger_count"]) == 1  # 计数不重复

    def test_idempotent_reprepare_skips_count_keeps_block_check(self, tenant_id, device_row, boss_binding):
        """幂等重 prepare：不重复计数（§5.5.2 CR 非阻断 a），但阻断后仍拒绝。"""
        from src.db.database import get_db_connection
        from src.local_tools import repository
        from src.local_tools.security import generate_claim_token, sha256_hex
        from src.session_tasks.constants import SessionTaskError

        spec, script = _e2e_binding_spec(tenant_id)
        from tests.unit.boss_conversation.conftest import publish_and_claim_boss

        task_id, claimed = publish_and_claim_boss(tenant_id, device_row, boss_binding, spec)
        bid = boss_binding["conversation_binding_id"]
        _, decision = _submit_reply(tenant_id, device_row, claimed, boss_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗，我周五16:00有空", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([_fill_slots_output(script)])
        decisions_mod.run_decision_tick(model_call=model)
        prepared = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            uuid.UUID(decision["decision_id"]),
        )
        assert prepared["status"] == "prepared"
        assert int(_binding_row(tenant_id, bid)["rate_trigger_count"]) == 1
        # 幂等重 prepare：返回原 invocation，不重复计数
        again = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            uuid.UUID(decision["decision_id"]),
        )
        assert again["status"] == "prepared"
        assert again["invocation_id"] == prepared["invocation_id"]
        assert int(_binding_row(tenant_id, bid)["rate_trigger_count"]) == 1
        # 之后 binding 被同步阻断 → 幂等重 prepare 也不穿透（阻断检查不跳过）
        with get_db_connection() as conn:
            conn.cursor().execute(
                "UPDATE bs_boss_conversation_bindings SET automation_blocked=TRUE, "
                "automation_block_reason='rate_ledger_anomaly' WHERE tenant_id=%s AND id=%s",
                (tenant_id, bid),
            )
            conn.commit()
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
                uuid.UUID(decision["decision_id"]),
            )
        assert "同步阻断" in str(exc.value)
