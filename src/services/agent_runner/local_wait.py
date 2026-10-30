"""Explicitly end cloud waiting without claiming a desktop operation stopped."""

from .contracts import RunnerError
from .ownership import lock_runner
from .persistence_limits import capped_checkpoint_dumps
from .repository import decoded


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
