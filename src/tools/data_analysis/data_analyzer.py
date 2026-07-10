"""
DataAnalyzer — pandas/numpy 数据分析执行引擎

提供 query/aggregate/merge/pivot/calculate/compare/trend/to_table/to_chart 共 9 个分析方法，
以及数据加载（load_table）和变量管理（_resolve_source/_store）。
"""

import math
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from loguru import logger

from src.services.data_analysis.crypto import decrypt_password
from src.services.data_analysis.db_connector import DatabaseConnector


# 中文字体配置
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 过滤操作符白名单
FILTER_OP_WHITELIST = {
    "eq", "neq", "gt", "gte", "lt", "lte",
    "in", "not_in", "contains", "startswith", "endswith",
    "not_contains", "is", "is_not",
}

# 聚合函数白名单
AGG_FUNC_WHITELIST = {
    "sum", "mean", "count", "min", "max",
    "median", "std", "var", "nunique", "first", "last",
}


class DataAnalyzer:
    """pandas/numpy 数据分析执行引擎"""

    CHART_OUTPUT_DIR = "storage/analysis_charts"
    DATA_OUTPUT_DIR = "storage/analysis_data"

    def __init__(self, session_id: str = None):
        self.session_id = session_id or ""
        self._variables: Dict[str, pd.DataFrame] = {}
        self._tables: Dict[str, pd.DataFrame] = {}
        self._table_meta: Dict[str, Dict] = {}

    # ==================== 数据加载 ====================

    async def load_table(self, metadata: dict) -> str:
        """
        根据 metadata 加载数据到内存。

        Args:
            metadata: 表元数据，包含 table_id/doc_id、source 等字段

        Returns:
            table_id 字符串
        """
        table_id = str(metadata.get("table_id", metadata.get("doc_id", "")))
        if not table_id:
            raise ValueError("metadata 中缺少 table_id 或 doc_id")

        source = metadata.get("source", {})
        source_type = source.get("type", "")
        df = None

        if source_type == "excel":
            df = self._load_excel(source)
        elif source_type == "database":
            df = await self._load_database(source)
        else:
            # 尝试文件路径直接加载
            file_path = source.get("file_path", "")
            if file_path and os.path.exists(file_path):
                df = self._load_from_path(file_path, source)

        if df is None:
            # 必须显式失败：否则调用方误以为加载成功，错误会延迟到 aggregate/query
            # 才以"数据源不存在"暴露，源头难以定位（见孤儿元数据事故）
            raise ValueError(
                f"加载数据表 {table_id} 失败：源数据不存在或无法读取，"
                f"请检查源文件/数据库连接器是否仍存在"
            )

        self._tables[table_id] = df
        self._table_meta[table_id] = metadata
        logger.info(f"[DataAnalyzer] 加载表 {table_id}: {len(df)} 行, {len(df.columns)} 列")
        return table_id

    def _load_excel(self, source: dict) -> Optional[pd.DataFrame]:
        """从 Excel/CSV 文件加载数据"""
        file_path = source.get("file_path", "")
        if not file_path or not os.path.exists(file_path):
            logger.error(f"[DataAnalyzer] 文件不存在: {file_path}")
            return None
        return self._load_from_path(file_path, source)

    def _load_from_path(self, file_path: str, source: dict = None) -> Optional[pd.DataFrame]:
        """根据文件路径加载数据"""
        try:
            if file_path.endswith(".csv"):
                return pd.read_csv(file_path)
            else:
                sheet_name = (source or {}).get("sheet_name", 0)
                return pd.read_excel(file_path, sheet_name=sheet_name)
        except Exception as e:
            logger.error(f"[DataAnalyzer] 加载文件失败 {file_path}: {e}", exc_info=True)
            return None

    async def _load_database(self, source: dict) -> Optional[pd.DataFrame]:
        """从数据库加载数据"""
        connector_id = source.get("connector_id")
        db_table_name = source.get("db_table_name")

        if not connector_id or not db_table_name:
            logger.error("[DataAnalyzer] 数据库加载缺少 connector_id 或 db_table_name")
            return None

        def _load():
            from src.db.database import get_db_connection

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT db_type, host, port, database_name, username, password_encrypted "
                    "FROM data_connectors WHERE id = %s",
                    (connector_id,),
                )
                record = cursor.fetchone()
                if not record:
                    raise ValueError(f"连接器不存在: {connector_id}")

                r = dict(record)
                config = {
                    "db_type": r["db_type"],
                    "host": r["host"],
                    "port": r["port"],
                    "database_name": r["database_name"],
                    "username": r["username"],
                    "password": decrypt_password(r["password_encrypted"]),
                }

                connector = DatabaseConnector()
                engine = None
                try:
                    from sqlalchemy import create_engine, text
                    from urllib.parse import quote_plus

                    url = connector._build_url(config)
                    engine = create_engine(
                        url,
                        connect_args={"connect_timeout": 10},
                    )
                    df = pd.read_sql(
                        f"SELECT * FROM {db_table_name} LIMIT 10000",
                        engine,
                    )
                    return df
                finally:
                    if engine:
                        engine.dispose()

        try:
            import asyncio
            return await asyncio.to_thread(_load)
        except Exception as e:
            logger.error(f"[DataAnalyzer] 数据库加载失败: {e}", exc_info=True)
            return None

    # ==================== 变量管理 ====================

    def _resolve_source(self, source_ref: str) -> Optional[pd.DataFrame]:
        """
        解析数据源引用，支持三种方式（按优先级）：
        1. 内存变量名（_variables）
        2. 原始表 table_id（_tables）
        3. 文件路径
        """
        if not source_ref:
            return None

        # 优先级 1: 中间变量
        if source_ref in self._variables:
            return self._variables[source_ref].copy()

        # 优先级 2: 原始表
        if source_ref in self._tables:
            return self._tables[source_ref].copy()

        # 优先级 3: 文件路径
        if os.path.exists(source_ref):
            try:
                if source_ref.endswith(".csv"):
                    df = pd.read_csv(source_ref)
                elif source_ref.endswith((".xlsx", ".xls")):
                    df = pd.read_excel(source_ref)
                else:
                    return None
                logger.info(f"[DataAnalyzer] 从文件加载: {source_ref}, {len(df)} 行")
                return df
            except Exception as e:
                logger.error(f"[DataAnalyzer] 文件加载失败 {source_ref}: {e}")
                return None

        return None

    def _store(self, var_name: str, df: pd.DataFrame) -> str:
        """持久化 DataFrame 到内存 + CSV 文件"""
        self._variables[var_name] = df.copy()

        os.makedirs(self.DATA_OUTPUT_DIR, exist_ok=True)
        file_path = os.path.join(self.DATA_OUTPUT_DIR, f"{var_name}.csv")
        df.to_csv(file_path, index=False, encoding="utf-8-sig")
        logger.info(f"[DataAnalyzer] 存储变量 {var_name}: {len(df)} 行 -> {file_path}")
        return file_path

    # ==================== 过滤引擎 ====================

    def _apply_filters(self, df: pd.DataFrame, filters: List[dict]) -> pd.DataFrame:
        """应用过滤条件，使用操作符白名单"""
        if not filters:
            return df

        mask = pd.Series([True] * len(df), index=df.index)

        for f in filters:
            col = f.get("column", "")
            op = f.get("op", "")
            val = f.get("value")

            if col not in df.columns:
                raise ValueError(f"列不存在: {col}")
            if op not in FILTER_OP_WHITELIST:
                raise ValueError(f"不支持的操作符: {op}")

            try:
                if op == "eq":
                    mask &= (df[col] == val)
                elif op == "neq":
                    mask &= (df[col] != val)
                elif op == "gt":
                    mask &= (df[col] > val)
                elif op == "gte":
                    mask &= (df[col] >= val)
                elif op == "lt":
                    mask &= (df[col] < val)
                elif op == "lte":
                    mask &= (df[col] <= val)
                elif op == "in":
                    values = val if isinstance(val, list) else [val]
                    mask &= df[col].isin(values)
                elif op == "not_in":
                    values = val if isinstance(val, list) else [val]
                    mask &= ~df[col].isin(values)
                elif op == "contains":
                    mask &= df[col].astype(str).str.contains(str(val), na=False)
                elif op == "not_contains":
                    mask &= ~df[col].astype(str).str.contains(str(val), na=False)
                elif op == "startswith":
                    mask &= df[col].astype(str).str.startswith(str(val), na=False)
                elif op == "endswith":
                    mask &= df[col].astype(str).str.endswith(str(val), na=False)
                elif op == "is":
                    if val is None or (isinstance(val, str) and val.lower() == "null"):
                        mask &= df[col].isna()
                    else:
                        mask &= (df[col] == val)
                elif op == "is_not":
                    if val is None or (isinstance(val, str) and val.lower() == "null"):
                        mask &= df[col].notna()
                    else:
                        mask &= (df[col] != val)
            except Exception as e:
                raise ValueError(f"过滤条件执行失败 [{col} {op} {val}]: {e}")

        return df[mask]

    # ==================== 数据获取层 ====================

    def query(
        self,
        source: str,
        columns: Optional[List[str]] = None,
        filters: Optional[List[dict]] = None,
        sort_by: Optional[str] = None,
        sort_order: str = "asc",
        limit: int = 100,
    ) -> pd.DataFrame:
        """
        数据查询：过滤 + 列选择 + 排序 + limit。

        Args:
            source: 数据源引用（变量名 / table_id / 文件路径）
            columns: 返回列名列表，None 返回全部
            filters: 过滤条件列表，每项 {"column", "op", "value"}
            sort_by: 排序字段
            sort_order: "asc" | "desc"
            limit: 返回行数上限

        Returns:
            过滤后的 DataFrame
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        # 过滤
        df = self._apply_filters(df, filters or [])

        # 列选择
        if columns:
            valid_cols = [c for c in columns if c in df.columns]
            if not valid_cols:
                raise ValueError(f"指定的列均不存在: {columns}")
            df = df[valid_cols]

        # 排序
        if sort_by:
            if sort_by not in df.columns:
                raise ValueError(f"排序列不存在: {sort_by}")
            ascending = sort_order.lower() != "desc"
            df = df.sort_values(by=sort_by, ascending=ascending)

        # limit
        df = df.head(limit).reset_index(drop=True)
        return df

    # ==================== 数据处理层 ====================

    def aggregate(
        self,
        source: str,
        group_by: List[str],
        aggregations: List[dict],
        sort_by: Optional[str] = None,
        sort_order: str = "desc",
        limit: int = 100,
    ) -> pd.DataFrame:
        """
        分组聚合：groupby + agg + MultiIndex 展平。

        Args:
            source: 数据源引用
            group_by: 分组字段列表
            aggregations: 聚合操作列表，每项 {"column", "function", "alias"(可选)}
            sort_by: 排序字段
            sort_order: "asc" | "desc"
            limit: 返回行数上限
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        # 校验 group_by 列
        for col in group_by:
            if col not in df.columns:
                raise ValueError(f"分组列不存在: {col}")

        # 构建聚合映射和别名
        agg_dict: Dict[str, list] = {}
        alias_map: Dict[tuple, str] = {}

        for agg in aggregations:
            col = agg.get("column", "")
            func = agg.get("function", "")
            alias = agg.get("alias", f"{col}_{func}")

            if col not in df.columns:
                raise ValueError(f"聚合列不存在: {col}")
            if func not in AGG_FUNC_WHITELIST:
                raise ValueError(f"不支持的聚合函数: {func}，支持: {', '.join(sorted(AGG_FUNC_WHITELIST))}")

            if col not in agg_dict:
                agg_dict[col] = []
            agg_dict[col].append(func)
            alias_map[(col, func)] = alias

        # 执行聚合
        grouped = df.groupby(group_by, dropna=False).agg(agg_dict)

        # 展平 MultiIndex 列名
        grouped.columns = [
            alias_map.get((col, func), f"{col}_{func}")
            for col, func in grouped.columns
        ]
        result = grouped.reset_index()

        # 排序
        if sort_by:
            if sort_by in result.columns:
                ascending = sort_order.lower() != "desc"
                result = result.sort_values(by=sort_by, ascending=ascending)

        result = result.head(limit).reset_index(drop=True)
        return result

    def merge(
        self,
        left_ref: str,
        right_ref: str,
        left_on: str,
        right_on: str,
        how: str = "left",
    ) -> pd.DataFrame:
        """
        表关联：pd.merge。

        Args:
            left_ref: 左表引用
            right_ref: 右表引用
            left_on: 左表关联列
            right_on: 右表关联列
            how: "left" | "inner" | "outer" | "right"
        """
        left_df = self._resolve_source(left_ref)
        if left_df is None:
            raise ValueError(f"左表不存在: {left_ref}")
        right_df = self._resolve_source(right_ref)
        if right_df is None:
            raise ValueError(f"右表不存在: {right_ref}")

        if left_on not in left_df.columns:
            raise ValueError(f"左表关联列不存在: {left_on}")
        if right_on not in right_df.columns:
            raise ValueError(f"右表关联列不存在: {right_on}")

        if how not in ("left", "inner", "outer", "right"):
            raise ValueError(f"不支持的关联方式: {how}")

        result = pd.merge(left_df, right_df, left_on=left_on, right_on=right_on, how=how)
        return result.reset_index(drop=True)

    def pivot(
        self,
        source: str,
        index: str,
        columns: str,
        values: str,
        agg_func: str = "sum",
    ) -> pd.DataFrame:
        """
        透视表：pd.pivot_table + 列名展平。

        Args:
            source: 数据源引用
            index: 行维度列
            columns: 列维度列
            values: 值列
            agg_func: 聚合函数
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        for col_name, col_label in [(index, "index"), (columns, "columns"), (values, "values")]:
            if col_name not in df.columns:
                raise ValueError(f"{col_label} 列不存在: {col_name}")

        result = pd.pivot_table(df, index=index, columns=columns, values=values, aggfunc=agg_func)

        # 展平列名
        if isinstance(result.columns, pd.MultiIndex):
            result.columns = ["_".join(str(c) for c in col).strip() for col in result.columns]
        else:
            result.columns = [str(c) for c in result.columns]

        return result.reset_index()

    def calculate(
        self,
        source: str,
        operations: List[dict],
    ) -> pd.DataFrame:
        """
        安全表达式求值：添加新列。

        Args:
            source: 数据源引用
            operations: 操作列表，每项 {"expr": "表达式", "alias": "新列名"}
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        result = df.copy()
        for op in operations:
            expr = op.get("expr", "")
            alias = op.get("alias", "")
            if not expr or not alias:
                raise ValueError("每个 operation 必须包含 expr 和 alias")

            result[alias] = self._eval_expression(result, expr)

        return result

    # ==================== 分析层 ====================

    def compare(
        self,
        source: str,
        compare_column: str,
        value_column: str,
        agg_func: str = "sum",
        top_n: int = 10,
        baseline_column: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        维度对比分析：按维度分组 + 聚合 + 占比 + 可选基准对比。

        Args:
            source: 数据源引用
            compare_column: 对比维度列
            value_column: 数值列
            agg_func: 聚合函数
            top_n: 返回前 N 个
            baseline_column: 可选基准列（用于计算比率）
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        if compare_column not in df.columns:
            raise ValueError(f"对比列不存在: {compare_column}")
        if value_column not in df.columns:
            raise ValueError(f"数值列不存在: {value_column}")

        # 按维度聚合
        result = df.groupby(compare_column, dropna=False).agg({value_column: agg_func}).reset_index()
        result = result.sort_values(by=value_column, ascending=False).head(top_n)

        # 计算占比
        total = result[value_column].sum()
        if total != 0:
            result["占比(%)"] = (result[value_column] / total * 100).round(2)
        else:
            result["占比(%)"] = 0.0

        # 基准对比
        if baseline_column and baseline_column in df.columns:
            baseline_df = df.groupby(compare_column, dropna=False).agg({baseline_column: agg_func}).reset_index()
            result = pd.merge(result, baseline_df, on=compare_column, how="left")
            result["比率"] = np.where(
                result[baseline_column] != 0,
                (result[value_column] / result[baseline_column]).round(4),
                np.nan,
            )

        return result.reset_index(drop=True)

    def trend(
        self,
        source: str,
        date_column: str,
        value_column: str,
        freq: str = "M",
        agg_func: str = "sum",
        group_by: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        时间趋势分析：日期转换 + 周期聚合 + 环比。

        Args:
            source: 数据源引用
            date_column: 日期列
            value_column: 数值列
            freq: "D"(天) / "W"(周) / "M"(月) / "Q"(季) / "Y"(年)
            agg_func: 聚合函数
            group_by: 可选分组列
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        if date_column not in df.columns:
            raise ValueError(f"日期列不存在: {date_column}")
        if value_column not in df.columns:
            raise ValueError(f"数值列不存在: {value_column}")

        # 日期转换
        df[date_column] = pd.to_datetime(df[date_column], errors="coerce")
        df = df.dropna(subset=[date_column])

        if df.empty:
            return pd.DataFrame()

        # 生成周期列
        df["period"] = df[date_column].dt.to_period(freq)

        # 分组聚合
        if group_by:
            if group_by not in df.columns:
                raise ValueError(f"分组列不存在: {group_by}")
            trend_df = df.groupby([group_by, "period"], dropna=False).agg({value_column: agg_func}).reset_index()
        else:
            trend_df = df.groupby("period", dropna=False).agg({value_column: agg_func}).reset_index()

        # 周期转字符串
        trend_df["period"] = trend_df["period"].astype(str)

        # 环比变化率
        if group_by:
            trend_df["环比变化率"] = trend_df.groupby(group_by)[value_column].pct_change()
        else:
            trend_df["环比变化率"] = trend_df[value_column].pct_change()

        return trend_df.reset_index(drop=True)

    def extract_hierarchy(
        self,
        source: str,
        path_column: str,
        target_level: int,
        separator: str = "/",
        value_column: Optional[str] = None,
        agg_func: str = "sum",
        sort_by: Optional[str] = None,
        sort_order: str = "desc",
    ) -> pd.DataFrame:
        """
        层级路径解析：从路径列中提取指定层级，并将子节点聚合到该层级。

        典型用途：部门层级路径 "公司/事业部/中心/部门/组" 中提取四级部门，
        并将五级（组）的数据汇总到四级（部门）。

        Args:
            source: 数据源引用
            path_column: 层级路径列名（如 "完整部门"）
            target_level: 目标层级（1-based，路径从根开始计，根为第1级）
            separator: 路径分隔符，默认 "/"
            value_column: 需要聚合的数值列（如 "签约总额"），不传则只返回层级映射
            agg_func: 聚合函数，默认 "sum"
            sort_by: 排序列名，不传则默认按聚合值降序（有 value_column 时）或按路径升序
            sort_order: 排序方向 "asc" 或 "desc"，默认 "desc"
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        if path_column not in df.columns:
            raise ValueError(f"路径列不存在: {path_column}")

        # 解析路径，提取目标层级名称和完整路径
        def parse_level(path_val, level):
            if pd.isna(path_val):
                return None
            parts = str(path_val).split(separator)
            if level < 1 or level > len(parts):
                return None
            return separator.join(parts[:level])

        level_name_col = f"第{target_level}级"
        df = df.copy()
        df["_target_path"] = df[path_column].apply(lambda x: parse_level(x, target_level))
        # 提取目标层级的短名称（路径最后一段）
        df[level_name_col] = df["_target_path"].apply(
            lambda x: x.split(separator)[-1] if pd.notna(x) else None
        )
        # 记录原始路径层级深度
        df["_path_depth"] = df[path_column].apply(
            lambda x: len(str(x).split(separator)) if pd.notna(x) else 0
        )

        # 过滤掉无法解析的行
        df = df[df["_target_path"].notna()].copy()

        ascending = sort_order == "asc"

        if value_column and value_column in df.columns:
            # 按目标层级聚合数值
            agg_result = df.groupby("_target_path", dropna=False).agg(
                {value_column: agg_func}
            ).reset_index()
            agg_result[level_name_col] = agg_result["_target_path"].apply(
                lambda x: x.split(separator)[-1]
            )
            # 重命名列
            agg_col = f"{value_column}({agg_func})"
            agg_result = agg_result.rename(columns={
                "_target_path": "完整路径",
                value_column: agg_col,
            })
            # 排序
            sort_col = sort_by if sort_by and sort_by in agg_result.columns else agg_col
            agg_result = agg_result.sort_values(
                by=sort_col, ascending=ascending
            ).reset_index(drop=True)
            return agg_result
        else:
            # 只返回层级映射：短名称 -> 完整路径
            mapping = df[["_target_path", level_name_col, "_path_depth"]].drop_duplicates(
                subset=["_target_path"]
            ).sort_values(by="_target_path").reset_index(drop=True)
            mapping = mapping.rename(columns={
                "_target_path": "完整路径",
                "_path_depth": "路径深度",
            })
            return mapping

    # ==================== 输出层 ====================

    def to_table(
        self,
        source: str,
        columns: Optional[List[str]] = None,
        max_rows: int = 50,
    ) -> dict:
        """
        DataFrame 转结构化输出（含 numpy 类型转换）。

        Returns:
            {"columns": [...], "rows": [[...], ...], "row_count": int, "total_count": int}
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        total = len(df)

        # 列选择
        if columns:
            valid_cols = [c for c in columns if c in df.columns]
            if valid_cols:
                df = df[valid_cols]

        # 截断
        df = df.head(max_rows)

        # numpy 类型转原生
        rows = []
        for _, row in df.iterrows():
            rows.append([self._to_native(v) for v in row.values])

        return {
            "columns": list(df.columns),
            "rows": rows,
            "row_count": len(rows),
            "total_count": total,
        }

    def to_chart(
        self,
        source: str,
        chart_type: str,
        x_column: str,
        y_columns: Optional[List[str]] = None,
        title: str = "数据分析",
        group_by: Optional[str] = None,
    ) -> dict:
        """
        生成图表并保存为 PNG。

        Args:
            source: 数据源引用
            chart_type: bar/line/pie/scatter/stacked_bar/grouped_bar
            x_column: X 轴列
            y_columns: Y 轴列列表
            title: 图表标题
            group_by: 分组列（暂不使用，预留）

        Returns:
            {"file_path": str, "chart_type": str, "title": str, "data_columns": [...]}
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        if x_column not in df.columns:
            raise ValueError(f"X 轴列不存在: {x_column}")

        # 默认取所有非 x_column 的数值列
        if not y_columns:
            y_columns = [c for c in df.columns if c != x_column and pd.api.types.is_numeric_dtype(df[c])]
        if not y_columns:
            raise ValueError("未指定 y_columns 且无法自动推断数值列")

        # 校验 y_columns
        for yc in y_columns:
            if yc not in df.columns:
                raise ValueError(f"Y 轴列不存在: {yc}")

        fig, ax = plt.subplots(figsize=(12, 6))
        x = df[x_column]
        x_range = range(len(x))

        if chart_type == "pie":
            values = df[y_columns[0]]
            ax.pie(values, labels=x, autopct="%1.1f%%", startangle=90)
            ax.set_title(title)

        elif chart_type in ("bar", "grouped_bar"):
            width = 0.8 / len(y_columns) if len(y_columns) > 1 else 0.6
            for i, y_col in enumerate(y_columns):
                offset = (i - len(y_columns) / 2 + 0.5) * width
                ax.bar([xi + offset for xi in x_range], df[y_col], width=width, label=y_col)
            ax.set_xticks(list(x_range))
            ax.set_xticklabels(x, rotation=45, ha="right")
            ax.set_title(title)
            ax.legend()
            ax.grid(axis="y", alpha=0.3)

        elif chart_type == "stacked_bar":
            bottom = np.zeros(len(x))
            for y_col in y_columns:
                ax.bar(x_range, df[y_col], bottom=bottom, label=y_col)
                bottom += df[y_col].values
            ax.set_xticks(list(x_range))
            ax.set_xticklabels(x, rotation=45, ha="right")
            ax.set_title(title)
            ax.legend()
            ax.grid(axis="y", alpha=0.3)

        elif chart_type == "line":
            for y_col in y_columns:
                ax.plot(x, df[y_col], marker="o", label=y_col)
            ax.set_title(title)
            ax.legend()
            ax.grid(axis="y", alpha=0.3)
            plt.xticks(rotation=45, ha="right")

        elif chart_type == "scatter":
            ax.scatter(df[x_column], df[y_columns[0]], alpha=0.6)
            if len(y_columns) > 1:
                for y_col in y_columns[1:]:
                    ax.scatter(df[x_column], df[y_col], alpha=0.6, label=y_col)
                ax.legend()
            ax.set_title(title)
            ax.grid(alpha=0.3)

        else:
            plt.close(fig)
            raise ValueError(f"不支持的图表类型: {chart_type}")

        # 保存
        os.makedirs(self.CHART_OUTPUT_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", title)
        file_path = os.path.join(self.CHART_OUTPUT_DIR, f"{safe_title}_{timestamp}.png")
        fig.savefig(file_path, dpi=150, bbox_inches="tight")
        plt.close(fig)

        logger.info(f"[DataAnalyzer] 图表已保存: {file_path}")

        return {
            "file_path": file_path,
            "chart_type": chart_type,
            "title": title,
            "data_columns": list(df.columns),
        }

    # ==================== 便捷方法 ====================

    def load_related(
        self,
        table_id: str,
        relation_name: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        便捷方法：根据 relations 配置自动关联加载。

        Args:
            table_id: 主表 table_id
            relation_name: 关联关系名称（None 取第一个）
        """
        meta = self._table_meta.get(table_id)
        if not meta:
            raise ValueError(f"表元数据不存在: {table_id}")

        relations = meta.get("relations", [])
        if not relations:
            raise ValueError(f"表 {table_id} 没有定义关联关系")

        # 查找关联
        rel = None
        if relation_name:
            for r in relations:
                if r.get("name") == relation_name:
                    rel = r
                    break
            if not rel:
                raise ValueError(f"关联关系不存在: {relation_name}")
        else:
            rel = relations[0]

        to_table_name = rel.get("to_table", "")
        from_column = rel.get("from_column", "")
        to_column = rel.get("to_column", "")

        if not to_table_name:
            raise ValueError("关联配置缺少 to_table")

        # 查找关联表的 table_id
        related_table_id = None
        for tid, tmeta in self._table_meta.items():
            if tmeta.get("table_name") == to_table_name:
                related_table_id = tid
                break

        if not related_table_id:
            raise ValueError(f"关联表 {to_table_name} 未加载，请先加载该表")

        # 获取数据
        left_df = self._resolve_source(table_id)
        right_df = self._resolve_source(related_table_id)
        if left_df is None or right_df is None:
            raise ValueError("关联表数据获取失败")

        result = pd.merge(
            left_df, right_df,
            left_on=from_column, right_on=to_column,
            how="left",
        )
        return result.reset_index(drop=True)

    # ==================== 表达式安全求值 ====================

    def _eval_expression(self, df: pd.DataFrame, expr: str):
        """安全表达式求值：列名替换 + if 转换 + 白名单命名空间"""
        # 列名替换（按长度降序，避免短名误替换长名子串）
        col_names = sorted(df.columns, key=len, reverse=True)
        eval_expr = expr
        for col in col_names:
            pattern = r"(?<![a-zA-Z0-9_一-鿿])" + re.escape(col) + r"(?![a-zA-Z0-9_一-鿿])"
            eval_expr = re.sub(pattern, f'__df__["{col}"]', eval_expr)

        # if → np.where 转换
        eval_expr = self._transform_if_expr(eval_expr)

        # 安全命名空间
        safe_namespace = {
            "__df__": df,
            "np": np,
            "round": round,
            "abs": abs,
            "min": min,
            "max": max,
            "len": len,
            "math": math,
            "sqrt": np.sqrt,
            "log": np.log,
            "ceil": np.ceil,
            "floor": np.floor,
            "to_float": lambda s: pd.to_numeric(s, errors="coerce"),
            "to_int": lambda s: pd.to_numeric(s, errors="coerce").astype("Int64"),
            # SQL/pandas 常用函数映射
            "null": np.nan,
            "isnull": lambda s: s.isnull(),
            "isna": lambda s: s.isna(),
            "notnull": lambda s: s.notnull(),
            "notna": lambda s: s.notna(),
            "coalesce": lambda *args: args[0].fillna(args[1]) if len(args) == 2 else pd.Series(np.where(args[0].isna(), args[1], args[0])),
            "length": lambda s: s.astype(str).str.len(),
            "len": len,
            "substr": lambda s, start, length=None: s.astype(str).str.slice(start, start + length if length else None),
            "substring": lambda s, start, length=None: s.astype(str).str.slice(start, start + length if length else None),
            "split_part": lambda s, sep, n: s.astype(str).str.split(sep).str[n - 1],
            "contains": lambda s, pat: s.astype(str).str.contains(pat, na=False),
            "startswith": lambda s, pat: s.astype(str).str.startswith(pat, na=False),
            "endswith": lambda s, pat: s.astype(str).str.endswith(pat, na=False),
            "lower": lambda s: s.astype(str).str.lower(),
            "upper": lambda s: s.astype(str).str.upper(),
            "trim": lambda s: s.astype(str).str.strip(),
            "replace": lambda s, old, new: s.astype(str).str.replace(old, new, regex=False),
            "concat": lambda *args: pd.concat([a.astype(str) for a in args], axis=1).agg("".join, axis=1),
            "cast": lambda s, dtype: s.astype(dtype),
            "date": lambda s: pd.to_datetime(s, errors="coerce"),
            "year": lambda s: pd.to_datetime(s, errors="coerce").dt.year,
            "month": lambda s: pd.to_datetime(s, errors="coerce").dt.month,
            "day": lambda s: pd.to_datetime(s, errors="coerce").dt.day,
        }

        return eval(eval_expr, {"__builtins__": {}}, safe_namespace)

    def describe(
        self,
        source: str,
        columns: Optional[List[str]] = None,
        max_unique: int = 20,
    ) -> Dict[str, Any]:
        """
        数据概览：返回指定列（或全部列）的统计摘要。
        包含 dtype、非空数、唯一值数、唯一值示例、数值列的 min/max/mean。

        Args:
            source: 数据源引用
            columns: 要统计的列名列表，None 返回全部列
            max_unique: 每列最多展示的唯一值数量
        """
        df = self._resolve_source(source)
        if df is None:
            raise ValueError(f"数据源不存在: {source}")

        target_cols = columns if columns else list(df.columns)
        col_stats = []

        for col in target_cols:
            if col not in df.columns:
                continue
            series = df[col]
            non_null = int(series.notna().sum())
            null_count = int(series.isna().sum())
            nunique = int(series.nunique(dropna=True))
            dtype = str(series.dtype)

            stat = {
                "column": col,
                "dtype": dtype,
                "non_null": non_null,
                "null_count": null_count,
                "unique_count": nunique,
            }

            # 唯一值示例
            if nunique <= max_unique:
                uniques = series.dropna().unique().tolist()
                stat["unique_values"] = [self._to_native(v) for v in uniques]
            else:
                top_vals = series.dropna().value_counts().head(max_unique)
                stat["top_values"] = [
                    {"value": self._to_native(v), "count": int(c)}
                    for v, c in top_vals.items()
                ]

            # 数值列统计
            if pd.api.types.is_numeric_dtype(series):
                desc = series.describe()
                stat["numeric_summary"] = {
                    "min": self._to_native(desc.get("min")),
                    "max": self._to_native(desc.get("max")),
                    "mean": round(float(desc.get("mean", 0)), 2),
                    "std": round(float(desc.get("std", 0)), 2),
                    "median": self._to_native(desc.get("50%")),
                }

            col_stats.append(stat)

        # 前 3 行样本数据，帮助 LLM 快速理解数据内容
        sample_rows = self._df_to_native_rows(df.head(3))

        return {
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns_summary": col_stats,
            "sample_rows": sample_rows,
            "sample_hint": "以上为前 3 行样本数据，总行数见 row_count。query/filter 时请确保 limit >= row_count",
        }

    def _transform_if_expr(self, expr: str) -> str:
        """将 if( 替换为 np.where(，支持嵌套"""
        result = []
        i = 0
        while i < len(expr):
            if expr[i:i + 3] == "if(" and (i == 0 or not expr[i - 1].isalnum()):
                result.append("np.where(")
                i += 3
            else:
                result.append(expr[i])
                i += 1
        return "".join(result)

    # ==================== 工具方法 ====================

    @staticmethod
    def _to_native(val) -> Any:
        """将 numpy 类型转为 Python 原生类型"""
        if val is None:
            return None
        if isinstance(val, np.integer):
            return int(val)
        if isinstance(val, np.floating):
            if np.isnan(val):
                return None
            return float(val)
        if isinstance(val, np.bool_):
            return bool(val)
        if isinstance(val, np.datetime64):
            return str(val)
        if isinstance(val, (pd.Timestamp,)):
            return str(val)
        try:
            if pd.isna(val):
                return None
        except (ValueError, TypeError):
            pass
        return val

    @staticmethod
    def _df_to_native_rows(df) -> List[List]:
        """将 DataFrame 行转为 Python 原生类型列表。"""
        rows = []
        for _, row in df.iterrows():
            rows.append([DataAnalyzer._to_native(v) for v in row])
        return rows
