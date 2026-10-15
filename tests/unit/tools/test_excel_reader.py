"""Excel 解析必须避免动态维度的重复扫描，并释放工作簿。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.tools.file.excel_reader import ExcelReader


class _CountingSheet:
    title = "项目"

    def __init__(self):
        self.dimension_reads = {"row": 0, "column": 0}
        self.values = {(1, 1): "项目", (2, 1): "桥梁科普馆", (2, 2): 0, (2, 3): False}

    @property
    def max_row(self):
        self.dimension_reads["row"] += 1
        return 2

    @property
    def max_column(self):
        self.dimension_reads["column"] += 1
        return 3

    def cell(self, row, column):
        return SimpleNamespace(value=self.values.get((row, column)))


@pytest.mark.parametrize("method", ["_extract_sheet_data", "_format_sheet_to_text"])
def test_sheet_dimensions_are_scanned_once_per_operation(method):
    """openpyxl 动态维度扫描不能随列数重复，否则样式宽表会阻塞数分钟。"""
    reader = ExcelReader()
    sheet = _CountingSheet()
    result = getattr(reader, method)(sheet)

    assert sheet.dimension_reads == {"row": 1, "column": 1}
    if isinstance(result, dict):
        assert result["rows"][1] == ["桥梁科普馆", 0, False]
        assert result["headers"] == ["项目", "", ""]
    else:
        assert "桥梁科普馆" in result and "False" in result and "0" in result


@pytest.mark.parametrize("outcome", ["success", "missing_sheet", "parse_error"])
def test_workbook_is_closed_on_every_loaded_exit(outcome):
    """查不到指定工作表或解析抛异常时，同样必须释放已打开的工作簿。"""
    reader = ExcelReader()
    workbook = MagicMock()
    workbook.sheetnames = ["项目"]
    with patch.object(Path, "exists", return_value=True), \
         patch.object(reader.openpyxl, "load_workbook", return_value=workbook), \
         patch.object(reader, "_extract_sheet_data", return_value={}) as extract, \
         patch.object(reader, "_extract_document_info", return_value={}), \
         patch.object(reader, "_format_sheet_to_text", return_value="桥梁科普馆"):
        if outcome == "parse_error":
            extract.side_effect = ValueError("测试解析错误")
        result = reader.read_excel_document("fixture.xlsx", "不存在" if outcome == "missing_sheet" else None)

    assert result["success"] is (outcome == "success")
    if outcome == "missing_sheet":
        assert "工作表不存在" in result["error"]
    if outcome == "parse_error":
        assert "测试解析错误" in result["error"]
    workbook.close.assert_called_once_with()
