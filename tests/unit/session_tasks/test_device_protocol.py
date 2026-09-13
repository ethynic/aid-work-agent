"""设备协议：claim/fence/租约、events 前缀 ACK、决策幂等、预算预留（C1 测试矩阵）。"""

import uuid

import pytest

from src.session_tasks import service
from src.session_tasks.constants import (
    ERR_BUDGET_EXCEEDED,
    ERR_EVENT_GAP,
    ERR_EVENT_PAYLOAD_CONFLICT,
    ERR_STALE_ASSIGNMENT,
    SessionTaskError,
)

from tests.unit.session_tasks.conftest import build_spec, expire_assignment_lease, publish_task_helper


def _device_dict(tenant_id, device_row):
    return {"id": device_row["id"], "tenant_id": tenant_id, "user_id": "user-1"}


def _publish_and_claim(tenant_id, device_row, binding, spec=None, runtime="rt-1"):
    published = publish_task_helper(tenant_id, binding, spec)
    claimed = service.claim_task(_device_dict(tenant_id, device_row), runtime)
    assert claimed is not None
    return published, claimed


class TestClaimAndLease:
    def test_claim_assigns_fence_and_spec(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec(opening=True))
        assert claimed["fence"] == 1
        assert claimed["spec"]["goal"].startswith("确认对方")
        assert claimed["lease_seconds"] == 60

    def test_second_instance_cannot_steal_valid_lease(self, tenant_id, device_row, verified_binding):
        _publish_and_claim(tenant_id, device_row, verified_binding, runtime="rt-1")
        assert service.claim_task(_device_dict(tenant_id, device_row), "rt-2") is None

    def test_same_instance_reclaim_skips_held_task(self, tenant_id, device_row, verified_binding):
        """同实例已持有有效租约：claim 跳过（不重复下发），靠 renew 维持（评审 P1-2）。"""
        _, first = _publish_and_claim(tenant_id, device_row, verified_binding, runtime="rt-1")
        assert service.claim_task(_device_dict(tenant_id, device_row), "rt-1") is None
        result = service.renew_assignment(
            tenant_id, device_row["id"], uuid.UUID(first["assignment_id"]), first["fence"], first["control_epoch"]
        )
        assert result["control"]["status"] == "active"

    def test_expired_lease_reassigned_with_fence_increment(self, tenant_id, device_row, verified_binding):
        _, first = _publish_and_claim(tenant_id, device_row, verified_binding, runtime="rt-1")
        expire_assignment_lease(tenant_id, first["assignment_id"])
        second = service.claim_task(_device_dict(tenant_id, device_row), "rt-2")
        assert second["fence"] == first["fence"] + 1
        assert second["assignment_id"] != first["assignment_id"]
        # 旧 fence 的 renew 必须 STALE
        with pytest.raises(SessionTaskError) as exc_info:
            service.renew_assignment(tenant_id, device_row["id"], uuid.UUID(first["assignment_id"]), first["fence"], first["control_epoch"])
        assert exc_info.value.code == ERR_STALE_ASSIGNMENT

    def test_renew_extends_lease_and_returns_control(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        result = service.renew_assignment(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], claimed["control_epoch"]
        )
        assert result["control"]["status"] == "active"

    def test_renew_wrong_control_epoch_stale(self, tenant_id, device_row, verified_binding):
        published, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        service.control_task(tenant_id, "user-1", uuid.UUID(published["task_id"]), "pause", published["version"])
        with pytest.raises(SessionTaskError) as exc_info:
            service.renew_assignment(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], claimed["control_epoch"]
            )
        assert exc_info.value.code == ERR_STALE_ASSIGNMENT

    def test_renew_after_pause_reports_paused_not_active(self, tenant_id, device_row, verified_binding):
        published, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        paused = service.control_task(tenant_id, "user-1", uuid.UUID(published["task_id"]), "pause", published["version"])
        result = service.renew_assignment(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], paused["control_epoch"]
        )
        assert result["control"]["status"] == "paused"

    def test_expired_lease_renew_rejected(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        expire_assignment_lease(tenant_id, claimed["assignment_id"])
        with pytest.raises(SessionTaskError, match="租约已过期"):
            service.renew_assignment(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], claimed["control_epoch"]
            )

    def test_claim_missing_capability_rejected(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        publish_task_helper(tenant_id, verified_binding)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE local_tool_devices SET capabilities_json = %s WHERE id = %s",
                ('{"providers": []}', device_row["id"]),
            )
            conn.commit()
        with pytest.raises(SessionTaskError, match="缺少必需能力"):
            service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
    def test_paused_task_not_claimed(self, tenant_id, device_row, verified_binding):
        published = publish_task_helper(tenant_id, verified_binding)
        service.control_task(tenant_id, "user-1", uuid.UUID(published["task_id"]), "pause", published["version"])
        assert service.claim_task(_device_dict(tenant_id, device_row), "rt-1") is None


def _event(seq, event_id=None, event_type="status", payload=None):
    return {
        "local_seq": seq,
        "event_id": event_id or f"evt-{seq}",
        "type": event_type,
        "payload": payload or {"phase": "waiting_peer"},
    }


class TestEventsIngestion:
    def test_prefix_ack_and_replay_idempotent(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        assignment = uuid.UUID(claimed["assignment_id"])
        result = service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"], [_event(1), _event(2)])
        assert result["ack_seq"] == 2
        # 同 event_id 同 payload 重投 → 幂等，不重复计数
        replay = service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"], [_event(1), _event(2)])
        assert replay["ack_seq"] == 2

    def test_same_id_different_payload_conflict(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        assignment = uuid.UUID(claimed["assignment_id"])
        service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"], [_event(1, payload={"phase": "a"})])
        with pytest.raises(SessionTaskError) as exc_info:
            service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"], [_event(1, payload={"phase": "b"})])
        assert exc_info.value.code == ERR_EVENT_PAYLOAD_CONFLICT

    def test_out_of_order_seq_rejected(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        assignment = uuid.UUID(claimed["assignment_id"])
        with pytest.raises(SessionTaskError) as exc_info:
            service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"], [_event(2)])
        assert exc_info.value.code == ERR_EVENT_GAP
        # 补发 1 后 2 可接纳
        result = service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"], [_event(1), _event(2)])
        assert result["ack_seq"] == 2

    def test_batch_event_materializes_messages(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        assignment = uuid.UUID(claimed["assignment_id"])
        batch_id = str(uuid.uuid4())
        batch_payload = {
            "batch_id": batch_id,
            "input_version": 1,
            "conversation_binding_id": verified_binding["conversation_binding_id"],
            "binding_version": 0,
            "observation_id": "obs-1",
            "messages": [
                {"local_message_id": "m-1", "sender": "peer", "text": "在吗", "source_evidence_ref": "evd:1"},
                {"local_message_id": "m-2", "sender": "peer", "text": "  保留空格  ", "source_evidence_ref": "evd:2"},
            ],
        }
        result = service.ingest_events(
            tenant_id, device_row["id"], assignment, claimed["fence"], [_event(1, event_type="batch", payload=batch_payload)]
        )
        assert result["ack_seq"] == 1
        detail = service.get_task(tenant_id, "user-1", uuid.UUID(claimed["task_id"]))
        assert detail["status"] == "active"
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT message_ids_json FROM session_task_batches WHERE tenant_id=%s AND task_id=%s AND batch_id=%s",
                    (tenant_id, claimed["task_id"], batch_id),
                )
                assert cursor.fetchone()["message_ids_json"].count("m-") == 2

    def test_events_wrong_fence_stale(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        with pytest.raises(SessionTaskError) as exc_info:
            service.ingest_events(tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"] + 5, [_event(1)])
        assert exc_info.value.code == ERR_STALE_ASSIGNMENT


class TestDecisions:
    def test_reply_decision_idempotent(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        assignment = uuid.UUID(claimed["assignment_id"])
        batch_id = str(uuid.uuid4())
        service.ingest_events(
            tenant_id, device_row["id"], assignment, claimed["fence"],
            [_event(1, event_type="batch", payload={
                "batch_id": batch_id, "input_version": 3,
                "conversation_binding_id": verified_binding["conversation_binding_id"],
                "messages": [{"local_message_id": "m-1", "sender": "peer", "text": "好", "source_evidence_ref": "e"}],
            })],
        )
        first = service.create_decision(tenant_id, device_row["id"], assignment, claimed["fence"], batch_id, "reply", 3,
                                    claimed["control_epoch"], claimed["spec_revision"])
        second = service.create_decision(tenant_id, device_row["id"], assignment, claimed["fence"], batch_id, "reply", 3,
                                    claimed["control_epoch"], claimed["spec_revision"])
        assert first["decision_id"] == second["decision_id"] and first["status"] == "pending"

    def test_reply_input_version_mismatch_rejected(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        assignment = uuid.UUID(claimed["assignment_id"])
        batch_id = str(uuid.uuid4())
        service.ingest_events(
            tenant_id, device_row["id"], assignment, claimed["fence"],
            [_event(1, event_type="batch", payload={
                "batch_id": batch_id, "input_version": 3,
                "conversation_binding_id": verified_binding["conversation_binding_id"],
                "messages": [{"local_message_id": "m-1", "sender": "peer", "text": "好", "source_evidence_ref": "e"}],
            })],
        )
        with pytest.raises(SessionTaskError, match="input_version"):
            service.create_decision(tenant_id, device_row["id"], assignment, claimed["fence"], batch_id, "reply", 2,
                                    claimed["control_epoch"], claimed["spec_revision"])

    def test_opening_decision_once_across_revisions(self, tenant_id, device_row, verified_binding):
        published, claimed = _publish_and_claim(
            tenant_id, device_row, verified_binding, build_spec(opening=True)
        )
        assignment = uuid.UUID(claimed["assignment_id"])
        first = service.create_decision(tenant_id, device_row["id"], assignment, claimed["fence"], "opening", "opening", 0,
                                    claimed["control_epoch"], claimed["spec_revision"])
        assert first["status"] == "pending"
        # 同批重复创建幂等
        again = service.create_decision(tenant_id, device_row["id"], assignment, claimed["fence"], "opening", "opening", 0,
                                    claimed["control_epoch"], claimed["spec_revision"])
        assert again["decision_id"] == first["decision_id"]

    def test_reply_cannot_use_opening_batch(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec(opening=True))
        with pytest.raises(SessionTaskError, match="opening 合成批次"):
            service.create_decision(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], "opening", "reply", 0,
                claimed["control_epoch"], claimed["spec_revision"],
            )

    def test_paused_task_rejects_new_decision(self, tenant_id, device_row, verified_binding):
        published, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        paused = service.control_task(tenant_id, "user-1", uuid.UUID(published["task_id"]), "pause", published["version"])
        # 暂停后 control_epoch 已变：旧 epoch 请求先被版本校验拒绝（评审 P1-6）
        with pytest.raises(SessionTaskError, match="任务控制代或版本已变化"):
            service.create_decision(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], "opening", "opening", 0,
                claimed["control_epoch"], claimed["spec_revision"],
            )

    def test_get_decision_scoped_to_assignment(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec(opening=True))
        decision = service.create_decision(
            tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"], "opening", "opening", 0,
            claimed["control_epoch"], claimed["spec_revision"],
        )
        got = service.get_decision(tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), uuid.UUID(decision["decision_id"]))
        assert got["decision_kind"] == "opening"
        with pytest.raises(SessionTaskError, match="不属于"):
            service.get_decision(tenant_id, uuid.uuid4(), uuid.uuid4(), uuid.UUID(decision["decision_id"]))


class TestBudgetReservations:
    def test_reserve_settle_release_and_idempotency(self, tenant_id, device_row, verified_binding):
        published = publish_task_helper(tenant_id, verified_binding)
        task_id = uuid.UUID(published["task_id"])
        first = service.reserve_cost(tenant_id, task_id, "decision", "dec-1", 10)
        assert first["state"] == "reserved" and first["idempotent"] is False
        replay = service.reserve_cost(tenant_id, task_id, "decision", "dec-1", 10)
        assert replay["idempotent"] is True
        service.settle_cost(tenant_id, task_id, "decision", "dec-1", 8.5)
        second = service.reserve_cost(tenant_id, task_id, "invocation", "inv-1", 20)
        service.release_cost(tenant_id, task_id, "invocation", "inv-1")
        detail = service.get_task(tenant_id, "user-1", task_id)
        assert detail["cost"]["settled"] == pytest.approx(8.5)
        assert detail["cost"]["reserved"] == pytest.approx(0)
        assert detail["cost"]["max_cost_units"] == 100

    def test_reserve_over_limit_rejected(self, tenant_id, device_row, verified_binding):
        published = publish_task_helper(tenant_id, verified_binding)
        task_id = uuid.UUID(published["task_id"])
        service.reserve_cost(tenant_id, task_id, "decision", "dec-1", 99)
        with pytest.raises(SessionTaskError) as exc_info:
            service.reserve_cost(tenant_id, task_id, "decision", "dec-2", 2)
        assert exc_info.value.code == ERR_BUDGET_EXCEEDED
