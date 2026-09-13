"""设计者二轮评审（9 项）修复的行为验证。

覆盖：真实 _AttrDict 配置加载、多任务连续 claim、confirm 版本 CAS、
verified 完整门禁、控制动作 subject 同步、决策门控、旧 assignment 历史事实
补交、预算 Decimal 边界。
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.session_tasks import service
from src.session_tasks.constants import ERR_FEATURE_DISABLED, ERR_STALE_ASSIGNMENT, SessionTaskError
from src.session_tasks.models import TaskDraftCreatePayload

from tests.unit.session_tasks.conftest import build_draft_payload, build_spec, make_verified_binding, publish_task_helper

import tests.unit.session_tasks.conftest as c
from src.weixin_conversation.config import scenario_enabled as _real_scenario_enabled


def _device_dict(tenant_id, device_row):
    return {"id": device_row["id"], "tenant_id": tenant_id, "user_id": "user-1"}


def _publish(tenant_id, binding, spec=None):
    created = service.create_draft(
        tenant_id, "user-1", TaskDraftCreatePayload.model_validate(build_draft_payload(binding, spec))
    )
    confirmation = service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"])
    published = service.publish_task(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
                                     uuid.UUID(confirmation["confirmation_id"]))
    published["task_id"] = created["task_id"]
    return published


class TestRealConfigLoading:
    """真实加载路径：settings 额外节是 _AttrDict（属性对象），必须被正确读取（P1-1）。

    注意：`import src.config.settings as x` 会因包属性遮蔽拿到 Settings 实例，
    因此用 sys.modules 定位模块对象再替换其 settings 属性。
    """

    @staticmethod
    def _patch_settings(monkeypatch, fake):
        import sys

        monkeypatch.setattr(sys.modules["src.config.settings"], "settings", fake, raising=False)

    def test_attrdict_node_parsed(self, monkeypatch):
        from src.config.settings import _AttrDict

        import src.session_tasks.config as st_config

        self._patch_settings(
            monkeypatch,
            _AttrDict({"session_tasks": {"enabled": True, "lease_seconds": 120, "tenant_allowlist": ["t1"]}}),
        )
        cfg = st_config.get_session_tasks_config()
        assert cfg.enabled is True and cfg.lease_seconds == 120 and cfg.tenant_allowlist == ["t1"]

    def test_scenario_enabled_hot_read(self, tmp_path):
        """场景开关热读真实解析路径：enabled/allowlist 生效且 fail-closed。"""
        gate_yaml = tmp_path / "gate.yaml"
        gate_yaml.write_text("weixin_conversation:\n  enabled: true\n", encoding="utf-8")
        assert _real_scenario_enabled("any-tenant", str(gate_yaml)) is True

        gate_yaml.write_text(
            "weixin_conversation:\n  enabled: true\n  tenant_allowlist:\n    - t2\n", encoding="utf-8"
        )
        assert _real_scenario_enabled("t2", str(gate_yaml)) is True
        assert _real_scenario_enabled("t1", str(gate_yaml)) is False

        gate_yaml.write_text("weixin_conversation:\n  enabled: false\n", encoding="utf-8")
        assert _real_scenario_enabled("t2", str(gate_yaml)) is False
        assert _real_scenario_enabled("t2", str(tmp_path / "missing.yaml")) is False  # 缺失 fail-closed

    def test_publish_and_claim_blocked_when_scenario_disabled(self, tenant_id, device_row, verified_binding, monkeypatch):
        """发布与分配同时检查通用开关与微信场景开关（P1-1）。"""
        created = service.create_draft(
            tenant_id, "user-1", TaskDraftCreatePayload.model_validate(build_draft_payload(verified_binding))
        )
        confirmation = service.issue_publish_confirmation(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
        )
        import src.weixin_conversation.config as wx_config

        monkeypatch.setattr(wx_config, "scenario_enabled", lambda tenant: False)
        with pytest.raises(SessionTaskError) as exc_info:
            service.publish_task(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
                                 uuid.UUID(confirmation["confirmation_id"]))
        assert exc_info.value.code == ERR_FEATURE_DISABLED
        # 通用开关关闭时 claim 也拒绝（即使任务已存在）
        monkeypatch.setattr(wx_config, "scenario_enabled", lambda tenant: True)
        monkeypatch.setattr(service, "tenant_allowed", lambda tenant: False)
        with pytest.raises(SessionTaskError):
            service.claim_task(_device_dict(tenant_id, device_row), "rt-1")


class TestMultiTaskClaim:
    def test_sequential_claims_across_tasks(self, tenant_id, device_row, verified_binding):
        """同设备两个会话任务：连续 claim 依次领取 A、B，第三次无任务（P1-2）。"""
        binding_b = make_verified_binding(tenant_id, str(device_row["id"]))
        task_a = _publish(tenant_id, verified_binding)
        task_b = _publish(tenant_id, binding_b)
        first = service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        second = service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        third = service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        assert {first["task_id"], second["task_id"]} == {task_a["task_id"], task_b["task_id"]}
        assert third is None
        # 他实例：两个任务都被 rt-1 有效持有 → 无可领取
        assert service.claim_task(_device_dict(tenant_id, device_row), "rt-2") is None

    def test_expired_task_reclaimed_by_other_instance(self, tenant_id, device_row, verified_binding):
        """A 租约过期后 rt-2 抢占换代；rt-1 再 claim 领取的是无租约的 B（如有）。"""
        binding_b = make_verified_binding(tenant_id, str(device_row["id"]))
        task_a = _publish(tenant_id, verified_binding)
        task_b = _publish(tenant_id, binding_b)
        first = service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        c.expire_assignment_lease(tenant_id, first["assignment_id"])
        stolen = service.claim_task(_device_dict(tenant_id, device_row), "rt-2")
        assert stolen["task_id"] == task_a["task_id"] and stolen["fence"] == first["fence"] + 1
        del task_b


class TestConfirmVersionCas:
    def test_confirm_binds_viewed_version(self, tenant_id, verified_binding):
        """用户看过 v1、他人改成 v2 后点确认 → 版本 CAS 拒绝（P1-3）。"""
        created = service.create_draft(
            tenant_id, "user-1", TaskDraftCreatePayload.model_validate(build_draft_payload(verified_binding))
        )
        with pytest.raises(SessionTaskError, match="版本冲突"):
            service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created["task_id"]), 99)
        service.update_draft(tenant_id, "user-1", uuid.UUID(created["task_id"]), 1, build_spec("rounds"))
        with pytest.raises(SessionTaskError, match="版本冲突"):
            service.issue_publish_confirmation(tenant_id, "user-1", uuid.UUID(created["task_id"]), 1)

    def test_detail_returns_published_and_draft(self, tenant_id, verified_binding):
        """暂停改版后详情同时返回已发布 spec 与待确认 draft_spec（P1-3）。"""
        published = _publish(tenant_id, verified_binding)
        task_id = uuid.UUID(published["task_id"])
        paused = service.control_task(tenant_id, "user-1", task_id, "pause", published["version"])
        service.update_draft(tenant_id, "user-1", task_id, paused["version"], build_spec("rounds"))
        detail = service.get_task(tenant_id, "user-1", task_id)
        assert detail["spec"]["completion_rule"]["mode"] == "judged"
        assert detail["draft_spec"]["completion_rule"]["mode"] == "rounds"


class TestVerifiedBindingGate:
    def _published_draft(self, tenant_id, binding):
        created = service.create_draft(
            tenant_id, "user-1", TaskDraftCreatePayload.model_validate(build_draft_payload(binding))
        )
        confirmation = service.issue_publish_confirmation(
            tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"]
        )
        return created, confirmation

    def test_expired_verified_binding_rejects_publish(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bs_weixin_conversation_bindings SET expires_at=%s WHERE tenant_id=%s AND id=%s",
                (datetime.now(timezone.utc) - timedelta(days=1), tenant_id, verified_binding["conversation_binding_id"]),
            )
            conn.commit()
        created, confirmation = self._published_draft(tenant_id, verified_binding)
        with pytest.raises(SessionTaskError, match="验证已过期"):
            service.publish_task(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
                                 uuid.UUID(confirmation["confirmation_id"]))

    def test_zero_identity_version_rejects_publish(self, tenant_id, verified_binding):
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bs_weixin_conversation_bindings SET identity_version=0 WHERE tenant_id=%s AND id=%s",
                (tenant_id, verified_binding["conversation_binding_id"]),
            )
            conn.commit()
        created, confirmation = self._published_draft(tenant_id, verified_binding)
        with pytest.raises(SessionTaskError, match="identity_version"):
            service.publish_task(tenant_id, "user-1", uuid.UUID(created["task_id"]), created["version"],
                                 uuid.UUID(confirmation["confirmation_id"]))


class TestSubjectSync:
    def test_control_actions_sync_subject(self, tenant_id, verified_binding):
        """pause/stop 同步 subject 状态；stop 递增 authorization_epoch（P1-5）。"""
        published = _publish(tenant_id, verified_binding)
        task_id = uuid.UUID(published["task_id"])
        from src.db.database import get_db_connection

        def subject_row():
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT status, authorization_epoch FROM desktop_automation_subjects WHERE tenant_id=%s AND scenario_key='weixin.conversation.v1' AND kind='task' AND ref=%s",
                    (tenant_id, str(task_id)),
                )
                return cursor.fetchone()

        paused = service.control_task(tenant_id, "user-1", task_id, "pause", published["version"])
        row = subject_row()
        # pause 即撤销授权代：subject epoch 递增（评审 P1-1）
        assert row["status"] == "paused" and row["authorization_epoch"] == published["control_epoch"] + 1
        stopped = service.control_task(tenant_id, "user-1", task_id, "stop", paused["version"], reason_code="done")
        row = subject_row()
        # stop 再次撤销：epoch = published + 2
        assert row["status"] == "stopped" and row["authorization_epoch"] == published["control_epoch"] + 2

    def test_resume_blocked_in_c1(self, tenant_id, verified_binding):
        """C1 阶段恢复明确阻断（服务端复核待 C2/C3；不接受客户端声明）。"""
        published = _publish(tenant_id, verified_binding)
        paused = service.control_task(tenant_id, "user-1", uuid.UUID(published["task_id"]), "pause", published["version"])
        with pytest.raises(SessionTaskError, match="C1 未接入"):
            service.control_task(tenant_id, "user-1", uuid.UUID(published["task_id"]), "resume", paused["version"])


class TestDecisionGate:
    def test_decision_blocked_when_disabled(self, tenant_id, device_row, verified_binding, monkeypatch):
        """新决策是新授权点：功能关闭即拒绝（P1-6）。"""
        _publish(tenant_id, verified_binding)
        claimed = service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        monkeypatch.setattr(service, "tenant_allowed", lambda tenant: False)
        with pytest.raises(SessionTaskError) as exc_info:
            service.create_decision(tenant_id, device_row["id"], uuid.UUID(claimed["assignment_id"]),
                                    claimed["fence"], "opening", "opening", 0,
                                    claimed["control_epoch"], claimed["spec_revision"])
        assert exc_info.value.code == ERR_FEATURE_DISABLED


class TestHistoricalFacts:
    def test_superseded_assignment_can_backfill_facts(self, tenant_id, device_row, verified_binding):
        """换代后旧 assignment 未同步事实可补交（historical=true，不授予执行权）（P2-8）。"""
        published = _publish(tenant_id, verified_binding)
        old = service.claim_task(_device_dict(tenant_id, device_row), "rt-1")
        c.expire_assignment_lease(tenant_id, old["assignment_id"])
        new = service.claim_task(_device_dict(tenant_id, device_row), "rt-2")
        assert new["fence"] == old["fence"] + 1
        result = service.ingest_events(
            tenant_id, device_row["id"], uuid.UUID(old["assignment_id"]), old["fence"],
            [{"local_seq": 1, "event_id": "evt-hist-1", "type": "status", "payload": {"phase": "waiting_peer"}}],
        )
        assert result["historical"] is True and result["ack_seq"] == 1
        # 当前代错 fence 仍拒绝（历史路径只对已换代 assignment 开放）
        with pytest.raises(SessionTaskError) as exc_info:
            service.ingest_events(
                tenant_id, device_row["id"], uuid.UUID(new["assignment_id"]), new["fence"] + 9,
                [{"local_seq": 1, "event_id": "evt-hist-2", "type": "status", "payload": {}}],
            )
        assert exc_info.value.code == ERR_STALE_ASSIGNMENT


class TestDecimalBudget:
    def test_float_precision_boundary(self, tenant_id, device_row, verified_binding):
        """0.1+0.2 预算 0.3：Decimal 比较不再误拒（P2-9）。"""
        spec = build_spec()
        spec["limits"]["max_cost_units"] = 0.3
        published = _publish(tenant_id, verified_binding, spec)
        task_id = uuid.UUID(published["task_id"])
        service.reserve_cost(tenant_id, task_id, "decision", "d1", 0.1)
        service.reserve_cost(tenant_id, task_id, "decision", "d2", 0.2)
        with pytest.raises(SessionTaskError, match="任务预算不足"):
            service.reserve_cost(tenant_id, task_id, "decision", "d3", 0.1)

    def test_invalid_amounts_rejected(self, tenant_id, device_row, verified_binding):
        published = _publish(tenant_id, verified_binding)
        task_id = uuid.UUID(published["task_id"])
        service.reserve_cost(tenant_id, task_id, "decision", "d1", 5)
        for bad in (-1, 0, "abc", float("nan"), float("inf")):
            with pytest.raises(SessionTaskError, match="非法数值|必须为正"):
                service.reserve_cost(tenant_id, task_id, "decision", f"bad-{bad}", bad)
        with pytest.raises(SessionTaskError, match="settled_amount"):
            service.settle_cost(tenant_id, task_id, "decision", "d1", -0.1)
