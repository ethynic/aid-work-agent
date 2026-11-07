"""contract-generator 技能测试（workbuddy 移植）：注册发现 + prep/fill 端到端冒烟 + fetch_file 胶水。

端到端用 python-docx 动态生成样例合同模板（含甲方空位、金额、大写行、清单表），
走 prep.py → analysis.json → fill_contract.py（stdin）全链路，断言 fill_report 验收结论。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

pytestmark = __import__("pytest").mark.skills

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = PROJECT_ROOT / "src" / "skills" / "contract-generator-1.0.0"
SCRIPTS = SKILL_DIR / "scripts"


def _run(cmd, cwd, stdin_data=None):
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PROJECT_ROOT": str(PROJECT_ROOT),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "HOME": "/tmp",
    }
    proc = subprocess.run(
        [sys.executable] + [str(c) for c in cmd], cwd=str(cwd), env=env,
        capture_output=True, text=True, input=stdin_data, timeout=120,
    )
    return proc


def _build_template(path: Path):
    """生成样例合同模板 .docx（无占位符，模拟真实原件）。"""
    from docx import Document

    doc = Document()
    doc.add_paragraph("软件服务合同")
    doc.add_paragraph("甲方（客户）：")
    doc.add_paragraph("乙方：示例科技有限公司（地址：北京市海淀区，统一社会信用代码：91110100TEST01X0）")
    doc.add_paragraph("合同总金额（小写）：￥110,000")
    doc.add_paragraph("合同总金额（大写）：壹拾壹万元整")

    table = doc.add_table(rows=4, cols=5)
    headers = ["序号", "项目名称", "单价", "数量", "金额"]
    for i, h in enumerate(headers):
        table.rows[0].cells[i].text = h
    rows = [
        ["1", "样例产品A", "110,000", "1 套", "110,000"],
        ["2", "样例产品B", "20,000", "2 套", "40,000"],
        ["", "合计", "", "", "150,000"],
    ]
    for r, row_vals in enumerate(rows, start=1):
        for c, v in enumerate(row_vals):
            table.rows[r].cells[c].text = v
    doc.save(str(path))


ANALYSIS = {
    "template": "sample_contract.docx",
    "analysis_note": "测试样例：甲方空位、总额、大写、清单表",
    "fields": [
        {
            "key": "party_a",
            "label": "甲方公司全称",
            "question": "客户（甲方）公司全称？",
            "required": True,
            "locators": [{"kind": "after_label", "label": "甲方（客户）："}],
        },
        {
            "key": "total_amount",
            "label": "合同总金额",
            "formula": "sum:amount",
            "locators": [{"kind": "literal", "text": "￥110,000",
                          "value_template": "￥{value_fmt}"}],
        },
        {
            "key": "total_upper",
            "label": "合同总金额大写",
            "formula": "cny_upper:total_amount",
            "locators": [{"kind": "literal", "text": "壹拾壹万元整"}],
        },
    ],
    "service_tables": [
        {
            "table_index": 0,
            "rows_source": "service_items",
            "header_rows": 1,
            "sample_rows": [1, 2],
            "footer_rows": [3],
            "columns": [
                {"key": "name", "col": 1, "sample": "样例产品A", "label": "项目名称"},
                {"key": "unit_price", "col": 2, "sample": "110,000",
                 "value_template": "{value_fmt}"},
                {"key": "qty", "col": 3, "sample": "1 套", "value_template": "{qty} {unit}"},
                {"key": "amount", "col": 4, "sample": "110,000",
                 "value_template": "{value_fmt}", "formula": "unit_price*qty"},
            ],
            "total_cells": [
                {"footer_index": 0, "col": 4, "formula": "sum('amount')",
                 "value_template": "{value_fmt}"}
            ],
        }
    ],
}

ANSWERS = {
    "_meta": {"client": "测试客户", "note": "单测"},
    "party_a": "测试甲方科技有限公司",
    "service_items": [
        {"name": "AI智能体平台", "unit_price": "50000", "qty": "1", "unit": "套"},
        {"name": "实施服务", "unit_price": "10000", "qty": "2", "unit": "次"},
        {"name": "运维支持", "unit_price": "5000", "qty": "3", "unit": "年"},
    ],
}


class TestContractGeneratorSkill(unittest.TestCase):
    def test_skill_discovered_by_registry(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        self.assertIn("contract-generator", registry.list_skills())
        skill = registry.get("contract-generator")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.version, "1.0.0")

    def test_skill_requires_tools_declared(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        skill = registry.get("contract-generator")
        for tool in ("knowledge_base_search", "knowledge_file_search"):
            self.assertIn(tool, getattr(skill, "requires_tools", []))

    def test_prep_and_fill_end_to_end(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tpl_dir = tmp / "templates"
            tpl_dir.mkdir()
            _build_template(tpl_dir / "sample_contract.docx")

            # 第 1+2 步：prep 一次编排（扫描 + dump + 缓存检查）
            proc = _run([SCRIPTS / "prep.py", "--work", tmp / "work",
                         "--templates", tpl_dir], cwd=tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            prep = json.loads(proc.stdout)
            self.assertEqual(prep["templates_count"], 1)
            self.assertEqual(prep["analysis_cache"]["hit"], False)
            self.assertIn("compact_dump", prep)

            # 第 2 步产物：分析缓存
            work = tmp / "work"
            (work / "analysis.json").write_text(
                json.dumps(ANALYSIS, ensure_ascii=False), encoding="utf-8")

            # 第 4 步：stdin 一次回填 + 报告验收
            proc = _run(
                [SCRIPTS / "fill_contract.py", tpl_dir / "sample_contract.docx",
                 work / "analysis.json", "-", work / "output.docx",
                 "--report", work / "fill_report.json",
                 "--cache-dir", tmp / "cache"], cwd=tmp,
                stdin_data=json.dumps(ANSWERS, ensure_ascii=False))
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            report = json.loads((work / "fill_report.json").read_text(encoding="utf-8"))
            self.assertTrue(report["ok"], report.get("summary"))
            self.assertEqual(report["missing_required"], [])
            self.assertEqual(report["unmatched_locators"], [])
            self.assertEqual(
                report["verification"]["fields"].get("party_a"),
                "测试甲方科技有限公司")
            tables = report["verification"]["tables"]
            self.assertTrue(tables and tables[0].get("items") == 3,
                            f"清单应为 3 条: {tables}")
            self.assertTrue(report.get("analysis_cached"), "ok=true 时 analysis 应入缓存")

            # 大写与小写金额一致（85000 = 50000 + 10000*2 + 5000*3）
            amount_lines = " ".join(report["verification"]["amount_lines"])
            self.assertIn("85,000", amount_lines)
            self.assertIn("捌万伍仟", amount_lines)

            # 缓存复用：同模板再跑 prep 应命中
            proc = _run([SCRIPTS / "prep.py", "--work", tmp / "work2",
                         "--templates", tpl_dir, "--cache-dir", tmp / "cache"],
                        cwd=tmp)
            prep2 = json.loads(proc.stdout)
            self.assertTrue(prep2["analysis_cache"]["hit"], "同模板二次 prep 应命中缓存")


class TestFetchFileGlue(unittest.TestCase):
    """fetch_file.py 胶水：租户路径放行 + 越界拒绝。"""

    def _run_fetch(self, args, cwd):
        return _run([SCRIPTS / "fetch_file.py"] + args, cwd=cwd)

    def test_src_inside_tenant_storage_allowed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tenants = tmp / "storage" / "tenants" / "t1" / "templates"
            tenants.mkdir(parents=True)
            src = tenants / "contract_tpl.docx"
            src.write_bytes(b"PK\x03\x04fake")
            dest = tmp / "out" / "templates"
            proc = self._run_fetch(["--src", str(src), "--dest", str(dest)], cwd=tmp)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr[-500:])
            out = json.loads(proc.stdout)
            self.assertTrue(out["success"])
            self.assertEqual(out["name"], "contract_tpl.docx")
            self.assertTrue((dest / "contract_tpl.docx").exists())

    def test_src_outside_tenant_storage_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            outside = tmp / "etc" / "secret.docx"
            outside.parent.mkdir(parents=True)
            outside.write_bytes(b"x")
            proc = self._run_fetch(["--src", str(outside), "--dest", str(tmp / "o")],
                                   cwd=tmp)
            self.assertNotEqual(proc.returncode, 0)
            out = json.loads(proc.stdout)
            self.assertFalse(out["success"])
            self.assertIn("拒绝", out["error"])

    def test_missing_source_args_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            proc = self._run_fetch(["--dest", str(Path(td) / "o")], cwd=Path(td))
            self.assertNotEqual(proc.returncode, 0)
            out = json.loads(proc.stdout)
            self.assertFalse(out["success"])


if __name__ == "__main__":
    unittest.main()
