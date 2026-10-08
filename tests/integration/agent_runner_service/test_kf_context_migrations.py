"""Original dual-DDL Context upgrade/replay and semantic-key catalog rejection.

The swapped-key fixture preserves every original conkey attnum and physical
column type/default while changing their actual names. Final rejection proves
semantic validation; no claim of running this counterexample on old v1 is made.
"""
from pathlib import Path

import pytest

from .kf_ingress_fixtures import kf_scope, seed_intent, text_message

pytestmark=pytest.mark.integration
TABLES=('wecom_kf_receipt_classifications','wecom_kf_context_consumptions','wecom_kf_context_task_intents')


def migration():
    from src.db.database import _load_db_update_blocks
    path=Path(__file__).resolve().parents[3]/'deploy/db_update.yaml'
    blocks=_load_db_update_blocks(path)
    indices=[i for i,block in enumerate(blocks)
        if 'CREATE TABLE IF NOT EXISTS wecom_kf_context_task_intents' in block['statements']]
    assert len(indices)==1 and indices[0]>0
    return path,blocks,indices[0]


def rewind(cursor,blocks,index):
    cursor.execute('ALTER TABLE _db_update_applied ADD COLUMN IF NOT EXISTS last_datetime TEXT')
    cursor.execute("INSERT INTO _db_update_applied(id,file_hash,last_datetime) VALUES('db_update','',%s) "
        'ON CONFLICT(id) DO UPDATE SET last_datetime=EXCLUDED.last_datetime',
        (blocks[index-1]['datetime'],))


def catalog(database):
    return {table:{
        'columns':database.rows('SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns '
            "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(table,)),
        'constraints':database.rows('SELECT conname,contype,convalidated,conkey,pg_get_constraintdef(oid) AS definition '
            'FROM pg_constraint WHERE conrelid=%s::regclass ORDER BY conname',(table,)),
        'indexes':database.rows("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=%s ORDER BY indexname",(table,))
        } for table in TABLES}


def test_actual_context_fresh_legacy_null_sid_upgrade_and_force_replay_keep_original_receipts_exact(kf_scope):
    from src.db.database import _apply_db_updates
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_auth import bounded_page
    s=kf_scope
    path,blocks,index=migration()
    fresh=catalog(s.database)
    assert all(row['convalidated'] for shape in fresh.values() for row in shape['constraints'])
    repository=KfIngressRepository(s.database.connect)
    seed_intent(s,repository)
    lease,_=repository.claim('context_legacy_migration_'+s.marker)
    assert lease is not None
    try:
        repository.commit_page(lease,bounded_page({'errcode':0,'has_more':0,'next_cursor':'context_legacy_receipt',
            'msg_list':[text_message(s,'context_legacy_'+s.marker)]},lease.proof))
    finally:
        repository.release(lease)
    original={table:s.rows('SELECT * FROM '+table+' WHERE config_id=%s',(s.config_id,))
        for table in ('wecom_kf_inbox','wecom_kf_account_sync','channel_session_routes')}
    assert len(original['wecom_kf_inbox'])==1 and original['wecom_kf_inbox'][0]['accepted_input_ref'] is None
    original_session=s.rows('SELECT * FROM channel_sessions WHERE session_id=%s',(s.legacy_sid,))
    assert len(original_session)==1 and original_session[0]['user_id'] is None and original_session[0]['subagent_id']==''
    with s.database.connect() as conn,conn.cursor() as cursor:
        for table in reversed(TABLES):
            cursor.execute('DROP TABLE '+table)
        rewind(cursor,blocks,index)
    try:
        for mode in ('upgrade','nochange_replay','force_replay'):
            if mode=='force_replay':
                with s.database.connect() as conn,conn.cursor() as cursor:
                    rewind(cursor,blocks,index)
            with s.database.connect() as conn:
                _apply_db_updates(conn,path)
            assert catalog(s.database)==fresh
            assert all(s.rows('SELECT 1 FROM '+table)==[] for table in TABLES)
            for table,rows in original.items():
                assert s.rows('SELECT * FROM '+table+' WHERE config_id=%s',(s.config_id,))==rows
            assert s.rows('SELECT * FROM channel_sessions WHERE session_id=%s',(s.legacy_sid,))==original_session
        assert s.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime']==blocks[-1]['datetime']
    finally:
        with s.database.connect() as conn,conn.cursor() as cursor:
            rewind(cursor,blocks,index)
        with s.database.connect() as conn:
            _apply_db_updates(conn,path)
        assert catalog(s.database)==fresh


def test_actual_context_swapped_physical_text_keys_reject_whole_do_without_watermark_advance(service_database):
    from src.db.database import _apply_db_updates
    database=service_database
    path,blocks,index=migration()
    fresh=catalog(database)
    table='wecom_kf_context_task_intents'
    assert database.rows('SELECT 1 FROM '+table)==[]
    from psycopg2 import sql
    original_checks={row['conname']:row['definition'] for row in fresh[table]['constraints']
        if row['contype']=='c'}

    def restore_original_checks(cursor):
        # RENAME may rewrite CHECK column names. Restore the trusted original
        # catalog definitions so no CHECK mismatch masks this keys-only case.
        for name,definition in original_checks.items():
            cursor.execute(sql.SQL('ALTER TABLE {} DROP CONSTRAINT {}').format(
                sql.Identifier(table),sql.Identifier(name)))
            cursor.execute(sql.SQL('ALTER TABLE {} ADD CONSTRAINT {} {}').format(
                sql.Identifier(table),sql.Identifier(name),sql.SQL(definition)))

    with database.connect() as conn,conn.cursor() as cursor:
        # RENAME retains the original constraint attnums, unlike only replacing
        # PK (which could accidentally be caught by the unchanged unique key).
        cursor.execute('ALTER TABLE '+table+' RENAME COLUMN task_id TO own_swap_context_key')
        cursor.execute('ALTER TABLE '+table+' RENAME COLUMN account_id TO task_id')
        cursor.execute('ALTER TABLE '+table+' RENAME COLUMN own_swap_context_key TO account_id')
        restore_original_checks(cursor)
        rewind(cursor,blocks,index)
    invalid=catalog(database)
    assert {row['conname']:row['definition'] for row in invalid[table]['constraints']
        if row['contype']=='c'}==original_checks
    assert all(row['convalidated'] for row in invalid[table]['constraints'])
    original_keys=[row for row in fresh[table]['constraints'] if row['contype'] in ('p','u')]
    changed_keys=[row for row in invalid[table]['constraints'] if row['contype'] in ('p','u')]
    assert [row['conkey'] for row in changed_keys]==[row['conkey'] for row in original_keys]
    assert [row['definition'] for row in changed_keys]!=[row['definition'] for row in original_keys]
    assert next(row for row in changed_keys if row['contype']=='p')['definition']=='PRIMARY KEY (account_id)'
    assert 'UNIQUE (task_id, namespace, message_id, task_name, operation_version)' in [row['definition'] for row in changed_keys]
    before_watermark=database.rows("SELECT * FROM _db_update_applied WHERE id='db_update'")
    try:
        with database.connect() as conn:
            _apply_db_updates(conn,path)
            with conn.cursor() as cursor:
                cursor.execute("SELECT count(*) AS n FROM pg_class WHERE relnamespace=pg_my_temp_schema() "
                    "AND relname LIKE 'context_reference_%%'")
                assert cursor.fetchone()['n']==0
        assert catalog(database)==invalid
        assert database.rows("SELECT * FROM _db_update_applied WHERE id='db_update'")==before_watermark
        assert all(database.rows('SELECT 1 FROM '+name)==[] for name in TABLES)
    finally:
        with database.connect() as conn,conn.cursor() as cursor:
            cursor.execute('ALTER TABLE '+table+' RENAME COLUMN account_id TO own_swap_context_key')
            cursor.execute('ALTER TABLE '+table+' RENAME COLUMN task_id TO account_id')
            cursor.execute('ALTER TABLE '+table+' RENAME COLUMN own_swap_context_key TO task_id')
            restore_original_checks(cursor)
            rewind(cursor,blocks,index)
        with database.connect() as conn:
            _apply_db_updates(conn,path)
        assert catalog(database)==fresh
