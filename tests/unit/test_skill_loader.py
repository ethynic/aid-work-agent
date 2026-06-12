"""
SkillLoader 解析测试

测试 SKILL.md frontmatter + body 解析、AgentSkills 兼容字段、触发器解析
"""

import pytest

pytestmark = pytest.mark.skills

import importlib.util
import tempfile
import unittest
from pathlib import Path

# 直接加载 skill_loader 模块，避免触发 src.core.__init__ 中的 master_agent 单例
_spec = importlib.util.spec_from_file_location(
    "skill_loader",
    str(Path(__file__).parent.parent.parent / "src" / "core" / "skill_loader.py"),
)
_loader_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_loader_mod)
SkillLoader = _loader_mod.SkillLoader


class TestSkillLoaderStandard(unittest.TestCase):
    """标准字段解析"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill_md(self, content: str, filename: str = "SKILL.md") -> Path:
        skill_dir = Path(self.temp_dir) / "test-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / filename
        skill_md.write_text(content, encoding="utf-8")
        return skill_md

    def test_parse_standard_fields(self):
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

        assert skill is not None
        assert skill.name == "test-skill"
        assert skill.description == "测试技能"
        assert skill.version == "2.0.0"
        assert skill.author == "tester"
        assert skill.body.strip() == "# 测试技能\n这是正文。"

    def test_parse_agent_skills_fields(self):
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

        assert skill is not None
        assert skill.license == "Apache-2.0"
        assert skill.compatibility == "需要 Python 3.10+"
        assert skill.metadata["author"] == "example-org"
        assert skill.metadata["version"] == "1.0"
        assert skill.metadata["custom_field"] == "custom_value"
        assert skill.allowed_tools == ["Read", "Bash", "Grep"]
        assert skill.user_invocable is False
        assert skill.disable_model_invocation is True

    def test_parse_minimal_skill(self):
        content = """---
name: minimal
description: 最小技能
---
正文。
"""
        path = self._create_skill_md(content)
        loader = SkillLoader(Path(self.temp_dir))
        skill = loader.parse_skill_md(path)

        assert skill is not None
        assert skill.license is None
        assert skill.compatibility is None
        assert skill.metadata is None
        assert skill.allowed_tools is None
        assert skill.user_invocable is True
        assert skill.disable_model_invocation is False


class TestSkillLoaderTriggers(unittest.TestCase):
    """触发器解析"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill_md(self, content: str) -> Path:
        skill_dir = Path(self.temp_dir) / "test-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(content, encoding="utf-8")
        return skill_md

    def test_parse_metadata_triggers(self):
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

        assert skill is not None
        assert len(skill.triggers) == 2
        assert skill.triggers[0].type == "file_extension"
        assert skill.triggers[0].pattern == ".pdf"
        assert skill.triggers[1].type == "keyword"
        assert skill.triggers[1].pattern == "pdf"

    def test_parse_top_level_triggers_backward_compat(self):
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

        assert skill is not None
        assert len(skill.triggers) == 3
        assert skill.triggers[0].type == "file_extension"
        assert skill.triggers[1].type == "keyword"
        assert skill.triggers[2].type == "regex"

    def test_parse_merged_triggers_dedup(self):
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

        assert skill is not None
        patterns = [t.pattern for t in skill.triggers]
        assert ".pdf" in patterns
        assert "pdf" in patterns
        assert "document" in patterns
        assert len(patterns) == 3
