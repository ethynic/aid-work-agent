"""compacted 过滤的语义加强测试（v3.1 Phase 4.4）

既有测试用 `assert "compacted = FALSE OR compacted IS NULL" in sql` 验证，
但任何注释里包含这串字符、或 SELECT 列里包含它、或 ORDER BY 子句包含它都会通过。

本文件通过解析 SQL 结构验证：
- compacted 过滤出现在 WHERE 子句（不是 SELECT 列或注释）
- include_compacted=True 时 WHERE 不含 compacted 相关条件
- before_message_id 与 compacted 过滤共存（ChannelSessionManager 分页场景）
- 缓存 key 因 include_compacted 不同而不同（防止缓存污染）
"""

import re
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


def _make_mock_cursor(rows=None):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows or []
    return mock_cursor


def _patch_db_with_cursor(monkeypatch, module, mock_cursor):
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cursor

    @contextmanager
    def _ctx():
        yield mock_conn

    monkeypatch.setattr(module, "get_db_connection", _ctx)


def _extract_where_clause(sql: str) -> str:
    """从 SQL 中提取 WHERE 子句（小写）"""
    m = re.search(r"\bwhere\b(.+?)(\border by\b|\blimit\b|\bgroup by\b|$)",
                  sql, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


# ============== MessageDB.list_by_session ==============


def test_compacted_filter_in_where_clause_not_select(monkeypatch):
    """compacted 过滤必须出现在 WHERE 子句，不能在 SELECT 列"""
    import src.db.models as models_mod
    from src.db.models import MessageDB

    mock_cursor = _make_mock_cursor()
    _patch_db_with_cursor(monkeypatch, models_mod, mock_cursor)
    monkeypatch.setattr(models_mod, "get_cached", lambda *a, **k: None)
    monkeypatch.setattr(models_mod, "set_cached", lambda *a, **k: None)

    MessageDB.list_by_session("s1", limit=100)
    sql = str(mock_cursor.execute.call_args.args[0])

    where_clause = _extract_where_clause(sql).lower()
    assert "compacted" in where_clause, (
        "compacted 过滤必须出现在 WHERE 子句中，实际 WHERE=" + where_clause
    )


def test_compacted_filter_not_in_where_when_include_true(monkeypatch):
    """include_compacted=True → WHERE 中不含 compacted"""
    import src.db.models as models_mod
    from src.db.models import MessageDB

    mock_cursor = _make_mock_cursor()
    _patch_db_with_cursor(monkeypatch, models_mod, mock_cursor)
    monkeypatch.setattr(models_mod, "get_cached", lambda *a, **k: None)
    monkeypatch.setattr(models_mod, "set_cached", lambda *a, **k: None)

    MessageDB.list_by_session("s1", limit=100, include_compacted=True)
    sql = str(mock_cursor.execute.call_args.args[0])

    where_clause = _extract_where_clause(sql).lower()
    assert "compacted" not in where_clause


def test_roles_filter_and_compacted_filter_coexist_in_where(monkeypatch):
    """roles + compacted 同时在 WHERE 子句中（用 AND 连接）"""
    import src.db.models as models_mod
    from src.db.models import MessageDB

    mock_cursor = _make_mock_cursor()
    _patch_db_with_cursor(monkeypatch, models_mod, mock_cursor)
    monkeypatch.setattr(models_mod, "get_cached", lambda *a, **k: None)
    monkeypatch.setattr(models_mod, "set_cached", lambda *a, **k: None)

    MessageDB.list_by_session("s1", limit=100, roles=["user", "assistant"])
    sql = str(mock_cursor.execute.call_args.args[0]).lower()
    where_clause = _extract_where_clause(sql)

    assert "role in" in where_clause
    assert "compacted" in where_clause
    # 两个条件都通过 AND 连接
    assert "and" in where_clause


def test_cache_key_differs_by_include_compacted(monkeypatch):
    """include_compacted=True/False 应使用不同 cache key（防缓存污染）"""
    import src.db.models as models_mod
    from src.db.models import MessageDB

    captured_keys = []
    mock_cursor = _make_mock_cursor()

    _patch_db_with_cursor(monkeypatch, models_mod, mock_cursor)

    def _fake_get_cached(*args):
        # 记录 cache key（第 4 个参数是 compacted_flag）
        captured_keys.append(args)
        return None
    monkeypatch.setattr(models_mod, "get_cached", _fake_get_cached)
    monkeypatch.setattr(models_mod, "set_cached", lambda *a, **k: None)

    MessageDB.list_by_session("s1", limit=100, include_compacted=False)
    MessageDB.list_by_session("s1", limit=100, include_compacted=True)

    assert len(captured_keys) == 2
    # 两次调用的 cache key 必须不同（compacted_flag 参数不同）
    key1 = captured_keys[0]
    key2 = captured_keys[1]
    assert key1 != key2, (
        "include_compacted=True/False 必须使用不同的 cache key（防缓存污染）"
    )


# ============== ChannelSessionManager.get_messages ==============


def test_channel_compacted_filter_in_where(monkeypatch):
    """channel get_messages: compacted 必须出现在 WHERE"""
    import src.channels.session as session_mod
    from src.channels.session import ChannelSessionManager

    mock_cursor = _make_mock_cursor()
    _patch_db_with_cursor(monkeypatch, session_mod, mock_cursor)

    ChannelSessionManager().get_messages("s_kf", limit=50)
    sql = str(mock_cursor.execute.call_args.args[0])

    where_clause = _extract_where_clause(sql).lower()
    assert "compacted" in where_clause


def test_channel_compacted_filter_with_before_message_id(monkeypatch):
    """channel: before_message_id 分页 + compacted 过滤同时生效"""
    import src.channels.session as session_mod
    from src.channels.session import ChannelSessionManager

    mock_cursor = _make_mock_cursor()
    _patch_db_with_cursor(monkeypatch, session_mod, mock_cursor)

    ChannelSessionManager().get_messages(
        "s_kf", limit=50, before_message_id="msg_abc"
    )
    sql = str(mock_cursor.execute.call_args.args[0]).lower()
    where_clause = _extract_where_clause(sql)

    # 必须同时有 id < 子查询 和 compacted 过滤
    assert "id <" in where_clause, "before_message_id 必须产生 id < 子查询"
    assert "compacted" in where_clause, "compacted 过滤必须共存"


def test_channel_no_compacted_filter_when_include_true_with_paging(monkeypatch):
    """channel: include_compacted=True + before_message_id → WHERE 不含 compacted"""
    import src.channels.session as session_mod
    from src.channels.session import ChannelSessionManager

    mock_cursor = _make_mock_cursor()
    _patch_db_with_cursor(monkeypatch, session_mod, mock_cursor)

    ChannelSessionManager().get_messages(
        "s_kf", limit=50, before_message_id="msg_abc", include_compacted=True
    )
    sql = str(mock_cursor.execute.call_args.args[0])
    where_clause = _extract_where_clause(sql).lower()

    assert "compacted" not in where_clause


# ============== 缓存 key 在 channel 也应该生效（如果有的话）==============


def test_message_db_returns_full_compacted_rows_when_include_true(monkeypatch):
    """include_compacted=True 时返回所有行（含 compacted=True）"""
    import src.db.models as models_mod
    from src.db.models import MessageDB

    rows = [
        {"id": 1, "session_id": "s1", "role": "user", "content": "active",
         "metadata": None, "compacted": False},
        {"id": 2, "session_id": "s1", "role": "user", "content": "compacted_msg",
         "metadata": None, "compacted": True},
    ]
    mock_cursor = _make_mock_cursor(rows)
    _patch_db_with_cursor(monkeypatch, models_mod, mock_cursor)
    monkeypatch.setattr(models_mod, "get_cached", lambda *a, **k: None)
    monkeypatch.setattr(models_mod, "set_cached", lambda *a, **k: None)

    result = MessageDB.list_by_session("s1", limit=100, include_compacted=True)
    assert len(result) == 2
    contents = [r["content"] for r in result]
    assert "active" in contents
    assert "compacted_msg" in contents


def test_channel_get_messages_parses_json_fields(monkeypatch):
    """channel: get_messages 返回的 attachments/metadata 字段被 JSON 解析"""
    import json
    import src.channels.session as session_mod
    from src.channels.session import ChannelSessionManager

    rows = [
        {
            "id": 1, "session_id": "s", "role": "user", "content": "x",
            "attachments": json.dumps([{"url": "http://x"}]),
            "metadata": json.dumps({"k": "v"}),
        },
    ]
    mock_cursor = _make_mock_cursor(rows)
    _patch_db_with_cursor(monkeypatch, session_mod, mock_cursor)

    result = ChannelSessionManager().get_messages("s", limit=10)
    assert len(result) == 1
    # attachments 应被解析为 list
    assert isinstance(result[0]["attachments"], list)
    assert result[0]["attachments"][0]["url"] == "http://x"
    # metadata 应被解析为 dict
    assert isinstance(result[0]["metadata"], dict)
    assert result[0]["metadata"]["k"] == "v"
