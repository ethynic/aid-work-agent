#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
excel_utils.py - Excel Data Assistant 共享工具函数

提供 Excel 格式化、编码检测、输出目录创建等通用功能，
供 md_to_excel / analyze_data / clean_data / aggregate_chart 共用。
"""

import csv
import json
import os
import sys
import uuid
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 尝试导入可选依赖，给出友好提示
try:
    import pandas as pd
except ImportError:
    print(json.dumps({
        "success": False,
        "error": "pandas 未安装。请运行: pip install pandas",
        "missing_dep": "pandas"
    }, ensure_ascii=False))
    sys.exit(1)

try:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.chart import BarChart, LineChart, PieChart, ScatterChart, Reference
    from openpyxl.utils import get_column_letter
except ImportError:
    print(json.dumps({
        "success": False,
        "error": "openpyxl 未安装。请运行: pip install openpyxl",
        "missing_dep": "openpyxl"
    }, ensure_ascii=False))
    sys.exit(1)


# ============== 样式常量 ==============

HEADER_FILL = PatternFill(start_color="D6E4F0", end_color="D6E4F0", fill_type="solid")
HEADER_FONT = Font(bold=True, size=11)
DATA_FONT = Font(size=11)
THIN_BORDER = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)
CENTER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT_ALIGN = Alignment(horizontal="left", vertical="center", wrap_text=True)


# ============== 输出目录 ==============

def ensure_output_dir(output_dir: str) -> Path:
    """创建输出目录，返回 Path 对象。"""
    p = Path(output_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def generate_output_path(output_dir: str, filename: str) -> Path:
    """生成输出文件路径，确保目录存在且文件名安全。"""
    safe_name = Path(filename).name  # 去掉可能的路径前缀
    if not safe_name.endswith((".xlsx", ".xls")):
        safe_name += ".xlsx"
    out_dir = ensure_output_dir(output_dir)
    return out_dir / safe_name


# ============== CSV 编码检测 ==============

def detect_csv_encoding(file_path: str) -> str:
    """
    自动检测 CSV 文件编码。
    尝试顺序：chardet → utf-8-sig → utf-8 → gbk → gb2312 → latin-1
    """
    # 优先使用 chardet
    try:
        import chardet
        with open(file_path, "rb") as f:
            raw = f.read(65536)  # 读前 64KB 足够检测
        result = chardet.detect(raw)
        if result and result.get("confidence", 0) > 0.7:
            return result["encoding"]
    except ImportError:
        pass

    # fallback：逐个尝试
    candidates = ["utf-8-sig", "utf-8", "gbk", "gb2312", "latin-1"]
    for enc in candidates:
        try:
            with open(file_path, "r", encoding=enc) as f:
                f.read(8192)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    return "utf-8"  # 最终 fallback


def detect_csv_delimiter(file_path: str, encoding: str) -> str:
    """用 csv.Sniffer 检测 CSV 分隔符。"""
    try:
        with open(file_path, "r", encoding=encoding) as f:
            sample = f.read(8192)
        sniffer = csv.Sniffer()
        dialect = sniffer.sniff(sample, delimiters=",\t;|")
        return dialect.delimiter
    except Exception:
        return ","


# ============== 数据读取 ==============

def read_data_file(file_path: str, sheet_name: Optional[str] = None,
                   encoding: Optional[str] = None) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    统一读取 CSV 或 Excel 文件，返回 (DataFrame, 元信息)。

    元信息包含：file_type, encoding, sheet_names, total_rows, total_columns 等。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    meta: Dict[str, Any] = {
        "file_path": str(path),
        "file_name": path.name,
        "size_bytes": path.stat().st_size,
    }

    suffix = path.suffix.lower()

    if suffix == ".csv":
        enc = encoding or detect_csv_encoding(file_path)
        sep = detect_csv_delimiter(file_path, enc)
        df = pd.read_csv(file_path, encoding=enc, sep=sep, dtype=str, keep_default_na=True)
        meta["file_type"] = "csv"
        meta["encoding"] = enc
        meta["delimiter"] = sep
    elif suffix in (".xlsx", ".xls"):
        # 先读取 sheet 列表
        xls = pd.ExcelFile(file_path, engine="openpyxl")
        meta["file_type"] = "xlsx"
        meta["sheet_names"] = xls.sheet_names
        meta["sheet_count"] = len(xls.sheet_names)
        target = sheet_name or xls.sheet_names[0]
        df = pd.read_excel(xls, sheet_name=target, dtype=str, keep_default_na=True)
        meta["current_sheet"] = target
        xls.close()
    else:
        raise ValueError(f"不支持的文件格式: {suffix}，仅支持 .csv / .xlsx / .xls")

    meta["total_rows"] = len(df)
    meta["total_columns"] = len(df.columns)
    return df, meta


# ============== Excel 格式化 ==============

def write_formatted_excel(
    data_frames: List[Tuple[str, pd.DataFrame]],
    output_path: Path,
    header_row: bool = True,
) -> Dict[str, Any]:
    """
    将多个 DataFrame 写入同一个 Excel 文件，每个占一个 Sheet，带格式化。

    Args:
        data_frames: [(sheet_name, DataFrame), ...]
        output_path: 输出文件路径
        header_row: 第一行是否为表头

    Returns:
        包含 sheets 和 total_rows 的摘要字典
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # 删除默认 Sheet

    sheets = []
    total_rows = 0

    for sheet_name, df in data_frames:
        # Sheet 名最长 31 字符（Excel 限制），且不能包含特殊字符
        safe_name = sheet_name[:31].replace("/", "-").replace("\\", "-").replace("*", "")
        ws = wb.create_sheet(title=safe_name)

        # 写入表头
        for col_idx, col_name in enumerate(df.columns, 1):
            cell = ws.cell(row=1, column=col_idx, value=col_name)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = CENTER_ALIGN
            cell.border = THIN_BORDER

        # 写入数据行
        for row_idx, row in enumerate(df.itertuples(index=False), 2):
            for col_idx, value in enumerate(row, 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                # 处理 NaN
                if pd.isna(value):
                    cell.value = None
                else:
                    cell.value = value
                cell.font = DATA_FONT
                cell.border = THIN_BORDER
                cell.alignment = LEFT_ALIGN

        # 自动列宽
        _auto_column_width(ws, df.columns.tolist())

        sheets.append(safe_name)
        total_rows += len(df)

    wb.save(str(output_path))
    return {"sheets": sheets, "total_rows": total_rows}


def _auto_column_width(ws, headers: List[str], max_width: int = 50, min_width: int = 8):
    """根据内容自动调整列宽。"""
    for col_idx, header in enumerate(headers, 1):
        max_len = len(str(header))
        for row in ws.iter_rows(min_row=2, min_col=col_idx, max_col=col_idx, max_row=min(ws.max_row, 102)):
            for cell in row:
                if cell.value:
                    # 中文字符按 2 计算宽度
                    val_len = sum(2 if ord(c) > 127 else 1 for c in str(cell.value))
                    max_len = max(max_len, val_len)
        col_letter = get_column_letter(col_idx)
        ws.column_dimensions[col_letter].width = min(max(max_len + 2, min_width), max_width)


# ============== 图表构建 ==============

CHART_TYPES = {
    "bar": BarChart,
    "line": LineChart,
    "pie": PieChart,
    "scatter": ScatterChart,
}

def create_chart(
    chart_type: str,
    ws_data: openpyxl.worksheet.worksheet.Worksheet,
    title: str,
    x_col: int,
    y_col: int,
    start_row: int = 1,
    end_row: Optional[int] = None,
) -> Any:
    """
    创建 openpyxl 原生图表对象。

    Args:
        chart_type: bar / line / pie / scatter
        ws_data: 包含数据的 worksheet
        title: 图表标题
        x_col: X 轴列号（1-based）
        y_col: Y 轴列号（1-based）
        start_row: 数据起始行（含表头）
        end_row: 数据结束行

    Returns:
        openpyxl Chart 对象
    """
    if end_row is None:
        end_row = ws_data.max_row

    chart_cls = CHART_TYPES.get(chart_type)
    if not chart_cls:
        raise ValueError(f"不支持的图表类型: {chart_type}，可选: {list(CHART_TYPES.keys())}")

    chart = chart_cls()
    chart.title = title
    chart.style = 10

    data_ref = Reference(ws_data, min_col=y_col, min_row=start_row, max_row=end_row)
    cats_ref = Reference(ws_data, min_col=x_col, min_row=start_row + 1, max_row=end_row)

    if chart_type == "scatter":
        chart.x_values = cats_ref
        chart.y_values = data_ref
    elif chart_type == "pie":
        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats_ref)
    else:
        chart.add_data(data_ref, titles_from_data=True)
        chart.set_categories(cats_ref)

    # 柱状图宽度调整
    if chart_type == "bar" and hasattr(chart, 'grouping'):
        chart.grouping = "clustered"

    return chart


# ============== Markdown 解析 ==============

def parse_markdown_tables(text: str) -> List[List[List[str]]]:
    """
    解析 markdown 中的表格，返回 [table1, table2, ...]，
    每个 table 是 [[row1_cell1, row1_cell2, ...], ...]。
    自动跳过分隔行（|---|---|）。
    """
    tables = []
    current_table: List[List[str]] = []

    for line in text.split("\n"):
        stripped = line.strip()
        if "|" in stripped and stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            # 检测分隔行：全是 -、:、空格
            if all(set(c) <= {"-", ":", " ", ""} for c in cells):
                continue
            current_table.append(cells)
        else:
            if current_table:
                tables.append(current_table)
                current_table = []

    if current_table:
        tables.append(current_table)

    return tables


def parse_markdown_lists(text: str) -> List[List[Tuple[Optional[int], str]]]:
    """
    解析 markdown 中的列表，返回 [list1, list2, ...]，
    每个 list 是 [(序号或None, 内容), ...]。
    """
    lists = []
    current_list: List[Tuple[Optional[int], str]] = []

    for line in text.split("\n"):
        stripped = line.strip()
        # 无序列表: - item 或 * item
        if stripped.startswith("- ") or stripped.startswith("* "):
            content = stripped[2:].strip()
            current_list.append((None, content))
        # 有序列表: 1. item
        elif len(stripped) > 2 and stripped[0].isdigit():
            dot_pos = stripped.find(". ")
            if dot_pos > 0 and dot_pos <= 3:
                try:
                    num = int(stripped[:dot_pos])
                    content = stripped[dot_pos + 2:].strip()
                    current_list.append((num, content))
                except ValueError:
                    if current_list:
                        lists.append(current_list)
                        current_list = []
            else:
                if current_list:
                    lists.append(current_list)
                    current_list = []
        else:
            if current_list:
                lists.append(current_list)
                current_list = []

    if current_list:
        lists.append(current_list)

    return lists


# ============== 类型推断 ==============

def infer_column_type(series: pd.Series) -> str:
    """推断一列数据的类型。"""
    # 去掉空值
    non_null = series.dropna()
    if len(non_null) == 0:
        return "empty"

    sample = non_null.head(100)

    # 尝试数值
    try:
        numeric = pd.to_numeric(sample, errors="coerce")
        if numeric.notna().sum() / len(sample) > 0.8:
            if (numeric.dropna() % 1 == 0).all():
                return "int"
            return "float"
    except Exception:
        pass

    # 尝试日期
    try:
        dates = pd.to_datetime(sample, errors="coerce", infer_datetime_format=True)
        if dates.notna().sum() / len(sample) > 0.8:
            return "datetime"
    except Exception:
        pass

    return "str"


# ============== JSON 输出工具 ==============

def output_json(data: Dict[str, Any]):
    """统一输出 JSON 到 stdout。"""
    print(json.dumps(data, ensure_ascii=False, default=str))


def output_error(message: str, **extra):
    """统一输出错误 JSON 到 stdout。"""
    result = {"success": False, "error": message}
    result.update(extra)
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(1)
