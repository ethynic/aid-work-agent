"""
trace_persist.update_user_message_id 单元测试。

验证：
1. 成功路径：UPDATE SQL 正确、参数顺序正确、commit 被调用
2. 失败路径：异常被吞，只记 debug log，不抛出
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@contextmanager
def _mock_logs_cm(cursor):
    """mock get_logs_connection 返回的 context manager"""
    yield cursor


def test_update_user_message_id_success():
    """成功路径：UPDATE SQL 与参数正确，commit 被调用"""
    mock_cursor = MagicMock()

    from src.core.trace_persist import update_user_message_id
    with patch(
        "src.db.database.get_logs_connection",
        return_value=_mock_logs_cm(mock_cursor),
    ):
        update_user_message_id("tr_abc123", "msg_xyz789")

    # 验证 execute 调用
    mock_cursor.execute.assert_called_once()
    sql_arg, params_arg = mock_cursor.execute.call_args[0]
    assert "UPDATE obs_traces" in sql_arg
    assert "user_message_id" in sql_arg
    assert "trace_id" in sql_arg
    # 参数顺序：(user_message_id, trace_id)
    assert params_arg == ("msg_xyz789", "tr_abc123")
    # commit 必须被调用
    mock_cursor.commit.assert_called_once()


def test_update_user_message_id_failure_no_raise():
    """失败路径：execute 抛异常时，函数不抛出（只记 debug log）"""
    mock_cursor = MagicMock()
    mock_cursor.execute.side_effect = Exception("DB connection lost")

    from src.core.trace_persist import update_user_message_id
    with patch(
        "src.db.database.get_logs_connection",
        return_value=_mock_logs_cm(mock_cursor),
    ):
        # 不应抛异常
        update_user_message_id("tr_abc123", "msg_xyz789")

    # execute 被调用一次（异常发生在 execute）
    mock_cursor.execute.assert_called_once()
    # commit 不应被调用（execute 已抛异常）
    mock_cursor.commit.assert_not_called()


def test_update_user_message_id_connection_failure_no_raise():
    """连接失败时也不抛异常"""
    from src.core.trace_persist import update_user_message_id

    # get_logs_connection 本身抛异常（如追踪库未配置）
    with patch(
        "src.db.database.get_logs_connection",
        side_effect=RuntimeError("追踪库未配置"),
    ):
        # 不应抛异常
        update_user_message_id("tr_abc123", "msg_xyz789")
