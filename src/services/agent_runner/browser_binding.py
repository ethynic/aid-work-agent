"""Mandatory PostgreSQL linkage of the original Browser run and owned call.

The caller holds the root checkpoint lock and accepts the returned revision.
Browser lease renewal is independent of the parked Runner execution attempt.
This repository does not create a browser or infer completion from a client.
"""

import copy
import hashlib

from src.db.database import get_db_connection
from .event_repository import EventRepository
from .contracts import canonical_json
from .ownership import LeaseLost, lock_runner
from .repository import decoded
from .browser_endpoint import validate_browser_endpoint
from .wait_identity import owned_wait_id
from .persistence_limits import capped_checkpoint_dumps


def owned_browser_execution(row, checkpoint, execution_id, call_id):
    root = checkpoint.get('execution')
    if not isinstance(root, dict) or root.get('execution_id') != row['runner_id']:
        raise LeaseLost('BROWSER_ROOT_OWNER_MISMATCH')
    pending, seen, matches = [root], set(), []
    identity = {key: row[key] for key in ('tenant_id', 'user_id', 'session_id', 'source', 'session_kind')}
    while pending:
        node = pending.pop()
        owner = node.get('execution_id')
        if not isinstance(owner, str) or owner in seen or len(seen) >= 1000:
            raise LeaseLost('BROWSER_TREE_OWNER_MISMATCH')
        seen.add(owner)
        if node.get('identity') != identity:
            raise LeaseLost('BROWSER_EXECUTION_IDENTITY_MISMATCH')
        if owner == execution_id:
            matches.append(node)
        for key, child in (node.get('children') or {}).items():
            if not isinstance(child, dict):
                raise LeaseLost('BROWSER_CHILD_OWNER_MISMATCH')
            saved = child.get('checkpoint')
            call = ((node.get('tools') or {}).get(key) or {}).get('call') or {}
            if saved is None and child.get('unstarted') is True:
                task = child.get('task_record') or {}
                if (child.get('status') == 'unstarted' and task.get('execution_id') == child.get('execution_id')
                        and task.get('task_id') and task.get('status') in {'pending', 'running'}
                        and task.get('result') is None and not task.get('completed_at') and call.get('id') == key
                        and task.get('subagent_name') == (call.get('arguments') or {}).get('subagent_name')
                        and child.get('profile_id') and child.get('profile_fingerprint')):
                    continue
                raise LeaseLost('BROWSER_CHILD_OWNER_MISMATCH')
            if (not isinstance(saved, dict) or saved.get('execution_id') != child.get('execution_id')
                    or call.get('id') != key):
                raise LeaseLost('BROWSER_CHILD_OWNER_MISMATCH')
            pending.append(saved)
    if len(matches) != 1 or not row['tenant_id'] or not row['user_id']:
        raise LeaseLost('BROWSER_EXECUTION_OWNER_MISMATCH')
    node = matches[0]
    tool = (node.get('tools') or {}).get(call_id) or {}
    call = tool.get('call') or {}
    if call.get('id') != call_id or call.get('name') != 'browser_automation':
        raise LeaseLost('BROWSER_TOOL_OWNER_MISMATCH')
    return node, tool, hashlib.sha256(canonical_json(call.get('arguments') or {}).encode()).hexdigest()


def _same_binding(run, expected):
    if run is None or any(run.get(key) != value for key, value in expected.items()):
        raise LeaseLost('BROWSER_RUN_OWNER_MISMATCH')


def _live_browser_lease(cursor, run):
    cursor.execute('SELECT clock_timestamp() AS database_now')
    if (run['runtime_state'] != 'live' or run['owner_lease_until'] is None
            or run['owner_lease_until'] <= cursor.fetchone()['database_now']):
        raise LeaseLost('BROWSER_RUNTIME_OWNER_EXPIRED')


class BrowserOwnerRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    @staticmethod
    def _commit_checkpoint(cursor, row, checkpoint):
        cursor.execute('''UPDATE agent_runners SET checkpoint=%s::jsonb,revision=revision+1,
            updated_at=clock_timestamp() WHERE runner_id=%s RETURNING *''',
            (capped_checkpoint_dumps(checkpoint), row['runner_id']))
        from .application_public_projection import sync_public_display
        return sync_public_display(cursor, decoded(cursor.fetchone()))

    def bind_run(self, attempt, revision, *, execution_id, call_id, record,
                 worker_boot, browser_epoch, endpoint, lease_seconds):
        endpoint = validate_browser_endpoint(endpoint)
        if (not worker_boot or not browser_epoch or type(lease_seconds) is not int or lease_seconds <= 0):
            raise ValueError('BROWSER_OWNER_BINDING_INVALID')
        record = copy.deepcopy(record)
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
            if row['revision'] != revision:
                raise LeaseLost('CHECKPOINT_REVISION_CHANGED')
            checkpoint = copy.deepcopy(row['checkpoint'])
            node, tool, digest = owned_browser_execution(row, checkpoint, execution_id, call_id)
            if tool.get('phase') != 'dispatching':
                raise LeaseLost('BROWSER_TOOL_NOT_DISPATCHED')
            if (any(record.get(key) != row[key] for key in ('tenant_id', 'user_id', 'session_id'))
                    or not isinstance(record.get('run_id'), str) or not record['run_id']
                    or record.get('execution_target') != 'server' or record.get('state') != 'CREATED'):
                raise LeaseLost('BROWSER_RUN_IDENTITY_MISMATCH')
            binding = dict(runner_id=row['runner_id'], runner_execution_id=execution_id,
                runner_tool_call_id=call_id, owner_worker_id=attempt.worker_id,
                owner_boot_id=worker_boot, browser_epoch=browser_epoch, owner_endpoint=endpoint)
            fact = dict(version=1, run_id=record['run_id'], **binding, arguments_digest=digest, waits={})
            previous = (node.get('resources') or {}).get('browser_runs', {}).get(call_id)
            cursor.execute('''SELECT * FROM bs_browser_runs WHERE run_id=%s OR
                (runner_id=%s AND runner_execution_id=%s AND runner_tool_call_id=%s) FOR UPDATE''',
                (record['run_id'], row['runner_id'], execution_id, call_id))
            existing = cursor.fetchall()
            if previous is not None or existing:
                if previous != fact or len(existing) != 1:
                    raise LeaseLost('BROWSER_ORIGINAL_RUN_ALREADY_BOUND')
                _same_binding(existing[0], {**binding, 'run_id':record['run_id'],
                    'tenant_id':row['tenant_id'], 'user_id':row['user_id'], 'session_id':row['session_id']})
                if existing[0]['runtime_state'] != 'starting':
                    raise LeaseLost('BROWSER_ORIGINAL_RUN_ALREADY_STARTED')
                lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
                return row
            cursor.execute('''INSERT INTO bs_browser_runs (tenant_id,user_id,run_id,session_id,execution_target,state,
                runner_id,runner_execution_id,runner_tool_call_id,owner_worker_id,owner_boot_id,browser_epoch,
                owner_endpoint,owner_lease_until,runtime_state) VALUES
                (%s,%s,%s,%s,'server','CREATED',%s,%s,%s,%s,%s,%s,%s,
                 clock_timestamp()+(%s*INTERVAL '1 second'),'starting')''',
                (row['tenant_id'], row['user_id'], record['run_id'], row['session_id'], row['runner_id'],
                 execution_id, call_id, attempt.worker_id, worker_boot, browser_epoch, endpoint, lease_seconds))
            node.setdefault('resources', {}).setdefault('browser_runs', {})[call_id] = fact
            lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
            result = self._commit_checkpoint(cursor, row, checkpoint)
            result = EventRepository.notify_in_tx(cursor,row,after=result)
            connection.commit()
            return result

    def activate_run(self, attempt, *, execution_id, call_id, run_id, worker_boot,
                     browser_epoch, lease_seconds):
        """Called after actual executor.start and the original Redis owner fence.

        A starting row is not a live runtime proof. No existing live/closed/lost
        row is rebound to another process, epoch or original tool call.
        """
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError('BROWSER_OWNER_LEASE_INVALID')
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
            node, _, _ = owned_browser_execution(row, row['checkpoint'], execution_id, call_id)
            fact = (node.get('resources') or {}).get('browser_runs', {}).get(call_id) or {}
            expected = dict(runner_id=attempt.runner_id, runner_execution_id=execution_id,
                runner_tool_call_id=call_id, owner_worker_id=attempt.worker_id,
                owner_boot_id=worker_boot, browser_epoch=browser_epoch, run_id=run_id)
            if any(fact.get(key) != value for key, value in expected.items()):
                raise LeaseLost('BROWSER_RUN_CHECKPOINT_MISMATCH')
            cursor.execute('SELECT * FROM bs_browser_runs WHERE run_id=%s FOR UPDATE', (run_id,))
            run = cursor.fetchone()
            _same_binding(run, dict(expected, **{key: row[key]
                for key in ('tenant_id', 'user_id', 'session_id')}))
            cursor.execute('SELECT clock_timestamp() AS database_now')
            now = cursor.fetchone()['database_now']
            if run['runtime_state'] != 'starting' or run['owner_lease_until'] <= now:
                raise LeaseLost('BROWSER_START_OWNER_EXPIRED')
            lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
            cursor.execute('''UPDATE bs_browser_runs SET runtime_state='live',state='RUNNING_AGENT',
                owner_lease_until=clock_timestamp()+(%s*INTERVAL '1 second'),updated_at=CURRENT_TIMESTAMP
                WHERE run_id=%s RETURNING *''', (lease_seconds, run_id))
            result = dict(cursor.fetchone())
            from .application_public_projection import sync_public_display
            sync_public_display(cursor,row)
            EventRepository.notify_in_tx(cursor,row)
            connection.commit()
            return result

    def renew_owner(self, *, tenant_id, run_id, worker_id, worker_boot, browser_epoch, lease_seconds):
        """Original live Browser owner renewal; a closed or lost owner stays closed."""
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError('BROWSER_OWNER_LEASE_INVALID')
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute('SELECT * FROM bs_browser_runs WHERE tenant_id=%s AND run_id=%s FOR UPDATE',
                (tenant_id, run_id))
            run = cursor.fetchone()
            _same_binding(run, dict(owner_worker_id=worker_id, owner_boot_id=worker_boot,
                browser_epoch=browser_epoch, tenant_id=tenant_id, run_id=run_id))
            cursor.execute('SELECT clock_timestamp() AS database_now')
            if (run['runner_id'] is None or run['runtime_state'] != 'live'
                    or run['owner_lease_until'] <= cursor.fetchone()['database_now']):
                raise LeaseLost('BROWSER_RUNTIME_OWNER_EXPIRED')
            cursor.execute('''UPDATE bs_browser_runs SET owner_lease_until=clock_timestamp()+
                (%s*INTERVAL '1 second'),updated_at=CURRENT_TIMESTAMP WHERE run_id=%s RETURNING *''',
                (lease_seconds, run_id))
            result = dict(cursor.fetchone())
            connection.commit()
            return result

    def authorize_action(self, attempt, *, execution_id, call_id, run_id, worker_boot, browser_epoch):
        """Fence an original agent command immediately before its physical IO."""
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
            node, _, digest = owned_browser_execution(row, row['checkpoint'], execution_id, call_id)
            fact = (node.get('resources') or {}).get('browser_runs', {}).get(call_id) or {}
            expected = dict(runner_id=row['runner_id'],runner_execution_id=execution_id,
                runner_tool_call_id=call_id,owner_worker_id=attempt.worker_id,
                owner_boot_id=worker_boot,browser_epoch=browser_epoch,run_id=run_id)
            if fact.get('arguments_digest') != digest or any(fact.get(key)!=value for key,value in expected.items()):
                raise LeaseLost('BROWSER_RUN_CHECKPOINT_MISMATCH')
            cursor.execute('SELECT * FROM bs_browser_runs WHERE run_id=%s FOR UPDATE',(run_id,))
            run = cursor.fetchone()
            _same_binding(run,dict(expected,**{key:row[key] for key in ('tenant_id','user_id','session_id')}))
            _live_browser_lease(cursor,run)
            lock_runner(cursor,attempt.runner_id,attempt,dispatch=True)

    def record_runtime_state(self, *, binding, state, closed=False):
        """Original runtime observations use its independent Browser owner.

        Closure is supplied only after the live manager proves resource close;
        expiration grants no new IO, but does not forbid recording that close.
        """
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            row=lock_runner(cursor,binding['runner_id'])
            cursor.execute('SELECT * FROM bs_browser_runs WHERE run_id=%s FOR UPDATE',(binding['run_id'],))
            run=cursor.fetchone()
            _same_binding(run,binding)
            if closed:
                if run['runtime_state'] not in {'starting','live','closed'}:
                    raise LeaseLost('BROWSER_RUNTIME_OWNER_EXPIRED')
                cursor.execute('''UPDATE bs_browser_runs SET state=%s,runtime_state='closed',
                    owner_lease_until=NULL,closed_at=COALESCE(closed_at,clock_timestamp()),
                    updated_at=CURRENT_TIMESTAMP WHERE run_id=%s RETURNING *''',(state,binding['run_id']))
            else:
                cursor.execute('SELECT clock_timestamp() AS database_now')
                now=cursor.fetchone()['database_now']
                if run['runtime_state'] not in {'starting','live'} or not run['owner_lease_until'] or run['owner_lease_until']<=now:
                    raise LeaseLost('BROWSER_RUNTIME_OWNER_EXPIRED')
                cursor.execute('UPDATE bs_browser_runs SET state=%s,updated_at=CURRENT_TIMESTAMP WHERE run_id=%s RETURNING *',
                    (state,binding['run_id']))
            result=dict(cursor.fetchone())
            from .application_public_projection import sync_public_display
            sync_public_display(cursor,row)
            EventRepository.notify_in_tx(cursor,row)
            connection.commit()
            return result

    def bind_wait(self, attempt, revision, *, execution_id, call_id, assistance,
                  worker_boot, browser_epoch):
        """Store an observed wait from the current trusted process owner scope."""
        assistance = copy.deepcopy(assistance)
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt)
            if row['revision'] != revision:
                raise LeaseLost('CHECKPOINT_REVISION_CHANGED')
            checkpoint = copy.deepcopy(row['checkpoint'])
            node, tool, digest = owned_browser_execution(row, checkpoint, execution_id, call_id)
            if tool.get('phase') not in {'dispatching', 'waiting'}:
                raise LeaseLost('BROWSER_TOOL_NOT_DISPATCHED')
            fact = (node.get('resources') or {}).get('browser_runs', {}).get(call_id)
            if not isinstance(fact, dict) or fact.get('arguments_digest') != digest:
                raise LeaseLost('BROWSER_ORIGINAL_RUN_NOT_BOUND')
            if (not worker_boot or not browser_epoch or fact.get('owner_worker_id') != attempt.worker_id
                    or fact.get('owner_boot_id') != worker_boot or fact.get('browser_epoch') != browser_epoch):
                raise LeaseLost('BROWSER_RUN_OWNER_MISMATCH')
            expected_run = dict(runner_id=row['runner_id'], runner_execution_id=execution_id,
                runner_tool_call_id=call_id, owner_worker_id=attempt.worker_id,
                owner_boot_id=worker_boot, browser_epoch=browser_epoch)
            if any(fact.get(key) != value for key, value in expected_run.items()):
                raise LeaseLost('BROWSER_RUN_CHECKPOINT_MISMATCH')
            if (any(assistance.get(key) != row[key] for key in ('tenant_id', 'user_id', 'session_id'))
                    or assistance.get('agent_execution_id') != execution_id or assistance.get('tool_call_id') != call_id
                    or assistance.get('run_id') != fact['run_id'] or not assistance.get('assistance_id')):
                raise LeaseLost('BROWSER_ASSISTANCE_OWNER_MISMATCH')
            waiting = dict(kind='human_assistance', tool_call_id=call_id, assistance_id=assistance['assistance_id'])
            wait_id = owned_wait_id(row['runner_id'], execution_id, waiting)
            cursor.execute('SELECT * FROM bs_browser_runs WHERE run_id=%s FOR UPDATE', (fact['run_id'],))
            run = cursor.fetchone()
            _same_binding(run, dict(expected_run, run_id=fact['run_id'], owner_endpoint=fact['owner_endpoint'],
                **{key: row[key] for key in ('tenant_id', 'user_id', 'session_id')}))
            _live_browser_lease(cursor, run)
            cursor.execute('SELECT * FROM bs_browser_assistance_requests WHERE assistance_id=%s FOR UPDATE',
                (assistance['assistance_id'],))
            existing = cursor.fetchone()
            expected = dict(tenant_id=row['tenant_id'], user_id=row['user_id'], run_id=fact['run_id'],
                agent_execution_id=execution_id, tool_call_id=call_id, runner_id=row['runner_id'],
                runner_wait_id=wait_id, owner_boot_id=fact['owner_boot_id'], browser_epoch=fact['browser_epoch'])
            # The original random bac is persisted with the wait, independently of
            # any completion phase. Rows written before this column existed keep
            # their NULL continuation; only newly inserted waits carry the bac.
            continuation_id = assistance.get('continuation_id')
            if continuation_id is not None and (existing is None or existing.get('continuation_id') is not None):
                expected['continuation_id'] = continuation_id
            if existing:
                _same_binding(existing, expected)
                if fact.get('waits', {}).get(wait_id) != assistance['assistance_id']:
                    raise LeaseLost('BROWSER_WAIT_CHECKPOINT_MISMATCH')
                lock_runner(cursor, attempt.runner_id, attempt)
                _live_browser_lease(cursor, run)
                return row
            cursor.execute('''INSERT INTO bs_browser_assistance_requests
                (tenant_id,user_id,assistance_id,run_id,agent_execution_id,tool_call_id,state,reason_code,
                 instruction_code,completion_mode,predicate_type,expires_at,runner_id,runner_wait_id,owner_boot_id,
                 browser_epoch,continuation_id)
                VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (row['tenant_id'], row['user_id'], assistance['assistance_id'], fact['run_id'], execution_id, call_id,
                 assistance['reason_code'], assistance['instruction_code'], assistance['completion_mode'],
                 assistance.get('predicate_type'), assistance.get('expires_at'), row['runner_id'], wait_id,
                 fact['owner_boot_id'], fact['browser_epoch'], continuation_id))
            fact.setdefault('waits', {})[wait_id] = assistance['assistance_id']
            lock_runner(cursor, attempt.runner_id, attempt)
            _live_browser_lease(cursor, run)
            result = self._commit_checkpoint(cursor, row, checkpoint)
            result = EventRepository.notify_in_tx(cursor,row,after=result)
            connection.commit()
            return result
