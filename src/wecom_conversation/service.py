"""企微会话旁路日志与日报查询服务（设计 §5/§6）。

读写全部带 tenant_id + 创建用户 user_id 条件（同租户当前创建用户可见）。
本模块只做本地 SQL 与报告保存；不派发本机读取（补录/日报任务的 Runner
提交见 plans.py），不承担任务恢复、工单状态或人工记录开关（设计 §7）。
"""
from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from psycopg2.extras import Json

from src.db.database import get_db_connection

from .adapters import DEFAULT_TIMEZONE, decode_locator

BUSINESS_SKILL = 'wecom-conversation'


class ConversationServiceError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def _tz(name):
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _row(row):
    return dict(row) if row is not None else None


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def valid_timezone(name):
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


# ==================== 记录范围会话（设计 §3.3/§6） ====================

def list_conversations(tenant_id, user_id, *, limit=100, offset=0):
    """记录范围会话列表（行存在即在范围；无人工关注开关）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT * FROM bs_wecom_conversations WHERE tenant_id=%s AND user_id=%s
               ORDER BY updated_at DESC LIMIT %s OFFSET %s''',
            (tenant_id, user_id, min(int(limit), 200), int(offset)))
        rows = [_row(item) for item in cursor.fetchall()]
    for item in rows:
        item['coverage'] = _json(item.get('coverage'))
        item['enroll_reasons'] = _json(item.get('enroll_reasons'))
    return rows


def get_conversation(tenant_id, user_id, conversation_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        return get_conversation_in_tx(cursor, tenant_id, user_id, conversation_id)


def get_conversation_in_tx(cursor, tenant_id, user_id, conversation_id):
    cursor.execute(
        '''SELECT * FROM bs_wecom_conversations WHERE id=%s AND tenant_id=%s AND user_id=%s''',
        (conversation_id, tenant_id, user_id))
    row = _row(cursor.fetchone())
    if row:
        row['coverage'] = _json(row.get('coverage'))
        row['enroll_reasons'] = _json(row.get('enroll_reasons'))
    return row


def conversations_for_locators(tenant_id, user_id, locators):
    """按稳定标识取已登记会话行（计划来源解析用；缺行不补建）。"""
    decoded = [decode_locator(locator) for locator in locators]
    pairs = [(item[0], item[1]) for item in decoded if item is not None]
    if not pairs:
        return []
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT * FROM bs_wecom_conversations WHERE tenant_id=%s AND user_id=%s
               AND (source_account_id, source_conversation_id) IN (
                   SELECT * FROM unnest(%s::text[], %s::text[]))''',
            (tenant_id, user_id, [pair[0] for pair in pairs], [pair[1] for pair in pairs]))
        rows = [_row(item) for item in cursor.fetchall()]
    for item in rows:
        item['coverage'] = _json(item.get('coverage'))
        item['enroll_reasons'] = _json(item.get('enroll_reasons'))
    return rows


def plan_conversations(tenant_id, user_id, plan_task_id):
    """一个日报计划管理的会话清单（设计 §5.1：一个计划管理多个会话）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT * FROM bs_wecom_conversations
               WHERE tenant_id=%s AND user_id=%s AND daily_plan_task_id=%s
               ORDER BY id''',
            (tenant_id, user_id, plan_task_id))
        rows = [_row(item) for item in cursor.fetchall()]
    for item in rows:
        item['coverage'] = _json(item.get('coverage'))
        item['enroll_reasons'] = _json(item.get('enroll_reasons'))
    return rows


# ==================== 消息查询（设计 §6.1） ====================

def _day_bounds(business_date, tz_name):
    tz = _tz(tz_name)
    start = datetime.combine(business_date, time.min, tzinfo=tz)
    return start.astimezone(dt_timezone.utc), (start + timedelta(days=1)).astimezone(dt_timezone.utc)


def list_messages(tenant_id, user_id, conversation_id, *, business_date=None, before_id=None,
                  before_sent_at=None, after_id=None, after_sent_at=None,
                  limit=50, undated=False, order='asc'):
    """按日期或复合 keyset 读取已存消息（不要求 Runtime 在线）。

    - business_date：按会话业务时区的自然日边界查询 sent_at；
    - undated=True：单列 sent_at 为空的记录；
    - keyset 与排序同键：`(COALESCE(sent_at,'infinity'), id)`。
    """
    conversation = get_conversation(tenant_id, user_id, conversation_id)
    if conversation is None:
        raise ConversationServiceError('CONVERSATION_NOT_FOUND', '会话不存在或无权访问', 404)
    clauses = ['tenant_id=%s', 'conversation_id=%s']
    values = [tenant_id, conversation_id]
    if business_date is not None:
        start, end = _day_bounds(business_date, conversation.get('timezone'))
        clauses.append('sent_at >= %s AND sent_at < %s')
        values.extend([start, end])
    elif undated:
        clauses.append('sent_at IS NULL')
    sort_key = "COALESCE(sent_at, 'infinity'::timestamptz)"
    if order == 'desc':
        # 显示顺序向后翻页：严格小于上一页末行键
        if before_id is not None:
            clauses.append(
                f'''({sort_key} < COALESCE(%s::timestamptz, 'infinity'::timestamptz)
                     OR ({sort_key} = COALESCE(%s::timestamptz, 'infinity'::timestamptz) AND id < %s))''')
            values.extend([before_sent_at, before_sent_at, int(before_id)])
        direction = 'DESC'
    else:
        if after_id is not None:
            clauses.append(
                f'''({sort_key} > COALESCE(%s::timestamptz, 'infinity'::timestamptz)
                     OR ({sort_key} = COALESCE(%s::timestamptz, 'infinity'::timestamptz) AND id > %s))''')
            values.extend([after_sent_at, after_sent_at, int(after_id)])
        direction = 'ASC'
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'''SELECT * FROM bs_wecom_messages WHERE {' AND '.join(clauses)}
                ORDER BY {sort_key} {direction}, id {direction} LIMIT %s''',
            (*values, min(int(limit), 200)))
        rows = [_row(item) for item in cursor.fetchall()]
        for item in rows:
            for key in ('source_fields', 'attachment_ref', 'origin_evidence'):
                item[key] = _json(item.get(key))
        return rows


def get_message(tenant_id, user_id, message_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT * FROM bs_wecom_messages WHERE id=%s AND tenant_id=%s AND user_id=%s''',
            (message_id, tenant_id, user_id))
        row = _row(cursor.fetchone())
        if row is None:
            raise ConversationServiceError('MESSAGE_NOT_FOUND', '消息不存在或无权访问', 404)
        for key in ('source_fields', 'attachment_ref', 'origin_evidence'):
            row[key] = _json(row.get(key))
        return row


# ==================== 日报保存与查询（设计 §5.2/§5.3/§6.2） ====================

def _summary_row(row):
    item = _row(row)
    if item:
        item['summary'] = _json(item.get('summary'))
        item['coverage'] = _json(item.get('coverage'))
        item['business_date'] = str(item.get('business_date'))
    return item


def list_daily_summaries(tenant_id, user_id, conversation_id, *, before_date=None, limit=30):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        clauses = 'tenant_id=%s AND user_id=%s AND conversation_id=%s'
        values = [tenant_id, user_id, conversation_id]
        if before_date is not None:
            clauses += ' AND business_date < %s'
            values.append(before_date)
        cursor.execute(
            f'''SELECT * FROM bs_wecom_daily_summaries WHERE {clauses}
                ORDER BY business_date DESC LIMIT %s''',
            (*values, min(int(limit), 100)))
        return [_summary_row(item) for item in cursor.fetchall()]


def get_daily_summary(tenant_id, user_id, conversation_id, business_date):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT * FROM bs_wecom_daily_summaries WHERE conversation_id=%s AND business_date=%s
               AND tenant_id=%s AND user_id=%s''',
            (conversation_id, business_date, tenant_id, user_id))
        return _summary_row(cursor.fetchone())


def daily_summaries_for_date(tenant_id, user_id, business_date, *, conversation_ids=None):
    """日报工作台按日期聚合：该用户记录范围会话在指定业务日的报告（含缺失）。"""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT c.id AS conversation_id, c.name, c.timezone, c.daily_plan_task_id,
                      s.summary, s.coverage, s.snapshot_at, s.generated_at, s.source_runner_id,
                      s.updated_at AS report_updated_at
               FROM bs_wecom_conversations c
               LEFT JOIN bs_wecom_daily_summaries s
                 ON s.conversation_id = c.id AND s.business_date = %s
                AND s.tenant_id = c.tenant_id AND s.user_id = c.user_id
               WHERE c.tenant_id=%s AND c.user_id=%s
               ORDER BY c.updated_at DESC LIMIT 500''',
            (business_date, tenant_id, user_id))
        rows = [_row(item) for item in cursor.fetchall()]
    if conversation_ids is not None:
        wanted = set(conversation_ids)
        rows = [item for item in rows if item['conversation_id'] in wanted]
    for item in rows:
        item['summary'] = _json(item.get('summary'))
        item['coverage'] = _json(item.get('coverage'))
    return rows


def last_daily_generation(tenant_id, user_id, business_date, *, task_id=None):
    """本次日报执行（该业务日对应范围最近一次）的逐会话事实（评审二 P2-11、
    评审三 P1-4）。

    复用 Runner checkpoint 中业务分支持久化的 wecom_daily_result；无新状态
    表/owner 协议。带 task_id 时只取该计划的执行（多计划互不覆盖）；无
    task_id 取用户该日最近一次。查询/前端/通知共用同一事实，不按名称猜归属。
    """
    conditions = ["tenant_id=%s", "user_id=%s",
                  "checkpoint->'execution'->'resources'->'wecom_daily_result'->>'business_date' = %s"]
    values = [tenant_id, user_id, str(business_date)]
    if task_id:
        conditions.append(
            "checkpoint->'execution'->'resources'->'wecom_daily_result'->>'task_id' = %s")
        values.append(task_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            f'''SELECT runner_id, status, finished_at,
                       checkpoint->'execution'->'resources'->'wecom_daily_result' AS result
                FROM agent_runners
                WHERE {' AND '.join(conditions)}
                ORDER BY accepted_at DESC, runner_id DESC LIMIT 1''',
            values)
        row = _row(cursor.fetchone())
    if row is None or not isinstance(row.get('result'), dict):
        return None
    result = row['result']
    return {
        'runner_id': row.get('runner_id'),
        'status': row.get('status'),
        'finished_at': row.get('finished_at').isoformat() if row.get('finished_at') else None,
        'business_date': str(business_date),
        'task_id': result.get('task_id'),
        'scope_conversation_ids': [item for item in result.get('scope_conversation_ids') or []
                                   if isinstance(item, int)],
        'completed': [{'conversation_id': item.get('conversation_id'), 'name': item.get('name')}
                      for item in result.get('completed') or []
                      if isinstance(item, dict) and item.get('conversation_id')],
        'failed': [{'conversation_id': item.get('conversation_id'), 'name': item.get('name'),
                    'code': item.get('code')}
                   for item in result.get('failed') or []
                   if isinstance(item, dict) and item.get('conversation_id')],
    }


def save_daily_report(tenant_id, user_id, conversation_id, business_date, payload, *,
                      snapshot_at, generated_at, source_runner_id=None):
    """程序按会话+业务日期 UPSERT 日报（设计 §5.2）。

    新鲜度以较新的首轮 read_window.snapshot_at 优先（同快照时点按本次生成
    时间），避免旧数据晚完成覆盖新数据；同会话同日只保留最新一份，不建
    报告版本表或生成锁。
    """
    conversation = get_conversation(tenant_id, user_id, conversation_id)
    if conversation is None:
        raise ConversationServiceError('CONVERSATION_NOT_FOUND', '会话不存在或无权访问', 404)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO bs_wecom_daily_summaries
                   (tenant_id, user_id, conversation_id, business_date, timezone,
                    snapshot_at, generated_at, source_runner_id, coverage, summary)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id, conversation_id, business_date) DO UPDATE SET
                   timezone = EXCLUDED.timezone,
                   snapshot_at = EXCLUDED.snapshot_at,
                   generated_at = EXCLUDED.generated_at,
                   source_runner_id = EXCLUDED.source_runner_id,
                   coverage = EXCLUDED.coverage,
                   summary = EXCLUDED.summary,
                   updated_at = CURRENT_TIMESTAMP
               WHERE bs_wecom_daily_summaries.snapshot_at IS NULL
                  OR EXCLUDED.snapshot_at > bs_wecom_daily_summaries.snapshot_at
                  OR (EXCLUDED.snapshot_at = bs_wecom_daily_summaries.snapshot_at
                      AND EXCLUDED.generated_at >= bs_wecom_daily_summaries.generated_at)
               RETURNING *''',
            (tenant_id, user_id, conversation_id, business_date,
             conversation.get('timezone') or DEFAULT_TIMEZONE,
             snapshot_at, generated_at, source_runner_id,
             Json(payload.get('coverage')) if payload.get('coverage') else None,
             Json(payload.get('summary')) if payload.get('summary') else None))
        row = _summary_row(cursor.fetchone())
        conn.commit()
    if row is None:
        # 新鲜度条件未命中：返回现存行（旧数据晚完成不覆盖新数据）
        return get_daily_summary(tenant_id, user_id, conversation_id, business_date)
    return row


# ==================== 日报计划（设计 §5.1；执行沿现有定时任务） ====================

def candidate_plans_for_source(tenant_id, user_id, *, device_id, account, profile_id=None):
    """同用户、同来源设备/账号的活跃日报计划候选（按来源严格过滤，有序）。

    来源维度（账号/设备/智能体）在此严格过滤；调度与要求维度由调用方按
    配置兼容性逐个判定后取首个（评审三 P2-3：首个候选不兼容时继续找下一个
    兼容计划，而不是新建重复计划）。
    """
    if not account or not device_id:
        return []  # 设备归属不可确定时不猜计划
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT DISTINCT t.* FROM bs_wecom_conversations c
               JOIN scheduled_tasks t ON t.task_id = c.daily_plan_task_id
               WHERE c.tenant_id=%s AND c.user_id=%s AND c.source_account_id=%s
                 AND c.daily_plan_task_id IS NOT NULL AND t.status='active'
               ORDER BY t.task_id LIMIT 20''',
            (tenant_id, user_id, account))
        rows = [_row(item) for item in cursor.fetchall()]
    wanted_device = str(device_id)
    candidates = []
    for row in rows:
        if _plan_device(row) != wanted_device:
            continue
        if profile_id is not None and row.get('profile_id') != profile_id:
            continue
        candidates.append(row)
    return candidates


def find_suitable_plan(tenant_id, user_id, *, device_id, account, profile_id=None):
    """兼容包装（logsink 自动并入用）：候选首个，无则 None。"""
    candidates = candidate_plans_for_source(tenant_id, user_id, device_id=device_id,
                                            account=account, profile_id=profile_id)
    return candidates[0] if candidates else None


def _plan_device(plan_row):
    """计划配置行携带的来源设备（plans.py 写入的标记行；解析失败返回 None）。"""
    from .plans import parse_plan_prompt
    config = parse_plan_prompt(plan_row.get('task_prompt') or '')
    return (config or {}).get('device')


def link_conversations_to_plan(tenant_id, user_id, plan_task_id, conversation_ids):
    """把会话并入计划清单（受理/自动并入共用；幂等）。"""
    if not conversation_ids:
        return 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''UPDATE bs_wecom_conversations SET daily_plan_task_id=%s, updated_at=CURRENT_TIMESTAMP
               WHERE id=ANY(%s::bigint[]) AND tenant_id=%s AND user_id=%s''',
            (plan_task_id, list(conversation_ids), tenant_id, user_id))
        updated = cursor.rowcount
        conn.commit()
    return updated


def enroll_conversations_for_plan(tenant_id, user_id, locators, *, plan_task_id,
                                  timezone_name, device_id=None):
    """建立/扩展日报计划时登记会话范围（原因 daily_report；设计 §3.3）。

    返回 [(会话行, 是否首次纳入)]：首次纳入的会话由调用方提交首次补录
    （评审 P2-10，与发送回调共用同一接点）。范围登记与计划关联同事务提交。
    """
    from . import archive
    results = []
    with get_db_connection() as conn:
        for locator in locators:
            if decode_locator(locator) is None:
                raise ConversationServiceError('SESSION_LOCATOR_INVALID',
                                               f'会话标识无效：{str(locator)[:60]}')
            row, newly = archive.register_scope(conn, tenant_id, user_id, locator,
                                                device_id=device_id, reason='daily_report',
                                                timezone=timezone_name)
            if row is not None:
                results.append((row, newly))
        if plan_task_id and results:
            cursor = conn.cursor()
            cursor.execute(
                '''UPDATE bs_wecom_conversations SET daily_plan_task_id=%s, updated_at=CURRENT_TIMESTAMP
                   WHERE id=ANY(%s::bigint[]) AND tenant_id=%s AND user_id=%s''',
                (plan_task_id, [row['id'] for row, _ in results], tenant_id, user_id))
        conn.commit()
    return results
