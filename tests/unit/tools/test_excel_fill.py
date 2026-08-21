"""
excel_fill（Excel ETL M5 后半：纯代码填充 + 校验报告 + D17 命名）单测

覆盖（D13/D15/D16/D17）：
- 黄金 14 条写入标准模板 → openpyxl 读回字段级全等（含 D13 排序）
- 同人多条备注标记落表（annotate_same_person 产出备注随记录写入）
- 终检：行数 / 表头完整 / 无残留行
- 未匹配模板列 → warning
- 样式复制冒烟：表头行 border/font 存在于数据行
- D17 命名
"""

import importlib.util
import json
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest

from src.tools.excel.excel_extract import annotate_same_person, load_default_schema
from src.tools.excel.excel_fill import (
    fill_template,
    generate_report,
    output_names,
    sort_records,
)

pytestmark = [pytest.mark.tools]

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "excel_etl"
TEMPLATE_XLSX = FIXTURE_DIR / "生成标准模板格式.xlsx"


@pytest.fixture(scope="module")
def schema():
    return load_default_schema()


@pytest.fixture(scope="module")
def golden():
    return json.loads((FIXTURE_DIR / "golden_records.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def template():
    """模板 xlsx（.gitignore 排除，确定性重建）"""
    if not TEMPLATE_XLSX.exists():
        spec = importlib.util.spec_from_file_location(
            "build_fixtures", FIXTURE_DIR / "build_fixtures.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.build_standard_template()
    return TEMPLATE_XLSX


# ============================================================
# 黄金 14 条填充 → 读回字段级全等
# ============================================================


class TestFillGolden:
    def test_fill_golden_records_field_equal(self, template, schema, golden, tmp_path):
        """黄金 14 条写入模板 → 读回 18 字段逐条全等（含 D13 排序后的行序）"""
        out = tmp_path / "汇总.xlsx"
        result = fill_template(template, golden, schema, output_path=out)
        assert result["success"] is True
        assert result["written"] == 14
        assert result["warnings"] == []
        assert all(result["checks"].values())

        field_names = [f["name"] for f in schema["fields"]]
        wb = openpyxl.load_workbook(out)
        ws = wb.worksheets[0]

        # 列绑定：首行表头 → schema 字段名
        header_map = {str(c.value).strip(): c.column for c in ws[1]}
        rows = [
            {name: ws.cell(row=r, column=header_map[name]).value for name in field_names}
            for r in range(2, ws.max_row + 1)
        ]
        assert len(rows) == 14

        # 排序后逐条全等（None 与 "" 统一视为空）
        ordered = sort_records(golden)
        for expect, actual in zip(ordered, rows):
            for name in field_names:
                ev, av = expect.get(name) or None, actual.get(name) or None
                assert ev == av, f"{expect.get('姓名')}.{name}: {ev!r} != {av!r}"

    def test_d13_ordering_addition_before_reduction(self, template, schema, golden, tmp_path):
        """D13 排序：增员在前减员在后，组内按（来源文件名, 行号）稳定排序"""
        ordered = sort_records(golden)
        types = [r["增减类型"] for r in ordered]
        assert types == sorted(types, key=lambda t: {"增员": 0, "减员": 1}[t])
        # 增员 6 条（德勤2+万宝2+上海2）、减员 8 条
        assert types.count("增员") == 6 and types.count("减员") == 8
        # 组内来源有序：增员组首条来自德勤（文件名序最前）；
        # 减员组末条来自文件名序最后的来源（"活" > "外" > "上" > "万" > "2026..."）
        assert ordered[0]["_source"].startswith("202608德勤派单增减人员")
        assert ordered[-1]["_source"].startswith("活悦安徽分增减表")

        out = tmp_path / "排序.xlsx"
        fill_template(template, golden, schema, output_path=out)
        wb = openpyxl.load_workbook(out)
        ws = wb.worksheets[0]
        written_types = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
        assert written_types == types

    def test_duplicate_person_note_written_to_sheet(self, template, schema, golden, tmp_path):
        """同人多条：跨来源同人备注追加【同人多条：另见 X】并落表"""
        dup = golden + [dict(golden[0], _source="另一样本.xlsx#新增#5")]
        annotated = annotate_same_person(dup)
        assert len(annotated["duplicate_groups"]) == 1

        out = tmp_path / "同人.xlsx"
        fill_template(template, annotated["records"], schema, output_path=out)
        wb = openpyxl.load_workbook(out)
        ws = wb.worksheets[0]
        header_map = {str(c.value).strip(): c.column for c in ws[1]}
        notes = [
            ws.cell(row=r, column=header_map["备注"]).value
            for r in range(2, ws.max_row + 1)
            if ws.cell(row=r, column=header_map["姓名"]).value == golden[0]["姓名"]
        ]
        # 两条同人多带标记；golden[0] 原备注为 None → 备注即标记本身
        assert len(notes) == 2
        assert all(n and "【同人多条：另见" in n for n in notes)

    def test_final_checks_fail_on_residual_rows(self, template, schema, golden, tmp_path):
        """终检失败路径：模板尾行残留占位行（写入区之外）→ checks 失败 + success False"""
        wb = openpyxl.load_workbook(template)
        ws = wb.worksheets[0]
        for col in range(1, 19):
            ws.cell(row=20, column=col, value=f"占位{col}")  # 1+14 行之外的残留行
        dirty = tmp_path / "dirty_template.xlsx"
        wb.save(dirty)
        wb.close()

        result = fill_template(dirty, golden, schema, output_path=tmp_path / "out.xlsx")
        assert result["success"] is False
        assert result["checks"]["row_count_ok"] is False
        assert result["checks"]["no_residual_rows"] is False
        # 表头仍然完整（残留行不影响表头检查）
        assert result["checks"]["headers_complete"] is True

    def test_unmatched_column_reported_as_warning(self, template, schema, golden, tmp_path):
        """未匹配列：模板加一列未知表头 → warning 提示，不阻断填充"""
        wb = openpyxl.load_workbook(template)
        wb.worksheets[0].cell(row=1, column=19, value="神秘列\n（说明）")
        variant = tmp_path / "extra_col.xlsx"
        wb.save(variant)
        wb.close()

        result = fill_template(variant, golden, schema, output_path=tmp_path / "out.xlsx")
        assert result["success"] is True
        assert any("神秘列" in w for w in result["warnings"])
        assert "模板列未匹配到 schema 字段" in result["warnings"][0]

    def test_header_newline_normalized_for_binding(self, template, schema, golden, tmp_path):
        """表头含换行/空白的别名匹配：改写首列表头为"增减 类型\\n(增/减)"（别名数组外）不匹配，
        但 schema 主表头带换行变体（去空白后相同）应能绑定——用别名"类型"验证规范化"""
        wb = openpyxl.load_workbook(template)
        ws = wb.worksheets[0]
        ws.cell(row=1, column=1, value="增减\n类型")  # 去空白后 == "增减类型"
        variant = tmp_path / "nl_header.xlsx"
        wb.save(variant)
        wb.close()

        result = fill_template(variant, golden, schema, output_path=tmp_path / "out.xlsx")
        assert result["success"] is True
        assert result["warnings"] == []
        # 第一列仍绑定增减类型：读回第 2 行首列为第一条记录的类型
        wb2 = openpyxl.load_workbook(tmp_path / "out.xlsx")
        assert wb2.worksheets[0].cell(row=2, column=1).value == "增员"

    def test_style_copied_from_header_row(self, template, schema, golden, tmp_path):
        """样式复制冒烟：表头行 border/font 复制到数据行"""
        from openpyxl.styles import Font, Side, Border

        wb = openpyxl.load_workbook(template)
        ws = wb.worksheets[0]
        side = Side(style="thin")
        for c in ws[1]:
            c.border = Border(left=side, right=side, top=side, bottom=side)
            c.font = Font(name="仿宋", size=11, bold=True)
        styled = tmp_path / "styled.xlsx"
        wb.save(styled)
        wb.close()

        fill_template(styled, golden, schema, output_path=tmp_path / "out.xlsx")
        wb2 = openpyxl.load_workbook(tmp_path / "out.xlsx")
        ws2 = wb2.worksheets[0]
        for row in (2, 15):  # 首末数据行
            for col in (1, 9, 18):
                cell = ws2.cell(row=row, column=col)
                assert cell.border.left.style == "thin", f"R{row}C{col} 边框缺失"
                assert cell.font.name == "仿宋" and cell.font.bold is True, f"R{row}C{col} 字体缺失"

    def test_empty_records_fill_only_header(self, template, schema, tmp_path):
        """空 records：只保留表头行，终检通过"""
        result = fill_template(template, [], schema, output_path=tmp_path / "empty.xlsx")
        assert result["success"] is True
        assert result["written"] == 0
        wb = openpyxl.load_workbook(tmp_path / "empty.xlsx")
        assert (wb.worksheets[0].max_row or 1) == 1

    def test_merged_cells_unmerged_before_write(self, template, schema, golden, tmp_path):
        """合并单元格模板不再崩溃：跨数据区合并先拆分再写入（原值保留在左上角），
        带 warning 提示；表头区横向合并不受影响"""
        wb = openpyxl.load_workbook(template)
        ws = wb.worksheets[0]
        # 表头纵向合并（1-2 行，旧实现直接 AttributeError: MergedCell value 只读）
        ws.merge_cells(start_row=1, start_column=1, end_row=2, end_column=1)
        # 数据区纵向合并（值在锚点，若跳过写入会静默丢第 2 行值）
        ws.merge_cells(start_row=2, start_column=2, end_row=3, end_column=2)
        merged_tpl = tmp_path / "merged.xlsx"
        wb.save(merged_tpl)
        wb.close()

        result = fill_template(
            merged_tpl, golden[:2], schema, output_path=tmp_path / "out.xlsx"
        )
        assert result["success"] is True
        assert any("合并单元格" in w for w in result["warnings"])
        wb2 = openpyxl.load_workbook(tmp_path / "out.xlsx")
        ws2 = wb2.worksheets[0]
        # 两行数据都完整写入（合并拆分后每行独立值）
        assert ws2.cell(row=2, column=1).value == golden[:2][0]["增减类型"]
        assert ws2.cell(row=3, column=1).value == golden[:2][1]["增减类型"]
        assert ws2.cell(row=2, column=2).value == golden[:2][0]["姓名"]
        assert ws2.cell(row=3, column=2).value == golden[:2][1]["姓名"]


# ============================================================
# D16 报告
# ============================================================


class TestGenerateReport:
    def _bundle(self, golden):
        return {
            "template_name": "生成标准模板格式.xlsx",
            "xlsx_path": "/tmp/x.xlsx",
            "sources": [
                {"file": "a.xlsx", "extracted": 8, "written": 8, "skipped": 0},
                {"file": "b.xlsx", "extracted": 6, "written": 5, "skipped": 1},
            ],
            "records": golden,
            "record_warnings": [["身份证为脱敏形式: 340203********1234"]] + [[]] * 13,
            "manual_review": [{
                "record": {"姓名": "坏行", "身份证": "340203199003074258"},
                "errors": ["身份证校验位错误: 340203199003074258"],
                "warnings": [],
                "source_row": "| 1 | 坏行 | 340203199003074258 |",
                "_source": "b.xlsx#减员#9",
            }],
            "duplicate_groups": [{
                "key": "340203199003074259",
                "members": [
                    {"姓名": "张伟", "_source": "a.xlsx#新增#32"},
                    {"姓名": "张伟", "_source": "c.xlsx#增员#2"},
                ],
            }],
            "metering": {"llm_calls": 9, "prompt_tokens": 9000, "completion_tokens": 1800,
                         "total_tokens": 10800, "credit": 2.5},
            "fill_warnings": [],
        }

    def test_report_sections(self, golden, tmp_path):
        out = tmp_path / "报告.md"
        content = generate_report(self._bundle(golden), output_path=out)
        assert out.exists() and out.read_text(encoding="utf-8") == content
        # 摘要：各来源数与合计
        assert "| a.xlsx | 8 | 8 | 0 |" in content
        assert "| **合计** | **14** | **13** | **1** |" in content
        # 明细：逐行 warning + 人工清单 error + 原始行
        assert "身份证为脱敏形式" in content
        assert "身份证校验位错误" in content
        assert "| 1 | 坏行 | 340203199003074258 |" in content
        # 同人多条分组
        assert "同人多条" in content and "a.xlsx#新增#32" in content and "c.xlsx#增员#2" in content
        # 计量小计
        assert "LLM 调用次数：9" in content
        assert "2.5000" in content

    def test_report_without_metering_or_issues(self, golden, tmp_path):
        """无可选段：计量缺省/零 warning 时报告仍完整可读"""
        bundle = self._bundle(golden)
        bundle["metering"] = None
        bundle["record_warnings"] = [[]] * 14
        bundle["manual_review"] = []
        bundle["duplicate_groups"] = []
        content = generate_report(bundle, output_path=tmp_path / "r.md")
        assert "计量小计" not in content
        assert "全部记录校验通过" in content
        assert "无同人多条记录" in content


# ============================================================
# D17 命名
# ============================================================


class TestOutputNames:
    def test_output_names_pattern(self):
        now = datetime(2026, 8, 19, 14, 30)
        xlsx, md = output_names("生成标准模板格式.xlsx", now=now)
        assert xlsx == "生成标准模板格式_汇总_20260819-1430.xlsx"
        assert md == "生成标准模板格式_校验报告_20260819-1430.md"

    def test_output_names_default_now(self):
        xlsx, md = output_names("模板")
        assert xlsx.endswith(".xlsx") and "_汇总_" in xlsx
        assert md.endswith(".md") and "_校验报告_" in md

    def test_output_names_empty(self):
        xlsx, md = output_names("")
        assert xlsx.startswith("模板_汇总_")
