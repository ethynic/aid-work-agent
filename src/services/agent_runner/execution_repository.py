"""Logical claims and renewable execution attempts are separate durable owners."""

from src.db.database import get_db_connection
from .ownership import Attempt, LeaseLost, lock_runner
from .persistence_limits import capped_checkpoint_dumps, capped_snapshot_dumps
from .repository import decoded
from .control_projection import merge_control_projection
from .event_repository import EventRepository
from .application_public_projection import sync_public_display, project_public_snapshot


def release_proof(attempt, status, reason):
    return {'attempt':attempt.number,'worker_id':attempt.worker_id,'status':status,'reason':reason}


def released_by(row, attempt):
    """A committed release is distinct from loss of execution authority."""
    checkpoint = row.get('checkpoint') or {}
    proof = checkpoint.get('released_attempt')
    if (row['attempt'] != attempt.number or row.get('worker_id') is not None
            or row.get('lease_until') is not None or not isinstance(proof,dict)
            or proof.get('attempt') != attempt.number or proof.get('worker_id') != attempt.worker_id
            or proof.get('status') != row['status']):
        return False
    status, reason = row['status'],proof.get('reason')
    if status == 'waiting' and reason == 'cancel_verification':
        blocked = checkpoint.get('cancel_completion_blocked')
        return (row.get('cancel_requested') is True and not checkpoint.get('pending_finalization')
            and checkpoint.get('finalization_intent') == 'cancel_only'
            and checkpoint.get('cancel_completion_requested') is True and isinstance(blocked,dict)
            and blocked.get('attempt') == attempt.number and blocked.get('worker_id') == attempt.worker_id
            and blocked.get('revision') == row['revision']
            and blocked.get('error_code') in {'LOCAL_CANCEL_ACK_PENDING',
                'LOCAL_CANCEL_EFFECT_VERIFICATION_REQUIRED','LOCAL_CANCEL_DEADLINE_VERIFICATION_REQUIRED'})
    if status in {'completed','failed','cancelled'} and reason == 'finalized':
        pending = checkpoint.get('pending_finalization')
        return (isinstance(pending,dict) and pending.get('status') == status
            and isinstance(row.get('result'),dict)
            and row['result'] == {key:pending.get(key) for key in ('status','output','images','error_code')})
    if status == 'paused' and reason == 'unstarted_pause':
        return (checkpoint.get('unstarted') is True and row.get('pause_requested') is True
            and not any(checkpoint.get(key) for key in ('execution','children','tools','pending_finalization')))
    if status in {'paused','waiting'} and reason == 'parked':
        execution = checkpoint.get('execution')
        if not isinstance(execution,dict) or execution.get('outcome') != status:
            return False
        from src.core.agent_engine.contracts import ExecutionState
        from .pause_protocol import safe_paused_tree
        try:
            return safe_paused_tree(ExecutionState.restore(execution))
        except (ValueError,TypeError,KeyError):
            return False
    return False


class ExecutionRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory
        self.source_port = None
        self.input_repository = None

    def _source_dispatch(self,cursor,row,prepared):
        if self.source_port is not None:
            if (row.get('checkpoint') or {}).get('source_initial_ref'):
                self.input_repository.lock_claim(cursor,row)
            self.source_port.authorize_row_in_tx(cursor,row,execute=True,prepared=prepared)

    def _final_dispatch(self,cursor,attempt,prepared):
        # Source/config/input locks may have waited after the entry fence. They
        # are now owned; recheck source freshness and the actual Attempt clock
        # at the final cursor boundary, never mint a replacement permission.
        row=lock_runner(cursor,attempt.runner_id,attempt,dispatch=True)
        self._source_dispatch(cursor,row,prepared)
        return lock_runner(cursor,attempt.runner_id,attempt,dispatch=True)

    def acquire(self, worker_id, lease_seconds):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute("""SELECT r.* FROM agent_runners r WHERE
                (r.status='queued'
                 AND NOT EXISTS (SELECT 1 FROM agent_runner_session_claims c WHERE
                    c.scope_key=r.scope_key AND c.session_kind=r.session_kind AND c.session_id=r.session_id)
                 AND NOT EXISTS (SELECT 1 FROM agent_runners previous WHERE
                    previous.scope_key=r.scope_key AND previous.session_kind=r.session_kind
                    AND previous.session_id=r.session_id AND previous.status='queued'
                    AND previous.queue_order<r.queue_order))
                OR (r.status='finalizing' AND (r.lease_until IS NULL OR r.lease_until<=clock_timestamp())
                    AND EXISTS (SELECT 1 FROM agent_runner_session_claims c WHERE c.owner_runner_id=r.runner_id))
                OR (r.status IN ('waiting','paused','interrupted') AND r.cancel_requested
                    AND (NOT (r.checkpoint ? 'cancel_completion_blocked')
                         OR r.checkpoint->'cancel_completion_blocked'->>'ready'='true')
                    AND (r.lease_until IS NULL OR r.lease_until<=clock_timestamp())
                    AND EXISTS (SELECT 1 FROM agent_runner_session_claims c WHERE c.owner_runner_id=r.runner_id))
                ORDER BY r.queue_order FOR UPDATE OF r SKIP LOCKED LIMIT 1""")
            row = decoded(cursor.fetchone())
            if row is None:
                return None
            if row["status"] == "queued":
                cursor.execute("""INSERT INTO agent_runner_session_claims
                    (scope_key,tenant_id,session_kind,session_id,owner_runner_id)
                    VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING owner_runner_id""",
                    (row["scope_key"], row["tenant_id"], row["session_kind"], row["session_id"], row["runner_id"]))
                if not cursor.fetchone():
                    return None
            previous = row
            cancel_only = row["status"] in ("waiting", "paused", "interrupted")
            checkpoint = row.get("checkpoint") or {}
            if cancel_only:
                checkpoint = {**checkpoint, "finalization_intent": "cancel_only"}
                checkpoint.pop('cancel_completion_blocked',None)
            cursor.execute("""UPDATE agent_runners SET status=CASE WHEN status='queued' THEN 'running'
                WHEN status IN ('waiting','paused','interrupted') THEN 'finalizing' ELSE status END,
                checkpoint=%s::jsonb,
                attempt=attempt+1, worker_id=%s, lease_until=clock_timestamp()+(%s*INTERVAL '1 second'),
                revision=revision+1,view_revision=view_revision+1,updated_at=CURRENT_TIMESTAMP
                WHERE runner_id=%s RETURNING *""", (capped_checkpoint_dumps(checkpoint), worker_id, lease_seconds, row["runner_id"]))
            row = decoded(cursor.fetchone())
            row = sync_public_display(cursor,row,advance_view=False)
            row = EventRepository.notify_in_tx(cursor,previous,after=row)
            row["cancel_only"] = cancel_only
            conn.commit()
            return row

    def heartbeat(self, attempt, lease_seconds):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            row = lock_runner(cursor, attempt.runner_id)
            if released_by(row,attempt):
                return {'closed':True,'cancel_requested':row['cancel_requested'],
                        'pause_requested':row.get('pause_requested',False),'control_revision':row['control_revision']}
            if (row['attempt'] != attempt.number or row['worker_id'] != attempt.worker_id
                    or not row['lease_valid'] or row['status'] not in ('running','finalizing')):
                raise LeaseLost('RUNNER_ATTEMPT_EXPIRED')
            cursor.execute("""UPDATE agent_runners SET lease_until=clock_timestamp()+(%s*INTERVAL '1 second')
                WHERE runner_id=%s""", (lease_seconds, attempt.runner_id))
            conn.commit()
            return {"closed":False,"cancel_requested": row["cancel_requested"], "pause_requested": row.get('pause_requested',False),
                    "control_revision": row["control_revision"]}

    def assert_dispatch(self, attempt, *, source_prepared=None):
        with self.connection_factory() as conn:
            cursor=conn.cursor()
            row=lock_runner(cursor,attempt.runner_id,attempt,dispatch=True)
            self._source_dispatch(cursor,row,source_prepared)
            return self._final_dispatch(cursor,attempt,source_prepared)

    def save_checkpoint(self, attempt, revision, checkpoint, snapshot=None, *, dispatch=False,
                        source_prepared=None,input_boundary=None):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt, dispatch=dispatch)
            if row['status'] != 'running':
                raise LeaseLost('RUNNER_EXECUTION_CLOSED')
            if row["revision"] != revision:
                raise LeaseLost("CHECKPOINT_REVISION_CHANGED")
            physical_dispatch=(dispatch or ((row.get('checkpoint') or {}).get('source_initial_ref')
                and input_boundary in {'child.before_model','child.before_tool'}))
            if physical_dispatch:
                self._source_dispatch(cursor,row,source_prepared)
            if self.input_repository is not None:
                checkpoint=self.input_repository.merge_in_tx(cursor,row,checkpoint,before_model=input_boundary=='before_model')
            snapshot = merge_control_projection(snapshot,row.get('public_snapshot'))
            snapshot = project_public_snapshot(cursor,row,checkpoint=checkpoint,snapshot=snapshot)
            cursor.execute("""UPDATE agent_runners SET checkpoint=%s::jsonb,
                public_snapshot=COALESCE(%s::jsonb,public_snapshot),revision=revision+1,
                view_revision=view_revision+1,updated_at=CURRENT_TIMESTAMP WHERE runner_id=%s RETURNING *""",
                (capped_checkpoint_dumps(checkpoint),
                 capped_snapshot_dumps(snapshot) if snapshot is not None else None, attempt.runner_id))
            result = decoded(cursor.fetchone())
            result = sync_public_display(cursor,result,advance_view=False)
            result = EventRepository.notify_in_tx(cursor,row,after=result)
            if physical_dispatch:
                result=self._final_dispatch(cursor,attempt,source_prepared)
            elif (row.get('checkpoint') or {}).get('source_initial_ref'):
                result=lock_runner(cursor,attempt.runner_id,attempt)
            conn.commit()
            return result

    def block_cancel(self, attempt, error_code):
        """Only the cancellation owner can hold an unsettled external action."""
        if error_code not in {'LOCAL_CANCEL_ACK_PENDING','LOCAL_CANCEL_EFFECT_VERIFICATION_REQUIRED',
                              'LOCAL_CANCEL_DEADLINE_VERIFICATION_REQUIRED'}:
            raise ValueError('INVALID_CANCEL_VERIFICATION_CODE')
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            row = lock_runner(cursor,attempt.runner_id,attempt)
            checkpoint = row.get('checkpoint') or {}
            if (not row['cancel_requested'] or checkpoint.get('pending_finalization')
                    or (row['status']=='finalizing' and checkpoint.get('finalization_intent')!='cancel_only')):
                raise LeaseLost('FINALIZATION_INTENT_IMMUTABLE')
            checkpoint = {**checkpoint,'finalization_intent':'cancel_only','cancel_completion_requested':True,
                'cancel_completion_blocked':{'attempt':attempt.number,'worker_id':attempt.worker_id,
                    'revision':row['revision']+1,'error_code':error_code,'ready':False},
                'released_attempt':release_proof(attempt,'waiting','cancel_verification')}
            message = '取消已请求，原操作结果尚待核对，任务和文件已保留。'
            snapshot = {**(row.get('public_snapshot') or {}),'progress':message,
                'waiting':{'kind':'verification','question':message}}
            snapshot['progressMessages'] = [*(snapshot.get('progressMessages') or []),
                {'type':'progress','data':message}]
            cursor.execute("""UPDATE agent_runners SET status='waiting',checkpoint=%s::jsonb,
                public_snapshot=%s::jsonb,worker_id=NULL,lease_until=NULL,revision=revision+1,
                view_revision=view_revision+1,updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *""",
                (capped_checkpoint_dumps(checkpoint),capped_snapshot_dumps(snapshot),attempt.runner_id))
            result = decoded(cursor.fetchone())
            result = sync_public_display(cursor,result,advance_view=False)
            result = EventRepository.notify_in_tx(cursor,row,after=result)
            conn.commit()
            return result

    def park(self, attempt, revision, status, checkpoint, snapshot):
        if status not in ("waiting", "paused", "interrupted"):
            raise ValueError("INVALID_PARK_STATUS")
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt)
            if row['status'] != 'running':
                raise LeaseLost('RUNNER_EXECUTION_CLOSED')
            if row["revision"] != revision:
                raise LeaseLost("CHECKPOINT_REVISION_CHANGED")
            checkpoint = {**checkpoint,'released_attempt':release_proof(attempt,status,'parked')}
            snapshot = merge_control_projection(snapshot,row.get('public_snapshot'))
            snapshot = project_public_snapshot(cursor,row,checkpoint=checkpoint,snapshot=snapshot,status=status)
            cursor.execute("""UPDATE agent_runners SET status=%s,checkpoint=%s::jsonb,public_snapshot=%s::jsonb,
                worker_id=NULL,lease_until=NULL,revision=revision+1,view_revision=view_revision+1,
                updated_at=CURRENT_TIMESTAMP WHERE runner_id=%s RETURNING *""", (status,
                capped_checkpoint_dumps(checkpoint), capped_snapshot_dumps(snapshot), attempt.runner_id))
            result = decoded(cursor.fetchone())
            result = sync_public_display(cursor,result,advance_view=False)
            result = EventRepository.notify_in_tx(cursor,row,after=result)
            conn.commit()
            return result

    def stage_finalization(self, attempt, revision, checkpoint, result, snapshot):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt)
            previous = (row.get('checkpoint') or {}).get('pending_finalization')
            if previous is not None:
                if previous != result:
                    raise LeaseLost('FINALIZATION_INTENT_IMMUTABLE')
                return row
            if row['revision']!=revision: raise LeaseLost('CHECKPOINT_REVISION_CHANGED')
            if self.input_repository is not None and not (row.get('checkpoint') or {}).get('pending_finalization'):
                from .repository import RunnerRepository
                checkpoint,continuation=self.input_repository.finish_in_tx(cursor,row,checkpoint,result,RunnerRepository(self.connection_factory))
                if continuation:
                    cursor.execute('''UPDATE agent_runners SET checkpoint=%s::jsonb,revision=revision+1
                        WHERE runner_id=%s RETURNING *''',(capped_checkpoint_dumps(checkpoint),row['runner_id']))
                    resumed=decoded(cursor.fetchone())
                    lock_runner(cursor,attempt.runner_id,attempt)
                    conn.commit();return resumed
            checkpoint = {**checkpoint, "pending_finalization": result}
            snapshot = merge_control_projection(snapshot,row.get('public_snapshot'))
            snapshot = project_public_snapshot(cursor,row,checkpoint=checkpoint,snapshot=snapshot,status='finalizing')
            previous = (row.get('checkpoint') or {}).get('pending_finalization')
            if previous is not None:
                if previous != result:
                    raise LeaseLost('FINALIZATION_INTENT_IMMUTABLE')
                return row
            if row['status'] == 'finalizing' and (
                    (row.get('checkpoint') or {}).get('finalization_intent') != 'cancel_only'
                    or result.get('status') != 'cancelled'):
                raise LeaseLost('FINALIZATION_INTENT_IMMUTABLE')
            if row["revision"] != revision:
                raise LeaseLost("CHECKPOINT_REVISION_CHANGED")
            cursor.execute("""UPDATE agent_runners SET status='finalizing', checkpoint=%s::jsonb,
                public_snapshot=%s::jsonb,revision=revision+1,view_revision=view_revision+1,
                updated_at=CURRENT_TIMESTAMP WHERE runner_id=%s RETURNING *""",
                (capped_checkpoint_dumps(checkpoint),
                 capped_snapshot_dumps(snapshot), attempt.runner_id))
            result = decoded(cursor.fetchone())
            result = sync_public_display(cursor,result,advance_view=False)
            result = EventRepository.notify_in_tx(cursor,row,after=result)
            if (row.get('checkpoint') or {}).get('source_initial_ref'):
                result=lock_runner(cursor,attempt.runner_id,attempt)
            conn.commit()
            return result

    def reap_expired(self):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            # A crashed running owner cannot be replayed from the beginning. Its
            # logical claim stays until an explicit safe recovery/control action.
            cursor.execute("""SELECT * FROM agent_runners WHERE status='running'
                AND lease_until<=clock_timestamp() ORDER BY queue_order FOR UPDATE""")
            previous = {row["runner_id"]:decoded(row) for row in cursor.fetchall()}
            cursor.execute("""UPDATE agent_runners SET status='interrupted',worker_id=NULL,lease_until=NULL,
                revision=revision+1,view_revision=view_revision+1,updated_at=CURRENT_TIMESTAMP
                WHERE runner_id=ANY(%s) RETURNING *""", (list(previous),))
            interrupted_rows = [decoded(row) for row in cursor.fetchall()]
            interrupted = []
            for row in interrupted_rows:
                row = sync_public_display(cursor,row,advance_view=False)
                row = EventRepository.notify_in_tx(cursor,previous[row['runner_id']],after=row)
                interrupted.append(row['runner_id'])
            # Finalizing is safe to retry without reentering the Engine; the old
            # attempt is still fenced by lease_until until a new acquire increments it.
            conn.commit()
            return interrupted

    def interrupt_attempt(self, attempt, *, public_verification=None):
        """Preserve the latest committed checkpoint, never a failed in-memory one."""
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt)
            previous = row
            if row['status'] == 'finalizing':
                # A staged terminal intent retries only the finalizer.
                return row
            snapshot=row.get('public_snapshot') or {}
            if public_verification is not None:
                snapshot={**snapshot,'waiting':{'kind':'verification','question':public_verification}}
            cursor.execute("""UPDATE agent_runners SET status='interrupted',worker_id=NULL,lease_until=NULL,
                public_snapshot=%s::jsonb,revision=revision+1,view_revision=view_revision+1,updated_at=clock_timestamp()
                WHERE runner_id=%s RETURNING *""", (capped_snapshot_dumps(snapshot),attempt.runner_id))
            row = decoded(cursor.fetchone())
            row = sync_public_display(cursor,row,advance_view=False)
            row = EventRepository.notify_in_tx(cursor,previous,after=row)
            conn.commit()
            return row
