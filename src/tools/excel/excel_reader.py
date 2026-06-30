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


def read_all_sheets(file_path: str) -> Dict[str, Any]:
    """
    读取 Excel 所有 Sheet，返回字典行格式（与 hotel_excel_analysis.json 一致）。

    Returns:
        {
            "sheet_names": ["贵阳酒店", "安顺酒店", ...],
            "贵阳酒店": {
                "max_row": 22, "max_col": 13,
                "merged_cells": ["A23:A24", ...],
                "headers": ["市内区域", "钻级", ...],
                "rows": [{"市内区域": "云岩区", "钻级": "4钻", ...}, ...]
            },
            ...
        }
    """
    src = Path(file_path)
    if not src.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    file_type = ExcelFileHandler.detect_file_type(file_path)
    if file_type not in ("xlsx",):
        return {"success": False, "error": f"仅支持 .xlsx 格式，当前: {file_type}"}

    try:
        wb = openpyxl.load_workbook(str(src), data_only=True, read_only=True)
        result: Dict[str, Any] = {"sheet_names": wb.sheetnames}

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            max_row = ws.max_row or 0
            max_col = ws.max_column or 0

            merged = []
            if hasattr(ws, "merged_cells") and ws.merged_cells:
                for mc in ws.merged_cells.ranges:
                    merged.append(str(mc))

            headers: List[str] = []
            rows: List[Dict[str, Any]] = []

            for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
                values = [v if v is not None else "" for v in row]
                if row_idx == 1:
                    headers = [str(v).strip() if v else "" for v in values]
                else:
                    if any(v != "" and v is not None for v in values):
                        row_dict = {}
                        for col_idx, header in enumerate(headers):
                            if col_idx < len(values):
                                row_dict[header] = values[col_idx]
                        rows.append(row_dict)

            result[sheet_name] = {
                "max_row": max_row,
                "max_col": max_col,
                "merged_cells": merged,
                "headers": headers,
                "rows": rows,
            }

        wb.close()
        result["success"] = True
        return result
    except Exception as e:
        logger.error(f"[ExcelReader] read_all_sheets 失败: {e}", exc_info=True)
        return {"success": False, "error": f"读取 Excel 失败: {e}"}


def _read_csv(file_path: str) -> Dict[str, Any]:
    """读取 CSV 文件"""
    try:
        # 编码检测 fallback
        csv_rows = None
        for encoding in ("utf-8-sig", "utf-8", "gbk", "gb2312", "latin-1"):
            try:
                with open(file_path, "r", encoding=encoding) as f:
                    sample = f.read(4096)
                    f.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample)
                    except csv.Error:
                        dialect = csv.excel
                    csv_rows = list(csv.reader(f, dialect))
                    break
            except UnicodeDecodeError:
                continue
        else:
            return {"success": False, "error": "无法识别 CSV 编码格式"}

        headers = []
        rows = []
        for idx, row in enumerate(csv_rows or []):
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


def read_excel_document(file_path: str, sheet_name: Optional[str] = None) -> Dict[str, Any]:
    """
    读取 Excel 文档的完整内容，返回结构化数据 + 文本格式化内容 + 文档元信息。

    与 read_sheet() 的区别：
    - read_sheet(): 读取指定 Sheet/范围的结构化数据（headers, rows, merged_cells, formulas）
    - read_excel_document(): 读取文档级信息（全 Sheet 文本、文档元信息、行数统计）
    """
    src = Path(file_path)
    if not src.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    if src.suffix.lower() == ".xls":
        return {"success": False, "error": "目前仅支持.xlsx格式，请将.xls文件转换为.xlsx格式"}

    if src.suffix.lower() != ".xlsx":
        return {"success": False, "error": f"不支持的文件格式: {src.suffix}，仅支持.xlsx"}

    try:
        wb = openpyxl.load_workbook(str(src), data_only=True)

        # 1. 收集所有 Sheet 元信息
        sheet_names = wb.sheetnames
        sheet_count = len(sheet_names)
        sheets_info = []
        for name in sheet_names:
            ws = wb[name]
            sheets_info.append({
                "name": name,
                "title": ws.title,
                "max_row": ws.max_row,
                "max_column": ws.max_column,
            })

        # 2. 选择目标 Sheet
        target_sheet = sheet_name if sheet_name and sheet_name in sheet_names else sheet_names[0]
        if target_sheet not in sheet_names:
            wb.close()
            return {"success": False, "error": f"工作表不存在: {target_sheet}，可用: {', '.join(sheet_names)}"}

        sheet_data = _extract_sheet_data_doc(wb[target_sheet])

        # 3. 遍历所有 Sheet，格式化为固定列宽文本
        all_sheets_content = []
        for name in sheet_names:
            ws = wb[name]
            sheet_text = _format_sheet_to_text_doc(ws)
            all_sheets_content.append(f"=== 工作表: {name} ===\n{sheet_text}")

        full_content = "\n\n".join(all_sheets_content)
        total_lines = len(full_content.splitlines())

        # 4. 文档元信息
        doc_info = _extract_document_info_doc(wb, src)

        wb.close()

        return {
            "success": True,
            "message": "成功读取Excel文档",
            "file_path": str(src),
            "file_type": "xlsx",
            "sheet_count": sheet_count,
            "sheet_names": sheet_names,
            "sheets_info": sheets_info,
            "current_sheet": target_sheet,
            "sheet_data": sheet_data,
            "document_info": doc_info,
            "content": full_content,
            "total_lines": total_lines,
        }
    except Exception as e:
        logger.error(f"[ExcelReader] read_excel_document 失败: {e}", exc_info=True)
        return {"success": False, "error": f"读取Excel文档失败: {e}"}


def _extract_sheet_data_doc(sheet) -> Dict[str, Any]:
    """提取工作表结构化数据（文档级读取用）"""
    data = {
        "name": sheet.title,
        "max_row": sheet.max_row,
        "max_column": sheet.max_column,
        "headers": [],
        "rows": [],
        "data": [],
    }

    if sheet.max_row and sheet.max_row >= 1 and sheet.max_column:
        headers = []
        for col in range(1, sheet.max_column + 1):
            val = sheet.cell(1, col).value
            headers.append(str(val) if val is not None else "")
        data["headers"] = headers

        rows = []
        for row in range(1, sheet.max_row + 1):
            row_data = []
            for col in range(1, sheet.max_column + 1):
                row_data.append(sheet.cell(row, col).value)
            rows.append(row_data)
        data["rows"] = rows

        if sheet.max_row > 1:
            data["data"] = rows[1:]

    return data


def _format_sheet_to_text_doc(sheet) -> str:
    """将工作表格式化为固定列宽文本"""
    if not sheet.max_row or sheet.max_row == 0 or not sheet.max_column:
        return "(空工作表)"

    col_widths = []
    for col in range(1, sheet.max_column + 1):
        max_width = 0
        for row in range(1, sheet.max_row + 1):
            val = sheet.cell(row, col).value
            cell_str = str(val) if val is not None else ""
            max_width = max(max_width, len(cell_str))
        col_widths.append(min(max_width + 2, 50))

    text_lines = []
    for row in range(1, sheet.max_row + 1):
        row_parts = []
        for col in range(1, sheet.max_column + 1):
            val = sheet.cell(row, col).value
            cell_str = str(val) if val is not None else ""
            width = col_widths[col - 1]
            row_parts.append(cell_str.ljust(width))
        text_lines.append(" | ".join(row_parts))

    return "\n".join(text_lines)


def _extract_document_info_doc(workbook, path: Path) -> Dict[str, Any]:
    """提取文档元信息"""
    info = {
        "file_name": path.name,
        "file_size": path.stat().st_size,
        "sheet_count": len(workbook.sheetnames),
        "active_sheet": workbook.active.title if workbook.active else None,
    }

    try:
        if hasattr(workbook, "properties") and workbook.properties:
            props = workbook.properties
            info.update({
                "title": props.title or "",
                "author": props.creator or "",
                "created": str(props.created) if props.created else "",
                "modified": str(props.modified) if props.modified else "",
            })
    except Exception:
        pass

    return info
