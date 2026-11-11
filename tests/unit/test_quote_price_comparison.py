"""quote-price-comparison 技能测试（workbuddy 移植）：注册发现 + xlsx 端到端三脚本冒烟 + 子智能体薄壳守护。

端到端覆盖 extract_quotes.py -> compare_prices.py -> mark_sources.py 主链路（仅依赖 openpyxl，
本地已装），验证：抽取条目、红绿判定（甲贵乙便宜）、marks.json 生成、源单副本标色文件产出。

TestQuotePriceComparisonUpstreamSamples 使用第三方上游样例（tests/fixtures/quote_compare/，
5 来源格式 xlsx/csv/docx/pdf/手工 JSON + library/web/USD 条目），断言与上游期望输出
（ext_package/quote-price-comparison-testdata/out）逐项一致；docx/pdf 依赖缺失时跳过。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import openpyxl

pytestmark = __import__("pytest").mark.skills

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = PROJECT_ROOT / "src" / "skills" / "quote-price-comparison-1.0.0"
SCRIPTS = SKILL_DIR / "scripts"
SUBAGENT_MD = PROJECT_ROOT / "subagents" / "procurement-comparison" / "SUBAGENT.md"
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "quote_compare"


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
        cwd=str(SKILL_DIR), env=env, capture_output=True, text=True, timeout=120,
    )


def _make_quote(path: Path, price: float, supplier_file: str) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["序号", "产品名称", "规格型号", "数量", "单位", "单价", "金额"])
    ws.append([1, "冷轧钢板", "1.0mm*1250*2500", 10, "吨", price, price * 10])
    ws.append([2, "工业交换机", "8口千兆 导轨式", 2, "台", price / 10, price / 10 * 2])
    wb.save(path)


class TestQuotePriceComparisonSkill(unittest.TestCase):
    def test_skill_discovered_by_registry(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        self.assertIn("quote-price-comparison", registry.list_skills())
        skill = registry.get("quote-price-comparison")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.version, "1.0.0")

    def test_skill_requires_tools_declared(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        skill = registry.get("quote-price-comparison")
        for tool in ("knowledge_base_search", "knowledge_file_search",
                     "web_search", "web_fetch", "cp"):
            self.assertIn(tool, getattr(skill, "requires_tools", []))

    def test_subagent_shell_references_skills(self):
        """薄壳子智能体 frontmatter 顶格闭合且 skills.allowed 同时指向两个技能（防 frontmatter 陷阱）。"""
        text = SUBAGENT_MD.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        import yaml

        frontmatter = text.split("---\n", 2)[1]
        meta = yaml.safe_load(frontmatter)
        self.assertEqual(meta["name"], "采购比价助手")
        self.assertIn("quote-price-comparison", meta["skills"]["allowed"])
        self.assertIn("supplier-qualification-review", meta["skills"]["allowed"])
        self.assertTrue(meta["tools"].get("inherit"))

    def test_e2e_extract_compare_mark(self):
        """最小端到端：两份 xlsx 报价 -> 抽取 -> 判定（甲贵乙便宜） -> 副本标色。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            q_a = tmp / "甲公司报价.xlsx"
            q_b = tmp / "乙公司报价.xlsx"
            _make_quote(q_a, 4520.0, "甲公司报价.xlsx")   # 基准 4200 -> +7.6% 偏贵
            _make_quote(q_b, 3950.0, "乙公司报价.xlsx")   # 基准 4200 -> -6.0% 偏便宜

            work = tmp / "work"
            work.mkdir()

            proc = _run([
                SCRIPTS / "extract_quotes.py", q_a, q_b,
                "--out", work / "quotes.json",
                "--supplier-map", json.dumps({"甲公司报价.xlsx": "甲公司", "乙公司报价.xlsx": "乙公司"}, ensure_ascii=False),
            ])
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            quotes = json.loads((work / "quotes.json").read_text(encoding="utf-8"))
            self.assertEqual(quotes["item_count"], 4)
            self.assertEqual({i["supplier"] for i in quotes["items"]}, {"甲公司", "乙公司"})
            # locator 必须保留（标色依据）
            for item in quotes["items"]:
                self.assertEqual(item["locator"]["kind"], "xlsx")
                self.assertTrue(item["locator"]["file"])

            proc = _run([
                SCRIPTS / "compare_prices.py",
                "--quotes", work / "quotes.json", "--out-dir", work,
            ])
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            comparison = json.loads((work / "comparison.json").read_text(encoding="utf-8"))
            flags = {}
            for group in comparison.get("groups", []):
                for row in group.get("rows", []):
                    flags[(row.get("supplier"), row.get("name"))] = row.get("flag")
            self.assertEqual(flags[("甲公司", "冷轧钢板")], "expensive")
            self.assertEqual(flags[("乙公司", "冷轧钢板")], "cheap")

            marks = json.loads((work / "marks.json").read_text(encoding="utf-8"))
            self.assertEqual(marks["count"], 4)  # 两个品项 x 甲贵乙便宜

            proc = _run([
                SCRIPTS / "mark_sources.py",
                "--marks", work / "marks.json", "--out-dir", work,
            ])
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            self.assertTrue((work / "甲公司报价_marked.xlsx").exists())
            self.assertTrue((work / "乙公司报价_marked.xlsx").exists())
            report = json.loads((work / "mark_report.json").read_text(encoding="utf-8"))
            self.assertEqual(len(report.get("unmarked", [])), 0)


class TestQuotePriceComparisonUpstreamSamples(unittest.TestCase):
    """上游样例全链路回归：5 来源（xlsx/csv/docx/pdf/手工 JSON）+ library/web/USD，对齐上游期望。"""

    EXPECTED_FLAGS = {
        ("G001", "甲公司"): "expensive", ("G001", "乙公司"): "cheap",
        ("G001", "丙公司"): "cheap", ("G001", "丁公司"): "expensive",
        ("G001", "资料库-2025框架价"): "cheap", ("G001", "1688批发(参考)"): "reference",
        ("G001", "戊公司"): "expensive",
        ("G002", "甲公司"): "neutral", ("G002", "乙公司"): "cheap",
        ("G002", "丙公司"): "expensive", ("G002", "丁公司"): "neutral",
        ("G002", "资料库-2025框架价"): "cheap", ("G002", "海外电商(参考)"): "reference",
        ("G002", "戊公司"): "neutral",
        ("G003", "甲公司"): "expensive", ("G003", "乙公司"): "neutral", ("G003", "丙公司"): "neutral",
        ("G004", "甲公司"): "expensive", ("G004", "乙公司"): "cheap",
        ("G004", "丙公司"): "expensive", ("G004", "资料库-2025框架价"): "cheap",
        ("G005", "乙公司"): "no_baseline",
    }

    def setUp(self):
        import pytest
        pytest.importorskip("docx", reason="python-docx 未安装（容器外跳过 docx/pdf 链路）")
        pytest.importorskip("fitz", reason="PyMuPDF 未安装（容器外跳过 docx/pdf 链路）")
        self._tmp = tempfile.TemporaryDirectory()
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args):
        proc = _run([SCRIPTS / args[0]] + list(args[1:]))
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return proc

    def test_full_pipeline_matches_upstream_expectations(self):
        work = self.work
        self._run(
            "extract_quotes.py",
            FIXTURES / "甲公司报价单.xlsx", FIXTURES / "乙公司报价.xlsx",
            FIXTURES / "丙公司报价.csv", FIXTURES / "丁公司报价.docx",
            "--out", work / "quotes.json",
            "--supplier-map", json.dumps(
                {"甲公司报价单.xlsx": "甲公司", "乙公司报价.xlsx": "乙公司",
                 "丙公司报价.csv": "丙公司", "丁公司报价.docx": "丁公司"}, ensure_ascii=False),
        )
        # 手工 JSON：{{SRC_DIR}} 占位符运行时替换为实际 fixtures 路径（上游原始 locator 路径不可移植）
        manual_text = (FIXTURES / "manual_sources.json").read_text(encoding="utf-8")
        manual_path = work / "manual_sources.json"
        manual_path.write_text(manual_text.replace("{{SRC_DIR}}", str(FIXTURES)), encoding="utf-8")
        self._run("extract_quotes.py", manual_path, "--out", work / "quotes.json", "--merge")

        quotes = json.loads((work / "quotes.json").read_text(encoding="utf-8"))
        self.assertEqual(quotes["item_count"], 22)
        self.assertEqual(quotes["files"][0], "manual_sources.json")

        self._run("compare_prices.py", "--quotes", work / "quotes.json",
                  "--out-dir", work, "--fx", json.dumps({"USD": 7.15}))

        comparison = json.loads((work / "comparison.json").read_text(encoding="utf-8"))
        self.assertEqual(len(comparison["groups"]), 5)
        actual = {}
        for g in comparison["groups"]:
            for r in g["rows"]:
                actual[(g["group_id"], r["supplier"])] = r.get("flag")
        self.assertEqual(actual, self.EXPECTED_FLAGS)
        # 无汇率折算与税务归一口径与上游一致
        g2 = {r["supplier"]: r for g in comparison["groups"] if g["group_id"] == "G002" for r in g["rows"]}
        self.assertAlmostEqual(g2["海外电商(参考)"]["price_norm"], 1272.7, places=1)
        self.assertIn("未税→含税 @13%", g2["丙公司"]["norm_reasons"])

        marks = json.loads((work / "marks.json").read_text(encoding="utf-8"))
        self.assertEqual(marks["count"], 11)
        self._run("mark_sources.py", "--marks", work / "marks.json", "--out-dir", work)

        report = json.loads((work / "mark_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["marked"], 11)
        self.assertEqual(report["total_marks"], 11)
        self.assertEqual(report.get("unmarked", []), [])
        for name in ("甲公司报价单_marked.xlsx", "乙公司报价_marked.xlsx",
                     "丙公司报价_marked.xlsx", "丁公司报价_marked.docx", "戊公司报价_marked.pdf"):
            self.assertTrue((work / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
