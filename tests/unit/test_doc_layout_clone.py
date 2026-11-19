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
        # 2026-11-19 合并上游 1.2.0 -> 1.3.0（分轮问答 + 信息采集表）
        self.assertEqual(skill.version, "1.3.0")
        self.assertIn("cp", skill.requires_tools)

    def test_required_scripts_exist(self):
        for name in ("prep.py", "fill_doc.py", "dump_template.py",
                     "pdf_to_docx.py", "scan_templates.py",
                     "probe.py", "make_intake.py"):
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

    def test_fill_default_fallback_avoids_missing(self):
        # 上游 1.2.1：required + default 字段无显式答案时用默认值兜底，不误报缺失
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            prep = self._prep(tmp)
            work = Path(prep["work"])
            analysis = {
                "fields": [
                    {"key": "party_a", "label": "甲方", "required": True,
                     "default": "甲方：默认主体有限公司",
                     "locators": [{"kind": "literal",
                                   "text": "甲方：样例科技有限公司"}]},
                ],
            }
            (work / "analysis.json").write_text(
                json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
            r = _run(
                ["fill_doc.py", prep["working_template"], work / "analysis.json",
                 "-", work / "output.docx", "--report", work / "fill_report.json"],
                stdin="{}")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rep = json.loads(r.stdout)
            self.assertTrue(rep["ok"], rep["summary"])
            self.assertEqual(rep["missing_required"], [])
            self.assertEqual(rep["defaults"][0]["field"], "party_a")
            self.assertEqual(rep["verification"]["fields"]["party_a"],
                             "甲方：默认主体有限公司")

    def test_fill_deferred_by_dependency_not_missing(self):
        # 上游 1.2.0：被 depends_on 挡住的必填字段记 deferred，不算 missing_required
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            prep = self._prep(tmp)
            work = Path(prep["work"])
            analysis = {
                "fields": [
                    {"key": "party_a", "label": "甲方", "required": True,
                     "locators": [{"kind": "literal",
                                   "text": "甲方：样例科技有限公司"}]},
                    {"key": "installment_count", "label": "分期期数",
                     "required": True,
                     "depends_on": {"key": "settle_mode", "equals": "分期付款"},
                     "locators": [{"kind": "literal",
                                   "text": "签订日期：2026 年 1 月 1 日"}]},
                ],
            }
            (work / "analysis.json").write_text(
                json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
            r = _run(
                ["fill_doc.py", prep["working_template"], work / "analysis.json",
                 "-", work / "output.docx", "--report", work / "fill_report.json"],
                stdin=json.dumps({"party_a": "甲方：测试股份有限公司",
                                  "settle_mode": "一次性付款"},
                                 ensure_ascii=False))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rep = json.loads(r.stdout)
            self.assertTrue(rep["ok"], rep["summary"])
            self.assertEqual(rep["missing_required"], [])
            self.assertEqual(len(rep["deferred"]), 1)
            self.assertEqual(rep["deferred"][0]["field"], "installment_count")
            self.assertEqual(
                rep["verification"]["checks"]["deferred_by_dependency"], 1)


class TestDocLayoutCloneProbe(unittest.TestCase):
    """probe.py 分轮编排：依赖判定 / 自动解决字段 / ready_to_fill / 答案合并。"""

    ANALYSIS = {
        "fields": [
            {"key": "settle_mode", "label": "结算方式", "required": True},
            {"key": "installment_count", "label": "分期期数", "required": True,
             "phase": 2, "depends_on": {"key": "settle_mode", "equals": "分期付款"}},
            {"key": "sign_date", "label": "签订日期", "required": True,
             "default": "2026 年 12 月 31 日"},
            {"key": "total", "label": "合计", "formula": "sum:amount"},
        ],
        "list_tables": [],
    }

    def test_round1_only_unlocked_and_auto_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            apath = Path(tmp) / "analysis.json"
            apath.write_text(json.dumps(self.ANALYSIS, ensure_ascii=False),
                             encoding="utf-8")
            r = _run(["probe.py", apath, "--json-only"])
            self.assertEqual(r.returncode, 0, r.stderr)
            plan = json.loads(r.stdout)
            self.assertFalse(plan["ready_to_fill"])
            # 第 1 轮只问 settle_mode；分期期数被挡、日期/合计自动解决
            self.assertEqual([f["key"] for f in plan["open_fields"]],
                             ["settle_mode"])
            self.assertEqual([f["key"] for f in plan["blocked_fields"]],
                             ["installment_count"])
            self.assertEqual({f["key"] for f in plan["auto_resolved"]},
                             {"sign_date", "total"})

    def test_round2_unlocks_after_answer_and_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            apath = tmp / "analysis.json"
            apath.write_text(json.dumps(self.ANALYSIS, ensure_ascii=False),
                             encoding="utf-8")
            new = tmp / "new.json"
            new.write_text(json.dumps({"settle_mode": "分期付款"},
                                      ensure_ascii=False), encoding="utf-8")
            answers = tmp / "answers.json"
            r = _run(["probe.py", apath, "--round", "2", "--merge", new,
                      "--merge-out", answers, "--json-only"])
            self.assertEqual(r.returncode, 0, r.stderr)
            plan = json.loads(r.stdout)
            # 依赖解锁后分期期数进入待问（尚未 ready），需再答一轮
            self.assertFalse(plan["ready_to_fill"])
            self.assertEqual([f["key"] for f in plan["open_fields"]],
                             ["installment_count"])
            # 合并留痕：_meta.rounds 记录本轮答案键
            merged = json.loads(answers.read_text(encoding="utf-8"))
            self.assertEqual(merged["settle_mode"], "分期付款")
            self.assertEqual(merged["_meta"]["rounds"][0]["answered_keys"],
                             ["settle_mode"])

            # 第 3 轮补上期数答案 -> ready_to_fill
            new3 = tmp / "new3.json"
            new3.write_text(json.dumps({"installment_count": "6"},
                                       ensure_ascii=False), encoding="utf-8")
            r = _run(["probe.py", apath, answers, "--round", "3",
                      "--merge", new3, "--merge-out", answers, "--json-only"])
            self.assertEqual(r.returncode, 0, r.stderr)
            plan = json.loads(r.stdout)
            self.assertTrue(plan["ready_to_fill"], plan["counts"])
            self.assertEqual(plan["counts"]["open"], 0)

    def test_dependency_unsatisfied_blocked_but_ready(self):
        # settle_mode 答了"一次性付款"：分期期数被挡（blocked）本轮不问，
        # 也不阻塞 ready_to_fill（与 fill_doc 的 deferred 语义一致）
        with tempfile.TemporaryDirectory() as tmp:
            apath = Path(tmp) / "analysis.json"
            apath.write_text(json.dumps(self.ANALYSIS, ensure_ascii=False),
                             encoding="utf-8")
            r = _run(["probe.py", apath, "-", "--round", "2", "--json-only"],
                     stdin=json.dumps({"settle_mode": "一次性付款"},
                                      ensure_ascii=False))
            self.assertEqual(r.returncode, 0, r.stderr)
            plan = json.loads(r.stdout)
            self.assertTrue(plan["ready_to_fill"], plan["counts"])
            self.assertEqual(plan["counts"]["blocked"], 1)
            self.assertEqual(plan["counts"]["open"], 0)


class TestDocLayoutCloneIntake(unittest.TestCase):
    """make_intake.py 信息采集表：生成（示例值黄色高亮）+ 回收（不依赖高亮）。"""

    FIELDS = {
        "title": "测试信息采集表",
        "note": "黄色高亮=示例数据，请替换为真实值。",
        "groups": [
            {"name": "一、主体信息", "fields": [
                {"key": "company_name", "label": "公司全称", "sample": "样例科技",
                 "hint": "营业执照全称", "required": True},
                {"key": "revenue", "label": "营业收入（万元）", "sample": ""},
            ]},
        ],
    }

    def test_build_and_extract_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fpath = tmp / "fields.json"
            fpath.write_text(json.dumps(self.FIELDS, ensure_ascii=False),
                             encoding="utf-8")
            out = tmp / "信息采集表.docx"
            r = _run(["make_intake.py", fpath, out])
            self.assertEqual(r.returncode, 0, r.stderr)
            res = json.loads(r.stdout)
            self.assertEqual(res["groups"], 1)
            self.assertEqual(res["fields"], 2)
            self.assertEqual(res["highlight"], "yellow=示例数据")
            self.assertTrue(out.is_file())

            # 回收：填写第 3 列（不保留高亮），读回 key -> value
            from docx import Document
            doc = Document(str(out))
            tbl = doc.tables[0]
            tbl.rows[1].cells[2].text = "测试股份有限公司"
            tbl.rows[2].cells[2].text = "4500"
            filled = tmp / "filled.docx"
            doc.save(str(filled))
            values_out = tmp / "intake_values.json"
            r = _run(["make_intake.py", "--extract", filled, fpath,
                      "--out", values_out])
            self.assertEqual(r.returncode, 0, r.stderr)
            res = json.loads(r.stdout)
            self.assertEqual(res["count"], 2)
            self.assertEqual(res["values"]["company_name"], "测试股份有限公司")
            self.assertEqual(res["values"]["revenue"], "4500")
            self.assertTrue(values_out.is_file())


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
