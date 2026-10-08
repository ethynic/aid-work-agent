"""Actual fresh/incremental/forced-replay event DDL, no shadow table schema."""
from pathlib import Path

import pytest
from psycopg2 import sql

from .test_storage import storage

pytestmark = pytest.mark.integration


def event_update():
    from src.db.database import _load_db_update_blocks
    path = Path(__file__).resolve().parents[3] / 'deploy' / 'db_update.yaml'
    blocks = _load_db_update_blocks(path)
    selected = [index for index, block in enumerate(blocks)
        if 'CREATE TABLE IF NOT EXISTS agent_runner_events' in block['statements']]
    assert len(selected) == 1 and selected[0] == len(blocks) - 1
    return path, blocks, selected[0]


def rewind(cursor, blocks, index):
    cursor.execute('ALTER TABLE _db_update_applied ADD COLUMN IF NOT EXISTS last_datetime TEXT')
    cursor.execute('''INSERT INTO _db_update_applied(id,file_hash,last_datetime)
        VALUES ('db_update','',%s) ON CONFLICT(id) DO UPDATE SET last_datetime=EXCLUDED.last_datetime''',
        (blocks[index - 1]['datetime'],))


def schema(database):
    return {
        'columns': database.rows('''SELECT table_name,column_name,data_type,is_nullable,column_default
            FROM information_schema.columns WHERE table_schema='public' AND
            (table_name='agent_runner_events' OR table_name='agent_runners' AND column_name IN ('event_seq','event_floor_seq'))
            ORDER BY table_name,column_name'''),
        'constraints': database.rows('''SELECT c.relname AS table_name,p.conname,p.convalidated,pg_get_constraintdef(p.oid) AS definition
            FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid
            WHERE c.relname='agent_runner_events' OR c.relname='agent_runners' AND p.conname='ck_runner_event_watermarks'
            ORDER BY table_name,p.conname'''),
        'indexes': database.rows("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='agent_runner_events' ORDER BY indexname"),
    }


def apply(database, path):
    from src.db.database import _apply_db_updates
    with database.connect() as connection:
        _apply_db_updates(connection, path)


def test_actual_fresh_incremental_forced_replay_preserves_legacy_zero_head_and_no_backfill(
        storage, actors, service_database):
    repository, _, submit = storage
    path, blocks, index = event_update()
    fresh = schema(service_database)
    row, principal = submit(actors['a'])
    assert row['event_seq'] == 1
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('DROP TABLE agent_runner_events')
        cursor.execute('ALTER TABLE agent_runners DROP CONSTRAINT ck_runner_event_watermarks')
        cursor.execute('ALTER TABLE agent_runners DROP COLUMN event_seq, DROP COLUMN event_floor_seq')
        rewind(cursor, blocks, index)
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s', (row['runner_id'],))
        legacy = dict(cursor.fetchone())
    apply(service_database, path)
    assert schema(service_database) == fresh
    upgraded = repository.get(row['runner_id'])
    assert upgraded['event_seq'] == upgraded['event_floor_seq'] == 0
    assert {key: upgraded[key] for key in legacy} == legacy
    assert service_database.rows('SELECT 1 FROM agent_runner_events') == []
    # Actual watermark skip and forced original SQL replay both retain zero
    # historical baseline; neither produces synthetic accepted events.
    apply(service_database, path)
    with service_database.connect() as connection, connection.cursor() as cursor:
        rewind(cursor, blocks, index)
    apply(service_database, path)
    assert schema(service_database) == fresh
    assert repository.get(row['runner_id']) == upgraded
    assert service_database.rows('SELECT 1 FROM agent_runner_events') == []
    assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'") == [
        {'last_datetime': blocks[index]['datetime']}]
    assert service_database.rows("SELECT COUNT(*) AS count FROM pg_class WHERE relname LIKE 'runner_event%%reference'") == [{'count': 0}]


def test_actual_catalog_rejection_rolls_whole_do_back_and_does_not_advance_watermark(
        storage, actors, service_database):
    repository, _, submit = storage
    path, blocks, index = event_update()
    row, _ = submit(actors['a'])
    original_shape = service_database.rows('''SELECT pg_get_constraintdef(oid) AS definition
        FROM pg_constraint WHERE conrelid='agent_runner_events'::regclass
        AND conname='ck_runner_event_shape' ''')[0]['definition']
    # Alter the actual prior production relation, not a test-designed shadow.
    # Missing head columns would otherwise be added before payload validation.
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('ALTER TABLE agent_runner_events DROP CONSTRAINT ck_runner_event_shape')
        cursor.execute('ALTER TABLE agent_runner_events ALTER COLUMN payload TYPE TEXT USING payload::text')
        cursor.execute('ALTER TABLE agent_runner_events ADD CONSTRAINT ck_runner_event_shape CHECK (TRUE)')
        cursor.execute('ALTER TABLE agent_runners DROP CONSTRAINT ck_runner_event_watermarks')
        cursor.execute('ALTER TABLE agent_runners DROP COLUMN event_seq, DROP COLUMN event_floor_seq')
        rewind(cursor, blocks, index)
    before = schema(service_database)
    roots = service_database.rows('SELECT * FROM agent_runners WHERE runner_id=%s', (row['runner_id'],))
    events = service_database.rows('SELECT * FROM agent_runner_events WHERE runner_id=%s', (row['runner_id'],))
    try:
        apply(service_database, path)  # Original runner logs/savepoints; does not raise Python error.
        assert schema(service_database) == before
        assert service_database.rows('SELECT * FROM agent_runners WHERE runner_id=%s', (row['runner_id'],)) == roots
        assert service_database.rows('SELECT * FROM agent_runner_events WHERE runner_id=%s', (row['runner_id'],)) == events
        assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'") == [
            {'last_datetime': blocks[index - 1]['datetime']}]
        assert service_database.rows("SELECT COUNT(*) AS count FROM pg_class WHERE relname LIKE 'runner_event%%reference'") == [{'count': 0}]
    finally:
        # Restore only the exact original relation through the original DDL,
        # even if an oracle fails; never poison the following isolated shape.
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('ALTER TABLE agent_runner_events DROP CONSTRAINT IF EXISTS ck_runner_event_shape')
            cursor.execute('ALTER TABLE agent_runner_events ALTER COLUMN payload TYPE JSONB USING payload::jsonb')
            cursor.execute(sql.SQL('ALTER TABLE agent_runner_events ADD CONSTRAINT ck_runner_event_shape {}')
                .format(sql.SQL(original_shape)))
        apply(service_database, path)
    assert repository.get(row['runner_id'])['event_seq'] == 0
