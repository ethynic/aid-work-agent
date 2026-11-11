"""Persisted one-shot WeCom sends owned by the existing proxy device dispatcher."""
from __future__ import annotations

import asyncio

from loguru import logger

from src.db.database import get_db_connection
from src.local_tools import repository
from src.local_tools.proxy_tool import WecomMessageSendTool
from src.local_tools.wecom_send_status import SendStatusScopeError, original_scope

from . import proxy_tasks

SEND_TOOL = 'wecom_message_send'
SEND_KIND = 'wecom_proxy_opening'
RECONCILE_TOOL = 'wecom_send_status'


def _load_group(key):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''SELECT s.id AS target_id,s.task_id,s.session,s.web_session_id,s.opening_send_status,
                      s.opening_send_invocation_id,s.opening_send_reconcile_id,
                      t.tenant_id,t.user_id,t.device_id,t.opening_message,
                      to_jsonb(i) AS invocation,to_jsonb(r) AS reconciliation
                 FROM bs_wecom_proxy_task_sessions s JOIN bs_wecom_proxy_tasks t
                   ON t.tenant_id=s.tenant_id AND t.user_id=s.user_id AND t.id=s.task_id
                 LEFT JOIN local_tool_invocations i ON i.id=s.opening_send_invocation_id
                   AND i.tenant_id=s.tenant_id AND i.user_id=s.user_id
                 LEFT JOIN local_tool_invocations r ON r.id=s.opening_send_reconcile_id
                   AND r.tenant_id=s.tenant_id AND r.user_id=s.user_id
                WHERE t.tenant_id=%s AND t.device_id=%s AND t.wecom_account_id=%s
                  AND (s.opening_send_status IN ('pending','sending')
                    OR (s.opening_send_status='unknown' AND
                        (i.result_json IS NOT NULL OR r.id IS NOT NULL)))
                ORDER BY s.id''', key)
        return [dict(row) for row in cursor.fetchall()]


def _finish(row, previous, status, error=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if previous == 'pending':
            task = proxy_tasks._task_for_update(cursor, row['tenant_id'], row['user_id'], row['task_id'])
            proxy_tasks.lock_device_group_in_tx(cursor, task)
            if proxy_tasks.device_group_busy_in_tx(cursor, task, include_pending_opening=False):
                return False  # A stale readiness failure cannot skip someone else's turn.
        cursor.execute(
            '''UPDATE bs_wecom_proxy_task_sessions
                  SET opening_send_status=%s,opening_send_error=%s
                WHERE tenant_id=%s AND user_id=%s AND task_id=%s AND id=%s
                  AND opening_send_status=%s''',
            (status, error, row['tenant_id'], row['user_id'], row['task_id'], row['target_id'], previous))
        changed = cursor.rowcount
        conn.commit()
    if changed and error:
        logger.warning(f'后端日志：企微首启发送 task={row["task_id"]} target={row["target_id"]} '
                       f'invocation={row["opening_send_invocation_id"]} code={error}')
    return bool(changed)


def _bind(row, *, reconcile=False):
    """Creation and the pointer to the original invocation commit together."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        tenant_id, user_id = row['tenant_id'], row['user_id']
        task = proxy_tasks._task_for_update(cursor, tenant_id, user_id, row['task_id'])
        if not reconcile:
            proxy_tasks.lock_device_group_in_tx(cursor, task)
            if proxy_tasks.device_group_busy_in_tx(cursor, task, include_pending_opening=False):
                return False
        cursor.execute(
            '''SELECT * FROM bs_wecom_proxy_task_sessions
                WHERE tenant_id=%s AND user_id=%s AND task_id=%s AND id=%s FOR UPDATE''',
            (tenant_id, user_id, task['id'], row['target_id']))
        target = cursor.fetchone()
        if reconcile:
            if target['opening_send_status'] != 'unknown' or target['opening_send_reconcile_id']:
                return False
            # The repository revalidates this scope before creating and delivering the read.
            repository.lock_operation_admission(cursor, tenant_id)
            scope = original_scope(cursor, tenant_id, user_id, target['web_session_id'],
                                   str(target['opening_send_invocation_id']), device_id=str(task['device_id']))
            tool_name, kind = RECONCILE_TOOL, RECONCILE_TOOL
            arguments = {'receipt_id': scope['receipt_id']}
            binding = {'version': 1, 'send_invocation_id': scope['send_invocation_id']}
        else:
            if (target['opening_send_status'] != 'pending' or target['status'] != proxy_tasks.TARGET_ACTIVE
                    or task['status'] != proxy_tasks.TASK_RUNNING or task['stop_requested']):
                return False
            tool_name, kind = SEND_TOOL, SEND_KIND
            arguments = {'session': target['session'], 'text': task['opening_message']}
            binding = {'version': 1, 'task_id': task['id'], 'target_id': target['id']}
        invocation_id = repository.create_invocation_in_tx(
            cursor, tenant_id, user_id, str(task['device_id']), tool_name, arguments,
            target['web_session_id'], provider_key='wecom', business_kind=kind,
            business_ref=binding, dedupe_key=f'proxy-opening:{target["id"]}', verify_binding=True)
        if reconcile:
            cursor.execute('''UPDATE bs_wecom_proxy_task_sessions SET opening_send_reconcile_id=%s
                              WHERE id=%s''', (invocation_id, target['id']))
        else:
            cursor.execute('''UPDATE bs_wecom_proxy_task_sessions
                                 SET opening_send_invocation_id=%s,opening_send_status='sending'
                               WHERE id=%s''', (invocation_id, target['id']))
        conn.commit()
        return True


def _expire_unclaimed(invocation):
    # Same wait budget as ordinary WeCom text send. An unclaimed call cannot
    # be allowed to start indefinitely after its device went away.
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''UPDATE local_tool_invocations SET state='expired',effect='none',finished_at=NOW()
                WHERE id=%s AND tenant_id=%s AND state='queued'
                  AND created_at<=NOW()-(%s*INTERVAL '1 second')''',
            (invocation['id'], invocation['tenant_id'], WecomMessageSendTool.timeout_seconds))
        changed = cursor.rowcount
        conn.commit()
    return changed


def _active(invocation):
    if invocation['state'] == 'queued':
        return not _expire_unclaimed(invocation)
    if invocation['state'] not in repository.TERMINAL_STATES:
        if repository.claim_lease_expired(invocation['id'], invocation['tenant_id']):
            repository.expire_stale_claims()
        return True  # Observe the new authoritative state on the next tick.
    return False


def _confirmed(invocation, row):
    data = (invocation.get('result_json') or {}).get('data') or {}
    return (invocation['state'] == 'succeeded' and data.get('verification') == 'local-record'
            and data.get('session') == row['session'])


def _settle_send(row):
    invocation = row['invocation']
    if _active(invocation):
        return False
    current = repository.observed_invocation(invocation)
    if _confirmed(current, row):
        _finish(row, 'sending', 'succeeded')
    elif current['state'] in ('failed', 'cancelled', 'expired') and (
            current['effect'] == 'none' or not current['started_at']):
        _finish(row, 'sending', 'failed', current['error_code'] or 'OPENING_SEND_NOT_EXECUTED')
    elif current['state'] == 'queued':
        _finish(row, 'sending', 'failed', 'OPENING_SEND_NOT_EXECUTED')
    else:
        _finish(row, 'sending', 'unknown', current['error_code'] or 'EXECUTION_UNKNOWN')
    return True


async def _reconcile(row):
    original = repository.observed_invocation(row['invocation'])
    if _confirmed(original, row):
        await asyncio.to_thread(_finish, row, 'unknown', 'succeeded')
        return
    read = row['reconciliation']
    if read:
        if await asyncio.to_thread(_active, read):
            return
        read = repository.observed_invocation(read)
        data = (read.get('result_json') or {}).get('data') or {}
        confirmed = read['state'] == 'succeeded' and data.get('send_status') == 'confirmed'
        await asyncio.to_thread(_finish, row, 'unknown', 'succeeded' if confirmed else 'unconfirmed',
                                None if confirmed else 'EXECUTION_UNKNOWN')
        return
    try:
        await asyncio.to_thread(_bind, row, reconcile=True)
    except SendStatusScopeError:
        await asyncio.to_thread(_finish, row, 'unknown', 'unconfirmed', 'EXECUTION_UNKNOWN')
    except repository.DeviceOperationUnresolved:
        # Original evidence remains durable; wait for the same device's existing admission.
        return


async def run_tick(key, *, runner_busy):
    """Called only with the shared group lock. True leaves ordinary work behind this send."""
    from . import proxy_api

    rows = await asyncio.to_thread(_load_group, key)
    sending = next((row for row in rows if row['opening_send_status'] == 'sending'), None)
    if sending and not await asyncio.to_thread(_settle_send, sending):
        return True
    for row in rows:
        if row['opening_send_status'] != 'pending':
            continue
        if runner_busy:
            return True
        tenant_id, user_id = row['tenant_id'], row['user_id']
        _, error = await proxy_api._task_device(tenant_id, user_id, row['device_id'], (SEND_TOOL,))
        code = None
        tool = WecomMessageSendTool()
        if error is not None:
            from .proxy_dispatcher import proxy_monitor_error_parts
            code, _ = proxy_monitor_error_parts(error)
        elif tool._tool_credit_price() > 0 and await tool._tenant_credit_blocked(tenant_id):
            code = 'NO_CREDIT'
        else:
            try:
                await asyncio.to_thread(_bind, row)
                # A peer can take occupancy after this tick's snapshot. Yield
                # after a rejected binding as well and reload the group next tick.
                return True
            except repository.DeviceOperationUnresolved as exc:
                code = str(exc)
        if code and not await asyncio.to_thread(_finish, row, 'pending', 'failed', code):
            return True
    if not runner_busy:
        # Reconciliation never sits ahead of a new send or holds the monitor queue.
        for row in rows:
            if row['opening_send_status'] == 'unknown':
                await _reconcile(row)
    return False
