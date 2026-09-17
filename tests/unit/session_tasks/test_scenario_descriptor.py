"""B1.1 ScenarioDescriptor 引入测试（设计 §4.1 / 计划 §4）。

覆盖：描述器注册表（get/require/未注册报错）、register_scenario 原子注册
（任一步失败恢复调用前状态：旧值按对象身份恢复、无旧值删除新增）、微信描述器
内容快照（复刻旧常量 + gate=None + settle no-op）、ensure_registered 三处
注册表一致与双门控、_WeixinBindingResolver 行为锁定（真实 DB）。

描述器注册/快照用例为纯进程内对象；BindingResolver 行为用例依赖真实 DB
（conftest 的 DB 池初始化除外）。
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest

from src.session_tasks import scenario_descriptor


def _cfg(enabled=True):
    from src.session_tasks.config import SessionTasksConfig

    return SessionTasksConfig(enabled=enabled)


@pytest.fixture(autouse=True)
def _clean_registries():
    """用例前后清理三处注册表（含假场景键），不向其他用例泄漏。"""
    yield
    import src.session_tasks.scenario_hooks as scenario_hooks
    from src.desktop_automation.adapters import TrustedAdapterRegistry

    scenario_descriptor.unregister_descriptor("fake.scenario.v1")
    scenario_hooks.unregister_hooks("fake.scenario.v1")
    TrustedAdapterRegistry.unregister("fake.scenario.v1")
    import src.weixin_conversation.registration as registration

    registration.reset_registration()


# ---------------------------------------------------------------------------
# 描述器注册表：get / require / 未注册报错
# ---------------------------------------------------------------------------


class TestDescriptorRegistry:
    def test_get_missing_returns_none(self):
        assert scenario_descriptor.get_descriptor("no.such.scenario") is None

    def test_require_missing_raises_typed_error(self):
        from src.session_tasks.scenario_descriptor import ScenarioDescriptorError

        with pytest.raises(ScenarioDescriptorError, match="no.such.scenario"):
            scenario_descriptor.require_descriptor("no.such.scenario")

    def test_register_and_unregister_roundtrip(self):
        descriptor = SimpleNamespace(scenario_key="fake.scenario.v1")
        scenario_descriptor.register_descriptor(descriptor)
        assert scenario_descriptor.get_descriptor("fake.scenario.v1") is descriptor
        assert scenario_descriptor.require_descriptor("fake.scenario.v1") is descriptor
        assert "fake.scenario.v1" in scenario_descriptor.registered_keys()
        scenario_descriptor.unregister_descriptor("fake.scenario.v1")
        assert scenario_descriptor.get_descriptor("fake.scenario.v1") is None


# ---------------------------------------------------------------------------
# register_scenario 原子注册：一致性预检 + 快照恢复
# ---------------------------------------------------------------------------


class _FakeFactory:
    """构造三处注册表假对象（scenario_key 可指定；SimpleNamespace 相等即同一对象）。"""

    @staticmethod
    def adapter(key="fake.scenario.v1"):
        return SimpleNamespace(scenario_key=key)

    @staticmethod
    def hooks(key="fake.scenario.v1"):
        return SimpleNamespace(scenario_key=key, execution_lane="session_task")

    @staticmethod
    def descriptor(key="fake.scenario.v1", adapter=None, hooks=None):
        return SimpleNamespace(
            scenario_key=key,
            adapter=adapter if adapter is not None else _FakeFactory.adapter(key),
            decision_hooks=hooks if hooks is not None else _FakeFactory.hooks(key),
        )


def _populate_trio(key, adapter, hooks, descriptor):
    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from src.session_tasks import scenario_hooks

    TrustedAdapterRegistry.register(adapter)
    scenario_hooks.register_hooks(hooks)
    scenario_descriptor.register_descriptor(descriptor)


def _trio_state(key):
    from src.desktop_automation.adapters import TrustedAdapterRegistry
    from src.session_tasks import scenario_hooks

    return (
        TrustedAdapterRegistry.get(key),
        scenario_hooks.get_hooks(key),
        scenario_descriptor.get_descriptor(key),
    )


class TestRegisterScenarioAtomicity:
    def test_key_mismatch_rejected_with_zero_writes(self):
        """scenario_key 三处不一致：写入前抛 ScenarioDescriptorError，既有注册零变化。"""
        from src.session_tasks.scenario_descriptor import ScenarioDescriptorError

        old = _FakeFactory.descriptor()
        _populate_trio("fake.scenario.v1", old.adapter, old.decision_hooks, old)

        mismatched_adapter = _FakeFactory.descriptor(adapter=_FakeFactory.adapter("other.scenario.v1"))
        with pytest.raises(ScenarioDescriptorError, match="scenario_key 不一致"):
            scenario_descriptor.register_scenario(mismatched_adapter)
        mismatched_hooks = _FakeFactory.descriptor(hooks=_FakeFactory.hooks("other.scenario.v1"))
        with pytest.raises(ScenarioDescriptorError, match="scenario_key 不一致"):
            scenario_descriptor.register_scenario(mismatched_hooks)

        adapter_now, hooks_now, descriptor_now = _trio_state("fake.scenario.v1")
        assert adapter_now is old.adapter
        assert hooks_now is old.decision_hooks
        assert descriptor_now is old
        assert "other.scenario.v1" not in scenario_descriptor.registered_keys()

    def test_missing_scenario_key_member_fails_before_any_write(self):
        """缺 scenario_key 成员（hooks/adapter）：预检 AttributeError，三表零写入。"""
        broken_hooks = SimpleNamespace(
            scenario_key="fake.scenario.v1",
            adapter=_FakeFactory.adapter(),
            decision_hooks=SimpleNamespace(execution_lane="session_task"),  # 缺 scenario_key
        )
        with pytest.raises(AttributeError):
            scenario_descriptor.register_scenario(broken_hooks)
        assert _trio_state("fake.scenario.v1") == (None, None, None)

        broken_adapter = SimpleNamespace(
            scenario_key="fake.scenario.v1",
            adapter=SimpleNamespace(),  # 缺 scenario_key
            decision_hooks=_FakeFactory.hooks(),
        )
        with pytest.raises(AttributeError):
            scenario_descriptor.register_scenario(broken_adapter)
        assert _trio_state("fake.scenario.v1") == (None, None, None)

    def test_hooks_step_failure_restores_previous_registrations(self, monkeypatch):
        """已有同 key 三项注册、钩子步失败：旧三项按对象身份完整恢复（不丢旧注册）。"""
        from src.session_tasks import scenario_hooks

        old = _FakeFactory.descriptor()
        _populate_trio("fake.scenario.v1", old.adapter, old.decision_hooks, old)
        fresh = _FakeFactory.descriptor()

        def _boom(hooks):
            raise RuntimeError("hooks registry broken")

        monkeypatch.setattr(scenario_hooks, "register_hooks", _boom)
        with pytest.raises(RuntimeError, match="hooks registry broken"):
            scenario_descriptor.register_scenario(fresh)

        adapter_now, hooks_now, descriptor_now = _trio_state("fake.scenario.v1")
        assert adapter_now is old.adapter
        assert hooks_now is old.decision_hooks
        assert descriptor_now is old

    def test_descriptor_step_failure_restores_previous_registrations(self, monkeypatch):
        """已有同 key 三项注册、描述器步失败：旧三项按对象身份完整恢复。"""
        old = _FakeFactory.descriptor()
        _populate_trio("fake.scenario.v1", old.adapter, old.decision_hooks, old)
        fresh = _FakeFactory.descriptor()

        def _boom(descriptor):
            raise RuntimeError("descriptor registry broken")

        monkeypatch.setattr(scenario_descriptor, "register_descriptor", _boom)
        with pytest.raises(RuntimeError, match="descriptor registry broken"):
            scenario_descriptor.register_scenario(fresh)

        adapter_now, hooks_now, descriptor_now = _trio_state("fake.scenario.v1")
        assert adapter_now is old.adapter
        assert hooks_now is old.decision_hooks
        assert descriptor_now is old

    @pytest.mark.parametrize("step", ["hooks", "descriptor"])
    def test_failure_with_empty_registries_stays_empty(self, monkeypatch, step):
        """空注册表下任一步失败：三表保持全空，不留新增残留。"""
        if step == "hooks":
            from src.session_tasks import scenario_hooks

            def _boom(*args):
                raise RuntimeError("boom hooks")

            monkeypatch.setattr(scenario_hooks, "register_hooks", _boom)
            expectation = "boom hooks"
        else:

            def _boom(*args):
                raise RuntimeError("boom descriptor")

            monkeypatch.setattr(scenario_descriptor, "register_descriptor", _boom)
            expectation = "boom descriptor"

        with pytest.raises(RuntimeError, match=expectation):
            scenario_descriptor.register_scenario(_FakeFactory.descriptor())
        assert _trio_state("fake.scenario.v1") == (None, None, None)

    def test_success_registers_all_three(self):
        """成功路径：适配器/决策钩子/描述器三处按 scenario_key 指向描述器内对象。"""
        descriptor = _FakeFactory.descriptor()
        scenario_descriptor.register_scenario(descriptor)
        adapter_now, hooks_now, descriptor_now = _trio_state("fake.scenario.v1")
        assert adapter_now is descriptor.adapter
        assert hooks_now is descriptor.decision_hooks
        assert descriptor_now is descriptor


# ---------------------------------------------------------------------------
# 微信描述器内容快照：复刻旧常量、gate=None、settle no-op
# ---------------------------------------------------------------------------


class TestWeixinDescriptorSnapshot:
    @pytest.fixture()
    def descriptor(self):
        from src.weixin_conversation.descriptor import build_weixin_descriptor

        return build_weixin_descriptor()

    def test_scenario_key(self, descriptor):
        assert descriptor.scenario_key == "weixin.conversation.v1"

    def test_spec_validator_is_original_function(self, descriptor):
        """零包装：spec_validator 必须是 models.validate_task_spec 原函数。"""
        from src.session_tasks.models import validate_task_spec

        assert descriptor.spec_validator is validate_task_spec

    def test_required_send_capability(self, descriptor):
        assert descriptor.required_send_capability == "weixin_message_send_v2"

    def test_operation_descriptor_matches_current_hardcode(self, descriptor):
        """逐键复刻 decisions.py prepare-send 现状（B1.2 切换点声明）。"""
        assert descriptor.operation_descriptor == {
            "operation": "weixin_message_send_v2",
            "provider_key": "weixin",
            "target_ref_source": "conversation_binding_id",
        }

    def test_receipt_policy_matches_current_hardcode(self, descriptor):
        """逐键复刻现状：submitted 双命名空间如实声明（verified=None=现状宽松）。"""
        assert descriptor.receipt_policy == {
            "mode": "submission",
            "context": "weixin_name",
            "submission_evidence_namespace": "weixin-submission",
            "verified_evidence_namespace": None,
        }

    def test_send_eligibility_gate_is_none(self, descriptor):
        """微信不注册频控门禁：通用层直走既有 prepare-send（设计 §4.1）。"""
        assert descriptor.send_eligibility_gate is None

    def test_decision_hooks_delegate_and_keys(self, descriptor):
        from src.weixin_conversation.registration import _ConversationHooks

        assert isinstance(descriptor.decision_hooks, _ConversationHooks)
        assert descriptor.decision_hooks.scenario_key == "weixin.conversation.v1"
        assert descriptor.decision_hooks.execution_lane == "session_task"

    def test_settle_operation_result_is_noop_callable(self, descriptor):
        """settle 为可调用 no-op：微信结算现状无场景账本，调用零副作用。"""
        settle = descriptor.adapter.settle_operation_result
        assert callable(settle)
        assert settle(None, None) is None

    def test_binding_resolver_members_callable(self, descriptor):
        from src.weixin_conversation.descriptor import _WeixinBindingResolver, resolve_workbench_label

        assert isinstance(descriptor.binding_resolver, _WeixinBindingResolver)
        assert descriptor.workbench_label_resolver is resolve_workbench_label

    def test_build_returns_fresh_instances(self):
        """每次 build 新实例与新 dict：注册间不共享可变默认值。"""
        from src.weixin_conversation.descriptor import build_weixin_descriptor

        d1, d2 = build_weixin_descriptor(), build_weixin_descriptor()
        assert d1 is not d2
        assert d1.operation_descriptor is not d2.operation_descriptor
        assert d1.receipt_policy is not d2.receipt_policy


# ---------------------------------------------------------------------------
# ensure_registered / reset_registration：三处注册表一致 + 双门控
# ---------------------------------------------------------------------------


class TestEnsureRegistered:
    def test_registered_populates_all_three_registries(self, monkeypatch):
        import src.weixin_conversation.config as wx_config
        import src.weixin_conversation.registration as registration
        from src.desktop_automation.adapters import TrustedAdapterRegistry
        from src.session_tasks import scenario_hooks

        monkeypatch.setattr(registration, "get_session_tasks_config", lambda: _cfg(enabled=True))
        monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: True)
        assert registration.ensure_registered() is True
        assert TrustedAdapterRegistry.get("weixin.conversation.v1") is not None
        assert scenario_hooks.get_hooks("weixin.conversation.v1") is not None
        assert scenario_descriptor.get_descriptor("weixin.conversation.v1") is not None
        # 幂等：二次调用返回 True，注册对象不重复替换为异实例
        first = scenario_descriptor.get_descriptor("weixin.conversation.v1")
        assert registration.ensure_registered() is True
        assert scenario_descriptor.get_descriptor("weixin.conversation.v1") is first

    def test_reset_clears_all_three_registries(self, monkeypatch):
        import src.weixin_conversation.config as wx_config
        import src.weixin_conversation.registration as registration
        from src.desktop_automation.adapters import TrustedAdapterRegistry
        from src.session_tasks import scenario_hooks

        monkeypatch.setattr(registration, "get_session_tasks_config", lambda: _cfg(enabled=True))
        monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: True)
        assert registration.ensure_registered() is True
        registration.reset_registration()
        assert TrustedAdapterRegistry.get("weixin.conversation.v1") is None
        assert scenario_hooks.get_hooks("weixin.conversation.v1") is None
        assert scenario_descriptor.get_descriptor("weixin.conversation.v1") is None

    def test_session_tasks_gate_closed_registers_nothing(self, monkeypatch):
        """session_tasks.enabled 关闭：返回 False 且三处注册表零写入。"""
        import src.weixin_conversation.config as wx_config
        import src.weixin_conversation.registration as registration
        from src.desktop_automation.adapters import TrustedAdapterRegistry
        from src.session_tasks import scenario_hooks

        monkeypatch.setattr(registration, "get_session_tasks_config", lambda: replace(_cfg(), enabled=False))
        monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: True)
        assert registration.ensure_registered() is False
        assert TrustedAdapterRegistry.get("weixin.conversation.v1") is None
        assert scenario_hooks.get_hooks("weixin.conversation.v1") is None
        assert scenario_descriptor.get_descriptor("weixin.conversation.v1") is None

    def test_scenario_gate_closed_registers_nothing(self, monkeypatch):
        """weixin_conversation 门控关闭：返回 False 且三处注册表零写入。"""
        import src.weixin_conversation.config as wx_config
        import src.weixin_conversation.registration as registration
        from src.desktop_automation.adapters import TrustedAdapterRegistry
        from src.session_tasks import scenario_hooks

        monkeypatch.setattr(registration, "get_session_tasks_config", lambda: _cfg(enabled=True))
        monkeypatch.setattr(wx_config, "scenario_enabled_gate", lambda: False)
        assert registration.ensure_registered() is False
        assert TrustedAdapterRegistry.get("weixin.conversation.v1") is None
        assert scenario_hooks.get_hooks("weixin.conversation.v1") is None
        assert scenario_descriptor.get_descriptor("weixin.conversation.v1") is None


# ---------------------------------------------------------------------------
# _WeixinBindingResolver 行为锁定：查询语义与 service.py/workbench.py 现查一致
# （B1.2 切换调用点前的行为基线；真实 DB）
# ---------------------------------------------------------------------------


class TestWeixinBindingResolverBehavior:
    def _resolver(self):
        from src.weixin_conversation.descriptor import _WeixinBindingResolver

        return _WeixinBindingResolver()

    def _insert_name_context_binding(self, cursor, tenant_id, device_id, expires_at):
        """构造名称定位上下文绑定行（语义同 name_contexts.from_resolution 落库）。"""
        from src.weixin_conversation.name_contexts import RESOLVER

        binding_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO bs_weixin_conversation_bindings
            (id, tenant_id, user_id, device_id, account_binding_id, conversation_type,
             conversation_label, identity_version, verification_status, verifier_version, expires_at)
            VALUES (%s, %s, 'user-1', %s, %s, 'direct', '名称上下文', 1, 'resolved', %s, %s)
            """,
            (binding_id, tenant_id, str(device_id), str(uuid.uuid4()), RESOLVER, expires_at),
        )
        return binding_id

    def test_get_binding_by_id_roundtrip_and_missing(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        resolver = self._resolver()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            row = resolver.get_binding_by_id(cursor, tenant_id, verified_binding["conversation_binding_id"])
            assert row is not None
            assert str(row["device_id"]) == str(device_row["id"])
            assert row["verification_status"] == "verified"
            # 列集覆盖 service 两处现查 SELECT 的并集
            for column in ("id", "tenant_id", "user_id", "account_binding_id",
                           "identity_version", "verified_at", "verifier_version", "expires_at"):
                assert column in row
            assert resolver.get_binding_by_id(cursor, tenant_id, str(uuid.uuid4())) is None

    def test_get_runtime_identity_fields_and_missing(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        resolver = self._resolver()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            row = resolver.get_runtime_identity(cursor, tenant_id, verified_binding["conversation_binding_id"])
            assert row is not None
            assert row["conversation_label"] == "测试会话"
            assert int(row["identity_version"]) >= 1
            assert row["verifier_version"] == "test-fixture"
            # 无营销账号行（LEFT JOIN 未命中）：account_version 为 NULL（claim 身份段现状）
            assert row["account_version"] is None
            assert resolver.get_runtime_identity(cursor, tenant_id, str(uuid.uuid4())) is None

    def test_is_valid_for_allocation_verified_and_expired(self, tenant_id, device_row, verified_binding):
        from src.db.database import get_db_connection

        resolver = self._resolver()
        binding_id = verified_binding["conversation_binding_id"]
        with get_db_connection() as conn:
            cursor = conn.cursor()
            assert resolver.is_valid_for_allocation(conn, tenant_id, binding_id) is True
            # 过期（expires_at 非空但 ≤ now）：不视为有效
            cursor.execute(
                "UPDATE bs_weixin_conversation_bindings SET expires_at=CURRENT_TIMESTAMP - INTERVAL '1 hour' WHERE tenant_id=%s AND id=%s",
                (tenant_id, binding_id),
            )
            conn.commit()
            assert resolver.is_valid_for_allocation(conn, tenant_id, binding_id) is False
            # 未验证 pending：无效
            cursor.execute(
                "UPDATE bs_weixin_conversation_bindings SET verification_status='pending', expires_at=CURRENT_TIMESTAMP + INTERVAL '1 day' WHERE tenant_id=%s AND id=%s",
                (tenant_id, binding_id),
            )
            conn.commit()
            assert resolver.is_valid_for_allocation(conn, tenant_id, binding_id) is False

    def test_is_valid_for_allocation_name_context_branch(self, tenant_id, device_row):
        """名称定位上下文（verifier_version=current-login-name-v1 + resolved）：
        未过期有效、过期无效（与 service._binding_valid_for_allocation 同口径）。"""
        from src.db.database import get_db_connection

        resolver = self._resolver()
        with get_db_connection() as conn:
            cursor = conn.cursor()
            binding_id = self._insert_name_context_binding(
                cursor, tenant_id, device_row["id"], datetime.now(timezone.utc) + timedelta(hours=1)
            )
            conn.commit()
            assert resolver.is_valid_for_allocation(conn, tenant_id, binding_id) is True
            cursor.execute(
                "UPDATE bs_weixin_conversation_bindings SET expires_at=CURRENT_TIMESTAMP - INTERVAL '1 minute' WHERE tenant_id=%s AND id=%s",
                (tenant_id, binding_id),
            )
            conn.commit()
            assert resolver.is_valid_for_allocation(conn, tenant_id, binding_id) is False

    def test_resolve_workbench_label_and_missing(self, tenant_id, device_row):
        from src.db.database import get_db_connection
        from src.weixin_conversation.bindings import create_binding
        from src.weixin_conversation.descriptor import resolve_workbench_label

        binding = create_binding(
            tenant_id, "user-1", str(device_row["id"]), str(uuid.uuid4()), "group", label="工作台标签"
        )
        with get_db_connection() as conn:
            cursor = conn.cursor()
            assert resolve_workbench_label(cursor, tenant_id, binding["conversation_binding_id"]) == "工作台标签"
            assert resolve_workbench_label(cursor, tenant_id, str(uuid.uuid4())) is None
