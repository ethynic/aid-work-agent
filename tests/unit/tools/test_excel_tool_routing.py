import sys
from pathlib import Path

import openpyxl
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.asyncio
async def test_excel_router_exports_markdown_table_without_llm():
    from src.tools.excel.excel_router import ExcelRouter

    router = ExcelRouter()
    context = """请导出Excel文件

| 类别 | 项目 | 金额 |
|------|------|------|
| 门票 | 成人票 | 120 |
"""

    result = await router.route(context)

    assert result["task"] == "export"
    assert result["params"]["data_type"] == "markdown"


@pytest.mark.asyncio
async def test_excel_router_converts_csv_attachment_without_llm(tmp_path):
    from src.tools.excel.excel_router import ExcelRouter

    csv_path = tmp_path / "quote.csv"
    csv_path.write_text("项目,金额\n门票,120\n", encoding="utf-8")

    result = await ExcelRouter().route("有EXCEL格式吗", [str(csv_path)])

    assert result["task"] == "convert"
    assert result["params"]["source_format"] == "csv"
    assert result["params"]["target_format"] == "excel"
    assert result["params"]["output_name"] == "quote.xlsx"


def test_convert_csv_to_excel_creates_readable_workbook(tmp_path, monkeypatch):
    from src.tools.excel.excel_lib import ExcelFileHandler
    from src.tools.excel.excel_writer import convert_format

    csv_path = tmp_path / "quote.csv"
    csv_path.write_text("项目,金额\n门票,120\n住宿,300\n", encoding="utf-8")
    monkeypatch.setattr(ExcelFileHandler, "get_session_dir", staticmethod(lambda: tmp_path))

    result = convert_format(str(csv_path), target_format="excel", output_name="quote.xlsx")

    assert result["success"] is True
    assert result["file_name"] == "quote.xlsx"

    wb = openpyxl.load_workbook(result["file_path"])
    ws = wb.active
    assert ws["A1"].value == "项目"
    assert ws["B1"].value == "金额"
    assert ws["A2"].value == "门票"
    assert ws["B2"].value == 120
    wb.close()


def test_excel_save_temp_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    from src.tools.excel.excel_lib import ExcelFileHandler
    from src.tools.excel.excel_writer import create_excel

    monkeypatch.setattr(ExcelFileHandler, "get_session_dir", staticmethod(lambda: tmp_path))

    first = create_excel(
        data="| 项目 | 金额 |\n|------|------|\n| 门票 | 120 |",
        data_type="markdown",
        file_name="quote.xlsx",
    )
    second = create_excel(
        data="| 项目 | 金额 |\n|------|------|\n| 住宿 | 300 |",
        data_type="markdown",
        file_name="quote.xlsx",
    )

    assert first["success"] is True
    assert second["success"] is True
    assert first["file_path"] != second["file_path"]
    assert Path(first["file_path"]).exists()
    assert Path(second["file_path"]).exists()


def test_create_excel_writes_currency_text_as_number(tmp_path, monkeypatch):
    from src.tools.excel.excel_lib import ExcelFileHandler
    from src.tools.excel.excel_writer import create_excel

    monkeypatch.setattr(ExcelFileHandler, "get_session_dir", staticmethod(lambda: tmp_path))

    result = create_excel(
        data="| 项目 | 金额 |\n|------|------|\n| 合计 | ¥1,446.07 |",
        data_type="markdown",
        file_name="amount.xlsx",
    )

    assert result["success"] is True

    wb = openpyxl.load_workbook(result["file_path"])
    ws = wb.active
    assert ws["B2"].value == 1446.07
    wb.close()
