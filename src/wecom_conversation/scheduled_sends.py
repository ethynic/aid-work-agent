"""Dedicated WeCom schedule definitions and their durable per-conversation queue."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from loguru import logger
from psycopg2.extras import execute_values

from src.db.database import get_db_connection
from src.scheduler.schedule import compute_next_due, parse_run_at
from src.scheduler.special_plans import WECOM_SCHEDULED_SEND_MARKER, first_line_sql

from . import proxy_tasks
from .service import ConversationServiceError, add_verified_conversations_in_tx

MARKER = WECOM_SCHEDULED_SEND_MARKER
TIMEZONE = ZoneInfo('Asia/Shanghai')
SEND_WINDOW = timedelta(minutes=10)
WEEKDAYS = ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')


def local_time(value: datetime) -> datetime:
    """Convert the legacy schedule columns' time to naive Beijing wall time."""
    return value.astimezone(TIMEZONE).replace(tzinfo=None) if value.tzinfo is not None else value


def _database_now(cursor, override: datetime | None) -> datetime:
    if override is not None:
        return local_time(override)
    cursor.execute("SELECT clock_timestamp() AT TIME ZONE 'Asia/Shanghai' AS wall_now")
    return cursor.fetchone()['wall_now']


def _error(code, message, status=409):
    return ConversationServiceError(code, message, status)


def _time_fields(schedule_type, values):
    if not isinstance(values, dict):
        raise _error('SCHEDULE_INVALID', '请填写发送时间', 400)
    if schedule_type == 'once' and set(values) == {'run_at'}:
        due = parse_run_at(values['run_at']) if isinstance(values['run_at'], str) else None
        if due is not None:
            return None, due
    elif schedule_type in ('daily', 'weekly'):
        expected = {'hour', 'minute', 'weekdays'} if schedule_type == 'weekly' else {'hour', 'minute'}
        hour, minute = values.get('hour'), values.get('minute')
        if (set(values) == expected and type(hour) is int and 0 <= hour <= 23
                and type(minute) is int and 0 <= minute <= 59):
            if schedule_type == 'daily':
                return f'{minute} {hour} * * *', None
            days = values['weekdays']
            if isinstance(days, list) and days and all(day in WEEKDAYS for day in days):
                cron_days = ','.join(str(index) for index, day in enumerate(WEEKDAYS) if day in days)
                return f'{minute} {hour} * * {cron_days}', None
    raise _error('SCHEDULE_INVALID', '发送时间须为一次性、每天或每周的有效北京时间', 400)


def _definition(device_id, account, sessions, text):
    text = proxy_tasks.validate_opening_message(text, field_name='定时发送消息')
    if text is None:
        raise _error('VALIDATION_FAILED', '请输入定时发送消息', 400)
    return {'version': 1, 'device_id': str(device_id), 'account': account,
            'sessions': proxy_tasks._validate_sessions(sessions), 'text': text}


def _prompt(definition):
    return MARKER + '\n' + json.dumps(definition, ensure_ascii=False, separators=(',', ':'))


def _read_definition(task):
    first, _, body = task['task_prompt'].partition('\n')
    try:
        value = json.loads(body) if first.strip() == MARKER else None
    except ValueError:
        value = None
    if (not isinstance(value, dict) or value.get('version') != 1
            or set(value) != {'version', 'device_id', 'account', 'sessions', 'text'}):
        raise _error('SCHEDULE_INVALID', '企微定时发送定义无效')
    return value


def _plan(task):
    definition = _read_definition(task)
    result = {key: task[key] for key in ('task_id', 'name', 'schedule_type', 'status',
                                        'next_due_at', 'created_at', 'updated_at')}
    if task['schedule_type'] == 'once':
        result['time_config'] = {'run_at': task['run_at']}
    else:
        minute, hour, _, _, days = task['cron_expression'].split()
        result['time_config'] = {'hour': int(hour), 'minute': int(minute)}
        if task['schedule_type'] == 'weekly':
            result['time_config']['weekdays'] = [WEEKDAYS[int(day)] for day in days.split(',')]
    return {**result, **{key: definition[key] for key in ('device_id', 'account', 'sessions', 'text')}}


def _owned_plan(cursor, tenant_id, user_id, task_id, *, lock=False):
    cursor.execute(
        f"""SELECT * FROM scheduled_tasks WHERE tenant_id=%s AND user_id=%s AND task_id=%s
               AND status<>'deleted' AND {first_line_sql()} LIKE %s""" + (' FOR UPDATE' if lock else ''),
        (tenant_id, user_id, task_id, MARKER + '%'))
    return cursor.fetchone()


def _require_owned(cursor, tenant_id, user_id, task_id):
    task = _owned_plan(cursor, tenant_id, user_id, task_id, lock=True)
    if task is None:
        raise _error('PLAN_NOT_FOUND', '企微定时发送配置不存在或无权访问', 404)
    return task


def _validate_targets(cursor, tenant_id, user_id, definition):
    proxy_tasks._validated_targets(
        cursor, tenant_id, user_id, definition['sessions'],
        account=definition['account'], device_id=definition['device_id'])


def _register_verified_targets(conn, cursor, tenant_id, user_id, definition, targets):
    from src.local_tools.repository import lock_selected_device

    device_id = definition['device_id']
    if not lock_selected_device(cursor, tenant_id, user_id, device_id):
        raise _error('DEVICE_MISMATCH', '选定设备已改变，请重新选择会话')
    add_verified_conversations_in_tx(conn, tenant_id, user_id, targets,
                                    account=definition['account'], device_id=device_id)


def _device_group(task, definition):
    return (task['tenant_id'], definition['device_id'], definition['account'])


def _group_task(group):
    return dict(zip(('tenant_id', 'device_id', 'wecom_account_id'), group, strict=True))


def create_plan(tenant_id, user_id, *, device_id, account, name, sessions, text,
                schedule_type, time_config, verified_targets=None):
    """Bind a trusted directory/device selection from the dedicated authenticated entry."""
    definition = _definition(device_id, account, sessions, text)
    name = proxy_tasks._validate_title(name)
    cron, run_at = _time_fields(schedule_type, time_config)
    task_id = f'sched_{uuid4().hex}'
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if verified_targets is not None:
            _register_verified_targets(conn, cursor, tenant_id, user_id, definition, verified_targets)
        _validate_targets(cursor, tenant_id, user_id, definition)
        cursor.execute(
            """INSERT INTO scheduled_tasks (task_id, tenant_id, user_id, name, task_prompt,
                    schedule_type, cron_expression, run_at, session_id, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'paused') RETURNING *""",
            (task_id, tenant_id, user_id, name, _prompt(definition), schedule_type, cron, run_at,
             f'wecom_scheduled_plan:{task_id}'))
        task = _plan(cursor.fetchone())
        conn.commit()
        return task


def get_plan(tenant_id, user_id, task_id):
    with get_db_connection() as conn:
        task = _owned_plan(conn.cursor(), tenant_id, user_id, task_id)
        return _plan(task) if task else None


def list_plans(tenant_id, user_id, *, limit=50, offset=0):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f"""SELECT * FROM scheduled_tasks WHERE tenant_id=%s AND user_id=%s
                  AND status<>'deleted' AND {first_line_sql()} LIKE %s
                ORDER BY id DESC LIMIT %s OFFSET %s""",
            (tenant_id, user_id, MARKER + '%', min(limit, 100), offset))
        return [_plan(task) for task in cursor.fetchall()]


def edit_plan(tenant_id, user_id, task_id, *, name, sessions, text, schedule_type, time_config,
              expected_sessions=None, verified_targets=None):
    cron, run_at = _time_fields(schedule_type, time_config)
    name = proxy_tasks._validate_title(name)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        task = _require_owned(cursor, tenant_id, user_id, task_id)
        if task['status'] != 'paused':
            raise _error('PLAN_STATE_CONFLICT', '请先暂停计划后再编辑')
        previous = _read_definition(task)
        if expected_sessions is not None and previous['sessions'] != expected_sessions:
            raise _error('PLAN_CHANGED', '会话配置已更新，请重新打开编辑')
        definition = _definition(previous['device_id'], previous['account'], sessions, text)
        if verified_targets is not None:
            _register_verified_targets(conn, cursor, tenant_id, user_id, definition, verified_targets)
        added = [locator for locator in definition['sessions'] if locator not in previous['sessions']]
        if added:
            _validate_targets(cursor, tenant_id, user_id, {**definition, 'sessions': added})
        cursor.execute(
            """UPDATE scheduled_tasks SET name=%s,task_prompt=%s,schedule_type=%s,
                    cron_expression=%s,run_at=%s,next_due_at=NULL,updated_at=now()
                 WHERE task_id=%s RETURNING *""",
            (name, _prompt(definition), schedule_type, cron, run_at, task_id))
        edited = _plan(cursor.fetchone())
        conn.commit()
        return edited


def enable_plan(tenant_id, user_id, task_id, *, now=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        task = _require_owned(cursor, tenant_id, user_id, task_id)
        if task['status'] != 'paused':
            raise _error('PLAN_STATE_CONFLICT', '只有已暂停的计划可以启用')
        now = _database_now(cursor, now)
        next_due = task['run_at'] if task['schedule_type'] == 'once' else compute_next_due(task, now)
        if next_due is None or next_due <= now:
            raise _error('SCHEDULE_INVALID', '一次性发送时间已过，请修改为未来时间')
        cursor.execute(
            """UPDATE scheduled_tasks SET status='active',next_due_at=%s,pause_reason=NULL,updated_at=now()
                 WHERE task_id=%s RETURNING *""", (next_due, task_id))
        enabled = _plan(cursor.fetchone())
        conn.commit()
        return enabled


def _stop_plan(tenant_id, user_id, task_id, *, delete):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        task = _require_owned(cursor, tenant_id, user_id, task_id)
        if not delete and task['status'] not in ('active', 'completed', 'expired'):
            raise _error('PLAN_STATE_CONFLICT', '当前计划不能暂停')
        group = _device_group(task, _read_definition(task))
        proxy_tasks.lock_device_group_in_tx(cursor, _group_task(group))
        from .scheduled_delivery import retire_in_tx

        retire_in_tx(cursor, 'tenant_id=%s AND user_id=%s AND task_id=%s',
                     (tenant_id, user_id, task_id), 'cancelled', 'PLAN_DELETED' if delete else 'PLAN_PAUSED')
        cursor.execute(
            """UPDATE scheduled_tasks SET status=%s,next_due_at=NULL,updated_at=now()
                 WHERE task_id=%s RETURNING *""", ('deleted' if delete else 'paused', task_id))
        updated = _plan(cursor.fetchone())
        conn.commit()
        return updated


def pause_plan(tenant_id, user_id, task_id):
    return _stop_plan(tenant_id, user_id, task_id, delete=False)


def delete_plan(tenant_id, user_id, task_id):
    return _stop_plan(tenant_id, user_id, task_id, delete=True)


def list_executions(tenant_id, user_id, *, task_id=None, limit=50, offset=0):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """SELECT id,task_id,plan_name,due_at,deadline_at,session,session_name,status,
                      started_at,finished_at,invocation_id,error_code
                 FROM bs_wecom_scheduled_sends
                WHERE tenant_id=%s AND user_id=%s AND (%s::text IS NULL OR task_id=%s)
                ORDER BY due_at DESC, plan_order, target_order LIMIT %s OFFSET %s""",
            (tenant_id, user_id, task_id, task_id, min(limit, 100), offset))
        return [dict(row) for row in cursor.fetchall()]


def freeze_due_in_tx(cursor, task, *, now=None):
    """Consume the locked plan occurrence directly into the WeCom queue."""
    definition = _read_definition(task)
    group = _device_group(task, definition)
    proxy_tasks.lock_device_group_in_tx(cursor, _group_task(group))
    wall_now = _database_now(cursor, now)
    due = task['next_due_at']
    occurrences = [due]
    next_due = compute_next_due(task, wall_now)
    if task['schedule_type'] != 'once' and wall_now > due + SEND_WINDOW:
        recent = compute_next_due(task, wall_now - SEND_WINDOW - timedelta(microseconds=1))
        if due < recent <= wall_now:
            occurrences.append(recent)

    sessions = definition['sessions']
    cursor.execute(
        """SELECT session_locator,name FROM bs_wecom_conversations
            WHERE tenant_id=%s AND user_id=%s AND device_id=%s AND source_account_id=%s
              AND session_locator=ANY(%s)""",
        (task['tenant_id'], task['user_id'], definition['device_id'], definition['account'], sessions))
    names = {row['session_locator']: row['name'] for row in cursor.fetchall()}
    records = []
    for occurrence in occurrences:
        expired = wall_now > occurrence + SEND_WINDOW
        for order, session in enumerate(sessions):
            records.append((task['tenant_id'], task['user_id'], task['task_id'], task['id'], task['name'],
                            occurrence.replace(tzinfo=TIMEZONE),
                            (occurrence + SEND_WINDOW).replace(tzinfo=TIMEZONE), order,
                            definition['device_id'], definition['account'], session, names.get(session),
                            definition['text'], 'expired' if expired else 'queued',
                            wall_now.replace(tzinfo=TIMEZONE) if expired else None,
                            'SEND_WINDOW_EXPIRED' if expired else None))
    inserted = execute_values(
        cursor,
        """INSERT INTO bs_wecom_scheduled_sends
                   (tenant_id,user_id,task_id,plan_order,plan_name,due_at,deadline_at,target_order,
                    device_id,account,session,session_name,text,status,finished_at,error_code)
            VALUES %s ON CONFLICT (tenant_id,task_id,due_at,session) DO NOTHING
            RETURNING id,error_code""", records, fetch=True)
    for row in inserted:
        if row['error_code']:
            logger.info(f'后端日志：企微定时消息到期未派发 execution={row["id"]} tenant={task["tenant_id"]} '
                        f'code={row["error_code"]}')
    status = 'expired' if task['schedule_type'] == 'once' and wall_now > due + SEND_WINDOW else 'active'
    cursor.execute(
        """UPDATE scheduled_tasks SET next_due_at=%s,status=%s,last_run_at=%s,updated_at=now()
             WHERE task_id=%s""", (next_due, status, occurrences[-1], task['task_id']))


def group_has_turn_in_tx(cursor, group, *, include_queued, now=None):
    """Scheduled occupancy under the shared device group lock."""
    at = _database_now(cursor, now).replace(tzinfo=TIMEZONE)
    cursor.execute(
        """SELECT 1 FROM bs_wecom_scheduled_sends
            WHERE tenant_id=%s AND device_id=%s AND account=%s
              AND (status IN ('dispatched','running')
                   OR (status IN ('unknown','succeeded') AND released_at IS NULL)
                   OR (%s AND status='queued' AND deadline_at >= %s))
            LIMIT 1""", (*group, include_queued, at))
    return cursor.fetchone() is not None


def next_execution_in_tx(cursor, group, *, now=None):
    """Hold the device group fence while the caller selects and binds the queue turn."""
    task = _group_task(group)
    proxy_tasks.lock_device_group_in_tx(cursor, task)
    if (proxy_tasks.device_group_busy_in_tx(cursor, task, include_pending_opening=False)
            or group_has_turn_in_tx(cursor, group, include_queued=False, now=now)):
        return None
    at = _database_now(cursor, now).replace(tzinfo=TIMEZONE)
    cursor.execute(
        """SELECT * FROM bs_wecom_scheduled_sends
            WHERE tenant_id=%s AND device_id=%s AND account=%s
              AND status='queued' AND deadline_at >= %s
            ORDER BY due_at, plan_order, target_order LIMIT 1""", (*group, at))
    row = cursor.fetchone()
    return dict(row) if row else None
