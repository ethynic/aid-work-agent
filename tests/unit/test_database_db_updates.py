"""src.db.database._apply_db_updates 单元测试：锁超时重试 + 失败不更新哈希"""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from src.db.database import _apply_db_updates, _is_lock_timeout_error

TEST_SQL = """\
-- 注释行
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS logo_file_id TEXT;
CREATE TABLE IF NOT EXISTS foo_bar (id SERIAL PRIMARY KEY);
"""


class FakeCursor:
    """模拟 psycopg2 cursor，按 SQL 内容区分元数据查询与数据语句。"""

    def __init__(self, fail_sql_substr=None, fail_times=0, error="lock_timeout"):
        self.calls = []
        self._last_sql = None
        self._data_counts = {}
        self._fail_sql_substr = fail_sql_substr
        self._fail_times = fail_times
        self._error = error
        self.rowcount = 0

    def execute(self, sql, *args):
        sql = sql if isinstance(sql, str) else str(sql)
        self.calls.append((sql, args))
        self._last_sql = sql
        if (
            self._classify(sql) == "DATA"
            and self._fail_sql_substr
            and self._fail_sql_substr in sql
        ):
            self._data_counts[sql] = self._data_counts.get(sql, 0) + 1
            if self._data_counts[sql] <= self._fail_times:
                raise self._make_error()

    def fetchone(self):
        sql = self._last_sql or ""
        if "information_schema.columns" in sql:
            return {"column_name": "file_hash"}
        if "SELECT file_hash FROM _db_update_applied" in sql:
            return None
        if "pg_try_advisory_lock" in sql:
            return {"locked": True}
        if "pg_advisory_unlock" in sql:
            return {"unlocked": True}
        return None

    def _classify(self, sql):
        if sql.startswith("SAVEPOINT") or sql.startswith("ROLLBACK"):
            return "SAVEPOINT"
        meta_marks = (
            "information_schema",
            "file_hash",
            "pg_try_advisory_lock",
            "pg_advisory_unlock",
            "_db_update_applied",
        )
        if any(m in sql for m in meta_marks):
            return "META"
        return "DATA"

    def _make_error(self):
        if self._error == "lock_timeout":
            err = Exception("canceling statement due to lock timeout")
            err.pgcode = "55P03"
            return err
        return Exception("syntax error near 'foo'")


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.rollback_calls = 0

    def cursor(self):
        return self._cursor

    def rollback(self):
        self.rollback_calls += 1


def _hash_upserted(cursor):
    return any("INSERT INTO _db_update_applied (id, file_hash)" in c[0] for c in cursor.calls)


def _data_exec_count(cursor, substr):
    return sum(1 for sql, _ in cursor.calls if sql.startswith(substr))


def _run(cursor):
    """执行 _apply_db_updates，mock 掉文件读取与 time.sleep，返回 (conn, sleep_mock)。"""
    conn = FakeConn(cursor)
    with ExitStack() as stack:
        stack.enter_context(patch("pathlib.Path.read_text", return_value=TEST_SQL))
        stack.enter_context(patch.object(Path, "exists", return_value=True))
        sleep_mock = stack.enter_context(patch("time.sleep"))
        _apply_db_updates(conn)
    return conn, sleep_mock


class TestApplyDbUpdates:
    def test_all_success_updates_hash(self):
        cursor = FakeCursor()
        conn, _ = _run(cursor)
        assert _hash_upserted(conn._cursor)
        assert _data_exec_count(cursor, "ALTER TABLE tenants") == 1
        assert _data_exec_count(cursor, "CREATE TABLE IF NOT EXISTS foo_bar") == 1

    def test_lock_timeout_retry_then_success(self):
        cursor = FakeCursor(
            fail_sql_substr="ALTER TABLE tenants", fail_times=1, error="lock_timeout"
        )
        conn, sleep_mock = _run(cursor)
        # 首失败 + 重试成功：ALTER 执行 2 次，另一条 1 次
        assert _data_exec_count(cursor, "ALTER TABLE tenants") == 2
        assert _data_exec_count(cursor, "CREATE TABLE IF NOT EXISTS foo_bar") == 1
        # 第一次重试等 5s
        assert sleep_mock.call_args_list == [((5,), {})]
        # 重试成功 → 更新哈希
        assert _hash_upserted(conn._cursor)

    def test_lock_timeout_retry_exhausted_no_hash(self):
        cursor = FakeCursor(
            fail_sql_substr="ALTER TABLE tenants", fail_times=99, error="lock_timeout"
        )
        conn, sleep_mock = _run(cursor)
        # 1 次执行 + 3 次重试均失败：ALTER 执行 4 次，另一条成功
        assert _data_exec_count(cursor, "ALTER TABLE tenants") == 4
        assert _data_exec_count(cursor, "CREATE TABLE IF NOT EXISTS foo_bar") == 1
        # 退避等待 5s / 10s / 15s
        assert sleep_mock.call_args_list == [((5,), {}), ((10,), {}), ((15,), {})]
        # 有失败 → 不更新哈希（下次启动重试）
        assert not _hash_upserted(conn._cursor)

    def test_non_lock_error_no_retry_no_hash(self):
        cursor = FakeCursor(
            fail_sql_substr="ALTER TABLE tenants", fail_times=1, error="normal"
        )
        conn, sleep_mock = _run(cursor)
        # 非锁超时错误不重试：ALTER 执行 1 次
        assert _data_exec_count(cursor, "ALTER TABLE tenants") == 1
        assert _data_exec_count(cursor, "CREATE TABLE IF NOT EXISTS foo_bar") == 1
        assert sleep_mock.call_count == 0
        assert not _hash_upserted(conn._cursor)

    def test_is_lock_timeout_error_by_pgcode(self):
        err = Exception("canceling statement due to lock timeout")
        err.pgcode = "55P03"
        assert _is_lock_timeout_error(err) is True

    def test_is_lock_timeout_error_by_message(self):
        assert (
            _is_lock_timeout_error(Exception("canceling statement due to lock timeout"))
            is True
        )
        assert _is_lock_timeout_error(Exception("syntax error")) is False
