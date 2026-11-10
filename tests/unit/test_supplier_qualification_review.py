"""supplier-qualification-review 技能测试（workbuddy 移植）：注册发现 + verify_uscc 端到端 + 无联网红线守护。

端到端直接调用 scripts/verify_uscc.py（纯本地算法，零第三方依赖），
覆盖合法/非法样例与 --json 结构化输出；并守护上游「技能不联网」红线。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

pytestmark = __import__("pytest").mark.skills

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = PROJECT_ROOT / "src" / "skills" / "supplier-qualification-review-1.1.0"
SCRIPTS = SKILL_DIR / "scripts"
SUBAGENT_MD = PROJECT_ROOT / "subagents" / "supplier-qualification-review" / "SUBAGENT.md"


def _run(args):
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PROJECT_ROOT": str(PROJECT_ROOT),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "HOME": "/tmp",
    }
    return subprocess.run(
        [sys.executable] + [str(a) for a in args],
        cwd=str(SKILL_DIR), env=env, capture_output=True, text=True, timeout=60,
    )


class TestSupplierQualificationReviewSkill(unittest.TestCase):
    def test_skill_discovered_by_registry(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        self.assertIn("supplier-qualification-review", registry.list_skills())
        skill = registry.get("supplier-qualification-review")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.version, "1.1.0")

    def test_skill_requires_tools_declared(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        skill = registry.get("supplier-qualification-review")
        for tool in ("image_read", "web_search", "web_fetch", "word_process", "cp"):
            self.assertIn(tool, getattr(skill, "requires_tools", []))

    def test_verify_uscc_self_test(self):
        proc = _run([SCRIPTS / "verify_uscc.py", "--self-test"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SELF-TEST OK", proc.stdout)

    def test_verify_uscc_valid_code_json(self):
        # 91110108MA01ABC23 + 算法校验位 9（self-test 已确认合法）
        proc = _run([SCRIPTS / "verify_uscc.py", "--json", "91110108MA01ABC239"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertTrue(result["valid"])
        self.assertTrue(result["length_ok"])
        self.assertTrue(result["charset_ok"])
        self.assertTrue(result["check_digit_ok"])
        self.assertEqual(result["reg_dept_desc"], "市场监管（工商）")
        self.assertEqual(result["org_category_desc"], "企业")

    def test_verify_uscc_invalid_check_digit(self):
        proc = _run([SCRIPTS / "verify_uscc.py", "--json", "91110000100000000X"])
        self.assertEqual(proc.returncode, 1)
        result = json.loads(proc.stdout)
        self.assertFalse(result["valid"])
        self.assertFalse(result["check_digit_ok"])

    def test_verify_uscc_bad_length_and_charset(self):
        proc = _run([SCRIPTS / "verify_uscc.py", "--json", "91110108MA01ABC23"])
        result = json.loads(proc.stdout)
        self.assertFalse(result["valid"])
        self.assertFalse(result["length_ok"])

        proc = _run([SCRIPTS / "verify_uscc.py", "--json", "91110108MA01ABC2OI"])
        result = json.loads(proc.stdout)
        self.assertFalse(result["valid"])
        self.assertFalse(result["charset_ok"])

    def test_script_has_no_network_dependency(self):
        """守护上游红线：技能脚本不得引入任何联网逻辑。"""
        text = (SCRIPTS / "verify_uscc.py").read_text(encoding="utf-8")
        for forbidden in ("requests", "urllib", "httpx", "socket", "aiohttp",
                          "urlopen", "WebSearch", "web_search", "fetch("):
            self.assertNotIn(forbidden, text)

    def test_subagent_shell_references_skill(self):
        """薄壳子智能体 frontmatter 顶格闭合且 skills.allowed 指向本技能（防 frontmatter 陷阱）。"""
        text = SUBAGENT_MD.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        import yaml

        frontmatter = text.split("---\n", 2)[1]
        meta = yaml.safe_load(frontmatter)
        self.assertEqual(meta["name"], "供应商资质审查")
        self.assertIn("supplier-qualification-review", meta["skills"]["allowed"])
        self.assertTrue(meta["tools"].get("inherit"))


if __name__ == "__main__":
    unittest.main()
