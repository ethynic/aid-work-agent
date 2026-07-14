"""
Skill 系统优化 - 单元测试

测试覆盖：
1. _handle_skill_execute command 可选化
2. SkillLoader AgentSkills 兼容字段解析
3. ShortTermMemory to_llm_messages Skill 摘要支持
4. content_generate 新增预设类型
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from collections import deque
from datetime import datetime

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestSkillLoaderCompatibility(unittest.TestCase):
    """SkillLoader AgentSkills 兼容字段解析测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill_md(self, content: str, filename: str = "SKILL.md") -> Path:
        """创建临时 SKILL.md 文件"""
        skill_dir = Path(self.temp_dir) / "test-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / filename
        skill_md.write_text(content, encoding="utf-8")
        return skill_md

    def test_parse_standard_fields(self):
        """标准字段解析"""
        from src.core.skill_loader import SkillLoader

        content = """---
name: test-skill
description: 测试技能
version: 2.0.0
author: tester
---
# 测试技能
这是正文。
"""
        path = self._create_skill_md(content)
        loader = SkillLoader(Path(self.temp_dir))
        skill = loader.parse_skill_md(path)

        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "test-skill")
        self.assertEqual(skill.description, "测试技能")
        self.assertEqual(skill.version, "2.0.0")
        self.assertEqual(skill.author, "tester")
        self.assertEqual(skill.body.strip(), "# 测试技能\n这是正文。")

    def test_parse_agent_skills_fields(self):
        """AgentSkills 兼容字段解析"""
        from src.core.skill_loader import SkillLoader

        content = """---
name: pdf-processing
description: 处理PDF文件。
license: Apache-2.0
compatibility: 需要 Python 3.10+
metadata:
  author: example-org
  version: "1.0"
  custom_field: custom_value
allowed-tools: Read Bash Grep
user-invocable: false
disable-model-invocation: true
---
# PDF处理
正文内容。
"""
        path = self._create_skill_md(content)
        loader = SkillLoader(Path(self.temp_dir))
        skill = loader.parse_skill_md(path)

        self.assertIsNotNone(skill)
        self.assertEqual(skill.license, "Apache-2.0")
        self.assertEqual(skill.compatibility, "需要 Python 3.10+")
        self.assertEqual(skill.metadata["author"], "example-org")
        self.assertEqual(skill.metadata["version"], "1.0")
        self.assertEqual(skill.metadata["custom_field"], "custom_value")
        self.assertEqual(skill.allowed_tools, ["Read", "Bash", "Grep"])
        self.assertFalse(skill.user_invocable)
        self.assertTrue(skill.disable_model_invocation)

    def test_parse_metadata_triggers(self):
        """metadata.triggers 触发器解析"""
        from src.core.skill_loader import SkillLoader

        content = """---
name: test-skill
description: 测试技能
metadata:
  triggers:
    - .pdf
    - pdf
---
# 测试
正文。
"""
        path = self._create_skill_md(content)
        loader = SkillLoader(Path(self.temp_dir))
        skill = loader.parse_skill_md(path)

        self.assertIsNotNone(skill)
        self.assertEqual(len(skill.triggers), 2)
        self.assertEqual(skill.triggers[0].type, "file_extension")
        self.assertEqual(skill.triggers[0].pattern, ".pdf")
        self.assertEqual(skill.triggers[1].type, "keyword")
        self.assertEqual(skill.triggers[1].pattern, "pdf")

    def test_parse_top_level_triggers_backward_compat(self):
        """顶层 triggers 向后兼容"""
        from src.core.skill_loader import SkillLoader

        content = """---
name: test-skill
description: 测试技能
triggers:
  - .pdf
  - pdf
  - ^test.*
---
# 测试
正文。
"""
        path = self._create_skill_md(content)
        loader = SkillLoader(Path(self.temp_dir))
        skill = loader.parse_skill_md(path)

        self.assertIsNotNone(skill)
        self.assertEqual(len(skill.triggers), 3)
        self.assertEqual(skill.triggers[0].type, "file_extension")
        self.assertEqual(skill.triggers[1].type, "keyword")
        self.assertEqual(skill.triggers[2].type, "regex")

    def test_parse_metadata_and_top_level_triggers_merged(self):
        """metadata.triggers 和顶层 triggers 合并（去重）"""
        from src.core.skill_loader import SkillLoader

        content = """---
name: test-skill
description: 测试技能
triggers:
  - .pdf
  - pdf
metadata:
  triggers:
    - pdf
    - document
---
# 测试
正文。
"""
        path = self._create_skill_md(content)
        loader = SkillLoader(Path(self.temp_dir))
        skill = loader.parse_skill_md(path)

        self.assertIsNotNone(skill)
        # pdf 应该去重，metadata 优先所以 pdf 在前
        # 总共 3 个唯一触发器
        patterns = [t.pattern for t in skill.triggers]
        self.assertIn(".pdf", patterns)
        self.assertIn("pdf", patterns)
        self.assertIn("document", patterns)
        self.assertEqual(len(patterns), 3)

    def test_parse_minimal_skill(self):
        """最小化 Skill（仅 name + description）"""
        from src.core.skill_loader import SkillLoader

        content = """---
name: minimal
description: 最小技能
---
正文。
"""
        path = self._create_skill_md(content)
        loader = SkillLoader(Path(self.temp_dir))
        skill = loader.parse_skill_md(path)

        self.assertIsNotNone(skill)
        self.assertEqual(skill.license, None)
        self.assertEqual(skill.compatibility, None)
        self.assertEqual(skill.metadata, None)
        self.assertEqual(skill.allowed_tools, None)
        self.assertTrue(skill.user_invocable)
        self.assertFalse(skill.disable_model_invocation)


class TestShortTermMemorySkillSummary(unittest.TestCase):
    """ShortTermMemory to_llm_messages Skill 摘要支持测试"""

    def test_skill_summary_included_as_user_message(self):
        """Skill 摘要消息应作为 user 角色消息保留"""
        from src.memory.short_term import ShortTermMemory

        memory = ShortTermMemory()
        session_id = "test-session"

        memory.add(session_id, "user", "你好")
        memory.add_message(session_id, {
            "role": "system",
            "content": "[技能执行记录] 使用技能「article-writing」完成任务。结果：已生成文章",
            "_skill_summary": True,
        })

        messages = memory.to_llm_messages(session_id)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "你好")
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("技能执行记录", messages[1]["content"])

    def test_normal_system_message_excluded(self):
        """普通 system 消息应被过滤（不是 Skill 摘要的）"""
        from src.memory.short_term import ShortTermMemory

        memory = ShortTermMemory()
        session_id = "test-session"

        memory.add(session_id, "user", "你好")
        memory.add_message(session_id, {
            "role": "system",
            "content": "你是一个助手",
        })

        messages = memory.to_llm_messages(session_id)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["content"], "你好")


class TestContentGenerateNewTypes(unittest.TestCase):
    """content_generate 新增预设类型测试"""

    def test_new_content_types_in_guidance(self):
        """验证新增的 content_type 预设有对应提示词"""
        from src.tools.llm.content_generate_tool import ContentGenerateTool

        tool = ContentGenerateTool()

        # 测试所有新增类型都有对应的 system prompt
        new_types = ["outline", "article", "report", "polish"]
        for content_type in new_types:
            prompt = tool._get_system_prompt("zh", content_type)
            self.assertTrue(len(prompt) > 0)
            self.assertNotEqual(
                prompt,
                "你是一个专业的内容生成助手。请根据用户提供的提示词生成高质量的内容。",
                f"content_type={content_type} 没有匹配到特定提示词"
            )

    def test_existing_content_types_still_work(self):
        """验证原有 content_type 仍然正常"""
        from src.tools.llm.content_generate_tool import ContentGenerateTool

        tool = ContentGenerateTool()

        existing_types = ["customer_list", "email", "market_report", ""]
        for content_type in existing_types:
            prompt = tool._get_system_prompt("zh", content_type)
            self.assertTrue(len(prompt) > 0)

    def test_custom_content_type_falls_back(self):
        """自定义 content_type 回退到默认提示词"""
        from src.tools.llm.content_generate_tool import ContentGenerateTool

        tool = ContentGenerateTool()
        prompt = tool._get_system_prompt("zh", "custom_unknown_type")

        self.assertEqual(
            prompt,
            "你是一个专业的内容生成助手。请根据用户提供的提示词生成高质量的内容。 请使用简体中文回复。"
        )


class TestSkillExecuteTool(unittest.TestCase):
    """验证 skill_execute 工具定义正确"""

    def test_skill_execute_command_not_required(self):
        """skill_execute 的 command 不再是 required"""
        from unittest.mock import MagicMock
        from src.tools.skill.skill_execute_tool import SkillExecuteTool

        tool = SkillExecuteTool(skill_executor=MagicMock(), skill_registry=MagicMock())
        defn = tool.to_tool_definition()
        required = defn["input_schema"].get("required", [])
        self.assertIn("skill", required)
        self.assertNotIn("command", required)
        # command 应该仍然在 properties 中
        self.assertIn("command", defn["input_schema"]["properties"])


if __name__ == "__main__":
    unittest.main()
