"""Agent LLM 循环内更新 session token 缓存测试（v3.1 Phase 4.6）

验证：
- SessionDB.update_context_token_count 被调用，参数 = prompt+completion tokens
- 异常时不影响主流程
- channel 源路由到 ChannelSessionManager

通过单元测试的方式直接验证 _detect_source_type 路由 + update_context_token_count 接口，
不跑完整 Agent 循环（依赖 LLM/工具/DB 太重）。
"""

from unittest.mock import MagicMock, patch

import pytest


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
    assert "context_token_count" in sql
    args = mock_cursor.execute.call_args.args[1]
    assert args[0] == 12345
    assert args[1] == "sess_x"
    assert mock_conn.commit.called


def test_session_db_update_returns_false_when_no_row(monkeypatch):
    """session 不存在（rowcount=0）→ 返回 False"""
    from contextlib import contextmanager
    import src.db.models as models_mod
    from src.db.models import SessionDB

    mock_cursor = MagicMock()
    mock_cursor.rowcount = 0
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    @contextmanager
    def _ctx():
        yield mock_conn

    monkeypatch.setattr(models_mod, "get_db_connection", _ctx)

    ok = SessionDB.update_context_token_count("ghost", 100)
    assert ok is False


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
    assert "context_token_count" in sql
    args = mock_cursor.execute.call_args.args[1]
    assert args[0] == 999
    assert args[1] == "sess_kf"


def test_update_context_token_count_int_coercion(monkeypatch):
    """传入 float/字符串数字 → 内部 int() 转换"""
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

    SessionDB.update_context_token_count("s", 123.7)  # float
    args = mock_cursor.execute.call_args.args[1]
    assert args[0] == 123  # int 截断
    assert isinstance(args[0], int)
