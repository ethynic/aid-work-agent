"""退役只移除 gate；真实更新器使用假 SQL 连接验证历史保留和增量执行。"""

from pathlib import Path
from unittest.mock import patch

import pytest

from src.db.database import _apply_db_updates, _load_db_update_blocks, _split_sql_statements
from tests.unit.test_database_db_updates import FakeConn, FakeCursor


ROOT = Path(__file__).resolve().parents[2]
UPDATE_FILE = ROOT / 'deploy' / 'db_update.yaml'
RETIREMENT_TIME = '2026-10-08 15:21:10'
RETIREMENT_SQL = 'ALTER TABLE IF EXISTS agent_runner_session_claims DROP COLUMN IF EXISTS gate;'


class WatermarkCursor(FakeCursor):
    """持久化时间戳端口，第二次调用必须读取第一次执行后实际写入的值。"""

    def execute(self, sql, *args):
        super().execute(sql, *args)
        if 'INSERT INTO _db_update_applied (id, file_hash, last_datetime)' in sql:
            self._last_datetime = args[0][0]


@pytest.fixture
def blocks():
    return _load_db_update_blocks(UPDATE_FILE)


def run_updates(cursor):
    with patch('time.sleep'):
        _apply_db_updates(FakeConn(cursor), update_file=UPDATE_FILE)


def statements(blocks):
    return [sql for block in blocks for sql in _split_sql_statements(block['statements'])]


def data_calls(cursor, expected):
    allowed = set(expected)
    return [sql for sql, args in cursor.calls if sql in allowed]


def test_retirement_is_one_later_block_that_preserves_old_native_schema_and_data(blocks):
    assert blocks[-1]['datetime'] == RETIREMENT_TIME
    assert blocks[-2]['datetime'] < RETIREMENT_TIME
    assert statements(blocks[-1:]) == [RETIREMENT_SQL]
    historical = '\n'.join(block['statements'] for block in blocks[:-1])
    for table in ('wecom_kf_inbox', 'wecom_kf_deliveries', 'wecom_kf_wire_operations', 'agent_runner_inputs'):
        assert table in historical, 'Retirement must not erase already shipped migration history'
    assert 'DROP TABLE' not in blocks[-1]['statements'].upper()
    assert 'CASCADE' not in blocks[-1]['statements'].upper()


def test_existing_database_executes_only_retirement_and_second_start_executes_no_ddl(blocks):
    cursor = WatermarkCursor(last_datetime=blocks[-2]['datetime'])
    expected = statements(blocks)
    run_updates(cursor)
    assert data_calls(cursor, expected) == [RETIREMENT_SQL]
    assert cursor._last_datetime == RETIREMENT_TIME
    cursor.calls.clear()
    run_updates(cursor)
    assert data_calls(cursor, expected) == []
    assert not any('INSERT INTO _db_update_applied' in sql for sql, args in cursor.calls)


def test_empty_watermark_runs_every_historical_statement_before_retirement(blocks):
    cursor = WatermarkCursor()
    expected = statements(blocks)
    run_updates(cursor)
    assert data_calls(cursor, expected) == expected
    assert expected[-1] == RETIREMENT_SQL and expected.count(RETIREMENT_SQL) == 1
    assert cursor._last_datetime == RETIREMENT_TIME


def test_fresh_schema_keeps_old_tables_but_finishes_with_same_gate_retirement():
    text = (ROOT / 'deploy' / 'init-postgres.sql').read_text(encoding='utf-8')
    ddl = _split_sql_statements(text)
    assert ddl[-1] == RETIREMENT_SQL
    assert any('CREATE TABLE' in sql and 'wecom_kf_deliveries' in sql for sql in ddl[:-1])
    assert any('CREATE TABLE' in sql and 'wecom_kf_inbox' in sql for sql in ddl[:-1])
    assert any('agent_runner_session_claims' in sql and 'gate' in sql for sql in ddl[:-1])


def test_failed_gate_retirement_does_not_advance_watermark_or_drop_native_tables(blocks):
    cursor = WatermarkCursor(last_datetime=blocks[-2]['datetime'],
        fail_sql_substr=RETIREMENT_SQL, fail_times=99, error='lock_timeout')
    run_updates(cursor)
    assert cursor._last_datetime == blocks[-2]['datetime']
    assert data_calls(cursor, statements(blocks)) == [RETIREMENT_SQL] * 4
    assert not any('INSERT INTO _db_update_applied' in sql for sql, args in cursor.calls)
    assert not any('DROP TABLE' in sql.upper() for sql, args in cursor.calls)


def test_other_worker_completed_retirement_is_rechecked_under_migration_lock(blocks):
    class ConcurrentCursor(WatermarkCursor):
        watermark_reads = 0

        def fetchone(self):
            if 'SELECT last_datetime FROM _db_update_applied' in (self._last_sql or ''):
                self.watermark_reads += 1
                if self.watermark_reads == 2:
                    self._last_datetime = RETIREMENT_TIME
            return super().fetchone()

    cursor = ConcurrentCursor(last_datetime=blocks[-2]['datetime'])
    run_updates(cursor)
    assert cursor.watermark_reads == 2
    assert data_calls(cursor, statements(blocks)) == []
    assert any('pg_advisory_unlock' in sql for sql, args in cursor.calls)
