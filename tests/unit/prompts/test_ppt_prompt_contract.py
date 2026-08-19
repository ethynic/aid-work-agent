"""Prompt contracts for direct and delegated PPT generation."""

from pathlib import Path

import pytest

from src.prompts.manager import PromptManager
from src.tools.file.cp_tool import CpInput
from src.tools.ppt.ppt_process_tool import PptProcessInput
from src.tools.registry import discover_tool_classes


@pytest.fixture
def prompt_manager():
    return PromptManager()


@pytest.mark.parametrize("template_name", ["master_agent.md", "subagent_base.md"])
def test_agent_prompts_require_structured_ppt_arguments(prompt_manager, template_name):
    prompt = prompt_manager.load_template(template_name)

    assert "### PPT 工具调用" in prompt
    assert "`instruction`" in prompt
    assert "`content`" in prompt
    assert 'content_type="html"' in prompt
    assert all(mode in prompt for mode in ("high_fidelity", "editable", "both"))
    assert "不要把完整指令和正文混入" in prompt


@pytest.mark.parametrize("template_name", ["master_agent.md", "subagent_base.md"])
def test_agent_prompts_require_workspace_copy_and_truthful_delivery(
    prompt_manager, template_name
):
    prompt = prompt_manager.load_template(template_name)

    assert "当前 workspace" in prompt
    assert "register_download=false" in prompt
    assert "visible=false" in prompt
    assert 'file_path="workspace/' in prompt
    assert "cp 返回的新路径" in prompt
    assert "`file_paths`" in prompt
    assert "`success=true`" in prompt or "失败或未返回 `file_path`" in prompt
    assert "不得声称" in prompt
    assert "分别调用 `cp`" in prompt
    assert "`display_name`" in prompt


def test_guizang_skill_has_direct_html_to_pptx_path():
    skill_path = (
        Path(__file__).resolve().parents[3]
        / "src"
        / "skills"
        / "guizang-ppt-skill"
        / "SKILL.md"
    )
    skill = skill_path.read_text(encoding="utf-8")

    assert "### Step 7 · 按需导出 PPTX" in skill
    assert "ppt_process(" in skill
    assert 'content_type="html"' in skill
    assert 'export_mode="both"' in skill
    assert "alternate_file_path" in skill
    assert "display_name" in skill
    assert "不得声称 PPTX 已生成" in skill


def test_documented_calls_match_public_tool_schemas():
    ppt = PptProcessInput.model_validate(
        {
            "instruction": "将网页演示导出为 PPTX",
            "content_type": "html",
            "file_paths": ["storage/workspace/index.html"],
            "export_mode": "both",
            "output_name": "季度复盘",
        }
    )
    cp = CpInput.model_validate(
        {
            "source_file_path": "C:/temp/index.html",
            "file_path": "workspace/index.html",
            "register_download": False,
            "visible": False,
        }
    )

    assert ppt.export_mode == "both"
    assert ppt.content_type == "html"
    assert cp.file_path == "workspace/index.html"
    assert cp.register_download is False


def test_master_and_subagent_have_direct_ppt_tool_contracts():
    project_root = Path(__file__).resolve().parents[3]
    agent_source = (project_root / "src" / "core" / "agent.py").read_text(
        encoding="utf-8"
    )
    config = (project_root / "configs" / "config.yaml").read_text(encoding="utf-8")

    # 工具自动发现改造（docs/tools/tool-auto-discovery-design.md）后，
    # ppt_process 不再在 agent.py 写死注册，改由 discover_tool_classes() 自动注册
    # （黄金清单测试见 tests/unit/tools/test_tool_discovery.py）
    assert "for cls in discover_tool_classes().values():" in agent_source
    assert "ppt_process" in discover_tool_classes()
    assert "guizang-ppt-skill" in config
