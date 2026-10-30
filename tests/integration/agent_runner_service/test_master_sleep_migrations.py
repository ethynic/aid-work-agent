"""Existing master watermarks must not hide older feature-branch sleep DDL."""
from pathlib import Path
import uuid

import pytest
from psycopg2 import sql

pytestmark = pytest.mark.integration
COUNTERS = ('total_token_count', 'prompt_tokens', 'completion_tokens', 'cached_input_tokens',
            'agent_iterations', 'duration_ms', 'embedding_tokens', 'asr_calls')
PATH = Path(__file__).resolve().parents[3] / 'deploy/db_update.yaml'
# This fixture has the latest master notification schema. Earlier watermarks
# need their actual old schemas, not a forged date on the current schema.
MASTER_WATERMARK = '2026-10-29 15:50:00'


def structures(database):
    return database.rows("""SELECT column_name,data_type FROM information_schema.columns
        WHERE table_schema='public' AND (table_name='chat_records' AND column_name=ANY(%s)
        OR table_name='agent_runners' AND column_name='wake_at') ORDER BY table_name,column_name""", (list(COUNTERS),)), database.rows("""
        SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public'
        AND indexname IN ('idx_agent_runners_wake','idx_chat_messages_session_id',
            'idx_lt_inv_wecom_inbound_pending') ORDER BY indexname""")


def test_master_watermark_upgrade_restores_sleep_schema_and_retains_usage(service_database):
    from src.db.database import _apply_db_updates, _load_db_update_blocks
    blocks = _load_db_update_blocks(PATH)
    expected = structures(service_database)
    record = 'master-sleep-upgrade-' + uuid.uuid4().hex
    try:
        with service_database.connect() as connection, connection.cursor() as cursor:
            # No real service runs in this module. Only this disposable DB is
            # downgraded to the schema that master already marked as current.
            cursor.execute('ALTER TABLE agent_runners DROP COLUMN wake_at')
            cursor.execute('DROP INDEX idx_chat_messages_session_id')
            cursor.execute('DROP INDEX idx_lt_inv_wecom_inbound_pending')
            cursor.execute(sql.SQL('ALTER TABLE chat_records ')+sql.SQL(', ').join(
                sql.SQL('ALTER COLUMN {} TYPE INTEGER').format(sql.Identifier(column)) for column in COUNTERS))
            cursor.execute('INSERT INTO chat_records(record_id,total_token_count,credit_cost) VALUES(%s,123,7.25)', (record,))
            cursor.execute('ALTER TABLE _db_update_applied ADD COLUMN IF NOT EXISTS last_datetime TEXT')
            cursor.execute("""INSERT INTO _db_update_applied(id,file_hash,last_datetime) VALUES('db_update','',%s)
                ON CONFLICT(id) DO UPDATE SET last_datetime=EXCLUDED.last_datetime""", (MASTER_WATERMARK,))
        assert structures(service_database) != expected
        with service_database.connect() as connection:
            _apply_db_updates(connection, PATH)
        assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime'] == blocks[-1]['datetime']
        assert structures(service_database) == expected
        from src.services.agent_runner.repository import RunnerRepository
        # Ordinary BOSS workers require this schema even with sleep disabled.
        RunnerRepository(service_database.connect).assert_schema()
        # Prove both ordinary restart and forced logical reapplication are safe.
        for force in (False, True):
            if force:
                # Reapply this feature's catch-up, not retired notification DDL
                # whose old seed columns were intentionally dropped by master.
                service_database.rows("UPDATE _db_update_applied SET last_datetime=%s WHERE id='db_update'",
                                      (MASTER_WATERMARK,))
            with service_database.connect() as connection:
                _apply_db_updates(connection, PATH)
            assert structures(service_database) == expected
            row = service_database.rows('SELECT total_token_count,credit_cost FROM chat_records WHERE record_id=%s', (record,))[0]
            assert row['total_token_count'] == 123 and str(row['credit_cost']) == '7.25'
        assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime'] == blocks[-1]['datetime']
        service_database.rows('UPDATE chat_records SET total_token_count=%s WHERE record_id=%s', (2**31+10, record))
        assert service_database.rows('SELECT total_token_count FROM chat_records WHERE record_id=%s', (record,))[0]['total_token_count'] == 2**31+10
    finally:
        service_database.rows('DELETE FROM chat_records WHERE record_id=%s', (record,))
