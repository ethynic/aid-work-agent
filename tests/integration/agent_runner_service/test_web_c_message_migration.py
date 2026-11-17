"""Verify the Web C index upgrade with historical messages in the real PostgreSQL schema."""

from pathlib import Path

import pytest
from psycopg2.extras import Json

from src.db.database import _apply_db_updates, _load_db_update_blocks

pytestmark = pytest.mark.integration

UPDATE = Path(__file__).resolve().parents[3] / 'deploy' / 'db_update.yaml'
LEGACY_SESSION = 'tenant_demo_wecom_kf_helpdesk_visitor_main'
WEB_C_SESSION = 'tenant_demo_web_c_visitor_main'


def add_message(cursor, message_id, session_id, role, **metadata):
    cursor.execute('''INSERT INTO channel_messages
        (message_id, session_id, tenant_id, role, content, metadata)
        VALUES (%s, %s, 'tenant_demo', %s, %s, %s)
        ON CONFLICT DO NOTHING RETURNING message_id''',
        (message_id, session_id, role, message_id, Json(metadata)))
    return cursor.fetchone() is not None


@pytest.mark.parametrize('watermark,old_index_applied', [
    ('2026-11-16 10:00:00', False),
    ('2026-11-16 15:00:00', True),
    ('2026-11-17 10:00:00', True),
])
def test_web_c_index_upgrade_preserves_other_channels_and_deduplicates_per_role(service_database, watermark, old_index_applied):
    update = next(block for block in _load_db_update_blocks(UPDATE)
                  if 'CREATE UNIQUE INDEX IF NOT EXISTS uq_web_c_messages_runner_final' in block['statements'])
    with service_database.connect() as connection, connection.cursor() as cursor:
        cursor.execute('CREATE TEMP TABLE channel_messages (LIKE public.channel_messages INCLUDING DEFAULTS)')
        cursor.execute('''CREATE TEMP TABLE _db_update_applied (
            id TEXT PRIMARY KEY, file_hash TEXT, last_datetime TEXT, applied_at TIMESTAMP)''')
        cursor.execute("INSERT INTO _db_update_applied(id,last_datetime) VALUES ('db_update',%s)", (watermark,))
        assert add_message(cursor, 'kf-user', LEGACY_SESSION, 'user', runner_id='old-runner')
        if old_index_applied:
            cursor.execute('''CREATE UNIQUE INDEX uq_channel_messages_runner_final
                ON channel_messages (session_id, (metadata->>'runner_id'))
                WHERE metadata->>'runner_id' <> '' AND COALESCE(metadata->>'kind', '') = '';
                CREATE UNIQUE INDEX uq_channel_messages_client_request
                ON channel_messages (session_id, (metadata->>'client_request_id'))
                WHERE metadata->>'client_request_id' IS NOT NULL''')
            assert add_message(cursor, 'web-user', WEB_C_SESSION, 'user', client_request_id='request-1')
        else:
            assert add_message(cursor, 'kf-assistant', LEGACY_SESSION, 'assistant', runner_id='old-runner')
        cursor.execute('SELECT message_id FROM channel_messages ORDER BY message_id')
        history = cursor.fetchall()
        connection.commit()

        _apply_db_updates(connection, UPDATE)
        cursor.execute("SELECT last_datetime FROM _db_update_applied WHERE id='db_update'")
        assert cursor.fetchone()['last_datetime'] == update['datetime']
        cursor.execute('SELECT message_id FROM channel_messages ORDER BY message_id')
        assert cursor.fetchall() == history
        assert add_message(cursor, 'kf-new', LEGACY_SESSION, 'assistant', runner_id='old-runner')
        if not old_index_applied:
            assert add_message(cursor, 'web-user', WEB_C_SESSION, 'user', client_request_id='request-1')
        assert add_message(cursor, 'web-direct', WEB_C_SESSION, 'assistant', client_request_id='request-1', kind='direct_reply')
        assert not add_message(cursor, 'web-user-retry', WEB_C_SESSION, 'user', client_request_id='request-1')
        assert not add_message(cursor, 'web-direct-retry', WEB_C_SESSION, 'assistant', client_request_id='request-1', kind='direct_reply')
        assert add_message(cursor, 'web-ask', WEB_C_SESSION, 'assistant', runner_id='web-runner', kind='clarification')
        assert add_message(cursor, 'web-final', WEB_C_SESSION, 'assistant', runner_id='web-runner')
        assert not add_message(cursor, 'web-final-retry', WEB_C_SESSION, 'assistant', runner_id='web-runner')
        cursor.execute(update['statements'])
        assert not add_message(cursor, 'web-final-replay', WEB_C_SESSION, 'assistant', runner_id='web-runner')
