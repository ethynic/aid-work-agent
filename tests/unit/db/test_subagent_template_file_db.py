"""
subagent_template_file_db 单元测试。

mock get_db_connection，验证 get / set(UPSERT 全量覆盖) / delete 的 SQL 行为与返回。
"""

import pytest
from unittest.mock import patch, MagicMock

from src.db.subagent_template_file_db import SubagentTemplateFileDB


def _mock_dbcm(cursor):
    """构造 get_db_connection() 返回的 context manager mock"""
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn


class TestGet:
    def test_returns_files_when_present(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"files": [{"name": "A", "file_id": "f1"}]}
        with patch(
            "src.db.subagent_template_file_db.get_db_connection",
            return_value=_mock_dbcm(cursor),
        ):
            result = SubagentTemplateFileDB.get("tenant_1", "agent_x")
        assert result == [{"name": "A", "file_id": "f1"}]

    def test_returns_empty_list_when_absent(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        with patch(
            "src.db.subagent_template_file_db.get_db_connection",
            return_value=_mock_dbcm(cursor),
        ):
            result = SubagentTemplateFileDB.get("tenant_1", "agent_x")
        assert result == []

    def test_returns_empty_list_when_files_null(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = {"files": None}
        with patch(
            "src.db.subagent_template_file_db.get_db_connection",
            return_value=_mock_dbcm(cursor),
        ):
            result = SubagentTemplateFileDB.get("tenant_1", "agent_x")
        assert result == []


class TestSet:
    def test_upsert_calls_commit_and_returns_true(self):
        cursor = MagicMock()
        cm = _mock_dbcm(cursor)
        with patch(
            "src.db.subagent_template_file_db.get_db_connection", return_value=cm
        ):
            ok = SubagentTemplateFileDB.set("tenant_1", "agent_x", [{"name": "A"}])
        assert ok is True
        assert cursor.execute.call_count == 1
        cm.commit.assert_called_once()

    def test_rollback_returns_false_on_exception(self):
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("db error")
        cm = _mock_dbcm(cursor)
        with patch(
            "src.db.subagent_template_file_db.get_db_connection", return_value=cm
        ):
            ok = SubagentTemplateFileDB.set("tenant_1", "agent_x", [])
        assert ok is False
        cm.rollback.assert_called_once()


class TestDelete:
    def test_delete_returns_true_when_rowcount(self):
        cursor = MagicMock()
        cursor.rowcount = 1
        cm = _mock_dbcm(cursor)
        with patch(
            "src.db.subagent_template_file_db.get_db_connection", return_value=cm
        ):
            ok = SubagentTemplateFileDB.delete("tenant_1", "agent_x")
        assert ok is True
        cm.commit.assert_called_once()

    def test_delete_returns_false_when_no_row(self):
        cursor = MagicMock()
        cursor.rowcount = 0
        with patch(
            "src.db.subagent_template_file_db.get_db_connection",
            return_value=_mock_dbcm(cursor),
        ):
            ok = SubagentTemplateFileDB.delete("tenant_1", "agent_x")
        assert ok is False
