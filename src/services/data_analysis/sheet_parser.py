"""
数据分析 - Excel/CSV 文件解析器

解析上传的 Excel 或 CSV 文件，提取 sheet 结构、列信息和采样数据。
"""

import os
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from loguru import logger


# 限制常量
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_ROWS_PER_SHEET = 10000
SAMPLE_ROW_COUNT = 20


class SheetParser:
    """Excel/CSV 文件解析器"""

    def parse_file(self, file_path: str) -> List[Dict[str, Any]]:
        """
        解析文件中的所有 sheet。

        Args:
            file_path: 文件路径（.xlsx / .xls / .csv）

        Returns:
            每个 sheet 的解析结果列表:
            [
                {
                    "sheet_name": str,
                    "rows": int,
                    "columns": List[str],
                    "sample_data": List[List],  # 前 20 行
                    "data_types": Dict[str, str],  # 列名 -> 推断类型
                },
                ...
            ]
        """
        # 文件大小检查
        file_size = os.path.getsize(file_path)
        if file_size > MAX_FILE_SIZE:
            raise ValueError(f"文件大小 {file_size / 1024 / 1024:.1f}MB 超过限制 50MB")

        ext = Path(file_path).suffix.lower()

        if ext == ".csv":
            return [self._parse_csv(file_path)]
        elif ext in (".xlsx", ".xls"):
            return self._parse_excel(file_path)
        else:
            raise ValueError(f"不支持的文件格式: {ext}，仅支持 .xlsx、.xls、.csv")

    def _parse_csv(self, file_path: str) -> Dict[str, Any]:
        """解析 CSV 文件"""
        # 尝试不同编码
        df = self._read_csv_with_encoding(file_path)
        return self._dataframe_to_sheet_info(df, sheet_name="Sheet1")

    def _parse_excel(self, file_path: str) -> List[Dict[str, Any]]:
        """解析 Excel 文件（所有 sheet）"""
        results = []
        # 读取所有 sheet 名
        xl = pd.ExcelFile(file_path, engine="openpyxl")
        for sheet_name in xl.sheet_names:
            df = pd.read_excel(xl, sheet_name=sheet_name, dtype=str, nrows=MAX_ROWS_PER_SHEET)
            results.append(self._dataframe_to_sheet_info(df, sheet_name=sheet_name))
        xl.close()
        return results

    def _read_csv_with_encoding(self, file_path: str) -> pd.DataFrame:
        """用多种编码尝试读取 CSV"""
        # 按优先级尝试编码
        encodings = ["utf-8", "utf-8-sig", "latin-1", "gbk", "gb2312"]

        for enc in encodings:
            try:
                df = pd.read_csv(
                    file_path,
                    encoding=enc,
                    dtype=str,
                    nrows=MAX_ROWS_PER_SHEET,
                )
                return df
            except (UnicodeDecodeError, UnicodeError):
                continue

        # 最后使用 chardet 检测
        try:
            import chardet

            with open(file_path, "rb") as f:
                raw = f.read(10000)
                detected = chardet.detect(raw)
                detected_enc = detected.get("encoding", "utf-8")
            df = pd.read_csv(
                file_path,
                encoding=detected_enc,
                dtype=str,
                nrows=MAX_ROWS_PER_SHEET,
            )
            return df
        except Exception as e:
            raise ValueError(f"无法解析 CSV 文件编码: {e}")

    def _dataframe_to_sheet_info(self, df: pd.DataFrame, sheet_name: str) -> Dict[str, Any]:
        """将 DataFrame 转换为 sheet 信息字典"""
        # 列名
        columns = list(df.columns)

        # 采样数据（前 SAMPLE_ROW_COUNT 行）
        sample_df = df.head(SAMPLE_ROW_COUNT)
        sample_data = []
        for _, row in sample_df.iterrows():
            sample_data.append([self._clean_value(v) for v in row.values])

        # 推断类型
        data_types = {}
        for col in columns:
            data_types[col] = self._infer_type(df[col])

        return {
            "sheet_name": sheet_name,
            "rows": len(df),
            "columns": columns,
            "sample_data": sample_data,
            "data_types": data_types,
        }

    def _infer_type(self, series: pd.Series) -> str:
        """
        推断列的数据类型。

        返回: text / integer / decimal / date / boolean
        """
        # 去掉空值
        non_null = series.dropna()
        if len(non_null) == 0:
            return "text"

        # 尝试检测 boolean
        unique_vals = set(str(v).strip().lower() for v in non_null.head(100))
        bool_sets = [
            {"true", "false"},
            {"1", "0"},
            {"yes", "no"},
            {"是", "否"},
        ]
        for bs in bool_sets:
            if unique_vals.issubset(bs):
                return "boolean"

        # 尝试检测 integer
        integer_count = 0
        for v in non_null.head(100):
            try:
                val = str(v).strip()
                int(val)
                # 排除科学计数法和前导零（如 "007"）
                if "." not in val and "e" not in val.lower() and (len(val) == 1 or not val.startswith("0")):
                    integer_count += 1
            except (ValueError, AttributeError):
                pass
        if integer_count >= len(non_null.head(100)) * 0.8:
            return "integer"

        # 尝试检测 decimal
        decimal_count = 0
        for v in non_null.head(100):
            try:
                val = str(v).strip()
                float(val)
                decimal_count += 1
            except (ValueError, AttributeError):
                pass
        if decimal_count >= len(non_null.head(100)) * 0.8:
            return "decimal"

        # 尝试检测 date
        date_count = 0
        for v in non_null.head(100):
            if self._is_date_like(str(v)):
                date_count += 1
        if date_count >= len(non_null.head(100)) * 0.5:
            return "date"

        return "text"

    def _is_date_like(self, value: str) -> bool:
        """检查字符串是否像日期"""
        import re

        value = value.strip()
        # 常见日期格式
        date_patterns = [
            r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}",  # 2024-01-15, 2024/01/15
            r"^\d{1,2}[-/]\d{1,2}[-/]\d{4}",  # 15-01-2024
            r"^\d{4}年\d{1,2}月\d{1,2}日",  # 2024年1月15日
        ]
        for pattern in date_patterns:
            if re.match(pattern, value):
                return True
        return False

    @staticmethod
    def _clean_value(value) -> Any:
        """清洗单元格值，转为适合 JSON 序列化的格式"""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        if isinstance(value, float) and value == int(value):
            return int(value)
        return str(value)
