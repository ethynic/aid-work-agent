"""
ContentGenerateTool 新增预设类型测试
"""

import pytest

pytestmark = pytest.mark.skills

from src.tools.llm.content_generate_tool import ContentGenerateTool


class TestContentGenerateNewTypes:
    """验证新增的 content_type 预设有对应提示词"""

    def test_new_content_types_in_guidance(self):
        tool = ContentGenerateTool()
        new_types = ["outline", "article", "report", "polish"]
        for content_type in new_types:
            prompt = tool._get_system_prompt("zh", content_type)
            assert len(prompt) > 0
            assert prompt != "你是一个专业的内容生成助手。请根据用户提供的提示词生成高质量的内容。", \
                f"content_type={content_type} 没有匹配到特定提示词"

    def test_existing_content_types_still_work(self):
        tool = ContentGenerateTool()
        existing_types = ["customer_list", "email", "market_report", ""]
        for content_type in existing_types:
            prompt = tool._get_system_prompt("zh", content_type)
            assert len(prompt) > 0

    def test_custom_content_type_falls_back(self):
        tool = ContentGenerateTool()
        prompt = tool._get_system_prompt("zh", "custom_unknown_type")
        assert prompt == "你是一个专业的内容生成助手。请根据用户提供的提示词生成高质量的内容。 请使用简体中文回复。"


class TestAgentSkillCompleteTool:
    """验证 skill_complete 工具定义正确"""

    def test_skill_complete_tool_definition(self):
        from src.tools.skill.skill_complete_tool import SkillCompleteTool

        tool = SkillCompleteTool()
        assert tool.name == "skill_complete"
        assert tool.display_name
        defn = tool.to_tool_definition()
        assert defn["name"] == "skill_complete"
        schema = defn["input_schema"]
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        assert "skill" in required
        assert "summary" in required

    def test_skill_execute_command_not_required(self):
        from unittest.mock import MagicMock
        from src.tools.skill.skill_execute_tool import SkillExecuteTool

        tool = SkillExecuteTool(skill_executor=MagicMock(), skill_registry=MagicMock())
        defn = tool.to_tool_definition()
        required = defn["input_schema"].get("required", [])
        assert "skill" in required
        assert "command" not in required
        assert "command" in defn["input_schema"]["properties"]
