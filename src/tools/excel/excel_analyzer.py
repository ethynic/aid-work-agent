"""
Excel 数据分析

对 Excel 数据进行统计分析：摘要、相关性、分布、异常值、透视。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from src.tools.excel.excel_lib import ExcelFileHandler


def analyze_data(file_path: str, sheet_name: Optional[str] = None,
                 analysis_type: str = "summary",
                 **kwargs) -> Dict[str, Any]:
    """
    对 Excel 数据进行统计分析。

    analysis_type: summary | correlation | distribution | anomaly | pivot
    """
    src = Path(file_path)
    if not src.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    try:
        df = _load_dataframe(file_path, sheet_name)
        if df is None:
            return {"success": False, "error": "无法加载数据"}

        if analysis_type == "summary":
            return _analyze_summary(df)
        elif analysis_type == "correlation":
            return _analyze_correlation(df)
        elif analysis_type == "distribution":
            return _analyze_distribution(df)
        elif analysis_type == "anomaly":
            return _analyze_anomaly(df, **kwargs)
        elif analysis_type == "pivot":
            return _analyze_pivot(df, **kwargs)
        else:
            return {"success": False, "error": f"不支持的分析类型: {analysis_type}"}
    except Exception as e:
        logger.error(f"[ExcelAnalyzer] 分析失败: {e}", exc_info=True)
        return {"success": False, "error": f"数据分析失败: {e}"}


def _load_dataframe(file_path: str, sheet_name: Optional[str] = None) -> Optional[pd.DataFrame]:
    """加载文件为 DataFrame"""
    file_type = ExcelFileHandler.detect_file_type(file_path)

    try:
        if file_type == "csv":
            # 编码 fallback
            for encoding in ("utf-8-sig", "utf-8", "gbk", "gb2312", "latin-1"):
                try:
                    return pd.read_csv(file_path, encoding=encoding)
                except (UnicodeDecodeError, pd.errors.ParserError):
                    continue
            return None
        elif file_type == "xlsx":
            kwargs = {"engine": "openpyxl"}
            if sheet_name:
                kwargs["sheet_name"] = sheet_name
            return pd.read_excel(file_path, **kwargs)
        return None
    except Exception as e:
        logger.error(f"[ExcelAnalyzer] 加载数据失败: {e}")
        return None


def _analyze_summary(df: pd.DataFrame) -> Dict[str, Any]:
    """基础统计摘要"""
    columns_info = []
    for col in df.columns:
        col_data = df[col].dropna()
        info = {
            "name": str(col),
            "type": str(df[col].dtype),
            "null_count": int(df[col].isnull().sum()),
            "null_rate": round(df[col].isnull().sum() / len(df), 4) if len(df) > 0 else 0,
            "unique_count": int(df[col].nunique()),
        }

        if pd.api.types.is_numeric_dtype(df[col]):
            info["min"] = _safe_number(col_data.min())
            info["max"] = _safe_number(col_data.max())
            info["mean"] = _safe_number(col_data.mean())
            info["median"] = _safe_number(col_data.median())
            info["std"] = _safe_number(col_data.std())

        columns_info.append(info)

    return {
        "success": True,
        "analysis_type": "summary",
        "result": {
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": columns_info,
        },
    }


def _analyze_correlation(df: pd.DataFrame) -> Dict[str, Any]:
    """相关性分析"""
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    if len(numeric_cols) < 2:
        return {"success": False, "error": "数值列不足 2 个，无法进行相关性分析"}

    corr = df[numeric_cols].corr()
    corr_dict = {}
    for col in corr.columns:
        corr_dict[str(col)] = {str(k): _safe_number(v) for k, v in corr[col].items()}

    # 找出强相关对
    strong_pairs = []
    for i, c1 in enumerate(numeric_cols):
        for c2 in numeric_cols[i + 1:]:
            val = abs(corr.loc[c1, c2])
            if val > 0.7:
                strong_pairs.append({
                    "col1": str(c1), "col2": str(c2),
                    "correlation": _safe_number(corr.loc[c1, c2]),
                })

    return {
        "success": True,
        "analysis_type": "correlation",
        "result": {
            "columns": [str(c) for c in numeric_cols],
            "correlation_matrix": corr_dict,
            "strong_pairs": strong_pairs,
        },
    }


def _analyze_distribution(df: pd.DataFrame) -> Dict[str, Any]:
    """分布分析"""
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    if not numeric_cols:
        return {"success": False, "error": "无数值列，无法进行分布分析"}

    distributions = {}
    for col in numeric_cols:
        col_data = df[col].dropna()
        desc = col_data.describe()
        distributions[str(col)] = {
            "count": int(desc.get("count", 0)),
            "mean": _safe_number(desc.get("mean")),
            "std": _safe_number(desc.get("std")),
            "min": _safe_number(desc.get("min")),
            "q1": _safe_number(desc.get("25%")),
            "median": _safe_number(desc.get("50%")),
            "q3": _safe_number(desc.get("75%")),
            "max": _safe_number(desc.get("max")),
            "skewness": _safe_number(col_data.skew()),
            "kurtosis": _safe_number(col_data.kurtosis()),
        }

    return {
        "success": True,
        "analysis_type": "distribution",
        "result": {
            "columns": [str(c) for c in numeric_cols],
            "distributions": distributions,
        },
    }


def _analyze_anomaly(df: pd.DataFrame, **kwargs) -> Dict[str, Any]:
    """异常值检测（基于 IQR）"""
    numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
    if not numeric_cols:
        return {"success": False, "error": "无数值列，无法检测异常值"}

    threshold = kwargs.get("threshold", 1.5)
    anomalies = {}

    for col in numeric_cols:
        col_data = df[col].dropna()
        q1 = col_data.quantile(0.25)
        q3 = col_data.quantile(0.75)
        iqr = q3 - q1
        lower = q1 - threshold * iqr
        upper = q3 + threshold * iqr

        outlier_mask = (col_data < lower) | (col_data > upper)
        outlier_count = int(outlier_mask.sum())
        outlier_rate = round(outlier_count / len(col_data), 4) if len(col_data) > 0 else 0

        if outlier_count > 0:
            anomalies[str(col)] = {
                "outlier_count": outlier_count,
                "outlier_rate": outlier_rate,
                "lower_bound": _safe_number(lower),
                "upper_bound": _safe_number(upper),
                "sample_values": [_safe_number(v) for v in col_data[outlier_mask].head(10).tolist()],
            }

    return {
        "success": True,
        "analysis_type": "anomaly",
        "result": {
            "method": "IQR",
            "threshold": threshold,
            "checked_columns": [str(c) for c in numeric_cols],
            "anomaly_columns": anomalies,
        },
    }


def _analyze_pivot(df: pd.DataFrame, **kwargs) -> Dict[str, Any]:
    """透视分析"""
    group_by = kwargs.get("group_by")
    aggregations = kwargs.get("aggregations")

    if not group_by or not aggregations:
        return {"success": False, "error": "透视分析需要 group_by 和 aggregations 参数"}

    if group_by not in df.columns:
        return {"success": False, "error": f"列 '{group_by}' 不存在"}

    try:
        agg_dict = {}
        for agg in aggregations:
            col = agg.get("column")
            func = agg.get("function", "sum")
            if col and col in df.columns:
                agg_dict[col] = func

        if not agg_dict:
            return {"success": False, "error": "无有效的聚合配置"}

        pivot = df.groupby(group_by).agg(agg_dict).reset_index()

        # 转为可序列化格式
        pivot_data = pivot.to_dict(orient="records")
        for row in pivot_data:
            for k, v in row.items():
                row[k] = _safe_number(v) if hasattr(v, "__float__") else str(v)

        return {
            "success": True,
            "analysis_type": "pivot",
            "result": {
                "group_by": group_by,
                "aggregations": agg_dict,
                "groups_count": len(pivot),
                "data": pivot_data,
            },
        }
    except Exception as e:
        return {"success": False, "error": f"透视分析失败: {e}"}


def _safe_number(val):
    """安全转换数值类型为可序列化的 Python 原生类型"""
    try:
        if hasattr(val, "item"):
            return val.item()
        if isinstance(val, float) and (val != val):  # NaN check
            return None
        return val
    except (ValueError, TypeError):
        return val
