"""Cursor-only producer at the original Runner terminal transaction tail."""
from datetime import timedelta

from .contracts import Subject, session_title


def _user_owned_web_runner(runner):
    """与 task.completed/failed 同一口径：chat/web 来源、用户本人触发的 runner。

    scheduled 来源（任务属主即 runner.user_id，Phase 3 扩展）同样适用。"""
    return (runner['source'] in ('chat', 'scheduled') and runner['session_kind'] == 'web'
            and bool(runner.get('user_id'))
            and (runner['source'] != 'chat' or (
                runner['actor_kind'] == 'user' and runner['actor_id'] == runner['user_id'])))


def _scheduled_parameters(cursor, runner, event_kind):
    """scheduled 事件参数：会话标题（=任务名）+ session_id/task_id/目标快照
    （设计 §8.2：notify_target_id 在事件生成时从任务行快照，加密存储不可变）。"""
    cursor.execute('''SELECT title FROM chat_sessions WHERE session_id=%s AND user_id=%s
        AND tenant_id IS NOT DISTINCT FROM %s''', (runner['session_id'], runner['user_id'],
                                                   runner['tenant_id']))
    session = cursor.fetchone()
    if not session:
        return None
    cursor.execute('''SELECT notification_target_id,name FROM scheduled_tasks
        WHERE session_id=%s''', (runner['session_id'],))
    task = cursor.fetchone()
    scheduled_meta = ((runner.get('input') or {}).get('request_data') or {}).get('scheduled') or {}
    parameters = {'session_title': session_title(session['title']),
                  'event_kind': event_kind,
                  'task_id': str(scheduled_meta.get('task_id') or '')[:64],
                  'task_name': session_title((task or {}).get('name') or session['title'])}
    target_id = (task or {}).get('notification_target_id')
    if target_id:
        parameters['notify_target_id'] = str(target_id)[:128]
    return parameters


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
    event_kind = runner['status']
    if runner['source'] == 'scheduled':
        parameters = _scheduled_parameters(cursor, runner, event_kind)
        if parameters is None:
            return  # Deleted resources have no recipient-readable result.
        occurred = runner['finished_at']
        from .events import publish_in_tx
        return publish_in_tx(cursor, subject=subject, producer='runner',
            event_type='task.' + event_kind,
            event_key='terminal:' + runner['runner_id'],
            resource_ref={'type': 'runner_result', 'id': runner['runner_id']},
            parameters=parameters,
            occurred_at=occurred, accepted_at=runner['accepted_at'],
            expires_at=occurred + timedelta(seconds=config.ttl_seconds))
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
    if runner['source'] == 'scheduled':
        # scheduled 非交互执行无提问等待（工具过滤保证）；下车等待走
        # publish_runner_needs_action_in_tx（设计 §8.1，需要用户动作的等待）。
        publish_runner_needs_action_in_tx(cursor, runner, config=config)
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


# 确需用户动作的等待类型（设计 §8.1：设备确认/人工辅助/登录；远程结果与
# 子执行等待是系统自行推进的正常状态，不通知防噪声——按下钻叶子实际类型判断）。
NEEDS_ACTION_WAIT_KINDS = {'verification', 'human_assistance'}


def _needs_action_leaf(waiting):
    """下钻等待树取叶子：命中需用户动作类型返回该叶子，否则 None
    （remote_tool/子执行等待/clarification 均不产生 needs_action）。"""
    node = waiting
    for _ in range(64):
        if not isinstance(node, dict):
            return None
        if node.get('kind') in NEEDS_ACTION_WAIT_KINDS:
            return node
        node = node.get('child_wait') or node.get('waiting')
    return None


_ACTION_LABELS = {
    'verification': '需要在设备上确认操作结果',
    'human_assistance': '需要人工处理（如登录或页面确认）',
}


def publish_runner_needs_action_in_tx(cursor, runner, config=None):
    """scheduled 非提问类等待停车通知（Phase 3，设计 §8.1）。

    仅确需用户动作的等待（verification/human_assistance）生产；event_key 按
    (runner, wait 标识) 幂等——同一次停车的重复 park 不重复通知，恢复后再次
    停车产生新通知。"""
    if config is None:
        from src.config.settings import settings
        config = settings.notifications
    if runner['status'] != 'waiting' or runner['source'] != 'scheduled':
        return
    waiting_leaf = _needs_action_leaf((runner.get('public_snapshot') or {}).get('waiting'))
    if waiting_leaf is None:
        return
    subject = Subject(runner['user_id'], runner['tenant_id'])
    if subject.scope_key != runner['scope_key']:
        raise ValueError('NOTIFICATION_RUNNER_SCOPE_MISMATCH')
    parameters = _scheduled_parameters(cursor, runner, 'needs_action')
    if parameters is None:
        return
    wait_id = (waiting_leaf.get('wait_id') or waiting_leaf.get('assistance_id')
               or waiting_leaf.get('invocation_id') or runner['runner_id'])
    parameters['action_label'] = _ACTION_LABELS.get(waiting_leaf['kind'], '需要人工处理')[:120]
    from .events import publish_in_tx
    occurred = runner['updated_at']
    return publish_in_tx(cursor, subject=subject, producer='scheduler',
        event_type='task.needs_action',
        event_key=f'needs-action:{runner["runner_id"]}:{wait_id}',
        resource_ref={'type': 'runner_waiting', 'id': runner['runner_id']},
        parameters=parameters,
        occurred_at=occurred, accepted_at=runner.get('accepted_at'),
        expires_at=occurred + timedelta(seconds=config.ttl_seconds))
