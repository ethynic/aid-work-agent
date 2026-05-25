"""
followup_manager.py 单元测试

测试跟进记录管理脚本的核心命令，使用 mock 数据库连接。
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from argparse import Namespace

import sys
from pathlib import Path
script_dir = Path(__file__).resolve().parents[2] / "src" / "skills" / "followup-tracking-1.0.0" / "scripts"
sys.path.insert(0, str(script_dir))

import followup_manager


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch):
    monkeypatch.setenv("CURRENT_TENANT_ID", "test_tenant_001")


@pytest.fixture
def mock_output_json():
    with patch.object(followup_manager, "output_json") as m:
        yield m


@pytest.fixture
def mock_db():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    with patch.object(followup_manager, "get_db", return_value=mock_conn):
        yield mock_conn, mock_cursor


def make_args(**kwargs):
    return Namespace(**kwargs)


class TestAddRecord:
    def test_add_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        args = make_args(
            lead_id="lead_001", user_id="user_001", type="phone",
            content="电话沟通，客户有意向", outcome="positive",
            next_action=None, next_followup_at=None, duration_minutes=15,
        )
        followup_manager.cmd_add_record(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True
        data = mock_output_json.call_args[1].get("data") or mock_output_json.call_args[0][1]
        assert data["record_id"].startswith("fcr_")

    def test_add_invalid_type(self, mock_db, mock_output_json):
        args = make_args(
            lead_id="lead_001", user_id="user_001", type="invalid_type",
            content="test", outcome=None, next_action=None,
            next_followup_at=None, duration_minutes=None,
        )
        followup_manager.cmd_add_record(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is False
        assert "无效跟进类型" in mock_output_json.call_args[1].get("error", "")


class TestListRecords:
    def test_list_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        mock_cursor.description = [
            ("record_id",), ("lead_id",), ("user_id",), ("followup_type",),
            ("content",), ("followup_at",), ("outcome",), ("quality_score",),
            ("company_name",), ("contact_name",),
        ]
        mock_cursor.fetchall.return_value = [
            ("fcr_001", "lead_001", "user_001", "phone", "通话", "2026-05-25", "positive", 8, "ABC公司", "张三"),
        ]

        args = make_args(user_id="user_001", lead_id=None, date_from=None, date_to=None, limit=50)
        followup_manager.cmd_list_records(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True


class TestGetRecord:
    def test_get_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        mock_cursor.description = [
            ("record_id",), ("lead_id",), ("content",), ("company_name",),
        ]
        mock_cursor.fetchone.return_value = ("fcr_001", "lead_001", "通话内容", "ABC公司")

        args = make_args(record_id="fcr_001")
        followup_manager.cmd_get_record(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True

    def test_get_not_found(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        mock_cursor.description = [("record_id",)]
        mock_cursor.fetchone.return_value = None

        args = make_args(record_id="fcr_nonexist")
        followup_manager.cmd_get_record(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is False


class TestGetReminders:
    def test_get_reminders_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        mock_cursor.description = [
            ("lead_id",), ("company_name",), ("contact_name",), ("phone",),
            ("stage",), ("next_followup_at",), ("assigned_to",), ("followup_count",),
        ]
        mock_cursor.fetchall.return_value = [
            ("lead_001", "ABC公司", "张三", "13800138000", "new", "2026-05-20", "user_001", 2),
        ]

        args = make_args(user_id="user_001", due_before=None)
        followup_manager.cmd_get_reminders(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True


class TestRecordAICall:
    def test_record_ai_call_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        args = make_args(
            lead_id="lead_001", user_id="user_001", call_id="call_001",
            transcript="客户表示有兴趣", sentiment="positive",
            summary="客户对产品感兴趣", duration=120,
        )
        followup_manager.cmd_record_ai_call(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True
        data = mock_output_json.call_args[1].get("data") or mock_output_json.call_args[0][1]
        assert data["call_id"] == "call_001"
        assert data["sentiment"] == "positive"

    def test_record_ai_call_invalid_sentiment(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        args = make_args(
            lead_id="lead_001", user_id="user_001", call_id="call_001",
            transcript="test", sentiment="bad_sentiment",
            summary="test", duration=None,
        )
        followup_manager.cmd_record_ai_call(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True
        data = mock_output_json.call_args[1].get("data") or mock_output_json.call_args[0][1]
        assert data["sentiment"] == "neutral"  # fallback


class TestReminderStats:
    def test_stats_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        mock_cursor.fetchone.return_value = (100, 5, 10, 20, 3.5)

        args = make_args(user_id="user_001")
        followup_manager.cmd_reminder_stats(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True
        data = mock_output_json.call_args[1].get("data") or mock_output_json.call_args[0][1]
        assert data["total_leads"] == 100
        assert data["overdue"] == 5


class TestBatchAICallResults:
    def test_batch_success(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        args = make_args(
            results='[{"lead_id": "lead_001", "user_id": "user_001", "call_id": "c1", "transcript": "t1", "sentiment": "positive", "summary": "s1"}]'
        )
        followup_manager.cmd_batch_ai_call_results(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True
        data = mock_output_json.call_args[1].get("data") or mock_output_json.call_args[0][1]
        assert data["recorded"] == 1

    def test_batch_missing_lead_id(self, mock_db, mock_output_json):
        mock_conn, mock_cursor = mock_db
        args = make_args(results='[{"user_id": "user_001"}]')
        followup_manager.cmd_batch_ai_call_results(args)

        mock_output_json.assert_called_once()
        assert mock_output_json.call_args[0][0] is True
        data = mock_output_json.call_args[1].get("data") or mock_output_json.call_args[0][1]
        assert data["recorded"] == 0
        assert data["errors"] != []
