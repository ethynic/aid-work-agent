"""skill_execute 脚本路径解析加固单测（2026-10-27 trace 实证三类缺陷）。

根因背景：`_process_command` 原用裸 str.replace 替换 `scripts/<name>`：
- 命令写绝对路径 /app/src/skills/<skill>/scripts/xxx.py 时被二次拼接为
  /app/src/skills/<skill>//app/src/skills/<skill>/scripts/xxx.py；
- 命令写裸文件名（python lead_db.py init）完全不替换，落到临时会话 cwd
  报 No such file。
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.skills

from src.core.skill_executor import SkillExecutionContext, SkillExecutor


@pytest.fixture
def executor(tmp_path):
    registry = MagicMock()
    skill = MagicMock()
    skill.scripts = [tmp_path / "src" / "skills" / "demo-1.0.0" / "scripts" / "lead_db.py"]
    registry.get.return_value = skill
    return SkillExecutor(registry, workspace=tmp_path / "ws")


def _ctx(tmp_path):
    return SkillExecutionContext(skill_name="demo", workdir=tmp_path / "work")


class TestScriptPathResolution:
    def test_relative_scripts_path_replaced(self, executor, tmp_path):
        cmd = executor._process_command("python scripts/lead_db.py init", _ctx(tmp_path))
        assert cmd.endswith("/scripts/lead_db.py init")
        assert cmd.count("lead_db.py") == 1

    def test_dot_slash_scripts_path_replaced(self, executor, tmp_path):
        cmd = executor._process_command("python ./scripts/lead_db.py init", _ctx(tmp_path))
        assert cmd.endswith("/scripts/lead_db.py init")
        assert cmd.count("lead_db.py") == 1

    def test_absolute_path_not_double_prefixed(self, executor, tmp_path):
        """绝对路径中的 scripts/xxx 不得被再拼一次技能目录（双前缀 bug）。"""
        abs_path = (tmp_path / "src" / "skills" / "demo-1.0.0" / "scripts" / "lead_db.py").as_posix()
        cmd = executor._process_command(f"python {abs_path} init", _ctx(tmp_path))
        assert cmd == f"python {abs_path} init"

    def test_bare_filename_resolved_to_script_path(self, executor, tmp_path):
        """裸文件名应解析到技能 scripts 目录，而非临时会话 cwd。"""
        cmd = executor._process_command('python lead_db.py init --name "x"', _ctx(tmp_path))
        assert "/scripts/lead_db.py init" in cmd
        assert cmd.count("lead_db.py") == 1

    def test_bare_filename_inside_other_token_not_replaced(self, executor, tmp_path):
        """路径上下文中的同名文件（--body-file /tmp/other_lead_db.py）不动。"""
        cmd = executor._process_command(
            "python lead_db.py add --file /tmp/other_lead_db.py", _ctx(tmp_path)
        )
        assert "/scripts/lead_db.py add" in cmd
        assert "/tmp/other_lead_db.py" in cmd

    def test_no_skill_scripts_untouched(self, tmp_path):
        registry = MagicMock()
        registry.get.return_value = None
        executor = SkillExecutor(registry, workspace=tmp_path / "ws")
        cmd = executor._process_command("python other.py run", _ctx(tmp_path))
        assert cmd == "python other.py run"
