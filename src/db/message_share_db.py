"""Persistence for the independently readable copy of one Web reply."""

import json

from src.db.database import get_db_connection


class MessageShareDB:
    @staticmethod
    def resource_grant(file_id, tenant_id):
        """Use persisted asset/document provenance and the existing KB download rule."""
        with get_db_connection() as connection:
            cursor = connection.cursor()
            cursor.execute('''SELECT f.tenant_id,
                (f.tenant_id IS NOT DISTINCT FROM %s OR (
                    f.source='knowledge_base' AND d.status<>'deleted'
                    AND EXISTS (SELECT 1 FROM tenant_knowledge_shares k
                        WHERE k.from_tenant_id=f.tenant_id AND k.to_tenant_id=%s)
                    AND EXISTS (SELECT 1 FROM subagent_knowledge_sources s
                        WHERE s.tenant_id=%s AND s.sources @> jsonb_build_array(
                            jsonb_build_object('owner_tenant_id',f.tenant_id,'source_type',d.source_type)))
                )) AS allowed
                FROM file_assets f LEFT JOIN documents d
                    ON d.id::text=f.extra->>'linked_doc_id' AND d.tenant_id=f.tenant_id
                WHERE f.file_id=%s''', (tenant_id, tenant_id, tenant_id, file_id))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def source_message(session, message_id):
        projected_runner = message_id[:-len(':assistant')] if message_id.endswith(':assistant') else None
        with get_db_connection() as connection:
            cursor = connection.cursor()
            if projected_runner:
                cursor.execute('''SELECT r.runner_id,r.status,r.public_snapshot,r.result,r.finished_at
                    FROM agent_runners r JOIN chat_sessions s ON s.session_id=r.session_id
                    WHERE s.session_id=%s AND s.user_id=%s AND s.tenant_id IS NOT DISTINCT FROM %s
                        AND r.runner_id=%s AND r.session_kind='web'
                        AND r.user_id=s.user_id AND r.tenant_id IS NOT DISTINCT FROM s.tenant_id''',
                    (session['session_id'], session['user_id'], session.get('tenant_id'), projected_runner))
                runner = cursor.fetchone()
                if not runner:
                    return None
                snapshot = runner.get('public_snapshot') or {}
                result = runner.get('result') or {}
                if isinstance(snapshot, str):
                    snapshot = json.loads(snapshot)
                if isinstance(result, str):
                    result = json.loads(result)
                metadata = {**(result.get('assistant_metadata') or {}), **snapshot,
                    'runner_id': runner['runner_id'], 'images': result.get('images') or snapshot.get('images') or [],
                    'cancelled': runner['status']=='cancelled'}
                return {'message_id': message_id, 'content': result.get('output') if result.get('output') is not None else snapshot.get('output',''),
                    'metadata': metadata, 'runner_status': runner['status'], 'created_at': runner.get('finished_at')}
            cursor.execute('''SELECT m.*, r.status AS runner_status
                FROM chat_messages m JOIN chat_sessions s ON s.session_id=m.session_id
                LEFT JOIN agent_runners r ON r.runner_id=(m.metadata::jsonb)->>'runner_id'
                WHERE s.session_id=%s AND s.user_id=%s AND s.tenant_id IS NOT DISTINCT FROM %s
                  AND m.role='assistant' AND (m.message_id=%s OR
                       (%s IS NOT NULL AND (m.metadata::jsonb)->>'runner_id'=%s))
                  AND NOT (COALESCE(m.metadata::jsonb, '{}'::jsonb) ? 'tool_calls')
                  AND NOT (COALESCE(m.metadata::jsonb, '{}'::jsonb) ? 'tool_call_id')
                  AND NOT (COALESCE(m.metadata::jsonb, '{}'::jsonb) ? 'wait_id')
                ORDER BY m.created_at DESC,m.id DESC LIMIT 1''',
                (session['session_id'], session['user_id'], session.get('tenant_id'),
                 message_id, projected_runner, projected_runner))
            row = cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    def create(record):
        with get_db_connection() as connection:
            cursor = connection.cursor()
            cursor.execute('''INSERT INTO chat_message_shares
                (share_key,tenant_id,user_id,session_id,message_id,reply_data,password_hash)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)''',
                (record['share_key'], record.get('tenant_id'), record['user_id'],
                 record['session_id'], record['message_id'],
                 json.dumps(record['reply_data'], ensure_ascii=False), record.get('password_hash')))
            connection.commit()

    @staticmethod
    def get(share_key):
        with get_db_connection() as connection:
            cursor = connection.cursor()
            cursor.execute('SELECT * FROM chat_message_shares WHERE share_key=%s', (share_key,))
            row = cursor.fetchone()
            if not row:
                return None
            record = dict(row)
            if isinstance(record.get('reply_data'), str):
                record['reply_data'] = json.loads(record['reply_data'])
            return record
