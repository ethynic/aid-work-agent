"""Actual production Browser schema with legacy nullable rows and constraints."""
import uuid

from psycopg2 import sql
from psycopg2.errors import CheckViolation
import pytest

from .test_browser_migrations import browser_schema, browser_update, rewind_browser_update

pytestmark = pytest.mark.integration
RUN_OWNER_COLUMNS = ('runner_id', 'runner_execution_id', 'runner_tool_call_id', 'owner_worker_id',
    'owner_boot_id', 'browser_epoch', 'owner_endpoint', 'owner_lease_until', 'runtime_state', 'closed_at')
WAIT_OWNER_COLUMNS = ('runner_id', 'runner_wait_id', 'owner_boot_id', 'browser_epoch', 'completion_ref', 'completion_fact')


def test_legacy_nullable_browser_rows_keep_original_ids_and_states_through_owner_column_upgrade(service_database):
    from src.db.database import _apply_db_updates
    path, blocks, index = browser_update()
    fresh = browser_schema(service_database)
    suffix = uuid.uuid4().hex
    run_id, assistance_id, job_id = ('legacy-browser-' + kind + '-' + suffix for kind in ('run', 'wait', 'job'))
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('''INSERT INTO bs_browser_runs(run_id,state) VALUES (%s,'WAITING_HUMAN') RETURNING id''', (run_id,))
        run_pk = cursor.fetchone()['id']
        cursor.execute('''INSERT INTO bs_browser_assistance_requests(assistance_id,run_id,state)
            VALUES (%s,%s,'controlling') RETURNING id''', (assistance_id, run_id))
        wait_pk = cursor.fetchone()['id']
        cursor.execute('''INSERT INTO bs_browser_resume_jobs(job_id,assistance_id,run_id,state)
            VALUES (%s,%s,%s,'pending') RETURNING id''', (job_id, assistance_id, run_id))
        job_pk = cursor.fetchone()['id']
        # Derive the old tables from genuine fresh DDL by removing only the
        # newly introduced owner columns. No test reimplements baseline DDL.
        for table, columns in (('bs_browser_runs', RUN_OWNER_COLUMNS),
                               ('bs_browser_assistance_requests', WAIT_OWNER_COLUMNS)):
            for column in columns:
                cursor.execute(sql.SQL('ALTER TABLE {} DROP COLUMN {} CASCADE')
                               .format(sql.Identifier(table), sql.Identifier(column)))
        rewind_browser_update(cursor, blocks, index)
    try:
        with service_database.connect() as connection:
            _apply_db_updates(connection, path)
        assert browser_schema(service_database) == fresh
        row = service_database.rows('SELECT * FROM bs_browser_runs WHERE run_id=%s', (run_id,))[0]
        assert row['id'] == run_pk and row['state'] == 'WAITING_HUMAN'
        assert row['tenant_id'] is None and row['user_id'] is None
        assert all(row[column] is None for column in RUN_OWNER_COLUMNS)
        wait = service_database.rows('SELECT * FROM bs_browser_assistance_requests WHERE assistance_id=%s', (assistance_id,))[0]
        assert wait['id'] == wait_pk and wait['state'] == 'controlling'
        assert all(wait[column] is None for column in WAIT_OWNER_COLUMNS)
        assert service_database.rows('SELECT id,state FROM bs_browser_resume_jobs WHERE job_id=%s', (job_id,)) == [{'id': job_pk, 'state': 'pending'}]
        service_database.rows("UPDATE _db_update_applied SET last_datetime=%s WHERE id='db_update'", (blocks[index - 1]['datetime'],))
        with service_database.connect() as connection:
            _apply_db_updates(connection, path)
        assert service_database.rows('SELECT * FROM bs_browser_runs WHERE run_id=%s', (run_id,)) == [row]
        assert service_database.rows('SELECT * FROM bs_browser_assistance_requests WHERE assistance_id=%s', (assistance_id,)) == [wait]
    finally:
        service_database.rows('DELETE FROM bs_browser_resume_jobs WHERE job_id=%s', (job_id,))
        service_database.rows('DELETE FROM bs_browser_assistance_requests WHERE assistance_id=%s', (assistance_id,))
        service_database.rows('DELETE FROM bs_browser_runs WHERE run_id=%s', (run_id,))


@pytest.mark.parametrize('table', ['bs_browser_runs', 'bs_browser_assistance_requests'])
def test_runner_browser_partial_owner_linkage_is_rejected_by_real_database_constraint(service_database, table):
    marker = 'fixture-partial-owner-' + uuid.uuid4().hex
    identifier = 'run_id' if table == 'bs_browser_runs' else 'assistance_id'
    with pytest.raises(CheckViolation):
        service_database.rows(sql.SQL('INSERT INTO {} ({},runner_id) VALUES (%s,%s)')
                              .format(sql.Identifier(table), sql.Identifier(identifier)), (marker, marker))
    assert service_database.rows(sql.SQL('SELECT 1 FROM {} WHERE {}=%s')
                                  .format(sql.Identifier(table), sql.Identifier(identifier)), (marker,)) == []


def test_compatible_legacy_integer_ids_and_audit_timestamps_remain_unchanged_on_actual_migration(service_database):
    from src.db.database import _apply_db_updates
    path, blocks, index = browser_update()
    original = browser_schema(service_database)
    with service_database.connect() as connection, connection.cursor() as cursor:
        # Older serial-int4 tables coexist with serial-int8 audit tables.
        # These are genuine production tables, not copied fixture definitions.
        for table in ('bs_browser_runs', 'bs_browser_resume_jobs'):
            cursor.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN id TYPE INTEGER').format(sql.Identifier(table)))
        rewind_browser_update(cursor, blocks, index)
    compatible = browser_schema(service_database)
    try:
        with service_database.connect() as connection:
            _apply_db_updates(connection, path)
        assert browser_schema(service_database) == compatible
        assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")[0]['last_datetime'] == blocks[-1]['datetime']
        columns = compatible['columns']
        assert {row['data_type'] for row in columns if row['column_name'] == 'id'} == {'integer', 'bigint'}
        assert all(row['data_type'] == 'timestamp without time zone' for row in columns
                   if row['column_name'] in ('created_at', 'updated_at'))
        assert all(row['data_type'] == 'timestamp with time zone' for row in columns
                   if row['column_name'] in ('owner_lease_until', 'lease_until'))
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            for table in ('bs_browser_runs', 'bs_browser_resume_jobs'):
                cursor.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN id TYPE BIGINT').format(sql.Identifier(table)))
        assert browser_schema(service_database) == original
