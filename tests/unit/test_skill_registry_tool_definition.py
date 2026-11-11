"""
SkillRegistry.use_skill 工具定义测试

验证工具 description 不含技能名单（名单只保留系统提示一处），
以及空注册表 fallback 语义不变。

置于 unit 层：integration 层在宿主机模式因 DB 守卫整模块 skip，
本文件用例不依赖 DB，需真实通过 pytest 执行。
"""

import pytest

pytestmark = pytest.mark.skills

from pathlib import Path

from src.core.skill_registry import SkillRegistry


class TestSkillToolDefinitionUnit:
    """use_skill 工具定义单元测试"""

    def test_tool_definition_excludes_skill_catalog(self):
        """工具定义 description 不含技能名单，只保留使用规则"""
        fixtures_skills = Path(__file__).parent.parent / "fixtures" / "skills"
        registry = SkillRegistry()
        loaded = registry.load_from_directory(fixtures_skills)
        assert loaded >= 1

        defn = registry.get_skill_tool_definition()
        # fixtures 下的技能名逐一断言不出现在 description（名单只保留系统提示一处）
        for skill_name in registry.list_skills():
            assert skill_name not in defn["description"]
        assert "test-skill" not in defn["description"]
        assert "minimal-skill" not in defn["description"]
        # 使用流程关键词仍在
        assert "操作指南" in defn["description"]
        assert "use_skill" in defn["description"]

    def test_tool_definition_empty_registry_fallback(self):
        """空注册表时 fallback 定义仍返回 use_skill 与暂无可用技能"""
        registry = SkillRegistry()

        defn = registry.get_skill_tool_definition()
        assert defn["name"] == "use_skill"
        assert "暂无可用技能" in defn["description"]
