"""bid-opportunity-evaluator 技能测试（workbuddy 移植）：注册发现 + competitor_intel verify
占位代号剔除 + run_pipeline 端到端冒烟 + ai-bid-assistant 子智能体配置。

端到端直接调用 scripts/ 下脚本；流水线依赖 python-docx/openpyxl/pymupdf，
本地环境缺失时 importorskip 跳过。
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
SKILL_DIR = PROJECT_ROOT / "src" / "skills" / "bid-opportunity-evaluator-1.0.0"
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
        cwd=str(cwd or SKILL_DIR), env=env, capture_output=True, text=True, timeout=120,
    )


def _make_company_profile(path: Path) -> None:
    profile = {
        "capability": {
            "legal_entity": True, "legal_entity_evidence": "营业执照",
            "scope_covers": True, "scope_evidence": "经营范围含设备销售",
            "has_license_or_authorization": True, "license_status": "制造商",
            "license_evidence": "许可证编号", "license_note": "",
        },
        "finance": {
            "has_audit_report": True, "has_quarterly_statements": True,
            "evidence": "2025年度审计报告", "note": "",
            "max_bond_bearable": 500000,
            "target_gross_margin": 0.16, "min_gross_margin": 0.08,
        },
        "tax_social": {"has_records": True, "evidence": "纳税证明", "note": ""},
        "credit": {"clean": True, "evidence": "信用中国", "note": ""},
        "performance": {
            "required_count": 1, "required_years": 3,
            "required_amount": None, "required_type": None,
            "available_count": 1,
            "projects": [{"name": "某项目", "year": 2025, "amount": 3200000,
                          "scope": "设备供货", "buyer": "某单位"}],
        },
        "use_jv": False, "use_subcontract": False, "local_presence": True,
        "capacity": {"annual_output": "充足", "regions": [], "lead_time_hours": 48},
        "product_match_rate": 100,
    }
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_tender_docx(path: Path) -> None:
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    doc.add_heading("XX单位设备采购项目招标文件", 0)
    doc.add_paragraph(
        "项目编号：CS-2026-001。建设地点：某市某区。工期：30日历天。资金来源：财政资金。"
        "投标截止时间：2026年11月30日 09:00。投标保证金：50000元。招标控制价：2000000元。"
    )
    doc.add_heading("第一章 投标人资格要求", 1)
    doc.add_paragraph(
        "1. 具有独立法人资格；2. 具有有效期内的生产许可证；"
        "3. 近三年具有同类项目业绩不少于1个；4. 不接受联合体投标。"
    )
    doc.add_heading("第二章 采购清单", 1)
    t = doc.add_table(rows=3, cols=3)
    for i, row in enumerate([["序号", "名称", "数量"], ["1", "设备A", "10台"], ["2", "设备B", "20套"]]):
        for j, cell in enumerate(row):
            t.rows[i].cells[j].text = cell
    doc.add_heading("第三章 评分办法", 1)
    doc.add_paragraph(
        "采用综合评分法，总分100分：商务分30分、技术分40分、报价分30分。"
        "投标报价高于基准价1%扣1分，低于基准价1%扣0.5分。质保期2年。"
    )
    doc.save(str(path))


class TestBidOpportunityEvaluatorRegistry(unittest.TestCase):
    def test_skill_discovered_by_registry(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        self.assertIn("bid-opportunity-evaluator", registry.list_skills())
        skill = registry.get("bid-opportunity-evaluator")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.version, "1.0.0")

    def test_skill_requires_tools_declared(self):
        from src.core.skill_registry import SkillRegistry

        registry = SkillRegistry()
        registry.load_from_directory(str(PROJECT_ROOT / "src" / "skills"))
        skill = registry.get("bid-opportunity-evaluator")
        for tool in ("read", "cp", "image_read", "knowledge_base_search", "knowledge_file_search"):
            self.assertIn(tool, getattr(skill, "requires_tools", []))

    def test_builtin_template_asset_exists(self):
        tpl = SKILL_DIR / "assets" / "投标可行性评估报告模板.docx"
        self.assertTrue(tpl.exists() and tpl.stat().st_size > 1000)

    def test_scripts_compile(self):
        for py in sorted(SCRIPTS.glob("*.py")):
            self.assertEqual(
                _run([py, "--help"]).returncode, 0, f"{py.name} --help 失败"
            )


class TestCompetitorIntelVerify(unittest.TestCase):
    def test_placeholder_names_rejected(self):
        raw = {
            "source": "测试",
            "competitors": [
                {"name": "竞品A", "type": "品牌型", "positioning": "x", "region": "y",
                 "price_band": "高档", "overlap": "高", "strength": "s", "weakness": "w",
                 "evidence": "官网"},
                {"name": "某某公司", "type": "区域型", "positioning": "x", "region": "y",
                 "price_band": "中档", "overlap": "中", "strength": "s", "weakness": "w",
                 "evidence": "中标公告"},
                {"name": "真实品牌公司", "type": "品牌型", "positioning": "x", "region": "y",
                 "price_band": "高档", "overlap": "高", "strength": "s", "weakness": "w",
                 "evidence": "中标公告"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            src = tmp / "competitors_raw.json"
            src.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            out = tmp / "competitors.json"
            r = _run([SCRIPTS / "competitor_intel.py", "verify",
                      "--input", src, "--out", out])
            self.assertEqual(r.returncode, 0, r.stderr[-500:])
            data = json.loads(out.read_text(encoding="utf-8"))
            names = [c["name"] for c in data.get("competitors", [])]
            self.assertEqual(names, ["真实品牌公司"])


class TestRunPipeline(unittest.TestCase):
    def test_end_to_end_word_report(self):
        pytest.importorskip("docx")

        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            tender = work / "tender.docx"
            _make_tender_docx(tender)
            profile = work / "company_profile.json"
            _make_company_profile(profile)
            out = work / "out"
            r = _run([SCRIPTS / "run_pipeline.py",
                      "--tender", tender, "--company", profile, "--out", out])
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            report = next((out / "04-报告").glob("投标可行性评估报告-*.docx"), None)
            self.assertIsNotNone(report, "未产出 Word 报告")
            ev = json.loads((out / "03-评估" / "evaluation.json").read_text(encoding="utf-8"))
            self.assertIn(ev["decision"]["code"], ("GO", "CAUTION", "NO_GO"))
            self.assertEqual(ev["decision"]["code"], "GO")


class TestSubagentBinding(unittest.TestCase):
    def test_ai_bid_assistant_allows_skill_and_display_name(self):
        import re

        text = SUBAGENT_MD.read_text(encoding="utf-8")
        assert "name: AI投标助手" in text
        skills_block = re.search(r"skills:\s*\n\s*allowed:\s*\n((?:\s+- .+\n)+)", text)
        self.assertIsNotNone(skills_block)
        allowed = [ln.strip().lstrip("- ").strip() for ln in skills_block.group(1).strip().splitlines()]
        self.assertIn("bid-doc-generator", allowed)
        self.assertIn("bid-opportunity-evaluator", allowed)
