"""
Excel 模板管理

支持两种模板来源：
- 系统模板：storage/excel_templates/ 目录下
- 用户上传模板：file_paths 中的临时文件

模板语法：{{变量名}}
- 字符串/数字值：直接替换单元格文本
- 列表值：展开为多行（行展开模式）
- 字典列表：按表头文本匹配填入
"""

import json
import re
from copy import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill
from loguru import logger

from src.tools.excel.excel_lib import ExcelFileHandler

# 变量占位符正则
VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")


def list_templates() -> List[Dict[str, Any]]:
    """列出可用的系统 Excel 模板"""
    template_dir = Path("storage/excel_templates")
    if not template_dir.exists():
        return []

    templates = []
    for xlsx_file in sorted(template_dir.glob("*.xlsx")):
        info = {"name": xlsx_file.stem, "file": str(xlsx_file)}

        # 检查是否有元数据文件
        json_file = xlsx_file.with_suffix(".json")
        if json_file.exists():
            try:
                meta = json.loads(json_file.read_text(encoding="utf-8"))
                info.update(meta)
            except json.JSONDecodeError:
                pass

        # 自动检测模板中的变量
        try:
            detected = detect_variables(str(xlsx_file))
            info["variable_count"] = detected.get("variable_count", 0)
            info["variables"] = list(detected.get("variables", {}).keys())
        except Exception:
            pass

        templates.append(info)

    return templates


def detect_variables(file_path: str) -> Dict[str, Any]:
    """扫描 Excel 文件，提取所有 {{变量名}} 占位符及其位置"""
    wb = openpyxl.load_workbook(file_path)
    variables = {}
    row_info = None

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value and isinstance(cell.value, str):
                    matches = VAR_PATTERN.findall(cell.value)
                    for var_name in matches:
                        if var_name not in variables:
                            variables[var_name] = {
                                "cells": [],
                                "type": "unknown",
                            }
                        variables[var_name]["cells"].append(f"{ws.title}!{cell.coordinate}")

    wb.close()

    has_row_variables = any(v["type"] == "unknown" for v in variables.values())

    return {
        "variables": variables,
        "variable_count": len(variables),
        "has_row_variables": has_row_variables,
    }


def fill_template(template_path: str, variables: Dict[str, Any],
                  output_name: Optional[str] = None) -> Dict[str, Any]:
    """
    使用数据填充 Excel 模板。

    流程：
    1. 打开模板（不修改原文件）
    2. 扫描所有 {{变量名}} 占位符
    3. 按变量值类型分类处理
    4. 如果无占位符，尝试结构感知模式
    """
    src = Path(template_path)
    if not src.exists():
        return {"success": False, "error": f"模板文件不存在: {template_path}"}

    try:
        wb, info = ExcelFileHandler.copy_and_open(template_path)
        ws = wb.active

        # 扫描占位符
        placeholders = _scan_placeholders(ws)

        if placeholders:
            result = _fill_with_placeholders(ws, placeholders, variables)
        else:
            result = _fill_structured(ws, variables)

        if not result["success"]:
            wb.close()
            return result

        # 保存
        output_name = output_name or (src.stem + "_filled.xlsx")
        save_result = ExcelFileHandler.save_temp(wb, file_name=output_name)
        wb.close()

        save_result["success"] = True
        save_result["variables_replaced"] = result.get("variables_replaced", 0)
        save_result["rows_inserted"] = result.get("rows_inserted", 0)
        save_result["unmatched_variables"] = result.get("unmatched_variables", [])
        save_result["message"] = f"已替换 {result.get('variables_replaced', 0)} 个变量"

        return save_result
    except Exception as e:
        logger.error(f"[ExcelTemplate] 模板填充失败: {e}", exc_info=True)
        return {"success": False, "error": f"模板填充失败: {e}"}


def _scan_placeholders(ws) -> List[Dict]:
    """
    扫描工作表中所有 {{变量名}} 占位符。

    返回：[{"name": "变量名", "cell": Cell对象, "row": 行号, "col": 列号, "full_text": 完整文本}, ...]
    """
    placeholders = []
    for row in ws.iter_rows():
        for cell in row:
            if cell.value and isinstance(cell.value, str):
                matches = VAR_PATTERN.findall(cell.value)
                for var_name in matches:
                    placeholders.append({
                        "name": var_name,
                        "cell": cell,
                        "row": cell.row,
                        "col": cell.column,
                        "full_text": cell.value,
                    })
    return placeholders


def _fill_with_placeholders(ws, placeholders: List[Dict], variables: Dict[str, Any]) -> Dict:
    """使用占位符模式填充模板"""
    variables_replaced = 0
    rows_inserted = 0

    # 分组：哪些变量是列表类型
    list_vars = {}
    single_vars = {}

    for var_name, value in variables.items():
        if isinstance(value, list) and value:
            if isinstance(value[0], dict):
                list_vars[var_name] = value  # 字典列表
            else:
                list_vars[var_name] = value  # 简单列表
        else:
            single_vars[var_name] = value

    # 找出包含列表变量的行
    list_placeholder_rows = {}
    for p in placeholders:
        if p["name"] in list_vars:
            if p["row"] not in list_placeholder_rows:
                list_placeholder_rows[p["row"]] = []
            list_placeholder_rows[p["row"]].append(p)

    # 处理行展开
    # 需要从下往上处理，避免行号偏移
    for row_num in sorted(list_placeholder_rows.keys(), reverse=True):
        row_placeholders = list_placeholder_rows[row_num]

        # 检测这一行是方式 A（每列一个变量）还是方式 B（字典列表）
        dict_list_var = None
        simple_list_vars = []

        for p in row_placeholders:
            var_value = list_vars.get(p["name"])
            if isinstance(var_value, list) and var_value and isinstance(var_value[0], dict):
                dict_list_var = (p["name"], var_value)
            else:
                simple_list_vars.append(p)

        if dict_list_var:
            # 方式 B：字典列表
            var_name, items = dict_list_var
            n = len(items)
            inserted = _expand_dict_list_row(ws, row_num, items, row_placeholders)
            rows_inserted += inserted
        else:
            # 方式 A：简单列表
            list_lengths = {}
            for p in simple_list_vars:
                var_value = list_vars.get(p["name"], [])
                list_lengths[p["name"]] = len(var_value)

            # 校验长度一致
            lengths = set(list_lengths.values())
            if len(lengths) > 1:
                return {
                    "success": False,
                    "error": f"同一行的列表变量长度不一致: {list_lengths}",
                }

            n = lengths.pop() if lengths else 0
            if n > 0:
                inserted = _expand_simple_list_row(ws, row_num, simple_list_vars, list_vars)
                rows_inserted += inserted

    # 处理单值替换（在行展开之后，因为行展开可能改变了行号）
    for p in placeholders:
        if p["name"] in single_vars:
            # 查找当前所有匹配的占位符（可能因为行展开，产生了新的单元格）
            _replace_all_placeholders(ws, p["name"], single_vars[p["name"]])
            variables_replaced += 1

    # 处理列表变量的最终替换（展开后替换模板行中的占位符）
    for var_name in list_vars:
        _replace_all_placeholders(ws, var_name, "")  # 清理残留占位符

    unmatched = [p["name"] for p in placeholders if p["name"] not in variables]

    return {
        "success": True,
        "variables_replaced": variables_replaced + len(list_vars),
        "rows_inserted": rows_inserted,
        "unmatched_variables": unmatched,
    }


def _expand_simple_list_row(ws, row_num: int, placeholders: List[Dict],
                            list_vars: Dict) -> int:
    """
    方式 A：每列一个列表变量，展开为多行。
    返回额外插入的行数。
    """
    # 获取列表长度
    n = len(list(list_vars.values())[0]) if list_vars else 0
    if n <= 1:
        # 只有一条数据或无数据，直接替换
        for p in placeholders:
            vals = list_vars.get(p["name"], [])
            val = vals[0] if vals else ""
            ws.cell(row=row_num, column=p["col"]).value = val
        return 0

    # 保存模板行的样式
    template_styles = _save_row_styles(ws, row_num)

    # 插入 N-1 行
    ws.insert_rows(row_num + 1, amount=n - 1)

    # 复制样式并填入数据
    for i in range(n):
        target_row = row_num + i
        _apply_row_styles(ws, target_row, template_styles)

        for p in placeholders:
            vals = list_vars.get(p["name"], [])
            val = vals[i] if i < len(vals) else ""
            ws.cell(row=target_row, column=p["col"]).value = val

    return n - 1


def _expand_dict_list_row(ws, row_num: int, items: List[Dict],
                          placeholders: List[Dict]) -> int:
    """
    方式 B：字典列表，按表头文本匹配填入。
    """
    n = len(items)
    if n == 0:
        return 0

    # 获取表头文本（上一行）
    header_row = row_num - 1
    headers = {}
    for col in range(1, ws.max_column + 1):
        val = ws.cell(row=header_row, column=col).value
        if val and isinstance(val, str):
            headers[val.strip()] = col

    if n == 1:
        # 只有一条数据
        item = items[0]
        for key, col in headers.items():
            if key in item:
                ws.cell(row=row_num, column=col).value = item[key]
        # 清理占位符
        for p in placeholders:
            ws.cell(row=row_num, column=p["col"]).value = ""
        return 0

    # 保存模板行样式
    template_styles = _save_row_styles(ws, row_num)

    # 插入 N-1 行
    ws.insert_rows(row_num + 1, amount=n - 1)

    # 填入数据
    for i, item in enumerate(items):
        target_row = row_num + i
        _apply_row_styles(ws, target_row, template_styles)

        for key, col in headers.items():
            if key in item:
                ws.cell(row=target_row, column=col).value = item[key]

        # 清理占位符
        for p in placeholders:
            ws.cell(row=target_row, column=p["col"]).value = ""

    return n - 1


def _replace_all_placeholders(ws, var_name: str, value: Any):
    """替换工作表中所有 {{var_name}} 占位符"""
    for row in ws.iter_rows():
        for cell in row:
            if cell.value and isinstance(cell.value, str):
                pattern = "{{" + var_name + "}}"
                if pattern in cell.value:
                    if cell.value == pattern:
                        cell.value = value
                    else:
                        cell.value = cell.value.replace(pattern, str(value))


def _fill_structured(ws, variables: Dict[str, Any]) -> Dict:
    """
    结构感知模式：无 {{}} 占位符时，自动检测表头和数据区域。
    """
    # 检测表头行
    header_row = _detect_header_row(ws)
    if header_row is None:
        return {"success": False, "error": "无法检测到表头行，请在模板中使用 {{变量名}} 占位符"}

    # 收集表头文本
    headers = {}
    for col in range(1, ws.max_column + 1):
        val = ws.cell(row=header_row, column=col).value
        if val and isinstance(val, str):
            headers[val.strip()] = col

    if not headers:
        return {"success": False, "error": "表头行为空"}

    variables_replaced = 0
    data_start_row = header_row + 1

    for var_name, value in variables.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            # 字典列表数据
            for i, item in enumerate(value):
                target_row = data_start_row + i
                # 如果行不够，插入
                if target_row > ws.max_row:
                    ws.insert_rows(target_row)

                for key, col in headers.items():
                    if key in item:
                        ws.cell(row=target_row, column=col).value = item[key]

                variables_replaced += len(item)
        elif isinstance(value, list):
            # 简单列表，尝试按变量名匹配列
            col = headers.get(var_name)
            if col:
                for i, val in enumerate(value):
                    ws.cell(row=data_start_row + i, column=col, value=val)
                variables_replaced += len(value)
        else:
            # 单值，尝试匹配列名
            col = headers.get(var_name)
            if col:
                ws.cell(row=data_start_row, column=col, value=value)
                variables_replaced += 1

    return {
        "success": True,
        "variables_replaced": variables_replaced,
        "rows_inserted": 0,
        "unmatched_variables": [],
    }


def _detect_header_row(ws) -> Optional[int]:
    """检测表头行（启发式：第一个有加粗/背景色/非空内容的行）"""
    for row in range(1, min(ws.max_row + 1, 20)):  # 只搜索前 20 行
        has_bold = False
        has_fill = False
        non_empty = 0

        for col in range(1, ws.max_column + 1):
            cell = ws.cell(row=row, column=col)
            if cell.value is not None and str(cell.value).strip():
                non_empty += 1
                if cell.font and cell.font.bold:
                    has_bold = True
                if cell.fill and cell.fill.start_color and cell.fill.start_color.rgb and cell.fill.start_color.rgb != "00000000":
                    has_fill = True

        # 非空列 >= 2 且有样式特征，认为是表头
        if non_empty >= 2 and (has_bold or has_fill):
            return row

    # fallback：第一个有 >= 2 非空列的行
    for row in range(1, min(ws.max_row + 1, 20)):
        non_empty = sum(
            1 for col in range(1, ws.max_column + 1)
            if ws.cell(row=row, column=col).value is not None
            and str(ws.cell(row=row, column=col).value).strip()
        )
        if non_empty >= 2:
            return row

    return None


def _save_row_styles(ws, row_num: int) -> List[Dict]:
    """保存一行的所有单元格样式"""
    styles = []
    for col in range(1, ws.max_column + 1):
        cell = ws.cell(row=row_num, column=col)
        style = {}
        if cell.has_style:
            style["font"] = copy(cell.font)
            style["border"] = copy(cell.border)
            style["fill"] = copy(cell.fill)
            style["number_format"] = cell.number_format
            style["protection"] = copy(cell.protection)
            style["alignment"] = copy(cell.alignment)
        styles.append(style)
    return styles


def _apply_row_styles(ws, row_num: int, styles: List[Dict]):
    """将保存的样式应用到一行"""
    for col_idx, style in enumerate(styles, 1):
        cell = ws.cell(row=row_num, column=col_idx)
        if "font" in style:
            cell.font = style["font"]
        if "border" in style:
            cell.border = style["border"]
        if "fill" in style:
            cell.fill = style["fill"]
        if "number_format" in style:
            cell.number_format = style["number_format"]
        if "protection" in style:
            cell.protection = style["protection"]
        if "alignment" in style:
            cell.alignment = style["alignment"]

    # 复制行高
    # 注意：模板行的行高需要从原始行复制，但此时可能已被 insert_rows 改变
