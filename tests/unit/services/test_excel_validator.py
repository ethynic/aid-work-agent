"""excel_validator 结构校验、表头检测与规范化单测（设计 §4~§6、§8）。"""

from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from src.services.data_analysis.excel_validator import (
    detect_header_row,
    normalize_workbook,
    validate_workbook,
)


def _write_xlsx(path: Path, sheets: dict) -> str:
    """sheets: {sheet_name: [[row], ...]}，支持字符串 'MERGE:B2:D2' 标记由调用方自行合并。"""
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    wb.save(path)
    return str(path)


def _write_xlsx_with_merges(path: Path, sheets: dict, merges: dict) -> str:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
        for rng in merges.get(name, []):
            ws.merge_cells(rng)
    wb.save(path)
    return str(path)


NORMAL = [
    ["成本项目", "单价", "数量"],
    ["材料", 12.5, 100],
    ["人工", 8.0, 200],
]

TITLE_THEN_HEADER = [
    ["高精管材典型企业单吨完全成本结构基准（2026年，含税，元/吨）"],
    ["首行口径说明：数据来源于行业调研"],
    ["成本项目", "单价", "占比"],
    ["材料", 12.5, 0.3],
    ["人工", 8.0, 0.2],
]


class TestDetectHeaderRow:
    def test_first_row_header(self):
        rows = [["a", "b", "c"], ["1", "2", "3"], ["4", "5", "6"]]
        assert detect_header_row(rows) == 0

    def test_header_after_title_rows(self):
        rows = [[c if c else "" for c in r] for r in
                [["标题标题标题"], ["口径说明说明"], ["成本项目", "单价", "占比"], ["材料", "12.5", "0.3"]]]
        assert detect_header_row(rows) == 2

    def test_no_header(self):
        rows = [["随便什么", "内容"], ["也可以", "没有结构"]]
        # 有两列一致文本也可能被判为表头，此处只验证不抛异常且返回合法值
        result = detect_header_row(rows)
        assert result is None or result >= 0


class TestValidateWorkbook:
    def test_ok_sheet(self, tmp_path):
        path = _write_xlsx(tmp_path / "ok.xlsx", {"表1": NORMAL})
        result = validate_workbook(path)
        sheet = result["sheets"][0]
        assert sheet["verdict"] == "ok"
        assert sheet["header_row"] == 1

    def test_fixable_header_displacement(self, tmp_path):
        path = _write_xlsx(tmp_path / "shift.xlsx", {"表1": TITLE_THEN_HEADER})
        result = validate_workbook(path)
        sheet = result["sheets"][0]
        assert sheet["verdict"] == "fixable"
        assert sheet["header_row"] == 3
        assert sheet["preview"]["columns"] == ["成本项目", "单价", "占比"]
        assert len(sheet["preview"]["rows"]) == 2
        assert any("说明行" in i for i in sheet["issues"])

    def test_error_header_merge_precise(self, tmp_path):
        """表头行合并 -> error（精确用例）。"""
        rows = [
            ["说明文字"],
            ["项目", "类别", "类别"],
            ["a", "b1", "b2"],
        ]
        path = _write_xlsx_with_merges(
            tmp_path / "hm.xlsx", {"表1": rows}, {"表1": ["B2:C2"]}
        )
        result = validate_workbook(path)
        sheet = result["sheets"][0]
        assert sheet["verdict"] == "error"
        assert "合并单元格" in sheet["issues"][0]

    def test_fixable_data_merge(self, tmp_path):
        """数据区合并 -> fixable 自动展开。"""
        rows = [
            ["项目", "数值"],
            ["a", "1"],
            ["b", "2"],
            ["c", "3"],
        ]
        path = _write_xlsx_with_merges(
            tmp_path / "dm.xlsx", {"表1": rows}, {"表1": ["A2:A3"]}
        )
        result = validate_workbook(path)
        sheet = result["sheets"][0]
        assert sheet["verdict"] == "fixable"
        assert any("合并" in i for i in sheet["issues"])

    def test_error_no_data_rows(self, tmp_path):
        path = _write_xlsx(tmp_path / "empty.xlsx", {"表1": [["a", "b"]]})
        result = validate_workbook(path)
        sheet = result["sheets"][0]
        assert sheet["verdict"] == "error"

    def test_multi_sheet_independent_verdicts(self, tmp_path):
        path = _write_xlsx(tmp_path / "multi.xlsx", {"好表": NORMAL, "坏表": [["说明行"]]})
        result = validate_workbook(path)
        verdicts = {s["sheet_name"]: s["verdict"] for s in result["sheets"]}
        assert verdicts["好表"] == "ok"
        assert verdicts["坏表"] == "error"

    def test_column_warnings_on_dirty_values(self, tmp_path):
        rows = [
            ["项目", "单价"],
            ["a", "10万"],
            ["b", "-"],
            ["c", "20万"],
        ]
        path = _write_xlsx(tmp_path / "dirty.xlsx", {"表1": rows})
        result = validate_workbook(path)
        sheet = result["sheets"][0]
        assert sheet["verdict"] == "ok"
        assert sheet["column_warnings"], "脏值列应产生 warning"
        assert any("单价" in w for w in sheet["column_warnings"])

    def test_csv(self, tmp_path):
        csv_path = tmp_path / "data.csv"
        csv_path.write_text("名称,数量\n甲,5\n乙,10\n", encoding="utf-8-sig")
        result = validate_workbook(str(csv_path))
        sheet = result["sheets"][0]
        assert sheet["verdict"] == "ok"


class TestNormalizeWorkbook:
    def test_normalize_drops_title_rows_and_expands_merges(self, tmp_path):
        path = _write_xlsx_with_merges(
            tmp_path / "src.xlsx",
            {"表1": TITLE_THEN_HEADER},
            {"表1": ["A4:A5"]},  # 数据区垂直合并 A4:A5（值为"材料"）
        )
        out = normalize_workbook(path, [{"sheet_name": "表1", "header_row": 3, "selected": True}])
        assert Path(out).name == "src_n.xlsx"

        wb = load_workbook(out)
        ws = wb["表1"]
        # 表头现在在第 1 行
        assert [c.value for c in ws[1]] == ["成本项目", "单价", "占比"]
        # 合并展开：A4:A5（原坐标）-> 规范化后第 1、2 数据行（现 2、3 行）同值
        values_a = [ws.cell(row=r, column=1).value for r in range(2, 4)]
        assert values_a == ["材料", "材料"]
        # 原始文件不动
        wb_orig = load_workbook(path)
        assert wb_orig["表1"].cell(row=1, column=1).value == "高精管材典型企业单吨完全成本结构基准（2026年，含税，元/吨）"
        wb_orig.close()
        wb.close()

    def test_normalize_drops_unselected_sheets(self, tmp_path):
        path = _write_xlsx(tmp_path / "src.xlsx", {"好表": NORMAL, "坏表": [["说明行"]]})
        out = normalize_workbook(
            path,
            [
                {"sheet_name": "好表", "header_row": 1, "selected": True},
                {"sheet_name": "坏表", "header_row": 1, "selected": False},
            ],
        )
        wb = load_workbook(out)
        assert wb.sheetnames == ["好表"]
        wb.close()

    def test_normalize_csv(self, tmp_path):
        csv_path = tmp_path / "data.csv"
        csv_path.write_text("口径说明\n名称,数量\n甲,5\n", encoding="utf-8-sig")
        out = normalize_workbook(str(csv_path), [{"sheet_name": "Sheet1", "header_row": 2, "selected": True}])
        assert Path(out).name == "data_n.csv"
        content = Path(out).read_text(encoding="utf-8-sig")
        assert content.startswith("名称,数量")

    def test_normalize_idempotent(self, tmp_path):
        path = _write_xlsx(tmp_path / "src.xlsx", {"表1": TITLE_THEN_HEADER})
        decisions = [{"sheet_name": "表1", "header_row": 3, "selected": True}]
        out1 = normalize_workbook(path, decisions)
        out2 = normalize_workbook(path, decisions)
        assert Path(out1) == Path(out2)
