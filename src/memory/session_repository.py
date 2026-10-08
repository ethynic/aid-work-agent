"""Compression session storage; independent of ingress and delivery managers."""

import json
from src.db.database import get_db_connection


class CompressionSessionRepository:
    def get_session(self, session_id, source_type):
        if source_type == "chat":
            from src.db.models import SessionDB
            return SessionDB.get_by_id(session_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM channel_sessions WHERE session_id = %s", (session_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def load_messages(self, session_id, source_type, limit=10000):
        if source_type == "chat":
            from src.db.models import MessageDB
            return MessageDB.list_by_session(session_id, limit=limit)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""SELECT * FROM channel_messages WHERE session_id = %s
                AND (compacted = FALSE OR compacted IS NULL)
                AND (is_recalled = FALSE OR is_recalled IS NULL)
                AND (status = 'active' OR status IS NULL)
                ORDER BY id DESC LIMIT %s""", (session_id, limit))
            messages = list(reversed([dict(row) for row in cursor.fetchall()]))
        for message in messages:
            for field, default in (("metadata", {}), ("attachments", [])):
                value = message.get(field)
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        value = default
                message[field] = value if value is not None else default
        return messages

    def count_messages(self, session_id, source_type):
        if source_type == "chat":
            from src.db.models import MessageDB
            return MessageDB.count_messages_by_session(session_id)
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""SELECT COUNT(*) AS cnt FROM channel_messages WHERE session_id = %s
                AND (compacted = FALSE OR compacted IS NULL)
                AND (is_recalled = FALSE OR is_recalled IS NULL)
                AND (status = 'active' OR status IS NULL)""", (session_id,))
            return int(cursor.fetchone()["cnt"])
