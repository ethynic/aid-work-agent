"""Recurring WeChat range summaries, dispatched by the existing scheduler leader."""

from datetime import timedelta
from typing import Annotated, Literal
from uuid import UUID

from loguru import logger
from psycopg2.extras import Json
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, model_validator

from src.config.settings import settings
from src.db.database import get_db_connection
from src.local_tools import repository, weixin_tracking as tracking
from src.notifications.contracts import Subject
from src.notifications.events import publish_in_tx
from src.notifications.repository import task_rule_matches
from src.scheduler.schedule import compute_next_due

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80,
                                         pattern=r'^[^\x00-\x1f\x7f]+$')]
RuleId = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class Definition(BaseModel):
    model_config = ConfigDict(extra='forbid')

    device_id: UUID
    title: Title
    group_ids: list[UUID] = Field(min_length=1)
    preset: tracking.Preset
    frequency: Literal['daily', 'weekly']
    weekday: int | None = Field(default=None, ge=0, le=6)
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    notify: StrictBool = False
    notification_rule_id: RuleId | None = None

    @model_validator(mode='after')
    def validate_options(self):
        if (self.frequency == 'weekly') != (self.weekday is not None):
            raise ValueError('weekday is required only for weekly schedules')
        if self.notification_rule_id and not self.notify:
            raise ValueError('notification rule requires notifications')
        return self


class ScheduleState(BaseModel):
    model_config = ConfigDict(extra='forbid')

    paused: StrictBool


def _next_due(row, now):
    # compute_next_due accepts naive Beijing instants and returns local wall time.
    weekday = '*' if row['frequency'] == 'daily' else ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')[row['weekday']]
    cron = f"{row['minute']} {row['hour']} * * {weekday}"
    local = now.astimezone(tracking.BEIJING).replace(tzinfo=None)
    return compute_next_due({'schedule_type': row['frequency'], 'cron_expression': cron}, local).replace(
        tzinfo=tracking.BEIJING,
    )


def _rule_rows(cursor, tenant_id):
    cursor.execute('''SELECT r.rule_id,r.name,array_agg(DISTINCT t.provider ORDER BY t.provider) AS channels
                      FROM notification_rules r JOIN notification_targets t
                        ON t.rule_id=r.rule_id AND t.tenant_id=r.tenant_id
                      WHERE r.tenant_id=%s AND r.enabled AND r.event_type='scheduled.task'
                        AND t.state='active' AND t.channel='im_webhook' AND t.recipient_user_id IS NULL
                        AND (r.conditions->'events' IS NULL OR r.conditions->'events'='[]'::jsonb
                             OR r.conditions->'events' ?| ARRAY['task.completed','task.failed'])
                      GROUP BY r.rule_id,r.name ORDER BY r.name,r.rule_id''', (tenant_id,))
    return [dict(row) for row in cursor.fetchall()]


def rules(tenant_id):
    with get_db_connection() as conn:
        return _rule_rows(conn.cursor(), tenant_id)


def _public(row, now):
    fields = ('id', 'device_id', 'title', 'group_ids', 'preset', 'frequency', 'weekday', 'hour',
              'minute', 'notification_rule_id', 'notify', 'next_due_at')
    item = {field: row[field] for field in fields}
    item['paused'] = item['next_due_at'] is None
    if item['paused']:
        item['preview_at'] = _next_due(row, now)
    return item


def list_schedules(tenant_id, user_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        now = tracking.clock(cursor)
        cursor.execute('''SELECT * FROM bs_weixin_agent_schedules WHERE tenant_id=%s AND user_id=%s
                          ORDER BY created_at DESC,id DESC''', (tenant_id, user_id))
        return [_public(row, now) for row in cursor.fetchall()]


def _locked_schedule(cursor, tenant_id, user_id, schedule_id):
    cursor.execute('''SELECT * FROM bs_weixin_agent_schedules
                      WHERE id=%s AND tenant_id=%s AND user_id=%s FOR UPDATE''',
                   (schedule_id, tenant_id, user_id))
    row = cursor.fetchone()
    if not row:
        raise tracking.Unavailable('SCHEDULE_NOT_FOUND')
    return row


def save_schedule(tenant_id, user_id, body, schedule_id=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        current = _locked_schedule(cursor, tenant_id, user_id, schedule_id) if schedule_id else None
        if current and str(current['device_id']) != str(body.device_id):
            raise tracking.Unavailable('DEVICE_NOT_SELECTED')
        if current is None:
            repository.lock_operation_admission(cursor, tenant_id)
            if not repository.lock_selected_device(cursor, tenant_id, user_id, str(body.device_id)):
                raise tracking.Unavailable('DEVICE_NOT_SELECTED')
        groups = tracking.load_groups(cursor, tenant_id, user_id, body.group_ids)
        if body.notification_rule_id and not any(
                rule['rule_id'] == body.notification_rule_id for rule in _rule_rows(cursor, tenant_id)):
            raise tracking.Unavailable('RULE_UNAVAILABLE')
        now = tracking.clock(cursor)
        due = _next_due(body.model_dump(), now) if not current or current['next_due_at'] else None
        values = (body.title, Json([group['group_id'] for group in groups]), body.preset, body.frequency,
                  body.weekday, body.hour, body.minute, body.notification_rule_id, body.notify, due)
        if current:
            cursor.execute('''UPDATE bs_weixin_agent_schedules SET
                title=%s,group_ids=%s,preset=%s,frequency=%s,weekday=%s,hour=%s,minute=%s,
                notification_rule_id=%s,notify=%s,next_due_at=%s WHERE id=%s RETURNING *''',
                (*values, schedule_id))
        else:
            cursor.execute('''INSERT INTO bs_weixin_agent_schedules
                (title,group_ids,preset,frequency,weekday,hour,minute,notification_rule_id,notify,next_due_at,
                 tenant_id,user_id,device_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (*values, tenant_id, user_id, str(body.device_id)))
        result = _public(cursor.fetchone(), now)
        conn.commit()
        return result


def set_paused(tenant_id, user_id, schedule_id, paused):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = _locked_schedule(cursor, tenant_id, user_id, schedule_id)
        now = tracking.clock(cursor)
        due = None if paused else row['next_due_at'] or _next_due(row, now)
        cursor.execute('UPDATE bs_weixin_agent_schedules SET next_due_at=%s WHERE id=%s RETURNING *',
                       (due, schedule_id))
        result = _public(cursor.fetchone(), now)
        conn.commit()
        return result


def _dispatch(cursor, row, now):
    tenant_id, user_id, device_id = row['tenant_id'], row['user_id'], str(row['device_id'])
    due = row['next_due_at']
    # Missed ticks coalesce at the latest elapsed planned time, never move the time window to now.
    cadence = timedelta(days=1 if row['frequency'] == 'daily' else 7)
    due += ((now - due) // cadence) * cadence
    cursor.execute('UPDATE bs_weixin_agent_schedules SET next_due_at=%s WHERE id=%s',
                   (_next_due(row, now), row['id']))
    cursor.execute('''SELECT 1 FROM users u JOIN tenants t ON t.tenant_id=%s
                      WHERE u.user_id=%s AND u.status='active' AND (u.tenant_id=t.tenant_id OR u.role='platform_admin')
                      AND t.status='active' AND (t.expire_at IS NULL OR t.expire_at>clock_timestamp())''',
                   (tenant_id, user_id))
    if not cursor.fetchone():
        return
    groups = tracking.load_groups(cursor, tenant_id, user_id, row['group_ids'], strict=False)
    if not groups:
        return
    start_at, end_at = tracking.preset_bounds(row['preset'], due)
    snapshot = {**dict(row), 'next_due_at': due}
    if start_at == end_at:
        tracking.insert_batch(cursor, tenant_id, user_id, device_id, groups, start_at, end_at,
                              schedule=snapshot, empty=True)
        return
    cursor.execute('SAVEPOINT enqueue_weixin_batch')
    try:
        tracking.validate_bounds(start_at, end_at, due)
        tracking._check_credit(tenant_id)
        digest = tracking.check_device(cursor, tenant_id, user_id, device_id, selected=False)
        tracking.insert_batch(cursor, tenant_id, user_id, device_id, groups, start_at, end_at,
                              digest=digest, schedule=snapshot)
    except (tracking.Unavailable, repository.DeviceOperationUnresolved) as error:
        cursor.execute('ROLLBACK TO SAVEPOINT enqueue_weixin_batch')
        tracking.insert_batch(cursor, tenant_id, user_id, device_id, groups, start_at, end_at,
                              failure=str(error), schedule=snapshot)
    finally:
        cursor.execute('RELEASE SAVEPOINT enqueue_weixin_batch')


def _publish_ready(cursor, now):
    cursor.execute('''SELECT b.* FROM bs_weixin_agent_batches b WHERE b.notification_pending
                      AND NOT EXISTS (
                        SELECT 1 FROM jsonb_array_elements(b.groups_json) g
                        JOIN local_tool_invocations i ON i.id=(g->>'invocation_id')::uuid
                        AND i.tenant_id=b.tenant_id AND i.user_id=b.user_id
                        WHERE (i.state IN ('queued','claimed') AND i.deadline_at>%s)
                           OR i.state IN ('running','cancel_requested')
                      ) ORDER BY b.created_at LIMIT 20 FOR UPDATE OF b SKIP LOCKED''', (now,))
    rows = cursor.fetchall()
    for row in rows:
        result = tracking.project_batch(cursor, row, now)
        if result['state'] in tracking.ACTIVE:
            continue
        # A partial batch has at least one usable group result and emits a single completion event.
        outcome = 'failed' if result['state'] == 'failed' else 'completed'
        event_type = 'task.' + outcome
        rule_id = row['notification_rule_id']
        if rule_id:
            cursor.execute('''SELECT conditions FROM notification_rules
                              WHERE rule_id=%s AND tenant_id=%s AND enabled
                                AND event_type='scheduled.task' FOR SHARE''',
                           (rule_id, row['tenant_id']))
            rule = cursor.fetchone()
            if rule and not task_rule_matches(rule['conditions'], event_type):
                cursor.execute('''UPDATE bs_weixin_agent_batches
                                  SET notification_pending=FALSE,notification_skipped=TRUE WHERE id=%s''',
                               (row['id'],))
                continue
        name = row['title']
        batch_id = str(row['id'])
        ref = publish_in_tx(
            cursor, subject=Subject(row['user_id'], row['tenant_id']), producer='weixin-agent-scheduler',
            event_type=event_type, event_key='batch:' + batch_id,
            resource_ref={'type': 'weixin_summary_batch', 'id': batch_id},
            parameters={'session_title': name, 'task_name': name, 'task_id': str(row['schedule_id']),
                        'event_kind': outcome, 'weixin_batch_status': result['state'],
                        'notify_rule_id': rule_id},
            occurred_at=now, accepted_at=now, expires_at=now + timedelta(seconds=settings.notifications.ttl_seconds),
        )
        cursor.execute('UPDATE bs_weixin_agent_batches SET notification_pending=FALSE,notification_ref=%s WHERE id=%s',
                       (ref, row['id']))


def _expire_invocations(cursor):
    cursor.execute("""WITH stale AS (
        SELECT id FROM local_tool_invocations WHERE tool_name=%s
          AND ((state IN ('queued','claimed') AND deadline_at<=clock_timestamp())
               OR (state IN ('claimed','running','cancel_requested') AND lease_expires_at<=clock_timestamp()::timestamp)
               OR (state IN ('failed','unknown','expired','cancelled') AND arguments_json ? 'grant'))
        ORDER BY deadline_at LIMIT 100 FOR UPDATE SKIP LOCKED
    )
    UPDATE local_tool_invocations AS run SET
        state=CASE WHEN run.state IN ('queued','claimed') AND run.deadline_at<=clock_timestamp() THEN 'expired'
                   WHEN run.state IN ('claimed','running','cancel_requested') THEN 'unknown' ELSE run.state END,
        effect=CASE WHEN run.state IN ('queued','claimed') AND run.deadline_at<=clock_timestamp() THEN 'none'
                    WHEN run.state IN ('claimed','running','cancel_requested') THEN 'unknown' ELSE run.effect END,
        error_code=COALESCE(run.error_code,'RESULT_TIMEOUT'),
        finished_at=COALESCE(run.finished_at,clock_timestamp()::timestamp),
        lease_expires_at=NULL,
        arguments_json=run.arguments_json - 'grant'
    FROM stale WHERE run.id=stale.id""", (tracking.TOOL,))


def run_tick():
    # One transaction per occurrence isolates independent schedules. The due row
    # remains locked until its next date and complete batch have both been written.
    for _ in range(20):
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                now = tracking.clock(cursor)
                cursor.execute('''SELECT * FROM bs_weixin_agent_schedules WHERE next_due_at<=%s
                                  ORDER BY next_due_at LIMIT 1 FOR UPDATE SKIP LOCKED''', (now,))
                row = cursor.fetchone()
                if not row:
                    break
                _dispatch(cursor, row, now)
                conn.commit()
        except Exception as error:
            logger.warning('微信定时总结调度未完成，error={}', type(error).__name__)
            break
    with get_db_connection() as conn:
        cursor = conn.cursor()
        _expire_invocations(cursor)
        _publish_ready(cursor, tracking.clock(cursor))
        conn.commit()
