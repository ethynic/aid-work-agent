"""Consume scheduled WeCom records under the existing proxy device group."""

import asyncio

from loguru import logger

from src.db.database import get_db_connection
from src.local_tools import repository
from src.local_tools.wecom_send_status import SendStatusScopeError

from . import proxy_tasks, scheduled_delivery as delivery, scheduled_sends as schedules

GROUP_MATCH = 'tenant_id=%s AND device_id=%s AND account=%s'


def _refresh_group(group):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        proxy_tasks.lock_device_group_in_tx(cursor, schedules._group_task(group))
        delivery.retire_in_tx(cursor, GROUP_MATCH + ' AND deadline_at<clock_timestamp()',
                              group, 'expired', 'SEND_WINDOW_EXPIRED')
        cursor.execute('''SELECT s.id,s.tenant_id,
                                 (i.state IN ('claimed','running','cancel_requested') AND i.lease_expires_at<now()
                                  OR r.state IN ('claimed','running','cancel_requested') AND r.lease_expires_at<now()) AS stale
                            FROM bs_wecom_scheduled_sends s
                            LEFT JOIN local_tool_invocations i ON i.id=s.invocation_id AND i.tenant_id=s.tenant_id
                            LEFT JOIN local_tool_invocations r ON r.id=s.reconcile_invocation_id AND r.tenant_id=s.tenant_id
                           WHERE s.tenant_id=%s AND s.device_id=%s AND s.account=%s
                             AND (s.status IN ('dispatched','running')
                                  OR (s.status='succeeded' AND (s.released_at IS NULL OR s.billed_at IS NULL))
                                  OR (s.status='unknown' AND (s.released_at IS NULL
                                       OR i.result_json ? '_late_result' OR r.result_json ? '_late_result'
                                       OR r.state IN ('claimed','running','cancel_requested','succeeded'))))
                           ORDER BY s.due_at,s.plan_order,s.target_order''', group)
        rows = cursor.fetchall()
        conn.commit()
    if any(row['stale'] for row in rows):
        repository.expire_stale_claims()
    for row in rows:
        try:
            delivery.settle_execution(row['tenant_id'], row['id'])
        except Exception:
            logger.opt(exception=True).error(f'后端日志：核定企微定时发送失败 execution={row["id"]} tenant={row["tenant_id"]}')
    _finalize_once(group)


def _finalize_once(group):
    # Separate from group/execution locks. Configuration changes always take
    # the plan row before the group; never enter that order backwards.
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE scheduled_tasks p SET status=CASE WHEN EXISTS (
                              SELECT 1 FROM bs_wecom_scheduled_sends s WHERE s.tenant_id=p.tenant_id
                                AND s.task_id=p.task_id AND s.status<>'expired'
                                AND s.due_at=p.last_run_at AT TIME ZONE 'Asia/Shanghai'
                          ) THEN 'completed' ELSE 'expired' END, updated_at=now()
                           WHERE p.tenant_id=%s AND p.schedule_type='once' AND p.status='active'
                             AND p.next_due_at IS NULL AND p.last_run_at IS NOT NULL
                             AND EXISTS (SELECT 1 FROM bs_wecom_scheduled_sends s WHERE s.tenant_id=p.tenant_id
                                 AND s.task_id=p.task_id AND s.device_id=%s AND s.account=%s
                                 AND s.due_at=p.last_run_at AT TIME ZONE 'Asia/Shanghai')
                             AND NOT EXISTS (SELECT 1 FROM bs_wecom_scheduled_sends s WHERE s.tenant_id=p.tenant_id
                                 AND s.task_id=p.task_id AND s.status IN ('queued','dispatched','running')
                                 AND s.due_at=p.last_run_at AT TIME ZONE 'Asia/Shanghai')''', group)
        conn.commit()


def _head(group):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        row = schedules.next_execution_in_tx(cursor, group)
        pending = row is not None or schedules.group_has_turn_in_tx(cursor, group, include_queued=True)
        return row, pending


def _bind_head(group, execution_id):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        head = schedules.next_execution_in_tx(cursor, group)
        if not head or head['id'] != execution_id:
            return False
        tenant_id, device_id, _ = group
        repository.lock_operation_admission(cursor, tenant_id)
        if not repository.lock_active_device(cursor, tenant_id, device_id):
            return False
        repository.assert_device_stop_available(cursor, tenant_id, device_id)
        cursor.execute('''SELECT 1 FROM local_tool_invocations WHERE tenant_id=%s AND device_id=%s
                            AND business_kind='wecom_proxy_monitor_read' AND state='queued'
                            AND deadline_at>=clock_timestamp() LIMIT 1''',
                       (tenant_id, device_id))
        if cursor.fetchone():
            return False
        row = delivery._locked_execution(cursor, tenant_id, execution_id)
        cursor.execute('SELECT clock_timestamp() AS now')
        if row['status'] != 'queued' or cursor.fetchone()['now'] > row['deadline_at']:
            return False
        invocation_id = delivery.invocation_in_tx(cursor, row)
        if row['invocation_id']:
            cursor.execute('''UPDATE local_tool_invocations SET state='queued',effect=NULL,finished_at=NULL
                               WHERE tenant_id=%s AND id=%s AND state='unknown' AND effect='none'
                                 AND claim_token_hash IS NULL AND started_at IS NULL RETURNING id''',
                           (tenant_id, invocation_id))
            if not cursor.fetchone():
                return False
        cursor.execute('''UPDATE bs_wecom_scheduled_sends SET invocation_id=%s,status='dispatched'
                           WHERE tenant_id=%s AND id=%s''', (invocation_id, tenant_id, execution_id))
        conn.commit()
        return True


def _reconcile_candidates(group, *, held_only):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''SELECT s.id,s.tenant_id,s.user_id,s.device_id FROM bs_wecom_scheduled_sends s
                            JOIN local_tool_invocations i ON i.id=s.invocation_id AND i.tenant_id=s.tenant_id
                           WHERE s.tenant_id=%s AND s.device_id=%s AND s.account=%s AND s.status='unknown'
                             AND (NOT %s OR s.released_at IS NULL) AND s.reconcile_invocation_id IS NULL
                             AND (i.result_json->'data' ? 'receipt_id'
                                 OR i.result_json->'_late_result'->'data' ? 'receipt_id')
                           ORDER BY s.due_at,s.plan_order,s.target_order''', (*group, held_only))
        return cursor.fetchall()


def _bind_reconciliation(candidate):
    tenant_id, device_id = candidate['tenant_id'], str(candidate['device_id'])
    with get_db_connection() as conn:
        cursor = conn.cursor()
        repository.lock_operation_admission(cursor, tenant_id)
        if not repository.lock_active_device(cursor, tenant_id, device_id):
            return
        row = delivery._locked_execution(cursor, tenant_id, candidate['id'])
        if not row or row['status'] != 'unknown' or row['reconcile_invocation_id']:
            return
        invocation_id = delivery.invocation_in_tx(cursor, row, read=True)
        cursor.execute('''UPDATE bs_wecom_scheduled_sends SET reconcile_invocation_id=%s
                           WHERE tenant_id=%s AND id=%s''', (invocation_id, tenant_id, row['id']))
        conn.commit()


async def run_tick(group, *, runner_busy):
    """True reserves the queue turn for a scheduled send or its live hold."""
    from . import proxy_api

    await asyncio.to_thread(_refresh_group, group)
    head, pending = await asyncio.to_thread(_head, group)
    if runner_busy:
        return pending
    if head:
        device, error = await proxy_api._task_device(head['tenant_id'], head['user_id'], head['device_id'],
                                                     (delivery.SEND_TOOL, 'wecom_read_sessions'))
        if error is None:
            account, error = await proxy_api._identity_probe(head['tenant_id'], head['user_id'], device, head['session'])
            if error is None and account == head['account']:
                try:
                    await asyncio.to_thread(_bind_head, group, head['id'])
                except repository.DeviceOperationUnresolved:
                    pass  # Existing device work gets its turn through the same admission fence.
        return True
    candidates = await asyncio.to_thread(_reconcile_candidates, group, held_only=pending)
    for row in candidates:
        _, error = await proxy_api._task_device(row['tenant_id'], row['user_id'], row['device_id'], (delivery.READ_TOOL,))
        if error is None:
            try:
                await asyncio.to_thread(_bind_reconciliation, row)
            except (repository.DeviceOperationUnresolved, SendStatusScopeError):
                continue
    return pending
