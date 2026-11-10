"""Runner ProfileCatalog 租户实例解析回归单测（全 mock，不依赖 DB/registry）

背景（2026-11-10 trace tr_runner_dcf4acda 事故）：resolve 曾先 registry.get
再在「查不到时」才走租户合并，DB 定义父智能体（如 sales-assistant）常驻
registry 导致实例行 LLM 覆盖永远不生效。回归点：父定义存在时租户合并仍优先。
"""

import pytest
from unittest.mock import MagicMock, patch

from src.services.agent_runner.profiles import ProfileCatalog
from src.models.subagent import SubagentConfig


def _base_config(**kw):
    defaults = dict(
        name="销售助手", dir_name="sales-assistant", description="基础描述",
        version="1.0.0", author="system", system_prompt="父提示词",
        llm_provider="deepseek", llm_model_codes={"deepseek": "deepseek-flash"},
        from_db=True,
    )
    defaults.update(kw)
    return SubagentConfig(**defaults)


def _instance_row(agent_id="sales-assistant", base_agent_id="sales-assistant", **kw):
    row = {
        "tenant_id": "t1", "agent_id": agent_id, "base_agent_id": base_agent_id,
        "display_name": "租户销售助手", "description": "租户描述",
        "extra_prompt": None, "greeting_summary": None, "greeting_prompts": None,
        "status": "active",
    }
    row.update(kw)
    return row


_LLM_OVERRIDE = {"provider": "qwen", "model_codes": {"qwen": "qwen3.8-flash"}}


@pytest.fixture
def catalog():
    cat = ProfileCatalog()
    reg = MagicMock()
    reg.get.return_value = _base_config()
    cat.registry = MagicMock(return_value=reg)
    return cat


class TestProfileCatalogTenantResolve:
    def test_override_applied_when_parent_in_registry(self, catalog):
        """父定义命中 registry 时，实例行 llm 覆盖仍生效（事故回归点）"""
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=_instance_row(llm_provider=_LLM_OVERRIDE)), \
             patch("src.saas.db.tenant_db.TenantDB.is_llm_edit_allowed", return_value=True), \
             patch("src.subagents.factory.AgentFactory.get_runtime_config",
                   return_value=_base_config()):
            config, fingerprint = catalog.resolve("sales-assistant", tenant_id="t1")
        assert config.llm_provider == "qwen"
        assert config.llm_model_codes == {"qwen": "qwen3.8-flash"}
        assert config.tenant_instance is True
        assert config.dir_name == "sales-assistant"

    def test_no_instance_row_falls_back_to_registry(self, catalog):
        """无实例行时回退 registry.get（普通 DB 定义/内置 agent 行为不变）"""
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=None):
            config, fingerprint = catalog.resolve("sales-assistant", tenant_id="t1")
        assert config.llm_provider == "deepseek"
        assert config.tenant_instance is not True

    def test_switch_off_keeps_parent_llm(self, catalog):
        """开关关闭时覆盖不生效，保留父定义 llm（fail-closed）"""
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=_instance_row(llm_provider=_LLM_OVERRIDE)), \
             patch("src.saas.db.tenant_db.TenantDB.is_llm_edit_allowed", return_value=False), \
             patch("src.subagents.factory.AgentFactory.get_runtime_config",
                   return_value=_base_config()):
            config, fingerprint = catalog.resolve("sales-assistant", tenant_id="t1")
        assert config.llm_provider == "deepseek"
        assert config.tenant_instance is True

    def test_no_tenant_context_skips_instance_lookup(self, catalog):
        """无租户上下文不查实例，直接 registry.get"""
        config, fingerprint = catalog.resolve("sales-assistant", tenant_id=None)
        assert config.llm_provider == "deepseek"
        catalog.registry.return_value.get.assert_called_once_with("sales-assistant")

    def test_copy_instance_resolves_by_instance_id(self, catalog):
        """副本实例按实例 agent_id 解析，dir_name 为实例 id"""
        with patch("src.db.tenant_agent_instance_db.TenantAgentInstanceDB.get",
                   return_value=_instance_row(agent_id="sales-assistant-t1-ab12")), \
             patch("src.subagents.factory.AgentFactory.get_runtime_config",
                   return_value=_base_config()):
            config, fingerprint = catalog.resolve("sales-assistant-t1-ab12", tenant_id="t1")
        assert config.dir_name == "sales-assistant-t1-ab12"
        assert config.tenant_instance is True

    def test_unknown_profile_still_404(self, catalog):
        """无租户且 registry 未命中保持原 404 口径"""
        catalog.registry.return_value.get.return_value = None
        with pytest.raises(Exception):
            catalog.resolve("nonexistent", tenant_id=None)
