"""B1.3 BOSS 描述器骨架注册定向测试（设计 §4.1/§5.1；计划 §6）。

覆盖：
- config 默认值：未配置/文件缺失 fail-closed；config.yaml 节点存在且 enabled=false；
- 双门控：session_tasks.enabled 或 boss_conversation.enabled 任一 false →
  ensure_registered False 且三注册表（适配器/决策钩子/描述器）无 boss 键；
- 启用后：ensure_registered True、三注册表含 boss.chat_reply.v1、描述器快照
  （scenario_key/能力/operation_descriptor/receipt_policy 逐键/gate None/guard None）、
  同实例幂等、reset 三清；
- 两场景并存：注册后微信描述器不受影响；
- 骨架 fail-closed：spec_validator 占位拒绝一切；decision_hooks/adapter/
  binding_resolver 行为成员抛 NotImplementedError（或 fail-closed 拒绝值）；
- control_requests._finish 租约归属守卫（B1.2 CR P2#2）：过期租约被另一实例
  认领后，原实例 _finish/_process_one 不生效，认领方完整重处理。

时间口径：租约过期一律用 DB 侧 NOW() ± INTERVAL 显式改写，不依赖宿主机细粒度时钟。
"""

import uuid
from pathlib import Path

import pytest
import yaml

from src.boss_conversation import config as boss_config
from src.boss_conversation import registration as boss_registration
from src.desktop_automation.adapters import TrustedAdapterRegistry
from src.session_tasks import control_requests as control_requests_mod
from src.session_tasks import scenario_descriptor, scenario_hooks

BOSS_KEY = "boss.chat_reply.v1"
WX_KEY = "weixin.conversation.v1"


@pytest.fixture(autouse=True)
def _boss_registration_cleanup():
    """测后复位 boss 三处注册表（不泄漏到其他用例；微信由 conftest _gate_open 复位）。"""
    yield
    boss_registration.reset_registration()


def _enable_boss(monkeypatch, *, session_tasks_enabled=True, boss_enabled=True):
    """双门控打桩：session_tasks 进程快照 + boss_conversation 热读门控。"""
    from dataclasses import replace

    from src.session_tasks.config import SessionTasksConfig

    monkeypatch.setattr(
        boss_registration, "get_session_tasks_config",
        lambda: replace(SessionTasksConfig(), enabled=session_tasks_enabled),
    )
    monkeypatch.setattr(boss_config, "scenario_enabled_gate", lambda: boss_enabled)


# ---------------------------------------------------------------------------
# config 默认值与热读门控
# ---------------------------------------------------------------------------


class TestBossConfig:
    def test_defaults_closed_without_node(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("session_tasks:\n  enabled: false\n", encoding="utf-8")
        assert boss_config.scenario_enabled_gate(str(config_path)) is False
        assert boss_config.tenant_allowed("t-1", str(config_path)) is False

    def test_missing_file_fail_closed(self, tmp_path):
        missing = str(tmp_path / "nope.yaml")
        assert boss_config.scenario_enabled_gate(missing) is False
        assert boss_config.tenant_allowed("t-1", missing) is False

    def test_enabled_and_allowlist(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            "boss_conversation:\n  enabled: true\n  tenant_allowlist: ['t-a']\n",
            encoding="utf-8",
        )
        assert boss_config.scenario_enabled_gate(str(config_path)) is True
        assert boss_config.tenant_allowed("t-a", str(config_path)) is True
        assert boss_config.tenant_allowed("t-b", str(config_path)) is False
        # 空 allowlist = 不限租户
        config_path.write_text(
            "boss_conversation:\n  enabled: true\n  tenant_allowlist: []\n",
            encoding="utf-8",
        )
        assert boss_config.tenant_allowed("t-b", str(config_path)) is True

    def test_repo_yaml_node_exists_and_disabled(self):
        """仓库 configs/config.yaml：boss_conversation 节点存在且默认关闭（B1.3 硬约束）。"""
        node = (yaml.safe_load(
            (Path(__file__).resolve().parents[3] / "configs" / "config.yaml").read_text(encoding="utf-8")
        ) or {}).get("boss_conversation")
        assert isinstance(node, dict)
        assert bool(node.get("enabled")) is False
        assert list(node.get("tenant_allowlist") or []) == []

    def test_process_snapshot_defaults(self):
        cfg = boss_config.get_boss_conversation_config()
        assert cfg.enabled is False
        assert list(cfg.tenant_allowlist) == []


# ---------------------------------------------------------------------------
# 双门控与注册
# ---------------------------------------------------------------------------


def _assert_no_boss_registration():
    assert TrustedAdapterRegistry.get(BOSS_KEY) is None
    assert scenario_hooks.get_hooks(BOSS_KEY) is None
    assert scenario_descriptor.get_descriptor(BOSS_KEY) is None


class TestBossRegistration:
    def test_session_tasks_gate_closed(self, monkeypatch):
        _enable_boss(monkeypatch, session_tasks_enabled=False, boss_enabled=True)
        assert boss_registration.ensure_registered() is False
        _assert_no_boss_registration()

    def test_boss_gate_closed(self, monkeypatch):
        _enable_boss(monkeypatch, session_tasks_enabled=True, boss_enabled=False)
        assert boss_registration.ensure_registered() is False
        _assert_no_boss_registration()

    def test_zero_patch_default_closed(self):
        """零打桩（真实仓库配置）：session_tasks.enabled=false ⇒ ensure_registered False。"""
        assert boss_registration.ensure_registered() is False
        _assert_no_boss_registration()

    def test_enabled_registers_and_idempotent(self, monkeypatch):
        _enable_boss(monkeypatch)
        assert boss_registration.ensure_registered() is True
        assert TrustedAdapterRegistry.get(BOSS_KEY) is not None
        assert scenario_hooks.get_hooks(BOSS_KEY) is not None
        first = scenario_descriptor.get_descriptor(BOSS_KEY)
        assert first is not None
        # 同实例幂等：再次调用返回 True 且描述器对象不变
        assert boss_registration.ensure_registered() is True
        assert scenario_descriptor.get_descriptor(BOSS_KEY) is first

    def test_descriptor_snapshot(self, monkeypatch):
        _enable_boss(monkeypatch)
        assert boss_registration.ensure_registered() is True
        descriptor = scenario_descriptor.require_descriptor(BOSS_KEY)
        assert descriptor.scenario_key == BOSS_KEY
        assert descriptor.required_send_capability == "boss_send_to_v2"
        assert descriptor.operation_descriptor == {
            "operation": "boss_send_to_v2",
            "provider_key": "boss-recruiting",
            "target_ref_source": "conversation_binding_id",
        }
        policy = descriptor.receipt_policy
        assert policy["mode"] == "submission"
        assert policy["context"] == "boss_reply"
        assert policy["submission_evidence_namespace"] == "boss-submission"
        assert policy["verified_evidence_namespace"] == "boss-send-verifier"
        # 频控门禁/绑定同步阻断随 B2 接入：骨架两成员必须为 None
        assert descriptor.send_eligibility_gate is None
        assert descriptor.binding_guard is None
        # 注册表三处 scenario_key 一致（register_scenario 预检之外的直接确认）
        assert TrustedAdapterRegistry.get(BOSS_KEY).scenario_key == BOSS_KEY
        assert scenario_hooks.get_hooks(BOSS_KEY).scenario_key == BOSS_KEY
        assert scenario_hooks.get_hooks(BOSS_KEY).execution_lane == "session_task"

    def test_reset_clears_three_registries(self, monkeypatch):
        _enable_boss(monkeypatch)
        assert boss_registration.ensure_registered() is True
        boss_registration.reset_registration()
        _assert_no_boss_registration()

    def test_workbench_label_resolver(self, monkeypatch):
        _enable_boss(monkeypatch)
        assert boss_registration.ensure_registered() is True
        descriptor = scenario_descriptor.require_descriptor(BOSS_KEY)
        assert descriptor.workbench_label_resolver(None, "t-1", "b-1") == "BOSS 会话"


class TestScenarioCoexistence:
    def test_weixin_descriptor_unaffected_after_boss_registration(self, monkeypatch):
        """双场景并存：boss 注册后微信描述器仍正常（互不干扰）。"""
        _enable_boss(monkeypatch)
        assert boss_registration.ensure_registered() is True
        # conftest _gate_open 已注册微信描述器（autouse），此处直接确认仍在
        wx_descriptor = scenario_descriptor.require_descriptor(WX_KEY)
        boss_descriptor = scenario_descriptor.require_descriptor(BOSS_KEY)
        assert wx_descriptor is not boss_descriptor
        assert wx_descriptor.scenario_key == WX_KEY
        assert wx_descriptor.required_send_capability == "weixin_message_send_v2"
        assert wx_descriptor.send_eligibility_gate is None
        # 微信适配器/钩子注册未被覆盖
        assert TrustedAdapterRegistry.get(WX_KEY) is not None
        assert scenario_hooks.get_hooks(WX_KEY) is not None


# ---------------------------------------------------------------------------
# 骨架 fail-closed：spec_validator / decision_hooks / adapter / binding_resolver
# ---------------------------------------------------------------------------


class TestSkeletonFailClosed:
    @pytest.fixture()
    def descriptor(self, monkeypatch):
        _enable_boss(monkeypatch)
        assert boss_registration.ensure_registered() is True
        return scenario_descriptor.require_descriptor(BOSS_KEY)

    def test_spec_validator_rejects_everything(self, descriptor):
        with pytest.raises(ValueError, match="B2"):
            descriptor.spec_validator({})
        with pytest.raises(ValueError, match="B2"):
            descriptor.spec_validator({"goal": "x", "completion_rule": {"mode": "rounds"}})

    def test_decision_hooks_members_not_implemented(self, descriptor):
        hooks = descriptor.decision_hooks
        with pytest.raises(NotImplementedError):
            hooks.build_decision_messages({}, [], "reply")
        with pytest.raises(NotImplementedError):
            hooks.validate_decision_output({}, "", [], "reply")
        with pytest.raises(NotImplementedError):
            hooks.build_review_messages({}, [], {})
        with pytest.raises(NotImplementedError):
            hooks.validate_review_output("")
        with pytest.raises(NotImplementedError):
            hooks.validate_review_conclusion({}, {}, {})
        with pytest.raises(NotImplementedError):
            hooks.validate_peer_confirmation([], {}, [])
        with pytest.raises(NotImplementedError):
            hooks.evaluate_completion()
        with pytest.raises(NotImplementedError):
            hooks.build_payload_ref(str(uuid.uuid4()))

    def test_adapter_authorize_and_evidence_not_implemented(self, descriptor):
        from src.desktop_automation.adapters import AdapterContext, EvidenceContext

        adapter = descriptor.adapter
        ctx = AdapterContext(
            tenant_id="t-1", user_id="u-1", scenario_key=BOSS_KEY,
            task_ref=str(uuid.uuid4()), revision_ref="rev-1",
        )
        with pytest.raises(NotImplementedError):
            adapter.authorize_operation(
                ctx, operation="boss_send_to_v2", target_ref="b-1", target_version=None,
                payload_hash=None, authorization_revision=None, authorization_epoch=None,
                invocation=None, cursor=None,
            )
        evidence_ctx = EvidenceContext(
            tenant_id="t-1", scenario_key=BOSS_KEY, request_id="req-1",
            evidence_ref="boss-submission:req-1:1",
        )
        with pytest.raises(NotImplementedError):
            adapter.validate_submission_evidence(evidence_ctx)
        with pytest.raises(NotImplementedError):
            adapter.validate_evidence(evidence_ctx)

    def test_adapter_fail_closed_members(self, descriptor):
        from src.desktop_automation.adapters import AdapterContext

        adapter = descriptor.adapter
        ctx = AdapterContext(
            tenant_id="t-1", user_id="u-1", scenario_key=BOSS_KEY,
            task_ref=str(uuid.uuid4()), revision_ref="rev-1",
        )
        assert adapter.validate_revision(ctx, {}).ok is False
        assert adapter.resolve_target(ctx, "b-1").ok is False
        with pytest.raises(Exception):  # noqa: B017 compile fail-closed（BossConversationError）
            adapter.compile_operations(ctx, {})
        with pytest.raises(Exception):  # noqa: B017 payload fail-closed
            adapter.serve_payload(ctx, "boss-reply:x")
        assert adapter.aggregate_result(ctx, []).verdict == "needs_manual_review"
        assert adapter.settle_operation_result(None, {}) is None  # no-op（同微信形态）
        assert adapter.invocation_receipt_arguments(ctx, "b-1") == {}

    def test_binding_resolver_members_not_implemented(self, descriptor):
        resolver = descriptor.binding_resolver
        with pytest.raises(NotImplementedError):
            resolver.get_binding_by_id(None, "t-1", "b-1")
        with pytest.raises(NotImplementedError):
            resolver.get_runtime_identity(None, "t-1", "b-1")
        with pytest.raises(NotImplementedError):
            resolver.is_valid_for_allocation(None, "t-1", "b-1")
        with pytest.raises(NotImplementedError):
            resolver.resolve_draft_targets(None, "t-1", "u-1", "d-1", "r-1")
        with pytest.raises(NotImplementedError):
            resolver.ensure_valid_for_publish({})
        with pytest.raises(NotImplementedError):
            resolver.runtime_target_policy({})
        with pytest.raises(NotImplementedError):
            resolver.account_identity_version({})


# ---------------------------------------------------------------------------
# 组合根：scheduler manager 任一场景注册（boss 在 weixin 之后）
# ---------------------------------------------------------------------------


class TestCombinationRoot:
    def test_any_scenario_registered_semantics(self, monkeypatch):
        """任一注册成功即 True；weixin 失败不阻断 boss；两者都关为 False。"""
        import src.scheduler.manager as manager
        import src.weixin_conversation.registration as wx_registration

        calls = []

        def wx_ok():
            calls.append("wx")
            return True

        def wx_fail():
            calls.append("wx")
            raise RuntimeError("injected wx failure")

        def wx_off():
            calls.append("wx")
            return False

        def boss_ok():
            calls.append("boss")
            return True

        def boss_fail():
            calls.append("boss")
            return False

        monkeypatch.setattr(wx_registration, "ensure_registered", wx_ok)
        monkeypatch.setattr(boss_registration, "ensure_registered", boss_ok)
        assert manager._ensure_any_session_scenario_registered() is True
        # 注册调用不短路（多场景同时启用时各自注册）；整体结果取"任一成功"
        assert calls == ["wx", "boss"]
        calls.clear()

        monkeypatch.setattr(wx_registration, "ensure_registered", wx_fail)
        assert manager._ensure_any_session_scenario_registered() is True
        assert calls == ["wx", "boss"]  # weixin 异常后 boss 仍被尝试
        calls.clear()

        monkeypatch.setattr(wx_registration, "ensure_registered", wx_off)
        monkeypatch.setattr(boss_registration, "ensure_registered", boss_fail)
        assert manager._ensure_any_session_scenario_registered() is False
        assert calls == ["wx", "boss"]  # boss 分支在 weixin 之后仍被执行


# ---------------------------------------------------------------------------
# control_requests._finish 租约归属守卫（B1.2 CR P2#2）
# ---------------------------------------------------------------------------


def _control_request_of(tenant_id: str) -> dict:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM session_task_control_requests WHERE tenant_id=%s", (tenant_id,)
        )
        rows = [dict(r) for r in cursor.fetchall()]
    assert len(rows) == 1
    return rows[0]


def _set_lease_expired(request_id) -> None:
    from src.db.database import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE session_task_control_requests SET processing_lease_expires_at = "
            "NOW() - INTERVAL '1 second' WHERE id=%s",
            (request_id,),
        )
        conn.commit()


class TestFinishLeaseOwnership:
    def test_finish_requires_lease_ownership(self, tenant_id):
        """过期租约下另一实例认领后：原实例 _finish 不写终态；认领方 _finish 生效。"""
        from src.db.database import get_db_connection

        task_id = str(uuid.uuid4())  # 任务不存在不影响本用例（只看请求行终态归属）
        with get_db_connection() as conn:
            assert control_requests_mod.insert_control_request(
                conn.cursor(), tenant_id, task_id,
                expected_control_epoch=1, expected_block_epoch=0,
                reason="lease_probe", source_type="permit_denied", source_ref="probe",
            ) is True
            conn.commit()
        # 实例 A 认领
        claimed_a = [r for r in control_requests_mod._claim_requests(50) if r["tenant_id"] == tenant_id]
        assert len(claimed_a) == 1
        row_a = claimed_a[0]
        owner_a = row_a["processing_owner"]
        assert owner_a
        # 租约过期 → 实例 B 重新认领（新 owner）
        _set_lease_expired(row_a["id"])
        claimed_b = [r for r in control_requests_mod._claim_requests(50) if r["tenant_id"] == tenant_id]
        assert len(claimed_b) == 1
        row_b = claimed_b[0]
        owner_b = row_b["processing_owner"]
        assert owner_b and owner_b != owner_a
        # 原实例 A 的 _finish：租约已归 B → 不写终态
        from src.db.database import get_db_connection

        with get_db_connection() as conn:
            assert control_requests_mod._finish(conn, conn.cursor(), row_a, "applied") is False
        row = _control_request_of(tenant_id)
        assert row["status"] == "processing"
        assert row["processing_owner"] == owner_b
        assert row["processing_lease_expires_at"] is not None
        # 认领方 B 的 _finish：租约归属成立 → applied
        with get_db_connection() as conn:
            assert control_requests_mod._finish(conn, conn.cursor(), row_b, "applied") is True
        row = _control_request_of(tenant_id)
        assert row["status"] == "applied"
        assert row["processing_owner"] is None
        assert row["processing_lease_expires_at"] is None

    def test_process_one_discards_migration_when_lease_lost(
        self, tenant_id, device_row, verified_binding
    ):
        """完整路径：原实例持过期租约处理请求 → lease_lost，迁移被回滚，
        任务保持 active；认领方重处理 → applied + 任务 human_required（通知仅一次）。"""
        from src.db.database import get_db_connection

        from tests.unit.session_tasks.conftest import publish_task_helper
        from tests.unit.session_tasks.test_c3_decisions import _task_row

        published = publish_task_helper(tenant_id, verified_binding)
        task_id = published["task_id"]
        task = _task_row(tenant_id, task_id)
        with get_db_connection() as conn:
            assert control_requests_mod.insert_control_request(
                conn.cursor(), tenant_id, task_id,
                expected_control_epoch=int(task["control_epoch"]), expected_block_epoch=0,
                reason="lease_race_probe", source_type="permit_denied", source_ref="probe",
            ) is True
            conn.commit()
        row_a = [r for r in control_requests_mod._claim_requests(50) if r["tenant_id"] == tenant_id][0]
        # 租约过期 → 实例 B 认领
        _set_lease_expired(row_a["id"])
        row_b = [r for r in control_requests_mod._claim_requests(50) if r["tenant_id"] == tenant_id][0]
        assert row_b["processing_owner"] != row_a["processing_owner"]
        # 原实例 A 按 A 的认领快照处理：迁移在事务内但终态写不中 → lease_lost
        assert control_requests_mod._process_one(row_a) == "lease_lost"
        assert _task_row(tenant_id, task_id)["status"] == "active"  # 迁移已回滚
        row = _control_request_of(tenant_id)
        assert row["status"] == "processing" and row["processing_owner"] == row_b["processing_owner"]
        # 认领方 B 完整重处理 → applied + human_required
        assert control_requests_mod._process_one(row_b) == "applied"
        assert _task_row(tenant_id, task_id)["status"] == "human_required"
        row = _control_request_of(tenant_id)
        assert row["status"] == "applied"
        # A 的未提交迁移已回滚：human_required 通知不重复
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(*) AS n FROM session_task_notifications "
                "WHERE tenant_id=%s AND task_id=%s AND status='human_required'",
                (tenant_id, task_id),
            )
            assert int(cursor.fetchone()["n"]) == 1
