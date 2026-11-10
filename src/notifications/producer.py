"""Cursor-only producer at the original Runner terminal transaction tail."""
from datetime import timedelta
import json
import uuid

from .contracts import (PLATFORM_RUNNER_POLICY_KEY, SCHEMA_VERSION, Subject,
                        session_title)


def publish_platform_runner_alert_in_tx(cursor, *, runner, duration_seconds):
    """Publish one global event inside the caller's locked Runner transaction."""
    runner_id, attempt = runner['runner_id'], runner['attempt']
    if runner['execution_started_at'] is None or attempt < 1 or duration_seconds < 0:
        raise ValueError('RUNNER_ALERT_NOT_ACTIVE')
    cursor.execute('''SELECT status,attempt,tenant_id,profile_id,execution_started_at FROM agent_runners
        WHERE runner_id=%s FOR UPDATE''', (runner_id,))
    current = cursor.fetchone()
    if (not current or current['status'] not in ('running', 'finalizing')
            or current['attempt'] != attempt
            or current['tenant_id'] != runner.get('tenant_id')
            or current['profile_id'] != runner['profile_id']
            or current['execution_started_at'] != runner['execution_started_at']):
        raise ValueError('RUNNER_ALERT_NOT_ACTIVE')
    from .events import EVENTS
    from .repository import protected
    from src.config.settings import settings
    resource = {'type': 'runner_alert', 'id': runner_id}
    definition, parameters = EVENTS.validate('runner.long_running', {
        'runner_id': runner_id, 'tenant_id': runner.get('tenant_id'),
        'profile_id': runner['profile_id'], 'duration_seconds': duration_seconds,
    }, resource)
    cursor.execute('SELECT clock_timestamp() AS now')
    now = cursor.fetchone()['now']
    event_key = f'{runner_id}:{attempt}'
    cursor.execute('''INSERT INTO notification_messages
        (notification_id,notification_ref,schema_version,producer,event_type,event_key,
         scope_key,tenant_id,recipient_user_id,resource_ref,template_key,template_version,
         parameters_encrypted,occurred_at,accepted_at,expires_at,category,priority,policy_key)
        VALUES(%s,%s,%s,'runner-monitor','runner.long_running',%s,
            'global',NULL,NULL,%s::jsonb,%s,1,%s,%s,%s,%s,'system',0,%s)
        ON CONFLICT(scope_key,producer,event_key) WHERE recipient_user_id IS NULL DO NOTHING
        RETURNING notification_ref''',
        ('notification_'+uuid.uuid4().hex, uuid.uuid4().hex, SCHEMA_VERSION, event_key,
         json.dumps(resource), definition.template_key, protected(parameters), now,
         runner.get('accepted_at') or now, now+timedelta(seconds=settings.notifications.ttl_seconds),
         PLATFORM_RUNNER_POLICY_KEY))
    created = cursor.fetchone()
    if created:
        return created['notification_ref']
    cursor.execute('''SELECT notification_ref FROM notification_messages
        WHERE scope_key='global' AND tenant_id IS NULL AND recipient_user_id IS NULL
          AND producer='runner-monitor' AND event_type='runner.long_running'
          AND policy_key=%s AND event_key=%s''', (PLATFORM_RUNNER_POLICY_KEY, event_key))
    existing = cursor.fetchone()
    if existing is None:
        raise ValueError('RUNNER_ALERT_EVENT_IDENTITY_CONFLICT')
    return existing['notification_ref']


def _user_owned_web_runner(runner):
    """与 task.completed/failed 同一口径：chat/web 来源、用户本人触发的 runner。

    scheduled 来源（任务属主即 runner.user_id，Phase 3 扩展）同样适用。"""
    return (runner['source'] in ('chat', 'scheduled') and runner['session_kind'] == 'web'
            and bool(runner.get('user_id'))
            and (runner['source'] != 'chat' or (
                runner['actor_kind'] == 'user' and runner['actor_id'] == runner['user_id'])))


def _wecom_daily_terminal_extras(cursor, runner, task, event_kind):
    """企微日报计划终态附加参数（wecom-conversation 设计 §5.4，评审 P1-12）。

    仅识别本模块创建的计划标记（task_prompt 首行）。日期与完成/缺失事实取
    **本次执行持久化**的 checkpoint.resources.wecom_daily_result（业务分支在
    保存报告后写入），不按通知生成时间猜测"昨天"、不数历史库存报告；
    业务前失败时仅从已受理 input 取日期，不虚构完成数量。链接为服务端构造
    的日报页面路径，不取模型输出。
    """
    try:
        from src.wecom_conversation.plans import parse_plan_prompt
        config = parse_plan_prompt((task or {}).get('task_prompt') or '')
    except Exception:
        return {}
    if not config or config.get('kind') != 'daily':
        return {}
    task_id = (task or {}).get('task_id')
    if not task_id:
        return {}
    result = (((runner.get('checkpoint') or {}).get('execution') or {})
              .get('resources', {}).get('wecom_daily_result'))
    result = result if isinstance(result, dict) else {}
    accepted = ((runner.get('input') or {}).get('request_data') or {}).get(
        'wecom_conversation_business')
    accepted = accepted if isinstance(accepted, dict) else {}
    business_date = result.get('business_date') or accepted.get('business_date')
    if not isinstance(business_date, str) or len(business_date) != 10:
        return {}
    completed = [item.get('name') for item in result.get('completed') or []
                 if isinstance(item, dict) and item.get('name')]
    failed = [item for item in result.get('failed') or []
              if isinstance(item, dict) and item.get('name')]
    superseded = [item for item in result.get('superseded') or []
                  if isinstance(item, dict) and item.get('name')]
    extras = {'daily_report_date': business_date,
              'daily_report_link': (f'/t/{runner["tenant_id"]}/wecom-conversations/daily'
                                    f'?date={business_date}&task_id={task_id}')}
    if event_kind == 'completed' and result:
        extras['daily_report_summary'] = f'本次已生成 {len(completed)} 个会话日报'
        if failed or superseded:
            # 正常部分失败（整体 completed、事实含 failed 明细）不能让通知生产
            # 抛 TypeError（通知生产位于 Runner 终态事务内，评审二 P1-4）。
            details = []
            if failed:
                names = '、'.join(str(item.get('name') or '?') for item in failed)[:80]
                details.append(f'本次未完成 {len(failed)} 个会话：{names}')
            if superseded:
                names = '、'.join(str(item.get('name') or '?') for item in superseded)[:80]
                details.append(f'已有更新日报，本次未覆盖 {len(superseded)} 个会话：{names}')
            extras['daily_report_missing'] = '；'.join(details)[:200]
    elif failed:
        # 全部失败（终态 failed）：失败通知本身表达结果，仍附日期链接便于
        # 查看旧日报；不虚构"已生成"。
        names = '、'.join(str(item.get('name') or '?') for item in failed)[:120]
        extras['daily_report_missing'] = f'本次未完成 {len(failed)} 个会话：{names}'
    return extras


def _scheduled_parameters(cursor, runner, event_kind, *, include_task=False):
    """scheduled 事件参数：会话标题（=任务名）+ session_id/task_id/绑定快照
    （设计 §8.2：notify_rule_id/notify_target_id 在事件生成时从任务行快照，
    规则优先、目标仅存量任务兜底，加密存储不可变）。"""
    cursor.execute('''SELECT title FROM chat_sessions WHERE session_id=%s AND user_id=%s
        AND tenant_id IS NOT DISTINCT FROM %s''', (runner['session_id'], runner['user_id'],
                                                   runner['tenant_id']))
    session = cursor.fetchone()
    if not session:
        return None
    cursor.execute('''SELECT task_id,session_id,tenant_id,user_id,notification_rule_id,
        notification_target_id,name,task_prompt FROM scheduled_tasks
        WHERE session_id=%s AND user_id=%s AND tenant_id=%s''',
        (runner['session_id'], runner['user_id'], runner['tenant_id']))
    task = cursor.fetchone()
    if not task:
        return None
    scheduled_meta = ((runner.get('input') or {}).get('request_data') or {}).get('scheduled') or {}
    parameters = {'session_title': session_title(session['title']),
                  'event_kind': event_kind,
                  'task_id': str(scheduled_meta.get('task_id') or (task or {}).get('task_id') or '')[:64],
                  'task_name': session_title((task or {}).get('name') or session['title'])}
    if event_kind in ('completed', 'failed'):
        parameters.update(_wecom_daily_terminal_extras(cursor, runner, task, event_kind))
    rule_id = (task or {}).get('notification_rule_id')
    target_id = (task or {}).get('notification_target_id')
    if rule_id:
        parameters['notify_rule_id'] = str(rule_id)[:128]
    elif target_id:
        parameters['notify_target_id'] = str(target_id)[:128]
    return (parameters, task) if include_task else parameters


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
        scheduled = _scheduled_parameters(cursor, runner, event_kind, include_task=True)
        if scheduled is None:
            return  # Deleted resources have no recipient-readable result.
        parameters, task = scheduled
        from src.services.task_result_sharing.service import ensure_task_result_share_in_tx

        ensure_task_result_share_in_tx(cursor, task, runner)
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
