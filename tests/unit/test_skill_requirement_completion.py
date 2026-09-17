"""
技能依赖自动补全机制单元测试

覆盖：
1. SkillLoader 解析 SKILL.md frontmatter 的 requires_tools / requires_recap
2. SubagentDefinitionService.apply_skill_requirements 的补全逻辑：
   - 工具补全（additional / allowed 两种键名、缺省 tools、inherit 语义保持）
   - recap 任务补全（缺失补全、同名不覆盖 when/enabled、空 recap 兜底）
   - 幂等（重复执行无新增）
   - 无依赖技能 / 无技能时原样返回
"""

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skills


# ============== SkillLoader 解析 ==============

class TestSkillLoaderRequires:
    """SKILL.md frontmatter requires_tools / requires_recap 解析"""

    def test_parse_requires_fields(self, tmp_path):
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "skill_loader_req",
            str(Path(__file__).parent.parent.parent / "src" / "core" / "skill_loader.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        SkillLoader = mod.SkillLoader

        skill_dir = tmp_path / "demo-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            """---
name: demo-skill
description: 演示技能
requires_tools: [http_api, record_lead_capture]
requires_recap:
  - name: external_push
    when: every_round
  - name: lead_refresh
    when: every_round
---
正文。
""",
            encoding="utf-8",
        )
        loader = SkillLoader(tmp_path)
        skill = loader.parse_skill_md(skill_dir / "SKILL.md")

        assert skill is not None
        assert skill.requires_tools == ["http_api", "record_lead_capture"]
        assert skill.requires_recap == [
            {"name": "external_push", "when": "every_round"},
            {"name": "lead_refresh", "when": "every_round"},
        ]
        assert skill.to_dict()["requires_tools"] == skill.requires_tools
        assert skill.to_dict()["requires_recap"] == skill.requires_recap

    def test_parse_requires_absent_defaults_empty(self, tmp_path):
        import importlib.util
        from pathlib import Path

        spec = importlib.util.spec_from_file_location(
            "skill_loader_req2",
            str(Path(__file__).parent.parent.parent / "src" / "core" / "skill_loader.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        SkillLoader = mod.SkillLoader

        skill_dir = tmp_path / "plain-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: plain-skill\ndescription: 无依赖技能\n---\n正文。\n",
            encoding="utf-8",
        )
        loader = SkillLoader(tmp_path)
        skill = loader.parse_skill_md(skill_dir / "SKILL.md")

        assert skill is not None
        assert skill.requires_tools == []
        assert skill.requires_recap == []


# ============== apply_skill_requirements ==============

def _make_skill(name, requires_tools=None, requires_recap=None):
    skill = MagicMock()
    skill.requires_tools = requires_tools or []
    skill.requires_recap = requires_recap or []
    return skill


class FakeRegistry:
    def __init__(self, skills):
        self._skills = skills

    def get_all_skill(self, name):
        return self._skills.get(name)


def _patch_registry(skills):
    fake_agent = MagicMock()
    fake_agent.skill_registry = FakeRegistry(skills)
    return patch("src.core.agent.get_master_agent", return_value=fake_agent)


class TestApplySkillRequirements:
    def _apply(self, tools, skills, recap, skill_map):
        from src.services.subagent_definition_service import SubagentDefinitionService
        with _patch_registry(skill_map):
            return SubagentDefinitionService.apply_skill_requirements(tools, skills, recap)

    def test_no_skills_returns_original(self):
        tools = {"inherit": False, "additional": ["a"]}
        recap = {"tasks": [{"name": "x", "when": "every_round", "enabled": False}]}
        new_tools, new_recap, applied = self._apply(tools, {"allowed": []}, recap, {})
        assert new_tools == tools
        assert new_recap == recap
        assert applied == []

    def test_skill_without_requirements_noop(self):
        tools = {"inherit": True, "additional": []}
        new_tools, new_recap, applied = self._apply(
            tools, {"allowed": ["plain"]}, None, {"plain": _make_skill("plain")},
        )
        assert new_tools == tools
        assert new_recap is None
        assert applied == []

    def test_missing_tools_and_recap_added(self):
        skill = _make_skill(
            "pre-sales-api",
            requires_tools=["http_api", "transfer_to_human"],
            requires_recap=[{"name": "external_push", "when": "every_round"}],
        )
        tools = {"inherit": False, "additional": ["web_search"]}
        new_tools, new_recap, applied = self._apply(
            tools, {"allowed": ["pre-sales-api"]}, None, {"pre-sales-api": skill},
        )
        assert new_tools["inherit"] is False
        assert new_tools["additional"] == ["web_search", "http_api", "transfer_to_human"]
        assert new_recap["tasks"] == [
            {"name": "external_push", "when": "every_round", "enabled": True},
        ]
        assert len(applied) == 3

    def test_tools_missing_defaults_inherit_true(self):
        # tools 缺省 -> 默认 inherit=true（inherit=false + 空列表会清空全部工具）
        skill = _make_skill("s1", requires_tools=["http_api"])
        new_tools, _, applied = self._apply(
            None, {"allowed": ["s1"]}, None, {"s1": skill},
        )
        assert new_tools["inherit"] is True
        assert new_tools["additional"] == ["http_api"]
        assert applied == ["工具: http_api"]

    def test_raw_allowed_key_supported(self):
        skill = _make_skill("s1", requires_tools=["http_api"])
        new_tools, _, _ = self._apply(
            {"inherit": False, "allowed": ["web_search"]},
            {"allowed": ["s1"]}, None, {"s1": skill},
        )
        assert new_tools["allowed"] == ["web_search", "http_api"]

    def test_recap_existing_task_not_overwritten(self):
        # 同名 recap 任务已存在：保留管理员的 when/enabled（尊重主动禁用）
        skill = _make_skill(
            "s1",
            requires_tools=["http_api"],
            requires_recap=[{"name": "external_push", "when": "every_round"}],
        )
        tools = {"inherit": False, "additional": ["http_api"]}
        recap = {"tasks": [{"name": "external_push", "when": "session_end", "enabled": False}]}
        _, new_recap, applied = self._apply(
            tools, {"allowed": ["s1"]}, recap, {"s1": skill},
        )
        assert new_recap["tasks"] == [
            {"name": "external_push", "when": "session_end", "enabled": False},
        ]
        assert applied == []

    def test_idempotent(self):
        skill = _make_skill(
            "s1",
            requires_tools=["http_api"],
            requires_recap=[{"name": "external_push", "when": "every_round"}],
        )
        tools = {"inherit": False, "additional": ["http_api"]}
        recap = {"tasks": [{"name": "external_push", "when": "every_round", "enabled": True}]}
        _, _, applied1 = self._apply(tools, {"allowed": ["s1"]}, recap, {"s1": skill})
        assert applied1 == []

    def test_multiple_skills_deduplicated(self):
        s1 = _make_skill("s1", requires_tools=["http_api"], requires_recap=[{"name": "external_push", "when": "every_round"}])
        s2 = _make_skill("s2", requires_tools=["http_api", "transfer_to_human"])
        new_tools, new_recap, _ = self._apply(
            {"inherit": False, "additional": []},
            {"allowed": ["s1", "s2"]}, None, {"s1": s1, "s2": s2},
        )
        assert new_tools["additional"] == ["http_api", "transfer_to_human"]
        assert new_recap["tasks"] == [{"name": "external_push", "when": "every_round", "enabled": True}]

    def test_empty_recap_dict_backfilled(self):
        # 前端保存时 recap 恒为 {tasks: []}，需能补全
        skill = _make_skill("s1", requires_recap=[{"name": "external_push", "when": "every_round"}])
        _, new_recap, applied = self._apply(
            {"inherit": True, "additional": []},
            {"allowed": ["s1"]}, {"tasks": []}, {"s1": skill},
        )
        assert new_recap["tasks"] == [{"name": "external_push", "when": "every_round", "enabled": True}]
        assert applied == ["recap: external_push(every_round)"]
