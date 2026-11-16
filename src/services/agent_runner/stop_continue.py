"""Trusted stop-and-continue orchestration; the resume chain stays the sole executor.

契约「停止与接续」：v3 停稳回执（runtime_recovery_stop_v1）是唯一触发事实。
本模块只在同一回执事务内追加一次既有 resume 控制的受理写入（服务端作为可信
编排者），领取、恢复前检与执行全部沿原 worker CAS 链；不接受 UI 传入的页面
操作参数或自动重放。任何门不满足都是静默跳过：占用已由停稳事实解除，既有
恢复卡仍是用户的显式路径。
"""

import hashlib
import json
import uuid

from .contracts import canonical_json
from .ownership import lock_runner
from .repository import decoded


def dispatch_continue_resume_in_tx(cursor, runner, checkpoint):
    """Write one accepted resume control under the caller's runner FOR UPDATE lock.

    Called only from the stop-valid receipt transaction after the scope merged
    into verified_local_invocations. Gates mirror the original acceptance
    order of control_repository: parked row, no cancellation, no in-flight
    resume, and original cancellation facts complete with the stop proof
    covering the merged scope.
    """
    from .local_recovery import _cancel_facts
    if (checkpoint.get('local_verification') or {}).get('continue_after_stop') is not True:
        return False
    row = lock_runner(cursor, runner['runner_id'])
    if (row['status'] not in ('waiting', 'paused', 'interrupted') or row['cancel_requested']
            or row['source'] != 'chat' or row.get('resume_control_id')
            or row.get('worker_id') is not None or row['lease_valid']):
        return False
    if checkpoint.get('pending_finalization'):
        return False  # The cancel-only finalizer owns this row's conclusion.
    verified = set(checkpoint.get('verified_local_invocations') or [])
    for fact in _cancel_facts(cursor, row, request=False):
        # The proven device boundary stands in for original calls it stopped
        # before their cooperative receipt could land; anything else must be
        # completion-known before continuation is dispatched.
        if (fact.get('kind') == 'local' and not fact['completion_known']
                and str(fact['id']) not in verified):
            return False
    request_id = row['runner_id'] + ':continue-after-stop'
    cursor.execute('SELECT control_id FROM agent_runner_controls WHERE runner_id=%s AND client_request_id=%s',
        (row['runner_id'], request_id))
    if cursor.fetchone():
        return False
    payload = {'action': 'resume'}
    control_id = 'control_' + uuid.uuid4().hex
    cursor.execute('''INSERT INTO agent_runner_controls
        (control_id,runner_id,tenant_id,scope_key,action,client_request_id,intent_digest,payload,status)
        VALUES (%s,%s,%s,%s,'resume',%s,%s,%s::jsonb,'accepted') RETURNING control_id''',
        (control_id, row['runner_id'], row['tenant_id'], row['scope_key'], request_id,
         hashlib.sha256(canonical_json(payload).encode()).hexdigest(), json.dumps(payload)))
    cursor.execute('''UPDATE agent_runners SET resume_control_id=%s,
        control_revision=control_revision+1,view_revision=view_revision+1,
        updated_at=clock_timestamp() WHERE runner_id=%s''', (control_id, row['runner_id']))
    from .application_public_projection import sync_public_display
    cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s', (row['runner_id'],))
    sync_public_display(cursor, decoded(cursor.fetchone()), advance_view=False)
    from .event_repository import EventRepository
    EventRepository.notify_in_tx(cursor, row)
    return True
