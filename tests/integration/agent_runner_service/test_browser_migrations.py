"""Real isolated Browser DDL equivalence; execute only after source handoff.

No test defines a shadow Browser schema. Both sides use production fresh init
and the actual incremental migration, while fixture deletion is scoped to its
three Browser audit/compatibility tables in the disposable database.
"""
from pathlib import Path

from psycopg2 import sql
import pytest

pytestmark = pytest.mark.integration
BROWSER_TABLES = (
    'bs_browser_runs', 'bs_browser_assistance_requests', 'bs_browser_resume_jobs',
)


def browser_schema(database):
    names = list(BROWSER_TABLES)
    return {
        'columns': database.rows('''SELECT table_name,column_name,data_type,is_nullable,column_default
            FROM information_schema.columns WHERE table_schema='public' AND table_name=ANY(%s)
            ORDER BY table_name,column_name''', (names,)),
        'constraints': database.rows('''SELECT c.relname AS table_name,pg_get_constraintdef(p.oid) AS definition
            FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid
            WHERE c.relname=ANY(%s) ORDER BY c.relname,definition''', (names,)),
        'indexes': database.rows('''SELECT tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname='public' AND tablename=ANY(%s) ORDER BY tablename,indexname''', (names,)),
    }


def browser_update():
    from src.db.database import _load_db_update_blocks
    path = Path(__file__).resolve().parents[3] / 'deploy' / 'db_update.yaml'
    blocks = _load_db_update_blocks(path)
    matches = [index for index, block in enumerate(blocks)
               if all(table in block['statements'] for table in BROWSER_TABLES)]
    assert len(matches) == 1
    index = matches[0]
    assert index > 0
    return path, blocks, index


def rewind_browser_update(cursor, blocks, index):
    # Fresh init has the historical tracker layout. The actual incremental
    # owner upgrades it before using its block watermark; mirror only this
    # legitimate fixture setup, not the Browser schema under test.
    cursor.execute('ALTER TABLE _db_update_applied ADD COLUMN IF NOT EXISTS last_datetime TEXT')
    cursor.execute('''INSERT INTO _db_update_applied(id,file_hash,last_datetime)
        VALUES ('db_update','',%s) ON CONFLICT(id)
        DO UPDATE SET last_datetime=EXCLUDED.last_datetime''', (blocks[index - 1]['datetime'],))


def test_browser_actual_incremental_fresh_and_replay_schemas_match(service_database):
    from src.db.database import _apply_db_updates
    path, blocks, index = browser_update()
    fresh = browser_schema(service_database)
    assert {row['table_name'] for row in fresh['columns']} == set(BROWSER_TABLES)
    with service_database.connect() as connection, connection.cursor() as cursor:
        for table in reversed(BROWSER_TABLES):
            cursor.execute(sql.SQL('DROP TABLE {}').format(sql.Identifier(table)))
        rewind_browser_update(cursor, blocks, index)
    with service_database.connect() as connection:
        _apply_db_updates(connection, path)
    assert browser_schema(service_database) == fresh
    # Exercise the watermark skip and then replay actual SQL, rather than
    # claiming that the skip branch alone proves migration idempotence.
    with service_database.connect() as connection:
        _apply_db_updates(connection, path)
    assert browser_schema(service_database) == fresh
    service_database.rows('''UPDATE _db_update_applied SET last_datetime=%s
        WHERE id='db_update' ''', (blocks[index - 1]['datetime'],))
    with service_database.connect() as connection:
        _apply_db_updates(connection, path)
    assert browser_schema(service_database) == fresh
    assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime'] == blocks[-1]['datetime']
