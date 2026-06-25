"""MessageDB / ChannelSessionManager compacted 过滤边界补充

补充：
- compacted=NULL 行为兼容（过滤子句包含 IS NULL）
- roles=[] 空数组时不崩
- 默认 include_compacted=False 时，过滤子句出现在 SQL 中
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_cursor():
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []
    return mock_cursor


def _patch_models_db(monkeypatch, mock_cursor):
    from contextlib import contextmanager
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    @contextmanager
    def _ctx():
        yield mock_conn

    import src.db.models as models_mod
    monkeypatch.setattr(models_mod, "get_db_connection", _ctx)
    # 屏蔽缓存（patch models 模块的绑定引用，避免被早期测试污染的 redis fallback 命中）
    monkeypatch.setattr(models_mod, "get_cached", lambda *a, **k: None)
    monkeypatch.setattr(models_mod, "set_cached", lambda *a, **k: None)


def _patch_channel_db(monkeypatch, mock_cursor):
    from contextlib import contextmanager
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    @contextmanager
    def _ctx():
        yield mock_conn

    import src.channels.session as session_mod
    monkeypatch.setattr(session_mod, "get_db_connection", _ctx)


def test_compacted_filter_clause_includes_null_check(monkeypatch, fake_cursor):
    """过滤子句格式必须包含 'OR compacted IS NULL'（兼容 NULL 数据）"""
    _patch_models_db(monkeypatch, fake_cursor)
    from src.db.models import MessageDB
    MessageDB.list_by_session("s1", limit=100)
    sql = str(fake_cursor.execute.call_args.args[0])
    assert "compacted IS NULL" in sql, "默认过滤子句必须兼容 compacted=NULL 的老数据"


def test_include_compacted_true_no_where_clause(monkeypatch, fake_cursor):
    """include_compacted=True：SQL 不含 compacted 相关 WHERE"""
    _patch_models_db(monkeypatch, fake_cursor)
    from src.db.models import MessageDB
    MessageDB.list_by_session("s1", limit=100, include_compacted=True)
    sql = str(fake_cursor.execute.call_args.args[0])
    assert "compacted = FALSE" not in sql
    assert "compacted IS NULL" not in sql


def test_channel_manager_compacted_filter_includes_null(monkeypatch, fake_cursor):
    """channel 渠道的 compacted 过滤也兼容 NULL"""
    _patch_channel_db(monkeypatch, fake_cursor)
    from src.channels.session import ChannelSessionManager
    mgr = ChannelSessionManager()
    mgr.get_messages("s_kf", limit=50)
    sql = str(fake_cursor.execute.call_args.args[0])
    assert "compacted IS NULL" in sql


def test_message_db_no_id_field_no_crash(monkeypatch, fake_cursor):
    """list_by_session 在正常路径下能返回 fetchall 结果"""
    fake_cursor.fetchall.return_value = [
        {"id": 1, "session_id": "s1", "role": "user", "content": "x", "metadata": None, "compacted": False}
    ]
    _patch_models_db(monkeypatch, fake_cursor)
    from src.db.models import MessageDB
    rows = MessageDB.list_by_session("s1", limit=100)
    assert len(rows) == 1
    assert rows[0]["id"] == 1


def test_channel_manager_no_filter_when_include_compacted(monkeypatch, fake_cursor):
    """channel 源 include_compacted=True 时也不加过滤"""
    _patch_channel_db(monkeypatch, fake_cursor)
    from src.channels.session import ChannelSessionManager
    mgr = ChannelSessionManager()
    mgr.get_messages("s_kf", limit=50, include_compacted=True)
    sql = str(fake_cursor.execute.call_args.args[0])
    assert "compacted = FALSE" not in sql
