"""
WorkOutcomeDB 单元测试

覆盖场景：
- generate_outcome_id：格式校验
- create：参数校验 + DB 写入 + commit + 返回值
- exists_by_session_and_type：命中 / 未命中
- list_by_tenant：过滤条件 / 分页 / 排序
- get_by_outcome_id：命中 / 未命中
- delete_by_outcome_id：成功 / 不存在
- get_stats：分组聚合
- _row_to_outcome：JSONB 解析 + created_at ISO 转换
"""

import json
from datetime import date, datetime
from unittest.mock import patch, MagicMock

import pytest

from src.reports.work_outcome_db import (
    WorkOutcomeDB,
    generate_outcome_id,
    _row_to_outcome,
)


pytestmark = [pytest.mark.tools]


# ============== generate_outcome_id ==============

class TestGenerateOutcomeId:
    """outcome_id 生成器测试"""

    def test_format_prefix(self):
        oid = generate_outcome_id()
        assert oid.startswith("wo_")

    def test_format_length(self):
        oid = generate_outcome_id()
        # wo_ + 8 hex chars
        assert len(oid) == 3 + 8

    def test_uniqueness(self):
        ids = {generate_outcome_id() for _ in range(100)}
        assert len(ids) == 100


# ============== _row_to_outcome ==============

class TestRowToOutcome:
    """_row_to_outcome 字段解析测试"""

    def test_none_row(self):
        assert _row_to_outcome(None) is None

    def test_metadata_dict_passthrough(self):
        """metadata 已是 dict 时原样返回"""
        row = {
            "outcome_id": "wo_abc12345",
            "tenant_id": "t1",
            "user_id": "u1",
            "subagent_id": None,
            "session_id": "s1",
            "channel": "web",
            "summary": "test summary",
            "outcome_type": "file",
            "importance": "normal",
            "file_id": "file_xxx",
            "file_name": "report.md",
            "file_path": "/tmp/report.md",
            "metadata": {"k": "v"},
            "source": "cp_realtime",
            "chat_record_id": None,
            "review_batch_id": None,
            "review_confidence": None,
            "created_at": datetime(2026, 7, 29, 10, 0, 0),
        }
        result = _row_to_outcome(row)
        assert result is not None
        assert result["outcome_id"] == "wo_abc12345"
        assert result["metadata"] == {"k": "v"}
        assert result["created_at"] == "2026-07-29T10:00:00"

    def test_metadata_json_string_parsed(self):
        """metadata 是 JSON 字符串时解析为 dict"""
        row = {
            "outcome_id": "wo_1",
            "metadata": '{"customer": "张三", "amount": 200}',
            "created_at": datetime(2026, 7, 29),
        }
        result = _row_to_outcome(row)
        assert result["metadata"] == {"customer": "张三", "amount": 200}

    def test_metadata_none_kept_as_is(self):
        """metadata 为 None 时保持 None（_row_to_outcome 仅处理 str）"""
        row = {
            "outcome_id": "wo_2",
            "metadata": None,
            "created_at": datetime(2026, 7, 29),
        }
        result = _row_to_outcome(row)
        assert result["metadata"] is None

    def test_metadata_invalid_json_kept_as_is(self):
        """metadata JSON 解析失败时保留原字符串"""
        row = {
            "outcome_id": "wo_3",
            "metadata": "{invalid json",
            "created_at": datetime(2026, 7, 29),
        }
        result = _row_to_outcome(row)
        # 实现是 pass，保留原值
        assert result["metadata"] == "{invalid json"


# ============== create ==============

class TestCreate:
    """create 方法测试"""

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_create_success_returns_ids(self, mock_conn_factory):
        """create 成功返回 id 和 outcome_id"""
        mock_cursor = MagicMock()
        # create 的 RETURNING id, outcome_id -> fetchone 返回这两个字段
        mock_cursor.fetchone.return_value = {"id": 42, "outcome_id": "wo_mockid"}
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        result = WorkOutcomeDB.create(
            tenant_id="t1",
            user_id="u1",
            session_id="s1",
            summary="交付文件：report.md",
            outcome_type="file",
            file_id="file_abc",
            file_name="report.md",
            file_path="/tmp/report.md",
            metadata={"source_tool": "cp"},
            source="cp_realtime",
        )

        assert result == {"id": 42, "outcome_id": "wo_mockid"}
        # 成功时调用 commit
        mock_conn.commit.assert_called_once()
        # 不应 rollback
        assert not mock_conn.rollback.called

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_create_default_outcome_type(self, mock_conn_factory):
        """未传 outcome_type 时默认 'other'"""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"id": 1, "outcome_id": "wo_1"}
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        WorkOutcomeDB.create(
            tenant_id="t1",
            user_id="u1",
            session_id="s1",
            summary="某个操作",
        )

        # 检查 execute 调用参数（第二个位置参数是 tuple）
        call_args = mock_cursor.execute.call_args
        params = call_args[0][1]
        # outcome_type 在 tuple 中位置 8（第 9 个，0-indexed 7）
        assert params[7] == "other"

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_create_rolls_back_on_exception(self, mock_conn_factory):
        """create 失败时 rollback"""
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB error")
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        with pytest.raises(Exception, match="DB error"):
            WorkOutcomeDB.create(
                tenant_id="t1",
                user_id="u1",
                session_id="s1",
                summary="test",
            )

        mock_conn.rollback.assert_called_once()


# ============== exists_by_session_and_type ==============

class TestExistsBySessionAndType:
    """exists_by_session_and_type 测试"""

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_exists_true(self, mock_conn_factory):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"cnt": 3}
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        result = WorkOutcomeDB.exists_by_session_and_type(
            session_id="s1", outcome_type="file"
        )
        assert result is True

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_exists_false(self, mock_conn_factory):
        mock_cursor = MagicMock()
        # exists_by_session_and_type 用 fetchone() is not None 判断
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        result = WorkOutcomeDB.exists_by_session_and_type(
            session_id="s1", outcome_type="file"
        )
        assert result is False


# ============== get_by_outcome_id ==============

class TestGetByOutcomeId:
    """get_by_outcome_id 测试"""

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_found(self, mock_conn_factory):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {
            "outcome_id": "wo_abc",
            "tenant_id": "t1",
            "user_id": "u1",
            "subagent_id": None,
            "session_id": "s1",
            "channel": "web",
            "summary": "test",
            "outcome_type": "other",
            "importance": "normal",
            "file_id": None,
            "file_name": None,
            "file_path": None,
            "metadata": {},
            "source": "scheduled_review",
            "chat_record_id": None,
            "review_batch_id": "rb_001",
            "review_confidence": 0.85,
            "created_at": datetime(2026, 7, 29, 10, 0, 0),
        }
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        result = WorkOutcomeDB.get_by_outcome_id("wo_abc")
        assert result is not None
        assert result["outcome_id"] == "wo_abc"
        assert result["review_confidence"] == 0.85

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_not_found(self, mock_conn_factory):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        result = WorkOutcomeDB.get_by_outcome_id("wo_not_exist")
        assert result is None


# ============== delete_by_outcome_id ==============

class TestDeleteByOutcomeId:
    """delete_by_outcome_id 测试"""

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_delete_success(self, mock_conn_factory):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        result = WorkOutcomeDB.delete_by_outcome_id("wo_abc")
        assert result is True
        mock_conn.commit.assert_called_once()

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_delete_not_found(self, mock_conn_factory):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        result = WorkOutcomeDB.delete_by_outcome_id("wo_not_exist")
        assert result is False

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_delete_rolls_back_on_exception(self, mock_conn_factory):
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB error")
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        with pytest.raises(Exception, match="DB error"):
            WorkOutcomeDB.delete_by_outcome_id("wo_abc")

        mock_conn.rollback.assert_called_once()


# ============== list_by_tenant ==============

class TestListByTenant:
    """list_by_tenant 测试"""

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_basic_list(self, mock_conn_factory):
        """基本列表查询"""
        mock_cursor = MagicMock()
        # 第一次 fetchone 返回总数
        # fetchall 返回列表
        mock_cursor.fetchone.return_value = {"cnt": 2}
        mock_cursor.fetchall.return_value = [
            {
                "outcome_id": "wo_1",
                "tenant_id": "t1",
                "user_id": "u1",
                "subagent_id": None,
                "session_id": "s1",
                "channel": "web",
                "summary": "成果1",
                "outcome_type": "file",
                "importance": "normal",
                "file_id": "file_1",
                "file_name": "f1.md",
                "file_path": "/tmp/f1.md",
                "metadata": {},
                "source": "cp_realtime",
                "chat_record_id": None,
                "review_batch_id": None,
                "review_confidence": None,
                "created_at": datetime(2026, 7, 29, 10, 0, 0),
            },
            {
                "outcome_id": "wo_2",
                "tenant_id": "t1",
                "user_id": "u1",
                "subagent_id": "trade-specialist",
                "session_id": "s2",
                "channel": "wecom",
                "summary": "成果2",
                "outcome_type": "action",
                "importance": "normal",
                "file_id": None,
                "file_name": None,
                "file_path": None,
                "metadata": {"k": "v"},
                "source": "scheduled_review",
                "chat_record_id": 123,
                "review_batch_id": "rb_001",
                "review_confidence": 0.85,
                "created_at": datetime(2026, 7, 29, 11, 0, 0),
            },
        ]
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        result = WorkOutcomeDB.list_by_tenant(
            tenant_id="t1", page=1, page_size=20
        )

        assert result["total"] == 2
        assert result["page"] == 1
        assert result["page_size"] == 20
        assert len(result["items"]) == 2
        assert result["items"][0]["outcome_id"] == "wo_1"

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_filter_by_outcome_type(self, mock_conn_factory):
        """按 outcome_type 过滤"""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"cnt": 0}
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        WorkOutcomeDB.list_by_tenant(
            tenant_id="t1", outcome_type="file", page=1, page_size=10
        )

        # 检查 SQL 包含 outcome_type 过滤
        sql_call = mock_cursor.execute.call_args_list[0]
        sql = sql_call[0][0]
        assert "outcome_type = %s" in sql

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_filter_by_date_range(self, mock_conn_factory):
        """按 start_date / end_date 过滤"""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"cnt": 0}
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        WorkOutcomeDB.list_by_tenant(
            tenant_id="t1",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 31),
            page=1,
            page_size=10,
        )

        sql_call = mock_cursor.execute.call_args_list[0]
        sql = sql_call[0][0]
        assert "created_at >= %s" in sql
        assert "created_at < %s" in sql

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_filter_by_main_subagent_uses_is_null(self, mock_conn_factory):
        """subagent_id='__NULL__' 转换为 IS NULL 查询（前端"主智能体"过滤）"""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"cnt": 0}
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        WorkOutcomeDB.list_by_tenant(
            tenant_id="t1", subagent_id="__NULL__", page=1, page_size=10
        )

        # SQL 应包含 IS NULL，且不应把 __NULL__ 作为参数传入
        sql_call = mock_cursor.execute.call_args_list[0]
        sql = sql_call[0][0]
        params = sql_call[0][1]
        assert "subagent_id IS NULL" in sql
        assert "__NULL__" not in params

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_filter_by_specific_subagent(self, mock_conn_factory):
        """指定 subagent_id 时用 = %s 精确匹配"""
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = {"cnt": 0}
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        WorkOutcomeDB.list_by_tenant(
            tenant_id="t1", subagent_id="trade-specialist", page=1, page_size=10
        )

        sql_call = mock_cursor.execute.call_args_list[0]
        sql = sql_call[0][0]
        params = sql_call[0][1]
        assert "subagent_id = %s" in sql
        assert "trade-specialist" in params


# ============== get_stats ==============

class TestGetStats:
    """get_stats 测试"""

    @patch("src.reports.work_outcome_db.get_db_connection")
    def test_stats_structure(self, mock_conn_factory):
        """统计返回结构完整"""
        mock_cursor = MagicMock()

        # fetchone 调用顺序：total / review / time_range
        mock_cursor.fetchone.side_effect = [
            {"cnt": 10},  # total
            {"review_batch_id": "rb_001", "cnt": 5, "avg_conf": 0.85},  # review
            {"start_ts": datetime(2026, 7, 1), "end_ts": datetime(2026, 7, 31)},  # time_range
        ]
        # fetchall 调用顺序：by_type / by_subagent / by_source / by_channel
        mock_cursor.fetchall.side_effect = [
            [{"outcome_type": "file", "cnt": 6}, {"outcome_type": "action", "cnt": 4}],  # by_type
            [{"subagent": "main", "cnt": 7}, {"subagent": "trade-specialist", "cnt": 3}],  # by_subagent
            [{"source": "cp_realtime", "cnt": 6}, {"source": "scheduled_review", "cnt": 4}],  # by_source
            [{"channel": "web", "cnt": 8}, {"channel": "wecom", "cnt": 2}],  # by_channel
        ]
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_conn_factory.return_value = mock_conn

        stats = WorkOutcomeDB.get_stats(tenant_id="t1")

        assert stats["total"] == 10
        assert stats["by_type"]["file"] == 6
        assert stats["by_type"]["action"] == 4
        assert stats["by_subagent"]["main"] == 7
        assert stats["by_subagent"]["trade-specialist"] == 3
        assert stats["by_source"]["cp_realtime"] == 6
        assert stats["by_source"]["scheduled_review"] == 4
        assert stats["by_channel"]["web"] == 8
        assert stats["by_channel"]["wecom"] == 2
        assert stats["review_stats"]["last_batch_id"] == "rb_001"
        assert stats["review_stats"]["last_batch_outcomes_count"] == 5
        assert stats["review_stats"]["avg_confidence"] == 0.85
