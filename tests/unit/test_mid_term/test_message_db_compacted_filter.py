"""MessageDB.list_by_session 默认过滤 compacted 测试（v3.1 Phase 4.4）

验证：
- 默认（include_compacted=False）只返回 compacted=False/NULL
- include_compacted=True 返回全部
- ChannelSessionManager.get_messages 同样行为
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_cursor_factory():
    """构造 mock cursor，记录每次 execute 的 SQL 和参数"""
    def _make(rows):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        return mock_cursor
    return _make


def _get_messages_models():
    from src.db.models import MessageDB
    return MessageDB


def _patch_db_conn(monkeypatch, mock_cursor):
    """patch get_db_connection 返回带 mock_cursor 的 conn"""
    from contextlib import contextmanager
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    @contextmanager
    def _ctx():
        yield mock_conn

    import src.db.models as models_mod
    monkeypatch.setattr(models_mod, "get_db_connection", _ctx)
    # models.py:20 静态绑定了 get_cached/set_cached，必须在绑定处 patch；
    # 否则全量回归下共享内存缓存被前置用例写入时提前返回（execute 不会被调用）
    monkeypatch.setattr(models_mod, "get_cached", lambda *a, **k: None)
    monkeypatch.setattr(models_mod, "set_cached", lambda *a, **k: None)
    # 屏蔽缓存
    import src.core.cache_utils as cache_mod
    monkeypatch.setattr(cache_mod, "get_cached", lambda *a, **k: None)
    monkeypatch.setattr(cache_mod, "set_cached", lambda *a, **k: None)


def test_default_filters_compacted(monkeypatch, fake_cursor_factory):
    """默认调用：SQL 必含 (compacted = FALSE OR compacted IS NULL)"""
    MessageDB = _get_messages_models()
    mock_cursor = fake_cursor_factory([
        {"id": 1, "session_id": "s1", "role": "user", "content": "x", "metadata": None, "compacted": False},
    ])
    _patch_db_conn(monkeypatch, mock_cursor)

    MessageDB.list_by_session("s1", limit=100)
    sql = str(mock_cursor.execute.call_args.args[0])
    assert "compacted = FALSE OR compacted IS NULL" in sql


def test_include_compacted_true_no_filter(monkeypatch, fake_cursor_factory):
    """include_compacted=True：SQL 不含 compacted 过滤"""
    MessageDB = _get_messages_models()
    mock_cursor = fake_cursor_factory([
        {"id": 1, "session_id": "s1", "role": "user", "content": "x", "metadata": None, "compacted": False},
        {"id": 2, "session_id": "s1", "role": "user", "content": "y", "metadata": None, "compacted": True},
    ])
    _patch_db_conn(monkeypatch, mock_cursor)

    MessageDB.list_by_session("s1", limit=100, include_compacted=True)
    sql = str(mock_cursor.execute.call_args.args[0])
    assert "compacted = FALSE OR compacted IS NULL" not in sql


def test_roles_filter_combined_with_compacted(monkeypatch, fake_cursor_factory):
    """roles + 默认 compacted 过滤同时生效"""
    MessageDB = _get_messages_models()
    mock_cursor = fake_cursor_factory([])
    _patch_db_conn(monkeypatch, mock_cursor)

    MessageDB.list_by_session("s1", limit=100, roles=["user", "assistant"])
    sql = str(mock_cursor.execute.call_args.args[0])
    assert "role IN" in sql
    assert "compacted = FALSE OR compacted IS NULL" in sql


def test_channel_manager_default_filters_compacted(monkeypatch, fake_cursor_factory):
    """ChannelSessionManager.get_messages 默认也过滤 compacted"""
    from src.channels.session import ChannelSessionManager
    mock_cursor = fake_cursor_factory([])
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        yield mock_conn

    import src.channels.session as session_mod
    monkeypatch.setattr(session_mod, "get_db_connection", _ctx)

    mgr = ChannelSessionManager()
    mgr.get_messages("s_kf", limit=50)
    sql = str(mock_cursor.execute.call_args.args[0])
    assert "compacted = FALSE OR compacted IS NULL" in sql


def test_channel_manager_include_compacted_true(monkeypatch, fake_cursor_factory):
    """ChannelSessionManager.get_messages(include_compacted=True) 不加过滤"""
    from src.channels.session import ChannelSessionManager
    mock_cursor = fake_cursor_factory([])
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        yield mock_conn

    import src.channels.session as session_mod
    monkeypatch.setattr(session_mod, "get_db_connection", _ctx)

    mgr = ChannelSessionManager()
    mgr.get_messages("s_kf", limit=50, include_compacted=True)
    sql = str(mock_cursor.execute.call_args.args[0])
    assert "compacted = FALSE OR compacted IS NULL" not in sql
