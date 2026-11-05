"""
Excel 数据读取

读取 Excel 文件的指定 Sheet 和范围，返回结构化数据。
"""

import csv
import io
import re
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional

import openpyxl
from loguru import logger
from openpyxl.utils.datetime import from_excel

from src.tools.excel.excel_lib import ExcelFileHandler, parse_range
from src.tools.file.excel_reader import MAX_NORMAL_ROWS, STREAM_EMPTY_TAIL, STREAM_MAX_SCAN_ROWS


def _bounded_dimensions(ws) -> tuple:
    """read_only 流式探测真实数据边界（used range 膨胀防御，2026-11-05 TPG80509）。

    整列/整行格式化会使声明维度膨胀到百万行列；已有数据后连续 STREAM_EMPTY_TAIL
    行全空即判定边界，未见过数据时最多扫 STREAM_MAX_SCAN_ROWS 行。
    返回 (true_max_row, true_max_col)。
    """
    true_max_row = 0
    true_max_col = 0
    empty_streak = 0
    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        col_idx = 0
        has_data = False
        for v in row:
            col_idx += 1
            if v is not None and v != "":
                has_data = True
                if col_idx > true_max_col:
                    true_max_col = col_idx
        if has_data:
            true_max_row = row_idx
            empty_streak = 0
        elif true_max_row:
            empty_streak += 1
            if empty_streak >= STREAM_EMPTY_TAIL:
                break
        if row_idx >= STREAM_MAX_SCAN_ROWS:
            break
    return max(true_max_row, 1), max(true_max_col, 1)


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
            max_row = ws.max_row or 1
            max_col = ws.max_column or 1
            # 膨胀防御：声明维度超阈值时流式探测真实边界，避免逐行遍历百万空行
            if max_row > MAX_NORMAL_ROWS or max_col > MAX_NORMAL_ROWS:
                true_rows, true_cols = _bounded_dimensions(ws)
                max_row, max_col = min(max_row, true_rows), min(max_col, true_cols)

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
        logger.opt(exception=True).error(f"[ExcelReader] 读取失败: {e}")
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
        logger.opt(exception=True).error(f"[ExcelReader] read_all_sheets 失败: {e}")
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
        logger.opt(exception=True).error(f"[ExcelReader] CSV 读取失败: {e}")
        return {"success": False, "error": f"读取 CSV 失败: {e}"}


def read_excel_document(file_path: str, sheet_name: Optional[str] = None) -> Dict[str, Any]:
    """
    读取 Excel 文档的完整内容（结构化数据 + 全 Sheet 文本 + 文档元信息）。

    实现已归一到 src/tools/file/excel_reader.py 的 ExcelReader（2026-11-05：
    与 read 工具双实现并存导致同类 used range 膨胀 bug 修一处漏一处，见该文件
    的膨胀防御注释），本函数仅保留薄委托。

    注意：sheet_name 无效时返回错误（与 read 工具一致），不再静默回退到第一个 Sheet。
    """
    from src.tools.file.excel_reader import ExcelReader

    return ExcelReader().read_excel_document(str(file_path), sheet_name=sheet_name)


def render_llm_view(file_path: str) -> Dict[str, Any]:
    """
    渲染 Excel 的 LLM 视图（D5 契约）：值按 number_format/is_date 渲染后输出 markdown 表。

    与 read_sheet()/read_all_sheets() 的区别：那些函数把单元格值裸吐（Excel 序列日期
    变成 46239、datetime 对象 str() 化、全空行照灌），LLM 看不到数字格式无从判断真实
    语义；本函数按文件真实语义渲染，专供 LLM 抽取管线（Excel ETL）使用。

    渲染规则：
    - datetime（无时间部分）-> ``YYYY-MM-DD``；带时间 -> ``YYYY-MM-DD HH:MM:SS``；
      date -> ``YYYY-MM-DD``；Excel 序列日期（数字 + 日期格式，如 46239）按日期渲染
    - 百分比格式（number_format 含 ``%``）-> 明文百分数（如 0.05 + ``0%`` -> ``5%``，
      按格式中的小数位数四舍五入）
    - 其余 ``str()`` 去首尾空白；None -> 空串；超 300 字符截断标 ``…(截断)``
    - 合并单元格非锚点格 -> 空串；全空行跳过；空列保留（列对齐）
    - 不消歧表头、不去重列名、不删任何行——脏结构原样给 LLM

    Returns:
        {
            "success": True,
            "file_name": "report.xlsx",
            "sheets": [
                {
                    "name": "新增",
                    "text": "## Sheet: 新增\\n\\n| 序号 | 姓名 |...\\n|---|---|...\\n| 1 | 张三 |...",
                    "row_count": 2,           # 有效数据行数（不含首行，含分段行/页脚等原样行）
                    "merged_cells": ["A1:E1", ...],
                },
                ...
            ],
        }
    """
    src = Path(file_path)
    if not src.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    file_type = ExcelFileHandler.detect_file_type(file_path)
    if file_type != "xlsx":
        return {"success": False, "error": f"仅支持 .xlsx 格式，当前: {file_type}"}

    try:
        # 注意：read_only=True 模式下 merged_cells.ranges/is_date/number_format 行为受限，
        # 故用普通模式加载（本管线的表长几百行，性能可接受）
        wb = openpyxl.load_workbook(str(src), data_only=True)

        sheets_out = []
        for ws in wb.worksheets:
            sheets_out.append(_render_sheet_llm_view(ws, wb.epoch))
        wb.close()

        logger.info(f"[ExcelReader] render_llm_view 完成: {src.name}, {len(sheets_out)} 个 sheet")
        return {
            "success": True,
            "file_name": src.name,
            "sheets": sheets_out,
        }
    except Exception as e:
        logger.opt(exception=True).error(f"[ExcelReader] render_llm_view 失败: {e}")
        return {"success": False, "error": f"渲染 Excel LLM 视图失败: {e}"}


# LLM 视图单元格文本上限（超长单元格截断，避免长文本撑爆上下文）
_LLM_VIEW_MAX_CELL = 300
_LLM_VIEW_TRUNCATION = "…(截断)"


def _render_sheet_llm_view(ws, epoch: datetime) -> Dict[str, Any]:
    """把单个 worksheet 渲染为 D5 契约的 markdown 表视图"""
    max_row = ws.max_row or 0
    max_col = ws.max_column or 0

    # 合并单元格：记录非锚点坐标（渲染为空串）与合并区列表
    merged_non_anchor = set()
    merged_list = []
    for mr in ws.merged_cells.ranges:
        merged_list.append(str(mr))
        for row_idx in range(mr.min_row, mr.max_row + 1):
            for col_idx in range(mr.min_col, mr.max_col + 1):
                if (row_idx, col_idx) != (mr.min_row, mr.min_col):
                    merged_non_anchor.add((row_idx, col_idx))
    merged_list.sort()

    rendered_rows: List[List[str]] = []
    for row_idx in range(1, max_row + 1):
        cells: List[str] = []
        for col_idx in range(1, max_col + 1):
            if (row_idx, col_idx) in merged_non_anchor:
                cells.append("")
                continue
            cells.append(_render_cell_llm_view(ws.cell(row=row_idx, column=col_idx), epoch))
        # 全空行跳过；空列保留（每行固定 max_col 列，保证 markdown 列对齐）
        if any(c != "" for c in cells):
            rendered_rows.append(cells)

    if rendered_rows:
        lines = ["| " + " | ".join(rendered_rows[0]) + " |",
                 "| " + " | ".join(["---"] * len(rendered_rows[0])) + " |"]
        for cells in rendered_rows[1:]:
            lines.append("| " + " | ".join(cells) + " |")
        text = f"## Sheet: {ws.title}\n\n" + "\n".join(lines)
    else:
        text = f"## Sheet: {ws.title}\n\n(空工作表)"

    return {
        "name": ws.title,
        "text": text,
        "row_count": max(len(rendered_rows) - 1, 0),
        "merged_cells": merged_list,
    }


def _render_cell_llm_view(cell, epoch: datetime) -> str:
    """按 D5 契约渲染单个单元格值为文本"""
    value = cell.value
    if value is None:
        return ""

    # 日期/时间：date/datetime/time 实例，或 Excel 序列日期（数字 + 日期格式，如 46239）
    if isinstance(value, datetime):
        return _format_datetime_llm_view(value)
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and cell.is_date:
        # Excel 序列日期按日期渲染是本层存在的首要理由（openpyxl 读取时通常已自动
        # 转成 datetime，此处兜底覆盖仍是裸数字的场景）
        return _format_datetime_llm_view(from_excel(value, epoch))

    # 百分比格式 -> 明文百分数（按格式中的小数位数四舍五入，如 0.055 + "0.0%" -> 5.5%）
    number_format = cell.number_format or ""
    if "%" in number_format and isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value) * 100:.{_percent_decimals(number_format)}f}%"

    text = str(value).strip()
    if len(text) > _LLM_VIEW_MAX_CELL:
        text = text[:_LLM_VIEW_MAX_CELL] + _LLM_VIEW_TRUNCATION
    return text


def _format_datetime_llm_view(value: datetime) -> str:
    """datetime 渲染：无时间部分 -> YYYY-MM-DD；带时间 -> YYYY-MM-DD HH:MM:SS"""
    if value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0:
        return value.strftime("%Y-%m-%d")
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _percent_decimals(number_format: str) -> int:
    """从百分比数字格式提取小数位数（"0%" -> 0，"0.0%" -> 1，"0.00%" -> 2）"""
    match = re.search(r"\.(\d*[0#])", number_format.split(";")[0])
    return len(match.group(1)) if match else 0
