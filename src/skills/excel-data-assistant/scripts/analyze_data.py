#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_data.py - 分析 CSV/Excel 文件，返回结构化数据概览

功能：
- 自动检测 CSV 编码和分隔符
- 推断列类型（int/float/str/datetime）
- 统计摘要：数值列 → min/max/mean/median/std；文本列 → top-5 高频值
- 数据预览：前 N 行（默认 100）
- 自动建议：空值、重复、类型混合等数据质量问题

调用方式：
  python scripts/analyze_data.py --file "data.xlsx" --preview-rows 100
  python scripts/analyze_data.py --file "data.csv" --sheet "Sheet1"
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from excel_utils import infer_column_type, output_error, output_json, read_data_file


def analyze_column(series: pd.Series) -> Dict[str, Any]:
    """分析单列数据的统计信息。"""
    col_info: Dict[str, Any] = {
        "name": str(series.name),
        "nulls": int(series.isna().sum()),
        "total": len(series),
        "null_rate": round(series.isna().sum() / max(len(series), 1), 4),
        "unique": int(series.nunique()),
    }

    col_type = infer_column_type(series)
    col_info["type"] = col_type

    non_null = series.dropna()

    if col_type in ("int", "float"):
        try:
            numeric = pd.to_numeric(non_null, errors="coerce").dropna()
            if len(numeric) > 0:
                col_info["min"] = float(numeric.min())
                col_info["max"] = float(numeric.max())
                col_info["mean"] = round(float(numeric.mean()), 4)
                col_info["median"] = float(numeric.median())
                col_info["std"] = round(float(numeric.std()), 4) if len(numeric) > 1 else 0
        except Exception:
            pass
    elif col_type == "str":
        value_counts = non_null.value_counts().head(5)
        col_info["top_values"] = {str(k): int(v) for k, v in value_counts.items()}
        lengths = non_null.astype(str).str.len()
        col_info["max_length"] = int(lengths.max())
        col_info["min_length"] = int(lengths.min())
    elif col_type == "datetime":
        try:
            dates = pd.to_datetime(non_null, errors="coerce").dropna()
            if len(dates) > 0:
                col_info["min_date"] = str(dates.min())
                col_info["max_date"] = str(dates.max())
        except Exception:
            pass

    return col_info


def generate_suggestions(df: pd.DataFrame, columns_info: List[Dict]) -> List[str]:
    """基于分析结果生成数据质量建议。"""
    suggestions = []

    # 空值多的列
    high_null_cols = [c for c in columns_info if c.get("null_rate", 0) > 0.1]
    if high_null_cols:
        names = [c["name"] for c in high_null_cols]
        suggestions.append(f"以下列空值率超过 10%: {', '.join(names)}，建议填充或删除")

    # 重复行
    dup_count = int(df.duplicated().sum())
    if dup_count > 0:
        suggestions.append(f"检测到 {dup_count} 个重复行（{round(dup_count/len(df)*100, 1)}%），建议去重")

    # 全空列
    empty_cols = [c for c in columns_info if c.get("unique", 1) == 0]
    if empty_cols:
        names = [c["name"] for c in empty_cols]
        suggestions.append(f"以下列完全为空: {', '.join(names)}，建议删除")

    # 唯一值过多的文本列（可能是 ID 列）
    for c in columns_info:
        if c.get("type") == "str" and c.get("unique", 0) > len(df) * 0.9 and len(df) > 10:
            suggestions.append(f"列「{c['name']}」几乎每行都不同（唯一值 {c['unique']}），可能是 ID 或自由文本列")

    if not suggestions:
        suggestions.append("数据质量良好，未发现明显问题")

    return suggestions


def main():
    parser = argparse.ArgumentParser(description="CSV/Excel 数据分析")
    parser.add_argument("--file", required=True, help="CSV 或 Excel 文件路径")
    parser.add_argument("--sheet", default=None, help="Excel Sheet 名称（默认第一个）")
    parser.add_argument("--preview-rows", type=int, default=100, help="预览行数（默认 100）")
    args = parser.parse_args()

    # 读取数据
    try:
        df, meta = read_data_file(args.file, sheet_name=args.sheet)
    except FileNotFoundError as e:
        output_error(str(e))
    except Exception as e:
        output_error(f"读取文件失败: {e}")

    # 分析每一列
    columns_info = []
    for col in df.columns:
        col_info = analyze_column(df[col])
        columns_info.append(col_info)

    # 生成建议
    suggestions = generate_suggestions(df, columns_info)

    # 数据预览（前 N 行，转为字典列表）
    preview_df = df.head(args.preview_rows)
    preview = preview_df.where(preview_df.notna(), None).to_dict(orient="records")

    output_json({
        "success": True,
        "file_info": meta,
        "columns": columns_info,
        "preview": preview,
        "suggestions": suggestions,
    })


if __name__ == "__main__":
    main()
