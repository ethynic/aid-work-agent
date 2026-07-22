"""
aggregator 模块单测

覆盖：
- parse_date_range：daily/weekly/monthly 时间范围计算
- aggregate_personal：mock 数据库返回，验证聚合逻辑
- aggregate_team：mock 数据库返回，验证团队聚合
"""

import json
from datetime import date, datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from src.reports.aggregator import (
    parse_date_range,
    aggregate_personal,
    aggregate_team,
)


class TestParseDateRange:
    """parse_date_range 测试"""

    def test_daily(self):
        """daily：当日 00:00 ~ 次日 00:00"""
        d = date(2026, 7, 22)
        start, end = parse_date_range(d, "daily")
        assert start == datetime(2026, 7, 22, 0, 0, 0)
        assert end == datetime(2026, 7, 23, 0, 0, 0)

    def test_weekly(self):
        """weekly：report_date 为周一，跨度 7 天"""
        d = date(2026, 7, 20)  # 周一
        start, end = parse_date_range(d, "weekly")
        assert start == datetime(2026, 7, 20, 0, 0, 0)
        assert end == datetime(2026, 7, 27, 0, 0, 0)

    def test_monthly_january(self):
        """monthly：1 月跨到 2 月"""
        d = date(2026, 1, 1)
        start, end = parse_date_range(d, "monthly")
        assert start == datetime(2026, 1, 1, 0, 0, 0)
        assert end == datetime(2026, 2, 1, 0, 0, 0)

    def test_monthly_december(self):
        """monthly：12 月跨年到次年 1 月"""
        d = date(2026, 12, 1)
        start, end = parse_date_range(d, "monthly")
        assert start == datetime(2026, 12, 1, 0, 0, 0)
        assert end == datetime(2027, 1, 1, 0, 0, 0)

    def test_invalid_type(self):
        """非法 report_type 抛 ValueError"""
        with pytest.raises(ValueError, match="不支持的 report_type"):
            parse_date_range(date(2026, 7, 22), "quarterly")


class TestAggregatePersonal:
    """aggregate_personal 测试"""

    def test_empty_records(self):
        """无对话记录时返回空聚合"""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.reports.aggregator.get_db_connection") as mock_get:
            mock_get.return_value.__enter__.return_value = mock_conn
            result = aggregate_personal("t1", "u1", date(2026, 7, 22), "daily")

        assert result["dialog_count"] == 0
        assert result["credit_cost"] == 0
        assert result["subagent_distribution"] == {}
        assert result["tool_distribution"] == {}
        assert result["source_distribution"] == {}
        assert result["saved_minutes"] == 0.0
        assert result["records"] == []

    def test_with_records(self):
        """有对话记录时正确聚合"""
        # 构造 mock 数据：3 条记录，含 subagent_calls / execution_details / source_type
        records = [
            {
                "record_id": "rec_1",
                "user_message": "帮我查客户 A",
                "assistant_message": "客户 A 信息...",
                "credit_cost": 10,
                "source_type": "chat",
                "duration_ms": 5000,
                "subagent_calls": json.dumps([{"subagent_id": "trade-specialist"}]),
                "execution_details": json.dumps({
                    "tool_executions": [{"tool_name": "query_customer"}, {"tool_name": "search_documents"}]
                }),
                "created_at": datetime(2026, 7, 22, 10, 0, 0),
            },
            {
                "record_id": "rec_2",
                "user_message": "发邮件给客户 B",
                "assistant_message": "邮件已发送...",
                "credit_cost": 5,
                "source_type": "wecom",
                "duration_ms": 8000,
                "subagent_calls": json.dumps([{"subagent_id": "trade-specialist"}]),
                "execution_details": json.dumps({
                    "tool_executions": [{"tool_name": "send_email"}]
                }),
                "created_at": datetime(2026, 7, 22, 14, 0, 0),
            },
        ]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = records
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.reports.aggregator.get_db_connection") as mock_get:
            mock_get.return_value.__enter__.return_value = mock_conn
            result = aggregate_personal("t1", "u1", date(2026, 7, 22), "daily")

        assert result["dialog_count"] == 2
        assert result["credit_cost"] == 15
        assert result["subagent_distribution"] == {"trade-specialist": 2}
        assert result["tool_distribution"] == {
            "query_customer": 1,
            "search_documents": 1,
            "send_email": 1,
        }
        assert result["source_distribution"] == {"chat": 1, "wecom": 1}
        # saved_minutes 应 > 0（每条都节省了几分钟）
        assert result["saved_minutes"] > 0


class TestAggregateTeam:
    """aggregate_team 测试"""

    def test_empty(self):
        """无对话记录时返回空聚合"""
        mock_cursor = MagicMock()
        # 依次返回 user_rows, total_row, source_rows
        mock_cursor.fetchall.side_effect = [[], [], []]
        mock_cursor.fetchone.return_value = {}
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.reports.aggregator.get_db_connection") as mock_get:
            mock_get.return_value.__enter__.return_value = mock_conn
            result = aggregate_team("t1", date(2026, 7, 22), "daily")

        assert result["active_user_count"] == 0
        assert result["total_dialog_count"] == 0
        assert result["total_credit_cost"] == 0
        assert result["user_stats"] == []
        assert result["source_distribution"] == {}

    def test_with_users(self):
        """有用户对话时正确聚合"""
        user_rows = [
            {"user_id": "u1", "dialog_count": 10, "credit_cost": 50, "total_duration_ms": 50000},
            {"user_id": "u2", "dialog_count": 5, "credit_cost": 25, "total_duration_ms": 25000},
        ]
        total_row = {"dialog_count": 15, "credit_cost": 75}
        source_rows = [
            {"source_type": "chat", "cnt": 10},
            {"source_type": "wecom", "cnt": 5},
        ]
        mock_cursor = MagicMock()
        mock_cursor.fetchall.side_effect = [user_rows, source_rows]
        mock_cursor.fetchone.return_value = total_row
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("src.reports.aggregator.get_db_connection") as mock_get:
            mock_get.return_value.__enter__.return_value = mock_conn
            result = aggregate_team("t1", date(2026, 7, 22), "daily")

        assert result["active_user_count"] == 2
        assert result["total_dialog_count"] == 15
        assert result["total_credit_cost"] == 75
        assert len(result["user_stats"]) == 2
        assert result["user_stats"][0]["user_id"] == "u1"
        assert result["user_stats"][0]["dialog_count"] == 10
        assert result["source_distribution"] == {"chat": 10, "wecom": 5}
        # 每用户节省时间 = dialog_count * 3
        assert result["user_stats"][0]["saved_minutes"] == 30.0
        assert result["total_saved_minutes"] == 45.0
