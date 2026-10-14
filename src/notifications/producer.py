"""Cursor-only producer at the original Runner terminal transaction tail."""
from datetime import timedelta

from .contracts import Subject, session_title


def _user_owned_web_runner(runner):
    """与 task.completed/failed 同一口径：chat/web 来源、用户本人触发的 runner。"""
    return (runner['source'] == 'chat' and runner['session_kind'] == 'web'
            and runner['actor_kind'] == 'user' and bool(runner.get('user_id'))
            and runner['actor_id'] == runner['user_id'])


def publish_runner_terminal_in_tx(cursor, runner, config=None):
    if config is None:
        from src.config.settings import settings
        config = settings.notifications
    if (runner['status'] not in ('completed', 'failed')
            or not _user_owned_web_runner(runner)):
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


def _clarification_leaf(waiting):
    """等待叶节点解析（子执行委派链：child_wait 逐层下钻至 clarification，
    有界 64 层，与 agent_runner 内既有下钻模式一致）。返回带 form 的叶节点
    或 None——顶层与嵌套子执行提问都走这一条解析（五轮评审 P2-3）。"""
    node = waiting
    for _ in range(64):
        if not isinstance(node, dict):
            return None
        if node.get('kind') == 'clarification' and isinstance(node.get('form'), dict):
            return node
        node = node.get('child_wait')
    return None


def publish_runner_waiting_in_tx(cursor, runner, config=None):
    """提问等待通知（Phase 2，设计 §5.6）：park 事务尾调用。

    仅 chat/web 用户自有 runner 进入 waiting 且携带结构化表单（ask_user，含
    子执行嵌套链）时通知；legacy clarify（无 form）不通知。event_key 按
    叶节点真实 runner+wait 去重——同一 wait 的重复 park 不会重复生产
    （ON CONFLICT DO NOTHING），下一轮 ask_user 产生新 wait_id 即新通知。
    """
    if config is None:
        from src.config.settings import settings
        config = settings.notifications
    if runner['status'] != 'waiting' or not _user_owned_web_runner(runner):
        return
    waiting = _clarification_leaf((runner.get('public_snapshot') or {}).get('waiting'))
    if waiting is None:
        return
    form = waiting['form']
    wait_id = form.get('wait_id') or waiting.get('wait_id')
    if not isinstance(wait_id, str) or not wait_id:
        return
    subject = Subject(runner['user_id'], runner['tenant_id'])
    if subject.scope_key != runner['scope_key']:
        raise ValueError('NOTIFICATION_RUNNER_SCOPE_MISMATCH')
    cursor.execute('''SELECT title FROM chat_sessions WHERE session_id=%s AND user_id=%s
        AND tenant_id IS NOT DISTINCT FROM %s''', (runner['session_id'],subject.user_id,subject.tenant_id))
    session = cursor.fetchone()
    if not session:
        return
    questions = [q for q in form.get('questions') or [] if isinstance(q, dict)]
    raw_title = (form.get('title') or (questions[0].get('prompt') if questions else '')
                 or '请回答提问').strip()
    question_title = raw_title[:120] or '请回答提问'
    from .events import publish_in_tx
    occurred = runner['updated_at']
    return publish_in_tx(cursor,subject=subject,producer='runner',event_type='task.waiting_input',
        event_key=f'waiting:{runner["runner_id"]}:{wait_id}',
        resource_ref={'type':'runner_waiting','id':runner['runner_id']},
        parameters={'session_title':session_title(session['title']),'event_kind':'waiting_input',
            'question_title':question_title,'session_id':str(runner['session_id'])},
        occurred_at=occurred,accepted_at=runner.get('accepted_at'),
        expires_at=occurred+timedelta(seconds=config.ttl_seconds))
