"""
time_saver 模块单测

覆盖：
- 工具耗时系数映射
- 单条对话节省时间计算
- 多条对话累加
- AI 慢于手工时不扣分（saved > 0 才累加）
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from src.reports.time_saver import (
    DEFAULT_COEFFICIENTS,
    estimate_saved_minutes,
)


class TestEstimateSavedMinutes:
    """estimate_saved_minutes 测试"""

    def test_empty_records(self):
        """空列表返回 0"""
        assert estimate_saved_minutes([]) == 0.0

    def test_single_record_no_tools(self):
        """无工具调用，按 default 系数 3 分钟估算"""
        records = [{"duration_ms": 5000}]  # AI 耗时 5 秒 ≈ 0.083 分钟
        saved = estimate_saved_minutes(records)
        # 默认系数 3 - 0.083 = 2.917，保留 1 位小数 = 2.9
        assert saved == pytest.approx(2.9, abs=0.1)

    def test_single_record_with_email_tool(self):
        """send_email 工具按 8 分钟系数"""
        records = [{
            "duration_ms": 10000,  # AI 耗时 10 秒 ≈ 0.167 分钟
            "execution_details": {
                "tool_executions": [{"tool_name": "send_email"}]
            }
        }]
        saved = estimate_saved_minutes(records)
        # 8 - 0.167 = 7.833，保留 1 位小数 = 7.8
        assert saved == pytest.approx(7.8, abs=0.1)

    def test_multiple_records_accumulate(self):
        """多条对话累加"""
        records = [
            {"duration_ms": 5000},  # 节省 ~2.9
            {"duration_ms": 5000},  # 节省 ~2.9
        ]
        saved = estimate_saved_minutes(records)
        # 总和 ~5.8
        assert saved == pytest.approx(5.8, abs=0.2)

    def test_ai_slower_than_manual_no_negative(self):
        """AI 慢于手工时不扣分"""
        # duration_ms 1000000 = 1000 秒 ≈ 16.67 分钟，大于 default 3 分钟
        records = [{"duration_ms": 1000000}]
        saved = estimate_saved_minutes(records)
        assert saved == 0.0

    def test_custom_coefficients_override(self):
        """自定义系数覆盖默认"""
        records = [{
            "duration_ms": 5000,
            "execution_details": {
                "tool_executions": [{"tool_name": "send_email"}]
            }
        }]
        # 自定义 send_email 系数 = 1
        saved = estimate_saved_minutes(records, coefficients={"send_email": 1, "default": 3})
        # 1 - 0.083 = 0.917，保留 1 位小数 = 0.9
        assert saved == pytest.approx(0.9, abs=0.1)

    def test_execution_details_as_string(self):
        """execution_details 为 JSON 字符串时也能解析"""
        import json
        records = [{
            "duration_ms": 5000,
            "execution_details": json.dumps({
                "tool_executions": [{"tool_name": "search_documents"}]
            })
        }]
        saved = estimate_saved_minutes(records)
        # search_documents 系数 5 - 0.083 = 4.917 ≈ 4.9
        assert saved == pytest.approx(4.9, abs=0.1)

    def test_multiple_tools_takes_max_coefficient(self):
        """一轮对话调多个工具时，取最大系数"""
        records = [{
            "duration_ms": 5000,
            "execution_details": {
                "tool_executions": [
                    {"tool_name": "send_email"},      # 8 分钟
                    {"tool_name": "search_documents"}, # 5 分钟
                ]
            }
        }]
        saved = estimate_saved_minutes(records)
        # 取最大 8 - 0.083 = 7.917 ≈ 7.9
        assert saved == pytest.approx(7.9, abs=0.1)

    def test_default_coefficients_has_all_keys(self):
        """默认系数表包含必要的 key"""
        assert "send_email" in DEFAULT_COEFFICIENTS
        assert "search_documents" in DEFAULT_COEFFICIENTS
        assert "default" in DEFAULT_COEFFICIENTS
        assert DEFAULT_COEFFICIENTS["default"] == 3
