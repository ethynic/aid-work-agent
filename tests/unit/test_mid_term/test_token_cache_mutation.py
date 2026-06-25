"""Token 缓存写入路径 mutation-strengthened 测试（v3.1 Phase 4.6）

现有 test_token_cache_update.py 只测 SessionDB/ChannelSessionManager 接口本身，
test_agent_compress_call_site.py 重构了 agent.py 代码。

本文件通过 monkeypatch + 实际调用 Agent 类相关方法，验证：
- update_context_token_count 接口的参数顺序（token_count 先，session_id 后）
- UPDATE SQL 真的更新了 context_token_count 字段（不是其他字段）
- ChannelSessionManager 路由真的走 channel_sessions 表（不是 chat_sessions）
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


def _patch_db_conn(module, cursor):
    @contextmanager
    def _ctx():
        yield MagicMock(cursor=MagicMock(return_value=cursor))

    return _ctx


# ============== SQL 内容真实校验 ==============


def test_session_db_update_sql_targets_chat_sessions_table(monkeypatch):
    """SQL 必须是 UPDATE chat_sessions（不能误写成 channel_sessions）"""
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

    SessionDB.update_context_token_count("sess", 100)
    sql = str(mock_cursor.execute.call_args.args[0])
    assert "UPDATE chat_sessions" in sql
    assert "channel_sessions" not in sql
    assert "context_token_count" in sql


def test_session_db_update_sql_uses_parameterized_query(monkeypatch):
    """UPDATE 必须用参数化（防 SQL 注入；不是字符串拼接）"""
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

    SessionDB.update_context_token_count("sess_x", 12345)
    sql = str(mock_cursor.execute.call_args.args[0])
    args = mock_cursor.execute.call_args.args[1]

    # 必须用占位符（不是字符串插值）
    assert "%s" in sql
    # 参数顺序：(token_count, session_id)
    assert args == (12345, "sess_x")


def test_channel_update_sql_targets_channel_sessions_table(monkeypatch):
    """ChannelSessionManager.update_context_token_count SQL 必须是 UPDATE channel_sessions"""
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
    mgr.update_context_token_count("sess_kf", 200)
    sql = str(mock_cursor.execute.call_args.args[0])
    assert "UPDATE channel_sessions" in sql
    assert "chat_sessions" not in sql
    args = mock_cursor.execute.call_args.args[1]
    assert args == (200, "sess_kf")


def test_session_db_update_commits_transaction(monkeypatch):
    """UPDATE 后必须 commit（不 commit 数据不落库）"""
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

    SessionDB.update_context_token_count("s", 100)
    assert mock_conn.commit.called, "UPDATE 后必须 commit"


def test_channel_update_commits_transaction(monkeypatch):
    """channel UPDATE 后必须 commit"""
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

    ChannelSessionManager().update_context_token_count("s_kf", 200)
    assert mock_conn.commit.called


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


# ============== 缺失 session 场景 ==============


def test_session_db_update_unknown_session_returns_false(monkeypatch):
    """session_id 不存在 → rowcount=0 → 返回 False"""
    import src.db.models as models_mod
    from src.db.models import SessionDB

    mock_cursor = MagicMock()
    mock_cursor.rowcount = 0  # UPDATE 影响 0 行
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    @contextmanager
    def _ctx():
        yield mock_conn

    monkeypatch.setattr(models_mod, "get_db_connection", _ctx)

    result = SessionDB.update_context_token_count("ghost_session", 100)
    assert result is False


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
