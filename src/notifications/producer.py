"""Cursor-only producer at the original Runner terminal transaction tail."""
from datetime import timedelta

from .contracts import Subject, session_title


def publish_runner_terminal_in_tx(cursor, runner, config=None):
    if config is None:
        from src.config.settings import settings
        config = settings.notifications
    if (runner['source'] != 'chat' or runner['session_kind'] != 'web'
            or runner['status'] not in ('completed', 'failed') or runner['actor_kind'] != 'user'
            or not runner.get('user_id') or runner['actor_id'] != runner['user_id']):
        return
    subject = Subject(runner['user_id'], runner['tenant_id'])
    if subject.scope_key != runner['scope_key']:
        raise ValueError('NOTIFICATION_RUNNER_SCOPE_MISMATCH')
    cursor.execute('''SELECT title FROM chat_sessions WHERE session_id=%s AND user_id=%s
        AND tenant_id IS NOT DISTINCT FROM %s''', (runner['session_id'],subject.user_id,subject.tenant_id))
    session = cursor.fetchone()
    if not session:
        return  # Deleted resources have no recipient-readable result.
    from .events import publish_in_tx
    occurred=runner['finished_at']
    return publish_in_tx(cursor,subject=subject,producer='runner',event_type='task.'+runner['status'],
        event_key='terminal:'+runner['runner_id'],resource_ref={'type':'runner_result','id':runner['runner_id']},
        parameters={'session_title':session_title(session['title']),'event_kind':runner['status']},
        occurred_at=occurred,accepted_at=runner['accepted_at'],
        expires_at=occurred+timedelta(seconds=config.ttl_seconds))
