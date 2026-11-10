"""租户数字员工实例解析器单测（全 mock，不依赖 DB/registry）"""

import pytest
from unittest.mock import MagicMock, patch

from src.subagents.tenant_resolver import resolve_tenant_config
from src.models.subagent import SubagentConfig


def _base_config(**kw):
    defaults = dict(
        name="销售助手", dir_name="sales", description="基础描述",
        version="1.0.0", author="system", system_prompt="父提示词",
        from_db=False,
    )
    defaults.update(kw)
    return SubagentConfig(**defaults)


def _instance_row(agent_id="sales-t1-ab12", base_agent_id="sales", **kw):
    row = {
        "tenant_id": "t1", "agent_id": agent_id, "base_agent_id": base_agent_id,
        "display_name": "南京销售助手", "description": "租户描述",
        "extra_prompt": None, "greeting_summary": None, "greeting_prompts": None,
        "status": "active",
    }
    row.update(kw)
    return row


@pytest.fixture
def registry():
    reg = MagicMock()
    return reg


class TestResolveTenantConfig:
    def test_no_tenant_context_returns_none(self, registry):
        assert resolve_tenant_config(registry, None, "sales") is None
        assert resolve_tenant_config(registry, "", "sales") is None

    def test_no_instance_row_returns_none(self, registry):
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get", return_value=None):
            assert resolve_tenant_config(registry, "t1", "sales") is None

    def test_copy_merges_overrides(self, registry):
        base = _base_config()
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=_instance_row()), \
             patch("src.subagents.factory.AgentFactory.get_runtime_config", return_value=base):
            merged = resolve_tenant_config(registry, "t1", "sales-t1-ab12")
        assert merged is not None
        assert merged.name == "南京销售助手"
        assert merged.description == "租户描述"
        assert merged.dir_name == "sales-t1-ab12"
        assert merged.tenant_instance is True
        assert merged.base_agent_id == "sales"
        assert merged.from_db is True
        # system_prompt/工具继承父定义
        assert merged.system_prompt == "父提示词"

    def test_direct_customization_row_keeps_dir_name(self, registry):
        base = _base_config()
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=_instance_row(agent_id="sales", base_agent_id="sales")), \
             patch("src.subagents.factory.AgentFactory.get_runtime_config", return_value=base):
            merged = resolve_tenant_config(registry, "t1", "sales")
        assert merged.dir_name == "sales"
        assert merged.name == "南京销售助手"

    def test_missing_overrides_fall_back_to_base(self, registry):
        base = _base_config()
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=_instance_row(display_name=None, description=None)), \
             patch("src.subagents.factory.AgentFactory.get_runtime_config", return_value=base):
            merged = resolve_tenant_config(registry, "t1", "sales-t1-ab12")
        assert merged.name == "销售助手"
        assert merged.description == "基础描述"

    def test_archived_row_strips_overrides(self, registry):
        """archived 降级：覆盖项剥离返回父定义，保历史会话可用"""
        base = _base_config()
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=_instance_row(status="archived")), \
             patch("src.subagents.factory.AgentFactory.get_runtime_config", return_value=base):
            merged = resolve_tenant_config(registry, "t1", "sales-t1-ab12")
        assert merged.name == "销售助手"
        assert merged.description == "基础描述"
        assert merged.tenant_instance is True

    def test_missing_base_definition_returns_none(self, registry):
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=_instance_row()), \
             patch("src.subagents.factory.AgentFactory.get_runtime_config", return_value=None):
            assert resolve_tenant_config(registry, "t1", "sales-t1-ab12") is None

    def test_db_error_returns_none(self, registry):
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   side_effect=RuntimeError("db down")):
            assert resolve_tenant_config(registry, "t1", "sales-t1-ab12") is None


class TestResolveTenantLlmOverride:
    """租户 LLM 覆盖合并：开关开且实例行有配置才生效，否则保留父定义值（行数据保留）"""

    def _resolve(self, registry, base, row, allowed):
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=row), \
             patch("src.subagents.factory.AgentFactory.get_runtime_config", return_value=base), \
             patch("src.saas.db.tenant_db.TenantDB.is_llm_edit_allowed", return_value=allowed):
            return resolve_tenant_config(registry, "t1", row["agent_id"])

    def test_allowed_and_configured_overrides_base(self, registry):
        base = _base_config(llm_provider="qwen", llm_model_codes={"qwen": "qwen-base"})
        row = _instance_row(llm_provider={"provider": "deepseek", "model_codes": {"deepseek": "deepseek-v3"}})
        merged = self._resolve(registry, base, row, allowed=True)
        assert merged.llm_provider == "deepseek"
        assert merged.llm_model_codes == {"deepseek": "deepseek-v3"}

    def test_switch_off_falls_back_to_base_data_kept(self, registry):
        """开关关：回退父定义；实例行数据保留（重开开关即恢复）"""
        base = _base_config(llm_provider="qwen", llm_model_codes={"qwen": "qwen-base"})
        row = _instance_row(llm_provider={"provider": "deepseek", "model_codes": {}})
        merged = self._resolve(registry, base, row, allowed=False)
        assert merged.llm_provider == "qwen"
        assert merged.llm_model_codes == {"qwen": "qwen-base"}

    def test_switch_on_then_off_reverts(self, registry):
        base = _base_config(llm_provider=None, llm_model_codes=None)
        row = _instance_row(llm_provider={"provider": "zhipu", "model_codes": {"zhipu": "GLM-5"}})
        on = self._resolve(registry, base, row, allowed=True)
        off = self._resolve(registry, base, row, allowed=False)
        assert on.llm_provider == "zhipu"
        assert off.llm_provider is None
        assert off.llm_model_codes is None

    def test_no_llm_data_keeps_base_without_checking_switch(self, registry):
        base = _base_config(llm_provider="qwen", llm_model_codes=None)
        row = _instance_row(llm_provider=None)
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=row), \
             patch("src.subagents.factory.AgentFactory.get_runtime_config", return_value=base), \
             patch("src.saas.db.tenant_db.TenantDB.is_llm_edit_allowed",
                   side_effect=AssertionError("不应查询开关")):
            merged = resolve_tenant_config(registry, "t1", row["agent_id"])
        assert merged.llm_provider == "qwen"

    def test_archived_row_ignores_llm_override(self, registry):
        base = _base_config(llm_provider=None, llm_model_codes=None)
        row = _instance_row(status="archived",
                            llm_provider={"provider": "deepseek", "model_codes": {}})
        merged = self._resolve(registry, base, row, allowed=True)
        assert merged.llm_provider is None
        assert merged.llm_model_codes is None

    def test_switch_check_error_fails_closed(self, registry):
        base = _base_config(llm_provider=None, llm_model_codes=None)
        row = _instance_row(llm_provider={"provider": "deepseek", "model_codes": {}})
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=row), \
             patch("src.subagents.factory.AgentFactory.get_runtime_config", return_value=base), \
             patch("src.saas.db.tenant_db.TenantDB.is_llm_edit_allowed",
                   side_effect=RuntimeError("db down")):
            merged = resolve_tenant_config(registry, "t1", row["agent_id"])
        assert merged.llm_provider is None
        assert merged.llm_model_codes is None
