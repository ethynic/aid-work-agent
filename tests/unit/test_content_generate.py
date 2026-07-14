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
            assert "严格遵循用户的指令" not in prompt, \
                f"content_type={content_type} 没有匹配到特定提示词，落入了通用 fallback"

    def test_existing_content_types_still_work(self):
        tool = ContentGenerateTool()
        existing_types = ["customer_list", "email", "market_report", ""]
        for content_type in existing_types:
            prompt = tool._get_system_prompt("zh", content_type)
            assert len(prompt) > 0

    def test_custom_content_type_falls_back(self):
        tool = ContentGenerateTool()
        prompt = tool._get_system_prompt("zh", "custom_unknown_type")
        # 增强版通用 fallback 应包含关键指令
        assert "严格遵循用户的指令" in prompt
        assert "请使用简体中文回复。" in prompt

    def test_tool_description_mentions_universal(self):
        tool = ContentGenerateTool()
        assert "通用" in tool.description
        assert "任意类型" in tool.description

    def test_input_model_prompt_description_is_detailed(self):
        schema = ContentGenerateTool.InputModel.model_json_schema()
        prompt_desc = schema["properties"]["prompt"]["description"]
        assert "角色定义" in prompt_desc
        assert "输出格式" in prompt_desc
        assert "输入素材" in prompt_desc


class TestSkillExecuteTool:
    """验证 skill_execute 工具定义正确"""

    def test_skill_execute_command_not_required(self):
        from unittest.mock import MagicMock
        from src.tools.skill.skill_execute_tool import SkillExecuteTool

        tool = SkillExecuteTool(skill_executor=MagicMock(), skill_registry=MagicMock())
        defn = tool.to_tool_definition()
        required = defn["input_schema"].get("required", [])
        assert "skill" in required
        assert "command" not in required
        assert "command" in defn["input_schema"]["properties"]
