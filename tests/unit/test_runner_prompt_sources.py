"""PromptSources._resolve_db_subagent_prompt 单元测试

重点覆盖副本实例（tenant_agent_instances 副本行）的 prompt 模板与 sections
回退父定义解析：副本行不落 prompt_versions / subagent_prompt_sections，
直接按实例 id 解析会拿到未渲染占位符的原始模板（agent2 线上事故）。
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.models.subagent import SubagentConfig
from src.services.agent_runner.runtime.prompt_sources import PromptSources

TEMPLATE = "### 角色说明\n{{role}}\n### 工作方式\n{{workflow}}"


def _make_config(dir_name: str, base_agent_id=None, system_prompt: str = "") -> SubagentConfig:
    return SubagentConfig(
        name="数据分析",
        dir_name=dir_name,
        description="数据分析",
        system_prompt=system_prompt,
        from_db=True,
        base_agent_id=base_agent_id,
        tenant_instance=base_agent_id is not None,
    )


def _make_sources(config: SubagentConfig) -> PromptSources:
    return PromptSources(
        mode=SimpleNamespace(value="subagent"), subagent_config=config, tenant_id="t1",
        skill_registry=None, style_manager=None, prompt_manager=None, tool_controls=None,
        subagent_registry=None, tools=lambda: [], available_subagents=[],
        usage_guides=lambda: "",
    )


def _patch_db(template_by_id, sections_by_id):
    """打桩 prompt_resolver.resolve 与 sections DB/缓存"""
    def fake_resolve(self, scope, scope_id, tenant_id=None):
        return template_by_id.get(scope_id)

    def fake_get_cached(*_prefix, **_kw):
        return None

    def fake_set_cached(*_prefix, value=None, **_kw):
        return None

    from src.db.subagent_prompt_section_db import SubagentPromptSectionDB

    def fake_sections_map(agent_id):
        return sections_by_id.get(agent_id, {})

    ctx = dict(
        template_by_id=template_by_id, sections_by_id=sections_by_id,
    )
    return (
        patch("src.prompts.prompt_resolver.PromptResolver.resolve", autospec=True, side_effect=lambda self, scope, scope_id, tenant_id=None: fake_resolve(self, scope, scope_id, tenant_id)),
        patch("src.core.cache_utils.get_cached", side_effect=fake_get_cached),
        patch("src.core.cache_utils.set_cached", side_effect=fake_set_cached),
        patch.object(SubagentPromptSectionDB, "get_sections_map", staticmethod(fake_sections_map)),
    )


class TestResolveDbSubagentPrompt:
    def test_copy_instance_falls_back_to_base_template_and_sections(self):
        """副本实例：模板与 sections 均按父定义解析并渲染"""
        copy_cfg = _make_config("data-analysis-tenant-53cfb-yreb", base_agent_id="data-analysis")
        patches = _patch_db(
            template_by_id={"data-analysis": TEMPLATE},
            sections_by_id={"data-analysis": {"role": "数据分析员", "workflow": "先查表再分析"}},
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = _make_sources(copy_cfg)._resolve_db_subagent_prompt()
        assert "{{role}}" not in result and "{{workflow}}" not in result
        assert "数据分析员" in result and "先查表再分析" in result

    def test_copy_instance_fallback_system_prompt_still_rendered(self):
        """副本实例：模板与 sections 全部缺失时，降级模板（原始占位符）也要渲染 sections"""
        copy_cfg = _make_config(
            "data-analysis-tenant-53cfb-yreb", base_agent_id="data-analysis",
            system_prompt=TEMPLATE,
        )
        patches = _patch_db(
            template_by_id={},
            sections_by_id={"data-analysis": {"role": "数据分析员", "workflow": "先查表再分析"}},
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = _make_sources(copy_cfg)._resolve_db_subagent_prompt()
        assert "{{role}}" not in result
        assert "数据分析员" in result

    def test_base_db_agent_unchanged(self):
        """非实例 DB 子智能体：按自身 agent_id 解析，行为不回归"""
        cfg = _make_config("data-analysis", system_prompt=TEMPLATE)
        patches = _patch_db(
            template_by_id={"data-analysis": TEMPLATE},
            sections_by_id={"data-analysis": {"role": "基础角色", "workflow": "基础流程"}},
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = _make_sources(cfg)._resolve_db_subagent_prompt()
        assert "基础角色" in result and "基础流程" in result

    def test_direct_instance_resolves_single_id(self):
        """直用实例行（agent_id == base_agent_id）：单 id 解析，不重复回退"""
        cfg = _make_config("data-analysis", base_agent_id="data-analysis")
        patches = _patch_db(
            template_by_id={"data-analysis": TEMPLATE},
            sections_by_id={"data-analysis": {"role": "直用角色", "workflow": "直用流程"}},
        )
        with patches[0], patches[1], patches[2], patches[3]:
            result = _make_sources(cfg)._resolve_db_subagent_prompt()
        assert "直用角色" in result

    def test_copy_instance_no_template_no_sections_keeps_raw(self):
        """副本实例：父定义也无模板无 sections（如父为内置员工）→ 原样保留"""
        cfg = _make_config(
            "builtin-tenant-53cfb-yreb", base_agent_id="builtin",
            system_prompt="内置员工提示词",
        )
        patches = _patch_db(template_by_id={}, sections_by_id={})
        with patches[0], patches[1], patches[2], patches[3]:
            result = _make_sources(cfg)._resolve_db_subagent_prompt()
        assert result == "内置员工提示词"
