"""web-search-pro 技能移植单测：注册发现、脚本与文档完整性、命令路径替换。"""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skills

SKILL_DIR = Path(__file__).resolve().parents[2] / "src" / "skills" / "web-search-pro-1.0.0"


def _load_registry():
    from src.core.skill_registry import SkillRegistry

    registry = SkillRegistry()
    registry.load_from_directory(SKILL_DIR.parent)
    return registry


class TestWebSearchProSkill:
    def test_skill_registered(self):
        registry = _load_registry()
        skill = registry.get("web-search-pro")
        assert skill is not None
        assert skill.version == "1.0.0"

    def test_skill_in_master_content_readable(self):
        registry = _load_registry()
        content = registry.get_content("web-search-pro")
        assert content is not None
        assert "pipeline.py" in content
        assert "skill_execute" in content

    def test_scripts_present(self):
        for name in ("web_search.py", "pipeline.py", "render.py"):
            assert (SKILL_DIR / "scripts" / name).is_file()
        assert (SKILL_DIR / "references" / "retrieval-notes.md").is_file()

    def test_process_command_rewrites_script_path(self):
        """skill_execute 的 _process_command 契约：scripts/<name> 替换为技能脚本绝对路径。"""
        import tempfile

        from src.core.skill_executor import SkillExecutionContext, SkillExecutor

        registry = _load_registry()
        executor = SkillExecutor(registry, workspace=Path(tempfile.mkdtemp()))
        context = SkillExecutionContext(
            skill_name="web-search-pro",
            workdir=executor._create_workdir("web-search-pro", "test_session"),
        )
        command = "python scripts/web_search.py search '测试' --json"
        processed = executor._process_command(command, context)
        expected = (SKILL_DIR / "scripts" / "web_search.py").resolve().as_posix()
        assert processed == f"python {expected} search '测试' --json"

    def test_web_search_help_runs_offline(self):
        result = subprocess.run(
            [sys.executable, str(SKILL_DIR / "scripts" / "web_search.py"), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "search" in result.stdout

    def test_pipeline_help_runs_offline(self):
        result = subprocess.run(
            [sys.executable, str(SKILL_DIR / "scripts" / "pipeline.py"), "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0

    def test_mask_merge_algorithm(self):
        sys.path.insert(0, str(SKILL_DIR / "scripts"))
        try:
            import pipeline
            pages = [
                {"url": "a", "masked_phones": [{"raw": "1391680****", "pattern": "1391680****"}]},
                {"url": "b", "masked_phones": [{"raw": "139****7580", "pattern": "139****7580"}]},
                {"url": "c", "masked_phones": [{"raw": "139******80", "pattern": "139******80"}]},
            ]
            merged = pipeline.merge_masked_phones(pages)
            assert merged and merged[0]["number"] == "13916807580"
            # 位冲突判不同号码，不误合并
            conflict = pipeline.merge_masked_phones([
                {"url": "a", "masked_phones": [{"raw": "1391680****", "pattern": "1391680****"}]},
                {"url": "b", "masked_phones": [{"raw": "138****7580", "pattern": "138****7580"}]},
            ])
            assert not conflict
            # 信息不足不下结论
            insufficient = pipeline.merge_masked_phones([
                {"url": "a", "masked_phones": [{"raw": "132******53", "pattern": "132******53"}]},
            ])
            assert not insufficient
        finally:
            sys.path.pop(0)
