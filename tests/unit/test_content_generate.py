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

    def test_input_model_prompt_description_is_field_only(self):
        """spec §1.2：Field description 只描述字段本身，不承载教程；
        prompt 的 5 要素写法要点由工具 description 唯一通道承载（不重复）"""
        schema = ContentGenerateTool.InputModel.model_json_schema()
        prompt_desc = schema["properties"]["prompt"]["description"]
        # 字段含义仍在
        assert "内容生成指令" in prompt_desc
        # 教程化 5 要素不在 Field description 中（已收敛到 description）
        for teaching_marker in ("角色定义", "输出格式", "输入素材", "写得越详细"):
            assert teaching_marker not in prompt_desc
        # 工具 description 承载 5 要素要点（唯一通道，零重复）
        assert "角色、任务、素材、格式、质量要求" in ContentGenerateTool.description


class TestContentGenerateErrorReturns:
    """spec §1.4：错误返回仅 success/error，无输入回显（language/content_type）"""

    @pytest.mark.asyncio
    async def test_llm_exception_returns_only_success_error(self):
        from unittest.mock import AsyncMock

        tool = ContentGenerateTool()
        tool.llm = AsyncMock()
        tool.llm.chat.side_effect = RuntimeError("上游服务异常")

        result = await tool.execute(
            prompt="生成一段内容", language="en", content_type="email"
        )
        assert result["success"] is False
        assert set(result.keys()) == {"success", "error"}
        assert "language" not in result
        assert "content_type" not in result

    @pytest.mark.asyncio
    async def test_empty_content_returns_only_success_error(self):
        from unittest.mock import AsyncMock

        tool = ContentGenerateTool()
        tool.llm = AsyncMock()
        tool.llm.chat.return_value = {"content": ""}  # 思考模型 content: null 场景

        result = await tool.execute(prompt="生成一段内容", language="zh")
        assert result["success"] is False
        assert set(result.keys()) == {"success", "error"}

    @pytest.mark.asyncio
    async def test_success_returns_no_language_echo(self):
        """spec §1.5：成功分支保留 content_type（任务范围仅移除 language），
        language 不得回显进返回值"""
        from unittest.mock import AsyncMock

        tool = ContentGenerateTool()
        tool.llm = AsyncMock()
        tool.llm.chat.return_value = {"content": "生成的正文内容"}

        result = await tool.execute(
            prompt="生成一段内容", language="ru", content_type="report"
        )
        assert result["success"] is True
        assert result["content"] == "生成的正文内容"
        assert result["content_type"] == "report"
        assert "language" not in result


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
