"""Resume the original logical claim; never enqueue a replacement runner."""

import copy

from src.db.database import get_db_connection
from .contracts import RunnerError
from .ownership import Attempt, lock_runner, LeaseLost, StopRequested
from .execution_repository import release_proof
from .persistence_limits import capped_checkpoint_dumps, capped_snapshot_dumps
from .repository import decoded
from .control_contracts import RunnerControl
from .control_repository import ControlRepository
from .control_projection import merge_control_projection
from .event_repository import EventRepository
from .application_public_projection import sync_public_display, project_public_snapshot


def lock_original_claim(cursor, row):
    cursor.execute('''SELECT * FROM agent_runner_session_claims WHERE scope_key=%s
        AND session_kind=%s AND session_id=%s FOR UPDATE''',
        (row['scope_key'],row['session_kind'],row['session_id']))
    claim = cursor.fetchone()
    if not claim or claim['owner_runner_id'] != row['runner_id']:
        raise RunnerError('RECOVERY_CLAIM_LOST',409)
    return claim


class RecoveryRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    def next_candidate(self):
        rows = self.next_candidates()
        return rows[0] if rows else None

    def next_candidates(self, *, after_queue_order=0, limit=16):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError('RECOVERY_CANDIDATE_LIMIT_INVALID')
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute('''SELECT r.* FROM agent_runners r WHERE r.status IN ('paused','waiting','interrupted')
                AND NOT r.cancel_requested AND r.resume_control_id IS NOT NULL
                AND (r.lease_until IS NULL OR r.lease_until<=clock_timestamp())
                AND EXISTS (SELECT 1 FROM agent_runner_session_claims c
                    WHERE c.scope_key=r.scope_key AND c.session_kind=r.session_kind
                    AND c.session_id=r.session_id AND c.owner_runner_id=r.runner_id)
                AND r.queue_order>%s ORDER BY r.queue_order LIMIT %s''',(after_queue_order,limit))
            return [decoded(row) for row in cursor.fetchall()]



    def claim_resume(self, runner_id, worker_id, lease_seconds, *, revision, control_id, checkpoint,
                     recovery_port=None, source_port=None, prepared_source=None):
        """Application preflight prepares state before this short CAS transaction.

        The caller must reauthorize dispatch after acquisition. The input hash and
        queue position are immutable; checkpoint and wait consumption commit with
        the new execution attempt or nothing changes.
        """
        if not isinstance(checkpoint,dict) or checkpoint.get('applied_control_id') != control_id:
            raise ValueError('RECOVERY_CONTROL_CHECKPOINT_MISMATCH')
        if lease_seconds <= 0:
            raise ValueError('INVALID_LEASE')
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor,runner_id)
            if row['status'] not in ('paused','waiting','interrupted') or row['cancel_requested'] or row['lease_valid']:
                return None
            if row['revision'] != revision or row.get('resume_control_id') != control_id:
                return None
            lock_original_claim(cursor,row)
            if source_port is not None:
                source_port.authorize_row_in_tx(cursor,row,execute=True,prepared=prepared_source)
            cursor.execute('SELECT * FROM agent_runner_controls WHERE runner_id=%s AND control_id=%s FOR UPDATE', (runner_id,control_id))
            control = cursor.fetchone()
            if not control or control['status'] != 'accepted' or control['action'] not in ('resume','reply','browser_complete'):
                raise RunnerError('RECOVERY_CONTROL_STALE',409)
            if control['action'] in ('reply','browser_complete'):
                request = RunnerControl(client_request_id=control['client_request_id'], **control['payload'])
                ControlRepository.assert_wait(row, request)
            if row['checkpoint'] and row['checkpoint'].get('pending_finalization'):
                raise RunnerError('RECOVERY_FINALIZATION_PENDING',409)
            if recovery_port is not None:
                recovery_port.assert_claim_in_tx(cursor,row,control,worker_id=worker_id)
            number = row['attempt'] + 1
            cursor.execute('''UPDATE agent_runner_controls SET status='consumed',consumed_attempt=%s,
                consumed_at=clock_timestamp() WHERE control_id=%s AND status='accepted' ''',(number,control_id))
            cursor.execute('''UPDATE agent_runners SET status='running',attempt=%s,worker_id=%s,
                lease_until=clock_timestamp()+(%s*INTERVAL '1 second'),checkpoint=%s::jsonb,
                pause_requested=FALSE,resume_control_id=NULL,revision=revision+1,
                control_revision=control_revision+1,view_revision=view_revision+1,updated_at=clock_timestamp()
                WHERE runner_id=%s RETURNING *''',
                (number,worker_id,lease_seconds,capped_checkpoint_dumps(checkpoint),runner_id))
            result = decoded(cursor.fetchone())
            result = sync_public_display(cursor,result,advance_view=False)
            if recovery_port is not None:
                recovery_port.assert_claim_in_tx(cursor,row,control,worker_id=worker_id)
            result = EventRepository.notify_in_tx(cursor,row,after=result)
            if source_port is not None:
                source_port.authorize_row_in_tx(cursor,result,execute=True,prepared=prepared_source)
            # Any source/config/input or presentation lock may have consumed the
            # new lease. All locks are now held; fence the actual Attempt last.
            try:
                lock_runner(cursor,runner_id,Attempt(runner_id,worker_id,number),dispatch=True)
            except LeaseLost as error:
                # No new Attempt or control consumption has committed. Roll
                # the whole transaction back and preserve the original command.
                from .source_receipts import SourceUnavailable
                unavailable=SourceUnavailable('SOURCE_RECOVERY_ATTEMPT_UNAVAILABLE')
                unavailable.public_verification='恢复执行许可尚待核对，任务与原命令已保留。'
                raise unavailable from error
            connection.commit()
            return result

    def hold_resume(self, runner_id, *, revision, control_id, public_verification=None):
        """A lost source proof does not consume a parked control or its claim."""
        from .source_receipts import SourceUnavailable
        question=public_verification or SourceUnavailable.public_verification
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            row=lock_runner(cursor,runner_id)
            if (row['status'] not in {'paused','waiting','interrupted'} or row['cancel_requested']
                    or row['lease_valid'] or row['revision']!=revision
                    or row.get('resume_control_id')!=control_id):
                return False
            lock_original_claim(cursor,row)
            cursor.execute('''SELECT status FROM agent_runner_controls
                WHERE runner_id=%s AND control_id=%s FOR UPDATE''',(runner_id,control_id))
            control=cursor.fetchone()
            if not control or control['status']!='accepted':
                return False
            snapshot=copy.deepcopy(row.get('public_snapshot') or {})
            snapshot['progress']=question
            # Keep the original clarification/child/human wait identity and
            # question actionable. Only a missing wait needs a verification card.
            if not isinstance(snapshot.get('waiting'),dict):
                snapshot['waiting']={'kind':'verification','question':question}
            snapshot=project_public_snapshot(cursor,row,snapshot=snapshot)
            if snapshot==(row.get('public_snapshot') or {}):
                return False
            cursor.execute('''UPDATE agent_runners SET public_snapshot=%s::jsonb,
                view_revision=view_revision+1,updated_at=clock_timestamp()
                WHERE runner_id=%s''',(capped_snapshot_dumps(snapshot),runner_id))
            EventRepository.notify_in_tx(cursor,row)
            connection.commit()
            return True

    def acknowledge_pause(self, attempt, revision, checkpoint, snapshot):
        """Only the application that stopped every owned leaf can call this.

        An unstarted queued runner reaches this method only after normal FIFO
        acquisition; no history, compression, model or tool was initialized.
        """
        if checkpoint.get('unstarted') is True:
            if any(checkpoint.get(key) for key in ('execution','children','tools','pending_finalization')):
                raise ValueError('PAUSE_CHECKPOINT_NOT_UNSTARTED')
        else:
            root = checkpoint.get('execution') or {}
            if root.get('outcome') != 'paused':
                raise ValueError('PAUSE_CHECKPOINT_NOT_ACKNOWLEDGED')
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor,attempt.runner_id,attempt)
            if row['cancel_requested']:
                raise StopRequested('cancel')
            if row['status'] != 'running' or row['revision'] != revision or not row.get('pause_requested'):
                raise LeaseLost('PAUSE_REVISION_CHANGED')
            lock_original_claim(cursor,row)
            cursor.execute('''UPDATE agent_runner_controls SET status='consumed',consumed_attempt=%s,
                consumed_at=clock_timestamp() WHERE runner_id=%s AND action='pause' AND status='accepted' ''',
                (attempt.number,attempt.runner_id))
            checkpoint = {**checkpoint,'released_attempt':release_proof(attempt,'paused',
                'unstarted_pause' if checkpoint.get('unstarted') is True else 'parked')}
            snapshot = merge_control_projection(snapshot,row.get('public_snapshot'))
            snapshot = project_public_snapshot(cursor,row,checkpoint=checkpoint,snapshot=snapshot,status='paused')
            cursor.execute('''UPDATE agent_runners SET status='paused',worker_id=NULL,lease_until=NULL,
                checkpoint=%s::jsonb,public_snapshot=%s::jsonb,revision=revision+1,
                view_revision=view_revision+1,updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *''',
                (capped_checkpoint_dumps(checkpoint),capped_snapshot_dumps(snapshot),attempt.runner_id))
            result = decoded(cursor.fetchone())
            result = sync_public_display(cursor,result,advance_view=False)
            result = EventRepository.notify_in_tx(cursor,row,after=result)
            connection.commit()
            return result

    def reject_resume(self, runner_id, *, revision, control_id, error_code):
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor,runner_id)
            if (row['status'] not in ('paused','waiting','interrupted') or row['cancel_requested']
                    or row['revision'] != revision or row.get('resume_control_id') != control_id
                    or row['lease_valid']):
                return False
            lock_original_claim(cursor,row)
            cursor.execute('''UPDATE agent_runner_controls SET status='rejected',error_code=%s,
                consumed_at=clock_timestamp() WHERE runner_id=%s AND control_id=%s AND status='accepted' ''',
                (error_code,runner_id,control_id))
            if cursor.rowcount != 1:
                return False
            cursor.execute('''UPDATE agent_runners SET resume_control_id=NULL,control_revision=control_revision+1,
                view_revision=view_revision+1,updated_at=clock_timestamp() WHERE runner_id=%s''',(runner_id,))
            cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s',(runner_id,))
            sync_public_display(cursor,decoded(cursor.fetchone()),advance_view=False)
            EventRepository.notify_in_tx(cursor,row)
            connection.commit()
            return True
