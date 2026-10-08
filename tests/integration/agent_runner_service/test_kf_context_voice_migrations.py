"""Original paired updater: old Context CHECK upgrade, force replay and rollback.

Only this isolated database's catalog is temporarily changed. Existing original
receipt/routes and NULL-user/raw-empty SID must survive byte-for-value. These
are catalog associations, not physical ASR/Runtime producer evidence.
"""
from pathlib import Path
import pytest

from .kf_ingress_fixtures import kf_scope,seed_intent,text_message

pytestmark=pytest.mark.integration
TABLE='wecom_kf_context_voice_preparations'
CONSUMPTION='wecom_kf_context_consumptions'


def migration():
    from src.db.database import _load_db_update_blocks
    path=Path(__file__).resolve().parents[3]/'deploy/db_update.yaml'
    blocks=_load_db_update_blocks(path)
    indices=[i for i,b in enumerate(blocks) if 'CREATE TABLE IF NOT EXISTS '+TABLE+' (' in b['statements']]
    assert len(indices)==1 and indices[0]>0
    return path,blocks,indices[0]


def rewind(cursor,blocks,index):
    cursor.execute('ALTER TABLE _db_update_applied ADD COLUMN IF NOT EXISTS last_datetime TEXT')
    cursor.execute("INSERT INTO _db_update_applied(id,file_hash,last_datetime) VALUES('db_update','',%s) "
        'ON CONFLICT(id) DO UPDATE SET last_datetime=EXCLUDED.last_datetime',(blocks[index-1]['datetime'],))


def catalog(database):
    return {table:{'columns':database.rows('SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns '
        "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(table,)),
        'constraints':database.rows('SELECT conname,contype,convalidated,pg_get_constraintdef(oid) AS definition FROM pg_constraint '
            'WHERE conrelid=%s::regclass ORDER BY conname',(table,)),
        'indexes':database.rows("SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=%s ORDER BY indexname",(table,))}
        for table in (TABLE,CONSUMPTION)}


def old_check(block):
    # Extract the actual old reference CHECK supplied by the original upgrade
    # block, not a manually weakened/new constraint policy.
    source=block['statements'];start=source.index('CREATE TEMP TABLE context_voice_old_consumption')
    first=source.index('CHECK (',start);opening=source.index('(',first);depth=0
    for end in range(opening,len(source)):
        depth+=int(source[end]=='(')-int(source[end]==')')
        if depth==0:
            value=source[first:end+1]
            assert "'pending_asr'" not in value and "'source_terminal'" in value
            return value
    raise AssertionError('ORIGINAL_OLD_CONTEXT_CHECK_NOT_FOUND')


def test_actual_context_voice_old_check_upgrade_force_replay_preserves_original_null_user_receipt_and_catalog(kf_scope):
    from src.db.database import _apply_db_updates
    from src.channels.wecom_kf.ingress_repository import KfIngressRepository
    from src.channels.wecom_kf.ingress_auth import bounded_page
    from psycopg2 import sql
    s=kf_scope;path,blocks,index=migration();fresh=catalog(s.database)
    assert all(c['convalidated'] for shape in fresh.values() for c in shape['constraints'])
    repo=KfIngressRepository(s.database.connect);seed_intent(s,repo)
    lease,_=repo.claim('context_voice_upgrade_'+s.marker);assert lease is not None
    message=text_message(s,'old_context_voice_media_'+s.marker);message.pop('text')
    message['msgtype']='voice';message['voice']={'media_id':'fictional_old_context_media'}
    try:
        assert repo.commit_page(lease,bounded_page({'errcode':0,'has_more':0,'next_cursor':'old_context_voice_cursor','msg_list':[message]},lease.proof))=={'received':1,'has_more':False}
    finally: repo.release(lease)
    before={table:s.rows('SELECT * FROM '+table+' WHERE config_id=%s',(s.config_id,))
        for table in ('wecom_kf_inbox','wecom_kf_account_sync','channel_session_routes')}
    session=s.rows('SELECT * FROM channel_sessions WHERE session_id=%s',(s.legacy_sid,))
    assert len(session)==1 and session[0]['user_id'] is None and session[0]['subagent_id']==''
    assert s.rows('SELECT 1 FROM '+TABLE)==[]
    with s.database.connect() as conn,conn.cursor() as cursor:
        cursor.execute('DROP TABLE '+TABLE)
        cursor.execute('ALTER TABLE '+CONSUMPTION+' DROP CONSTRAINT ck_kf_context_consumption_shape')
        cursor.execute(sql.SQL('ALTER TABLE {} ADD CONSTRAINT ck_kf_context_consumption_shape {}').format(
            sql.Identifier(CONSUMPTION),sql.SQL(old_check(blocks[index]))))
        rewind(cursor,blocks,index)
    try:
        for mode in ('upgrade','nochange','force'):
            if mode=='force':
                with s.database.connect() as conn,conn.cursor() as cursor: rewind(cursor,blocks,index)
            with s.database.connect() as conn: _apply_db_updates(conn,path)
            assert catalog(s.database)==fresh
            assert s.rows('SELECT 1 FROM '+TABLE)==[]
            for table,rows in before.items(): assert s.rows('SELECT * FROM '+table+' WHERE config_id=%s',(s.config_id,))==rows
            assert s.rows('SELECT * FROM channel_sessions WHERE session_id=%s',(s.legacy_sid,))==session
        assert s.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime']==blocks[-1]['datetime']
    finally:
        with s.database.connect() as conn,conn.cursor() as cursor: rewind(cursor,blocks,index)
        with s.database.connect() as conn: _apply_db_updates(conn,path)
        assert catalog(s.database)==fresh


def test_actual_context_voice_wrong_started_timestamp_rejects_atomic_upgrade_and_watermark(service_database):
    from src.db.database import _apply_db_updates
    database=service_database;path,blocks,index=migration();fresh=catalog(database)
    assert database.rows('SELECT 1 FROM '+TABLE)==[]
    with database.connect() as conn,conn.cursor() as cursor:
        cursor.execute('ALTER TABLE '+TABLE+" ALTER COLUMN started_at TYPE TIMESTAMP USING started_at AT TIME ZONE 'UTC'")
        rewind(cursor,blocks,index)
    invalid=catalog(database);watermark=database.rows("SELECT * FROM _db_update_applied WHERE id='db_update'")
    try:
        with database.connect() as conn:
            _apply_db_updates(conn,path)
            with conn.cursor() as cursor:
                cursor.execute("SELECT count(*) AS n FROM pg_class WHERE relnamespace=pg_my_temp_schema() AND relname LIKE 'context_voice_%%'")
                assert cursor.fetchone()['n']==0
        assert catalog(database)==invalid
        assert database.rows("SELECT * FROM _db_update_applied WHERE id='db_update'")==watermark
        assert database.rows('SELECT 1 FROM '+TABLE)==[]
    finally:
        with database.connect() as conn,conn.cursor() as cursor:
            cursor.execute('ALTER TABLE '+TABLE+" ALTER COLUMN started_at TYPE TIMESTAMPTZ USING started_at AT TIME ZONE 'UTC'")
            rewind(cursor,blocks,index)
        with database.connect() as conn: _apply_db_updates(conn,path)
        assert catalog(database)==fresh
