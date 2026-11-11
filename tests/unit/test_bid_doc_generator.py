"""bid-doc-generator 技能测试（workbuddy 移植）：注册发现 + check_inputs + price_quote
+ render_docs 冒烟 + 薄壳子智能体解析。

端到端直接调用 scripts/ 下脚本（顶层仅标准库，零第三方依赖）；
render_docs 依赖 python-docx，本地环境缺失时 importorskip 跳过。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.skills

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = PROJECT_ROOT / "src" / "skills" / "bid-doc-generator-1.0.0"
SCRIPTS = SKILL_DIR / "scripts"
SUBAGENT_MD = PROJECT_ROOT / "subagents" / "ai-bid-assistant" / "SUBAGENT.md"


def _run(args, cwd=None):
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PROJECT_ROOT": str(PROJECT_ROOT),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "HOME": "/tmp",
    }
    return subprocess.run(
        [sys.executable] + [str(a) for a in args],
        cwd=str(cwd or SKILL_DIR), env=env, capture_output=True, text=True, timeout=60,
    )


class TestBidDocGeneratorSkillRegistry(unittest.TestCase):
    def test_skill_discovered_by_registry(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        self.assertIn("bid-doc-generator", registry.list_skills())
        skill = registry.get("bid-doc-generator")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.version, "1.0.2")

    def test_skill_requires_tools_declared(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        skill = registry.get("bid-doc-generator")
        for tool in ("read", "cp", "image_read"):
            self.assertIn(tool, getattr(skill, "requires_tools", []))


class TestCheckInputs(unittest.TestCase):
    def test_all_inputs_ready(self):
        with tempfile.TemporaryDirectory() as td:
            files = []
            for i in range(4):
                p = Path(td) / f"input_{i}.txt"
                p.write_text("内容", encoding="utf-8")
                files.append(p)
            proc = _run([SCRIPTS / "check_inputs.py"] + files)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("全部输入就绪", proc.stdout)

    def test_missing_input_reports_list(self):
        with tempfile.TemporaryDirectory() as td:
            ok = Path(td) / "ok.txt"
            ok.write_text("内容", encoding="utf-8")
            gone = Path(td) / "not_exist.txt"
            proc = _run([SCRIPTS / "check_inputs.py", ok, gone, ok, ok])
            self.assertEqual(proc.returncode, 2)
            self.assertIn("历史标书（范例）", proc.stderr)
            self.assertIn("文件不存在", proc.stderr)

    def test_empty_input_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            ok = Path(td) / "ok.txt"
            ok.write_text("内容", encoding="utf-8")
            empty = Path(td) / "empty.txt"
            empty.write_text("", encoding="utf-8")
            proc = _run([SCRIPTS / "check_inputs.py", ok, ok, ok, empty])
            self.assertEqual(proc.returncode, 2)
            self.assertIn("文件为空", proc.stderr)


class TestPriceQuote(unittest.TestCase):
    def _write_input(self, path, cap):
        data = {
            "project_name": "测试项目",
            "tax_rate": 0.13,
            "cap": cap,
            "items": [
                {"no": 1, "name": "阳台地面砖", "unit": "m2", "qty": 100,
                 "unit_price_pretax": 58.00, "brand": "东鹏", "note": ""},
                {"no": 2, "name": "卫生间墙砖", "unit": "m2", "qty": 50.5,
                 "unit_price_pretax": 40.00, "brand": "", "note": ""},
            ],
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_calculation_and_uppercase(self):
        # 100*58 + 50.5*40 = 5800 + 2020 = 7820.00（不含税）
        # 含税 = 7820 * 1.13 = 8836.60
        with tempfile.TemporaryDirectory() as td:
            inp, out = Path(td) / "price_input.json", Path(td) / "price_result.json"
            self._write_input(inp, cap=10000.00)
            proc = _run([SCRIPTS / "price_quote.py", "--input", inp, "--out", out])
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(out.read_text(encoding="utf-8"))
            self.assertAlmostEqual(result["total_pretax"], 7820.00, places=2)
            self.assertAlmostEqual(result["total_taxed"], 8836.60, places=2)
            self.assertEqual(result["total_upper"], "捌仟捌佰叁拾陆元陆角整")
            self.assertTrue(result["all_pass"])
            self.assertEqual(len(result["rows"]), 2)
            self.assertEqual(result["rows"][0]["amount_pretax"], "5800.00")

    def test_over_cap_fails(self):
        with tempfile.TemporaryDirectory() as td:
            inp, out = Path(td) / "price_input.json", Path(td) / "price_result.json"
            self._write_input(inp, cap=8000.00)
            proc = _run([SCRIPTS / "price_quote.py", "--input", inp, "--out", out])
            self.assertEqual(proc.returncode, 1)
            result = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(result["all_pass"])
            failed = [c for c in result["checks"] if c["status"] == "FAIL"]
            self.assertTrue(any("限价" in c["name"] for c in failed))


class TestRenderDocs(unittest.TestCase):
    def test_minimal_render_smoke(self):
        pytest.importorskip("docx")
        with tempfile.TemporaryDirectory() as td:
            content = {
                "meta": {
                    "project_name": "测试项目",
                    "bidder": "测试投标人",
                    "tenderer": "测试招标人",
                },
                "docs": [
                    {
                        "file": "01-投标函.docx",
                        "title": "投标函",
                        "sections": [
                            {
                                "heading": "一、投标函",
                                "level": 1,
                                "blocks": [
                                    {"type": "p", "text": "致：测试招标人"},
                                    {"type": "sign", "lines": ["投标人：（盖单位章）"]},
                                ],
                            }
                        ],
                    }
                ],
            }
            content_path = Path(td) / "content.json"
            content_path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
            layout_path = Path(td) / "layout.json"
            layout_path.write_text("{}", encoding="utf-8")
            out_dir = Path(td) / "outputs"
            proc = _run(
                [SCRIPTS / "render_docs.py", content_path, "--out", out_dir,
                 "--layout", layout_path],
                cwd=td,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            produced = list(out_dir.glob("*.docx"))
            self.assertEqual([p.name for p in produced], ["01-投标函.docx"])

    def test_render_rejects_empty_docs(self):
        with tempfile.TemporaryDirectory() as td:
            content_path = Path(td) / "content.json"
            content_path.write_text(json.dumps({"meta": {}, "docs": []}), encoding="utf-8")
            proc = _run([SCRIPTS / "render_docs.py", content_path, "--out", Path(td) / "o"],
                        cwd=td)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("没有 docs", proc.stderr)


class TestBidAssistantSubagent(unittest.TestCase):
    def test_subagent_md_parses(self):
        import re

        raw = SUBAGENT_MD.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", raw, re.DOTALL)
        self.assertIsNotNone(m, "SUBAGENT.md frontmatter 未闭合（--- 必须顶格）")
        import yaml

        fm = yaml.safe_load(m.group(1))
        self.assertEqual(fm["name"], "AI标书助手")
        self.assertIn("bid-doc-generator", fm["skills"]["allowed"])
        self.assertTrue(fm["tools"]["inherit"])
        self.assertTrue(m.group(2).strip(), "system_prompt 应放在 body 中")
