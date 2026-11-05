"""excel/excel_reader 归一改造：read_excel_document 薄委托 + read_sheet 膨胀防护。"""

from unittest.mock import patch

import pytest

from src.tools.excel import excel_reader as er


def test_read_excel_document_delegates_to_file_reader():
    """read_excel_document 只做薄委托，参数原样透传、返回值原样返回。"""
    sentinel = {"success": True, "content": "委托结果"}
    with patch(
        "src.tools.file.excel_reader.ExcelReader.read_excel_document",
        return_value=sentinel,
    ) as mock_read:
        result = er.read_excel_document("a.xlsx", sheet_name="项目")

    assert result is sentinel
    mock_read.assert_called_once_with("a.xlsx", sheet_name="项目")


def test_read_excel_document_delegation_propagates_error():
    """委托目标返回错误时原样透传，不吞错不兜底。"""
    sentinel = {"success": False, "error": "文件不存在: a.xlsx"}
    with patch(
        "src.tools.file.excel_reader.ExcelReader.read_excel_document",
        return_value=sentinel,
    ):
        result = er.read_excel_document("a.xlsx")

    assert result == sentinel


class _StreamFakeSheet:
    """read_only 流式迭代的假工作表，仅支持 values_only 逐行生成"""

    def __init__(self, rows):
        self._rows = rows

    def iter_rows(self, values_only=False, **kwargs):
        for row in self._rows:
            yield row


def test_bounded_dimensions_stops_at_true_boundary():
    """已有数据后连续 200 空行即判定边界，早停后不扫尾部数据。"""
    rows = [["名称", None, None, "备注"], ["桥模", None, None, "A"]]
    rows.extend([None] * 4 for _ in range(300))
    rows.append(["尾部数据", None, None, None])  # 早停后不应到达

    true_rows, true_cols = er._bounded_dimensions(_StreamFakeSheet(rows))

    assert (true_rows, true_cols) == (2, 4)


def test_bounded_dimensions_respects_scan_cap(monkeypatch):
    """数据一直存在时最多扫 STREAM_MAX_SCAN_ROWS 行。"""
    rows = [[f"r{i}"] for i in range(1, 60)]
    monkeypatch.setattr(er, "STREAM_MAX_SCAN_ROWS", 10)

    true_rows, true_cols = er._bounded_dimensions(_StreamFakeSheet(rows))

    assert (true_rows, true_cols) == (10, 1)


def test_read_sheet_bounded_on_inflated_dimensions(tmp_path, monkeypatch):
    """声明维度膨胀时 read_sheet 流式探测真实边界，数据完整且不遍历百万空行。"""
    from openpyxl import Workbook

    path = tmp_path / "inflated.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["名称", "数量"])
    ws.append(["桥模", 3])
    wb.save(path)
    wb.close()

    monkeypatch.setattr(er, "MAX_NORMAL_ROWS", 1)

    result = er.read_sheet(str(path))

    assert result["success"], result
    assert result["headers"] == ["名称", "数量"]
    assert result["rows"] == [["桥模", 3]]


def test_read_sheet_normal_small_file_unchanged(tmp_path):
    """非膨胀文件 read_sheet 行为不变。"""
    from openpyxl import Workbook

    path = tmp_path / "normal.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["名称", "数量"])
    ws.append(["桥模", 3])
    ws.append(["灯塔", 5])
    wb.save(path)
    wb.close()

    result = er.read_sheet(str(path))

    assert result["success"], result
    assert result["row_count"] == 2
    assert result["rows"] == [["桥模", 3], ["灯塔", 5]]


@pytest.mark.asyncio
async def test_handle_read_invalid_sheet_returns_error_not_fallback(tmp_path):
    """归一后 sheet_name 无效返回错误（与 read 工具一致），不再静默回退第一个 Sheet。"""
    from openpyxl import Workbook

    from src.tools.excel.excel_process_tool import ExcelProcessTool, PipelineContext

    path = tmp_path / "sheets.xlsx"
    wb = Workbook()
    wb.active.title = "摘要"
    wb.active.append(["天空之桥"])
    wb.save(path)
    wb.close()

    ctx = PipelineContext(file_paths=[str(path)])
    tool = ExcelProcessTool()

    result = await tool._handle_read(ctx, {"sheet_name": "不存在"})

    assert result["success"] is False
    assert "工作表不存在" in result["error"]
