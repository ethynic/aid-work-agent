"""Conversation display preferences; never delete execution or accounting facts."""

import json

from fastapi import HTTPException

from src.core.cache_utils import CacheKeys, delete_cached
from src.db.database import get_db_connection


def failed_message_ui(session):
    metadata = session.get('metadata') or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    return metadata.get('chat_failed_message_ui') or {}


def update_failed_message_ui(session, runner_id, action, client_request_id=None):
    """Lock the owned session so independent deletes/retries cannot lose updates."""
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute('''SELECT metadata FROM chat_sessions WHERE session_id=%s AND user_id=%s
            AND tenant_id IS NOT DISTINCT FROM %s FOR UPDATE''',
            (session['session_id'], session['user_id'], session['tenant_id']))
        current = cursor.fetchone()
        if not current:
            raise HTTPException(404, 'SESSION_NOT_FOUND')
        cursor.execute('''SELECT runner_id FROM agent_runners WHERE runner_id=%s AND session_id=%s
            AND session_kind='web' AND source='chat' AND status='failed' AND user_id=%s
            AND tenant_id IS NOT DISTINCT FROM %s''',
            (runner_id, session['session_id'], session['user_id'], session['tenant_id']))
        if not cursor.fetchone():
            raise HTTPException(409, 'FAILED_MESSAGE_NOT_FOUND')
        preferences = failed_message_ui(current)
        section = 'hidden_failed' if action == 'hide' else 'retry_sources'
        key = runner_id if action == 'hide' else client_request_id
        entries = preferences.setdefault(section, {})
        if key not in entries and len(entries) >= 2000:
            raise HTTPException(409, 'FAILED_MESSAGE_PREFERENCES_FULL')
        # A request key is immutable; it must never be rebound to another source.
        value = True if action == 'hide' else runner_id
        if key in entries and entries[key] != value:
            raise HTTPException(409, 'RETRY_SOURCE_CONFLICT')
        entries[key] = value
        cursor.execute('''UPDATE chat_sessions SET metadata=jsonb_set(
            COALESCE(metadata, '{}'::jsonb), '{chat_failed_message_ui}', %s::jsonb)
            WHERE session_id=%s AND user_id=%s AND tenant_id IS NOT DISTINCT FROM %s''',
            (json.dumps(preferences), session['session_id'], session['user_id'], session['tenant_id']))
        connection.commit()
    delete_cached(CacheKeys.SESSION, session['session_id'])
