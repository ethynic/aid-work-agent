"""Explicitly end cloud waiting without claiming a desktop operation stopped."""

from .contracts import RunnerError
from .ownership import lock_runner
from .persistence_limits import capped_checkpoint_dumps
from .repository import decoded


def _pending_local_invocation_ids(node):
    active = {call_id for call_id, tool in node.get('tools', {}).items()
              if tool.get('phase') in {'dispatching','waiting'}}
    refs = (node.get('resources') or {}).get('local_invocations') or {}
    ids = {ref['invocation_id'] for ref in refs.values() if ref.get('tool_call_id') in active}
    for child in node.get('children', {}).values():
        ids.update(_pending_local_invocation_ids(child.get('checkpoint') or {}))
    return ids


def stage_interrupted_local_waits(connection_factory, *, after_queue_order=0, limit=20):
    """Converge old logical waits once the original device uses the new execution contract."""
    from loguru import logger
    from src.core.agent_engine.contracts import (ExecutionState, Outcome, TerminalDirective, LOCAL_EXECUTION_INTERRUPTED,
        interrupted_local_output)
    from src.local_tools.catalog import runtime_resource_scope
    from src.local_tools.repository import TERMINAL_STATES, expire_stale_claims_in_tx
    from .application_public_projection import sync_public_display
    from .event_repository import EventRepository
    from .local_recovery import _cancel_facts
    from .worker import execution_result

    with connection_factory() as connection:
        cursor = connection.cursor()
        cursor.execute("""SELECT r.runner_id,r.queue_order FROM agent_runners r
            WHERE r.status IN ('waiting','interrupted') AND NOT r.cancel_requested AND NOT r.pause_requested
              AND r.worker_id IS NULL AND r.lease_until IS NULL AND r.queue_order>%s
              AND EXISTS (SELECT 1 FROM agent_runner_session_claims c WHERE c.owner_runner_id=r.runner_id)
              AND EXISTS (SELECT 1 FROM local_tool_invocations i JOIN local_tool_devices d
                ON d.id=i.device_id AND d.tenant_id=i.tenant_id AND d.user_id=i.user_id
                WHERE i.tenant_id=r.tenant_id AND i.user_id=r.user_id AND i.business_kind='agent_runner'
                  AND i.business_ref->>'runner_id'=r.runner_id
                  AND (i.state='unknown' OR i.effect='unknown' OR (i.state IN ('claimed','running','cancel_requested')
                    AND i.lease_expires_at<NOW()))
                  AND d.capabilities_json->'runtime_resource_admission'->'version'='1'::jsonb)
            ORDER BY r.queue_order LIMIT %s""", (after_queue_order, limit))
        candidates = cursor.fetchall()
    for candidate in candidates:
        try:
            with connection_factory() as connection:
                cursor = connection.cursor()
                row = lock_runner(cursor, candidate['runner_id'])
                checkpoint = row.get('checkpoint') or {}
                node = checkpoint.get('execution') or {}
                wait = node.get('waiting') or {}
                interrupted = node.get('terminal_directive') == TerminalDirective.LOCAL_EXECUTION_INTERRUPTED.value
                local_wait = wait.get('kind') == 'verification' and (wait.get('error_code') in {
                    'LOCAL_EFFECT_VERIFICATION_REQUIRED','LOCAL_HEAL_EFFECT_VERIFICATION_REQUIRED',
                    'LOCAL_DEADLINE_VERIFICATION_REQUIRED'}
                    or interrupted and wait.get('reason') == 'TERMINAL_EFFECT_PENDING_VERIFICATION')
                if (row['status'] not in {'waiting','interrupted'} or row.get('worker_id') is not None
                        or row.get('lease_until') is not None or row['cancel_requested'] or row['pause_requested']
                        or not node or checkpoint.get('pending_finalization')
                        or wait and not local_wait):
                    continue
                facts = _cancel_facts(cursor, row, request=False)
                pending_ids = _pending_local_invocation_ids(node)
                in_flight = [str(fact['id']) for fact in facts if fact.get('kind') == 'local'
                    and str(fact['id']) in pending_ids and fact['state'] in ('claimed','running','cancel_requested')]
                expired = 0
                if in_flight:
                    cursor.execute('''SELECT i.id,d.capabilities_json FROM local_tool_invocations i
                        JOIN local_tool_devices d ON d.id=i.device_id AND d.tenant_id=i.tenant_id AND d.user_id=i.user_id
                        WHERE i.tenant_id=%s AND i.user_id=%s AND i.id=ANY(%s::uuid[])
                          AND i.lease_expires_at<NOW()
                        ORDER BY d.id FOR SHARE OF d''', (row['tenant_id'], row['user_id'], in_flight))
                    originals = [str(item['id']) for item in cursor.fetchall()
                        if runtime_resource_scope(item['capabilities_json']) is not None]
                    if originals:
                        expired = expire_stale_claims_in_tx(cursor, tenant_id=row['tenant_id'], invocation_ids=originals)
                        if expired:
                            facts = _cancel_facts(cursor, row, request=False)
                unknown = [str(fact['id']) for fact in facts if fact.get('kind') == 'local'
                    and str(fact['id']) in pending_ids and (fact['state'] == 'unknown' or fact['effect'] == 'unknown')]
                if not (interrupted or unknown) or any(not fact.get('known') if fact.get('kind') == 'domain'
                        else fact.get('state') not in TERMINAL_STATES or fact.get('kind') != 'local' and not fact.get('completion_known')
                        for fact in facts):
                    if expired:
                        connection.commit()
                    continue
                if not interrupted:
                    cursor.execute('''SELECT DISTINCT d.capabilities_json FROM local_tool_invocations i
                        JOIN local_tool_devices d ON d.id=i.device_id AND d.tenant_id=i.tenant_id AND d.user_id=i.user_id
                        WHERE i.tenant_id=%s AND i.user_id=%s AND i.id=ANY(%s::uuid[])''',
                        (row['tenant_id'], row['user_id'], unknown))
                    devices = cursor.fetchall()
                    if not devices or any(runtime_resource_scope(device['capabilities_json']) is None for device in devices):
                        if expired:
                            connection.commit()
                        continue
                state = ExecutionState.restore(node)
                state.outcome, state.waiting = Outcome.FAILED, None
                state.error_code, state.output = LOCAL_EXECUTION_INTERRUPTED, interrupted_local_output(state.output)
                checkpoint = {**checkpoint, 'execution': state.checkpoint(),
                    'pending_finalization': {**execution_result(state),
                        'duration_ms': checkpoint.get('execution_duration_ms', 0)}}
                cursor.execute('''UPDATE agent_runners SET status='finalizing',checkpoint=%s::jsonb,wake_at=NULL,
                    revision=revision+1,view_revision=view_revision+1,updated_at=clock_timestamp()
                    WHERE runner_id=%s RETURNING *''', (capped_checkpoint_dumps(checkpoint), row['runner_id']))
                result = sync_public_display(cursor, decoded(cursor.fetchone()), advance_view=False)
                EventRepository.notify_in_tx(cursor, row, after=result)
                connection.commit()
        except Exception as error:
            logger.warning('runtime interrupted wait finalization failed runner={} error={}',
                           candidate['runner_id'], type(error).__name__)
    return candidates[-1]['queue_order'] if len(candidates) == limit else 0


def end_waiting(repository, principal, runner_id):
    from .local_recovery import _cancel_facts, owned_invocations
    from .control_repository import close_pending_controls
    from .application_public_projection import sync_public_display
    from .event_repository import EventRepository
    with repository.connection_factory() as connection:
        cursor = connection.cursor()
        row = lock_runner(cursor, runner_id)
        repository.assert_owner(principal, row)
        if row['session_kind'] != 'web' or row['source'] != 'chat':
            raise RunnerError('LOCAL_END_WAIT_FORBIDDEN', 403)
        checkpoint = row.get('checkpoint') or {}
        if checkpoint.get('detached_local_wait') is True or checkpoint.get('ended_undispatched_wait') is True:
            return row  # Retrying a lost response cannot affect another owner.
        if (row['status'] not in {'waiting', 'paused', 'interrupted'}
                or row.get('worker_id') is not None or row.get('lease_until') is not None
                or checkpoint.get('pending_finalization')):
            raise RunnerError('LOCAL_END_WAIT_NOT_READY', 409)
        root = checkpoint.get('execution') or {}
        from .unbound_local import resolve_tree_in_tx
        converged = resolve_tree_in_tx(cursor, row, root)
        # Serialize the hold with all create/claim/permit device boundaries.
        ids = sorted({fact['request']['device_id'] for _, phase, fact
            in owned_invocations(root, runner_id) if phase is not None})
        undispatched = converged and not ids
        if not ids and not undispatched:
            raise RunnerError('LOCAL_END_WAIT_NOT_APPLICABLE', 409)
        from src.local_tools.repository import lock_operation_admission
        lock_operation_admission(cursor, row['tenant_id'])
        cursor.execute('SELECT id FROM local_tool_devices WHERE tenant_id=%s AND user_id=%s AND id=ANY(%s::uuid[]) ORDER BY id FOR UPDATE',
            (row['tenant_id'], row['user_id'], ids))
        if len(cursor.fetchall()) != len(ids):
            raise RunnerError('LOCAL_INVOCATION_OWNER_VERIFICATION_REQUIRED', 409)
        facts = _cancel_facts(cursor, row, request=False)
        from .public_view import local_recovery_applicable
        if not undispatched and not local_recovery_applicable(row):
            raise RunnerError('LOCAL_END_WAIT_NOT_APPLICABLE', 409)
        if (not undispatched and not any(fact.get('state') == 'unknown' or fact.get('effect') == 'unknown' for fact in facts)
                or any(fact.get('kind') == 'domain' and not fact.get('known') for fact in facts)):
            raise RunnerError('LOCAL_END_WAIT_NOT_APPLICABLE', 409)
        # Request stop, retain all original facts, then use the existing
        # cancel-only finalizer for history/usage and atomic claim release.
        _cancel_facts(cursor, row, request=True)
        if any(fact.get('kind') == 'local' and not fact['completion_known']
                and not fact['holdable'] for fact in facts):
            raise RunnerError('LOCAL_END_WAIT_NOT_APPLICABLE', 409)
        held = sorted(str(fact['id']) for fact in facts if fact.get('kind') == 'local'
            and not fact['completion_known'] and not fact.get('readonly_db'))
        checkpoint = ({**checkpoint, 'ended_undispatched_wait': True} if undispatched else
            {**checkpoint, 'detached_local_wait': True, 'detached_local_invocations': held})
        checkpoint.pop('cancel_completion_blocked', None)
        close_pending_controls(cursor, runner_id, 'CONTROL_EXECUTION_CLOSED')
        cursor.execute('''UPDATE agent_runners SET checkpoint=%s::jsonb,cancel_requested=TRUE,
            pause_requested=FALSE,resume_control_id=NULL,revision=revision+1,
            control_revision=control_revision+1,view_revision=view_revision+1,
            updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *''',
            (capped_checkpoint_dumps(checkpoint), runner_id))
        result = sync_public_display(cursor, decoded(cursor.fetchone()), advance_view=False)
        result = EventRepository.notify_in_tx(cursor, row, after=result)
        connection.commit()
        return result
