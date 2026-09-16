"""
每日记忆总结 _get_user_conversations 渠道消息 status 过滤测试

「新会话」命令会把渠道旧消息软删除为 status='invalid'，这些消息不应计入
当日记忆总结（与 get_messages 的 LLM 上下文口径对齐）。
"""

from unittest.mock import MagicMock, patch

from src.memory.memory_summarizer import _get_user_conversations


class _FakeCursor:
    """捕获 SQL 并返回空结果的最小 cursor mock"""

    def __init__(self):
        self.executed_sql = []

    def execute(self, sql, params=None):
        self.executed_sql.append((sql, params))

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _FakeConn:
    def __init__(self):
        self.cursor_obj = _FakeCursor()
        self.cursor = MagicMock(return_value=self.cursor_obj)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _run_with_status_column(has_column: bool) -> str:
    conn = _FakeConn()
    with patch("src.db.database.get_db_connection", return_value=conn), patch(
        "src.channels.session.channel_session_manager._has_status_column",
        return_value=has_column,
    ):
        _get_user_conversations("tenant_x", "user_1")
    channel_sqls = [
        sql for sql, _ in conn.cursor_obj.executed_sql if "channel_messages" in sql
    ]
    assert channel_sqls, "应执行渠道消息查询"
    return channel_sqls[0]


class TestGetUserConversationsStatusFilter:
    def test_excludes_invalid_when_status_column_exists(self):
        """status 列存在时，渠道消息查询带 status='active' 过滤"""
        sql = _run_with_status_column(True)
        assert "m.status = 'active'" in sql

    def test_no_filter_when_status_column_missing(self):
        """存量库无 status 列时不加过滤（迁移兼容，查询不报错）"""
        sql = _run_with_status_column(False)
        assert "m.status = 'active'" not in sql
