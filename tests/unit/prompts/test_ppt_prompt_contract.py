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
    assembly_source = (project_root / "src" / "tools" / "assembly.py").read_text(
        encoding="utf-8"
    )

    # 工具自动发现改造（docs/tools/tool-auto-discovery-design.md）后，
    # ppt_process 不再写死注册，由 discover_tool_classes() 经 assemble_agent_tools
    # 统一装配进各 Agent（黄金清单测试见 tests/unit/tools/test_tool_discovery.py）
    assert "discover_tool_classes()" in assembly_source
    assert "ppt_process" in discover_tool_classes()
