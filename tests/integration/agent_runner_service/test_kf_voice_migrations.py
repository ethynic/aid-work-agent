"""Prepared original Voice DO fresh/replay and incompatible catalog rollback.

These are actual updater/PG contracts; no shadow production table or runtime
producer is claimed. Existing inbox and NULL-user legacy SID remain intact.
"""
from pathlib import Path

import pytest

from .kf_ingress_fixtures import kf_scope, seed_intent, text_message

pytestmark = pytest.mark.integration
TABLE = 'wecom_kf_input_preparations'


def catalog(database):
    return {
        'columns': database.rows("SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (TABLE,)),
        'constraints': database.rows("SELECT conname,contype,convalidated,pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid=%s::regclass ORDER BY conname", (TABLE,)),
        'indexes': database.rows("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=%s ORDER BY indexname", (TABLE,))}


def migration():
    from src.db.database import _load_db_update_blocks
    path = Path(__file__).resolve().parents[3] / 'deploy/db_update.yaml'
    blocks = _load_db_update_blocks(path)
    indexes = [i for i, block in enumerate(blocks)
        if 'CREATE TABLE IF NOT EXISTS wecom_kf_input_preparations' in block['statements']]
    assert len(indexes) == 1 and indexes[0] > 0
    return path, blocks, indexes[0]


def rewind(cursor, blocks, index):
    cursor.execute('ALTER TABLE _db_update_applied ADD COLUMN IF NOT EXISTS last_datetime TEXT')
    cursor.execute("INSERT INTO _db_update_applied(id,file_hash,last_datetime) VALUES('db_update','',%s) ON CONFLICT(id) DO UPDATE SET last_datetime=EXCLUDED.last_datetime",
        (blocks[index - 1]['datetime'],))


def test_actual_voice_fresh_upgrade_force_replay_catalog_and_existing_null_user_media_only_inbox_remain_exact(kf_scope):
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_auth import bounded_page
    from src.db.database import _apply_db_updates
    s = kf_scope
    path, blocks, index = migration()
    fresh = catalog(s.database)
    assert all(item['convalidated'] for item in fresh['constraints'])
    assert s.rows('SELECT 1 FROM wecom_kf_input_preparations') == []
    repository = KfIngressRepository(s.database.connect)
    seed_intent(s, repository)
    lease, _ = repository.claim('voice_legacy_migration_' + s.marker)
    original = text_message(s, 'legacy_media_only_' + s.marker)
    original.pop('text')
    original['msgtype'], original['voice'] = 'voice', {'media_id': 'fictional_legacy_media'}
    try:
        assert repository.commit_page(lease, bounded_page({'errcode': 0, 'has_more': 0,
            'next_cursor': 'legacy_voice_kept', 'msg_list': [original]}, lease.proof)) == {'received': 1, 'has_more': False}
    finally:
        repository.release(lease)
    prior_inbox = s.rows('SELECT * FROM wecom_kf_inbox WHERE config_id=%s', (s.config_id,))
    prior_route = s.rows('SELECT * FROM channel_session_routes WHERE config_id=%s', (s.config_id,))
    prior_account = s.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (s.config_id,))
    with s.database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('DROP TABLE wecom_kf_input_preparations')
        rewind(cursor, blocks, index)
    try:
        with s.database.connect() as connection:
            _apply_db_updates(connection, path)
        assert catalog(s.database) == fresh
        assert s.rows('SELECT * FROM wecom_kf_inbox WHERE config_id=%s', (s.config_id,)) == prior_inbox
        assert s.rows('SELECT * FROM channel_session_routes WHERE config_id=%s', (s.config_id,)) == prior_route
        assert s.rows('SELECT * FROM wecom_kf_account_sync WHERE config_id=%s', (s.config_id,)) == prior_account
        assert s.rows('SELECT user_id FROM channel_sessions WHERE session_id=%s', (s.legacy_sid,)) == [{'user_id': None}]
        assert s.rows('SELECT 1 FROM wecom_kf_input_preparations') == []
        with s.database.connect() as connection:
            _apply_db_updates(connection, path)
        assert catalog(s.database) == fresh
        with s.database.connect() as connection, connection.cursor() as cursor:
            rewind(cursor, blocks, index)
        with s.database.connect() as connection:
            _apply_db_updates(connection, path)
        assert catalog(s.database) == fresh
        assert s.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime'] == blocks[-1]['datetime']
    finally:
        with s.database.connect() as connection, connection.cursor() as cursor:
            rewind(cursor, blocks, index)
        with s.database.connect() as connection:
            _apply_db_updates(connection, path)


def test_actual_voice_wrong_timestamp_catalog_rejects_whole_do_without_watermark_or_partial_reference(service_database):
    from src.db.database import _apply_db_updates
    database = service_database
    path, blocks, index = migration()
    fresh = catalog(database)
    assert database.rows('SELECT 1 FROM wecom_kf_input_preparations') == []
    with database.connect() as connection, connection.cursor() as cursor:
        cursor.execute("ALTER TABLE wecom_kf_input_preparations ALTER COLUMN started_at TYPE TIMESTAMP USING started_at AT TIME ZONE 'UTC'")
        rewind(cursor, blocks, index)
    invalid = catalog(database)
    try:
        with database.connect() as connection:
            _apply_db_updates(connection, path)
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('pg_temp.voice_preparation_reference') AS reference")
                assert cursor.fetchone()['reference'] is None
        assert catalog(database) == invalid
        assert database.rows('SELECT 1 FROM wecom_kf_input_preparations') == []
        assert database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime'] == blocks[index - 1]['datetime']
    finally:
        with database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("ALTER TABLE wecom_kf_input_preparations ALTER COLUMN started_at TYPE TIMESTAMPTZ USING started_at AT TIME ZONE 'UTC'")
            rewind(cursor, blocks, index)
        with database.connect() as connection:
            _apply_db_updates(connection, path)
        assert catalog(database) == fresh
