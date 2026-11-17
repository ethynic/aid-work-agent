"""doc-layout-clone 技能测试（workbuddy 移植）：注册发现 + prep 编排 + fill_doc 回填
端到端冒烟（动态清单行增减 + 板式指纹验收）。

脚本链路依赖 python-docx（prep/fill），本地缺失时 importorskip 跳过；
pdf2docx（PDF 样版转换）容器安装后才会执行对应用例。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.skills

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = PROJECT_ROOT / "src" / "skills" / "doc-layout-clone-1.0.0"
SCRIPTS = SKILL_DIR / "scripts"

docx = pytest.importorskip("docx")


def _run(args, stdin=None, cwd=None):
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PROJECT_ROOT": str(PROJECT_ROOT),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "HOME": "/tmp",
    }
    return subprocess.run(
        [sys.executable] + [str(a) for a in args],
        input=stdin, cwd=str(cwd or SCRIPTS), env=env,
        capture_output=True, text=True, timeout=120,
    )


def _make_template(path: Path) -> None:
    """构造最小样版：标题段 + 落款段 + 3 行样例清单表 + 合计行。"""
    from docx import Document

    doc = Document()
    doc.add_paragraph("服务合同")
    doc.add_paragraph("甲方：样例科技有限公司")
    doc.add_paragraph("签订日期：2026 年 1 月 1 日")
    tbl = doc.add_table(rows=5, cols=3)
    header = tbl.rows[0]
    for i, name in enumerate(("项目名称", "金额", "备注")):
        header.cells[i].text = name
    samples = [
        ("软件开发服务", "110,000", "含一年维护"),
        ("硬件采购", "50,000", ""),
        ("培训服务", "10,000", "两场"),
    ]
    for ri, row in enumerate(tbl.rows[1:4], start=1):
        for ci, val in enumerate(samples[ri - 1]):
            row.cells[ci].text = val
    total = tbl.rows[4]
    total.cells[0].text = "合计"
    total.cells[2].text = "110,000+50,000+10,000"
    doc.save(str(path))


class TestDocLayoutCloneSkillRegistry(unittest.TestCase):
    def test_skill_discovered_by_registry(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        self.assertIn("doc-layout-clone", registry.list_skills())
        skill = registry.get("doc-layout-clone")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.version, "1.0.0")
        self.assertIn("cp", skill.requires_tools)

    def test_required_scripts_exist(self):
        for name in ("prep.py", "fill_doc.py", "dump_template.py",
                     "pdf_to_docx.py", "scan_templates.py"):
            self.assertTrue((SCRIPTS / name).is_file(), name)


class TestDocLayoutClonePrep(unittest.TestCase):
    def test_prep_catalog_and_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpl = Path(tmp) / "tpl.docx"
            _make_template(tpl)
            work = Path(tmp) / "work"
            r = _run(["prep.py", "--work", work, "--templates", tpl,
                      "--cache-dir", Path(tmp) / "cache"])
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["selected"]["format"], "docx")
            self.assertEqual(out["layout_fidelity"], "exact-docx")
            self.assertFalse(out["analysis_cache"]["hit"])
            self.assertTrue(out["compact_dump"])
            self.assertTrue(out["anchors"])
            self.assertTrue((work / "template_dump.txt").is_file())
            # 单份样版自动选定
            self.assertFalse(out.get("needs_template_choice"))

    def test_prep_multiple_templates_needs_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp) / "tpls"
            tdir.mkdir()
            _make_template(tdir / "a.docx")
            _make_template(tdir / "b.docx")
            r = _run(["prep.py", "--work", Path(tmp) / "work",
                      "--templates", tdir, "--cache-dir", Path(tmp) / "cache"])
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertTrue(out["needs_template_choice"])
            self.assertIsNone(out["selected"])

    def test_prep_requires_templates_arg(self):
        # 我方补丁：--templates 必传（运行时 cwd 为临时目录，无相对默认值）
        r = _run(["prep.py", "--work", "/tmp/x"])
        self.assertNotEqual(r.returncode, 0)


class TestDocLayoutCloneFill(unittest.TestCase):
    def _prep(self, tmp: Path):
        tpl = tmp / "tpl.docx"
        _make_template(tpl)
        work = tmp / "work"
        r = _run(["prep.py", "--work", work, "--templates", tpl,
                  "--cache-dir", tmp / "cache"])
        return json.loads(r.stdout)

    def test_fill_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            prep = self._prep(tmp)
            work = Path(prep["work"])
            analysis = {
                "template": "tpl.docx",
                "analysis_note": "测试：改甲方、日期，清单行动态增减",
                "fields": [
                    {"key": "party_a", "label": "甲方", "required": True,
                     "locators": [{"kind": "literal",
                                   "text": "甲方：样例科技有限公司"}]},
                    {"key": "sign_date", "label": "签订日期", "required": True,
                     "locators": [{"kind": "regex",
                                   "pattern": r"签订日期：\d{4} 年 \d{1,2} 月 \d{1,2} 日",
                                   "value_template": "签订日期：{value}"}]},
                    {"key": "total", "label": "合计金额", "formula": "sum:amount",
                     "locators": [{"kind": "cell", "table": 0, "row": 4, "col": 2}]},
                ],
                "list_tables": [{
                    "table_index": 0, "rows_source": "items",
                    "header_rows": 1, "sample_rows": [1, 2, 3], "footer_rows": [4],
                    "columns": [
                        {"key": "name", "col": 0},
                        {"key": "amount", "col": 1, "value_template": "{value_fmt}"},
                        {"key": "note", "col": 2},
                    ],
                    "total_cells": [{"footer_index": 0, "col": 2,
                                     "formula": "sum:amount",
                                     "value_template": "{value_fmt}"}],
                }],
            }
            (work / "analysis.json").write_text(
                json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
            answers = {
                "party_a": "甲方：测试股份有限公司",
                "sign_date": "2026 年 11 月 16 日",
                "items": [
                    {"name": "咨询实施", "amount": "30000", "note": "首期"},
                    {"name": "系统运维", "amount": "12000.5", "note": ""},
                ],
            }
            cache_dir = tmp / "cache"
            r = _run(
                ["fill_doc.py", prep["working_template"], work / "analysis.json",
                 "-", work / "output.docx",
                 "--report", work / "fill_report.json",
                 "--cache-dir", cache_dir,
                 "--cache-key", prep["selected"]["hash"]],
                stdin=json.dumps(answers, ensure_ascii=False))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rep = json.loads(r.stdout)
            self.assertTrue(rep["ok"], rep["summary"])
            self.assertEqual(rep["missing_required"], [])
            self.assertEqual(rep["unmatched_locators"], [])
            self.assertTrue(rep["layout"]["identical"], rep["layout"])
            self.assertEqual(rep["tables"][0]["items"], 2)
            self.assertEqual(rep["tables"][0]["rows_after"], 4)  # 表头+2条+合计
            # 清单从 3 条样例减为 2 条，金额按新条目合计 42,000.5
            self.assertEqual(rep["total_cells"][0]["value"], "42,000.50")
            # 板式指纹一致、stdin 落盘 answers.json
            self.assertTrue((work / "answers.json").is_file())
            # ok=true 自动按源样版 hash 入缓存
            cached = cache_dir / ("%s.analysis.json" % prep["selected"]["hash"])
            self.assertTrue(cached.is_file())

    def test_fill_missing_required_fails_and_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            prep = self._prep(tmp)
            work = Path(prep["work"])
            analysis = {
                "fields": [{"key": "party_a", "label": "甲方", "required": True,
                            "locators": [{"kind": "literal",
                                          "text": "甲方：样例科技有限公司"}]}],
            }
            (work / "analysis.json").write_text(
                json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
            cache_dir = tmp / "cache"
            r = _run(
                ["fill_doc.py", prep["working_template"], work / "analysis.json",
                 "-", work / "output.docx", "--cache-dir", cache_dir],
                stdin="{}")
            self.assertEqual(r.returncode, 2)
            rep = json.loads(r.stdout)
            self.assertFalse(rep["ok"])
            self.assertEqual(rep["missing_required"], ["party_a"])
            self.assertIn("skipped", rep["analysis_cached"])
            self.assertFalse(list(cache_dir.glob("*.analysis.json")))


class TestDocLayoutClonePdf(unittest.TestCase):
    def test_pdf_template_conversion(self):
        pytest.importorskip("pdf2docx")
        fitz = pytest.importorskip("fitz")
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pdf = fitz.open()
            page = pdf.new_page()
            page.insert_text((72, 100), "项目结项报告", fontsize=16)
            page.insert_text((72, 140), "项目名称：样例项目", fontsize=11)
            pdf.save(str(tmp / "tpl.pdf"))
            pdf.close()
            r = _run(["prep.py", "--work", tmp / "work", "--templates",
                      tmp / "tpl.pdf", "--cache-dir", tmp / "cache"])
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["layout_fidelity"], "converted-from-pdf")
            self.assertIn("conversion", out)
            self.assertTrue(out["conversion"].get("coverage", 0) >= 0.7)


if __name__ == "__main__":
    unittest.main()
