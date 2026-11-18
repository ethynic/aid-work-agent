"""excel-layout-clone 技能测试（workbuddy 移植）：注册发现 + prep 编排 + fill_xlsx
回填端到端冒烟（动态清单行增减 + 板式指纹验收 + 按源模板 hash 缓存复用）。

脚本链路依赖 lxml（XML 手术引擎）与 openpyxl（构造测试模板，仅测试用），
本地缺失时 importorskip 跳过。
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
SKILL_DIR = PROJECT_ROOT / "src" / "skills" / "excel-layout-clone-1.0.0"
SCRIPTS = SKILL_DIR / "scripts"

pytest.importorskip("lxml")
openpyxl = pytest.importorskip("openpyxl")


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
    """构造最小模板：标题/编号/日期字段 + 合并单元格 + 2 行样例清单表 + 合计公式行。"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "报价单"
    ws["A1"] = "样例公司产品报价单"
    ws["A2"] = "编号：QT-2026-001"
    ws["A3"] = "日期：2026-01-01"
    ws["A5"] = "序号"
    ws["B5"] = "品名"
    ws["C5"] = "数量"
    ws["D5"] = "单价"
    ws["E5"] = "金额"
    ws["A6"] = 1
    ws["B6"] = "样例品A"
    ws["C6"] = 10
    ws["D6"] = 2.5
    ws["E6"] = "=C6*D6"
    ws["A7"] = 2
    ws["B7"] = "样例品B"
    ws["C7"] = 5
    ws["D7"] = 4.0
    ws["E7"] = "=C7*D7"
    ws["A8"] = "合计"
    ws["E8"] = "=SUM(E6:E7)"
    ws.merge_cells("A1:E1")
    wb.save(str(path))


ANALYSIS = {
    "template": "tpl.xlsx",
    "analysis_note": "测试：标题/编号/日期字段 + 明细表按条数扩展 + 合计公式",
    "fields": [
        {"key": "title", "label": "标题", "required": True, "kind": "text",
         "locators": [{"kind": "cell", "sheet": "报价单", "ref": "A1"}]},
        {"key": "doc_no", "label": "编号", "required": True, "kind": "text",
         "locators": [{"kind": "value", "sheet": "报价单", "text": "QT-2026-001"}]},
        {"key": "date", "label": "日期", "required": False, "kind": "text",
         "locators": [{"kind": "value", "sheet": "报价单", "text": "2026-01-01"}]},
    ],
    "list_tables": [{
        "sheet": "报价单", "header_row": 5, "sample_rows": [6, 7],
        "footer_rows": [8], "rows_source": "items",
        "columns": [
            {"key": "seq", "col": "A", "kind": "number"},
            {"key": "name", "col": "B", "kind": "text"},
            {"key": "qty", "col": "C", "kind": "number"},
            {"key": "price", "col": "D", "kind": "number"},
            {"key": "amount", "col": "E", "kind": "formula",
             "formula": "=C{row}*D{row}"},
        ],
        "total_cells": [{"footer_index": 0, "col": "E", "kind": "formula",
                         "formula": "=SUM(E{start_row}:E{end_row})"}],
    }],
}


class TestExcelLayoutCloneSkillRegistry(unittest.TestCase):
    def test_skill_discovered_by_registry(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        self.assertIn("excel-layout-clone", registry.list_skills())
        skill = registry.get("excel-layout-clone")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.version, "1.1.0")
        self.assertIn("cp", skill.requires_tools)

    def test_required_scripts_exist(self):
        for name in ("prep.py", "fill_xlsx.py", "dump_template.py",
                     "scan_templates.py", "xlsx_engine.py"):
            self.assertTrue((SCRIPTS / name).is_file(), name)


class TestExcelLayoutClonePrep(unittest.TestCase):
    def test_prep_catalog_and_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tpl = tmp / "tpl.xlsx"
            _make_template(tpl)
            work = tmp / "work"
            r = _run(["prep.py", "--work", work, "--templates", tpl,
                      "--cache-dir", tmp / "cache"])
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertEqual(out["selected"]["format"], "xlsx")
            self.assertIn("exact-xlsx", out["layout_fidelity"])
            self.assertFalse(out["analysis_cache"]["hit"])
            self.assertTrue(out["compact_dump"])
            self.assertTrue(out["anchors"])
            self.assertTrue((work / "template_dump.txt").is_file())
            # 单份模板自动选定
            self.assertFalse(out.get("needs_template_choice"))
            # 合并单元格与公式出现在 compact_dump 中
            dump_text = "\n".join(out["compact_dump"])
            self.assertIn("MERGE", dump_text)
            self.assertIn("=SUM(E6:E7)", dump_text)

    def test_prep_multiple_templates_needs_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tdir = tmp / "tpls"
            tdir.mkdir()
            _make_template(tdir / "a.xlsx")
            _make_template(tdir / "b.xlsx")
            r = _run(["prep.py", "--work", tmp / "work",
                      "--templates", tdir, "--cache-dir", tmp / "cache"])
            self.assertEqual(r.returncode, 0, r.stderr)
            out = json.loads(r.stdout)
            self.assertTrue(out["needs_template_choice"])
            self.assertIsNone(out["selected"])

    def test_prep_requires_templates_arg(self):
        # 我方补丁：--templates 必传（运行时 cwd 为临时目录，无相对默认值）
        r = _run(["prep.py", "--work", "/tmp/x"])
        self.assertNotEqual(r.returncode, 0)


class TestExcelLayoutCloneFill(unittest.TestCase):
    def _prep(self, tmp: Path):
        tpl = tmp / "tpl.xlsx"
        _make_template(tpl)
        work = tmp / "work"
        r = _run(["prep.py", "--work", work, "--templates", tpl,
                  "--cache-dir", tmp / "cache"])
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_fill_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            prep = self._prep(tmp)
            work = Path(prep["work"])
            (work / "analysis.json").write_text(
                json.dumps(ANALYSIS, ensure_ascii=False), encoding="utf-8")
            answers = {
                "title": "测试公司产品报价单",
                "doc_no": "QT-2026-002",
                "date": "2026-11-16",
                "items": [
                    {"seq": 1, "name": "定制开发", "qty": 3, "price": 10000},
                    {"seq": 2, "name": "运维服务", "qty": 12, "price": 500},
                    {"seq": 3, "name": "培训", "qty": 2, "price": 2000},
                ],
            }
            cache_dir = tmp / "cache"
            r = _run(
                ["fill_xlsx.py", prep["working_template"],
                 work / "analysis.json", "-", work / "output.xlsx",
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
            self.assertEqual(rep["tables"][0]["items"], 3)
            self.assertEqual(rep["tables"][0]["rows_after"], 3)  # 2 样例行扩为 3
            # 未触及部件按字节保留：只动工作表与工作簿，不得出现 styles/media/vba
            for part in rep["changed_parts"]:
                self.assertNotIn("styles", part, rep["changed_parts"])
                self.assertNotIn("media", part, rep["changed_parts"])
            # stdin 落盘 answers.json + ok=true 自动按源模板 hash 入缓存
            self.assertTrue((work / "answers.json").is_file())
            cached = cache_dir / ("%s.analysis.json" % prep["selected"]["hash"])
            self.assertTrue(cached.is_file())
            # 验收结论字段齐全
            self.assertIn("fields", rep["verification"])
            self.assertIn("preview", rep["verification"])

    def test_fill_missing_required_fails_and_not_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            prep = self._prep(tmp)
            work = Path(prep["work"])
            analysis = {"fields": [ANALYSIS["fields"][0]]}  # 仅保留必填 title
            (work / "analysis.json").write_text(
                json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
            cache_dir = tmp / "cache"
            # fill_xlsx.py 失败时退出码仍为 0，ok=false 写进 JSON 报告
            r = _run(
                ["fill_xlsx.py", prep["working_template"],
                 work / "analysis.json", "-", work / "output.xlsx",
                 "--cache-dir", cache_dir],
                stdin="{}")
            self.assertEqual(r.returncode, 0)
            rep = json.loads(r.stdout)
            self.assertFalse(rep["ok"])
            self.assertIn("title", rep["missing_required"])
            self.assertFalse(rep["analysis_cached"])
            self.assertFalse(list(cache_dir.glob("*.analysis.json")))


def _inject_pivot_parts(path: Path) -> None:
    """向 xlsx 包内注入最小透视表/透视缓存部件（仅测包级手术行为，不追求 Excel 可渲染）。"""
    import zipfile

    pivot_table = (
        '<pivotTableDefinition xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" name="PT1" cacheId="0" dataOnRows="1"/>'
    )
    pivot_cache = (
        '<pivotCacheDefinition xmlns="http://schemas.openxmlformats.org/'
        'spreadsheetml/2006/main" r:id="rId1" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'refreshOnLoad="0">'
        '<cacheSource type="worksheet">'
        '<worksheetSource ref="A5:E8" sheet="报价单"/>'
        '</cacheSource>'
        '</pivotCacheDefinition>'
    )
    src_zip = str(path) + ".src"
    path.rename(src_zip)
    with zipfile.ZipFile(src_zip) as fin, \
            zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as fout:
        for item in fin.infolist():
            fout.writestr(item, fin.read(item.filename))
        fout.writestr("xl/pivotTables/pivotTable1.xml", pivot_table)
        fout.writestr("xl/pivotCache/pivotCacheDefinition1.xml", pivot_cache)
    os.remove(src_zip)


class TestExcelLayoutClonePivot(unittest.TestCase):
    """上游 v1.1.0：透视缓存 worksheetSource 随明细行同步扩展 + refreshOnLoad。"""

    def _prep_injected(self, tmp: Path):
        tpl = tmp / "tpl.xlsx"
        _make_template(tpl)
        _inject_pivot_parts(tpl)
        work = tmp / "work"
        r = _run(["prep.py", "--work", work, "--templates", tpl,
                  "--cache-dir", tmp / "cache"])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        # 上游 v1.1.0 新警告文案：源区域随明细行扩展并设置打开时刷新
        self.assertTrue(any("源区域会随明细行扩展" in w for w in out["warnings"]),
                        out["warnings"])
        return out

    def test_pivot_source_extends_and_refresh_on_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            prep = self._prep_injected(tmp)
            work = Path(prep["work"])
            (work / "analysis.json").write_text(
                json.dumps(ANALYSIS, ensure_ascii=False), encoding="utf-8")
            answers = {
                "title": "测试公司产品报价单", "doc_no": "QT-2026-002",
                "date": "2026-11-16",
                "items": [
                    {"seq": 1, "name": "定制开发", "qty": 3, "price": 10000},
                    {"seq": 2, "name": "运维服务", "qty": 12, "price": 500},
                    {"seq": 3, "name": "培训", "qty": 2, "price": 2000},
                ],
            }
            r = _run(
                ["fill_xlsx.py", prep["working_template"],
                 work / "analysis.json", "-", work / "output.xlsx",
                 "--report", work / "fill_report.json"],
                stdin=json.dumps(answers, ensure_ascii=False))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rep = json.loads(r.stdout)
            # 2 样例行扩为 3 条，footer 8->9；透视缓存随行平移不再视为板式漂移
            self.assertTrue(rep["ok"], rep["summary"])
            self.assertTrue(rep["layout"]["identical"], rep["layout"])
            self.assertEqual(rep["tables"][0]["rows_after"], 3)
            self.assertIn("xl/pivotCache/pivotCacheDefinition1.xml",
                          rep["changed_parts"])
            # 校验成品包：worksheetSource 扩到 A5:E9 且 refreshOnLoad=1
            import zipfile

            with zipfile.ZipFile(work / "output.xlsx") as z:
                cache_xml = z.read("xl/pivotCache/pivotCacheDefinition1.xml").decode("utf-8")
            self.assertIn('ref="A5:E9"', cache_xml)
            self.assertIn('refreshOnLoad="1"', cache_xml)


if __name__ == "__main__":
    unittest.main()
