"""ContextSummaryDB 补充边界测试（SQL 差异化验证）"""

import pytest

from src.db.models import ContextSummaryDB


def test_list_by_session_inactive_filter_sql_differs(fake_db_connection):
    """include_inactive=False 的 SQL 必须包含 status = 'active' 过滤；
    include_inactive=True 的 SQL 不应包含该过滤。验证两个分支产生不同 SQL。"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchall.return_value = []

    # active only
    ContextSummaryDB.list_by_session("s1", "chat", include_inactive=False)
    sql_active = str(mock_cursor.execute.call_args.args[0])

    # all
    ContextSummaryDB.list_by_session("s1", "chat", include_inactive=True)
    sql_all = str(mock_cursor.execute.call_args.args[0])

    assert "status = 'active'" in sql_active.lower() or "status='active'" in sql_active.lower(), (
        "include_inactive=False 时 SQL 必须含 status='active' 过滤"
    )
    # 两次 SQL 必须不同（include_inactive=True 去掉了 status 过滤）
    assert sql_active != sql_all, (
        "include_inactive True/False 必须生成不同 SQL（前者去掉 status 过滤）"
    )


def test_get_active_by_session_orders_by_version_desc(fake_db_connection):
    """get_active_by_session 应 ORDER BY summary_version DESC（取最新版本）"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = None
    ContextSummaryDB.get_active_by_session("s1", "chat")
    sql = str(mock_cursor.execute.call_args.args[0]).lower()
    assert "order by summary_version desc" in sql, "应按 summary_version DESC 取最新"


def test_mark_superseded_filters_active_status(fake_db_connection):
    """mark_superseded 的 UPDATE WHERE 必须含 status='active'，避免误改已 superseded 的记录"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.rowcount = 1
    ContextSummaryDB.mark_superseded("csum_x")
    sql = str(mock_cursor.execute.call_args.args[0]).lower()
    assert "update chat_context_summaries" in sql
    assert "status = 'active'" in sql or "status='active'" in sql, (
        "UPDATE WHERE 子句必须限定 status='active'，避免重复 superseded"
    )


def test_create_includes_all_metadata_fields(fake_db_connection):
    """create 方法应把所有传入的元数据字段写入 SQL（验证 fallback_used/status 字段存在）"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchone.return_value = {
        "summary_id": "csum_x",
        "fallback_used": True,
        "status": "active",
    }
    ContextSummaryDB.create(
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
        fallback_used=True,
    )
    sql = str(mock_cursor.execute.call_args.args[0]).lower()
    assert "fallback_used" in sql, "INSERT 必须写入 fallback_used 字段"
    assert "status" in sql, "INSERT 必须写入 status 字段"
