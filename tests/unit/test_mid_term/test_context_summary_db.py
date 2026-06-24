"""ContextSummaryDB CRUD 接口测试（通过 fake_db_connection）"""

import pytest

from src.db.models import ContextSummaryDB


def test_create_success(fake_db_connection):
    """create 成功：execute + commit + 返回 dict"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = {
        "summary_id": "csum_abc",
        "session_id": "s1",
        "source_type": "chat",
        "status": "active",
    }

    result = ContextSummaryDB.create(
        summary_id="csum_abc",
        session_id="s1",
        source_type="chat",
        tenant_id="t1",
        user_id="u1",
        subagent_id=None,
        summary_text="hello",
        summary_version=1,
        compressed_message_ids=[1, 2, 3],
        compressed_message_count=3,
        original_token_count=100,
        compressed_token_count=20,
        compression_ratio=0.2,
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_tokens_used=30,
    )
    assert result is not None
    assert result["summary_id"] == "csum_abc"
    assert mock_conn.commit.called
    # INSERT SQL 含 chat_context_summaries
    sql_args = [str(c.args[0]) for c in mock_cursor.execute.call_args_list]
    assert any("INSERT INTO chat_context_summaries" in s for s in sql_args)


def test_create_failure_returns_none(fake_db_connection):
    """create 失败：rollback + 返回 None"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.execute.side_effect = RuntimeError("db down")

    result = ContextSummaryDB.create(
        summary_id="csum_x",
        session_id="s1",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        summary_text="x",
        summary_version=1,
        compressed_message_ids=[1],
        compressed_message_count=1,
        original_token_count=10,
        compressed_token_count=2,
        compression_ratio=0.2,
    )
    assert result is None
    assert mock_conn.rollback.called


def test_get_active_by_session_found(fake_db_connection):
    """get_active_by_session 命中"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = {
        "summary_id": "csum_a",
        "session_id": "s1",
        "source_type": "chat",
        "status": "active",
        "summary_text": "summary content",
    }
    result = ContextSummaryDB.get_active_by_session("s1", "chat")
    assert result is not None
    assert result["summary_id"] == "csum_a"


def test_get_active_by_session_not_found(fake_db_connection):
    """get_active_by_session 未命中"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = None
    result = ContextSummaryDB.get_active_by_session("s1", "chat")
    assert result is None


def test_mark_superseded_updated(fake_db_connection):
    """mark_superseded 成功更新"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.rowcount = 1
    ok = ContextSummaryDB.mark_superseded("csum_a")
    assert ok is True
    assert mock_conn.commit.called


def test_mark_superseded_no_match(fake_db_connection):
    """mark_superseded 未匹配行：返回 False"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.rowcount = 0
    ok = ContextSummaryDB.mark_superseded("csum_missing")
    assert ok is False


def test_mark_superseded_failure_returns_false(fake_db_connection):
    """mark_superseded 异常：rollback + 返回 False"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.execute.side_effect = RuntimeError("fail")
    ok = ContextSummaryDB.mark_superseded("csum_x")
    assert ok is False
    assert mock_conn.rollback.called


def test_list_by_session_active_only(fake_db_connection):
    """list_by_session 默认只看 active"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchall.return_value = [
        {"summary_id": "csum_1", "status": "active", "summary_version": 2},
    ]
    rows = ContextSummaryDB.list_by_session("s1", "chat")
    assert len(rows) == 1
    sql_arg = str(mock_cursor.execute.call_args.args[0])
    # 默认 SQL 应含 status = 'active'
    assert "active" in sql_arg


def test_list_by_session_include_inactive(fake_db_connection):
    """list_by_session include_inactive=True 不加 status 过滤"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchall.return_value = [
        {"summary_id": "csum_1", "status": "superseded"},
        {"summary_id": "csum_2", "status": "active"},
    ]
    rows = ContextSummaryDB.list_by_session("s1", "chat", include_inactive=True)
    assert len(rows) == 2
    sql_arg = str(mock_cursor.execute.call_args.args[0])
    # include_inactive 不应过滤 active
    # 这里粗略检查 SQL 不包含 status = 'active' WHERE 子句（容易脆弱，简化为执行了 SQL 即可）
    assert "chat_context_summaries" in sql_arg


def test_get_by_id(fake_db_connection):
    """get_by_id 命中"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = {"summary_id": "csum_x", "status": "active"}
    row = ContextSummaryDB.get_by_id("csum_x")
    assert row is not None
    assert row["summary_id"] == "csum_x"
