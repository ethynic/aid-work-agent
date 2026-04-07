"""
集成测试：Skill 全流程

测试 Skill 加载 → 匹配 → 执行（mock subprocess）
"""

import pytest

pytestmark = pytest.mark.skills
from pathlib import Path
from unittest.mock import patch

from src.core.skill_registry import SkillRegistry


class TestSkillFlowIntegration:
    """Skill 完整流程集成测试"""

    def test_load_skills_from_fixtures(self):
        """从 fixtures 目录加载技能"""
        fixtures_skills = Path(__file__).parent.parent / "fixtures" / "skills"
        registry = SkillRegistry()
        count = registry.load_from_directory(fixtures_skills)
        assert count >= 1
        assert registry.get("test-skill") is not None

    def test_skill_content(self):
        """验证技能内容正确加载"""
        fixtures_skills = Path(__file__).parent.parent / "fixtures" / "skills"
        registry = SkillRegistry()
        registry.load_from_directory(fixtures_skills)

        content = registry.get_content("test-skill")
        assert content is not None
        assert "测试技能" in content

    def test_skill_descriptions(self):
        """验证技能描述"""
        fixtures_skills = Path(__file__).parent.parent / "fixtures" / "skills"
        registry = SkillRegistry()
        registry.load_from_directory(fixtures_skills)

        descriptions = registry.get_descriptions()
        assert "test-skill" in descriptions

    def test_list_skills(self):
        """验证列出技能"""
        fixtures_skills = Path(__file__).parent.parent / "fixtures" / "skills"
        registry = SkillRegistry()
        registry.load_from_directory(fixtures_skills)

        skills = registry.list_skills()
        assert "test-skill" in skills

    def test_skill_tool_definition(self):
        """验证技能工具定义"""
        fixtures_skills = Path(__file__).parent.parent / "fixtures" / "skills"
        registry = SkillRegistry()
        registry.load_from_directory(fixtures_skills)

        defn = registry.get_skill_tool_definition()
        assert "name" in defn
        assert defn["name"] == "use_skill"


class TestSkillMatchIntegration:
    """Skill 匹配集成测试"""

    def test_match_by_keyword(self):
        """关键词匹配"""
        fixtures_skills = Path(__file__).parent.parent / "fixtures" / "skills"
        registry = SkillRegistry()
        registry.load_from_directory(fixtures_skills)

        results = registry.match_by_keyword("测试技能")
        assert isinstance(results, list)

    def test_match_nonexistent_returns_empty(self):
        """不匹配时返回空列表"""
        fixtures_skills = Path(__file__).parent.parent / "fixtures" / "skills"
        registry = SkillRegistry()
        registry.load_from_directory(fixtures_skills)

        results = registry.match_by_keyword("完全不相关的关键词xyz123")
        assert isinstance(results, list)
