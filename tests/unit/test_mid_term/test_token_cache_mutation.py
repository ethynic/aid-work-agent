"""Token 更新接口的返回值与数值转换测试。

本文件只替换数据库连接，实际调用 SessionDB / ChannelSessionManager，验证：
- rowcount 与布尔返回值一致
- 大数、浮点数和数值字符串转换
- 未知渠道会话不误报更新成功

表名、参数顺序、参数化查询和提交由 test_token_cache_update.py 覆盖。
"""

from contextlib import contextmanager
from unittest.mock import MagicMock


# ============== 参数语义测试 ==============


def test_session_db_update_returns_bool(monkeypatch):
    """返回值必须是 bool（True=更新了一行，False=未匹配 session）"""
    import src.db.models as models_mod
    from src.db.models import SessionDB

    for rowcount in (0, 1):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = rowcount
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        @contextmanager
        def _ctx():
            yield mock_conn

        monkeypatch.setattr(models_mod, "get_db_connection", _ctx)
        result = SessionDB.update_context_token_count("s", 100)
        assert isinstance(result, bool), (
            f"rowcount={rowcount}: 返回值必须是 bool，实际是 {type(result)}"
        )
        assert result == (rowcount > 0)


def test_session_db_update_handles_large_token_count(monkeypatch):
    """token_count = 1_000_000（大数）正确传递，不溢出"""
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

    big = 1_000_000
    SessionDB.update_context_token_count("s", big)
    args = mock_cursor.execute.call_args.args[1]
    assert args[0] == big


def test_session_db_update_int_coercion_for_float(monkeypatch):
    """传入 float 1024.7 → int 1024"""
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

    SessionDB.update_context_token_count("s", 1024.7)
    args = mock_cursor.execute.call_args.args[1]
    assert args[0] == 1024
    assert isinstance(args[0], int)


def test_session_db_update_int_coercion_for_numeric_string(monkeypatch):
    """传入字符串 '500' → int 500"""
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

    SessionDB.update_context_token_count("s", "500")
    args = mock_cursor.execute.call_args.args[1]
    assert args[0] == 500


# ============== 缺失 channel session 场景 ==============


def test_channel_update_unknown_session_returns_false(monkeypatch):
    """channel: session 不存在 → 返回 False"""
    import src.channels.session as session_mod
    from src.channels.session import ChannelSessionManager

    mock_cursor = MagicMock()
    mock_cursor.rowcount = 0
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    @contextmanager
    def _ctx():
        yield mock_conn

    monkeypatch.setattr(session_mod, "get_db_connection", _ctx)

    result = ChannelSessionManager().update_context_token_count("ghost", 100)
    assert result is False
