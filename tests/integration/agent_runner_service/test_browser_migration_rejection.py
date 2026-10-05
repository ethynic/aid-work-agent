"""Incompatible existing catalogs must not be silently blessed by migration.

Every fixture starts from actual production fresh DDL, mutates one existing
catalog object and drops one new nullable field to expose partial-DDL commits.
It never creates a shadow table. Cleanup repairs only the fixture alteration
then reapplies the actual production block inside the disposable database.
"""
from psycopg2 import sql
import uuid
import pytest

from .test_browser_migrations import browser_schema, browser_update, rewind_browser_update

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('incompatibility', ['owner-timestamp', 'nonunique-index', 'unvalidated-true-check'])
def test_incompatible_browser_catalog_rejects_migration_without_advancing_watermark_or_partial_ddl(
        service_database, incompatibility):
    from src.db.database import _apply_db_updates
    path, blocks, index = browser_update()
    original = browser_schema(service_database)
    marker = 'fixture-catalog-reject-' + uuid.uuid4().hex
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO bs_browser_runs(run_id,state,steps_count) VALUES (%s,'WAITING_HUMAN',3)", (marker,))
        cursor.execute("INSERT INTO bs_browser_assistance_requests(assistance_id,run_id,state) VALUES (%s,%s,'controlling')", (marker, marker))
        cursor.execute("INSERT INTO bs_browser_resume_jobs(job_id,assistance_id,run_id,state) VALUES (%s,%s,%s,'pending')", (marker, marker, marker))
        if incompatibility == 'owner-timestamp':
            cursor.execute('ALTER TABLE bs_browser_runs ALTER COLUMN owner_lease_until TYPE TIMESTAMP')
        elif incompatibility == 'nonunique-index':
            cursor.execute('DROP INDEX idx_browser_runner_original_call')
            cursor.execute('CREATE INDEX idx_browser_runner_original_call ON bs_browser_runs(run_id)')
        else:
            cursor.execute('ALTER TABLE bs_browser_runs DROP CONSTRAINT ck_browser_runner_binding')
            cursor.execute('ALTER TABLE bs_browser_runs ADD CONSTRAINT ck_browser_runner_binding CHECK (TRUE) NOT VALID')
        # The migration would add this field before discovering incompatible
        # objects. Failure must roll that addition back with its whole DO block.
        cursor.execute('ALTER TABLE bs_browser_assistance_requests DROP COLUMN completion_ref CASCADE')
        rewind_browser_update(cursor, blocks, index)
    bad = browser_schema(service_database)
    def rows():
        return {table: service_database.rows(sql.SQL('SELECT * FROM {} WHERE {}=%s')
                    .format(sql.Identifier(table), sql.Identifier(identifier)), (marker,))
                for table, identifier in (('bs_browser_runs', 'run_id'),
                    ('bs_browser_assistance_requests', 'assistance_id'), ('bs_browser_resume_jobs', 'job_id'))}
    unchanged_rows = rows()
    previous = blocks[index - 1]['datetime']
    try:
        with service_database.connect() as connection:
            # Generic migration runner logs a statement failure and retains
            # its watermark; it deliberately does not raise to its caller.
            _apply_db_updates(connection, path)
        watermark = service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime']
        assert watermark == previous, 'Incompatible Browser catalog was accepted as a successful migration'
        assert browser_schema(service_database) == bad, 'Failed Browser block committed partial schema changes'
        assert rows() == unchanged_rows, 'Failed Browser migration changed existing legacy audit rows'
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            if incompatibility == 'owner-timestamp':
                cursor.execute('ALTER TABLE bs_browser_runs ALTER COLUMN owner_lease_until TYPE TIMESTAMPTZ')
            elif incompatibility == 'nonunique-index':
                cursor.execute('DROP INDEX idx_browser_runner_original_call')
            else:
                cursor.execute('ALTER TABLE bs_browser_runs DROP CONSTRAINT ck_browser_runner_binding')
            rewind_browser_update(cursor, blocks, index)
        with service_database.connect() as connection:
            _apply_db_updates(connection, path)
        assert browser_schema(service_database) == original
        service_database.rows('DELETE FROM bs_browser_resume_jobs WHERE job_id=%s', (marker,))
        service_database.rows('DELETE FROM bs_browser_assistance_requests WHERE assistance_id=%s', (marker,))
        service_database.rows('DELETE FROM bs_browser_runs WHERE run_id=%s', (marker,))
