#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Excel 智能模板填充工具（Phase A）单测

覆盖：
- 行数不匹配三种（M==K / M>K / M<K）：输出无样例残留 + 数据正确
- 样式保留：非数据区字体/填充/数字格式/列宽/合并 位级一致；插入行复制行高+样式
- 合并单元格在行增删后正确重锚定
- analyze_structure（mock LLM）解析结构
- _verify_render 残留违例 fail-loud
- fill_with_sample 端到端 + 异常分支
"""

import json
import sys
from pathlib import Path

import openpyxl
import pytest
from openpyxl.styles import Font, PatternFill

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.excel.excel_template_ai import (
    ColumnBinding,
    FillData,
    SheetStructure,
    analyze_structure,
    fill_with_sample,
    _verify_render,
)


# ============================================================
# 测试样例 + 结构
# ============================================================

def _build_sample(path: Path) -> Path:
    """构造带样式的样例：标题合并 + meta + 表头(粗体填充) + 3 行明细(数字格式/行高) + 合计行"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "报价"

    ws.merge_cells("A1:E1")
    ws["A1"] = "报价单标题"
    ws["A1"].font = Font(bold=True, size=14)

    ws["A2"] = "客户："
    ws["B2"] = "（示例客户）"
    ws["D2"] = "日期："
    ws["E2"] = "2026-01-01"

    for i, h in enumerate(["类别", "项目", "单价", "数量", "金额"], 1):
        c = ws.cell(row=3, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill(fill_type="solid", start_color="4472C4", end_color="4472C4")

    example = [
        ["住宿", "酒店A", 100, 2, 200],
        ["门票", "景点B", 50, 3, 150],
        ["餐饮", "午餐C", 30, 3, 90],
    ]
    for r, row in enumerate(example, start=4):
        for i, v in enumerate(row, 1):
            c = ws.cell(row=r, column=i, value=v)
            if i in (3, 5):
                c.number_format = "#,##0.00"
    ws.row_dimensions[4].height = 30

    ws["A7"] = "合计"
    ws["E7"] = 440
    ws["E7"].number_format = "#,##0.00"
    ws.column_dimensions["A"].width = 15

    wb.save(str(path))
    return path


# 与 _build_sample 对应的结构（mock LLM 返回这个）
SAMPLE_STRUCTURE = {
    "title": {"row": 1, "col": 1, "merge": "A1:E1"},
    "meta_fields": [
        {"row": 2, "col": 2, "bind": "customer_name"},
        {"row": 2, "col": 5, "bind": "date"},
    ],
    "columns": [
        {"col": 1, "header": "类别", "bind": "category"},
        {"col": 2, "header": "项目", "bind": "name"},
        {"col": 3, "header": "单价", "bind": "unit_price"},
        {"col": 4, "header": "数量", "bind": "quantity"},
        {"col": 5, "header": "金额", "bind": "amount"},
    ],
    "detail_first_row": 4,
    "detail_last_row": 6,
    "detail_template_row": 4,
    "totals": [{"row": 7, "col": 5, "bind": "grand_total"}],
}


def _mock_llm(structure_dict=None):
    payload = json.dumps(structure_dict or SAMPLE_STRUCTURE, ensure_ascii=False)
    return lambda prompt: payload


def _data(rows, meta=None, totals=None):
    return FillData(
        rows=rows,
        meta=meta or {"customer_name": "张三", "date": "2026-08-01"},
        totals=totals or {"grand_total": 9999},
    )


# ============================================================
# 行数不匹配 + 残留 + 样式
# ============================================================


@pytest.fixture
def sample_path(tmp_path):
    return _build_sample(tmp_path / "sample.xlsx")


def test_fill_m_equals_k_no_residue(sample_path, tmp_path):
    """M==K：原位替换，无残留，样式/合并/列宽保留"""
    rows = [
        {"category": "住宿", "name": "酒店X", "unit_price": 200, "quantity": 2, "amount": 400},
        {"category": "门票", "name": "景点Y", "unit_price": 80, "quantity": 3, "amount": 240},
        {"category": "餐饮", "name": "午餐Z", "unit_price": 40, "quantity": 3, "amount": 120},
    ]
    res = fill_with_sample(
        str(sample_path), _data(rows),
        output_dir=str(tmp_path), llm_callable=_mock_llm(),
    )
    assert res["success"], res
    assert res["rows_rendered"] == 3

    wb = openpyxl.load_workbook(res["file_path"])
    ws = wb.active
    # 数据正确（无残留）
    assert ws["A4"].value == "住宿" and ws["B4"].value == "酒店X" and ws["E4"].value == 400
    assert ws["A6"].value == "餐饮" and ws["E6"].value == 120
    # 合计填入
    assert ws["E7"].value == 9999
    # meta 填入
    assert ws["B2"].value == "张三" and ws["E2"].value == "2026-08-01"
    # 样式保留：表头粗体 + 填充
    assert ws["A3"].font.bold is True
    assert ws["A3"].fill.start_color.rgb in ("004472C4", "4472C4")
    # 列宽保留
    assert ws.column_dimensions["A"].width == 15
    # 标题合并保留
    assert any(mr.min_row == 1 and mr.max_row == 1 and mr.min_col == 1 and mr.max_col == 5
               for mr in ws.merged_cells.ranges)
    # 数字格式保留
    assert ws["E4"].number_format == "#,##0.00"


def test_fill_m_greater_than_k_inserts_rows_with_style(sample_path, tmp_path):
    """M>K：插入行，续填，行高+样式复制，合计下移，合并保留"""
    rows = [
        {"category": "住宿", "name": "酒店X", "unit_price": 200, "quantity": 2, "amount": 400},
        {"category": "门票", "name": "景点Y", "unit_price": 80, "quantity": 3, "amount": 240},
        {"category": "餐饮", "name": "午餐Z", "unit_price": 40, "quantity": 3, "amount": 120},
        {"category": "用车", "name": "大巴W", "unit_price": 500, "quantity": 1, "amount": 500},
        {"category": "导游", "name": "导游V", "unit_price": 300, "quantity": 1, "amount": 300},
    ]
    res = fill_with_sample(
        str(sample_path), _data(rows),
        output_dir=str(tmp_path), llm_callable=_mock_llm(),
    )
    assert res["success"] and res["rows_rendered"] == 5

    wb = openpyxl.load_workbook(res["file_path"])
    ws = wb.active
    # 明细现在占 4-8 行，全部为 data
    assert ws["A4"].value == "住宿" and ws["A8"].value == "导游"
    assert ws["E8"].value == 300
    # 插入行（7,8）继承模板行 4 的数字格式 + 行高
    assert ws["E7"].number_format == "#,##0.00"
    assert ws["E8"].number_format == "#,##0.00"
    assert ws.row_dimensions[7].height == 30
    assert ws.row_dimensions[8].height == 30
    # 合计行原 7 → 9（delta=+2）
    assert ws["A9"].value == "合计"
    assert ws["E9"].value == 9999
    # 标题合并保留
    assert any(mr.min_row == 1 and mr.max_col == 5 for mr in ws.merged_cells.ranges)
    # 表头样式保留
    assert ws["A3"].font.bold is True


def test_fill_m_less_than_k_deletes_rows_no_residue(sample_path, tmp_path):
    """M<K：删除多余样例行，合计上移，无样例残留"""
    rows = [
        {"category": "住宿", "name": "酒店X", "unit_price": 200, "quantity": 2, "amount": 400},
        {"category": "门票", "name": "景点Y", "unit_price": 80, "quantity": 3, "amount": 240},
    ]
    res = fill_with_sample(
        str(sample_path), _data(rows),
        output_dir=str(tmp_path), llm_callable=_mock_llm(),
    )
    assert res["success"] and res["rows_rendered"] == 2

    wb = openpyxl.load_workbook(res["file_path"])
    ws = wb.active
    # 明细只剩 4-5
    assert ws["A4"].value == "住宿" and ws["A5"].value == "门票"
    # 原第 3 条样例"餐饮/午餐C/90"不得残留：合计行上移到 6
    assert ws["A6"].value == "合计"
    assert ws["E6"].value == 9999
    # 全表扫描：不应出现样例示例值"午餐C"或 90（作为餐饮行残留）
    for row in ws.iter_rows(values_only=True):
        assert "午餐C" not in [v for v in row if v is not None]
    # 90 已被替换/删除：E 列不再有 90
    e_values = [ws.cell(row=r, column=5).value for r in range(1, ws.max_row + 1)]
    assert 90 not in e_values


def test_inserted_row_inherits_border_fill(sample_path, tmp_path):
    """插入行继承模板行的填充与边框（样式完整复制）"""
    # 给模板行 4 加边框 + 填充，再验证插入行继承
    wb = openpyxl.load_workbook(str(sample_path))
    ws = wb.active
    from openpyxl.styles import Border, Side
    bd = Border(left=Side(style="thin"), right=Side(style="thin"))
    for col in range(1, 6):
        c = ws.cell(row=4, column=col)
        c.border = bd
        c.fill = PatternFill(fill_type="solid", start_color="FFF2CC", end_color="FFF2CC")
    wb.save(str(sample_path))

    rows = [
        {"category": "A", "name": "n1", "unit_price": 1, "quantity": 1, "amount": 1},
        {"category": "B", "name": "n2", "unit_price": 1, "quantity": 1, "amount": 1},
        {"category": "C", "name": "n3", "unit_price": 1, "quantity": 1, "amount": 1},
        {"category": "D", "name": "n4", "unit_price": 1, "quantity": 1, "amount": 1},
    ]
    res = fill_with_sample(
        str(sample_path), _data(rows),
        output_dir=str(tmp_path), llm_callable=_mock_llm(),
    )
    assert res["success"]
    wb2 = openpyxl.load_workbook(res["file_path"])
    ws2 = wb2.active
    # 插入行 7 应继承行 4 的填充
    assert ws2.cell(row=7, column=1).fill.start_color.rgb in ("00FFF2CC", "FFF2CC")
    assert ws2.cell(row=7, column=1).border.left.style == "thin"


# ============================================================
# analyze_structure（mock LLM）
# ============================================================


def test_analyze_structure_parses_mock_llm(sample_path):
    data = _data([{"category": "x"}])
    s = analyze_structure(str(sample_path), data, llm_callable=_mock_llm())
    assert isinstance(s, SheetStructure)
    assert s.detail_first_row == 4 and s.detail_last_row == 6
    assert len(s.columns) == 5
    assert s.columns[0].bind == "category"
    assert len(s.totals) == 1 and s.totals[0].bind == "grand_total"
    assert s.title == {"row": 1, "col": 1, "merge": "A1:E1"}


def test_analyze_structure_rejects_missing_detail(sample_path):
    """缺明细行区时 fail-loud"""
    bad = {"columns": [{"col": 1, "bind": "x"}]}  # 无 detail_first_row
    data = _data([{"x": 1}])
    with pytest.raises(ValueError):
        analyze_structure(str(sample_path), data, llm_callable=_mock_llm(bad))


def test_analyze_structure_rejects_bad_json(sample_path):
    data = _data([{"x": 1}])
    with pytest.raises(ValueError):
        analyze_structure(str(sample_path), data, llm_callable=lambda p: "不是JSON")


def test_analyze_structure_rejects_bad_group_rows(sample_path):
    """分组 detail_first_row > detail_last_row 时 fail-loud（避免 K<=0 插入超额行）"""
    bad = {
        "columns": [{"col": 1, "bind": "x"}],
        "groups": [{"name": "g", "detail_first_row": 8, "detail_last_row": 5}],
    }
    data = _data([{"x": 1}])
    with pytest.raises(ValueError):
        analyze_structure(str(sample_path), data, llm_callable=_mock_llm(bad))


def test_analyze_tolerates_null_fields_from_llm(sample_path):
    """LLM 对无表头列返回 header:null / 未绑定列 bind:null 不应崩溃（生产 trace tr_f93f6a0e 根因）"""
    structure_with_nulls = {
        "columns": [
            {"col": 1, "header": None, "bind": "category"},
            {"col": 2, "header": "项目", "bind": "name"},
            {"col": 5, "header": "金额", "bind": None},
        ],
        "detail_first_row": 4,
        "detail_last_row": 6,
        "totals": [{"row": 7, "col": 5, "bind": None}],
        "meta_fields": [{"row": 2, "col": 2, "label": None, "bind": "customer_name"}],
    }
    data = _data([{"category": "x"}])
    s = analyze_structure(str(sample_path), data, llm_callable=_mock_llm(structure_with_nulls))
    assert len(s.columns) == 3  # 三列都保留，None 被容忍
    assert s.columns[2].bind is None
    assert len(s.totals) == 1


def test_analyze_skips_malformed_records(sample_path):
    """缺必填/类型错的记录被跳过，不让单条坏数据搞垮整个分析"""
    bad = {
        "columns": [
            {"col": 1, "bind": "x"},
            {"header": "无col"},
            "not_a_dict",
            {"col": 2, "bind": "y"},
        ],
        "detail_first_row": 4,
        "detail_last_row": 6,
    }
    data = _data([{"x": 1}])
    s = analyze_structure(str(sample_path), data, llm_callable=_mock_llm(bad))
    assert len(s.columns) == 2  # 只剩 col=1 和 col=2 两条有效


# ============================================================
# 不变式校验 fail-loud
# ============================================================


def test_verify_render_raises_on_residue(tmp_path):
    """明细单元格残留样例值时 _verify_render 报错"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A4"] = "残留值"  # 与 data 不符
    structure = SheetStructure(
        columns=[ColumnBinding(col=1, bind="x")],
        detail_first_row=4, detail_last_row=4, detail_template_row=4,
    )
    data = FillData(rows=[{"x": "正确值"}])
    with pytest.raises(AssertionError):
        _verify_render(ws, structure, data)


# ============================================================
# 异常分支
# ============================================================


def test_fill_missing_sample_returns_error(tmp_path):
    res = fill_with_sample(str(tmp_path / "nope.xlsx"), _data([{"x": 1}]),
                           llm_callable=_mock_llm())
    assert not res["success"]
    assert "不存在" in res["error"]


def test_fill_empty_rows_returns_error(sample_path, tmp_path):
    res = fill_with_sample(str(sample_path), _data([]),
                           output_dir=str(tmp_path), llm_callable=_mock_llm())
    assert not res["success"]
    assert "rows" in res["error"]


# ============================================================
# 显式 columns 优先（脱离 LLM 列推断）
# ============================================================


def test_explicit_columns_override_llm(sample_path, tmp_path):
    """data.columns 显式提供时，覆盖 LLM 推断的列绑定"""
    rows = [{"cat": "住宿", "amt": 400}]
    data = FillData(
        rows=rows, meta={}, totals={},
        columns=[{"col": 1, "bind": "cat"}, {"col": 5, "bind": "amt"}],
    )
    res = fill_with_sample(
        str(sample_path), data,
        output_dir=str(tmp_path), llm_callable=_mock_llm(),
    )
    assert res["success"]
    wb = openpyxl.load_workbook(res["file_path"])
    ws = wb.active
    assert ws["A4"].value == "住宿"
    assert ws["E4"].value == 400
    # 未显式绑定的列（2,3,4）应被清空，不残留样例"酒店A/100/2"
    assert ws["B4"].value in (None, "")
    assert ws["C4"].value in (None, "")


# ============================================================
# Phase B：多分组 + 小计 + 合计 + 人均
# ============================================================


def _build_grouped_sample(path: Path) -> Path:
    """中科悦行风格：2 个分组（房餐车 / 门票），各带小计行 + 合计行 + 人均行"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "报价"

    ws.merge_cells("A1:F1")
    ws["A1"] = "文旅报价表"
    ws["A1"].font = Font(bold=True, size=14)

    ws["A2"] = "客户："
    ws["B2"] = "（示例客户）"
    ws["D2"] = "日期："
    ws["E2"] = "2026-01-01"

    for i, h in enumerate(["类别", "项目", "单价", "数量", "单位", "金额"], 1):
        c = ws.cell(row=3, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill(fill_type="solid", start_color="4472C4", end_color="4472C4")

    # 分组1：房餐车（示例 2 行）行4-5，小计行6
    g1 = [["住宿", "示例酒店A", 500, 2, "间", 1000],
          ["餐饮", "示例午餐B", 50, 3, "人", 150]]
    for r, row in enumerate(g1, start=4):
        for i, v in enumerate(row, 1):
            ws.cell(row=r, column=i, value=v)
    ws["A6"] = "房餐车小计"
    ws["F6"] = 1150
    ws["F6"].number_format = "#,##0.00"
    ws.row_dimensions[4].height = 28

    # 分组2：门票（示例 2 行）行7-8，小计行9
    g2 = [["门票/项目", "示例景点C", 160, 2, "人", 320],
          ["门票/项目", "示例项目D", 80, 2, "人", 160]]
    for r, row in enumerate(g2, start=7):
        for i, v in enumerate(row, 1):
            ws.cell(row=r, column=i, value=v)
    ws["A9"] = "门票小计"
    ws["F9"] = 480
    ws["F9"].number_format = "#,##0.00"

    # 合计行10 + 人均行11
    ws["A10"] = "合计"
    ws["F10"] = 1630
    ws["F10"].number_format = "#,##0.00"
    ws["A11"] = "成人人均"
    ws["B11"] = 815
    ws["C11"] = "儿童人均"
    ws["D11"] = 407.5

    ws.column_dimensions["A"].width = 14
    wb.save(str(path))
    return path


GROUPED_STRUCTURE = {
    "title": {"row": 1, "col": 1, "merge": "A1:F1"},
    "meta_fields": [
        {"row": 2, "col": 2, "bind": "customer_name"},
        {"row": 2, "col": 5, "bind": "date"},
    ],
    "columns": [
        {"col": 1, "header": "类别", "bind": "category"},
        {"col": 2, "header": "项目", "bind": "name"},
        {"col": 3, "header": "单价", "bind": "unit_price"},
        {"col": 4, "header": "数量", "bind": "quantity"},
        {"col": 5, "header": "单位", "bind": "unit"},
        {"col": 6, "header": "金额", "bind": "amount"},
    ],
    "groups": [
        {"name": "房餐车小计", "detail_first_row": 4, "detail_last_row": 5,
         "detail_template_row": 4, "subtotal_row": 6, "subtotal_col": 6,
         "match": {"category": ["住宿", "餐饮", "用车"]}},
        {"name": "门票小计", "detail_first_row": 7, "detail_last_row": 8,
         "detail_template_row": 7, "subtotal_row": 9, "subtotal_col": 6,
         "match": {"category": ["门票/项目"]}},
    ],
    "totals": [
        {"row": 10, "col": 6, "bind": "grand_total"},
        {"row": 11, "col": 2, "bind": "成人人均"},
        {"row": 11, "col": 4, "bind": "儿童人均"},
    ],
}


@pytest.fixture
def grouped_path(tmp_path):
    return _build_grouped_sample(tmp_path / "grouped.xlsx")


def _gdata(rows, *, group_subtotals=None, totals=None, meta=None):
    if group_subtotals is None:
        group_subtotals = {"房餐车小计": 1000, "门票小计": 200}
    if totals is None:
        totals = {"grand_total": 1200, "per_capita": {"成人人均": 600, "儿童人均": 300}}
    return FillData(
        rows=rows,
        meta=meta or {"customer_name": "张三", "date": "2026-08-01"},
        group_subtotals=group_subtotals,
        totals=totals,
    )


def test_multi_group_mixed_deltas(grouped_path, tmp_path):
    """分组1 M=3(>K=2 插入)、分组2 M=1(<K=2 删除)：小计/合计/人均正确重定位，无残留"""
    rows = [
        {"category": "住宿", "name": "酒店X", "unit_price": 200, "quantity": 2, "unit": "间", "amount": 400},
        {"category": "餐饮", "name": "午餐Y", "unit_price": 40, "quantity": 3, "unit": "人", "amount": 120},
        {"category": "用车", "name": "大巴Z", "unit_price": 480, "quantity": 1, "unit": "辆", "amount": 480},
        {"category": "门票/项目", "name": "景点W", "unit_price": 200, "quantity": 1, "unit": "团", "amount": 200},
    ]
    res = fill_with_sample(
        str(grouped_path), _gdata(rows),
        output_dir=str(tmp_path), llm_callable=_mock_llm(GROUPED_STRUCTURE),
    )
    assert res["success"] and res["rows_rendered"] == 4

    wb = openpyxl.load_workbook(res["file_path"])
    ws = wb.active

    # 分组1：3 行（4-6），末行是用车
    assert ws["A4"].value == "住宿" and ws["A5"].value == "餐饮" and ws["A6"].value == "用车"
    assert ws["F6"].value == 480
    # 分组1 小计在行 7（原 6 + 分组1 delta +1）
    assert ws["A7"].value == "房餐车小计" and ws["F7"].value == 1000
    # 分组2：1 行在行 8（原 7 + 分组1 delta +1）
    assert ws["A8"].value == "门票/项目" and ws["F8"].value == 200
    # 分组2 小计在行 9（原 9，+1-1=0 偏移）
    assert ws["A9"].value == "门票小计" and ws["F9"].value == 200
    # 合计 + 人均
    assert ws["A10"].value == "合计" and ws["F10"].value == 1200
    assert ws["B11"].value == 600 and ws["D11"].value == 300
    # meta
    assert ws["B2"].value == "张三" and ws["E2"].value == "2026-08-01"

    # 无样例残留：示例项目名/示例小计值不得出现
    all_vals = [str(c.value) for row in ws.iter_rows() for c in row if c.value is not None]
    assert not any("示例" in v for v in all_vals), "样例示例数据残留"
    # 旧样例小计值（房餐车 1150）不得残留（门票小计 480 已被 F9==200 覆盖，见上行断言）
    f6_values = [ws.cell(row=r, column=6).value for r in range(1, ws.max_row + 1)]
    assert 1150 not in f6_values

    # 样式保留：标题合并、表头粗体、小计数字格式、插入行继承行高
    assert any(mr.min_row == 1 and mr.max_col == 6 for mr in ws.merged_cells.ranges)
    assert ws["A3"].font.bold is True
    assert ws["F7"].number_format == "#,##0.00"  # 小计行数字格式（行6样式随内容移到行7）
    assert ws.row_dimensions[6].height == 28  # 插入的用车行继承模板行4的行高


def test_group_subtotals_numeric_fallback(grouped_path, tmp_path):
    """不传 group_subtotals 时，按 subtotal_col(amount) 数值求和"""
    rows = [
        {"category": "住宿", "name": "酒店X", "unit_price": 200, "quantity": 2, "unit": "间", "amount": 400},
        {"category": "餐饮", "name": "午餐Y", "unit_price": 40, "quantity": 3, "unit": "人", "amount": 120},
        {"category": "门票/项目", "name": "景点W", "unit_price": 200, "quantity": 1, "unit": "团", "amount": 200},
    ]
    data = _gdata(rows, group_subtotals={}, totals={"grand_total": 740})
    res = fill_with_sample(
        str(grouped_path), data,
        output_dir=str(tmp_path), llm_callable=_mock_llm(GROUPED_STRUCTURE),
    )
    assert res["success"]
    wb = openpyxl.load_workbook(res["file_path"])
    ws = wb.active
    # 分组1（住宿+餐饮）小计 = 400+120 = 520；M=2==K=2 无偏移，仍在行6
    assert ws["A6"].value == "房餐车小计" and ws["F6"].value == 520
    # 分组2（门票）M=1<K=2 删1行：明细上移到行7，小计行从9上移到8
    assert ws["A7"].value == "门票/项目"
    assert ws["A8"].value == "门票小计" and ws["F8"].value == 200


def test_unmatched_rows_go_to_first_group(grouped_path, tmp_path):
    """category 不匹配任何分组的行并入首组（不静默丢数据）"""
    rows = [
        {"category": "住宿", "name": "酒店X", "unit_price": 200, "quantity": 2, "unit": "间", "amount": 400},
        {"category": "未知类", "name": "神秘项", "unit_price": 10, "quantity": 1, "unit": "个", "amount": 10},
    ]
    res = fill_with_sample(
        str(grouped_path), _gdata(rows),
        output_dir=str(tmp_path), llm_callable=_mock_llm(GROUPED_STRUCTURE),
    )
    assert res["success"]
    wb = openpyxl.load_workbook(res["file_path"])
    ws = wb.active
    # 两行都进了分组1（行4-5）
    assert ws["A4"].value == "住宿" and ws["A5"].value == "未知类"


def test_groups_empty_falls_back_to_single_region(sample_path, tmp_path):
    """groups 为空时退回单明细区（Phase A 兼容路径）"""
    rows = [
        {"category": "A", "name": "n1", "unit_price": 1, "quantity": 1, "amount": 1},
        {"category": "B", "name": "n2", "unit_price": 1, "quantity": 1, "amount": 1},
    ]
    res = fill_with_sample(
        str(sample_path), _data(rows),
        output_dir=str(tmp_path), llm_callable=_mock_llm(),
    )
    assert res["success"] and res["rows_rendered"] == 2


# ============================================================
# 回归：行增删后下方已存在行的行高保留 + meta/totals 无值清空
# ============================================================


def test_shifted_rows_retain_height(sample_path, tmp_path):
    """openpyxl insert_rows 不平移 row_dimensions：下方已存在行（合计）的行高必须显式恢复。

    样例合计行（行7）有自定义行高 25；M>K 插入 2 行后合计移到行9，行高应保留。
    （修复前：row_dimensions[9].height 为 None，丢失样例行高）
    """
    wb = openpyxl.load_workbook(str(sample_path))
    ws = wb.active
    ws.row_dimensions[7].height = 25  # 合计行自定义高度
    wb.save(str(sample_path))

    rows = [
        {"category": "住宿", "name": "酒店X", "unit_price": 200, "quantity": 2, "amount": 400},
        {"category": "门票", "name": "景点Y", "unit_price": 80, "quantity": 3, "amount": 240},
        {"category": "餐饮", "name": "午餐Z", "unit_price": 40, "quantity": 3, "amount": 120},
        {"category": "用车", "name": "大巴W", "unit_price": 500, "quantity": 1, "amount": 500},
        {"category": "导游", "name": "导游V", "unit_price": 300, "quantity": 1, "amount": 300},
    ]
    res = fill_with_sample(
        str(sample_path), _data(rows),
        output_dir=str(tmp_path), llm_callable=_mock_llm(),
    )
    assert res["success"]
    wb2 = openpyxl.load_workbook(res["file_path"])
    ws2 = wb2.active
    # 合计行原 7 → 9（delta=+2），行高必须保留
    assert ws2["A9"].value == "合计"
    assert ws2.row_dimensions[9].height == 25, "下方已存在行的行高在 insert_rows 后丢失"


def test_meta_totals_cleared_when_not_provided(sample_path, tmp_path):
    """caller 未提供 meta/totals 值时，样例值必须清空（无残留契约）。

    样例 B2=（示例客户）、E7=440；data.meta={}、data.totals={}。
    修复前：B2 残留"（示例客户）"、E7 残留 440。
    """
    rows = [
        {"category": "住宿", "name": "酒店X", "unit_price": 200, "quantity": 2, "amount": 400},
        {"category": "门票", "name": "景点Y", "unit_price": 80, "quantity": 3, "amount": 240},
        {"category": "餐饮", "name": "午餐Z", "unit_price": 40, "quantity": 3, "amount": 120},
    ]
    data = FillData(rows=rows, meta={}, totals={})
    res = fill_with_sample(
        str(sample_path), data,
        output_dir=str(tmp_path), llm_callable=_mock_llm(),
    )
    assert res["success"]
    wb = openpyxl.load_workbook(res["file_path"])
    ws = wb.active
    # B2 (customer_name meta) 清空，不残留"（示例客户）"
    assert ws["B2"].value in (None, ""), f"meta 残留: B2={ws['B2'].value!r}"
    # E7 (grand_total total) 清空，不残留 440
    assert ws["E7"].value in (None, ""), f"totals 残留: E7={ws['E7'].value!r}"
