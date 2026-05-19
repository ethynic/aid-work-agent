"""
竞品研究子智能体和技能加载测试

验证 competitor-research 子智能体的 SUBAGENT.md 解析、
competitor-research 技能的 SKILL.md 解析。
"""

import re

import pytest

pytestmark = pytest.mark.skills

import importlib.util
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# 直接加载 loader 模块，避免触发 src.core.__init__ 中的 master_agent 单例
# ---------------------------------------------------------------------------

_project_root = Path(__file__).parent.parent.parent


def _load_module_from_file(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_loader_mod = _load_module_from_file(
    "subagent_loader", _project_root / "src" / "subagents" / "loader.py"
)
SubagentLoader = _loader_mod.SubagentLoader

_skill_loader_mod = _load_module_from_file(
    "skill_loader", _project_root / "src" / "core" / "skill_loader.py"
)
SkillLoader = _skill_loader_mod.SkillLoader


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

SUBAGENT_MD_PATH = _project_root / "subagents" / "competitor-research" / "SUBAGENT.md"
SKILL_MD_PATH = (
    _project_root / "src" / "skills" / "competitor-research-1.0.0" / "SKILL.md"
)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_frontmatter_and_body(path: Path):
    """读取文件并解析为 (frontmatter_dict, body_str)"""
    content = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(content)
    assert match, f"Failed to parse frontmatter from {path}"
    frontmatter_str, body = match.groups()
    frontmatter = yaml.safe_load(frontmatter_str)
    return frontmatter, body


# ===================================================================
# Subagent Loading Tests
# ===================================================================


class TestSubagentLoading:
    """验证 competitor-research SUBAGENT.md 的存在和解析"""

    def test_subagent_md_exists(self):
        """SUBAGENT.md 文件存在于预期路径"""
        assert SUBAGENT_MD_PATH.exists(), (
            f"SUBAGENT.md not found at {SUBAGENT_MD_PATH}"
        )
        assert SUBAGENT_MD_PATH.is_file()

    def test_subagent_md_parseable(self):
        """SubagentLoader 能正确解析 YAML frontmatter + body"""
        loader = SubagentLoader()
        config = loader.parse_subagent_md(SUBAGENT_MD_PATH)

        assert config is not None, "parse_subagent_md returned None"
        assert config.name, "Parsed config has empty name"

    def test_subagent_name(self):
        """子智能体名称为 '竞品研究专家'"""
        frontmatter, _ = _parse_frontmatter_and_body(SUBAGENT_MD_PATH)
        assert frontmatter["name"] == "竞品研究专家"

    def test_subagent_capabilities(self):
        """capabilities 包含预期的 4 项能力"""
        frontmatter, _ = _parse_frontmatter_and_body(SUBAGENT_MD_PATH)
        expected = [
            "deep_web_research",
            "competitor_analysis",
            "report_generation",
            "html_report",
        ]
        capabilities = frontmatter.get("capabilities", [])
        for cap in expected:
            assert cap in capabilities, f"Missing capability: {cap}"

    def test_subagent_tools_inherit(self):
        """tools.inherit 为 True"""
        frontmatter, _ = _parse_frontmatter_and_body(SUBAGENT_MD_PATH)
        tools = frontmatter.get("tools", {})
        assert tools.get("inherit") is True

    def test_subagent_skills_allowed(self):
        """skills.allowed 包含 'baidu-search'"""
        frontmatter, _ = _parse_frontmatter_and_body(SUBAGENT_MD_PATH)
        skills = frontmatter.get("skills", {})
        allowed = skills.get("allowed", [])
        assert "baidu-search" in allowed, (
            f"'baidu-search' not in skills.allowed: {allowed}"
        )

    def test_subagent_triggers(self):
        """triggers.keywords 包含预期关键词"""
        frontmatter, _ = _parse_frontmatter_and_body(SUBAGENT_MD_PATH)
        triggers = frontmatter.get("triggers", {})
        keywords = triggers.get("keywords", [])

        expected_keywords = ["竞品", "竞品分析", "竞品研究", "竞品报告", "竞争对手", "对手分析"]
        for kw in expected_keywords:
            assert kw in keywords, f"Missing trigger keyword: {kw}"

    def test_subagent_has_system_prompt(self):
        """body（system prompt）包含关键工具指令"""
        _, body = _parse_frontmatter_and_body(SUBAGENT_MD_PATH)

        key_instructions = ["file_write", "html_report_merger", "generate_prompt"]
        for key in key_instructions:
            assert key in body, (
                f"System prompt body missing key instruction: '{key}'"
            )

    def test_subagent_no_yaml_system_prompt(self):
        """system_prompt 不在 YAML frontmatter 中（应在 body 中）"""
        frontmatter, _ = _parse_frontmatter_and_body(SUBAGENT_MD_PATH)
        assert "system_prompt" not in frontmatter, (
            "system_prompt should NOT be in YAML frontmatter; "
            "it should be in the Markdown body after the closing ---"
        )

    def test_subagent_config_via_loader(self):
        """通过 SubagentLoader 加载后，SubagentConfig 字段正确"""
        loader = SubagentLoader()
        config = loader.parse_subagent_md(SUBAGENT_MD_PATH)

        assert config is not None
        assert config.name == "竞品研究专家"
        assert config.version == "1.0.0"
        assert config.tools.get("inherit") is True
        assert "baidu-search" in config.get_allowed_skills()
        assert len(config.capabilities) == 4
        assert config.system_prompt  # body should be used as system_prompt
        assert "file_write" in config.system_prompt

    def test_subagent_context_limits(self):
        """context 中包含 max_input_tokens 和 max_output_tokens"""
        frontmatter, _ = _parse_frontmatter_and_body(SUBAGENT_MD_PATH)
        context = frontmatter.get("context", {})
        assert "max_input_tokens" in context
        assert "max_output_tokens" in context
        assert context["max_input_tokens"] == 12000
        assert context["max_output_tokens"] == 6000

    def test_subagent_load_all_from_dir(self):
        """SubagentLoader.load_all() 能从 subagents 目录加载到竞品研究智能体"""
        subagents_dir = _project_root / "subagents"
        if not subagents_dir.exists():
            pytest.skip("subagents directory not found")

        loader = SubagentLoader(subagents_dir)
        config = loader.get("竞品研究专家")
        assert config is not None, (
            "SubagentLoader did not load '竞品研究专家' from subagents dir"
        )
        assert config.dir_name == "competitor-research"


# ===================================================================
# Skill Loading Tests
# ===================================================================


class TestSkillLoading:
    """验证 competitor-research SKILL.md 的存在和解析"""

    def test_skill_md_exists(self):
        """SKILL.md 文件存在于预期路径"""
        assert SKILL_MD_PATH.exists(), f"SKILL.md not found at {SKILL_MD_PATH}"
        assert SKILL_MD_PATH.is_file()

    def test_skill_md_parseable(self):
        """SkillLoader 能正确解析 SKILL.md"""
        loader = SkillLoader(_project_root / "src" / "skills")
        skill = loader.parse_skill_md(SKILL_MD_PATH)

        assert skill is not None, "parse_skill_md returned None"

    def test_skill_name(self):
        """技能名称为 'competitor-research'"""
        loader = SkillLoader(_project_root / "src" / "skills")
        skill = loader.parse_skill_md(SKILL_MD_PATH)

        assert skill is not None
        assert skill.name == "competitor-research"

    def test_skill_contains_templates(self):
        """技能 body 包含 HTML 模板和搜索策略模板"""
        loader = SkillLoader(_project_root / "src" / "skills")
        skill = loader.parse_skill_md(SKILL_MD_PATH)

        assert skill is not None
        body = skill.body

        # HTML template markers
        assert "<!DOCTYPE html>" in body, "Missing HTML template (DOCTYPE)"
        assert "cover-page" in body, "Missing cover page template"
        assert "swot-grid" in body, "Missing SWOT grid template"
        assert "info-card" in body, "Missing info-card template"

        # Search strategy markers
        assert "搜索策略" in body or "搜索关键词" in body, (
            "Missing search strategy templates"
        )

    def test_skill_description(self):
        """技能描述包含关键词"""
        loader = SkillLoader(_project_root / "src" / "skills")
        skill = loader.parse_skill_md(SKILL_MD_PATH)

        assert skill is not None
        assert "竞品研究" in skill.description

    def test_skill_contains_merge_instructions(self):
        """技能 body 包含报告合并说明"""
        loader = SkillLoader(_project_root / "src" / "skills")
        skill = loader.parse_skill_md(SKILL_MD_PATH)

        assert skill is not None
        body = skill.body

        assert "html_report_merger" in body, "Missing html_report_merger instruction"
        assert "full_report.html" in body, "Missing full_report.html reference"

    def test_skill_contains_css_design_system(self):
        """技能 body 包含 CSS 设计系统"""
        loader = SkillLoader(_project_root / "src" / "skills")
        skill = loader.parse_skill_md(SKILL_MD_PATH)

        assert skill is not None
        body = skill.body

        assert "--primary:" in body, "Missing CSS variable --primary"
        assert "--accent-green:" in body, "Missing CSS variable --accent-green"
        assert ":root" in body, "Missing :root CSS selector"

    def test_skill_load_all_from_dir(self):
        """SkillLoader.load_skills() 能从 skills 目录加载到 competitor-research"""
        loader = SkillLoader(_project_root / "src" / "skills")
        skill = loader.get_skill("competitor-research")

        assert skill is not None, (
            "SkillLoader did not load 'competitor-research' from skills dir"
        )
        assert skill.name == "competitor-research"

    def test_skill_generate_prompt_templates(self):
        """技能 body 包含材料文件的 generate_prompt 模板"""
        loader = SkillLoader(_project_root / "src" / "skills")
        skill = loader.parse_skill_md(SKILL_MD_PATH)

        assert skill is not None
        body = skill.body

        # Verify generate_prompt templates for material files exist
        assert "01_基础信息" in body, "Missing 01_基础信息 template"
        assert "02_产品分析" in body, "Missing 02_产品分析 template"
        assert "03_市场口碑" in body, "Missing 03_市场口碑 template"
        assert "04_竞争态势" in body, "Missing 04_竞争态势 template"
        assert "05_最新动态" in body, "Missing 05_最新动态 template"
