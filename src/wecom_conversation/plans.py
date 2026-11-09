"""企微每日总结专属计划配置及 Scheduler 认领时的受信范围快照。"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .adapters import DEFAULT_TIMEZONE
from src.scheduler.special_plans import first_line_sql

DAILY_MARKER = '[wecom-conversation:daily]'
DESCRIPTOR_KEY = 'wecom_conversation_business'
PLAN_SCHEMA_VERSION = 2

OPS_SESSION_METADATA = {'wecom_conversation_ops': True}


def daily_plan_config(*, account, device, target_day, scope_mode):
    return {'schema_version': PLAN_SCHEMA_VERSION, 'account': account,
            'device': str(device), 'timezone': DEFAULT_TIMEZONE,
            'target_day': target_day, 'scope_mode': scope_mode}


def daily_plan_schedule(target_day, *, hour=None, minute=None, interval_hours=None):
    """Dedicated schedule semantics; ordinary interval tasks keep their own immediate first due."""
    from src.scheduler.schedule import ScheduleConfigError, compute_next_due

    now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).replace(tzinfo=None)
    if target_day == 'yesterday':
        hour = 2 if hour is None else hour
        minute = 0 if minute is None else minute
        if (isinstance(hour, bool) or isinstance(minute, bool)
                or not isinstance(hour, int) or not isinstance(minute, int)
                or not 0 <= hour <= 23 or not 0 <= minute <= 59
                or interval_hours is not None):
            raise ScheduleConfigError('昨天仅支持每日固定时刻（小时 0–23，分钟 0–59）')
        cron = f'{minute} {hour} * * *'
        due = compute_next_due({'schedule_type': 'daily', 'cron_expression': cron,
                                'next_due_at': None}, now)
        return {'schedule_type': 'daily', 'cron_expression': cron,
                'interval_seconds': None, 'next_due_at': due}
    if target_day == 'today':
        if (isinstance(interval_hours, bool) or not isinstance(interval_hours, int)
                or not 1 <= interval_hours <= 24 or hour is not None or minute is not None):
            raise ScheduleConfigError('今天仅支持 1–24 整数小时间隔')
        return {'schedule_type': 'interval', 'cron_expression': None,
                'interval_seconds': interval_hours * 3600,
                'next_due_at': now + timedelta(hours=interval_hours)}
    raise ScheduleConfigError('目标日期仅支持今天或昨天')


def get_daily_plan(tenant_id, user_id):
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'''SELECT * FROM scheduled_tasks WHERE tenant_id=%s AND user_id=%s
                 AND {first_line_sql()} LIKE %s
               ORDER BY created_at LIMIT 2''',
            (tenant_id, user_id, DAILY_MARKER + '%'))
        rows = [dict(row) for row in cursor.fetchall()]
    if len(rows) > 1:
        from .service import ConversationServiceError
        raise ConversationServiceError('PLAN_DUPLICATE', '存在多条企微每日总结计划，请联系管理员', 409)
    return rows[0] if rows else None


def save_daily_plan(tenant_id, user_id, *, create, account, device,
                    target_day, scope_mode, conversation_ids, hour=None,
                    minute=None, interval_hours=None, notification_rule_id=None,
                    notification_rule_supplied=False):
    """One dedicated row and selected membership in one tenant/user transaction."""
    from src.db.database import get_db_connection
    from src.db.models import generate_session_id
    from src.scheduler.db import ScheduledTaskDB
    from src.scheduler.schedule import ScheduleConfigError
    from .service import ConversationServiceError

    if scope_mode not in ('all', 'selected'):
        raise ConversationServiceError('PLAN_SCOPE_INVALID', '会话范围仅支持全部或自选', 400)
    if not isinstance(conversation_ids, list) or any(
            isinstance(item, bool) or not isinstance(item, int) or item <= 0
            for item in conversation_ids):
        raise ConversationServiceError('PLAN_SCOPE_INVALID', '会话 ID 清单无效', 400)
    selected_ids = list(dict.fromkeys(conversation_ids))
    if scope_mode == 'selected' and not selected_ids:
        raise ConversationServiceError('PLAN_SCOPE_EMPTY', '请至少选择一个会话', 400)
    if scope_mode == 'all' and selected_ids:
        raise ConversationServiceError('PLAN_SCOPE_INVALID', '全部会话模式不接收自选清单', 400)
    if notification_rule_id is not None and (
            not isinstance(notification_rule_id, str) or not notification_rule_id):
        raise ConversationServiceError('NOTIFICATION_RULE_INVALID', '通知规则 ID 无效', 400)
    if notification_rule_id and not ScheduledTaskDB.notify_rule_visible(
            notification_rule_id, tenant_id=tenant_id):
        raise ConversationServiceError('NOTIFICATION_RULE_INVALID', '通知规则不存在或不可用', 400)
    config = daily_plan_config(account=account, device=device,
                               target_day=target_day, scope_mode=scope_mode)
    prompt = DAILY_MARKER + json.dumps(config, ensure_ascii=False, separators=(',', ':'))
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                       (f'scheduled_tasks:special:{tenant_id}:{user_id}',))
        cursor.execute(
            f'''SELECT * FROM scheduled_tasks WHERE tenant_id=%s AND user_id=%s
                 AND {first_line_sql()} LIKE %s
               FOR UPDATE''', (tenant_id, user_id, DAILY_MARKER + '%'))
        existing_rows = [dict(row) for row in cursor.fetchall()]
        if len(existing_rows) > 1:
            raise ConversationServiceError('PLAN_DUPLICATE', '存在多条企微每日总结计划，请联系管理员', 409)
        existing = existing_rows[0] if existing_rows else None
        if create and existing:
            raise ConversationServiceError('PLAN_EXISTS', '每日总结计划已存在，请编辑现有计划', 409)
        if not create and not existing:
            raise ConversationServiceError('PLAN_NOT_FOUND', '每日总结计划不存在', 404)
        if existing:
            old = parse_plan_prompt(existing['task_prompt']) or {}
            if (old.get('schema_version') != PLAN_SCHEMA_VERSION
                    or old.get('account') != account or str(old.get('device')) != str(device)):
                raise ConversationServiceError('PLAN_SOURCE_MISMATCH', '计划已绑定其他账号或设备，未更换来源', 409)
            if existing['status'] not in ('active', 'paused'):
                raise ConversationServiceError('PLAN_STATE_CONFLICT', '计划当前状态不可编辑', 409)
        if selected_ids:
            cursor.execute(
                '''SELECT id FROM bs_wecom_conversations
                   WHERE tenant_id=%s AND user_id=%s AND source_account_id=%s
                     AND device_id=%s AND id=ANY(%s::bigint[])''',
                (tenant_id, user_id, account, str(device), selected_ids))
            if {row['id'] for row in cursor.fetchall()} != set(selected_ids):
                raise ConversationServiceError('PLAN_SCOPE_INVALID', '自选会话不属于当前账号或设备', 409)
        try:
            schedule = daily_plan_schedule(target_day, hour=hour, minute=minute,
                                           interval_hours=interval_hours)
        except ScheduleConfigError as error:
            raise ConversationServiceError('SCHEDULE_INVALID', str(error), 400) from error
        if existing:
            task_id = existing['task_id']
            effective_rule = (notification_rule_id if notification_rule_supplied
                              else existing.get('notification_rule_id'))
            cursor.execute(
                '''UPDATE scheduled_tasks SET task_prompt=%s, schedule_type=%s,
                       cron_expression=%s, interval_seconds=%s, next_due_at=%s,
                       notification_rule_id=%s, updated_at=now()
                   WHERE task_id=%s RETURNING *''',
                (prompt, schedule['schedule_type'], schedule['cron_expression'],
                 schedule['interval_seconds'], schedule['next_due_at'],
                 effective_rule, task_id))
            task = dict(cursor.fetchone())
        else:
            task_id = f'sched_{uuid.uuid4().hex[:12]}'
            session_id = generate_session_id()
            name = '企微每日总结'
            cursor.execute(
                '''INSERT INTO chat_sessions
                   (session_id,user_id,tenant_id,title,metadata)
                   VALUES (%s,%s,%s,%s,%s::jsonb)''',
                (session_id, user_id, tenant_id, name,
                 json.dumps({'scheduled_task': task_id})))
            cursor.execute(
                '''INSERT INTO scheduled_tasks
                   (task_id,tenant_id,user_id,name,task_prompt,attachments,
                    profile_id,instance_id,schedule_type,cron_expression,
                    interval_seconds,session_id,notification_rule_id,status,next_due_at)
                   VALUES (%s,%s,%s,%s,%s,'[]'::jsonb,'main',NULL,%s,%s,%s,%s,%s,'active',%s)
                   RETURNING *''',
                (task_id, tenant_id, user_id, name, prompt, schedule['schedule_type'],
                 schedule['cron_expression'], schedule['interval_seconds'],
                 session_id, notification_rule_id, schedule['next_due_at']))
            task = dict(cursor.fetchone())
        cursor.execute(
            '''UPDATE bs_wecom_conversations SET daily_plan_task_id=NULL, updated_at=now()
               WHERE tenant_id=%s AND user_id=%s AND daily_plan_task_id=%s''',
            (tenant_id, user_id, task_id))
        if selected_ids:
            cursor.execute(
                '''UPDATE bs_wecom_conversations SET daily_plan_task_id=%s, updated_at=now()
                   WHERE tenant_id=%s AND user_id=%s AND id=ANY(%s::bigint[])''',
                (task_id, tenant_id, user_id, selected_ids))
        conn.commit()
    return task


def business_session_id(tenant_id, user_id, purpose, key):
    """每类任务独立会话（幂等重试复用同一会话）。"""
    digest = hashlib.sha1(f'{tenant_id}|{user_id}|{purpose}|{key}'.encode('utf-8')).hexdigest()[:24]
    return f'wecom_{purpose}_{digest}'


def _ensure_session(cursor, session_id, tenant_id, user_id, title='企微会话业务操作'):
    cursor.execute(
        '''INSERT INTO chat_sessions (session_id, user_id, tenant_id, title, metadata)
           VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT (session_id) DO NOTHING''',
        (session_id, user_id, tenant_id, title, json.dumps(OPS_SESSION_METADATA)))


def _ensure_session_committed(session_id, tenant_id, user_id, title='企微会话业务操作'):
    from src.db.database import get_db_connection
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _ensure_session(cursor, session_id, tenant_id, user_id, title)
        conn.commit()


def parse_plan_prompt(text):
    """读取日报首行配置；退役 backfill 和无效 JSON 不生成业务描述。"""
    if not isinstance(text, str):
        return None
    first_line, _, rest = text.partition('\n')
    first_line = first_line.strip()
    for marker, kind in ((DAILY_MARKER, 'daily'),):
        if first_line.startswith(marker):
            try:
                config = json.loads(first_line[len(marker):] or '{}')
            except ValueError:
                return None
            if not isinstance(config, dict):
                return None
            descriptor = {'kind': kind, **config}
            requirements = rest.strip()
            if requirements:
                descriptor['requirements'] = requirements
            return descriptor
    return None


def freeze_scheduled_plan_in_tx(cursor, task):
    """Freeze a dedicated plan's date and scope while claim_due holds its row lock."""
    if not isinstance(task.get('task_prompt'), str) or not task['task_prompt'].partition('\n')[0].strip().startswith(DAILY_MARKER):
        return None, None
    config = parse_plan_prompt(task['task_prompt']) or {}
    if (config.get('schema_version') != PLAN_SCHEMA_VERSION
            or config.get('kind') != 'daily'
            or config.get('target_day') not in ('today', 'yesterday')
            or config.get('scope_mode') not in ('all', 'selected')
            or config.get('timezone') != DEFAULT_TIMEZONE
            or not isinstance(config.get('account'), str) or not config['account']
            or not isinstance(config.get('device'), str) or not config['device']):
        return None, 'definition_invalid'
    cursor.execute("SELECT (clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date AS business_day")
    business_day = cursor.fetchone()['business_day']
    if config['target_day'] == 'yesterday':
        business_day -= timedelta(days=1)
    conditions = ['tenant_id=%s', 'user_id=%s', 'source_account_id=%s', 'device_id=%s']
    params = [task['tenant_id'], task['user_id'], config['account'], config['device']]
    if config['scope_mode'] == 'selected':
        conditions.append('daily_plan_task_id=%s')
        params.append(task['task_id'])
    cursor.execute(f'''SELECT id FROM bs_wecom_conversations
                   WHERE {' AND '.join(conditions)} ORDER BY id''', tuple(params))
    ids = [row['id'] for row in cursor.fetchall()]
    descriptor = {'schema_version': PLAN_SCHEMA_VERSION, 'kind': 'daily',
                  'trigger': 'scheduled', 'task_id': task['task_id'],
                  'business_date': business_day.isoformat(),
                  'timezone': DEFAULT_TIMEZONE, 'account': config['account'],
                  'device': config['device'], 'conversation_ids': ids}
    return descriptor, 'scope_empty' if not ids else None


def plan_prompt(kind, config, requirements=''):
    """手动日报既有文本构造；专属定时配置由 save_daily_plan 写入。"""
    if kind != 'daily':
        raise ValueError('退役的补录任务不可创建')
    header = DAILY_MARKER + json.dumps(config, ensure_ascii=False, separators=(',', ':'))
    body = (requirements or '').strip()
    return header + ('\n' + body if body else '')


def _runner_client():
    from src.services.agent_runner.web_client import RunnerServiceClient
    from src.config.settings import settings
    return RunnerServiceClient(settings.agent_runner)


async def submit_manual_report(authorization, tenant_id, user_id, *, conversation_ids,
                               account, device_id, business_date, client_request_id=None):
    """Submit one server-verified today's scope as one main-profile Runner."""
    config = {'schema_version': PLAN_SCHEMA_VERSION, 'kind': 'daily', 'trigger': 'manual',
              'business_date': business_date, 'timezone': DEFAULT_TIMEZONE,
              'account': account, 'device': str(device_id),
              'conversation_ids': list(conversation_ids)}
    request_id = client_request_id or str(uuid.uuid4())
    session_id = business_session_id(tenant_id, user_id, 'daily', request_id)
    _ensure_session_committed(session_id, tenant_id, user_id, '企微会话日报生成')
    body = {'source': 'chat', 'session': {'kind': 'web', 'session_id': session_id},
            'client_request_id': request_id, 'text': '更新今日企业微信会话总结',
            'profile_id': 'main', 'routing_policy': 'explicit',
            'request_data': {DESCRIPTOR_KEY: config, 'wecom_task_device': str(device_id)}}
    client = _runner_client()
    value = await client.request('POST', '/v1/runners', authorization=authorization,
                                 tenant=tenant_id, body=body, accept_new=True)
    runner = value.get('runner') or {}
    return {'runner_id': runner.get('runner_id'), 'created': value.get('created'),
            'status': runner.get('status'), 'session_id': session_id}
