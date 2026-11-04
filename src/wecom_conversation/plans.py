"""企微会话补录/日报计划的任务提交编排（设计 §3.3/§5.1/§5.4）。

补录与日报计划都是现有 scheduled_tasks 行：计划/补录的任务定义首行携带机器
可读标记（``[wecom-conversation:daily]{...}``），其后为人类可读文本（含特殊
关注要求）。Scheduler 派发时由 ``augment_submission`` 注入受信业务描述
（request_data.wecom_conversation_business，含标记行配置与要求文本），
RuntimeExecution 据此进入局部业务适配。普通任务不含标记、行为不变；不新增
execution_mode、Runner 类型或调度队列（设计 §5.1）。

- 补录（backfill）：一次性任务，只读当天＋零点前 100 条背景，不调用总结模型；
- 日报计划（daily）：周期任务管理同来源多个会话，逐会话读取→分析→保存，
  复用原定时任务终态通知（一条通知带日报日期链接，通知接点见 notifications）。
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

from .adapters import DEFAULT_TIMEZONE

DAILY_MARKER = '[wecom-conversation:daily]'
BACKFILL_MARKER = '[wecom-conversation:backfill]'
DESCRIPTOR_KEY = 'wecom_conversation_business'

OPS_SESSION_METADATA = {'wecom_conversation_ops': True}


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
    """计划任务定义首行标记 → 业务描述 dict；非本模块任务返回 None。

    首行格式：``[wecom-conversation:daily]{"timezone":...}``；标记后为紧凑
    JSON。首行之后的文本为人类可读的特殊关注要求，以 requirements 字段
    进入业务描述（评审 P2-15）。解析失败按普通任务处理。
    """
    if not isinstance(text, str):
        return None
    first_line, _, rest = text.partition('\n')
    first_line = first_line.strip()
    for marker, kind in ((DAILY_MARKER, 'daily'), (BACKFILL_MARKER, 'backfill')):
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


def augment_submission(body):
    """Scheduler 构造提交体时的受信业务描述注入（普通任务不受影响）。

    只识别本模块写入的任务定义标记；描述（含特殊关注要求）与原设备提示
    一起进入 request_data（web/调度提交方不可自由写 request_data，此路径
    为服务器受信构造）。
    """
    config = parse_plan_prompt(body.get('text') or '')
    if config is None:
        return
    scheduled = (body.get('request_data') or {}).get('scheduled') or {}
    if scheduled.get('task_id'):
        config['task_id'] = scheduled['task_id']
    request_data = dict(body.get('request_data') or {})
    request_data[DESCRIPTOR_KEY] = config
    if config.get('device'):
        request_data['wecom_task_device'] = str(config['device'])
    body['request_data'] = request_data


def plan_prompt(kind, config, requirements=''):
    """构造任务定义文本：首行机器标记 + 人类可读的特殊关注要求。"""
    marker = DAILY_MARKER if kind == 'daily' else BACKFILL_MARKER
    header = marker + json.dumps(config, ensure_ascii=False, separators=(',', ':'))
    body = (requirements or '').strip()
    return header + ('\n' + body if body else '')


def _assert_cron_at_most_hourly(cron):
    """cron 直传入口的粒度校验（§15 评审 P1）：与最短间隔约束对齐——
    连续触发间隔须 ≥1 小时（'* * * * *'、'*/30 * * * *' 等分钟级被拒，
    '0 * * * *' 每小时合法）。"""
    from apscheduler.triggers.cron import CronTrigger
    from zoneinfo import ZoneInfo
    from src.scheduler.schedule import TIMEZONE
    tz = ZoneInfo(TIMEZONE)
    try:
        trigger = CronTrigger.from_crontab(str(cron), timezone=tz)
        first = trigger.get_next_fire_time(None, datetime.now(tz))
        second = None if first is None else trigger.get_next_fire_time(
            None, first + timedelta(microseconds=1))
        if first is None or second is None:
            raise ValueError
    except ValueError as error:
        raise ValueError(
            f'cron_expression 不可解析或粒度过细：{cron}（须为至多每小时一次的'
            '日历规则，与定时任务最短间隔约束一致）') from error
    if (second - first).total_seconds() < 3600:
        raise ValueError(
            f'cron_expression 粒度过细：{cron}（连续触发间隔不足 1 小时，'
            '不支持分钟级循环）')


def _schedule_fields(schedule):
    """schedule 描述 → scheduled_tasks 调度列（评审 P2-22：对齐既有调度契约）。

    - cron_expression 直接指定：先过"至多每小时一次"粒度校验（§15 评审 P1：
      否则绕过整数小时规则可建分钟级循环），再按 compute_next_due 计算首次到期；
    - time_config：走 build_schedule_fields 完整校验（含整数小时与时间窗），
      起止时间随 fields 返回供 create 落库（§15 评审 P1：不丢窗口）；
    - once：run_at（北京时间 'YYYY-MM-DD HH:MM:SS'）按既有 parse_run_at 解析
      并要求未来时间；补录缺省 run_at 时立即到期。
    """
    from src.scheduler.schedule import build_schedule_fields, compute_next_due, parse_run_at
    schedule_type = schedule.get('schedule_type')
    cron = schedule.get('cron_expression')
    if schedule_type == 'once':
        run_at_text = schedule.get('run_at')
        if run_at_text:
            run_at = parse_run_at(str(run_at_text))
            if run_at is None:
                raise ValueError('run_at 须为 YYYY-MM-DD HH:MM:SS（北京时间）')
            if run_at <= datetime.now():
                raise ValueError('run_at 必须是未来时间')
        else:
            run_at = datetime.now() + timedelta(seconds=1)
        return {'schedule_type': 'once', 'cron_expression': None, 'interval_seconds': None,
                'run_at': run_at, 'next_due_at': run_at,
                'start_at': None, 'ends_at': None}
    if cron:
        _assert_cron_at_most_hourly(cron)
        next_due = compute_next_due({'schedule_type': schedule_type,
                                     'cron_expression': cron, 'next_due_at': None})
        return {'schedule_type': schedule_type, 'cron_expression': cron,
                'interval_seconds': None, 'run_at': None, 'next_due_at': next_due,
                'start_at': None, 'ends_at': None}
    (cron_expression, interval_seconds, run_at, next_due_at,
     start_at, ends_at) = build_schedule_fields(
        schedule_type, schedule.get('time_config') or {})
    return {'schedule_type': schedule_type, 'cron_expression': cron_expression,
            'interval_seconds': interval_seconds,
            'run_at': run_at, 'next_due_at': next_due_at,
            'start_at': start_at, 'ends_at': ends_at}


def _normalized_schedule(schedule):
    """入口 schedule 归一为 _schedule_fields 输入（评审 P2-22）。

    接受两种形状：{'schedule_type', 'cron_expression'|'time_config'|'run_at'}
    或 Skill/接口的 {'schedule_type', 'cron', 'interval_seconds', 'run_at'}；
    interval 秒按既有契约换算为整数小时（不足 1 小时按既有校验拒绝）。
    """
    if not isinstance(schedule, dict) or not schedule.get('schedule_type'):
        raise ValueError('schedule 缺少 schedule_type')
    if 'time_config' in schedule or 'cron_expression' in schedule:
        return schedule
    normalized = {'schedule_type': schedule['schedule_type']}
    if schedule.get('cron'):
        normalized['cron_expression'] = str(schedule['cron'])
    if schedule.get('interval_seconds') is not None:
        hours = int(schedule['interval_seconds']) / 3600
        if hours < 1 or hours != int(hours):
            raise ValueError('interval 仅支持 ≥1 的整数小时（既有调度契约）')
        normalized['time_config'] = {'interval_hours': int(hours)}
    if schedule.get('run_at'):
        normalized['run_at'] = schedule['run_at']
    return normalized


def _schedule_backfill_for(tenant_id, user_id, rows_newly, *, device_id):
    """首次纳入范围的会话提交普通只读补录（评审 P2-10，与发送回调共用接点）。"""
    for row, newly in rows_newly:
        if not newly:
            continue
        try:
            schedule_backfill_task(tenant_id, user_id, row, device_id=device_id or row.get('device_id'))
        except Exception as error:
            logger.warning(f'后端日志：企微首录补录任务提交失败（计划纳入） '
                           f'conversation={row.get("id")} error={type(error).__name__}')


def schedule_backfill_task(tenant_id, user_id, conversation_row, *, device_id):
    """首次纳入记录范围：创建一次性只读补录任务（设计 §3.1）。

    沿现有 Scheduler 派发与受理幂等；补录执行日期读取（当天＋零点前 100 条
    背景，include_context=true），不调用总结模型。提交失败由调用方记录，
    留待后续日期读取补充，不重试轰炸。
    """
    from src.scheduler.db import ScheduledTaskDB
    from src.scheduler.schedule import max_tasks_per_user as _max_tasks_per_user

    tz = conversation_row.get('timezone') or DEFAULT_TIMEZONE
    try:
        business_date = datetime.now(ZoneInfo(tz)).date().isoformat()
    except Exception:
        business_date = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date().isoformat()
    config = {'session': conversation_row['session_locator'],
              'date': str(business_date), 'timezone': tz}
    if device_id:
        config['device'] = str(device_id)
    name = conversation_row.get('name') or conversation_row['session_locator']
    prompt = plan_prompt('backfill', config)
    fields = _schedule_fields({'schedule_type': 'once'})
    _ensure_session_committed(
        business_session_id(tenant_id, user_id, 'backfill',
                            f"{conversation_row.get('id')}|{business_date}"),
        tenant_id, user_id, f'企微会话记录补录：{name}')
    task = ScheduledTaskDB.create(
        user_id=user_id, tenant_id=tenant_id,
        name=f'企微会话记录补录：{name}', task_prompt=prompt,
        schedule_type=fields['schedule_type'], run_at=fields['run_at'],
        start_at=fields.get('start_at'), ends_at=fields.get('ends_at'),
        next_due_at=fields['next_due_at'],
        max_active=_max_tasks_per_user(tenant_id))
    logger.info(f"后端日志：企微首录补录任务已登记 task_id={task.get('task_id')} "
                f"conversation={conversation_row.get('id')} date={business_date}")
    return task


def resolve_runner_source_device(tenant_id, user_id, *, runner_id=None, session_id=None,
                                 locators=None):
    """来源设备解析（评审四 P2-1、五 P2）：建立日报本身可以是会话的首次登记
    入口，不能依赖业务记录已存在——优先取当前已授权 Runner 的 wecom 绑定，
    其次取与**本次目标会话**匹配的成功原读取调用设备（"成功读取发生过"只证明
    该设备执行过某次读取，不证明它就是目标会话的来源）；都不确定返回 None，
    由调用方以业务会话记录兜底。纯读取，不改写任何事实。"""
    from src.db.database import get_db_connection

    runner_id = runner_id or None
    session_id = session_id or None
    targets = sorted({item for item in (locators or []) if item})
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if runner_id:
            cursor.execute(
                '''SELECT runner_id, tenant_id, user_id, session_id, source, session_kind,
                          checkpoint->'execution' AS execution
                   FROM agent_runners WHERE runner_id=%s
                     AND tenant_id=%s AND user_id=%s LIMIT 1''',
                (runner_id, tenant_id, user_id))
            row = cursor.fetchone()
            if row is not None:
                execution = row['execution'] if not isinstance(row['execution'], str) \
                    else json.loads(row['execution'] or '{}')
                if isinstance(execution, dict):
                    from src.services.agent_runner.wecom_device_binding import bound_device
                    try:
                        identity = {key: row.get(key) for key in
                                    ('tenant_id', 'user_id', 'session_id',
                                     'source', 'session_kind')}
                        device = bound_device(execution, str(row['runner_id']), identity)
                        if device:
                            return str(device)
                    except Exception:
                        pass  # 绑定不存在/失配 → 回退原读取调用上下文
        # 读取事实回退（评审五 P2）：必须关联本次目标会话——匹配原调用参数
        # （单读 session / 批读 sessions 数组）或结果稳定标识；不匹配目标会话
        # 的读取不作为来源证据，无匹配证据时不固化任何设备。
        base_where = ("tenant_id=%s AND user_id=%s"
                      " AND provider_key='wecom'"
                      " AND tool_name IN ('wecom_read_session','wecom_read_sessions')"
                      " AND state='succeeded' AND device_id IS NOT NULL")
        match_sql = (" AND (arguments_json->>'session' = ANY(%s::text[])"
                     " OR arguments_json->'sessions' ?| %s::text[]"
                     " OR result_json->'data'->>'session' = ANY(%s::text[])"
                     " OR EXISTS (SELECT 1 FROM jsonb_array_elements("
                     "result_json->'data'->'sessions') AS sess"
                     " WHERE sess->>'session' = ANY(%s::text[])))")
        queries = []
        values = []
        if session_id:
            queries.append("SELECT device_id FROM local_tool_invocations WHERE "
                           + base_where + " AND session_id=%s" + match_sql
                           + " ORDER BY created_at DESC LIMIT 1")
            values.append((tenant_id, user_id, session_id,
                           targets, targets, targets, targets))
        queries.append("SELECT device_id FROM local_tool_invocations WHERE "
                       + base_where + match_sql
                       + " ORDER BY created_at DESC LIMIT 1")
        values.append((tenant_id, user_id, targets, targets, targets, targets))
        for query, params in zip(queries, values):
            cursor.execute(query, params)
            row = cursor.fetchone()
            if row is not None and row['device_id']:
                return str(row['device_id'])
    return None


def ensure_daily_plan(tenant_id, user_id, *, locators, schedule, profile_id=None,
                      timezone_name=None, device_id=None, requirements='',
                      instance_id=None, notification_rule_id=None):
    """建立或复用日报计划并登记会话清单（设计 §5.1，评审 P1-7/P2-10）。

    - 复用（严格）：同来源账号＋同设备＋可确定时同智能体，且本次未显式
      提出与现有计划不同的 schedule/timezone/requirements/instance；不满足
      就新建对应计划，不做"退而求其次"兜底；
    - 新建：周期计划沿现有 scheduled_tasks；特殊关注要求写进任务定义文本，
      同计划多个会话适用；
    - 首次纳入的会话提交普通只读补录（与发送回调共用接点）；
    - 返回 {'plan': scheduled_tasks 行, 'created': bool, 'conversations': [...]}。
    """
    from . import service
    from .adapters import decode_locator

    if not locators:
        raise service.ConversationServiceError('LOCATORS_REQUIRED', '会话清单不能为空')
    decoded = [decode_locator(item) for item in locators]
    if any(item is None for item in decoded):
        raise service.ConversationServiceError('SESSION_LOCATOR_INVALID', '会话标识无效')
    accounts = {item[0] for item in decoded}
    if len(accounts) != 1:
        raise service.ConversationServiceError(
            'PLAN_SOURCE_MIXED', '一个计划只管理同一来源账号的会话；不同来源请分别建立计划')
    account = next(iter(accounts))
    # 来源贯通（评审三 P1-2）：来源设备先从已登记会话行解析，显式参数仅作
    # 授权覆盖；Skill 示例省略 --device 时计划仍绑定会话所属原设备，执行不
    # 依赖全局 selected。多个已知设备 = 跨来源，拒绝混入同一计划。
    known_rows = service.conversations_for_locators(tenant_id, user_id, locators)
    known_devices = {str(row['device_id']) for row in known_rows if row.get('device_id')}
    if device_id:
        known_devices.add(str(device_id))
    if len(known_devices) > 1:
        raise service.ConversationServiceError(
            'PLAN_SOURCE_MIXED', '一个计划只管理同一来源设备的会话；不同设备请分别建立计划')
    resolved_device = next(iter(known_devices), None)
    if resolved_device is None:
        # 首次登记入口（评审四 P2-1）：新会话无业务记录时，来源从当前已授权
        # Runner 绑定或原读取调用上下文固化，执行不依赖全局 selected。
        resolved_device = resolve_runner_source_device(
            tenant_id, user_id,
            runner_id=os.environ.get('AID_RUNNER_ID'),
            session_id=os.environ.get('AID_SESSION_ID'),
            locators=locators)
    timezone_name = timezone_name or DEFAULT_TIMEZONE
    if not service.valid_timezone(timezone_name):
        raise service.ConversationServiceError('TIMEZONE_INVALID', '时区名称无效')
    normalized_schedule = _normalized_schedule(schedule)

    # 先按既有调度构造归一校验（评审二 P2-5）：非法 time_config 在此统一报
    # 既有错误；复用/新建比较使用的都是归一后的实际调度字段。
    fields = _schedule_fields(normalized_schedule)
    # 记录本次移动会话原先归属的计划（评审三 P2-3）：enroll 覆盖后据此收口
    # 已无目标的旧计划（沿现有 Scheduler 暂停，不恢复采集接管框架）。
    previous_plan_ids = {row['daily_plan_task_id'] for row in known_rows
                         if row.get('daily_plan_task_id')}
    # 候选计划逐个判配置兼容，取首个兼容者（评审三 P2-3：首个候选不兼容时
    # 继续找下一个兼容计划，而不是新建重复计划）。
    existing = None
    for candidate in service.candidate_plans_for_source(
            tenant_id, user_id, device_id=resolved_device, account=account,
            profile_id=profile_id):
        if not _explicit_config_differs(
                candidate, normalized_schedule, timezone_name, requirements, instance_id):
            existing = candidate
            break
    if existing is not None:
        results = service.enroll_conversations_for_plan(
            tenant_id, user_id, locators, plan_task_id=existing['task_id'],
            timezone_name=timezone_name, device_id=resolved_device)
        _schedule_backfill_for(tenant_id, user_id, results, device_id=resolved_device)
        _pause_orphaned_plans(tenant_id, user_id, previous_plan_ids, existing['task_id'])
        return {'plan': existing, 'created': False,
                'conversations': [row for row, _ in results]}

    from src.scheduler.db import ScheduledTaskDB, ScheduledTaskQuotaError
    from src.scheduler.schedule import max_tasks_per_user as _max_tasks_per_user
    config = {'timezone': timezone_name, 'account': account}
    if resolved_device:
        config['device'] = str(resolved_device)
    prompt = plan_prompt('daily', config, requirements)
    name = '企微会话日报' + (f'（{requirements[:20]}）' if requirements else '')
    try:
        task = ScheduledTaskDB.create(
            user_id=user_id, tenant_id=tenant_id, name=name, task_prompt=prompt,
            schedule_type=fields['schedule_type'],
            cron_expression=fields['cron_expression'],
            interval_seconds=fields['interval_seconds'],
            run_at=fields['run_at'], profile_id=profile_id or 'main',
            instance_id=instance_id, notification_rule_id=notification_rule_id,
            start_at=fields.get('start_at'), ends_at=fields.get('ends_at'),
            next_due_at=fields['next_due_at'],
            max_active=_max_tasks_per_user(tenant_id))
    except ScheduledTaskQuotaError as error:
        raise service.ConversationServiceError(
            'TASK_QUOTA_EXCEEDED', str(error), 409) from error
    results = service.enroll_conversations_for_plan(
        tenant_id, user_id, locators, plan_task_id=task['task_id'],
        timezone_name=timezone_name, device_id=resolved_device)
    _schedule_backfill_for(tenant_id, user_id, results, device_id=resolved_device)
    _pause_orphaned_plans(tenant_id, user_id, previous_plan_ids, task['task_id'])
    return {'plan': task, 'created': True, 'conversations': [row for row, _ in results]}


def _pause_orphaned_plans(tenant_id, user_id, previous_plan_ids, keep_plan_id):
    """收口已无目标的旧计划（评审三 P2-3）：配置变化/拆分会把会话移动到新
    计划；被替换的旧计划若不再管理任何会话，沿现有 Scheduler 暂停能力置
    paused（保留历史、停止触发），不恢复采集接管框架、不删除数据。仍持有
    会话的旧计划不动（多会话计划拆出其一照常执行）。
    """
    from . import service
    for plan_task_id in sorted(previous_plan_ids):
        if plan_task_id == keep_plan_id:
            continue
        if service.plan_conversations(tenant_id, user_id, plan_task_id):
            continue  # 仍有会话：其余会话照原计划执行
        try:
            from src.scheduler.db import ScheduledTaskDB
            paused = ScheduledTaskDB.pause(
                plan_task_id, tenant_id=tenant_id, user_id=user_id,
                pause_reason='definition_invalid')
            if paused:
                logger.info(f'后端日志：企微日报旧计划已无目标会话，暂停收口 '
                            f'task_id={plan_task_id}')
        except Exception as error:
            # 收口失败不阻断新计划生效；旧计划空跑每天报目标为空，页面可见。
            logger.warning(f'后端日志：企微日报旧计划收口失败 task_id={plan_task_id} '
                           f'error={type(error).__name__}')


def _explicit_schedule_facts(schedule):
    """归一后的 schedule → 实际调度字段（评审二 P2-5）：time_config 与直接
    cron 走既有 generate_cron_expression 同一路径，比较归一结果而非输入形状。
    """
    schedule_type = schedule.get('schedule_type')
    cron = schedule.get('cron_expression')
    time_config = schedule.get('time_config')
    if cron is None and time_config and schedule_type in ('daily', 'weekly', 'monthly'):
        from src.scheduler.schedule import generate_cron_expression
        cron = generate_cron_expression(schedule_type, time_config)
    interval_seconds = None
    if schedule_type == 'interval':
        hours = (time_config or {}).get('interval_hours')
        if hours is not None:
            interval_seconds = int(hours) * 3600
    return schedule_type, cron, interval_seconds


def _explicit_config_differs(plan_row, schedule, timezone_name, requirements, instance_id):
    """本次显式要求与现有计划不同则不复用（评审 P1-7）。

    比较 schedule（经 _explicit_schedule_facts 归一）、timezone、requirements、
    instance；现有计划缺省的维度不视为冲突。
    """
    from . import service
    config = parse_plan_prompt(plan_row.get('task_prompt') or '') or {}
    if timezone_name and config.get('timezone') and config['timezone'] != timezone_name:
        return True
    schedule_type, cron, interval_seconds = _explicit_schedule_facts(schedule)
    explicit_kind = ('cron' if cron and schedule_type in ('daily', 'weekly', 'monthly')
                     else schedule_type if (interval_seconds is not None or schedule.get('run_at'))
                     else None)
    if explicit_kind:
        plan_kind = plan_row.get('schedule_type')
        # daily/weekly/monthly 都是 cron 种计划（cron_expression 表达），与显式
        # cron/time_config 意图同类；种类不同（如现有 cron 计划 vs 新 interval
        # 意图）不复用。
        comparable = 'cron' if plan_kind in ('daily', 'weekly', 'monthly') else plan_kind
        if comparable != explicit_kind:
            return True
        if cron and plan_row.get('cron_expression') and plan_row['cron_expression'] != cron:
            return True
        if interval_seconds is not None and plan_row.get('interval_seconds') != interval_seconds:
            return True
    run_at = schedule.get('run_at')
    if run_at and plan_row.get('schedule_type') == 'once':
        return True  # once 是单次计划：新的 once 意图总是新计划
    if requirements:
        existing_requirements = (plan_row.get('task_prompt') or '').partition('\n')[2].strip()
        if existing_requirements != requirements.strip():
            return True
    if instance_id and plan_row.get('instance_id') != instance_id:
        return True
    return False


def _runner_client():
    from src.services.agent_runner.web_client import RunnerServiceClient
    from src.config.settings import settings
    return RunnerServiceClient(settings.agent_runner)


async def submit_manual_report(authorization, tenant_id, user_id, *, conversation_ids=None,
                               plan_task_id=None, business_date=None, client_request_id=None,
                               timezone_name=None, device_id=None, profile_id=None,
                               instance_id=None):
    """手动生成/重算（"总结今天"或指定日期）：沿现有 Runner 网关提交。

    受信业务描述由本服务端构造（web 提交方不可自写 request_data）；网络重投
    沿同一 client_request_id 幂等。日报继承所选智能体模型配置（计划 profile/
    instance），不被 main 硬编码覆盖（设计 §5.3，评审 P2-11：定时执行和手动
    重算共用完整受信描述——来源设备、时区、profile/instance 与计划要求）。
    """
    from . import service

    if plan_task_id is None and not conversation_ids:
        raise service.ConversationServiceError('REPORT_TARGET_REQUIRED', '缺少计划或会话范围')
    conversations = []
    if conversation_ids:
        for item in conversation_ids:
            row = service.get_conversation(tenant_id, user_id, item)
            if row is None:
                raise service.ConversationServiceError('CONVERSATION_NOT_FOUND',
                                                       '会话不存在或无权访问', 404)
            conversations.append(row)
    plan = None
    if plan_task_id:
        from src.scheduler.db import ScheduledTaskDB
        plan = ScheduledTaskDB.get_by_id(plan_task_id, tenant_id=tenant_id, user_id=user_id)
        if plan is None:
            raise service.ConversationServiceError('PLAN_NOT_FOUND', '日报计划不存在或无权访问', 404)
    plan_config = {}
    if plan is not None:
        config = parse_plan_prompt(plan.get('task_prompt') or '')
        if config is None or config.get('kind') != 'daily':
            raise service.ConversationServiceError('PLAN_NOT_DAILY', '该任务不是企微日报计划')
        plan_config = config
        profile_id = profile_id or plan.get('profile_id')
        instance_id = instance_id or plan.get('instance_id')
    if conversations:
        # 会话清单须沿同一来源执行（设计 §5.1）：设备/时区取清单所属计划或
        # 首个会话的登记事实，不跨来源混发。
        linked = next((row.get('daily_plan_task_id') for row in conversations
                       if row.get('daily_plan_task_id')), None)
        if plan is None and linked:
            from src.scheduler.db import ScheduledTaskDB as _DB
            plan = _DB.get_by_id(linked, tenant_id=tenant_id, user_id=user_id)
            if plan is not None:
                plan_config = parse_plan_prompt(plan.get('task_prompt') or {}) or {}
                profile_id = profile_id or plan.get('profile_id')
                instance_id = instance_id or plan.get('instance_id')
    config = {'kind': 'daily', 'business_date': str(business_date), 'as_of': 'snapshot_now',
              'timezone': timezone_name or plan_config.get('timezone')
              or (conversations[0].get('timezone') if conversations else None)
              or DEFAULT_TIMEZONE}
    if plan_task_id:
        config['task_id'] = plan_task_id
    if conversations:
        # 已授权的明确清单写入受信描述（评审二 P1-3）：仅会话清单时它是唯一
        # 目标；计划内子集时明确清单优先于整计划范围（flow._resolve_conversations
        # 先读 conversation_ids）。
        config['conversation_ids'] = [row['id'] for row in conversations]
    if plan_config.get('requirements'):
        config['requirements'] = plan_config['requirements']
    device = device_id or plan_config.get('device')
    if conversations and not device:
        device = conversations[0].get('device_id')
    if device:
        config['device'] = str(device)
    request_id = client_request_id or str(uuid.uuid4())
    session_id = business_session_id(tenant_id, user_id, 'daily', request_id)
    _ensure_session_committed(session_id, tenant_id, user_id, '企微会话日报生成')
    body = {'source': 'chat', 'session': {'kind': 'web', 'session_id': session_id},
            'client_request_id': request_id, 'text': plan_prompt('daily', config, '手动生成日报'),
            'routing_policy': 'default_single',
            'request_data': {DESCRIPTOR_KEY: config}}
    if profile_id:
        body['profile_id'] = profile_id
    if instance_id:
        body['instance_id'] = instance_id
    if config.get('device'):
        body['request_data']['wecom_task_device'] = config['device']
    client = _runner_client()
    value = await client.request('POST', '/v1/runners', authorization=authorization,
                                 tenant=tenant_id, body=body, accept_new=True)
    runner = value.get('runner') or {}
    return {'runner_id': runner.get('runner_id'), 'created': value.get('created'),
            'status': runner.get('status'), 'session_id': session_id}
