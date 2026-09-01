"""src.db.database._apply_db_updates 单元测试：YAML 校验 + datetime 增量执行 + 锁超时重试"""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from src.db.database import (
    _apply_db_updates,
    _is_lock_timeout_error,
    _load_db_update_blocks,
    _split_sql_statements,
)

TEST_YAML = """\
- datetime: "2026-09-01 10:00:00"
  remark: 块1
  statements: |
    ALTER TABLE tenants ADD COLUMN IF NOT EXISTS logo_file_id TEXT;
    CREATE TABLE IF NOT EXISTS foo_bar (id SERIAL PRIMARY KEY);
- datetime: "2026-09-01 11:00:00"
  remark: 块2
  statements: |
    ALTER TABLE sessions ADD COLUMN IF NOT EXISTS title TEXT;
"""


class FakeCursor:
    """模拟 psycopg2 cursor，按 SQL 内容区分元数据查询与数据语句。"""

    def __init__(
        self,
        last_datetime=None,
        fail_sql_substr=None,
        fail_times=0,
        error="lock_timeout",
        lock_available=True,
    ):
        self.calls = []
        self._last_sql = None
        self._last_datetime = last_datetime
        self._data_counts = {}
        self._fail_sql_substr = fail_sql_substr
        self._fail_times = fail_times
        self._error = error
        self._lock_available = lock_available
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
            return {"column_name": "last_datetime"}
        if "SELECT last_datetime FROM _db_update_applied" in sql:
            if self._last_datetime is None:
                return None
            return {"last_datetime": self._last_datetime}
        if "pg_try_advisory_lock" in sql:
            return {"locked": self._lock_available}
        if "pg_advisory_unlock" in sql:
            return {"unlocked": True}
        return None

    def _classify(self, sql):
        if sql.startswith("SAVEPOINT") or sql.startswith("ROLLBACK"):
            return "SAVEPOINT"
        meta_marks = (
            "information_schema",
            "last_datetime",
            "_db_update_applied",
            "pg_try_advisory_lock",
            "pg_advisory_unlock",
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


def _write_yaml(tmp_path, yaml_text):
    p = tmp_path / "db_update.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    return p


def _run(cursor, tmp_path, yaml_text=TEST_YAML, update_file=None):
    """执行 _apply_db_updates，mock 掉 time.sleep，返回 (conn, sleep_mock)。"""
    if update_file is None:
        update_file = _write_yaml(tmp_path, yaml_text)
    conn = FakeConn(cursor)
    with ExitStack() as stack:
        sleep_mock = stack.enter_context(patch("time.sleep"))
        _apply_db_updates(conn, update_file=update_file)
    return conn, sleep_mock


def _last_datetime_upserted(cursor):
    return any(
        "INSERT INTO _db_update_applied (id, file_hash, last_datetime)" in c[0]
        for c in cursor.calls
    )


def _data_exec_count(cursor, substr):
    return sum(1 for sql, _ in cursor.calls if sql.startswith(substr))


class TestApplyDbUpdates:
    def test_all_success_updates_last_datetime(self, tmp_path):
        cursor = FakeCursor()
        conn, _ = _run(cursor, tmp_path)
        # 首次启动（last_datetime 为空）：两块全部执行
        assert _data_exec_count(cursor, "ALTER TABLE tenants") == 1
        assert _data_exec_count(cursor, "CREATE TABLE IF NOT EXISTS foo_bar") == 1
        assert _data_exec_count(cursor, "ALTER TABLE sessions") == 1
        # 全成功：记录 last_datetime = 最大 datetime
        assert _last_datetime_upserted(conn._cursor)

    def test_incremental_skip_applied_blocks(self, tmp_path):
        cursor = FakeCursor(last_datetime="2026-09-01 10:30:00")
        conn, _ = _run(cursor, tmp_path)
        # last_datetime 之前的块跳过，只执行 11:00 的块2
        assert _data_exec_count(cursor, "ALTER TABLE tenants") == 0
        assert _data_exec_count(cursor, "CREATE TABLE IF NOT EXISTS foo_bar") == 0
        assert _data_exec_count(cursor, "ALTER TABLE sessions") == 1
        assert _last_datetime_upserted(conn._cursor)

    def test_no_pending_skip(self, tmp_path):
        cursor = FakeCursor(last_datetime="2026-09-01 12:00:00")
        conn, _ = _run(cursor, tmp_path)
        # 无新增块：不执行任何语句，也不更新记录
        assert _data_exec_count(cursor, "ALTER") == 0
        assert _data_exec_count(cursor, "CREATE") == 0
        assert not _last_datetime_upserted(conn._cursor)

    def test_missing_file_warning(self, tmp_path):
        cursor = FakeCursor()
        conn, _ = _run(cursor, tmp_path, update_file=tmp_path / "not_exist.yaml")
        assert conn._cursor.calls == []

    def test_no_lock_skip(self, tmp_path):
        cursor = FakeCursor(lock_available=False)
        conn, _ = _run(cursor, tmp_path)
        # 无法获取 advisory lock：跳过执行，不更新记录
        assert _data_exec_count(cursor, "ALTER") == 0
        assert _data_exec_count(cursor, "CREATE") == 0
        assert not _last_datetime_upserted(conn._cursor)

    def test_lock_timeout_retry_then_success(self, tmp_path):
        cursor = FakeCursor(
            fail_sql_substr="ALTER TABLE tenants", fail_times=1, error="lock_timeout"
        )
        conn, sleep_mock = _run(cursor, tmp_path)
        # 首失败 + 重试成功：ALTER 执行 2 次，其余 1 次
        assert _data_exec_count(cursor, "ALTER TABLE tenants") == 2
        assert _data_exec_count(cursor, "CREATE TABLE IF NOT EXISTS foo_bar") == 1
        # 第一次重试等 5s
        assert sleep_mock.call_args_list == [((5,), {})]
        # 重试成功 → 更新 last_datetime
        assert _last_datetime_upserted(conn._cursor)

    def test_lock_timeout_retry_exhausted_no_last_datetime(self, tmp_path):
        cursor = FakeCursor(
            fail_sql_substr="ALTER TABLE tenants", fail_times=99, error="lock_timeout"
        )
        conn, sleep_mock = _run(cursor, tmp_path)
        # 1 次执行 + 3 次重试均失败：ALTER 执行 4 次
        assert _data_exec_count(cursor, "ALTER TABLE tenants") == 4
        assert _data_exec_count(cursor, "CREATE TABLE IF NOT EXISTS foo_bar") == 1
        # 退避等待 5s / 10s / 15s
        assert sleep_mock.call_args_list == [((5,), {}), ((10,), {}), ((15,), {})]
        # 有失败 → 不更新 last_datetime（下次启动重试）
        assert not _last_datetime_upserted(conn._cursor)

    def test_non_lock_error_no_retry_no_last_datetime(self, tmp_path):
        cursor = FakeCursor(
            fail_sql_substr="ALTER TABLE tenants", fail_times=1, error="normal"
        )
        conn, sleep_mock = _run(cursor, tmp_path)
        # 非锁超时错误不重试：ALTER 执行 1 次
        assert _data_exec_count(cursor, "ALTER TABLE tenants") == 1
        assert _data_exec_count(cursor, "CREATE TABLE IF NOT EXISTS foo_bar") == 1
        assert sleep_mock.call_count == 0
        assert not _last_datetime_upserted(conn._cursor)


INVALID_YAML_CASES = [
    ("缺少 datetime 字段", "- remark: 无时间\n  statements: |\n    SELECT 1;\n"),
    ("datetime 格式错（缺时分秒）", '- datetime: "2026-09-01"\n  remark: x\n  statements: |\n    SELECT 1;\n'),
    ("datetime 格式错（分隔符歧义）", '- datetime: "2026/09/01 10:00:00"\n  remark: x\n  statements: |\n    SELECT 1;\n'),
    ("datetime 非合法时间", '- datetime: "2026-13-99 10:00:00"\n  remark: x\n  statements: |\n    SELECT 1;\n'),
    ("datetime 类型非法", "- datetime: 123\n  remark: x\n  statements: |\n    SELECT 1;\n"),
    ("datetime 重复", '- datetime: "2026-09-01 10:00:00"\n  remark: a\n  statements: |\n    SELECT 1;\n- datetime: "2026-09-01 10:00:00"\n  remark: b\n  statements: |\n    SELECT 2;\n'),
    ("datetime 倒序", '- datetime: "2026-09-01 11:00:00"\n  remark: a\n  statements: |\n    SELECT 1;\n- datetime: "2026-09-01 10:00:00"\n  remark: b\n  statements: |\n    SELECT 2;\n'),
    ("remark 为空", '- datetime: "2026-09-01 10:00:00"\n  remark: ""\n  statements: |\n    SELECT 1;\n'),
    ("statements 为空", '- datetime: "2026-09-01 10:00:00"\n  remark: x\n  statements: ""\n'),
    ("statements 非字符串", '- datetime: "2026-09-01 10:00:00"\n  remark: x\n  statements: 123\n'),
]


class TestValidationFailFast:
    @pytest.mark.parametrize("name, yaml_text", INVALID_YAML_CASES, ids=[c[0] for c in INVALID_YAML_CASES])
    def test_invalid_yaml_raises_and_executes_nothing(self, tmp_path, name, yaml_text):
        cursor = FakeCursor()
        with pytest.raises(ValueError):
            _run(cursor, tmp_path, yaml_text=yaml_text)
        # 校验失败必须 fail-fast：不执行任何 DATA 语句
        assert _data_exec_count(cursor, "ALTER") == 0
        assert _data_exec_count(cursor, "CREATE") == 0
        assert _data_exec_count(cursor, "SELECT") == 0

    def test_non_list_top_level_raises(self, tmp_path):
        cursor = FakeCursor()
        with pytest.raises(ValueError):
            _run(cursor, tmp_path, yaml_text="datetime: '2026-09-01 10:00:00'\n")


class TestSplitSqlStatements:
    def test_split_plain_and_comments(self):
        text = "-- 注释\nALTER TABLE a ADD COLUMN IF NOT EXISTS c TEXT;\nCREATE TABLE IF NOT EXISTS b (id SERIAL);\n"
        stmts = _split_sql_statements(text)
        assert len(stmts) == 2
        assert stmts[0].startswith("ALTER TABLE a")
        assert stmts[1].startswith("CREATE TABLE IF NOT EXISTS b")

    def test_split_dollar_quote_block(self):
        text = (
            "DO $$\n"
            "BEGIN\n"
            "  -- 块内注释\n"
            "  PERFORM 1;\n"
            "END $$;\n"
            "ALTER TABLE a ADD COLUMN IF NOT EXISTS c TEXT;\n"
        )
        stmts = _split_sql_statements(text)
        assert len(stmts) == 2
        assert stmts[0].startswith("DO $$")
        assert "PERFORM 1" in stmts[0]
        assert stmts[1].startswith("ALTER TABLE a")

    def test_last_statement_without_semicolon(self):
        stmts = _split_sql_statements("ALTER TABLE a ADD COLUMN IF NOT EXISTS c TEXT")
        assert len(stmts) == 1
        assert stmts[0].endswith(";")


class TestLoadDbUpdateBlocks:
    def test_accepts_datetime_object_without_quotes(self, tmp_path):
        # YAML 未加引号时 PyYAML 解析成 datetime 对象，应兼容归一为字符串
        p = _write_yaml(tmp_path, "- datetime: 2026-09-01 10:00:00\n  remark: 块\n  statements: |\n    SELECT 1;\n")
        blocks = _load_db_update_blocks(p)
        assert blocks[0]["datetime"] == "2026-09-01 10:00:00"

    def test_empty_file_returns_empty(self, tmp_path):
        p = _write_yaml(tmp_path, "")
        assert _load_db_update_blocks(p) == []
        p2 = _write_yaml(tmp_path, "[]\n")
        assert _load_db_update_blocks(p2) == []


class TestLockTimeoutDetection:
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
