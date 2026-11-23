"""value_cleaner 值级确定性清洗单测（设计 docs/subagent/data-analysis/excel-upload-validation-design.md §7.2）。"""

import pandas as pd
import pytest

from src.services.data_analysis.value_cleaner import (
    clean_cell,
    clean_dataframe,
    column_warnings_from_quality,
    quality_summary_for_llm,
)


class TestCleanCell:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2021 -1-20", "date"),  # 日期杂空格
            ("2023年 10月", "date_month"),  # 中文年月 -> 月粒度
            ("2023-10-01", "date"),
            ("2023/10/1", "date"),
            ("2023年10月1日", "date"),
        ],
    )
    def test_date_forms(self, raw, expected):
        cleaned, action = clean_cell(raw)
        assert action == expected
        assert cleaned is not None

    def test_date_month_value(self):
        cleaned, action = clean_cell("2023年 10月")
        assert action == "date_month"
        assert (cleaned.year, cleaned.month, cleaned.day) == (2023, 10, 1)

    def test_invalid_date_becomes_null(self):
        cleaned, action = clean_cell("2023-2-30")  # 形态像日期但日历非法
        assert cleaned is None
        assert action == "invalid_date"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("10万", 100_000),
            ("3.5亿", 350_000_000),
            ("1,234.50", 1234.5),
            ("¥88", 88.0),
            ("50%", 0.5),
            ("１２３", 123.0),  # 全角数字
            ("120元", 120.0),
            ("-5,000", -5000.0),
        ],
    )
    def test_number_forms(self, raw, expected):
        cleaned, action = clean_cell(raw)
        assert action == "number"
        assert cleaned == pytest.approx(expected)

    @pytest.mark.parametrize("raw", ["-", "—", "/", "无", "N/A", "", "  "])
    def test_missing_markers(self, raw):
        cleaned, action = clean_cell(raw)
        assert cleaned is None
        assert action == "missing"

    def test_embedded_text_untouched(self):
        """嵌入文本不转换（整格匹配红线）。"""
        for raw in ["预算10万", "约30", "abc", "2021年（下半年）"]:
            cleaned, action = clean_cell(raw)
            assert action == ""
            assert cleaned == raw

    def test_none_and_numeric_passthrough(self):
        assert clean_cell(None) == (None, "")
        assert clean_cell(3.14) == (3.14, "")


class TestCleanDataFrame:
    def test_numeric_column_with_units(self):
        df = pd.DataFrame({"单价": ["10万", "20万", "3.5", "-", "5"]})
        cleaned, quality = clean_dataframe(df)
        assert quality["单价"]["dtype"] in ("decimal", "integer")
        assert quality["单价"]["converted"] == 4  # '10万'/'20万'/'3.5'/'5'（'-' 置空）
        assert quality["单价"]["to_null"] == 1
        values = [None if pd.isna(v) else v for v in cleaned["单价"]]
        assert values == [100_000.0, 200_000.0, 3.5, None, 5.0]

    def test_mixed_text_column_not_converted(self):
        """多数为文本的列不整列转数值，但整格可转换的值仍被转换单元格级处理。"""
        df = pd.DataFrame({"备注": ["预算10万", "正常", "加急", "2023-2-30", "其他"]})
        cleaned, quality = clean_dataframe(df)
        assert quality["备注"]["dtype"] == "text"
        assert quality["备注"]["to_null"] == 1  # 非法日期置空
        assert quality["备注"]["examples_failed"] == ["2023-2-30"]
        # 嵌入文本保持原样
        assert cleaned["备注"].iloc[0] == "预算10万"

    def test_date_column_granularity_reported(self):
        df = pd.DataFrame({"月份": ["2023年 1月", "2023年 10月", "2024-02-01"]})
        cleaned, quality = clean_dataframe(df)
        assert quality["月份"]["dtype"] == "date"
        assert quality["月份"]["granularity"] == "month"
        assert cleaned["月份"].notna().all()

    def test_missing_marker_column(self):
        df = pd.DataFrame({"值": [1.0, "-", 2.0, 3.0]})
        cleaned, quality = clean_dataframe(df)
        assert quality["值"]["to_null"] == 1
        assert cleaned["值"].isna().sum() == 1

    def test_percent_note(self):
        df = pd.DataFrame({"占比": ["50%", "60%", "70%", "80%"]})
        cleaned, quality = clean_dataframe(df)
        assert quality["占比"]["note"] == "百分比已换算为小数"
        assert cleaned["占比"].tolist() == [0.5, 0.6, 0.7, 0.8]

    def test_original_df_not_mutated(self):
        df = pd.DataFrame({"v": ["10万", "20万"]})
        original = df.copy()
        clean_dataframe(df)
        assert df["v"].tolist() == original["v"].tolist()


class TestReports:
    def test_column_warnings_text(self):
        quality = {"单价": {"dtype": "decimal", "converted": 3, "to_null": 1}}
        warnings = column_warnings_from_quality(quality)
        assert len(warnings) == 1
        assert "单价" in warnings[0] and "3 个值" in warnings[0]

    def test_warnings_skip_clean_columns(self):
        assert column_warnings_from_quality({"a": {"dtype": "text"}}) == []

    def test_llm_summary(self):
        quality = {
            "月份": {"dtype": "date", "converted": 2, "granularity": "month"},
            "备注": {"dtype": "text", "to_null": 1, "examples_failed": ["2023-2-30"]},
        }
        summary = quality_summary_for_llm(quality)
        assert "月份" in summary and "月粒度" in summary
        assert "2023-2-30" in summary

    def test_llm_summary_empty(self):
        assert quality_summary_for_llm({}) == ""
