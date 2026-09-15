"""C3 执行链测试（prepare-send 幂等/execution_lane 隔离/适配器授权链/serve_payload）。

真实测试 DB；决策经 run_decision_tick（fake 模型）物化 ready，再走 prepare_send
→ 底座 occurrence/run/delivery/invocation（execution_lane='session_task'）→ 定向 claim。
"""

import hashlib
import json
import uuid
from dataclasses import replace

import pytest

from src.session_tasks import decisions as decisions_mod
from src.session_tasks import service
from src.session_tasks.constants import ERR_BUDGET_EXCEEDED, SessionTaskError

from tests.unit.session_tasks.conftest import build_spec, publish_task_helper
from tests.unit.session_tasks.test_c3_decisions import (
    _batch_seq_by_assignment,
    _cfg,
    _device_dict,
    _fake_model,
    _feed_batch,
    _publish_and_claim,
    _submit_reply,
)


@pytest.fixture(autouse=True)
def c3_env(monkeypatch):
    import src.weixin_conversation.config as wx_config
    import src.weixin_conversation.registration as registration

    import src.session_tasks.config as st_config

    monkeypatch.setattr(registration, "get_session_tasks_config", lambda: replace(_cfg(), enabled=True))
    monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: True)
    monkeypatch.setattr(st_config, "tenant_allowed", lambda tenant: True)  # 适配器许可链双开关检查
    registration.ensure_registered()
    cfg = replace(_cfg())
    monkeypatch.setattr(decisions_mod, "get_session_tasks_config", lambda: cfg)
    monkeypatch.setattr(decisions_mod, "tenant_allowed", lambda tenant: True)
    monkeypatch.setattr(
        "src.services.session_record.record_background_llm_usage", lambda usage, **kw: None
    )
    monkeypatch.setattr(
        "src.services.billing.calculate_credit_cost_with_breakdown",
        lambda p, c, m, cached_input_tokens=0: (0.01, {}),
    )
    _batch_seq_by_assignment.clear()
    yield
    registration.reset_registration()


def _ready_reply(tenant_id, device_row, binding, runtime="rt-1"):
    """发布→领取→喂批次→决策 ready，返回 (claimed, decision_id)。"""
    _, claimed = _publish_and_claim(tenant_id, device_row, binding, runtime=runtime)
    _, decision = _submit_reply(tenant_id, device_row, claimed, binding, 1, [
        {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
    ])
    model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "您好，周五14:00可以吗"}, ensure_ascii=False)])
    decisions_mod.run_decision_tick(model_call=model)
    return claimed, decision["decision_id"]


def _invocation_row(tenant_id, invocation_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, state, execution_lane, business_kind, business_ref, tool_name
            FROM local_tool_invocations WHERE tenant_id=%s AND id=%s
            """,
            (tenant_id, invocation_id),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


class TestPrepareSend:
    def test_materializes_single_execution_unit_idempotently(self, tenant_id, device_row, verified_binding):
        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        result = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        assert result["invocation_id"]
        inv = _invocation_row(tenant_id, result["invocation_id"])
        assert inv["state"] == "queued"
        assert inv["execution_lane"] == "session_task"
        assert inv["business_kind"] == "desktop_automation"
        assert inv["business_ref"]["decision_id"] == decision_id
        assert inv["business_ref"]["task_ref"] == claimed["task_id"]
        assert inv["business_ref"]["execution_lane"] == "session_task"
        assert inv["tool_name"] == "weixin_message_send_v2"
        # 幂等：重复 prepare-send 返回同一 invocation（decision 唯一映射）
        again = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        assert again["invocation_id"] == result["invocation_id"]
        # execution_links 唯一
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM session_task_execution_links WHERE tenant_id=%s AND decision_id=%s",
                (tenant_id, decision_id),
            )
            assert int(cursor.fetchone()["n"]) == 1

    def test_superseded_decision_returns_null_invocation(self, tenant_id, device_row, verified_binding):
        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        # 新批次接纳 → 决策 superseded
        _feed_batch(tenant_id, device_row, claimed, verified_binding, 2, [
            {"local_message_id": "m2", "sender": "peer", "text": "改天吧", "source_evidence_ref": "e2"}
        ])
        result = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        assert result["invocation_id"] is None
        assert result["decision_status"] == "superseded"

    def test_stale_fence_rejected(self, tenant_id, device_row, verified_binding):
        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"] + 1, uuid.UUID(decision_id)
            )
        assert exc.value.code == "STALE_ASSIGNMENT"

    def test_max_replies_budget_stops_task(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec("rounds"))
        _, decision = _submit_reply(tenant_id, device_row, claimed, verified_binding, 1, [
            {"local_message_id": "m1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        ])
        model, _ = _fake_model([json.dumps({"action": "reply", "reply_text": "回复"}, ensure_ascii=False)])
        decisions_mod.run_decision_tick(model_call=model)
        # rounds_target=5 + max_replies=10：占满 10 个发送位后第 11 次 prepare 被拒
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            for i in range(10):
                cursor.execute(
                    """
                    INSERT INTO session_task_execution_links (tenant_id, task_id, decision_id)
                    VALUES (%s, %s, %s)
                    """,
                    (tenant_id, claimed["task_id"], str(uuid.uuid4())),
                )
            conn.commit()
        with pytest.raises(SessionTaskError) as exc:
            decisions_mod.prepare_send(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision["decision_id"])
            )
        assert exc.value.code == ERR_BUDGET_EXCEEDED
        from src.db.database import get_db_connection as _c

        with _c() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, completion_reason FROM session_tasks WHERE tenant_id=%s AND id=%s",
                           (tenant_id, claimed["task_id"]))
            task = cursor.fetchone()
        assert task["status"] == "stopped" and task["completion_reason"] == "max_replies_exhausted"


class TestExecutionLane:
    def test_generic_claim_excludes_session_lane(self, tenant_id, device_row, verified_binding):
        """全局通用 claim 在 SQL 层排除 session_task（老客户端不抢新道）。"""
        from src.local_tools import repository
        from src.local_tools.security import generate_claim_token, sha256_hex

        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        result = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        # 通用 claim 领不到（该租户设备仅有 session 道 invocation）
        got = repository.claim_next(str(device_row["id"]), tenant_id, sha256_hex(generate_claim_token()), 60)
        assert got is None

    def test_targeted_claim_requires_matching_assignment(self, tenant_id, device_row, verified_binding):
        from src.local_tools.security import generate_claim_token, sha256_hex

        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        result = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        invocation_id = uuid.UUID(result["invocation_id"])
        token_hash = sha256_hex(generate_claim_token())
        # 错 fence → 409
        with pytest.raises(SessionTaskError):
            decisions_mod.claim_session_invocation(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"] + 1,
                invocation_id, token_hash, 60, None,
            )
        # 正确 fence → 领取成功
        claimed_inv = decisions_mod.claim_session_invocation(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            invocation_id, token_hash, 60, None,
        )
        assert claimed_inv["invocation"] is not None
        assert claimed_inv["invocation"]["state"] == "claimed"
        assert claimed_inv["invocation"]["execution_lane"] == "session_task"
        # 已领取（非 queued）→ 返回当前状态不重复发
        again = decisions_mod.claim_session_invocation(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            invocation_id, token_hash, 60, None,
        )
        assert again["invocation"] is None and again["state"] == "claimed"

    def test_task_paused_blocks_targeted_claim(self, tenant_id, device_row, verified_binding):
        from src.local_tools.security import generate_claim_token, sha256_hex

        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        result = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT version FROM session_tasks WHERE tenant_id=%s AND id=%s",
                           (tenant_id, claimed["task_id"]))
            current_version = int(cursor.fetchone()["version"])
        service.control_task(tenant_id, "user-1", uuid.UUID(claimed["task_id"]), "pause", current_version)
        got = decisions_mod.claim_session_invocation(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
            uuid.UUID(result["invocation_id"]), sha256_hex(generate_claim_token()), 60, None,
        )
        assert got["invocation"] is None and got["state"].startswith("task_")


class TestConversationAdapter:
    def _adapter(self):
        from src.weixin_conversation.adapters import WeixinConversationAdapter

        return WeixinConversationAdapter()

    def _ctx(self, tenant_id, task_id, revision_ref, user_id="user-1"):
        from src.desktop_automation.adapters import AdapterContext

        return AdapterContext(
            tenant_id=tenant_id, user_id=user_id, scenario_key="weixin.conversation.v1",
            task_ref=str(task_id), revision_ref=str(revision_ref),
        )

    def test_serve_payload_returns_frozen_text(self, tenant_id, device_row, verified_binding):
        from src.weixin_conversation.render import build_payload_ref

        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        result = decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT current_spec_id FROM session_tasks WHERE tenant_id=%s AND id=%s",
                           (tenant_id, claimed["task_id"]))
            spec_id = cursor.fetchone()["current_spec_id"]
        data = self._adapter().serve_payload(self._ctx(tenant_id, claimed["task_id"], spec_id), build_payload_ref(decision_id))
        assert data.decode("utf-8") == "您好，周五14:00可以吗"
        assert hashlib.sha256(data).hexdigest() == hashlib.sha256("您好，周五14:00可以吗".encode()).hexdigest()

    def test_serve_payload_cross_task_rejected(self, tenant_id, device_row, verified_binding):
        from src.weixin_conversation.render import build_payload_ref

        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        decisions_mod.prepare_send(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], uuid.UUID(decision_id)
        )
        from src.weixin_conversation.adapters import ConversationAdapterError

        with pytest.raises(ConversationAdapterError):
            # ctx.task_ref 指向别的任务 → 跨会话引用被拒
            self._adapter().serve_payload(
                self._ctx(tenant_id, uuid.uuid4(), uuid.uuid4()), build_payload_ref(decision_id)
            )

    def test_authorize_denies_superseded_decision(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT reply_text_hash, current_spec_id FROM session_task_decisions d, session_tasks t "
                "WHERE t.tenant_id=%s AND t.id=%s",
                (tenant_id, claimed["task_id"]),
            )
            row = cursor.fetchone()
        decision_hash = _decision_hash(tenant_id, decision_id)
        ctx = self._ctx(tenant_id, claimed["task_id"], row["current_spec_id"])
        ok = self._adapter().authorize_operation(
            ctx, operation="weixin_message_send_v2", target_ref=verified_binding["conversation_binding_id"],
            target_version=None, payload_hash=decision_hash, authorization_revision=str(row["current_spec_id"]),
            authorization_epoch=1,
        )
        assert ok.allowed
        # 新批次 → 决策 superseded → 拒绝
        _feed_batch(tenant_id, device_row, claimed, verified_binding, 2, [
            {"local_message_id": "m2", "sender": "peer", "text": "新消息", "source_evidence_ref": "e2"}
        ])
        denied = self._adapter().authorize_operation(
            ctx, operation="weixin_message_send_v2", target_ref=verified_binding["conversation_binding_id"],
            target_version=None, payload_hash=decision_hash, authorization_revision=str(row["current_spec_id"]),
            authorization_epoch=1,
        )
        assert not denied.allowed
        assert "decision" in denied.reason

    def test_authorize_denies_wrong_hash(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        claimed, _ = _ready_reply(tenant_id, device_row, verified_binding)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT current_spec_id FROM session_tasks WHERE tenant_id=%s AND id=%s",
                           (tenant_id, claimed["task_id"]))
            spec_id = cursor.fetchone()["current_spec_id"]
        decision = self._adapter().authorize_operation(
            self._ctx(tenant_id, claimed["task_id"], spec_id),
            operation="weixin_message_send_v2", target_ref=verified_binding["conversation_binding_id"],
            target_version=None, payload_hash="deadbeef", authorization_revision=str(spec_id),
            authorization_epoch=1,
        )
        assert not decision.allowed  # hash 未命中任何决策 → 拒绝


def _decision_hash(tenant_id, decision_id):
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT reply_text_hash FROM session_task_decisions WHERE tenant_id=%s AND id=%s",
                       (tenant_id, decision_id))
        return cursor.fetchone()["reply_text_hash"]


class TestApiContract:
    def test_get_decision_returns_action(self, tenant_id, device_row, verified_binding):
        """get_decision 暴露冻结 action（端侧分流 reply/wait）。"""
        claimed, decision_id = _ready_reply(tenant_id, device_row, verified_binding)
        row = service.get_decision(tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), uuid.UUID(decision_id))
        assert row["action"] == "reply"
        assert row["status"] == "ready"
