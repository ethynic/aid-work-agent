"""
DataAnalyzer 单元测试

测试范围：数据加载、变量管理、9 个分析方法、安全表达式求值
"""

import os

import numpy as np
import pandas as pd
import pytest

from src.tools.data_analysis.data_analyzer import (
    AGG_FUNC_WHITELIST,
    FILTER_OP_WHITELIST,
    DataAnalyzer,
)

pytestmark = pytest.mark.tools


# ==================== Fixtures ====================


@pytest.fixture
def analyzer():
    """干净的 DataAnalyzer 实例"""
    return DataAnalyzer(session_id="test-session")


@pytest.fixture
def sample_df():
    """通用测试数据"""
    return pd.DataFrame({
        "region": ["华东", "华东", "华北", "华北", "华南", "华南", "华东", "华北"],
        "product": ["A", "B", "A", "B", "A", "B", "A", "A"],
        "sales": [100, 200, 150, 250, 180, 220, 120, 130],
        "cost": [60, 110, 80, 140, 90, 120, 70, 65],
        "date": [
            "2024-01-15", "2024-02-20", "2024-01-10", "2024-03-15",
            "2024-02-25", "2024-03-30", "2024-04-10", "2024-05-20",
        ],
    })


@pytest.fixture
def loaded_analyzer(analyzer, sample_df):
    """已注册测试表的 analyzer"""
    analyzer._tables["t1"] = sample_df
    analyzer._table_meta["t1"] = {"table_name": "sales_data", "relations": []}
    return analyzer


# ==================== 初始化 ====================


class TestDataAnalyzerInit:

    def test_default_session_id(self):
        da = DataAnalyzer()
        assert da.session_id == ""

    def test_custom_session_id(self):
        da = DataAnalyzer(session_id="s123")
        assert da.session_id == "s123"

    def test_empty_state(self):
        da = DataAnalyzer()
        assert da._variables == {}
        assert da._tables == {}
        assert da._table_meta == {}


# ==================== 数据加载：load_table ====================


class TestLoadTable:

    @pytest.mark.asyncio
    async def test_load_csv_file(self, analyzer, tmp_path):
        csv_path = str(tmp_path / "test.csv")
        pd.DataFrame({"a": [1, 2], "b": [3, 4]}).to_csv(csv_path, index=False)

        tid = await analyzer.load_table({
            "table_id": "csv1",
            "source": {"type": "excel", "file_path": csv_path},
        })
        assert tid == "csv1"
        assert "csv1" in analyzer._tables
        assert len(analyzer._tables["csv1"]) == 2

    @pytest.mark.asyncio
    async def test_load_excel_file(self, analyzer, tmp_path):
        xlsx_path = str(tmp_path / "test.xlsx")
        pd.DataFrame({"x": [10, 20], "y": [30, 40]}).to_excel(xlsx_path, index=False)

        tid = await analyzer.load_table({
            "table_id": "xlsx1",
            "source": {"type": "excel", "file_path": xlsx_path},
        })
        assert tid == "xlsx1"
        assert len(analyzer._tables["xlsx1"]) == 2

    @pytest.mark.asyncio
    async def test_load_with_doc_id_fallback(self, analyzer, tmp_path):
        csv_path = str(tmp_path / "test.csv")
        pd.DataFrame({"a": [1]}).to_csv(csv_path, index=False)

        tid = await analyzer.load_table({
            "doc_id": "fallback_id",
            "source": {"type": "excel", "file_path": csv_path},
        })
        assert tid == "fallback_id"

    @pytest.mark.asyncio
    async def test_load_file_not_found_raises(self, analyzer):
        """源文件不存在时必须显式抛异常，避免静默成功让错误延迟到 aggregate 才暴露"""
        with pytest.raises(ValueError, match="源数据不存在或无法读取"):
            await analyzer.load_table({
                "table_id": "missing",
                "source": {"type": "excel", "file_path": "/nonexistent/path.csv"},
            })
        assert "missing" not in analyzer._tables

    @pytest.mark.asyncio
    async def test_load_missing_table_id_raises(self, analyzer):
        with pytest.raises(ValueError, match="缺少 table_id"):
            await analyzer.load_table({"source": {"type": "excel"}})

    @pytest.mark.asyncio
    async def test_load_database_missing_params_raises(self, analyzer):
        """数据库加载缺少 connector_id 必须显式抛异常"""
        with pytest.raises(ValueError, match="源数据不存在或无法读取"):
            await analyzer.load_table({
                "table_id": "db1",
                "source": {"type": "database"},
            })
        assert "db1" not in analyzer._tables


# ==================== 变量管理：_resolve_source / _store ====================


class TestVariableManagement:

    def test_resolve_from_variables(self, analyzer):
        df = pd.DataFrame({"a": [1]})
        analyzer._variables["v1"] = df
        result = analyzer._resolve_source("v1")
        assert result is not None
        assert len(result) == 1
        # 返回的是 copy，修改不影响原数据
        result["a"] = 999
        assert analyzer._variables["v1"]["a"].iloc[0] == 1

    def test_resolve_from_tables(self, analyzer):
        df = pd.DataFrame({"a": [1]})
        analyzer._tables["t1"] = df
        result = analyzer._resolve_source("t1")
        assert result is not None

    def test_resolve_priority_variable_over_table(self, analyzer):
        analyzer._variables["x"] = pd.DataFrame({"val": [1]})
        analyzer._tables["x"] = pd.DataFrame({"val": [2]})
        result = analyzer._resolve_source("x")
        assert result["val"].iloc[0] == 1

    def test_resolve_from_csv_file(self, analyzer, tmp_path):
        csv_path = str(tmp_path / "data.csv")
        pd.DataFrame({"col": [42]}).to_csv(csv_path, index=False)
        result = analyzer._resolve_source(csv_path)
        assert result is not None
        assert result["col"].iloc[0] == 42

    def test_resolve_nonexistent_returns_none(self, analyzer):
        assert analyzer._resolve_source("no_such_var") is None

    def test_resolve_empty_string_returns_none(self, analyzer):
        assert analyzer._resolve_source("") is None

    def test_store_returns_path(self, analyzer):
        df = pd.DataFrame({"a": [1, 2, 3]})
        path = analyzer._store("my_var", df)
        assert path.endswith("my_var.csv")
        assert os.path.exists(path)

    def test_store_preserves_data(self, analyzer, tmp_path):
        df = pd.DataFrame({"x": [10, 20]})
        analyzer._store("s1", df)
        assert "s1" in analyzer._variables
        assert len(analyzer._variables["s1"]) == 2

    def test_store_csv_readable(self, analyzer):
        df = pd.DataFrame({"name": ["张三", "李四"], "age": [25, 30]})
        path = analyzer._store("chinese", df)
        loaded = pd.read_csv(path)
        assert loaded["name"].iloc[0] == "张三"


# ==================== 过滤引擎：_apply_filters ====================


class TestApplyFilters:

    def test_eq(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [{"column": "region", "op": "eq", "value": "华东"}],
        )
        assert all(result["region"] == "华东")
        assert len(result) == 3

    def test_neq(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [{"column": "region", "op": "neq", "value": "华东"}],
        )
        assert all(result["region"] != "华东")

    def test_gt(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [{"column": "sales", "op": "gt", "value": 150}],
        )
        assert all(result["sales"] > 150)

    def test_gte(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [{"column": "sales", "op": "gte", "value": 200}],
        )
        assert all(result["sales"] >= 200)

    def test_lt(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [{"column": "sales", "op": "lt", "value": 150}],
        )
        assert all(result["sales"] < 150)

    def test_lte(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [{"column": "sales", "op": "lte", "value": 130}],
        )
        assert all(result["sales"] <= 130)

    def test_in_with_list(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [{"column": "region", "op": "in", "value": ["华东", "华南"]}],
        )
        assert set(result["region"]).issubset({"华东", "华南"})

    def test_in_with_single_value(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [{"column": "region", "op": "in", "value": "华东"}],
        )
        assert all(result["region"] == "华东")

    def test_not_in(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [{"column": "region", "op": "not_in", "value": ["华东"]}],
        )
        assert "华东" not in result["region"].values

    def test_contains(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [{"column": "region", "op": "contains", "value": "华"}],
        )
        assert len(result) == 8  # 所有区域都含"华"

    def test_startswith(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [{"column": "region", "op": "startswith", "value": "华东"}],
        )
        assert all(result["region"].str.startswith("华东"))

    def test_unsupported_op_raises(self, loaded_analyzer):
        with pytest.raises(ValueError, match="不支持的操作符"):
            loaded_analyzer._apply_filters(
                loaded_analyzer._tables["t1"],
                [{"column": "sales", "op": "regex", "value": ".*"}],
            )

    def test_missing_column_raises(self, loaded_analyzer):
        with pytest.raises(ValueError, match="列不存在"):
            loaded_analyzer._apply_filters(
                loaded_analyzer._tables["t1"],
                [{"column": "no_col", "op": "eq", "value": 1}],
            )

    def test_empty_filters_returns_all(self, loaded_analyzer):
        df = loaded_analyzer._tables["t1"]
        result = loaded_analyzer._apply_filters(df, [])
        assert len(result) == len(df)

    def test_multiple_filters_combined(self, loaded_analyzer):
        result = loaded_analyzer._apply_filters(
            loaded_analyzer._tables["t1"],
            [
                {"column": "region", "op": "eq", "value": "华东"},
                {"column": "sales", "op": "gt", "value": 100},
            ],
        )
        assert all(result["region"] == "华东")
        assert all(result["sales"] > 100)


# ==================== query ====================


class TestQuery:

    def test_basic_query(self, loaded_analyzer):
        result = loaded_analyzer.query("t1")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 8

    def test_query_with_columns(self, loaded_analyzer):
        result = loaded_analyzer.query("t1", columns=["region", "sales"])
        assert list(result.columns) == ["region", "sales"]

    def test_query_with_filter(self, loaded_analyzer):
        result = loaded_analyzer.query(
            "t1", filters=[{"column": "region", "op": "eq", "value": "华东"}]
        )
        assert all(result["region"] == "华东")

    def test_query_with_sort_desc(self, loaded_analyzer):
        result = loaded_analyzer.query("t1", sort_by="sales", sort_order="desc")
        assert result["sales"].iloc[0] == 250

    def test_query_with_sort_asc(self, loaded_analyzer):
        result = loaded_analyzer.query("t1", sort_by="sales", sort_order="asc")
        assert result["sales"].iloc[0] == 100

    def test_query_with_limit(self, loaded_analyzer):
        result = loaded_analyzer.query("t1", limit=3)
        assert len(result) == 3

    def test_query_combined(self, loaded_analyzer):
        result = loaded_analyzer.query(
            "t1",
            columns=["region", "sales"],
            filters=[{"column": "sales", "op": "gte", "value": 150}],
            sort_by="sales",
            sort_order="desc",
            limit=2,
        )
        assert len(result) == 2
        assert result["sales"].iloc[0] >= result["sales"].iloc[1]

    def test_query_source_not_found(self, loaded_analyzer):
        with pytest.raises(ValueError, match="数据源不存在"):
            loaded_analyzer.query("no_table")

    def test_query_invalid_sort_column(self, loaded_analyzer):
        with pytest.raises(ValueError, match="排序列不存在"):
            loaded_analyzer.query("t1", sort_by="no_col")

    def test_query_all_invalid_columns(self, loaded_analyzer):
        with pytest.raises(ValueError, match="指定的列均不存在"):
            loaded_analyzer.query("t1", columns=["xxx", "yyy"])

    def test_query_invalid_filter_op(self, loaded_analyzer):
        with pytest.raises(ValueError, match="不支持的操作符"):
            loaded_analyzer.query(
                "t1",
                filters=[{"column": "sales", "op": "invalid", "value": 1}],
            )


# ==================== aggregate ====================


class TestAggregate:

    def test_single_agg(self, loaded_analyzer):
        result = loaded_analyzer.aggregate(
            "t1",
            group_by=["region"],
            aggregations=[{"column": "sales", "function": "sum", "alias": "总销售额"}],
        )
        assert "总销售额" in result.columns
        assert "region" in result.columns

    def test_multiple_agg_functions(self, loaded_analyzer):
        result = loaded_analyzer.aggregate(
            "t1",
            group_by=["region"],
            aggregations=[
                {"column": "sales", "function": "sum", "alias": "total"},
                {"column": "sales", "function": "mean", "alias": "avg"},
                {"column": "sales", "function": "count", "alias": "cnt"},
            ],
        )
        assert "total" in result.columns
        assert "avg" in result.columns
        assert "cnt" in result.columns

    def test_default_alias(self, loaded_analyzer):
        result = loaded_analyzer.aggregate(
            "t1",
            group_by=["region"],
            aggregations=[{"column": "sales", "function": "sum"}],
        )
        assert "sales_sum" in result.columns

    def test_agg_with_sort(self, loaded_analyzer):
        result = loaded_analyzer.aggregate(
            "t1",
            group_by=["region"],
            aggregations=[{"column": "sales", "function": "sum", "alias": "total"}],
            sort_by="total",
            sort_order="desc",
        )
        assert result["total"].iloc[0] > result["total"].iloc[-1]

    def test_agg_with_limit(self, loaded_analyzer):
        result = loaded_analyzer.aggregate(
            "t1",
            group_by=["region"],
            aggregations=[{"column": "sales", "function": "sum"}],
            limit=2,
        )
        assert len(result) == 2

    def test_agg_invalid_group_column(self, loaded_analyzer):
        with pytest.raises(ValueError, match="分组列不存在"):
            loaded_analyzer.aggregate(
                "t1",
                group_by=["no_col"],
                aggregations=[{"column": "sales", "function": "sum"}],
            )

    def test_agg_invalid_agg_column(self, loaded_analyzer):
        with pytest.raises(ValueError, match="聚合列不存在"):
            loaded_analyzer.aggregate(
                "t1",
                group_by=["region"],
                aggregations=[{"column": "no_col", "function": "sum"}],
            )

    def test_agg_unsupported_function(self, loaded_analyzer):
        with pytest.raises(ValueError, match="不支持的聚合函数"):
            loaded_analyzer.aggregate(
                "t1",
                group_by=["region"],
                aggregations=[{"column": "sales", "function": "custom_func"}],
            )

    def test_agg_source_not_found(self, loaded_analyzer):
        with pytest.raises(ValueError, match="数据源不存在"):
            loaded_analyzer.aggregate(
                "no_table",
                group_by=["region"],
                aggregations=[{"column": "sales", "function": "sum"}],
            )


# ==================== merge ====================


class TestMerge:

    def test_left_merge(self, analyzer):
        analyzer._variables["left"] = pd.DataFrame({"id": [1, 2, 3], "name": ["A", "B", "C"]})
        analyzer._variables["right"] = pd.DataFrame({"id": [1, 2, 4], "score": [90, 85, 70]})
        result = analyzer.merge("left", "right", "id", "id", how="left")
        assert len(result) == 3
        assert result["score"].iloc[2] is np.nan or pd.isna(result["score"].iloc[2])

    def test_inner_merge(self, analyzer):
        analyzer._variables["left"] = pd.DataFrame({"id": [1, 2, 3], "a": [10, 20, 30]})
        analyzer._variables["right"] = pd.DataFrame({"id": [1, 2, 4], "b": [40, 50, 60]})
        result = analyzer.merge("left", "right", "id", "id", how="inner")
        assert len(result) == 2

    def test_outer_merge(self, analyzer):
        analyzer._variables["left"] = pd.DataFrame({"id": [1, 3], "a": [10, 30]})
        analyzer._variables["right"] = pd.DataFrame({"id": [2, 3], "b": [50, 60]})
        result = analyzer.merge("left", "right", "id", "id", how="outer")
        assert len(result) == 3

    def test_merge_left_table_not_found(self, analyzer):
        analyzer._variables["right"] = pd.DataFrame({"id": [1], "v": [1]})
        with pytest.raises(ValueError, match="左表不存在"):
            analyzer.merge("no_left", "right", "id", "id")

    def test_merge_right_table_not_found(self, analyzer):
        analyzer._variables["left"] = pd.DataFrame({"id": [1], "v": [1]})
        with pytest.raises(ValueError, match="右表不存在"):
            analyzer.merge("left", "no_right", "id", "id")

    def test_merge_left_column_not_found(self, analyzer):
        analyzer._variables["left"] = pd.DataFrame({"id": [1], "v": [1]})
        analyzer._variables["right"] = pd.DataFrame({"id": [1], "v": [1]})
        with pytest.raises(ValueError, match="左表关联列不存在"):
            analyzer.merge("left", "right", "no_col", "id")

    def test_merge_right_column_not_found(self, analyzer):
        analyzer._variables["left"] = pd.DataFrame({"id": [1], "v": [1]})
        analyzer._variables["right"] = pd.DataFrame({"id": [1], "v": [1]})
        with pytest.raises(ValueError, match="右表关联列不存在"):
            analyzer.merge("left", "right", "id", "no_col")

    def test_merge_invalid_how(self, analyzer):
        analyzer._variables["left"] = pd.DataFrame({"id": [1], "v": [1]})
        analyzer._variables["right"] = pd.DataFrame({"id": [1], "v": [1]})
        with pytest.raises(ValueError, match="不支持的关联方式"):
            analyzer.merge("left", "right", "id", "id", how="cross")


# ==================== pivot ====================


class TestPivot:

    def test_basic_pivot(self, loaded_analyzer):
        result = loaded_analyzer.pivot("t1", index="region", columns="product", values="sales")
        assert "region" in result.columns
        assert len(result) == 3  # 3 个区域

    def test_pivot_missing_index(self, loaded_analyzer):
        with pytest.raises(ValueError, match="index 列不存在"):
            loaded_analyzer.pivot("t1", index="no_col", columns="product", values="sales")

    def test_pivot_missing_columns(self, loaded_analyzer):
        with pytest.raises(ValueError, match="columns 列不存在"):
            loaded_analyzer.pivot("t1", index="region", columns="no_col", values="sales")

    def test_pivot_missing_values(self, loaded_analyzer):
        with pytest.raises(ValueError, match="values 列不存在"):
            loaded_analyzer.pivot("t1", index="region", columns="product", values="no_col")

    def test_pivot_source_not_found(self, loaded_analyzer):
        with pytest.raises(ValueError, match="数据源不存在"):
            loaded_analyzer.pivot("no_table", index="a", columns="b", values="c")


# ==================== calculate ====================


class TestCalculate:

    def test_arithmetic(self, loaded_analyzer):
        result = loaded_analyzer.calculate(
            "t1",
            operations=[{"expr": "sales * 1.1", "alias": "taxed"}],
        )
        assert "taxed" in result.columns
        assert result["taxed"].iloc[0] == pytest.approx(110.0)

    def test_if_condition(self, loaded_analyzer):
        result = loaded_analyzer.calculate(
            "t1",
            operations=[{"expr": 'if(sales > 150, "high", "low")', "alias": "level"}],
        )
        assert set(result["level"].unique()) == {"high", "low"}

    def test_math_functions(self, loaded_analyzer):
        result = loaded_analyzer.calculate(
            "t1",
            operations=[
                {"expr": "round(sales / 3, 2)", "alias": "rounded"},
                {"expr": "sqrt(sales)", "alias": "root"},
            ],
        )
        assert "rounded" in result.columns
        assert "root" in result.columns
        assert result["root"].iloc[0] == pytest.approx(10.0)

    def test_nested_if(self, loaded_analyzer):
        result = loaded_analyzer.calculate(
            "t1",
            operations=[{
                "expr": 'if(sales > 200, "A", if(sales > 150, "B", "C"))',
                "alias": "grade",
            }],
        )
        assert set(result["grade"].unique()).issubset({"A", "B", "C"})

    def test_multi_column_expr(self, loaded_analyzer):
        result = loaded_analyzer.calculate(
            "t1",
            operations=[{"expr": "sales - cost", "alias": "profit"}],
        )
        assert "profit" in result.columns
        assert result["profit"].iloc[0] == 40  # 100 - 60

    def test_calculate_missing_expr(self, loaded_analyzer):
        with pytest.raises(ValueError, match="必须包含 expr 和 alias"):
            loaded_analyzer.calculate("t1", operations=[{"alias": "x"}])

    def test_calculate_missing_alias(self, loaded_analyzer):
        with pytest.raises(ValueError, match="必须包含 expr 和 alias"):
            loaded_analyzer.calculate("t1", operations=[{"expr": "1+1"}])

    def test_calculate_source_not_found(self, loaded_analyzer):
        with pytest.raises(ValueError, match="数据源不存在"):
            loaded_analyzer.calculate("no_table", operations=[{"expr": "1", "alias": "x"}])

    def test_column_name_not_substring_replaced(self, loaded_analyzer):
        """验证短列名不会误替换长列名中的子串"""
        df = pd.DataFrame({"price": [10], "unit_price": [5]})
        analyzer = DataAnalyzer()
        analyzer._variables["test"] = df
        result = analyzer.calculate(
            "test",
            operations=[{"expr": "unit_price * 2", "alias": "doubled"}],
        )
        assert result["doubled"].iloc[0] == 10


# ==================== compare ====================


class TestCompare:

    def test_basic_compare(self, loaded_analyzer):
        result = loaded_analyzer.compare("t1", compare_column="region", value_column="sales")
        assert "占比(%)" in result.columns
        assert result["占比(%)"].sum() == pytest.approx(100.0, abs=0.1)

    def test_compare_with_top_n(self, loaded_analyzer):
        result = loaded_analyzer.compare("t1", compare_column="product", value_column="sales", top_n=2)
        assert len(result) == 2

    def test_compare_with_baseline(self, loaded_analyzer):
        result = loaded_analyzer.compare(
            "t1",
            compare_column="region",
            value_column="sales",
            baseline_column="cost",
        )
        assert "比率" in result.columns
        assert "cost" in result.columns

    def test_compare_zero_total(self, analyzer):
        """总和为 0 时占比应为 0.0"""
        analyzer._variables["zeros"] = pd.DataFrame({"dim": ["A", "B"], "val": [0, 0]})
        result = analyzer.compare("zeros", compare_column="dim", value_column="val")
        assert all(result["占比(%)"] == 0.0)

    def test_compare_missing_column(self, loaded_analyzer):
        with pytest.raises(ValueError, match="对比列不存在"):
            loaded_analyzer.compare("t1", compare_column="no_col", value_column="sales")

    def test_compare_missing_value_column(self, loaded_analyzer):
        with pytest.raises(ValueError, match="数值列不存在"):
            loaded_analyzer.compare("t1", compare_column="region", value_column="no_col")

    def test_compare_source_not_found(self, loaded_analyzer):
        with pytest.raises(ValueError, match="数据源不存在"):
            loaded_analyzer.compare("no_table", compare_column="a", value_column="b")


# ==================== trend ====================


class TestTrend:

    def test_monthly_trend(self, loaded_analyzer):
        result = loaded_analyzer.trend("t1", date_column="date", value_column="sales", freq="M")
        assert "period" in result.columns
        assert "环比变化率" in result.columns
        assert len(result) >= 1

    def test_trend_with_group_by(self, loaded_analyzer):
        result = loaded_analyzer.trend(
            "t1", date_column="date", value_column="sales", freq="M", group_by="region",
        )
        assert "region" in result.columns

    def test_trend_first_row_pct_change_is_nan(self, loaded_analyzer):
        result = loaded_analyzer.trend("t1", date_column="date", value_column="sales", freq="M")
        assert pd.isna(result["环比变化率"].iloc[0])

    def test_trend_invalid_date(self, analyzer):
        """无效日期行应被自动丢弃"""
        analyzer._variables["mixed"] = pd.DataFrame({
            "dt": ["2024-01-01", "not-a-date", "2024-02-01"],
            "val": [10, 20, 30],
        })
        result = analyzer.trend("mixed", date_column="dt", value_column="val", freq="M")
        assert len(result) == 2  # 只有 2 个有效日期行

    def test_trend_all_invalid_dates(self, analyzer):
        """全部无效日期应返回空 DataFrame"""
        analyzer._variables["bad"] = pd.DataFrame({
            "dt": ["not-a-date", "also-bad"],
            "val": [10, 20],
        })
        result = analyzer.trend("bad", date_column="dt", value_column="val")
        assert result.empty

    def test_trend_missing_date_column(self, loaded_analyzer):
        with pytest.raises(ValueError, match="日期列不存在"):
            loaded_analyzer.trend("t1", date_column="no_col", value_column="sales")

    def test_trend_missing_value_column(self, loaded_analyzer):
        with pytest.raises(ValueError, match="数值列不存在"):
            loaded_analyzer.trend("t1", date_column="date", value_column="no_col")

    def test_trend_missing_group_by_column(self, loaded_analyzer):
        with pytest.raises(ValueError, match="分组列不存在"):
            loaded_analyzer.trend("t1", date_column="date", value_column="sales", group_by="no_col")

    def test_trend_source_not_found(self, loaded_analyzer):
        with pytest.raises(ValueError, match="数据源不存在"):
            loaded_analyzer.trend("no_table", date_column="d", value_column="v")


# ==================== to_table ====================


class TestToTable:

    def test_basic_to_table(self, loaded_analyzer):
        result = loaded_analyzer.to_table("t1")
        assert "columns" in result
        assert "rows" in result
        assert result["row_count"] == 8
        assert result["total_count"] == 8

    def test_to_table_with_columns(self, loaded_analyzer):
        result = loaded_analyzer.to_table("t1", columns=["region", "sales"])
        assert result["columns"] == ["region", "sales"]

    def test_to_table_max_rows(self, loaded_analyzer):
        result = loaded_analyzer.to_table("t1", max_rows=3)
        assert result["row_count"] == 3
        assert result["total_count"] == 8

    def test_to_table_numpy_type_conversion(self, analyzer):
        df = pd.DataFrame({
            "int_col": np.array([1, 2], dtype=np.int64),
            "float_col": np.array([1.5, 2.5], dtype=np.float64),
            "bool_col": np.array([True, False], dtype=np.bool_),
        })
        analyzer._variables["types"] = df
        result = analyzer.to_table("types")
        row = result["rows"][0]
        assert isinstance(row[0], int)
        assert isinstance(row[1], float)
        assert isinstance(row[2], bool)

    def test_to_table_nan_to_none(self, analyzer):
        df = pd.DataFrame({"val": [1.0, np.nan]})
        analyzer._variables["nan_test"] = df
        result = analyzer.to_table("nan_test")
        assert result["rows"][1][0] is None

    def test_to_table_source_not_found(self, loaded_analyzer):
        with pytest.raises(ValueError, match="数据源不存在"):
            loaded_analyzer.to_table("no_table")


# ==================== to_chart ====================


class TestToChart:

    def test_bar_chart(self, loaded_analyzer, tmp_path):
        loaded_analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        agg = loaded_analyzer.aggregate("t1", group_by=["region"], aggregations=[
            {"column": "sales", "function": "sum", "alias": "total"},
        ])
        loaded_analyzer._store("chart_data", agg)
        result = loaded_analyzer.to_chart("chart_data", chart_type="bar", x_column="region", y_columns=["total"])
        assert os.path.exists(result["file_path"])
        assert result["chart_type"] == "bar"

    def test_line_chart(self, analyzer, tmp_path):
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["line_data"] = pd.DataFrame({
            "month": ["Jan", "Feb", "Mar"],
            "val": [10, 20, 30],
        })
        result = analyzer.to_chart("line_data", chart_type="line", x_column="month", y_columns=["val"])
        assert os.path.exists(result["file_path"])

    def test_pie_chart(self, analyzer, tmp_path):
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["pie_data"] = pd.DataFrame({
            "label": ["A", "B", "C"],
            "value": [30, 50, 20],
        })
        result = analyzer.to_chart("pie_data", chart_type="pie", x_column="label", y_columns=["value"])
        assert os.path.exists(result["file_path"])

    def test_scatter_chart(self, analyzer, tmp_path):
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["scatter_data"] = pd.DataFrame({
            "x": [1, 2, 3, 4, 5],
            "y": [2, 4, 5, 4, 5],
        })
        result = analyzer.to_chart("scatter_data", chart_type="scatter", x_column="x", y_columns=["y"])
        assert os.path.exists(result["file_path"])

    def test_stacked_bar_chart(self, analyzer, tmp_path):
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["stacked"] = pd.DataFrame({
            "cat": ["A", "B"],
            "v1": [10, 20],
            "v2": [30, 40],
        })
        result = analyzer.to_chart("stacked", chart_type="stacked_bar", x_column="cat", y_columns=["v1", "v2"])
        assert os.path.exists(result["file_path"])

    def test_grouped_bar_chart(self, analyzer, tmp_path):
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["grouped"] = pd.DataFrame({
            "cat": ["A", "B"],
            "v1": [10, 20],
            "v2": [30, 40],
        })
        result = analyzer.to_chart("grouped", chart_type="grouped_bar", x_column="cat", y_columns=["v1", "v2"])
        assert os.path.exists(result["file_path"])

    def test_chart_auto_detect_y_columns(self, analyzer, tmp_path):
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["auto_y"] = pd.DataFrame({
            "name": ["A", "B"],
            "val1": [10, 20],
            "val2": [30, 40],
        })
        result = analyzer.to_chart("auto_y", chart_type="bar", x_column="name")
        assert len(result["data_columns"]) >= 2

    def test_chart_missing_x_column(self, loaded_analyzer):
        with pytest.raises(ValueError, match="X 轴列不存在"):
            loaded_analyzer.to_chart("t1", chart_type="bar", x_column="no_col")

    def test_chart_missing_y_column(self, loaded_analyzer):
        with pytest.raises(ValueError, match="Y 轴列不存在"):
            loaded_analyzer.to_chart("t1", chart_type="bar", x_column="region", y_columns=["no_col"])

    def test_chart_no_numeric_columns(self, analyzer, tmp_path):
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["no_num"] = pd.DataFrame({
            "name": ["A", "B"],
            "label": ["x", "y"],
        })
        with pytest.raises(ValueError, match="未指定 y_columns"):
            analyzer.to_chart("no_num", chart_type="bar", x_column="name")

    def test_chart_unsupported_type(self, analyzer, tmp_path):
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["chart_test"] = pd.DataFrame({
            "x": [1, 2], "y": [3, 4],
        })
        with pytest.raises(ValueError, match="不支持的图表类型"):
            analyzer.to_chart("chart_test", chart_type="radar", x_column="x", y_columns=["y"])

    def test_chart_source_not_found(self, loaded_analyzer):
        with pytest.raises(ValueError, match="数据源不存在"):
            loaded_analyzer.to_chart("no_table", chart_type="bar", x_column="a")

    def test_to_chart_group_by_auto_pivot(self, analyzer):
        """group_by 自动透视长表为宽表，实现多系列分组柱状图"""
        df = pd.DataFrame({
            "品类": ["T恤", "T恤", "T恤", "外套", "外套", "外套"],
            "渠道": ["淘宝", "京东", "拼多多", "淘宝", "京东", "拼多多"],
            "毛利": [10000, 8000, 5000, 20000, 15000, 9000],
        })
        analyzer._store("long_data", df)
        result = analyzer.to_chart(
            "long_data", chart_type="grouped_bar",
            x_column="品类", y_columns=["毛利"], group_by="渠道",
            title="各品类分渠道毛利对比",
        )
        assert result["chart_type"] == "grouped_bar"
        assert os.path.exists(result["file_path"])
        # 透视后 data_columns 应包含三个渠道列
        cols = result["data_columns"]
        assert "淘宝" in cols
        assert "京东" in cols
        assert "拼多多" in cols

    def test_to_chart_group_by_infer_value(self, analyzer):
        """group_by 不传 y_columns 时，自动推断唯一数值列"""
        df = pd.DataFrame({
            "品类": ["T恤", "T恤", "外套", "外套"],
            "渠道": ["淘宝", "京东", "淘宝", "京东"],
            "毛利": [10000, 8000, 20000, 15000],
        })
        analyzer._store("long2", df)
        result = analyzer.to_chart(
            "long2", chart_type="bar",
            x_column="品类", y_columns=None, group_by="渠道",
            title="自动推断值列",
        )
        assert result["chart_type"] == "bar"
        assert "淘宝" in result["data_columns"]
        assert "京东" in result["data_columns"]

    def test_to_chart_group_by_ambiguous_numeric_raises(self, analyzer):
        """group_by 存在、无 y_columns、且有多个数值列 → 抛 ValueError"""
        df = pd.DataFrame({
            "品类": ["T恤", "T恤", "外套", "外套"],
            "渠道": ["淘宝", "京东", "淘宝", "京东"],
            "毛利": [10000, 8000, 20000, 15000],
            "成本": [6000, 5000, 12000, 9000],
        })
        analyzer._store("ambig", df)
        with pytest.raises(ValueError, match="数值列"):
            analyzer.to_chart(
                "ambig", chart_type="bar",
                x_column="品类", y_columns=None, group_by="渠道",
                title="歧义",
            )

    def test_to_chart_group_by_not_in_columns_ignored(self, analyzer):
        """group_by 列不存在 → 忽略，走原逻辑不报错"""
        df = pd.DataFrame({
            "品类": ["T恤", "外套"],
            "毛利": [10000, 20000],
        })
        analyzer._store("simple", df)
        result = analyzer.to_chart(
            "simple", chart_type="bar",
            x_column="品类", y_columns=["毛利"], group_by="不存在的列",
            title="group_by忽略",
        )
        assert result["chart_type"] == "bar"
        assert os.path.exists(result["file_path"])

    def test_to_chart_group_by_conflicts_raise(self, analyzer):
        """group_by 与 x_column 或值列同名 → 抛明确错误，而非 pandas 内部异常"""
        df = pd.DataFrame({
            "品类": ["T恤", "T恤", "外套", "外套"],
            "渠道": ["淘宝", "京东", "淘宝", "京东"],
            "毛利": [100, 200, 300, 400],
        })
        analyzer._store("conflict", df)
        # group_by == x_column
        with pytest.raises(ValueError, match="x_column 相同"):
            analyzer.to_chart(
                "conflict", chart_type="bar",
                x_column="品类", y_columns=["毛利"], group_by="品类",
                title="x冲突",
            )
        # group_by == value_col
        with pytest.raises(ValueError, match="值列相同"):
            analyzer.to_chart(
                "conflict", chart_type="bar",
                x_column="品类", y_columns=["毛利"], group_by="毛利",
                title="值列冲突",
            )

    # ---------- 图表主题 ----------

    def test_chart_default_theme_is_ft(self, analyzer, tmp_path):
        """不传 theme 时默认财经风(ft)"""
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["td"] = pd.DataFrame({"cat": ["A", "B"], "v": [10, 20]})
        result = analyzer.to_chart("td", chart_type="bar", x_column="cat", y_columns=["v"])
        assert result["theme"] == "ft"
        assert result["theme_name"] == "财经风"
        assert os.path.exists(result["file_path"])

    def test_chart_theme_corporate(self, analyzer, tmp_path):
        """theme=corporate 商务深蓝"""
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["td"] = pd.DataFrame({"cat": ["A", "B"], "v": [10, 20]})
        result = analyzer.to_chart("td", chart_type="bar", x_column="cat", y_columns=["v"], theme="corporate")
        assert result["theme"] == "corporate"
        assert result["theme_name"] == "商务深蓝"
        assert os.path.exists(result["file_path"])

    def test_chart_theme_morandi(self, analyzer, tmp_path):
        """theme=morandi 莫兰迪（圆角柱）"""
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["td"] = pd.DataFrame({"cat": ["A", "B"], "v": [10, 20]})
        result = analyzer.to_chart("td", chart_type="bar", x_column="cat", y_columns=["v"], theme="morandi")
        assert result["theme_name"] == "莫兰迪"
        assert os.path.exists(result["file_path"])

    def test_chart_theme_dark(self, analyzer, tmp_path):
        """theme=dark 深色科技（圆角柱，深色底）"""
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["td"] = pd.DataFrame({"cat": ["A", "B"], "v": [10, 20]})
        result = analyzer.to_chart("td", chart_type="bar", x_column="cat", y_columns=["v"], theme="dark")
        assert result["theme_name"] == "深色科技"
        assert os.path.exists(result["file_path"])

    def test_chart_theme_dark_pie(self, analyzer, tmp_path):
        """深色主题下饼图正常（验证 wedge 边缝与百分比配色）"""
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["td"] = pd.DataFrame({"cat": ["A", "B", "C"], "v": [30, 50, 20]})
        result = analyzer.to_chart("td", chart_type="pie", x_column="cat", y_columns=["v"], theme="dark")
        assert result["theme_name"] == "深色科技"
        assert os.path.exists(result["file_path"])

    def test_chart_unknown_theme_falls_back(self, analyzer, tmp_path):
        """未知 theme 键回退默认 ft，不报错"""
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["td"] = pd.DataFrame({"cat": ["A", "B"], "v": [10, 20]})
        result = analyzer.to_chart("td", chart_type="bar", x_column="cat", y_columns=["v"], theme="nonexistent")
        assert result["theme_name"] == "财经风"
        assert os.path.exists(result["file_path"])

    def test_chart_negative_values_morandi(self, analyzer, tmp_path):
        """含负值数据在圆角主题(morandi)下回退直角柱，负值完整可见，不报错"""
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["td"] = pd.DataFrame({"cat": ["A", "B", "C"], "v": [10, -5, 20]})
        result = analyzer.to_chart("td", chart_type="bar", x_column="cat", y_columns=["v"], theme="morandi")
        assert result["theme_name"] == "莫兰迪"
        assert os.path.exists(result["file_path"])

    def test_chart_line_theme_returns_theme_name(self, analyzer, tmp_path):
        """非 bar 图（line）切换主题后返回值带 theme/theme_name"""
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["td"] = pd.DataFrame({"m": ["1月", "2月", "3月"], "v": [10, 20, 30]})
        result = analyzer.to_chart("td", chart_type="line", x_column="m", y_columns=["v"], theme="corporate")
        assert result["theme"] == "corporate"
        assert result["theme_name"] == "商务深蓝"
        assert os.path.exists(result["file_path"])

    def test_chart_pie_more_categories_than_palette(self, analyzer, tmp_path):
        """饼图类目数 > 色板长度(3)时派生渐变色，生成成功"""
        analyzer.CHART_OUTPUT_DIR = str(tmp_path / "charts")
        analyzer._variables["td"] = pd.DataFrame({
            "cat": ["A", "B", "C", "D", "E"], "v": [10, 20, 15, 25, 30],
        })
        result = analyzer.to_chart("td", chart_type="pie", x_column="cat", y_columns=["v"], theme="ft")
        assert result["theme_name"] == "财经风"
        assert os.path.exists(result["file_path"])

    # ---------- 图例防重叠 ----------

    def test_legend_outside_plot_area(self):
        """图例锚定在绘图区外上方：无论系列数多少，包围盒都与坐标区零重叠"""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch

        from src.tools.data_analysis.data_analyzer import CHART_THEMES, DEFAULT_CHART_THEME

        t = CHART_THEMES[DEFAULT_CHART_THEME]
        for n_series in (1, 4, 10):
            fig, ax = plt.subplots(figsize=(12, 6))
            try:
                handles = [Patch(facecolor="C0", label=f"系列{i}") for i in range(n_series)]
                leg = DataAnalyzer._style_legend(ax, t, handles=handles)
                fig.canvas.draw()
                leg_bb = leg.get_window_extent()
                ax_bb = ax.get_window_extent()
                assert not leg_bb.overlaps(ax_bb), f"{n_series} 系列：图例与绘图区重叠"
                # 图例整体位于绘图区上方，而非左侧/右侧/内部
                assert leg_bb.y0 >= ax_bb.y1, f"{n_series} 系列：图例底边低于绘图区顶边"
            finally:
                plt.close(fig)

    def test_legend_multi_row_shifts_title(self):
        """多系列多行图例向上生长顶到主标题时，标题被上移让位，两者不重叠"""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch

        from src.tools.data_analysis.data_analyzer import CHART_THEMES, DEFAULT_CHART_THEME

        t = CHART_THEMES[DEFAULT_CHART_THEME]
        fig, ax = plt.subplots(figsize=(12, 6))
        try:
            handles = [Patch(facecolor="C0", label=f"超长系列名称第{i}项") for i in range(10)]
            leg = DataAnalyzer._style_legend(ax, t, handles=handles)
            title_artist = ax.text(0, 1.13, "这是一个非常长的主标题用于测试避让",
                                   transform=ax.transAxes, fontsize=15, fontweight="bold")
            DataAnalyzer._avoid_title_legend_overlap(fig, ax, leg, title_artist)
            fig.canvas.draw()
            assert not leg.get_window_extent().overlaps(title_artist.get_window_extent())
        finally:
            plt.close(fig)


# ==================== _eval_expression 安全性 ====================


class TestEvalExpressionSecurity:

    @pytest.fixture
    def security_analyzer(self):
        da = DataAnalyzer()
        da._variables["sec"] = pd.DataFrame({"val": [1, 2, 3]})
        return da

    def test_import_blocked(self, security_analyzer):
        with pytest.raises(NameError):
            security_analyzer._eval_expression(
                security_analyzer._variables["sec"], '__import__("os").system("echo pwned")'
            )

    def test_open_blocked(self, security_analyzer):
        with pytest.raises(NameError):
            security_analyzer._eval_expression(
                security_analyzer._variables["sec"], 'open("/etc/passwd").read()'
            )

    def test_exec_blocked(self, security_analyzer):
        with pytest.raises(NameError):
            security_analyzer._eval_expression(
                security_analyzer._variables["sec"], 'exec("print(1)")'
            )

    def test_eval_blocked(self, security_analyzer):
        with pytest.raises(NameError):
            security_analyzer._eval_expression(
                security_analyzer._variables["sec"], 'eval("1+1")'
            )

    def test_builtins_access_blocked(self, security_analyzer):
        with pytest.raises((KeyError, TypeError, NameError)):
            security_analyzer._eval_expression(
                security_analyzer._variables["sec"], '__builtins__["open"]'
            )

    def test_safe_arithmetic(self, security_analyzer):
        result = security_analyzer._eval_expression(
            security_analyzer._variables["sec"], "val * 2"
        )
        assert list(result) == [2, 4, 6]

    def test_safe_round(self, security_analyzer):
        result = security_analyzer._eval_expression(
            security_analyzer._variables["sec"], "round(val / 3, 2)"
        )
        assert list(result) == [0.33, 0.67, 1.0]

    def test_safe_sqrt(self, security_analyzer):
        result = security_analyzer._eval_expression(
            security_analyzer._variables["sec"], "sqrt(val)"
        )
        assert abs(result.iloc[0] - 1.0) < 0.01

    def test_safe_log(self, security_analyzer):
        result = security_analyzer._eval_expression(
            security_analyzer._variables["sec"], "log(val)"
        )
        assert len(result) == 3


# ==================== _transform_if_expr ====================


class TestTransformIfExpr:

    def test_simple_if(self):
        da = DataAnalyzer()
        assert da._transform_if_expr("if(a, b, c)") == "np.where(a, b, c)"

    def test_nested_if(self):
        da = DataAnalyzer()
        result = da._transform_if_expr("if(a, if(b, c, d), e)")
        assert result == "np.where(a, np.where(b, c, d), e)"

    def test_no_if(self):
        da = DataAnalyzer()
        assert da._transform_if_expr("a + b") == "a + b"

    def test_endif_not_replaced(self):
        da = DataAnalyzer()
        result = da._transform_if_expr("endif(x)")
        assert result == "endif(x)"


# ==================== _to_native ====================


class TestToNative:

    def test_none(self):
        assert DataAnalyzer._to_native(None) is None

    def test_numpy_int(self):
        assert DataAnalyzer._to_native(np.int64(42)) == 42
        assert isinstance(DataAnalyzer._to_native(np.int64(42)), int)

    def test_numpy_float(self):
        assert DataAnalyzer._to_native(np.float64(3.14)) == pytest.approx(3.14)
        assert isinstance(DataAnalyzer._to_native(np.float64(3.14)), float)

    def test_numpy_nan(self):
        assert DataAnalyzer._to_native(np.float64("nan")) is None

    def test_numpy_bool(self):
        assert DataAnalyzer._to_native(np.bool_(True)) is True
        assert isinstance(DataAnalyzer._to_native(np.bool_(True)), bool)

    def test_numpy_datetime(self):
        result = DataAnalyzer._to_native(np.datetime64("2024-01-15"))
        assert isinstance(result, str)

    def test_pd_na(self):
        assert DataAnalyzer._to_native(pd.NA) is None

    def test_regular_string(self):
        assert DataAnalyzer._to_native("hello") == "hello"
