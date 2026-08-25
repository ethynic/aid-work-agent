"""#66 普通工具执行链删除型优化的静态与结果形状回归测试。"""

import ast
from pathlib import Path
import re

import pytest

from src.core.agent_events import extract_downloadable_file


REPO_ROOT = Path(__file__).resolve().parents[2]
ORDINARY_TOOL_NAMES = {
    "create_scheduled_task",
    "manage_scheduled_task",
    "content_generate",
    "web_search",
    "email_process",
    "read",
    "browser_automation",
}


def _compared_string_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for candidate in [node.left, *node.comparators]:
            if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
                values.add(candidate.value)
            elif isinstance(candidate, (ast.Tuple, ast.List, ast.Set)):
                values.update(
                    item.value
                    for item in candidate.elts
                    if isinstance(item, ast.Constant) and isinstance(item.value, str)
                )
    # match tool_name: case "..." 与 if 比较具有相同的执行分支语义。
    for node in ast.walk(tree):
        if isinstance(node, ast.MatchValue):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                values.add(value.value)
        elif isinstance(node, ast.MatchOr):
            values.update(
                pattern.value.value
                for pattern in node.patterns
                if isinstance(pattern, ast.MatchValue)
                and isinstance(pattern.value, ast.Constant)
                and isinstance(pattern.value.value, str)
            )
    return values


@pytest.mark.parametrize("relative_path", ["src/core/agent.py", "src/tools/executor.py"])
def test_ordinary_tool_names_are_not_compared_in_execution_core(relative_path):
    compared = _compared_string_literals(REPO_ROOT / relative_path)
    assert compared.isdisjoint(ORDINARY_TOOL_NAMES)


def test_frontend_does_not_branch_on_ordinary_tool_names():
    source = (REPO_ROOT / "frontend/web/composables/useAgent.ts").read_text(
        encoding="utf-8"
    )
    for name in ORDINARY_TOOL_NAMES:
        # 检查精确字符串字面量，普通英文标识符/方法名（尤其 read）不会误报。
        literal_pattern = rf"['\"]{re.escape(name)}['\"]"
        assert not re.search(literal_pattern, source)
        comparison_patterns = (
            rf"===\s*['\"]{re.escape(name)}['\"]",
            rf"!==\s*['\"]{re.escape(name)}['\"]",
            rf"case\s+['\"]{re.escape(name)}['\"]",
        )
        assert not any(re.search(pattern, source) for pattern in comparison_patterns)
    assert "downloadToolNames" not in source
    assert "boss_jobs_list" not in source
    assert "extractQuickOptions(result)" in source


def test_download_file_is_recognized_from_result_shape_for_any_tool():
    event = {
        "type": "tool_result",
        "toolName": "future_export_tool",
        "success": True,
        "result": {
            "success": True,
            "file_id": "file-1",
            "file_name": "report.pdf",
            "file_size": 42,
        },
    }
    assert extract_downloadable_file(event) == {
        "file_id": "file-1",
        "file_name": "report.pdf",
        "file_size": 42,
        "download_url": "",
        "mime_type": "",
    }


@pytest.mark.parametrize(
    "event",
    [
        {"type": "tool_result", "success": False, "result": {"file_id": "x"}},
        {"type": "tool_result", "success": True, "result": {"file_id": "x", "visible": False}},
        {"type": "tool_result", "success": True, "result": {"message": "done"}},
        {"type": "tool_result", "success": True, "result": "not-a-dict"},
    ],
)
def test_download_file_shape_rejects_non_download_results(event):
    assert extract_downloadable_file(event) is None
