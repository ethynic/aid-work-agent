"""Owner-scoped tracked groups and WeChat range-summary batch execution."""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from psycopg2.errors import UniqueViolation
from psycopg2.extras import Json
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from src.config.settings import settings
from src.db.database import get_db_connection
from src.local_tools import catalog, device_policy, repository, weixin_summary
from src.local_tools.plugin_manifests import MANIFEST_DIGESTS, load_device_tools
from src.local_tools.security import sha256_hex
from src.saas.services.credit_gate import check_tenant_credit_blocked

TOOL = catalog.WEIXIN_GROUP_RANGE_SUMMARY
PROVIDER = weixin_summary.PROVIDER
BEIJING = weixin_summary.BEIJING
WINDOW = timedelta(days=7)
ACTIVE = {'queued', 'claimed', 'running', 'cancel_requested'}
PRESETS = {'24h': timedelta(hours=24), '3d': timedelta(days=3), '7d': WINDOW}
Name = Annotated[str, StringConstraints(min_length=1, max_length=128,
                                             pattern=r'^[^\x00-\x1f\x7f]+$')]
Preset = Literal['today', '24h', '3d', '7d']


class Unavailable(ValueError):
    pass


class GroupName(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: Name

    @model_validator(mode='after')
    def require_visible_name(self):
        if not self.name.strip():
            raise ValueError('group name is required')
        return self


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    device_id: UUID
    group_ids: list[UUID] = Field(min_length=1)
    preset: Preset | Literal['custom'] = 'today'
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def validate_bounds(self):
        if self.preset == 'custom':
            if self.start_at is None or self.end_at is None or self.start_at >= self.end_at:
                raise ValueError('invalid custom range')
        elif self.start_at is not None or self.end_at is not None:
            raise ValueError('preset dates are calculated at submission')
        return self


def utc_string(value):
    return value.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _milliseconds(value):
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def preset_bounds(preset, end_at):
    if preset == 'today':
        start_at = end_at.astimezone(BEIJING).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_at = end_at - PRESETS[preset]
    return start_at, end_at


def validate_bounds(start_at, end_at, now):
    if not now - WINDOW <= start_at < end_at <= now:
        raise Unavailable('INVALID_RANGE')


def groups(tenant_id, user_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT id,name,deleted_at,created_at FROM bs_weixin_agent_groups
                          WHERE tenant_id=%s AND user_id=%s ORDER BY created_at,id''', (tenant_id, user_id))
        return [dict(row) for row in cursor.fetchall()]


def save_group(tenant_id, user_id, name, group_id=None):
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            if group_id is None:
                cursor.execute('''INSERT INTO bs_weixin_agent_groups (tenant_id,user_id,name)
                                  VALUES (%s,%s,%s) RETURNING id,name,deleted_at,created_at''',
                               (tenant_id, user_id, name))
            else:
                cursor.execute('''UPDATE bs_weixin_agent_groups SET name=%s
                                  WHERE id=%s AND tenant_id=%s AND user_id=%s AND deleted_at IS NULL
                                  RETURNING id,name,deleted_at,created_at''', (name, group_id, tenant_id, user_id))
            row = cursor.fetchone()
            if not row:
                raise Unavailable('GROUP_NOT_FOUND')
            conn.commit()
            return dict(row)
    except UniqueViolation:
        raise Unavailable('GROUP_DUPLICATE') from None


def remove_group(tenant_id, user_id, group_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE bs_weixin_agent_groups SET deleted_at=clock_timestamp()
                          WHERE id=%s AND tenant_id=%s AND user_id=%s AND deleted_at IS NULL''',
                       (group_id, tenant_id, user_id))
        if not cursor.rowcount:
            raise Unavailable('GROUP_NOT_FOUND')
        conn.commit()


def load_groups(cursor, tenant_id, user_id, group_ids, *, strict=True):
    ids = list(dict.fromkeys(str(group_id) for group_id in group_ids))
    cursor.execute('''SELECT id,name FROM bs_weixin_agent_groups WHERE tenant_id=%s AND user_id=%s
                      AND id=ANY(%s::uuid[]) AND deleted_at IS NULL''', (tenant_id, user_id, ids))
    selected = {str(row['id']): row['name'] for row in cursor.fetchall()}
    if strict and len(ids) != len(selected):
        raise Unavailable('GROUP_NOT_FOUND')
    return [{'group_id': group_id, 'name': selected[group_id]} for group_id in ids if group_id in selected]


def clock(cursor):
    cursor.execute('SELECT clock_timestamp() AS now')
    return _milliseconds(cursor.fetchone()['now'])


def check_device(cursor, tenant_id, user_id, device_id, *, selected):
    repository.lock_operation_admission(cursor, tenant_id)
    if selected and not repository.lock_selected_device(cursor, tenant_id, user_id, device_id):
        raise Unavailable('DEVICE_NOT_SELECTED')
    cursor.execute('''SELECT id,selected,status,last_seen_at,capabilities_json,
                    clock_timestamp() AS now,clock_timestamp()::timestamp AS local_now
                    FROM local_tool_devices WHERE id=%s AND tenant_id=%s AND user_id=%s
                    AND status='active' FOR SHARE''', (device_id, tenant_id, user_id))
    current = cursor.fetchone()
    if not current:
        raise Unavailable('DEVICE_NOT_SELECTED')
    readiness = device_policy.device_ready_error(
        current, TOOL, current['local_now'], require_selected=selected, declared_provider=PROVIDER,
    )
    if readiness == 'offline':
        raise Unavailable('DEVICE_OFFLINE')
    capabilities = current['capabilities_json'] or {}
    declared = catalog.declared_tools_for_device(capabilities) or {}
    signed = load_device_tools(settings.agent_runner.plugin_trust_roots, capabilities,
                               {PROVIDER: declared.get(PROVIDER)})
    if readiness or not any(provider == PROVIDER and tool['name'] == TOOL
                            and isinstance(tool.get('annotations'), dict)
                            and tool['annotations'].get('readOnlyHint') is True
                            for provider, tool in signed):
        raise Unavailable('WEIXIN_RANGE_PLUGIN_UNAVAILABLE')
    return capabilities[MANIFEST_DIGESTS][PROVIDER]


def _check_credit(tenant_id):
    if check_tenant_credit_blocked(tenant_id) is not None:
        raise Unavailable('NO_CREDIT')


def insert_batch(cursor, tenant_id, user_id, device_id, selected_groups, start_at, end_at, *,
                 digest=None, failure=None, schedule=None, retry_batch_id=None, empty=False):
    now = clock(cursor)
    start_at, end_at = _milliseconds(start_at), _milliseconds(end_at)
    group_results = []
    for index, group in enumerate(selected_groups):
        if empty:
            group_results.append({**group, 'empty': True})
            continue
        if failure:
            group_results.append({**group, 'error_code': failure})
            continue
        grant = secrets.token_urlsafe(32)
        bounds = {'start_at': utc_string(start_at), 'end_at': utc_string(end_at)}
        args = {'target_name': group['name'], **bounds, 'tenant_id': str(tenant_id), 'grant': grant}
        if retry_batch_id is not None:
            args['allow_open_group'] = True
        invocation_id = repository.create_invocation_in_tx(
            cursor, tenant_id, user_id, device_id, TOOL,
            args, provider_key=PROVIDER, business_kind=weixin_summary.KIND,
            business_ref={**bounds, 'plugin_manifest_digest': digest},
            # Account for the signed serial device's preceding UIA and model timeouts.
            dedupe_key=sha256_hex(grant), deadline_at=now + timedelta(minutes=5 + 4 * index),
        )
        group_results.append({**group, 'invocation_id': invocation_id})
    schedule = schedule or {}
    cursor.execute('''INSERT INTO bs_weixin_agent_batches
        (tenant_id,user_id,device_id,schedule_id,planned_at,retry_batch_id,title,start_at,end_at,
         groups_json,notification_rule_id,notification_pending)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
        (tenant_id, user_id, device_id, schedule.get('id'), schedule.get('next_due_at'), retry_batch_id,
         schedule.get('title'), start_at, end_at, Json(group_results), schedule.get('notification_rule_id'),
         bool(schedule.get('notify'))))
    return cursor.fetchone()


def create_batch(tenant_id, user_id, body):
    _check_credit(tenant_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        device_id = str(body.device_id)
        digest = check_device(cursor, tenant_id, user_id, device_id, selected=True)
        now = clock(cursor)
        if body.preset == 'custom':
            start_at, end_at = _milliseconds(body.start_at), _milliseconds(body.end_at)
        else:
            start_at, end_at = preset_bounds(body.preset, now)
        validate_bounds(start_at, end_at, now)
        selected_groups = load_groups(cursor, tenant_id, user_id, body.group_ids)
        row = insert_batch(cursor, tenant_id, user_id, device_id, selected_groups, start_at, end_at, digest=digest)
        detail = project_batch(cursor, row, now)
        conn.commit()
        return detail


def project_batch(cursor, row, now):
    inv_ids = [group['invocation_id'] for group in row['groups_json'] if group.get('invocation_id')]
    cursor.execute('''SELECT id,state,result_json,error_code,deadline_at FROM local_tool_invocations
                      WHERE id=ANY(%s::uuid[]) AND tenant_id=%s AND user_id=%s AND tool_name=%s''',
                   (inv_ids, row['tenant_id'], row['user_id'], TOOL))
    invocations = {str(item['id']): item for item in cursor.fetchall()}
    groups = []
    for group in row['groups_json']:
        item = {'group_id': group['group_id'], 'name': group['name']}
        inv = invocations.get(group.get('invocation_id'))
        if inv:
            state = inv['state']
            error = inv['error_code']
            if state in {'queued', 'claimed'} and inv['deadline_at'] <= now:
                state, error = 'expired', 'RESULT_TIMEOUT'
            item['state'] = state
            if state == 'succeeded':
                item.update(weixin_summary.normalize_success((inv['result_json'] or {}).get('data')) or {})
            elif error:
                item['error_code'] = error
        elif group.get('empty'):
            item.update(state='succeeded', summary=None, message_count=0, partial=False)
        else:
            item.update(state='failed', error_code=group.get('error_code', 'EXECUTION_UNKNOWN'))
        groups.append(item)

    states = {item['state'] for item in groups}
    if states & ACTIVE:
        state = 'queued' if states == {'queued'} else 'running'
    elif states == {'succeeded'}:
        state = 'partial' if any(item.get('partial') for item in groups) else 'succeeded'
    elif 'succeeded' in states:
        state = 'partial'
    else:
        state = 'failed'

    result = {key: row[key] for key in ('id', 'device_id', 'schedule_id', 'retry_batch_id', 'title',
                                        'start_at', 'end_at', 'created_at')}
    if result['created_at'].tzinfo is None:
        cursor.execute('SELECT %s::timestamp::timestamptz AS created_at', (result['created_at'],))
        result['created_at'] = cursor.fetchone()['created_at']
    result.update(state=state, groups=groups, source='schedule' if row['schedule_id'] else 'manual')
    if row['notification_skipped']:
        result['notification_status'] = 'skipped'
    elif row.get('notification_ref'):
        cursor.execute('''SELECT m.expansion_state,m.route_snapshot,d.state
                          FROM notification_messages m LEFT JOIN notification_deliveries d
                          ON m.notification_id=d.notification_id WHERE m.notification_ref=%s
                          AND m.tenant_id=%s AND m.recipient_user_id=%s''',
                       (row['notification_ref'], row['tenant_id'], row['user_id']))
        deliveries = cursor.fetchall()
        if not deliveries or deliveries[0]['expansion_state'] == 'expired':
            result['notification_status'] = 'failed'
        elif any(entry.get('skipped') == 'rule_event_filtered'
                 for entry in deliveries[0]['route_snapshot'] or []):
            result['notification_status'] = 'skipped'
        elif any(item['state'] in {'failed', 'expired', 'skipped'} for item in deliveries):
            result['notification_status'] = 'failed'
        elif deliveries[0]['expansion_state'] == 'expanded' and all(
                item['state'] in {None, 'accepted', 'delivered', 'displayed', 'read'} for item in deliveries):
            has_target = bool(deliveries[0]['state'])
            result['notification_status'] = 'sent' if has_target or not row['notification_rule_id'] else 'failed'
        else:
            result['notification_status'] = 'queued'
    elif row.get('notification_pending'):
        result['notification_status'] = 'queued'
    return result


def _load_batch(cursor, tenant_id, user_id, batch_id):
    cursor.execute('''SELECT * FROM bs_weixin_agent_batches WHERE id=%s AND tenant_id=%s AND user_id=%s''',
                   (batch_id, tenant_id, user_id))
    row = cursor.fetchone()
    if not row:
        raise Unavailable('BATCH_NOT_FOUND')
    return row


def get_batch(tenant_id, user_id, batch_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        return project_batch(cursor, _load_batch(cursor, tenant_id, user_id, batch_id), clock(cursor))


def list_batches(tenant_id, user_id, group_id=None, offset=0):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        sql = '''SELECT id,created_at,FALSE AS legacy FROM bs_weixin_agent_batches WHERE tenant_id=%s AND user_id=%s'''
        params = [tenant_id, user_id]
        if group_id:
            # V1 records have no stable group ID and participate only in unfiltered history.
            sql += ' AND groups_json @> %s'
            params.append(Json([{'group_id': group_id}]))
        else:
            sql += ''' UNION ALL SELECT id,created_at,TRUE AS legacy FROM local_tool_invocations
                       WHERE tenant_id=%s AND user_id=%s AND tool_name=%s AND business_kind=%s'''
            params.extend((tenant_id, user_id, weixin_summary.TOOL, weixin_summary.KIND))
        cursor.execute(sql + ' ORDER BY created_at DESC,id DESC LIMIT 31 OFFSET %s', (*params, offset))
        rows = cursor.fetchall()
        now = clock(cursor)
        results = []
        for row in rows[:30]:
            if row['legacy']:
                result = _legacy_batch_in_tx(cursor, tenant_id, user_id, row['id'], now)
                if result:
                    results.append(result)
            else:
                result = project_batch(cursor, _load_batch(cursor, tenant_id, user_id, row['id']), now)
                for group in result['groups']:
                    group.pop('summary', None)
                results.append(result)
        return {'batches': results, 'has_more': len(rows) > 30}


def _legacy_batch_in_tx(cursor, tenant_id, user_id, run_id, now):
    cursor.execute("""SELECT id,device_id,created_at::timestamptz,deadline_at,state,
                       arguments_json->>'target_name' AS name,business_ref ? 'grant_consumed' AS granted,
                       result_json->'data'->>'partial'='true' AS partial
                      FROM local_tool_invocations WHERE id=%s AND tenant_id=%s AND user_id=%s
                      AND tool_name=%s AND business_kind=%s""",
                   (run_id, tenant_id, user_id, weixin_summary.TOOL, weixin_summary.KIND))
    row = cursor.fetchone()
    if not row:
        return None
    state = row['state']
    if state in ACTIVE and row['deadline_at'] and row['deadline_at'] <= now and not row['granted']:
        state = 'expired'
    if state in ACTIVE:
        aggregate = 'queued' if state == 'queued' else 'running'
    elif state == 'succeeded':
        aggregate = 'partial' if row['partial'] else 'succeeded'
    else:
        aggregate = 'failed'
    return {'id': str(row['id']), 'legacy': True, 'device_id': str(row['device_id']), 'title': '今日总结',
            'source': 'manual', 'created_at': row['created_at'], 'state': aggregate,
            'groups': [{'group_id': str(row['id']), 'name': row['name'], 'state': state}]}


def retry_group(tenant_id, user_id, batch_id, group_id):
    _check_credit(tenant_id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        original = _load_batch(cursor, tenant_id, user_id, batch_id)
        now = clock(cursor)
        validate_bounds(original['start_at'], original['end_at'], now)
        result = project_batch(cursor, original, now)
        group = next((item for item in result['groups'] if item['group_id'] == group_id), None)
        if group is None:
            raise Unavailable('GROUP_NOT_FOUND')
        if group['state'] in ACTIVE | {'succeeded'}:
            raise Unavailable('GROUP_NOT_RETRYABLE')
        digest = check_device(cursor, tenant_id, user_id, str(original['device_id']), selected=False)
        row = insert_batch(cursor, tenant_id, user_id, str(original['device_id']),
                           [{'group_id': group_id, 'name': group['name']}],
                           original['start_at'], original['end_at'], digest=digest, retry_batch_id=batch_id)
        detail = project_batch(cursor, row, now)
        conn.commit()
        return detail
