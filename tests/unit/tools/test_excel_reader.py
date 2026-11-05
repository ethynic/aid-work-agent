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
         patch.object(reader, "_is_inflated_workbook", return_value=False), \
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


def test_inflation_probe_failure_degrades_to_normal_path():
    """膨胀探测自身失败时必须按普通路径读取，不能让文件读不出来。"""
    reader = ExcelReader()
    workbook = MagicMock()
    workbook.sheetnames = ["项目"]
    with patch.object(Path, "exists", return_value=True), \
         patch.object(reader.openpyxl, "load_workbook", side_effect=[OSError("探测失败"), workbook]), \
         patch.object(reader, "_extract_sheet_data", return_value={}), \
         patch.object(reader, "_extract_document_info", return_value={}), \
         patch.object(reader, "_format_sheet_to_text", return_value="桥梁科普馆"):
        result = reader.read_excel_document("fixture.xlsx")

    assert result["success"] is True
    workbook.close.assert_called_once_with()


class _StreamFakeSheet:
    """read_only 流式迭代的假工作表，仅支持 values_only 逐行生成"""

    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, values_only=False, **kwargs):
        for row in self._rows:
            yield row


def _make_xlsx(path, rows):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    wb.save(path)
    wb.close()


def test_stream_real_rows_boundary_and_empty_tail():
    """已有数据后连续 200 空行即停止，不继续扫描尾部空行。"""
    from src.tools.file.excel_reader import _stream_real_rows

    rows = [["名称", "数量"], ["桥模", 3]]
    rows.extend([None, None] for _ in range(300))
    rows.append(["尾部数据", 1])  # 早停后不应到达

    headers, data_rows, true_max_row, truncated = _stream_real_rows(_StreamFakeSheet(rows))

    assert headers == ["名称", "数量"]
    assert data_rows == [["桥模", 3]]
    assert true_max_row == 2
    assert truncated is False


def test_stream_real_rows_scan_cap(monkeypatch):
    """数据一直存在时最多扫 STREAM_MAX_SCAN_ROWS 行并标记截断。"""
    from src.tools.file import excel_reader as fer
    from src.tools.file.excel_reader import _stream_real_rows

    rows = [[f"r{i}"] for i in range(1, 60)]
    monkeypatch.setattr(fer, "STREAM_MAX_SCAN_ROWS", 10)

    headers, data_rows, true_max_row, truncated = _stream_real_rows(_StreamFakeSheet(rows))

    assert true_max_row == 10
    assert truncated is True


def test_stream_real_rows_data_row_limit():
    """数据行达到 STREAM_MAX_DATA_ROWS 即截断，防止单 Sheet 撑爆上下文。"""
    from src.tools.file import excel_reader as fer
    from src.tools.file.excel_reader import _stream_real_rows

    rows = [["表头"]] + [[f"v{i}"] for i in range(fer.STREAM_MAX_DATA_ROWS + 10)]
    headers, data_rows, true_max_row, truncated = _stream_real_rows(_StreamFakeSheet(rows))

    assert len(data_rows) == fer.STREAM_MAX_DATA_ROWS
    assert truncated is True


def test_read_excel_document_stream_path_on_inflated(tmp_path, monkeypatch):
    """used range 膨胀文件走流式路径，返回结构与普通路径一致且数据完整。"""
    from src.tools.file import excel_reader as fer

    path = tmp_path / "inflated.xlsx"
    _make_xlsx(path, [["名称", "数量"], ["桥模", 3], ["灯塔", 5]])
    monkeypatch.setattr(fer, "MAX_NORMAL_ROWS", 2)

    result = ExcelReader().read_excel_document(str(path))

    assert result["success"], result
    assert result["sheet_data"]["rows"] == [["名称", "数量"], ["桥模", 3], ["灯塔", 5]]
    assert result["sheets_info"][0]["max_row"] == 3
    assert "桥模" in result["content"] and "灯塔" in result["content"]
    assert result["current_sheet"] == result["sheet_names"][0]
    assert result.get("truncated") is False


def test_read_excel_document_stream_truncation(tmp_path, monkeypatch):
    """数据超过流式上限时标记截断，message 提示用 range 精确读取。"""
    from src.tools.file import excel_reader as fer

    path = tmp_path / "truncated.xlsx"
    _make_xlsx(path, [["表头"]] + [[f"v{i}"] for i in range(10)])
    monkeypatch.setattr(fer, "MAX_NORMAL_ROWS", 2)
    monkeypatch.setattr(fer, "STREAM_MAX_DATA_ROWS", 3)

    result = ExcelReader().read_excel_document(str(path))

    assert result["success"], result
    assert result["truncated"] is True
    assert "截断" in result["message"] and "range" in result["message"]
    assert len(result["sheet_data"]["data"]) == 3


def test_read_excel_document_normal_path_unchanged(tmp_path):
    """非膨胀文件仍走普通路径，行为与既有契约一致。"""
    path = tmp_path / "normal.xlsx"
    _make_xlsx(path, [["名称", "数量"], ["桥模", 3]])

    result = ExcelReader().read_excel_document(str(path))

    assert result["success"], result
    assert result["sheet_data"]["rows"][1] == ["桥模", 3]
    assert "桥模" in result["content"]
    assert "truncated" not in result
