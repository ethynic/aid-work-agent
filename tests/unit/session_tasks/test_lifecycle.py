"""任务生命周期：草稿 CAS → 确认链 → 发布占用 → 控制动作 → ACL（C1 测试矩阵）。"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.session_tasks import service
from src.session_tasks.constants import (
    ERR_CONFIRMATION_INVALID,
    ERR_CONVERSATION_IN_USE,
    SessionTaskError,
)
from src.session_tasks.models import TaskDraftCreatePayload

from tests.unit.session_tasks.conftest import build_draft_payload, build_spec, publish_task_helper


def _create(tenant_id, binding, spec=None):
    return service.create_draft(
        tenant_id, "user-1", TaskDraftCreatePayload.model_validate(build_draft_payload(binding, spec))
    )


class TestDraftLifecycle:
    def test_create_draft_stores_encrypted_spec(self, tenant_id, verified_binding):
        created = _create(tenant_id, verified_binding)
        assert created["status"] == "draft" and created["version"] == 1
        detail = service.get_task(tenant_id, "user-1", uuid.UUID(created["task_id"]))
        assert detail["spec"] is None and detail["draft_spec"]["goal"].startswith("确认对方")
        assert detail["draft_spec"]["limits"]["max_replies"] == 10

    def test_update_draft_cas_conflict(self, tenant_id, verified_binding):
        created = _create(tenant_id, verified_binding)
        with pytest.raises(SessionTaskError, match="版本冲突"):
            service.update_draft(tenant_id, "user-1", uuid.UUID(created["task_id"]), 99, build_spec())

    def test_update_draft_bumps_version(self, tenant_id, verified_binding):
        created = _create(tenant_id, verified_binding)
        result = service.update_draft(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), 1, build_spec("rounds")
        )
        assert result["version"] == 2

    def test_publish_without_confirmation_rejected(self, tenant_id, verified_binding):
        created = _create(tenant_id, verified_binding)
        with pytest.raises(SessionTaskError):
            service.publish_task(tenant_id, "user-1", uuid.UUID(created["task_id"]), 1, uuid.uuid4())

    def test_confirmation_consumed_replay_rejected(self, tenant_id, verified_binding):
        """已消费的确认凭据重放：走允许重发布的 paused 路径验证 consumed 拒绝。"""
        created = _create(tenant_id, verified_binding)
        confirmation = service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"])
        published = service.publish_task(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), 1, uuid.UUID(confirmation["confirmation_id"])
        )
        paused = service.control_task(tenant_id, "user-1", uuid.UUID(created["task_id"]), "pause", published["version"])
        with pytest.raises(SessionTaskError) as exc_info:
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(created["task_id"]), paused["version"],
                uuid.UUID(confirmation["confirmation_id"]),
            )
        assert exc_info.value.code == ERR_CONFIRMATION_INVALID

    def test_confirmation_invalid_after_spec_change(self, tenant_id, verified_binding):
        created = _create(tenant_id, verified_binding)
        confirmation = service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"])
        service.update_draft(tenant_id, "user-1", uuid.UUID(created["task_id"]), 1, build_spec("rounds"))
        with pytest.raises(SessionTaskError) as exc_info:
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(created["task_id"]), 2, uuid.UUID(confirmation["confirmation_id"])
            )
        assert exc_info.value.code == ERR_CONFIRMATION_INVALID

    def test_confirmation_cross_user_rejected(self, tenant_id, verified_binding):
        created = _create(tenant_id, verified_binding)
        confirmation = service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"])
        with pytest.raises(SessionTaskError):
            service.publish_task(
                tenant_id, "user-2", uuid.UUID(created["task_id"]), 1, uuid.UUID(confirmation["confirmation_id"])
            )

    def test_confirmation_expired_rejected(self, tenant_id, verified_binding):
        created = _create(tenant_id, verified_binding)
        confirmation = service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"])
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE session_task_confirmations SET expires_at=%s WHERE confirmation_id=%s",
                    (datetime.now(timezone.utc) - timedelta(seconds=1), confirmation["confirmation_id"]),
                )
            conn.commit()
        with pytest.raises(SessionTaskError) as exc_info:
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(created["task_id"]), 1, uuid.UUID(confirmation["confirmation_id"])
            )
        assert exc_info.value.code == ERR_CONFIRMATION_INVALID

    def test_publish_missing_capability_rejected(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE local_tool_devices SET capabilities_json='{\"providers\":[\"weixin\"]}'::jsonb WHERE id=%s",
                    (verified_binding["device_id"],),
                )
            conn.commit()
        created = _create(tenant_id, verified_binding)
        confirmation = service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"])
        with pytest.raises(SessionTaskError, match="缺少必需能力"):
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(created["task_id"]), 1, uuid.UUID(confirmation["confirmation_id"])
            )

    def test_publish_creates_subject_and_opening_batch(self, tenant_id, verified_binding):
        published = publish_task_helper(tenant_id, verified_binding, build_spec(opening=True))
        assert published["status"] == "active" and published["spec_revision"] == 1
        from src.db.database import get_db_connection

        task_id = uuid.UUID(published["task_id"])
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) AS c FROM desktop_automation_subjects WHERE tenant_id=%s AND scenario_key='weixin.conversation.v1' AND ref=%s",
                    (tenant_id, str(task_id)),
                )
                assert cursor.fetchone()["c"] == 1
                cursor.execute(
                    "SELECT synthetic, input_version FROM session_task_batches WHERE tenant_id=%s AND task_id=%s AND batch_id='opening'",
                    (tenant_id, task_id),
                )
                opening = cursor.fetchone()
                assert opening is not None and opening["synthetic"] is True and opening["input_version"] == 0


class TestOccupancyAndAcl:
    def test_same_conversation_double_publish_rejected(self, tenant_id, verified_binding):
        publish_task_helper(tenant_id, verified_binding)
        created2 = _create(tenant_id, verified_binding)
        confirmation2 = service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created2["task_id"]), created2["version"])
        with pytest.raises(SessionTaskError) as exc_info:
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(created2["task_id"]), 1, uuid.UUID(confirmation2["confirmation_id"])
            )
        assert exc_info.value.code == ERR_CONVERSATION_IN_USE

    def test_drafts_do_not_occupy(self, tenant_id, verified_binding):
        _create(tenant_id, verified_binding)
        created2 = _create(tenant_id, verified_binding)
        assert created2["status"] == "draft"

    def test_stopped_task_releases_occupancy(self, tenant_id, verified_binding):
        published = publish_task_helper(tenant_id, verified_binding)
        service.control_task(tenant_id, "user-1", uuid.UUID(published["task_id"]), "stop",
                             published["version"], reason_code="user_cancel")
        # 终态后同会话可再发布新任务
        publish_task_helper(tenant_id, verified_binding)

    def test_cross_tenant_404(self, tenant_id, verified_binding):
        published = publish_task_helper(tenant_id, verified_binding)
        with pytest.raises(SessionTaskError, match="不存在"):
            service.get_task("other-tenant", "user-1", uuid.UUID(published["task_id"]))

    def test_non_owner_same_tenant_404(self, tenant_id, verified_binding):
        published = publish_task_helper(tenant_id, verified_binding)
        with pytest.raises(SessionTaskError, match="不存在"):
            service.get_task(tenant_id, "user-2", uuid.UUID(published["task_id"]))

    def test_foreign_confirmation_rejected(self, tenant_id, verified_binding):
        """用别的任务的确认凭据发布 → 版本/摘要不匹配拒绝。"""
        created_a = _create(tenant_id, verified_binding)
        conf_a = service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created_a["task_id"]), created_a["version"])
        created_b = _create(tenant_id, verified_binding)
        with pytest.raises(SessionTaskError):
            service.publish_task(
                tenant_id, "user-1", uuid.UUID(created_b["task_id"]), 1, uuid.UUID(conf_a["confirmation_id"])
            )


class TestControlActions:
    def test_pause_stop_flow_and_resume_blocked_in_c1(self, tenant_id, verified_binding):
        """C1 阶段 resume 明确阻断（服务端复核能力待 C2/C3；不接受客户端声明）。"""
        published = publish_task_helper(tenant_id, verified_binding)
        task_id, version = uuid.UUID(published["task_id"]), published["version"]

        paused = service.control_task(tenant_id, "user-1", task_id, "pause", version)
        assert paused["status"] == "paused" and paused["control_epoch"] == published["control_epoch"] + 1

        with pytest.raises(SessionTaskError, match="C1 未接入"):
            service.control_task(tenant_id, "user-1", task_id, "resume", paused["version"])

        with pytest.raises(SessionTaskError, match="reason_code"):
            service.control_task(tenant_id, "user-1", task_id, "stop", paused["version"])
        stopped = service.control_task(tenant_id, "user-1", task_id, "stop", paused["version"], reason_code="budget")
        assert stopped["status"] == "stopped"

        with pytest.raises(SessionTaskError, match="已终结"):
            service.control_task(tenant_id, "user-1", task_id, "pause", stopped["version"])

    def test_handoff(self, tenant_id, verified_binding):
        published = publish_task_helper(tenant_id, verified_binding)
        result = service.control_task(tenant_id, "user-1", uuid.UUID(published["task_id"]), "handoff", published["version"])
        assert result["status"] == "human_required"
