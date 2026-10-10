"""SessionDB / ChannelSessionManager 的 token 缓存更新接口测试。

验证实际 UPDATE 表、字段、参数化查询、成功返回和提交事务。
返回值矩阵与数值转换由 test_token_cache_mutation.py 覆盖。
"""

from unittest.mock import MagicMock


def test_session_db_update_context_token_count(monkeypatch):
    """SessionDB.update_context_token_count 执行正确的 UPDATE"""
    from contextlib import contextmanager
    import src.db.models as models_mod
    from src.db.models import SessionDB

    mock_cursor = MagicMock()
    mock_cursor.rowcount = 1
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    @contextmanager
    def _ctx():
        yield mock_conn

    monkeypatch.setattr(models_mod, "get_db_connection", _ctx)

    ok = SessionDB.update_context_token_count("sess_x", 12345)
    assert ok is True
    sql = str(mock_cursor.execute.call_args.args[0])
    assert "UPDATE chat_sessions" in sql
    assert "channel_sessions" not in sql
    assert "context_token_count" in sql
    assert "%s" in sql
    args = mock_cursor.execute.call_args.args[1]
    assert args == (12345, "sess_x")
    assert mock_conn.commit.called


def test_channel_mgr_update_context_token_count(monkeypatch):
    """ChannelSessionManager.update_context_token_count 执行正确的 UPDATE"""
    from contextlib import contextmanager
    import src.channels.session as session_mod
    from src.channels.session import ChannelSessionManager

    mock_cursor = MagicMock()
    mock_cursor.rowcount = 1
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    @contextmanager
    def _ctx():
        yield mock_conn

    monkeypatch.setattr(session_mod, "get_db_connection", _ctx)

    mgr = ChannelSessionManager()
    ok = mgr.update_context_token_count("sess_kf", 999)
    assert ok is True
    sql = str(mock_cursor.execute.call_args.args[0])
    assert "UPDATE channel_sessions" in sql
    assert "chat_sessions" not in sql
    assert "context_token_count" in sql
    args = mock_cursor.execute.call_args.args[1]
    assert args == (999, "sess_kf")
    assert mock_conn.commit.called
