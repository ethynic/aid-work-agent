"""_persist_atomically 边界：空 compressed_ids / ratio 计算 / 旧 active superseded 语义"""

import pytest

from src.config.settings import MidTermMemoryConfig
from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def test_persist_empty_compressed_ids_skips_compacted_update(service, fake_db_connection):
    """compressed_message_ids 为空数组：第④步 UPDATE chat_messages 不应执行（有 if 守卫）"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchall.return_value = []

    summary_id = service._persist_atomically(
        session_id="sess_empty",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        summary_text="empty summary",
        compressed_message_ids=[],
        original_token_count=100,
        compressed_token_count=20,
    )
    assert summary_id.startswith("csum_")
    assert mock_conn.commit.called

    # SQL 执行序列中不应包含 UPDATE chat_messages
    all_sql = " ".join(str(c.args[0]) for c in mock_cursor.execute.call_args_list).lower()
    assert "update chat_messages" not in all_sql, (
        "空 compressed_message_ids 时不应执行 UPDATE chat_messages"
    )


def test_persist_zero_original_token_ratio_is_zero(service, fake_db_connection):
    """original_token_count=0 → ratio=0.0（避免除零）"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchall.return_value = []

    summary_id = service._persist_atomically(
        session_id="sess_zero",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        summary_text="x",
        compressed_message_ids=[1],
        original_token_count=0,
        compressed_token_count=0,
    )
    assert summary_id.startswith("csum_")
    # 验证 INSERT 参数中 ratio=0.0
    insert_call = None
    for call in mock_cursor.execute.call_args_list:
        sql = str(call.args[0])
        if "INSERT INTO chat_context_summaries" in sql:
            insert_call = call
            break
    assert insert_call is not None, "应执行 INSERT chat_context_summaries"
    insert_args = insert_call.args[1]
    # P1-3 后参数列表（按 SELECT 子句占位符顺序）：
    # 0:summary_id 1:session_id 2:source_type 3:tenant_id 4:user_id 5:subagent_id
    # 6:summary_text 7:session_id(max subquery) 8:source_type(max subquery)
    # 9:ids 10:count 11:orig_tok 12:comp_tok 13:ratio
    # 14:llm_provider 15:llm_model 16:llm_tokens_used 17:fallback_used 18:status
    assert insert_args[13] == 0.0, "original_token_count=0 时 ratio 必须为 0.0"


def test_persist_multiple_old_active_all_superseded(service, fake_db_connection):
    """存在多条旧 active summary（数据异常场景）→ 全部置为 superseded（不应只处理第一条）"""
    mock_conn, mock_cursor = fake_db_connection
    # 模拟数据异常：有 3 条 active（正常应只有 1 条，但部分索引未生效等场景）
    mock_cursor.fetchall.return_value = [
        {"summary_id": "csum_old1"},
        {"summary_id": "csum_old2"},
        {"summary_id": "csum_old3"},
    ]
    mock_cursor.rowcount = 1

    summary_id = service._persist_atomically(
        session_id="sess_multi_active",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        summary_text="new",
        compressed_message_ids=[1, 2],
        original_token_count=100,
        compressed_token_count=20,
    )
    assert summary_id.startswith("csum_")

    # 验证 UPDATE...superseded 至少执行 3 次（每条旧 active 一次）
    superseded_updates = 0
    for call in mock_cursor.execute.call_args_list:
        sql = str(call.args[0]).lower()
        if "update chat_context_summaries" in sql and "superseded" in sql:
            superseded_updates += 1
    assert superseded_updates == 3, (
        f"应有 3 条旧 active 被置为 superseded，实际 {superseded_updates}"
    )


def test_persist_new_version_increments_from_max(service, fake_db_connection):
    """新 summary_version = max(summary_version) + 1（验证递增计算）。

    P1-3 后：summary_version 由 INSERT...SELECT COALESCE(MAX,0)+1 单 SQL 计算，
    不再在 Python 层读-改-写，避免并发竞态。
    本测试验证 INSERT SQL 包含「COALESCE(MAX(...))+1」表达式。
    """
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchall.return_value = []

    summary_id = service._persist_atomically(
        session_id="sess_ver",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        summary_text="x",
        compressed_message_ids=[1],
        original_token_count=10,
        compressed_token_count=2,
    )
    assert summary_id.startswith("csum_")

    # 找到 INSERT 调用，验证 SQL 含 COALESCE((SELECT MAX(summary_version)...), 0) + 1
    for call in mock_cursor.execute.call_args_list:
        sql = str(call.args[0]).lower()
        if "insert into chat_context_summaries" in sql:
            assert "coalesce(" in sql and "max(summary_version)" in sql, (
                "INSERT SQL 必须含 COALESCE((SELECT MAX(summary_version)...), 0) 子句（P1-3）"
            )
            assert "+ 1" in sql or "+1" in sql, "必须含 +1 递增"
            return
    pytest.fail("未找到 INSERT 语句")


def test_persist_compacted_rowcount_mismatch_logs_warning_not_raise(
    service, fake_db_connection, caplog
):
    """UPDATE chat_messages 影响行数 != 预期 → 记 warning 但不抛异常（最终 commit）"""
    mock_conn, mock_cursor = fake_db_connection
    mock_cursor.fetchall.return_value = []
    # 假装 DB 只更新了 3 行，但传入了 5 个 id（2 行已被删/已 compacted）
    mock_cursor.rowcount = 3

    summary_id = service._persist_atomically(
        session_id="sess_mismatch",
        source_type="chat",
        tenant_id=None,
        user_id=None,
        subagent_id=None,
        summary_text="x",
        compressed_message_ids=[1, 2, 3, 4, 5],
        original_token_count=100,
        compressed_token_count=20,
    )
    # 不抛异常
    assert summary_id.startswith("csum_")
    # 仍 commit
    assert mock_conn.commit.called


def test_persist_insert_failure_rolls_back(service, fake_db_connection):
    """INSERT chat_context_summaries 失败 → 整个事务回滚，不更新任何 compacted。

    P1-3 后：SQL 顺序：active 查询(1) + INSERT(2) 抛异常。
    """
    mock_conn, mock_cursor = fake_db_connection
    call_count = [0]

    def fake_execute(sql, args=None):
        call_count[0] += 1
        # 第 1 次 fetchall, 第 2 次 INSERT 抛异常
        if call_count[0] == 2:
            raise RuntimeError("INSERT failed: constraint violation")

    mock_cursor.execute.side_effect = fake_execute
    mock_cursor.fetchall.return_value = []

    with pytest.raises(RuntimeError, match="INSERT failed"):
        service._persist_atomically(
            session_id="sess_insert_fail",
            source_type="chat",
            tenant_id=None,
            user_id=None,
            subagent_id=None,
            summary_text="x",
            compressed_message_ids=[1, 2],
            original_token_count=10,
            compressed_token_count=2,
        )
    assert mock_conn.rollback.called
    assert not mock_conn.commit.called
