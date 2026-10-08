"""Actual once-extension DDL through the original isolated migration runner."""
from pathlib import Path
import uuid

import pytest

from .test_browser_migrations import browser_schema, rewind_browser_update

pytestmark = pytest.mark.integration


def extension_update():
    from src.db.database import _load_db_update_blocks
    path = Path(__file__).resolve().parents[3] / 'deploy' / 'db_update.yaml'
    blocks = _load_db_update_blocks(path)
    matches = [i for i, block in enumerate(blocks)
               if 'extended_at' in block['statements'] and 'bs_browser_assistance_requests' in block['statements']]
    assert len(matches) == 1 and matches[0] > 0
    return path, blocks, matches[0]


def test_real_extension_fresh_nullable_tz_legacy_upgrade_and_replay_preserve_original_wait(service_database):
    from src.db.database import _apply_db_updates
    path, blocks, index = extension_update()
    fresh = browser_schema(service_database)
    column = [row for row in fresh['columns'] if row['table_name'] == 'bs_browser_assistance_requests'
              and row['column_name'] == 'extended_at']
    assert len(column) == 1 and column[0]['data_type'] == 'timestamp with time zone'
    assert column[0]['is_nullable'] == 'YES' and column[0]['column_default'] is None
    marker = 'human-legacy-extension-' + uuid.uuid4().hex
    try:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO bs_browser_runs(run_id,state) VALUES(%s,'WAITING_HUMAN')", (marker,))
            cursor.execute("""INSERT INTO bs_browser_assistance_requests(assistance_id,run_id,state,expires_at)
                VALUES(%s,%s,'controlling',clock_timestamp()+interval '4 minutes') RETURNING *""", (marker, marker))
            original = dict(cursor.fetchone())
            assert original['extended_at'] is None and original['runner_id'] is None
            cursor.execute('ALTER TABLE bs_browser_assistance_requests DROP COLUMN extended_at')
            rewind_browser_update(cursor, blocks, index)
        for replay in range(3):
            if replay == 2:
                with service_database.connect() as connection, connection.cursor() as cursor:
                    rewind_browser_update(cursor, blocks, index)
            with service_database.connect() as connection:
                _apply_db_updates(connection, path)
            assert browser_schema(service_database) == fresh
            assert service_database.rows('SELECT * FROM bs_browser_assistance_requests WHERE assistance_id=%s',
                (marker,)) == [original]
            assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'") == [
                {'last_datetime': blocks[-1]['datetime']}]
    finally:
        service_database.rows('DELETE FROM bs_browser_assistance_requests WHERE assistance_id=%s', (marker,))
        service_database.rows('DELETE FROM bs_browser_runs WHERE run_id=%s', (marker,))


def test_real_extension_incompatible_timestamp_does_not_advance_watermark_or_change_legacy_rows(service_database):
    from src.db.database import _apply_db_updates
    path, blocks, index = extension_update()
    original = browser_schema(service_database)
    marker = 'human-bad-extension-' + uuid.uuid4().hex
    try:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO bs_browser_runs(run_id,state) VALUES(%s,'WAITING_HUMAN')", (marker,))
            cursor.execute("INSERT INTO bs_browser_assistance_requests(assistance_id,run_id,state) VALUES(%s,%s,'pending')",
                           (marker, marker))
            cursor.execute('ALTER TABLE bs_browser_assistance_requests ALTER COLUMN extended_at TYPE TIMESTAMP')
            rewind_browser_update(cursor, blocks, index)
        bad = browser_schema(service_database)
        rows = service_database.rows('SELECT * FROM bs_browser_assistance_requests WHERE assistance_id=%s', (marker,))
        with service_database.connect() as connection:
            # The actual updater logs DO failure; it does not raise to Python.
            _apply_db_updates(connection, path)
        assert browser_schema(service_database) == bad
        assert service_database.rows('SELECT * FROM bs_browser_assistance_requests WHERE assistance_id=%s', (marker,)) == rows
        assert service_database.rows("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'") == [
            {'last_datetime': blocks[index - 1]['datetime']}]
    finally:
        with service_database.connect() as connection, connection.cursor() as cursor:
            cursor.execute('ALTER TABLE bs_browser_assistance_requests ALTER COLUMN extended_at TYPE TIMESTAMPTZ')
            rewind_browser_update(cursor, blocks, index)
        with service_database.connect() as connection:
            _apply_db_updates(connection, path)
        assert browser_schema(service_database) == original
        service_database.rows('DELETE FROM bs_browser_assistance_requests WHERE assistance_id=%s', (marker,))
        service_database.rows('DELETE FROM bs_browser_runs WHERE run_id=%s', (marker,))
