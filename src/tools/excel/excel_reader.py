"""
Excel 数据读取

读取 Excel 文件的指定 Sheet 和范围，返回结构化数据。
"""

import csv
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

import openpyxl
from loguru import logger

from src.tools.excel.excel_lib import ExcelFileHandler, parse_range


def read_sheet(file_path: str, sheet_name: Optional[str] = None,
               cell_range: Optional[str] = None,
               include_formulas: bool = False) -> Dict[str, Any]:
    """
    读取 Excel 文件数据。

    Returns:
        {
            "success": True,
            "file_name": "report.xlsx",
            "sheets": ["Sheet1", "Sheet2"],
            "active_sheet": "Sheet1",
            "headers": ["Name", "Amount", "Date"],
            "rows": [["张三", 1000, "2026-01-01"], ...],
            "row_count": 100,
            "column_count": 5,
            "merged_cells": ["A1:C1", ...],
            "formulas": {"B5": "=SUM(B2:B4)"}  # only if include_formulas=True
        }
    """
    src = Path(file_path)
    if not src.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    file_type = ExcelFileHandler.detect_file_type(file_path)

    if file_type == "csv":
        return _read_csv(file_path)
    elif file_type == "xls":
        return {"success": False, "error": "不支持 .xls 格式，请转换为 .xlsx 后重试"}
    elif file_type != "xlsx":
        return {"success": False, "error": f"不支持的文件格式: {file_type}"}

    try:
        data_only = not include_formulas
        wb = openpyxl.load_workbook(str(src), data_only=data_only, read_only=True)

        sheets = wb.sheetnames
        if sheet_name and sheet_name in sheets:
            ws = wb[sheet_name]
        else:
            ws = wb.active
            sheet_name = ws.title

        # 确定读取范围
        if cell_range:
            (min_col, min_row), (max_col, max_row) = parse_range(cell_range)
        else:
            min_col, min_row = 1, 1
            max_col, max_row = ws.max_column or 1, ws.max_row or 1

        # 读取数据
        headers = []
        rows = []
        formulas = {} if include_formulas else None

        for row_idx, row in enumerate(ws.iter_rows(
            min_row=min_row, max_row=max_row,
            min_col=min_col, max_col=max_col,
        ), start=1):
            values = []
            for cell in row:
                val = cell.value
                if val is None:
                    val = ""
                values.append(val)

                if include_formulas and cell.value and isinstance(cell.value, str) and cell.value.startswith("="):
                    formulas[f"{cell.column_letter}{cell.row}"] = cell.value

            if row_idx == 1:
                headers = [str(v) if v else "" for v in values]
            else:
                # 跳过全空行
                if any(v != "" and v is not None for v in values):
                    rows.append(values)

        # 获取合并单元格信息
        merged = []
        if hasattr(ws, "merged_cells") and ws.merged_cells:
            for mc in ws.merged_cells.ranges:
                merged.append(str(mc))

        wb.close()

        return {
            "success": True,
            "file_name": src.name,
            "sheets": sheets,
            "active_sheet": sheet_name,
            "headers": headers,
            "rows": rows,
            "row_count": len(rows),
            "column_count": len(headers),
            "merged_cells": merged,
            **({"formulas": formulas} if include_formulas else {}),
        }
    except Exception as e:
        logger.error(f"[ExcelReader] 读取失败: {e}", exc_info=True)
        return {"success": False, "error": f"读取 Excel 失败: {e}"}


def _read_csv(file_path: str) -> Dict[str, Any]:
    """读取 CSV 文件"""
    try:
        # 编码检测 fallback
        for encoding in ("utf-8-sig", "utf-8", "gbk", "gb2312", "latin-1"):
            try:
                with open(file_path, "r", encoding=encoding) as f:
                    sample = f.read(4096)
                    f.seek(0)
                    sniffer = csv.Sniffer()
                    dialect = sniffer.sniff(sample)
                    reader = csv.reader(f, dialect)
                    break
            except (UnicodeDecodeError, csv.Error):
                continue
        else:
            return {"success": False, "error": "无法识别 CSV 编码格式"}

        headers = []
        rows = []
        for idx, row in enumerate(reader):
            if idx == 0:
                headers = row
            else:
                if any(cell.strip() for cell in row):
                    rows.append(row)

        return {
            "success": True,
            "file_name": Path(file_path).name,
            "sheets": ["CSV"],
            "active_sheet": "CSV",
            "headers": headers,
            "rows": rows,
            "row_count": len(rows),
            "column_count": len(headers),
            "merged_cells": [],
        }
    except Exception as e:
        logger.error(f"[ExcelReader] CSV 读取失败: {e}", exc_info=True)
        return {"success": False, "error": f"读取 CSV 失败: {e}"}
