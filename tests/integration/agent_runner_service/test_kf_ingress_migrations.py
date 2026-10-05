"""Prepared actual fresh/incremental KF migration, no shadow tables/schema."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
TABLES = ('wecom_kf_account_sync', 'channel_session_routes', 'wecom_kf_inbox')


def schema(database):
    return {
        'columns': database.rows("""SELECT table_name,column_name,data_type,is_nullable,column_default
            FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%s)
            ORDER BY table_name,column_name""", (list(TABLES),)),
        'constraints': database.rows("""SELECT c.relname AS table_name,p.conname,
            pg_get_constraintdef(p.oid) AS definition,p.convalidated
            FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid
            WHERE c.relname=ANY(%s) ORDER BY c.relname,p.conname""", (list(TABLES),)),
        'indexes': database.rows("""SELECT tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname='public' AND tablename=ANY(%s) ORDER BY tablename,indexname""", (list(TABLES),)),
    }


def migration():
    from src.db.database import _load_db_update_blocks
    path = Path(__file__).resolve().parents[3] / 'deploy/db_update.yaml'
    blocks = _load_db_update_blocks(path)
    matching = [index for index, block in enumerate(blocks)
        if all(table in block['statements'] for table in TABLES)]
    assert len(matching) == 1 and matching[0] > 0
    return path, blocks, matching[0]


def rewind(cursor, blocks, index):
    # Actual updater adds this watermark column to original fresh tracker.
    cursor.execute('ALTER TABLE _db_update_applied ADD COLUMN IF NOT EXISTS last_datetime TEXT')
    cursor.execute("""INSERT INTO _db_update_applied(id,file_hash,last_datetime)
        VALUES('db_update','',%s) ON CONFLICT(id) DO UPDATE SET last_datetime=EXCLUDED.last_datetime""",
        (blocks[index - 1]['datetime'],))


def test_actual_kf_fresh_incremental_force_and_replay_schema_match(service_database):
    from src.db.database import _apply_db_updates
    from psycopg2 import sql
    path, blocks, index = migration()
    fresh = schema(service_database)
    assert {row['table_name'] for row in fresh['columns']} == set(TABLES)
    assert all(row['convalidated'] for row in fresh['constraints'])
    # Only the three production tables in this isolated database are removed;
    # actual production DO creates them again, no test-defined table shape.
    with service_database.connect() as connection, connection.cursor() as cursor:
        for table in reversed(TABLES):
            cursor.execute(sql.SQL('DROP TABLE {}').format(sql.Identifier(table)))
        rewind(cursor, blocks, index)
    with service_database.connect() as connection:
        _apply_db_updates(connection, path)
    assert schema(service_database) == fresh
    with service_database.connect() as connection:
        _apply_db_updates(connection, path)  # Watermark skip.
    assert schema(service_database) == fresh
    with service_database.connect() as connection, connection.cursor() as cursor:
        rewind(cursor, blocks, index)
    with service_database.connect() as connection:
        _apply_db_updates(connection, path)  # Force original block replay.
    assert schema(service_database) == fresh
    assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime'] == blocks[-1]['datetime']


def test_actual_migration_wrong_scope_index_cannot_partially_create_missing_route_or_advance_watermark(service_database):
    from src.db.database import _apply_db_updates
    path, blocks, index = migration()
    fresh = schema(service_database)
    original_index = service_database.rows("SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname='uq_kf_account_scope'")[0]['indexdef']
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('DROP TABLE channel_session_routes')
        cursor.execute('DROP INDEX uq_kf_account_scope')
        cursor.execute('CREATE INDEX uq_kf_account_scope ON wecom_kf_account_sync(tenant_id,config_id,corp_id,open_kfid)')
        rewind(cursor, blocks, index)
    incompatible = schema(service_database)
    try:
        with service_database.connect() as connection:
            _apply_db_updates(connection, path)  # Original runner logs, no Python exception promised.
        assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime'] == blocks[index - 1]['datetime']
        assert schema(service_database) == incompatible
        assert service_database.rows("SELECT to_regclass('public.channel_session_routes') IS NULL AS missing")[0]['missing'] is True
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('DROP INDEX uq_kf_account_scope')
            cursor.execute(original_index)  # Restore exact pre-corruption pg catalog definition.
            rewind(cursor, blocks, index)
        with service_database.connect() as connection:
            _apply_db_updates(connection, path)
        assert schema(service_database) == fresh

