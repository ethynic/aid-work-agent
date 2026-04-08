#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
clean_data.py - 按 LLM 生成的操作指令清洗数据，输出新 Excel

支持的操作（通过 --operations JSON 传入）：
  drop_empty_rows: true           - 删除全空行
  fill_na: {"列名": 值}           - 填充空值（支持 "ffill"/"bfill"）
  drop_duplicates: {"subset": [...], "keep": "first"} - 去重
  rename_columns: {"旧名": "新名"} - 重命名列
  change_types: {"列名": "float"} - 类型转换
  strip_whitespace: ["列名"]      - 去除空白
  replace_values: {"列名": {"旧": "新"}} - 值替换
  filter_rows: {"列名": ">= 100"} - 条件过滤
  drop_columns: ["列名"]          - 删除列

调用方式：
  python scripts/clean_data.py --file "data.xlsx" --operations '{"drop_empty_rows": true}' --output-dir "uploads/user123" --filename "清洗后.xlsx"
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from excel_utils import (
    generate_output_path,
    output_error,
    output_json,
    read_data_file,
    write_formatted_excel,
)


def count_nulls(df: pd.DataFrame) -> int:
    """统计 DataFrame 中的总空值数。"""
    return int(df.isna().sum().sum())


def apply_clean_operations(df: pd.DataFrame, ops: Dict[str, Any]) -> tuple:
    """
    按顺序应用清洗操作。

    Returns:
        (cleaned_df, applied_operations, details)
    """
    applied = []
    details = []

    for op_name, op_value in ops.items():
        try:
            before_rows = len(df)

            if op_name == "drop_empty_rows" and op_value:
                df = df.dropna(how="all")
                removed = before_rows - len(df)
                if removed > 0:
                    applied.append(op_name)
                    details.append(f"删除 {removed} 个全空行")

            elif op_name == "fill_na" and isinstance(op_value, dict):
                for col, fill_val in op_value.items():
                    if col in df.columns:
                        if fill_val in ("ffill", "bfill"):
                            df[col] = df[col].fillna(method=fill_val)
                        else:
                            df[col] = df[col].fillna(fill_val)
                applied.append(op_name)
                filled = sum(1 for c in op_value if c in df.columns)
                details.append(f"填充了 {filled} 列的空值")

            elif op_name == "drop_duplicates" and isinstance(op_value, dict):
                subset = op_value.get("subset", None)
                keep = op_value.get("keep", "first")
                # 确保 subset 中的列存在
                if subset:
                    subset = [c for c in subset if c in df.columns]
                df = df.drop_duplicates(subset=subset or None, keep=keep)
                removed = before_rows - len(df)
                if removed > 0:
                    applied.append(op_name)
                    details.append(f"去除 {removed} 个重复行")
                else:
                    applied.append(op_name)
                    details.append("未发现重复行")

            elif op_name == "rename_columns" and isinstance(op_value, dict):
                # 只重命名存在的列
                rename_map = {k: v for k, v in op_value.items() if k in df.columns}
                if rename_map:
                    df = df.rename(columns=rename_map)
                    applied.append(op_name)
                    details.append(f"重命名了 {len(rename_map)} 列")

            elif op_name == "change_types" and isinstance(op_value, dict):
                for col, target_type in op_value.items():
                    if col not in df.columns:
                        continue
                    try:
                        if target_type in ("int", "integer"):
                            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
                        elif target_type == "float":
                            df[col] = pd.to_numeric(df[col], errors="coerce")
                        elif target_type == "str":
                            df[col] = df[col].astype(str).replace("nan", None)
                        elif target_type == "datetime":
                            df[col] = pd.to_datetime(df[col], errors="coerce")
                    except Exception:
                        pass
                applied.append(op_name)
                details.append(f"转换了 {len(op_value)} 列的类型")

            elif op_name == "strip_whitespace" and isinstance(op_value, list):
                for col in op_value:
                    if col in df.columns and df[col].dtype == object:
                        df[col] = df[col].str.strip()
                applied.append(op_name)
                details.append(f"去除 {len(op_value)} 列的空白")

            elif op_name == "replace_values" and isinstance(op_value, dict):
                for col, replacements in op_value.items():
                    if col in df.columns and isinstance(replacements, dict):
                        df[col] = df[col].replace(replacements)
                applied.append(op_name)
                details.append(f"替换了 {len(op_value)} 列的值")

            elif op_name == "filter_rows" and isinstance(op_value, dict):
                mask = pd.Series([True] * len(df), index=df.index)
                for col, condition in op_value.items():
                    if col not in df.columns:
                        continue
                    try:
                        numeric_col = pd.to_numeric(df[col], errors="coerce")
                        if isinstance(condition, str):
                            # 解析简单条件: ">= 100", "< 50", "== 'xxx'"
                            condition = condition.strip()
                            if condition.startswith(">="):
                                mask &= numeric_col >= float(condition[2:].strip())
                            elif condition.startswith("<="):
                                mask &= numeric_col <= float(condition[2:].strip())
                            elif condition.startswith(">"):
                                mask &= numeric_col > float(condition[1:].strip())
                            elif condition.startswith("<"):
                                mask &= numeric_col < float(condition[1:].strip())
                            elif condition.startswith("=="):
                                val = condition[2:].strip().strip("'\"")
                                mask &= df[col].astype(str) == val
                            elif condition.startswith("!="):
                                val = condition[2:].strip().strip("'\"")
                                mask &= df[col].astype(str) != val
                    except Exception:
                        pass
                df = df[mask]
                removed = before_rows - len(df)
                applied.append(op_name)
                details.append(f"过滤后保留 {len(df)} 行（移除 {removed} 行）")

            elif op_name == "drop_columns" and isinstance(op_value, list):
                existing = [c for c in op_value if c in df.columns]
                if existing:
                    df = df.drop(columns=existing)
                    applied.append(op_name)
                    details.append(f"删除了 {len(existing)} 列")

        except Exception as e:
            details.append(f"操作 {op_name} 跳过（错误: {e}）")

    return df, applied, details


def main():
    parser = argparse.ArgumentParser(description="数据清洗")
    parser.add_argument("--file", required=True, help="输入文件路径")
    parser.add_argument("--operations", required=True, help="清洗操作 JSON 字符串")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--filename", default="清洗后数据.xlsx", help="输出文件名")
    parser.add_argument("--sheet", default=None, help="Excel Sheet 名称")
    args = parser.parse_args()

    # 解析操作 JSON
    try:
        ops = json.loads(args.operations)
    except json.JSONDecodeError as e:
        output_error(f"operations JSON 格式错误: {e}")

    if not isinstance(ops, dict) or len(ops) == 0:
        output_error("operations 必须是非空 JSON 对象")

    # 读取数据
    try:
        df, meta = read_data_file(args.file, sheet_name=args.sheet)
    except Exception as e:
        output_error(f"读取文件失败: {e}")

    before = {
        "rows": len(df),
        "columns": len(df.columns),
        "nulls": count_nulls(df),
    }

    # 应用清洗
    df_cleaned, applied, details = apply_clean_operations(df, ops)

    after = {
        "rows": len(df_cleaned),
        "columns": len(df_cleaned.columns),
        "nulls": count_nulls(df_cleaned),
    }

    # 生成输出
    output_path = generate_output_path(args.output_dir, args.filename)
    try:
        write_formatted_excel([("清洗后数据", df_cleaned)], output_path)
    except Exception as e:
        output_error(f"生成 Excel 失败: {e}")

    output_json({
        "success": True,
        "file_path": str(output_path),
        "filename": output_path.name,
        "before": before,
        "after": after,
        "operations_applied": applied,
        "details": details,
        "message": "清洗完成: " + "; ".join(details) if details else "清洗完成",
    })


if __name__ == "__main__":
    main()
