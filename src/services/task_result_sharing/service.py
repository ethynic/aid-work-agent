"""A share key maps to one scheduled run; the visible reply is read live."""

import json
import secrets
from urllib.parse import quote, urlencode

from fastapi import HTTPException

from src.core.download_ticket import issue_download_ticket, validate_download_ticket
from src.db.database import get_db_connection
from src.services.message_sharing import service as reply_sharing


def share_path(share_key):
    return '/api/public/task-result-shares/' + quote(share_key, safe='')


def ensure_task_result_share_in_tx(cursor, task, runner):
    """Create the opaque mapping in the runner's terminal transaction."""
    if (not task or task['session_id'] != runner['session_id']
            or task['user_id'] != runner['user_id']
            or task['tenant_id'] != runner['tenant_id']):
        raise ValueError('TASK_RESULT_SHARE_OWNER_MISMATCH')
    cursor.execute('''INSERT INTO task_result_shares
        (share_key,tenant_id,user_id,task_id,runner_id,session_id)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (task_id, runner_id) DO NOTHING''',
        (secrets.token_urlsafe(24), task['tenant_id'], task['user_id'], task['task_id'],
         runner['runner_id'], task['session_id']))
    cursor.execute('''SELECT share_key FROM task_result_shares
        WHERE task_id=%s AND runner_id=%s''', (task['task_id'], runner['runner_id']))
    return cursor.fetchone()['share_key']


def task_result_links_for_runners_in_tx(cursor, runner_ids, tenant_id, user_id):
    """Backfill old terminal notices in one cursor, returning only live owned runs."""
    ids = list(dict.fromkeys(runner_id for runner_id in runner_ids if runner_id))
    if not ids or not tenant_id or not user_id:
        return {}
    keys = [secrets.token_urlsafe(24) for _ in ids]
    cursor.execute('''INSERT INTO task_result_shares
        (share_key,tenant_id,user_id,task_id,runner_id,session_id)
        SELECT seed.share_key,t.tenant_id,t.user_id,t.task_id,r.runner_id,t.session_id
        FROM unnest(%s::text[],%s::text[]) AS seed(share_key,runner_id)
        JOIN agent_runners r ON r.runner_id=seed.runner_id
        JOIN scheduled_tasks t ON t.session_id=r.session_id
            AND t.user_id=r.user_id AND t.tenant_id=r.tenant_id
        WHERE r.source='scheduled' AND r.session_kind='web'
          AND r.status IN ('completed','failed') AND r.user_id=%s AND r.tenant_id=%s
        ON CONFLICT (task_id, runner_id) DO NOTHING''',
        (keys, ids, user_id, tenant_id))
    cursor.execute('''SELECT s.runner_id,s.share_key FROM task_result_shares s
        JOIN agent_runners r ON r.runner_id=s.runner_id AND r.session_id=s.session_id
            AND r.user_id=s.user_id AND r.tenant_id=s.tenant_id
        JOIN scheduled_tasks t ON t.task_id=s.task_id AND t.session_id=s.session_id
            AND t.user_id=s.user_id AND t.tenant_id=s.tenant_id
        WHERE s.runner_id=ANY(%s) AND s.user_id=%s AND s.tenant_id=%s
          AND r.source='scheduled' AND r.session_kind='web'
          AND r.status IN ('completed','failed')''',
        (ids, user_id, tenant_id))
    return {row['runner_id']: row['share_key'] for row in cursor.fetchall()}


def _row(cursor, sql, values):
    cursor.execute(sql, values)
    row = cursor.fetchone()
    return dict(row) if row else None


def _live_snapshot(share_key):
    with get_db_connection() as connection:
        cursor = connection.cursor()
        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        record = _row(cursor, 'SELECT * FROM task_result_shares WHERE share_key=%s', (share_key,))
        if not record:
            reply_sharing.fail(404, 'SHARE_NOT_FOUND', '分享不存在或已不可用')
        task = _row(cursor, '''SELECT t.* FROM scheduled_tasks t
            JOIN chat_sessions s ON s.session_id=t.session_id
                AND s.user_id=t.user_id AND s.tenant_id IS NOT DISTINCT FROM t.tenant_id
            WHERE t.task_id=%s AND t.session_id=%s AND t.user_id=%s AND t.tenant_id=%s''',
            (record['task_id'], record['session_id'], record['user_id'], record['tenant_id']))
        if not task:
            reply_sharing.fail(404, 'TASK_GONE', '任务已删除或链接已失效')
        runner = _row(cursor, '''SELECT * FROM agent_runners
            WHERE runner_id=%s AND session_id=%s AND user_id=%s AND tenant_id=%s
              AND source='scheduled' AND session_kind='web' AND status IN ('completed','failed')''',
            (record['runner_id'], record['session_id'], record['user_id'], record['tenant_id']))
        if not runner or runner['session_id'] != task['session_id'] or runner['user_id'] != task['user_id'] or runner['tenant_id'] != task['tenant_id'] or runner['status'] not in ('completed', 'failed'):
            reply_sharing.fail(404, 'TASK_GONE', '任务已删除或链接已失效')
        message = _row(cursor, '''SELECT content,metadata,created_at FROM chat_messages
            WHERE session_id=%s AND role='assistant' AND (metadata::jsonb)->>'runner_id'=%s
              AND NOT (COALESCE(metadata::jsonb,'{}'::jsonb) ? 'tool_calls')
              AND NOT (COALESCE(metadata::jsonb,'{}'::jsonb) ? 'tool_call_id')
              AND NOT (COALESCE(metadata::jsonb,'{}'::jsonb) ? 'wait_id')
            ORDER BY created_at DESC,id DESC LIMIT 1''',
            (record['session_id'], record['runner_id']))
        if not message:
            history_input = _row(cursor, '''SELECT 1 AS present FROM chat_messages
                WHERE session_id=%s AND role='user' AND (metadata::jsonb)->>'runner_id'=%s
                LIMIT 1''', (record['session_id'], record['runner_id']))
            if not history_input:
                reply_sharing.fail(404, 'TASK_GONE', '任务已删除或链接已失效')
            snapshot = runner.get('public_snapshot') or {}
            result = runner.get('result') or {}
            if isinstance(snapshot, str):
                snapshot = json.loads(snapshot)
            if isinstance(result, str):
                result = json.loads(result)
            # Text/image results are written to chat_messages; their absence means deletion.
            if (not result and not snapshot or result.get('output') or result.get('images')
                    or snapshot.get('output') or snapshot.get('images')):
                reply_sharing.fail(404, 'TASK_GONE', '任务已删除或链接已失效')
            message = {'content': result.get('output') if result.get('output') is not None else snapshot.get('output', ''),
                       'metadata': {**(result.get('assistant_metadata') or {}), **snapshot,
                                    'images': result.get('images') or snapshot.get('images') or []},
                       'created_at': runner.get('finished_at')}
        try:
            projected = reply_sharing.reply_snapshot(message)
        except HTTPException as error:
            if error.status_code != 422 or error.detail.get('code') != 'EMPTY_REPLY':
                raise
            reply_sharing.fail(404, 'TASK_GONE', '任务已删除或链接已失效')
        reply_sharing._bind_resources(projected, record['tenant_id'])
        return record, task, runner, projected


def view_task_result(share_key, viewer=None):
    record, task, runner, projected = _live_snapshot(share_key)
    base = share_path(share_key)
    ticket = issue_download_ticket(base, ttl_seconds=reply_sharing.ACCESS_TTL_SECONDS)

    def resource_url(match):
        return base + '/files/' + match['id'] + '?' + urlencode({'ticket': ticket})

    message = projected['message']
    message['content'] = reply_sharing.SHARE_FILE.sub(resource_url, message['content'])
    for name in ('images', 'downloadableFiles'):
        for item in message.get(name) or []:
            item['download_url'] = reply_sharing.SHARE_FILE.sub(resource_url, item.get('download_url') or '')
    return {'state': 'ready', 'task_name': task['name'], 'task_status': task['status'],
            'runner_status': runner['status'], 'finished_at': runner['finished_at'],
            'tenant_id': record['tenant_id'], 'task_id': record['task_id'],
            'runner_id': record['runner_id'], 'message': message, 'access_ticket': ticket,
            'viewer_is_owner': bool(viewer and viewer.get('user_id') == record['user_id']
                                    and viewer.get('tenant_id') == record['tenant_id'])}


def task_result_file(share_key, alias, ticket, download=False):
    try:
        access = validate_download_ticket(ticket, share_path(share_key))
    except (ValueError, TypeError, OverflowError):
        access = None
    if access is None:
        reply_sharing.fail(403, 'SHARE_ACCESS_REQUIRED', '请先打开分享页面并验证访问权限')
    record, _, _, projected = _live_snapshot(share_key)
    return reply_sharing.shared_file(
        {'share_key': record['share_key'], 'tenant_id': record['tenant_id'],
         'reply_data': projected}, alias, ticket, download, base_path=share_path(share_key))
