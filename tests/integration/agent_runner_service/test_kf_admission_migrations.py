"""Actual production Text DO through the original updater, no shadow schema."""
from pathlib import Path

import pytest

from .kf_ingress_fixtures import kf_scope, seed_intent, text_message

pytestmark = pytest.mark.integration
TABLES = ['agent_runner_inputs', 'wecom_kf_account_sync', 'wecom_kf_inbox']


def catalog(database):
    return {
        'columns': database.rows("SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%s) ORDER BY table_name,column_name", (TABLES,)),
        'checks': database.rows("SELECT c.relname,p.conname,pg_get_constraintdef(p.oid) AS definition,p.convalidated FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid WHERE c.relname=ANY(%s) ORDER BY c.relname,p.conname", (TABLES,)),
        'indexes': database.rows("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=ANY(%s) ORDER BY tablename,indexname", (TABLES,))}


def migration():
    from src.db.database import _load_db_update_blocks
    path = Path(__file__).resolve().parents[3] / 'deploy/db_update.yaml'
    blocks = _load_db_update_blocks(path)
    matches = [i for i, b in enumerate(blocks) if 'CREATE TABLE IF NOT EXISTS agent_runner_inputs' in b['statements']]
    assert len(matches) == 1 and matches[0] > 0
    return path, blocks, matches[0]


def rewind(cursor, blocks, index):
    cursor.execute('ALTER TABLE _db_update_applied ADD COLUMN IF NOT EXISTS last_datetime TEXT')
    cursor.execute("INSERT INTO _db_update_applied(id,file_hash,last_datetime) VALUES('db_update','',%s) ON CONFLICT(id) DO UPDATE SET last_datetime=EXCLUDED.last_datetime", (blocks[index - 1]['datetime'],))


def test_actual_text_fresh_schema_legacy_receipt_null_upgrade_force_and_replay_keep_original_null_user_sid(kf_scope):
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_auth import bounded_page
    from src.db.database import _apply_db_updates
    s = kf_scope
    path, blocks, index = migration()
    fresh = catalog(s.database)
    assert all(row['convalidated'] for row in fresh['checks'])
    repository = KfIngressRepository(s.database.connect)
    seed_intent(s, repository)
    lease, _ = repository.claim('text_legacy_migration_' + s.marker)
    try:
        repository.commit_page(lease, bounded_page({'errcode': 0, 'has_more': 0, 'next_cursor': 'legacy_text',
            'msg_list': [text_message(s, 'legacy_receipt_' + s.marker)]}, lease.proof))
    finally:
        repository.release(lease)
    old = s.rows('SELECT * FROM wecom_kf_inbox WHERE config_id=%s', (s.config_id,))[0]
    with s.database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('DROP TABLE agent_runner_inputs')
        cursor.execute('ALTER TABLE wecom_kf_account_sync DROP COLUMN inbox_seq CASCADE')
        for name in ('receive_seq', 'receipt_order', 'accepted_input_ref'):
            # Identifiers are fixed fixture constants, never provider text.
            cursor.execute('ALTER TABLE wecom_kf_inbox DROP COLUMN ' + name + ' CASCADE')
        rewind(cursor, blocks, index)
    try:
        with s.database.connect() as connection:
            _apply_db_updates(connection, path)
        upgraded = s.rows('SELECT * FROM wecom_kf_inbox WHERE config_id=%s', (s.config_id,))[0]
        new_fields = {'receive_seq', 'receipt_order', 'accepted_input_ref'}
        assert {k: v for k, v in upgraded.items() if k not in new_fields} == {k: v for k, v in old.items() if k not in new_fields}
        assert upgraded['receive_seq'] is None and upgraded['accepted_input_ref'] is None and upgraded['receipt_order'] > 0
        assert s.rows('SELECT user_id FROM channel_sessions WHERE session_id=%s', (s.legacy_sid,)) == [{'user_id': None}]
        assert catalog(s.database) == fresh
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
        # Never leave a structurally damaged shared isolated fixture after an
        # assertion; original migration is the only creator, not test DDL.
        with s.database.connect() as connection, connection.cursor() as cursor:
            rewind(cursor, blocks, index)
        with s.database.connect() as connection:
            _apply_db_updates(connection, path)


def test_actual_wrong_source_unique_index_rolls_back_whole_text_do_and_does_not_advance_watermark(service_database):
    from src.db.database import _apply_db_updates
    path, blocks, index = migration()
    fresh = catalog(service_database)
    original = service_database.rows("SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname='uq_runner_input_source'")[0]['indexdef']
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('DROP INDEX uq_runner_input_source')
        cursor.execute('CREATE INDEX uq_runner_input_source ON agent_runner_inputs(source_key)')
        cursor.execute('ALTER TABLE wecom_kf_inbox DROP COLUMN accepted_input_ref CASCADE')
        rewind(cursor, blocks, index)
    incompatible = catalog(service_database)
    try:
        with service_database.connect() as connection:
            _apply_db_updates(connection, path)
        assert catalog(service_database) == incompatible
        assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime'] == blocks[index - 1]['datetime']
        assert service_database.rows("SELECT 1 FROM information_schema.columns WHERE table_name='wecom_kf_inbox' AND column_name='accepted_input_ref'") == []
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('DROP INDEX uq_runner_input_source')
            cursor.execute(original)
            rewind(cursor, blocks, index)
        with service_database.connect() as connection:
            _apply_db_updates(connection, path)
        assert catalog(service_database) == fresh
