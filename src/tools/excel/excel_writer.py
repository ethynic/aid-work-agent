"""
Excel 创建/导出

从结构化数据（Markdown、CSV、JSON、表格）创建格式化的 Excel 文件。
"""

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import openpyxl
from loguru import logger

from src.tools.excel.excel_lib import (
    ExcelFileHandler,
    apply_table_style,
    auto_column_width,
)


def create_excel(data: Any, data_type: str = "markdown",
                 file_name: Optional[str] = None,
                 sheet_name: str = "Sheet1",
                 auto_format: bool = True) -> Dict[str, Any]:
    """
    从结构化数据创建 Excel 文件。

    data_type 支持：markdown, csv, json, table, dict_list

    Returns:
        {"success": True, "file_path": "...", "file_name": "...", "row_count": N, ...}
    """
    try:
        if data_type == "markdown":
            headers, rows = _parse_markdown_table(data)
        elif data_type == "csv":
            headers, rows = _parse_csv_text(data)
        elif data_type == "json":
            headers, rows = _parse_json_array(data)
        elif data_type == "table":
            headers, rows = _parse_table(data)
        elif data_type == "dict_list":
            headers, rows = _parse_dict_list(data)
        else:
            return {"success": False, "error": f"不支持的数据类型: {data_type}"}

        if not headers and not rows:
            return {"success": False, "error": "解析数据为空，无法生成 Excel"}

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = sheet_name[:31]  # Excel sheet name 限制 31 字符

        # 写入表头
        for col_idx, header in enumerate(headers, 1):
            ws.cell(row=1, column=col_idx, value=str(header) if header else "")

        # 写入数据
        for row_idx, row_data in enumerate(rows, 2):
            for col_idx, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                _set_cell_value(cell, val)

        # 自动格式化
        if auto_format and headers:
            apply_table_style(ws, header_row=1, data_start_row=2, data_end_row=len(rows) + 1)
            auto_column_width(ws)

        # 保存
        output_name = file_name or "export.xlsx"
        save_result = ExcelFileHandler.save_temp(wb, file_name=output_name)
        wb.close()

        return {
            "success": True,
            "file_path": save_result["file_path"],
            "file_name": Path(save_result["file_path"]).name,
            "file_size": save_result["file_size"],
            "sheet_name": sheet_name,
            "row_count": len(rows),
            "column_count": len(headers),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"[ExcelWriter] 创建 Excel 失败: {e}")
        return {"success": False, "error": f"创建 Excel 失败: {e}"}


def write_multi_sheet(sheets_data: Dict[str, Any], file_name: Optional[str] = None) -> Dict[str, Any]:
    """
    创建多 Sheet 的 Excel 文件。

    sheets_data: {"销售数据": markdown_str, "汇总": json_arr, ...}
    """
    try:
        wb = openpyxl.Workbook()
        first = True

        for name, data in sheets_data.items():
            if first:
                ws = wb.active
                ws.title = name[:31]
                first = False
            else:
                ws = wb.create_sheet(title=name[:31])

            # 自动检测数据类型
            if isinstance(data, list) and data and isinstance(data[0], dict):
                headers, rows = _parse_dict_list(data)
            elif isinstance(data, str) and "|" in data:
                headers, rows = _parse_markdown_table(data)
            elif isinstance(data, list) and data and isinstance(data[0], list):
                headers, rows = _parse_table(data)
            else:
                continue

            # 写入
            for col_idx, header in enumerate(headers, 1):
                ws.cell(row=1, column=col_idx, value=str(header) if header else "")
            for row_idx, row_data in enumerate(rows, 2):
                for col_idx, val in enumerate(row_data, 1):
                    cell = ws.cell(row=row_idx, column=col_idx)
                    _set_cell_value(cell, val)

            if headers:
                apply_table_style(ws, header_row=1, data_start_row=2, data_end_row=len(rows) + 1)
                auto_column_width(ws)

        output_name = file_name or "export.xlsx"
        save_result = ExcelFileHandler.save_temp(wb, file_name=output_name)
        wb.close()

        return {
            "success": True,
            "file_path": save_result["file_path"],
            "file_name": Path(save_result["file_path"]).name,
            "file_size": save_result["file_size"],
            "sheet_count": len(sheets_data),
        }
    except Exception as e:
        logger.opt(exception=True).error(f"[ExcelWriter] 多 Sheet 创建失败: {e}")
        return {"success": False, "error": f"创建多 Sheet Excel 失败: {e}"}


def merge_files(file_paths: List[str], output_name: Optional[str] = None,
                merge_mode: str = "sheets") -> Dict[str, Any]:
    """
    合并多个 Excel/CSV 文件。

    merge_mode: sheets（每个文件一个 Sheet）| rows（合并行）
    """
    if not file_paths or len(file_paths) < 2:
        return {"success": False, "error": "合并需要至少 2 个文件"}

    try:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        all_headers = None
        all_rows = []

        for fp in file_paths:
            from src.tools.excel.excel_reader import read_sheet
            result = read_sheet(fp)
            if not result.get("success"):
                logger.warning(f"[ExcelWriter] 合并时跳过文件: {fp}, 原因: {result.get('error')}")
                continue

            if merge_mode == "sheets":
                name = Path(fp).stem[:31]
                ws = wb.create_sheet(title=name)
                headers = result.get("headers", [])
                rows = result.get("rows", [])

                for col_idx, h in enumerate(headers, 1):
                    ws.cell(row=1, column=col_idx, value=h)
                for row_idx, row in enumerate(rows, 2):
                    for col_idx, val in enumerate(row, 1):
                        _set_cell_value(ws.cell(row=row_idx, column=col_idx), val)

                if headers:
                    apply_table_style(ws, header_row=1, data_start_row=2, data_end_row=len(rows) + 1)
                    auto_column_width(ws)
            else:  # rows 模式
                if all_headers is None:
                    all_headers = result.get("headers", [])
                all_rows.extend(result.get("rows", []))

        if merge_mode == "rows" and all_headers:
            ws = wb.create_sheet(title="合并数据")
            for col_idx, h in enumerate(all_headers, 1):
                ws.cell(row=1, column=col_idx, value=h)
            for row_idx, row in enumerate(all_rows, 2):
                for col_idx, val in enumerate(row, 1):
                    _set_cell_value(ws.cell(row=row_idx, column=col_idx), val)
            apply_table_style(ws, header_row=1, data_start_row=2, data_end_row=len(all_rows) + 1)
            auto_column_width(ws)

        output_name = output_name or "merged.xlsx"
        save_result = ExcelFileHandler.save_temp(wb, file_name=output_name)
        wb.close()

        return {
            "success": True,
            "file_path": save_result["file_path"],
            "file_name": Path(save_result["file_path"]).name,
            "file_size": save_result["file_size"],
            "merged_files": len(file_paths),
            "merge_mode": merge_mode,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"[ExcelWriter] 合并失败: {e}")
        return {"success": False, "error": f"合并文件失败: {e}"}


def convert_format(file_path: str, target_format: str = "xlsx",
                   output_name: Optional[str] = None) -> Dict[str, Any]:
    """
    文件格式转换：CSV ↔ Excel、JSON ↔ Excel
    """
    src = Path(file_path)
    file_type = ExcelFileHandler.detect_file_type(file_path)

    try:
        if file_type == "csv" and target_format in ("excel", "xlsx"):
            from src.tools.excel.excel_reader import read_sheet
            result = read_sheet(file_path)
            if not result.get("success"):
                return result
            headers, rows = result["headers"], result["rows"]
            name = output_name or (src.stem + ".xlsx")
            return create_excel(data=[headers] + rows, data_type="table", file_name=name)

        elif file_type == "json" and target_format in ("excel", "xlsx"):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            name = output_name or (src.stem + ".xlsx")
            if isinstance(data, list):
                return create_excel(data=data, data_type="dict_list", file_name=name)
            return {"success": False, "error": "JSON 文件内容不是数组，无法转为 Excel"}

        elif file_type in ("xlsx", "excel") and target_format == "csv":
            wb, _ = ExcelFileHandler.copy_and_open(file_path)
            ws = wb.active
            name = output_name or (src.stem + ".csv")
            save_dir = ExcelFileHandler.get_session_dir()
            save_dir.mkdir(parents=True, exist_ok=True)
            out_path = save_dir / name

            with open(str(out_path), "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                for row in ws.iter_rows(values_only=True):
                    writer.writerow([str(v) if v is not None else "" for v in row])
            wb.close()

            return {
                "success": True,
                "file_path": str(out_path.absolute()),
                "file_name": name,
                "file_size": out_path.stat().st_size,
            }
        else:
            return {"success": False, "error": f"不支持的转换: {file_type} → {target_format}"}
    except Exception as e:
        logger.opt(exception=True).error(f"[ExcelWriter] 格式转换失败: {e}")
        return {"success": False, "error": f"格式转换失败: {e}"}


# ── 内部解析函数 ──


def _parse_markdown_table(text: str):
    """解析 Markdown 表格文本"""
    if not text:
        return [], []

    lines = text.strip().split("\n")
    headers = []
    rows = []

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|") or not stripped.endswith("|"):
            continue

        # 跳过分隔行
        if re.match(r"^\|[\s\-:|]+\|$", stripped):
            continue

        cells = [c.strip() for c in stripped.split("|")[1:-1]]

        if not headers:
            headers = cells
        else:
            rows.append(cells)

    return headers, rows


def _parse_csv_text(text: str):
    """解析 CSV 文本"""
    if not text:
        return [], []

    reader = csv.reader(io.StringIO(text))
    headers = []
    rows = []
    for idx, row in enumerate(reader):
        if idx == 0:
            headers = row
        else:
            rows.append(row)
    return headers, rows


def _parse_json_array(data):
    """解析 JSON 数组"""
    if isinstance(data, str):
        data = json.loads(data)

    if not isinstance(data, list) or not data:
        return [], []

    if isinstance(data[0], dict):
        return _parse_dict_list(data)
    elif isinstance(data[0], list):
        return _parse_table(data)

    return [], []


def _parse_table(data):
    """解析二维数组 [[row1], [row2], ...]"""
    if not data or not isinstance(data, list):
        return [], []

    if isinstance(data[0], list):
        headers = [str(h) if h is not None else "" for h in data[0]]
        rows = []
        for row in data[1:]:
            if isinstance(row, list):
                rows.append([str(v) if v is not None else "" for v in row])
        return headers, rows

    return [], []


def _parse_dict_list(data):
    """解析字典列表 [{key: val, ...}, ...]"""
    if isinstance(data, str):
        data = json.loads(data)

    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        return [], []

    headers = list(data[0].keys())
    rows = []
    for item in data:
        rows.append([item.get(h, "") for h in headers])

    return headers, rows


def _set_cell_value(cell, val):
    """设置单元格值，自动处理类型"""
    if val is None or val == "":
        cell.value = None
        return

    # 尝试转为数字
    if isinstance(val, str):
        val_stripped = val.strip().replace(",", "")
        val_stripped = re.sub(r"^[¥￥$]\s*", "", val_stripped)
        try:
            if "." in val_stripped:
                cell.value = float(val_stripped)
                return
            else:
                cell.value = int(val_stripped)
                return
        except (ValueError, TypeError):
            pass

    cell.value = val
