"""技能依赖自动补全（SubagentDefinitionService.apply_skill_requirements）
与 recap 配置保存校验（agent_definitions._validate_recap_config）单元测试"""

import pytest
from types import SimpleNamespace
from fastapi import HTTPException

pytestmark = pytest.mark.unit


class TestValidateRecapConfig:
    def _call(self, recap):
        from src.api.agent_definitions import _validate_recap_config
        _validate_recap_config(recap)

    def test_none_empty_pass(self):
        self._call(None)
        self._call({})
        self._call({"tasks": []})

    def test_valid_task_passes(self):
        self._call({"tasks": [{"name": "external_push", "when": "every_round", "enabled": True}]})

    def test_unknown_name_rejected(self):
        with pytest.raises(HTTPException) as ei:
            self._call({"tasks": [{"name": "no_such_task"}]})
        assert ei.value.status_code == 400
        assert "no_such_task" in ei.value.detail

    def test_invalid_when_rejected(self):
        with pytest.raises(HTTPException) as ei:
            self._call({"tasks": [{"name": "external_push", "when": "daily"}]})
        assert ei.value.status_code == 400
        assert "daily" in ei.value.detail

    def test_missing_name_rejected(self):
        with pytest.raises(HTTPException) as ei:
            self._call({"tasks": [{"when": "every_round"}]})
        assert ei.value.status_code == 400

    def test_tasks_not_list_rejected(self):
        with pytest.raises(HTTPException) as ei:
            self._call({"tasks": "external_push"})
        assert ei.value.status_code == 400


class FakeRegistry:
    def __init__(self, skill):
        self._skill = skill

    def get_all_skill(self, name):
        return self._skill if name == "pre-sales-api" else None


class FakeAgent:
    def __init__(self, skill):
        self.skill_registry = FakeRegistry(skill)


def _fake_skill():
    return SimpleNamespace(
        requires_tools=["http_api"],
        requires_recap=[{"name": "external_push", "when": "every_round"}],
    )


class TestApplySkillRequirements:
    def _call(self, monkeypatch, tools, skills, recap):
        import src.core.agent as core_agent
        monkeypatch.setattr(core_agent, "get_master_agent", lambda: FakeAgent(_fake_skill()))
        from src.services.subagent_definition_service import SubagentDefinitionService
        return SubagentDefinitionService.apply_skill_requirements(tools, skills, recap)

    def test_auto_complete_adds_missing(self, monkeypatch):
        tools, recap, applied = self._call(
            monkeypatch,
            {"inherit": True, "additional": []},
            {"allowed": ["pre-sales-api"]},
            {"tasks": []},
        )
        assert tools["additional"] == ["http_api"]
        assert recap["tasks"] == [{"name": "external_push", "when": "every_round", "enabled": True}]
        assert any("http_api" in s for s in applied)
        assert any("external_push" in s for s in applied)

    def test_existing_recap_not_overridden(self, monkeypatch):
        _, recap, applied = self._call(
            monkeypatch,
            {"inherit": True, "additional": ["http_api"]},
            {"allowed": ["pre-sales-api"]},
            {"tasks": [{"name": "external_push", "when": "every_round", "enabled": False}]},
        )
        # 同名任务已存在：保留管理员设置的 when/enabled，不覆盖
        assert recap["tasks"] == [{"name": "external_push", "when": "every_round", "enabled": False}]
        assert applied == []

    def test_idempotent_second_call_no_change(self, monkeypatch):
        tools1, recap1, _ = self._call(
            monkeypatch,
            None,
            {"allowed": ["pre-sales-api"]},
            None,
        )
        tools2, recap2, applied2 = self._call(monkeypatch, tools1, {"allowed": ["pre-sales-api"]}, recap1)
        assert applied2 == []
        assert tools2 == tools1
        assert recap2 == recap1

    def test_empty_skills_returns_unchanged(self, monkeypatch):
        tools, recap, applied = self._call(
            monkeypatch,
            {"inherit": True, "additional": []},
            {"allowed": []},
            {"tasks": []},
        )
        assert applied == []
        assert tools == {"inherit": True, "additional": []}
        assert recap == {"tasks": []}
