"""Bounded database-clock wakeups; no user controls or second execution loop."""

import copy

from src.db.database import get_db_connection
from .contracts import RunnerError
from .durable_sleep import replayable_activation, validate_armed
from .ownership import Attempt, lock_runner
from .persistence_limits import capped_checkpoint_dumps, capped_snapshot_dumps
from .repository import decoded
from .recovery_repository import lock_original_claim
from .application_public_projection import project_public_snapshot, sync_public_display
from .event_repository import EventRepository


class TimerRepository:
    def __init__(self,connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    def next_candidates(self,*,after_queue_order=0,limit=16):
        if type(limit) is not int or not 1<=limit<=32:
            raise ValueError('TIMER_CANDIDATE_LIMIT_INVALID')
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            cursor.execute('''SELECT r.* FROM agent_runners r WHERE NOT r.cancel_requested
                AND NOT r.pause_requested AND r.resume_control_id IS NULL AND NOT (r.checkpoint ? 'sleep_blocked')
                AND (r.lease_until IS NULL OR r.lease_until<=clock_timestamp())
                AND ((r.status='waiting' AND r.wake_at<=clock_timestamp())
                     OR (r.status='interrupted' AND r.checkpoint->'timer_activation'->>'action_started'='false'
                         AND r.checkpoint->'timer_activation'->>'version'='1'))
                AND (r.status<>'interrupted' OR NOT EXISTS(
                    SELECT 1 FROM agent_runner_usage_receipts u
                    WHERE u.runner_id=r.runner_id AND u.authorized_attempt=r.attempt))
                AND EXISTS (SELECT 1 FROM agent_runner_session_claims c
                    WHERE c.scope_key=r.scope_key AND c.session_kind=r.session_kind
                    AND c.session_id=r.session_id AND c.owner_runner_id=r.runner_id)
                AND r.queue_order>%s ORDER BY r.queue_order LIMIT %s''',(after_queue_order,limit))
            return [decoded(row) for row in cursor.fetchall()]

    def claim(self,expected,worker_id,lease_seconds,checkpoint,*,source_port=None,prepared_source=None):
        if lease_seconds<=0:
            raise ValueError('INVALID_LEASE')
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            row=lock_runner(cursor,expected['runner_id'])
            if (row['status'] not in {'waiting','interrupted'} or row['cancel_requested']
                    or row.get('pause_requested') or row.get('resume_control_id') or row['lease_valid']
                    or row['revision']!=expected['revision']
                    or row['control_revision']!=expected['control_revision']):
                return None
            lock_original_claim(cursor,row)
            if row['status']=='waiting':
                timer,_=validate_armed(row)
                expected_timer,_=validate_armed(expected)
                if timer!=expected_timer or row.get('wake_at') is None:
                    return None
                cursor.execute('SELECT clock_timestamp() AS database_now')
                now=cursor.fetchone()['database_now']
                if row['wake_at']>now:
                    return None
                consumed=checkpoint.get('durable_sleep') or {}
                if (consumed.get('phase')!='consumed' or consumed.get('trigger')!='timer'
                        or consumed.get('wait_id')!=timer['wait_id']):
                    raise RunnerError('SLEEP_TIMER_MISMATCH',409)
            else:
                if not replayable_activation(row):
                    return None
                cursor.execute('''SELECT 1 FROM agent_runner_usage_receipts
                    WHERE runner_id=%s AND authorized_attempt=%s LIMIT 1''',(row['runner_id'],row['attempt']))
                if cursor.fetchone():
                    return None
                timer=row['checkpoint']['timer_activation']
                if timer!=expected['checkpoint'].get('timer_activation'):
                    return None
                cursor.execute('SELECT clock_timestamp() AS database_now')
                now=cursor.fetchone()['database_now']
            if source_port is not None:
                source_port.authorize_row_in_tx(cursor,row,execute=True,prepared=prepared_source)
            checkpoint=copy.deepcopy(checkpoint)
            checkpoint.pop('applied_control_id',None)
            checkpoint['timer_activation']={'version':1,'wait_id':timer['wait_id'],
                'attempt':row['attempt']+1,'action_started':False}
            consumed=checkpoint.get('durable_sleep') or {}
            if consumed.get('phase')=='consumed':
                consumed.setdefault('woke_at',now.isoformat())
                for state in checkpoint_nodes(checkpoint):
                    fact=state.get('tools',{}).get(consumed.get('tool_call_id'))
                    if state.get('execution_id')==consumed.get('execution_id') and fact:
                        fact['result']['woke_at']=consumed['woke_at']
            snapshot=project_public_snapshot(cursor,row,checkpoint=checkpoint,
                snapshot={**row.get('public_snapshot',{}),'waiting':None},status='running')
            cursor.execute('''UPDATE agent_runners SET status='running',attempt=attempt+1,worker_id=%s,
                lease_until=clock_timestamp()+(%s*INTERVAL '1 second'),checkpoint=%s::jsonb,
                public_snapshot=%s::jsonb,wake_at=NULL,revision=revision+1,view_revision=view_revision+1,
                updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *''',
                (worker_id,lease_seconds,capped_checkpoint_dumps(checkpoint),capped_snapshot_dumps(snapshot),row['runner_id']))
            result=decoded(cursor.fetchone())
            result=sync_public_display(cursor,result,advance_view=False)
            result=EventRepository.notify_in_tx(cursor,row,after=result)
            if source_port is not None:
                source_port.authorize_row_in_tx(cursor,result,execute=True,prepared=prepared_source)
            lock_runner(cursor,row['runner_id'],Attempt(row['runner_id'],worker_id,result['attempt']),dispatch=True)
            connection.commit()
            return result

    def hold(self,expected,error_code):
        """Failed preflight stops scanning, preserving the deadline and claim."""
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            row=lock_runner(cursor,expected['runner_id'])
            if (row['revision']!=expected['revision'] or row['control_revision']!=expected['control_revision']
                    or row['cancel_requested'] or row.get('resume_control_id') or row['lease_valid']):
                return False
            checkpoint=copy.deepcopy(row.get('checkpoint') or {})
            checkpoint['sleep_blocked']={'error_code':error_code}
            # Keep the original timer wait actionable for explicit resume.
            snapshot={**row.get('public_snapshot',{}),'progress':'自动继续暂不可用，原任务已保留，请核对后继续。'}
            cursor.execute('''UPDATE agent_runners SET wake_at=NULL,checkpoint=%s::jsonb,
                public_snapshot=%s::jsonb,revision=revision+1,view_revision=view_revision+1,
                updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *''',
                (capped_checkpoint_dumps(checkpoint),capped_snapshot_dumps(snapshot),row['runner_id']))
            result=sync_public_display(cursor,decoded(cursor.fetchone()),advance_view=False)
            EventRepository.notify_in_tx(cursor,row,after=result)
            connection.commit()
            return True


def checkpoint_nodes(checkpoint):
    from .durable_sleep import nodes
    return nodes(checkpoint['execution'])
