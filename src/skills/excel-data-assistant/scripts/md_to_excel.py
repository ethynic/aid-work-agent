#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
md_to_excel.py - 将 Markdown 中的表格和列表导出为格式化的 Excel 文件

功能：
- 解析 Markdown 中的管道表格（| col1 | col2 |）
- 解析有序列表（1. item）和无序列表（- item / * item）
- 每个 表格/列表 → 一个独立 Sheet
- 自动格式化：表头加粗+浅蓝背景、边框、自适应列宽

调用方式：
  python scripts/md_to_excel.py --input "markdown内容或文件路径" --output-dir "uploads/user123" --filename "导出.xlsx"
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

# 将 scripts/ 目录加入 path 以便导入 excel_utils
sys.path.insert(0, str(Path(__file__).parent))
from excel_utils import (
    generate_output_path,
    output_error,
    output_json,
    parse_markdown_lists,
    parse_markdown_tables,
    write_formatted_excel,
)


def main():
    parser = argparse.ArgumentParser(description="Markdown → Excel 导出")
    parser.add_argument("--input", required=True, help="Markdown 内容字符串或文件路径")
    parser.add_argument("--output-dir", required=True, help="输出目录路径")
    parser.add_argument("--filename", default="导出数据.xlsx", help="输出文件名")
    args = parser.parse_args()

    # 读取 markdown 内容
    input_path = Path(args.input)
    if input_path.exists():
        try:
            md_text = input_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            md_text = input_path.read_text(encoding="gbk")
    else:
        md_text = args.input

    if not md_text.strip():
        output_error("输入内容为空")

    # 解析表格和列表
    tables = parse_markdown_tables(md_text)
    lists = parse_markdown_lists(md_text)

    if not tables and not lists:
        output_error("未在 Markdown 中找到表格或列表。支持格式：管道表格(| col | col |)、有序列表(1. item)、无序列表(- item)")

    # 构建 DataFrame 列表
    data_frames = []
    sheet_names = []

    for i, table in enumerate(tables, 1):
        if len(table) < 1:
            continue
        # 第一行作为表头
        headers = table[0]
        data = table[1:] if len(table) > 1 else []
        df = pd.DataFrame(data, columns=headers)
        data_frames.append((f"表格{i}", df))
        sheet_names.append(f"表格{i}")

    for i, lst in enumerate(lists, 1):
        rows = []
        for idx, (num, content) in enumerate(lst, 1):
            rows.append({"序号": num or idx, "内容": content})
        df = pd.DataFrame(rows)
        data_frames.append((f"列表{i}", df))
        sheet_names.append(f"列表{i}")

    if not data_frames:
        output_error("解析结果为空")

    # 生成输出文件
    output_path = generate_output_path(args.output_dir, args.filename)

    try:
        result = write_formatted_excel(data_frames, output_path)
    except Exception as e:
        output_error(f"生成 Excel 失败: {e}")

    total_items = len(tables) + len(lists)
    output_json({
        "success": True,
        "file_path": str(output_path),
        "filename": output_path.name,
        "sheets": sheet_names,
        "total_rows": result["total_rows"],
        "message": f"已将 {len(tables)} 个表格和 {len(lists)} 个列表导出为 Excel" if total_items > 0
                   else "已导出为 Excel",
    })


if __name__ == "__main__":
    main()
