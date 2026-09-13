"""独立测试/CR 补充用例（C1 二轮）：pending 拒发布、加密边界、跨设备 ACL、
opening 跨 revision、batch 归属与强断言、claim 门控、paused 改版流程。"""

import uuid

import pytest

from src.session_tasks import service
from src.session_tasks.constants import ERR_FEATURE_DISABLED, ERR_IDEMPOTENCY_CONFLICT, SessionTaskError
from src.session_tasks.models import TaskDraftCreatePayload
from src.session_tasks.texts import load_text

from tests.unit.session_tasks.conftest import (
    build_draft_payload,
    build_spec,
    expire_assignment_lease,
    publish_task_helper,
)


def _create(tenant_id, binding, spec=None):
    return service.create_draft(
        tenant_id, "user-1", TaskDraftCreatePayload.model_validate(build_draft_payload(binding, spec))
    )


def _device_dict(tenant_id, device_row):
    return {"id": device_row["id"], "tenant_id": tenant_id, "user_id": "user-1"}


class TestPendingBinding:
    def test_pending_binding_allows_draft_but_rejects_publish(self, tenant_id, device_row, pending_binding):
        """设计 §13.3：pending 绑定可先准备草稿，拒绝生产发布。"""
        created = _create(tenant_id, pending_binding)
        assert created["status"] == "draft"
        confirmation = service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"])
        with pytest.raises(SessionTaskError, match="尚未通过真机验证"):
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(created["task_id"]), 1, uuid.UUID(confirmation["confirmation_id"])
            )


class TestPausedReviseFlow:
    def test_pause_update_draft_republish_new_revision(self, tenant_id, verified_binding):
        """设计 §4：修改需暂停→保存新版本→重新发布（P1 修复的行为验证）。"""
        published = publish_task_helper(tenant_id, verified_binding)
        task_id = uuid.UUID(published["task_id"])
        paused = service.control_task(tenant_id, "user-1", task_id, "pause", published["version"])
        updated = service.update_draft(tenant_id, "user-1", task_id, paused["version"], build_spec("rounds"))
        assert updated["version"] == paused["version"] + 1
        confirmation = service.issue_publish_confirmation(tenant_id, "user-1", task_id, updated["version"])
        republished = service.publish_task(tenant_id, "user-1", task_id, updated["version"],
                                           uuid.UUID(confirmation["confirmation_id"]))
        # 评审 P1-2：重发布仅冻结新版本，不进入 active（激活待 C2/C3 恢复门禁）
        assert republished["spec_revision"] == 2 and republished["status"] == "paused"
        assert republished["reactivated"] is False

    def test_active_cannot_update_draft(self, tenant_id, verified_binding):
        published = publish_task_helper(tenant_id, verified_binding)
        with pytest.raises(SessionTaskError, match="先暂停"):
            service.update_draft(tenant_id, "user-1", uuid.UUID(published["task_id"]), published["version"], build_spec())


class TestCryptoBoundaries:
    def test_cross_task_text_ref_rejected(self, tenant_id, verified_binding):
        published = publish_task_helper(tenant_id, verified_binding)
        other_task = uuid.uuid4()
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT spec_text_id FROM session_task_specs WHERE tenant_id=%s AND task_id=%s AND revision=1",
                (tenant_id, published["task_id"]),
            )
            spec_text_id = cursor.fetchone()["spec_text_id"]
        # 跨任务 text_ref（任务 B 读任务 A 的 spec 文本）必须被拒
        with _conn() as crypto_conn:
            with pytest.raises(SessionTaskError, match="不存在"):
                load_text(crypto_conn, tenant_id, other_task, spec_text_id, expected_purpose="spec")

    def test_corrupted_ciphertext_reports_unavailable(self, tenant_id, verified_binding):
        published = publish_task_helper(tenant_id, verified_binding)
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE session_task_texts SET encrypted_payload='garbage-not-fernet' WHERE tenant_id=%s AND task_id=%s AND purpose='spec'",
                (tenant_id, published["task_id"]),
            )
            conn.commit()
        with pytest.raises(SessionTaskError) as exc_info:
            service.get_task(tenant_id, "user-1", uuid.UUID(published["task_id"]))
        assert exc_info.value.code == "CRYPTO_UNAVAILABLE"

    def test_messages_stored_encrypted_not_plaintext(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        assignment = uuid.UUID(claimed["assignment_id"])
        batch_id = str(uuid.uuid4())
        service.ingest_events(
            tenant_id, device_row["id"], assignment, claimed["fence"],
            [{
                "local_seq": 1, "event_id": f"evt-{batch_id[:8]}", "type": "batch",
                "payload": {
                    "batch_id": batch_id, "input_version": 1,
                    "conversation_binding_id": verified_binding["conversation_binding_id"],
                    "messages": [{"local_message_id": "m-enc-1", "sender": "peer", "text": "绝密正文ABC123", "source_evidence_ref": "e"}],
                },
            }],
        )
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT t.encrypted_payload FROM session_task_texts t JOIN session_task_messages m ON m.text_id=t.id WHERE m.tenant_id=%s AND m.message_id='m-enc-1'",
                (tenant_id,),
            )
            payload = cursor.fetchone()["encrypted_payload"]
        assert "绝密正文ABC123" not in payload  # 密文，不含明文


def _conn():  # noqa: ANN202
    from src.db.database import get_db_connection

    return get_db_connection()


def _publish_and_claim(tenant_id, device_row, binding, spec=None, runtime="rt-1"):
    published = publish_task_helper(tenant_id, binding, spec)
    claimed = service.claim_task(_device_dict(tenant_id, device_row), runtime)
    assert claimed is not None
    return published, claimed


class TestCrossDeviceAcl:
    def test_other_device_cannot_operate_assignment(self, tenant_id, device_row, verified_binding):
        """设备 B 不能续租/注入 events/创建决策/读设备 A 的 assignment（CR P1-5）。"""
        from src.local_tools.repository import create_device

        device_b = create_device(tenant_id, "user-1", token_hash=uuid.uuid4().hex,
                                 capabilities={"providers": ["weixin"], "capabilities": ["session_task_v1", "session_observer_v1"]})
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        assignment = uuid.UUID(claimed["assignment_id"])
        with pytest.raises(SessionTaskError) as exc_info:
            service.renew_assignment(tenant_id, device_b["id"], assignment, claimed["fence"], claimed["control_epoch"])
        assert exc_info.value.code == "STALE_ASSIGNMENT"
        with pytest.raises(SessionTaskError):
            service.ingest_events(tenant_id, device_b["id"], assignment, claimed["fence"],
                                  [{"local_seq": 1, "event_id": "evt-b-1", "type": "status", "payload": {}}])
        with pytest.raises(SessionTaskError):
            service.create_decision(tenant_id, device_b["id"], assignment, claimed["fence"], "opening", "opening", 0,
                                    claimed["control_epoch"], claimed["spec_revision"])


class TestOpeningCrossRevision:
    def test_opening_unique_across_spec_revisions(self, tenant_id, device_row, verified_binding):
        """opening 部分唯一索引跨 spec_revision：重发布后不得重建（§13.2）。"""
        published, claimed = _publish_and_claim(tenant_id, device_row, verified_binding, build_spec(opening=True))
        task_id = uuid.UUID(published["task_id"])
        service.create_decision(tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]),
                                claimed["fence"], "opening", "opening", 0,
                                claimed["control_epoch"], claimed["spec_revision"])
        # 暂停 → 改版（仍带开场白）→ 重发布：保持 paused，不可领取、不可再建 opening
        # （评审 P1-2：从暂停回到可执行的入口共用恢复门禁，C1 阻断激活）
        paused = service.control_task(tenant_id, "user-1", task_id, "pause", published["version"])
        updated = service.update_draft(tenant_id, "user-1", task_id, paused["version"], build_spec(opening=True))
        confirmation = service.issue_publish_confirmation(tenant_id, "user-1", task_id, updated["version"])
        republished = service.publish_task(tenant_id, "user-1", task_id, updated["version"],
                                           uuid.UUID(confirmation["confirmation_id"]))
        assert republished["status"] == "paused" and republished["spec_revision"] == 2
        expire_assignment_lease(tenant_id, claimed["assignment_id"])
        assert service.claim_task(_device_dict(tenant_id, device_row), "rt-2") is None  # paused 不可领取
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT count(*) AS c FROM session_task_decisions WHERE tenant_id=%s AND task_id=%s AND decision_kind='opening'",
                (tenant_id, str(task_id)),
            )
            assert cursor.fetchone()["c"] == 1  # opening 决策仍只有一条（部分唯一索引兜底）


class TestBatchAttribution:
    def test_batch_wrong_conversation_rejected(self, tenant_id, device_row, verified_binding):
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        with pytest.raises(SessionTaskError, match="与任务绑定不符"):
            service.ingest_events(
                tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]), claimed["fence"],
                [{
                    "local_seq": 1, "event_id": "evt-foreign-1", "type": "batch",
                    "payload": {
                        "batch_id": str(uuid.uuid4()), "input_version": 1,
                        "conversation_binding_id": str(uuid.uuid4()),
                        "messages": [{"local_message_id": "m-x", "sender": "peer", "text": "x", "source_evidence_ref": "e"}],
                    },
                }],
            )

    def test_rebatch_reuses_existing_message(self, tenant_id, device_row, verified_binding):
        """重新合批（设计评审 P1-7）：批次 [m1] 之后新批次 [m1,m2] 引用 m1 并新增 m2。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        assignment = uuid.UUID(claimed["assignment_id"])

        def _batch(seq, batch_id, messages):
            return {
                "local_seq": seq, "event_id": f"evt-rebatch-{seq}", "type": "batch",
                "payload": {
                    "batch_id": batch_id, "input_version": seq,
                    "conversation_binding_id": verified_binding["conversation_binding_id"],
                    "messages": messages,
                },
            }

        m1 = {"local_message_id": "m-reb-1", "sender": "peer", "text": "在吗", "source_evidence_ref": "e1"}
        m2 = {"local_message_id": "m-reb-2", "sender": "peer", "text": "周五可以吗", "source_evidence_ref": "e2"}
        service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"],
                              [_batch(1, str(uuid.uuid4()), [m1])])
        second_batch_id = str(uuid.uuid4())
        service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"],
                              [_batch(2, second_batch_id, [m1, m2])])
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT message_ids_json FROM session_task_batches WHERE tenant_id=%s AND task_id=%s AND batch_id=%s",
                (tenant_id, claimed["task_id"], second_batch_id),
            )
            ids = cursor.fetchone()["message_ids_json"]
            cursor.execute(
                "SELECT count(*) AS c FROM session_task_messages WHERE tenant_id=%s AND task_id=%s AND message_id='m-reb-1'",
                (tenant_id, claimed["task_id"]),
            )
            m1_rows = cursor.fetchone()["c"]
        assert ids.count("m-reb-") == 2 and m1_rows == 1  # m1 只有一条事实，被两个批次引用

    def test_message_id_conflicting_content_409(self, tenant_id, device_row, verified_binding):
        """同 message_id 异正文 → 409（防篡改既有事实）。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        assignment = uuid.UUID(claimed["assignment_id"])

        def _batch(seq, batch_id, message_id, text):
            return {
                "local_seq": seq, "event_id": f"evt-conf-{seq}", "type": "batch",
                "payload": {
                    "batch_id": batch_id, "input_version": seq,
                    "conversation_binding_id": verified_binding["conversation_binding_id"],
                    "messages": [{"local_message_id": message_id, "sender": "peer", "text": text, "source_evidence_ref": "e"}],
                },
            }

        service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"],
                              [_batch(1, str(uuid.uuid4()), "m-conf-1", "原始内容")])
        with pytest.raises(SessionTaskError, match="正文不一致"):
            service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"],
                                  [_batch(2, str(uuid.uuid4()), "m-conf-1", "篡改内容")])

    def test_seen_event_does_not_advance_prefix(self, tenant_id, device_row, verified_binding):
        """已见 event 幂等放行但不推前缀（防跳号；CR P2①）。"""
        _, claimed = _publish_and_claim(tenant_id, device_row, verified_binding)
        assignment = uuid.UUID(claimed["assignment_id"])
        service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"],
                              [{"local_seq": 1, "event_id": "evt-seen-1", "type": "status", "payload": {"a": 1}}])
        # 重投 seq=5 的已见事件（伪造高 seq）不得把 ack 推到 5
        result = service.ingest_events(tenant_id, device_row["id"], assignment, claimed["fence"],
                                       [{"local_seq": 5, "event_id": "evt-seen-1", "type": "status", "payload": {"a": 1}}])
        assert result["ack_seq"] == 1


class TestClaimGate:
    def test_claim_rejected_when_disabled(self, tenant_id, device_row, verified_binding, monkeypatch):
        """claim 是新授权点：enabled=false 时拒绝分配（测试/CR 双 P1）。"""
        publish_task_helper(tenant_id, verified_binding)
        monkeypatch.setattr(service, "tenant_allowed", lambda tenant: False)
        with pytest.raises(SessionTaskError) as exc_info:
            service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        assert exc_info.value.code == ERR_FEATURE_DISABLED
