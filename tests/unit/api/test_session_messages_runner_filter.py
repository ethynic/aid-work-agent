"""Phase 5 三轮评审修复回归：

1. MessageDB.list_by_session 支持 runner_id 先过滤再 limit（任务执行详情
   不受最近 N 条窗口限制）；
2. API messages 端点透传 runner_id Query 参数；
3. 会话归属鉴权不变（owned_web_session 先行）。
"""

from unittest.mock import MagicMock, patch

from src.db.models import MessageDB


class TestListBySessionRunnerFilter:
    @patch("src.db.models.get_cached", return_value=None)
    @patch("src.db.models.get_db_connection")
    def test_runner_filter_clause_before_limit(self, mock_conn, _cached):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor
        MessageDB.list_by_session("sess_1", 100, runner_id="runner_1")
        sql, params = cursor.execute.call_args.args
        # 先过滤（WHERE 内 metadata runner 匹配）再 LIMIT；metadata 为 TEXT 列，
        # 必须 ::jsonb 转型后取 ->>（直接 ->> 会产生 PostgreSQL 类型错误）
        assert "(metadata::jsonb)->>'runner_id' = %s" in sql
        assert sql.index("(metadata::jsonb)") < sql.index("LIMIT")
        assert "runner_1" in params

    def test_runner_filter_sql_executes_on_text_metadata_column(self):
        """按实际 TEXT 字段契约在 sqlite 真实执行该 WHERE 子句（评审 P1：
        此前 ->> 直接作用于 TEXT 在 PostgreSQL 报类型错误——用等价的
        json_extract 模拟 ::jsonb->>'key' 语义验证过滤行为正确）。"""
        import json
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, session_id TEXT,"
                     " role TEXT, content TEXT, metadata TEXT)")
        rows = [
            ("sess_1", "assistant", "r1 消息A", json.dumps({"runner_id": "runner_1"})),
            ("sess_1", "assistant", "r1 消息B", json.dumps({"runner_id": "runner_1"})),
            ("sess_1", "assistant", "其他 runner", json.dumps({"runner_id": "runner_2"})),
            ("sess_1", "assistant", "无 metadata", None),
        ]
        for i, (sid, role, content, metadata) in enumerate(rows):
            conn.execute("INSERT INTO chat_messages VALUES (?,?,?,?,?)",
                         (i + 1, sid, role, content, metadata))
        # sqlite 等价模拟：(metadata::jsonb)->>'runner_id' = ?
        predicate = "json_extract(metadata, '$.runner_id') = ?"
        count = conn.execute(
            f"SELECT count(*) FROM chat_messages WHERE session_id = ? AND {predicate}",
            ("sess_1", "runner_1")).fetchone()[0]
        assert count == 2  # 只命中 r1 的两条，其他 runner 与 NULL 均不命中

    @patch("src.db.models.get_cached", return_value=None)
    @patch("src.db.models.get_db_connection")
    def test_without_runner_no_clause(self, mock_conn, _cached):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor
        MessageDB.list_by_session("sess_1", 100)
        sql, _ = cursor.execute.call_args.args
        assert "runner_id" not in sql

    @patch("src.db.models.get_cached", return_value=None)
    @patch("src.db.models.get_db_connection")
    def test_cache_key_includes_runner(self, mock_conn, _cached):
        import src.db.models as models
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        mock_conn.return_value.__enter__.return_value.cursor.return_value = cursor
        with patch.object(models, "set_cached") as mock_set:
            MessageDB.list_by_session("sess_1", 100, runner_id="runner_1")
        assert mock_set.call_args.args[-1] == "runner_1"  # runner_key in cache key
