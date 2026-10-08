"""Idempotent control acceptance under the original runner's ownership lock."""

import json
import uuid

from src.db.database import get_db_connection
from .contracts import RunnerError
from .ownership import lock_runner, LeaseLost
from .persistence_limits import capped_snapshot_dumps
from .repository import RunnerRepository, decoded
from .event_repository import EventRepository


def lock_owned_runner(cursor, principal, runner_id):
    try:
        row = lock_runner(cursor, runner_id)
    except LeaseLost as error:
        if str(error) == 'RUNNER_NOT_FOUND':
            raise RunnerError('RUNNER_NOT_FOUND', 404) from error
        raise
    RunnerRepository.assert_owner(principal, row)
    return row


def close_pending_controls(cursor, runner_id, reason):
    cursor.execute('''UPDATE agent_runner_controls SET status='rejected',error_code=%s,
        consumed_at=clock_timestamp() WHERE runner_id=%s AND status IN ('accepted','claimed')''',
        (reason,runner_id))


def execution_waits(checkpoint):
    """Walk only explicit execution/child facts, never arbitrary resource objects."""
    root = (checkpoint or {}).get('execution')
    pending = [root] if isinstance(root,dict) else []
    seen = set()
    while pending:
        state = pending.pop()
        execution_id = state.get('execution_id')
        if not isinstance(execution_id,str) or execution_id in seen:
            raise RunnerError('CHECKPOINT_TREE_INVALID',409)
        seen.add(execution_id)
        yield execution_id, state.get('waiting')
        children = state.get('children') or {}
        if not isinstance(children,dict) or len(seen) + len(pending) + len(children) > 1000:
            raise RunnerError('CHECKPOINT_TREE_INVALID',409)
        for child in children.values():
            if not isinstance(child,dict):
                raise RunnerError('CHECKPOINT_TREE_INVALID',409)
            value = child.get('checkpoint')
            if isinstance(value,dict):
                pending.append(value)


class ControlRepository:
    def __init__(self, connection_factory=get_db_connection, *, browser_completions=None):
        self.connection_factory = connection_factory
        self.browser_completions = browser_completions

    @staticmethod
    def assert_wait(row, request):
        for execution_id, waiting in execution_waits(row.get('checkpoint')):
            if execution_id == request.target_execution_id and isinstance(waiting,dict) and waiting.get('wait_id') == request.wait_id:
                expected = 'clarification' if request.action == 'reply' else 'human_assistance'
                if waiting.get('kind') != expected:
                    raise RunnerError('CONTROL_WAIT_KIND_MISMATCH',409)
                return
        raise RunnerError('CONTROL_WAIT_STALE',409)

    def accept_completion_in_tx(self, cursor, row, request):
        """Internal Browser fact bridge under an already locked original owner.

        This accepts only browser_complete and never relaxes the parked guard.
        The caller commits its completion watermark in this same transaction.
        """
        if request.action != 'browser_complete' or self.browser_completions is None:
            raise RunnerError('CONTROL_COMPLETION_FORBIDDEN',403)
        if (row['cancel_requested'] or row['status'] not in {'paused','waiting','interrupted'}
                or row.get('lease_valid')):
            raise RunnerError('RUNNER_NOT_PARKED',409)
        cursor.execute('''SELECT * FROM agent_runner_controls WHERE runner_id=%s
            AND client_request_id=%s''',(row['runner_id'],request.client_request_id))
        previous = decoded(cursor.fetchone())
        if previous:
            if previous['intent_digest'] != request.digest():
                raise RunnerError('IDEMPOTENCY_INPUT_MISMATCH',409)
            return previous
        if row.get('resume_control_id'):
            raise RunnerError('RUNNER_RESUME_PENDING',409)
        self.assert_wait(row,request)
        cursor.execute('''SELECT 1 FROM agent_runner_controls WHERE runner_id=%s
            AND status='consumed' AND action='browser_complete'
            AND payload->>'wait_id'=%s AND payload->>'target_execution_id'=%s''',
            (row['runner_id'],request.wait_id,request.target_execution_id))
        if cursor.fetchone():
            raise RunnerError('CONTROL_WAIT_CONSUMED',409)
        self.browser_completions.assert_completion_in_tx(cursor,row,request)
        control_id = 'control_' + uuid.uuid4().hex
        cursor.execute('''INSERT INTO agent_runner_controls
            (control_id,runner_id,tenant_id,scope_key,action,client_request_id,intent_digest,payload,status)
            VALUES (%s,%s,%s,%s,'browser_complete',%s,%s,%s::jsonb,'accepted') RETURNING *''',
            (control_id,row['runner_id'],row['tenant_id'],row['scope_key'],request.client_request_id,
             request.digest(),json.dumps(request.intent(),ensure_ascii=False)))
        control = decoded(cursor.fetchone())
        cursor.execute('''UPDATE agent_runners SET resume_control_id=%s,
            control_revision=control_revision+1,view_revision=view_revision+1,
            updated_at=clock_timestamp() WHERE runner_id=%s''',(control_id,row['runner_id']))
        from .application_public_projection import sync_public_display
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s',(row['runner_id'],))
        sync_public_display(cursor,decoded(cursor.fetchone()),advance_view=False)
        return control

    def submit(self, principal, runner_id, request, *, expected_revision=None):
        with self.connection_factory() as connection:
            result=self.submit_in_tx(connection.cursor(),principal,runner_id,request,
                                     expected_revision=expected_revision)
            connection.commit()
            return result

    def submit_in_tx(self,cursor,principal,runner_id,request,*,expected_revision=None):
        row = lock_owned_runner(cursor, principal, runner_id)
        cursor.execute('SELECT * FROM agent_runner_controls WHERE runner_id=%s AND client_request_id=%s',
                       (runner_id,request.client_request_id))
        previous = decoded(cursor.fetchone())
        if previous:
            if previous['intent_digest'] != request.digest():
                raise RunnerError('IDEMPOTENCY_INPUT_MISMATCH',409)
            return previous,False
        # 控制输入字节闸：已受理控制的幂等重放不受限，仅新控制 413 拒绝。
        from .persistence_limits import assert_intent_within_request_limit
        assert_intent_within_request_limit(request.intent(), code='CONTROL_TOO_LARGE')
        if expected_revision is not None and expected_revision != row['revision']:
            raise RunnerError('CHECKPOINT_REVISION_CHANGED',409)
        if row['cancel_requested'] or row['status'] in ('finalizing','completed','cancelled','failed'):
            raise RunnerError('CONTROL_EXECUTION_CLOSED',409)
        if request.action != 'pause':
            if row['status'] not in ('paused','waiting','interrupted'):
                raise RunnerError('RUNNER_NOT_PARKED',409)
            if row.get('resume_control_id'):
                raise RunnerError('RUNNER_RESUME_PENDING',409)
            if request.action in ('reply','browser_complete'):
                self.assert_wait(row,request)
                cursor.execute('''SELECT 1 FROM agent_runner_controls WHERE runner_id=%s
                    AND status='consumed' AND action IN ('reply','browser_complete')
                    AND payload->>'wait_id'=%s AND payload->>'target_execution_id'=%s''',
                    (runner_id, request.wait_id, request.target_execution_id))
                if cursor.fetchone():
                    raise RunnerError('CONTROL_WAIT_CONSUMED',409)
            if request.action == 'browser_complete':
                if self.browser_completions is None:
                    raise RunnerError('CONTROL_COMPLETION_FORBIDDEN',403)
                self.browser_completions.assert_completion_in_tx(cursor,row,request)
        control_id = 'control_' + uuid.uuid4().hex
        acknowledged_pause = request.action == 'pause' and row['status'] in ('paused','waiting','interrupted')
        status = 'consumed' if acknowledged_pause else 'accepted'
        cursor.execute('''INSERT INTO agent_runner_controls
            (control_id,runner_id,tenant_id,scope_key,action,client_request_id,intent_digest,payload,
             status,consumed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,
             CASE WHEN %s THEN clock_timestamp() ELSE NULL END) RETURNING *''',
            (control_id,runner_id,row['tenant_id'],row['scope_key'],request.action,request.client_request_id,
             request.digest(),json.dumps(request.intent(),ensure_ascii=False),status,acknowledged_pause))
        control = dict(cursor.fetchone())
        if request.action == 'pause':
            # A later explicit pause supersedes a not-yet-consumed resume. It
            # cannot overwrite finalization, which was rejected above.
            if row.get('resume_control_id'):
                cursor.execute('''UPDATE agent_runner_controls SET status='rejected',
                    error_code='RESUME_SUPERSEDED_BY_PAUSE',consumed_at=clock_timestamp()
                    WHERE control_id=%s AND status='accepted' ''',(row['resume_control_id'],))
            cursor.execute('''UPDATE agent_runners SET pause_requested=TRUE,resume_control_id=NULL,
                control_revision=control_revision+1,view_revision=view_revision+1,
                updated_at=clock_timestamp() WHERE runner_id=%s''',(runner_id,))
        else:
            snapshot = dict(row.get('public_snapshot') or {})
            if request.action == 'reply' or (request.action=='resume' and (request.answer.strip() or request.attachments)):
                snapshot.setdefault('supplementalInputs',[]).append({
                    'control_id':control_id,'message_id':control_id+':user','text':request.answer,
                    'client_request_id':request.client_request_id,
                    'wait_id':request.wait_id if request.action=='reply' else None,
                    'target_execution_id':request.target_execution_id if request.action=='reply' else runner_id,
                    'accepted_at':control['accepted_at'].isoformat(),
                    'attachments':[{key:item.model_dump(mode='json')[key]
                                    for key in ('file_id','name','type','mime_type','size')}
                                   for item in request.attachments]})
            cursor.execute('''UPDATE agent_runners SET resume_control_id=%s,
                public_snapshot=%s::jsonb,
                control_revision=control_revision+1,view_revision=view_revision+1,
                updated_at=clock_timestamp() WHERE runner_id=%s''',
                (control_id,capped_snapshot_dumps(snapshot),runner_id))
        from .application_public_projection import sync_public_display
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s',(runner_id,))
        sync_public_display(cursor,decoded(cursor.fetchone()),advance_view=False)
        EventRepository.notify_in_tx(cursor,row)
        return control,True


    def get(self, principal, runner_id, control_id):
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_owned_runner(cursor, principal, runner_id)
            cursor.execute('SELECT * FROM agent_runner_controls WHERE runner_id=%s AND control_id=%s', (runner_id,control_id))
            control = cursor.fetchone()
            if not control:
                raise RunnerError('CONTROL_NOT_FOUND',404)
            return dict(control)

    def find_request(self, principal, runner_id, request):
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            lock_owned_runner(cursor,principal,runner_id)
            cursor.execute('SELECT * FROM agent_runner_controls WHERE runner_id=%s AND client_request_id=%s',
                           (runner_id,request.client_request_id))
            previous = cursor.fetchone()
            if previous and previous['intent_digest'] != request.digest():
                raise RunnerError('IDEMPOTENCY_INPUT_MISMATCH',409)
            return dict(previous) if previous else None
