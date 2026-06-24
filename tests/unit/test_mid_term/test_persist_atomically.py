"""_persist_atomically 原子事务测试"""

import pytest

from src.memory.mid_term import ContextCompressionService


@pytest.fixture
def service(mid_term_settings):
    return ContextCompressionService(settings_cfg=mid_term_settings)


def test_persist_success_all_three_steps(service, fake_db_connection):
    """成功路径：三步 SQL 都执行，事务 commit，返回 summary_id。

    P1-3 后：summary_version 由 INSERT...SELECT COALESCE(MAX,0)+1 单 SQL 计算，
    原 SELECT MAX 已合并到 INSERT，SQL 总数减少 1。
    """
    mock_conn, mock_cursor = fake_db_connection

    # fetchall（旧 active summary）返回空（无前驱）
    mock_cursor.fetchall.return_value = []
    mock_cursor.rowcount = 5

    summary_id = service._persist_atomically(
        session_id="session_test1",
        source_type="chat",
        tenant_id="tenant_a",
        user_id="user_a",
        subagent_id=None,
        summary_text="some summary",
        compressed_message_ids=[1, 2, 3, 4, 5],
        original_token_count=1000,
        compressed_token_count=200,
        fallback_used=False,
        llm_tokens_used=50,
    )
    assert summary_id.startswith("csum_")
    # commit 被调用
    assert mock_conn.commit.called
    assert not mock_conn.rollback.called
    # 执行了多步 SQL：active 查询 + INSERT + UPDATE compacted
    assert mock_cursor.execute.call_count >= 3


def test_persist_failure_triggers_rollback(service, fake_db_connection):
    """失败路径：第三步 UPDATE 失败，事务回滚，前两步不生效。

    P1-3 后：SQL 顺序变为：active 查询(1) + INSERT(2) + UPDATE compacted(3)。
    让第 3 次抛异常。
    """
    mock_conn, mock_cursor = fake_db_connection

    execute_call_count = [0]

    def fake_execute(sql, args=None):
        execute_call_count[0] += 1
        # 第 1 次：active 查询 fetchall
        # 第 2 次：INSERT 新 summary
        # 第 3 次：UPDATE compacted → 抛异常
        if execute_call_count[0] == 3:
            raise RuntimeError("disk full on UPDATE")

    mock_cursor.execute.side_effect = fake_execute
    mock_cursor.fetchall.return_value = []

    with pytest.raises(RuntimeError, match="disk full"):
        service._persist_atomically(
            session_id="session_test2",
            source_type="chat",
            tenant_id=None,
            user_id=None,
            subagent_id=None,
            summary_text="x",
            compressed_message_ids=[1, 2],
            original_token_count=100,
            compressed_token_count=20,
        )

    # 回滚被调用
    assert mock_conn.rollback.called
    # commit 不应被调用
    assert not mock_conn.commit.called


def test_persist_supersedes_old_active(service, fake_db_connection):
    """旧 active summary 被置 superseded"""
    mock_conn, mock_cursor = fake_db_connection

    # 模拟有一条旧的 active summary
    mock_cursor.fetchall.return_value = [{"summary_id": "csum_old123"}]
    mock_cursor.rowcount = 3

    summary_id = service._persist_atomically(
        session_id="session_test3",
        source_type="wecom_kf",
        tenant_id="tenant_b",
        user_id="user_b",
        subagent_id="sub_x",
        summary_text="new summary",
        compressed_message_ids=[10, 11, 12],
        original_token_count=500,
        compressed_token_count=100,
    )
    assert summary_id.startswith("csum_")
    assert mock_conn.commit.called

    # 验证执行过的 SQL 包含 superseded 状态更新
    all_sql = " ".join(str(call.args[0]) for call in mock_cursor.execute.call_args_list)
    assert "superseded" in all_sql.lower()
