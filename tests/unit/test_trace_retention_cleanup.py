"""追踪库保留清理调度任务单元测试

覆盖 src/scheduler/manager.py 两个回调：
- _run_trace_retention_cleanup：分批删除 obs_traces/obs_spans/obs_scores，
  批满继续、未满停止、异常返回已删行数、追踪库未配置跳过
- _run_logs_db_vacuum：autocommit 连接执行 VACUUM、未配置跳过、异常隔离
"""

from unittest.mock import MagicMock, patch

import pytest

from src.scheduler.manager import ScheduledTaskManager


@pytest.fixture
def manager():
    return ScheduledTaskManager()


def _make_logs_conn(fetch_batches=None, delete_rowcounts=None):
    """构造 get_logs_connection 的 mock 上下文。

    fetch_batches: 每次 fetchall 依次返回的行列表（RealDictCursor 字典行）
    delete_rowcounts: 每条非 SELECT 语句执行后 cursor.rowcount 依次取值
    """
    conn = MagicMock()
    cursor = conn.cursor.return_value
    fetches = list(fetch_batches or [])
    rowcounts = list(delete_rowcounts or [])

    def _execute(*args, **kwargs):
        sql = ((args[0] if args else "") or "").lstrip().upper()
        if sql.startswith("SELECT"):
            cursor.rowcount = -1
        else:
            cursor.rowcount = rowcounts.pop(0) if rowcounts else 0

    cursor.execute.side_effect = _execute
    cursor.fetchall.side_effect = lambda: fetches.pop(0) if fetches else []

    ctx = MagicMock()
    ctx.__enter__.return_value = cursor
    ctx.__exit__.return_value = False
    return ctx


class TestTraceRetentionCleanup:
    def test_skips_when_logs_db_not_configured(self, manager):
        """LOGS_DATABASE_URL 未配置时直接返回 0，不触库"""
        with patch("src.db.database.LOGS_DATABASE_URL", ""):
            assert manager._run_trace_retention_cleanup() == 0

    def test_skips_when_pool_not_initialized(self, manager):
        """连接池未初始化（init 失败降级）时直接返回 0"""
        with patch("src.db.database.LOGS_DATABASE_URL", "postgresql://x"), \
             patch("src.db.database._logs_connection_pool", None):
            assert manager._run_trace_retention_cleanup() == 0

    def test_single_batch_under_limit(self, manager):
        """过期 trace 数小于批大小时一轮完成，删 spans 后删 traces"""
        ctx = _make_logs_conn(
            fetch_batches=[[{"trace_id": "t1"}, {"trace_id": "t2"}]],
            delete_rowcounts=[12, 2],
        )
        with patch("src.db.database.LOGS_DATABASE_URL", "postgresql://x"), \
             patch("src.db.database._logs_connection_pool", MagicMock()), \
             patch("src.db.database.get_logs_connection", return_value=ctx):
            # 2 个 trace + 12 个 span + 0 个 score
            assert manager._run_trace_retention_cleanup() == 14
        assert ctx.__enter__.return_value.commit.call_count == 2

    def test_multiple_batches_until_exhausted(self, manager):
        """批满继续下一轮，直到 SELECT 返回不足一批"""
        ctx = _make_logs_conn(
            fetch_batches=[
                [{"trace_id": f"t{i}"} for i in range(1000)],
                [{"trace_id": "t-last"}],
            ],
            delete_rowcounts=[6000, 1000, 5, 1, 6],
        )
        with patch("src.db.database.LOGS_DATABASE_URL", "postgresql://x"), \
             patch("src.db.database._logs_connection_pool", MagicMock()), \
             patch("src.db.database.get_logs_connection", return_value=ctx):
            # 第一批 1000 trace + 6000 span，第二批 1 trace + 5 span；
            # 第二批不足批大小停止；score 循环删 6
            assert manager._run_trace_retention_cleanup() == 7012

    def test_no_expired_rows(self, manager):
        """无过期数据时一轮空 SELECT 即退出，返回 0"""
        ctx = _make_logs_conn(fetch_batches=[[]], delete_rowcounts=[0])
        with patch("src.db.database.LOGS_DATABASE_URL", "postgresql://x"), \
             patch("src.db.database._logs_connection_pool", MagicMock()), \
             patch("src.db.database.get_logs_connection", return_value=ctx):
            assert manager._run_trace_retention_cleanup() == 0

    def test_exception_returns_partial_deleted(self, manager):
        """中途异常返回已删行数，不向上抛"""
        conn = MagicMock()
        cursor = conn.cursor.return_value
        calls = {"n": 0}

        def _execute(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 4:
                raise RuntimeError("boom")
            sql = ((args[0] if args else "") or "").lstrip().upper()
            cursor.rowcount = -1 if sql.startswith("SELECT") else 5

        cursor.execute.side_effect = _execute
        cursor.fetchall.side_effect = lambda: [{"trace_id": "t1"}]
        ctx = MagicMock()
        ctx.__enter__.return_value = cursor
        ctx.__exit__.return_value = False

        with patch("src.db.database.LOGS_DATABASE_URL", "postgresql://x"), \
             patch("src.db.database._logs_connection_pool", MagicMock()), \
             patch("src.db.database.get_logs_connection", return_value=ctx):
            # 第一批成功删 spans 5 + traces 5；obs_scores 删除语句（第 4 次 execute）异常
            assert manager._run_trace_retention_cleanup() == 10


class TestLogsDbVacuum:
    def test_vacuum_executes_on_all_tables(self, manager):
        """autocommit 连接依次 VACUUM 三张 obs 表，连接用后关闭归还"""
        conn = MagicMock()
        pool = MagicMock()
        pool.getconn.return_value = conn

        with patch("src.db.database.LOGS_DATABASE_URL", "postgresql://x"), \
             patch("src.db.database._logs_connection_pool", pool):
            assert manager._run_logs_db_vacuum() is True
        assert conn.autocommit is True
        executed = [c.args[0] for c in conn.cursor.return_value.execute.call_args_list]
        assert executed == [
            "SET statement_timeout = 0",
            "VACUUM (ANALYZE) obs_spans",
            "VACUUM (ANALYZE) obs_traces",
            "VACUUM (ANALYZE) obs_scores",
        ]
        pool.putconn.assert_called_once_with(conn, close=True)

    def test_skips_when_not_configured(self, manager):
        with patch("src.db.database.LOGS_DATABASE_URL", ""):
            assert manager._run_logs_db_vacuum() is False

    def test_exception_returns_false_and_releases_conn(self, manager):
        """VACUUM 异常返回 False，连接仍被关闭归还，不泄漏"""
        conn = MagicMock()
        conn.cursor.return_value.execute.side_effect = RuntimeError("boom")
        pool = MagicMock()
        pool.getconn.return_value = conn

        with patch("src.db.database.LOGS_DATABASE_URL", "postgresql://x"), \
             patch("src.db.database._logs_connection_pool", pool):
            assert manager._run_logs_db_vacuum() is False
        pool.putconn.assert_called_once_with(conn, close=True)
