"""Facts and controls for continuing one original worker-owned Browser call.

No browser IO occurs in a transaction. Completion is an observation under the
Browser lease; starting a continuation additionally requires the new Runner
attempt. A started continuation without a durable result is never replayed.
"""

import copy
import json
import uuid

from .browser_binding import owned_browser_execution, _same_binding, _live_browser_lease
from .contracts import RunnerError
from .control_contracts import RunnerControl
from .control_repository import ControlRepository
from .ownership import lock_runner, LeaseLost
from .recovery_repository import lock_original_claim
from src.db.database import get_db_connection
from .event_repository import EventRepository
from .persistence_limits import capped_snapshot_dumps


class BrowserRecoveryUnavailable(RunnerError):
    preserve_control = True

    def __init__(self, code='BROWSER_CONTINUATION_VERIFICATION_REQUIRED'):
        super().__init__(code, 409)


class BrowserCompletionRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    @staticmethod
    def scope(cursor, row, binding, assistance_id, *, live=True):
        # The bridge runs before normal recovery parsing, so one corrupt owned
        # checkpoint must be isolated here rather than escape the worker hook.
        from .browser_recovery import browser_references
        checkpoint=row.get('checkpoint')
        if not isinstance(checkpoint,dict) or not isinstance(checkpoint.get('execution'),dict):
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_OWNER_MISMATCH')
        try:
            list(browser_references(checkpoint))
        except RunnerError as error:
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_OWNER_MISMATCH') from error
        if (not isinstance(binding,dict) or any(not isinstance(binding.get(key),str) or not binding[key]
                for key in ('runner_id','run_id','runner_execution_id','runner_tool_call_id',
                            'owner_worker_id','owner_boot_id','browser_epoch','owner_endpoint'))):
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_OWNER_MISMATCH')
        try:
            return BrowserCompletionRepository._scope(cursor,row,binding,assistance_id,live=live)
        except LeaseLost as error:
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_OWNER_MISMATCH') from error

    @staticmethod
    def _scope(cursor, row, binding, assistance_id, *, live=True):
        node, tool, digest = owned_browser_execution(row, row['checkpoint'],
            binding['runner_execution_id'], binding['runner_tool_call_id'])
        ref = node.get('resources', {}).get('browser_runs', {}).get(binding['runner_tool_call_id']) or {}
        keys = ('runner_id', 'run_id', 'runner_execution_id', 'runner_tool_call_id',
                'owner_worker_id', 'owner_boot_id', 'browser_epoch', 'owner_endpoint')
        if (ref.get('version') != 1 or ref.get('arguments_digest') != digest
                or any(ref.get(key) != binding.get(key) for key in keys)):
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_OWNER_MISMATCH')
        cursor.execute('SELECT * FROM bs_browser_runs WHERE run_id=%s FOR UPDATE', (binding['run_id'],))
        run = cursor.fetchone()
        _same_binding(run, {**{key: binding[key] for key in keys},
            **{key: row[key] for key in ('tenant_id', 'user_id', 'session_id')}})
        cursor.execute('''SELECT * FROM bs_browser_assistance_requests WHERE assistance_id=%s FOR UPDATE''',
            (assistance_id,))
        wait = cursor.fetchone()
        expected = dict(assistance_id=assistance_id, run_id=binding['run_id'], runner_id=row['runner_id'],
            agent_execution_id=binding['runner_execution_id'], tool_call_id=binding['runner_tool_call_id'],
            owner_boot_id=binding['owner_boot_id'], browser_epoch=binding['browser_epoch'],
            **{key: row[key] for key in ('tenant_id', 'user_id')})
        _same_binding(wait, expected)
        if ref.get('waits', {}).get(wait['runner_wait_id']) != assistance_id:
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_WAIT_MISMATCH')
        if live:
            _live_browser_lease(cursor, run)
            # This separate statement runs after the assistance lock is held.
            # SQL preserves the database timezone semantics of legacy expiry.
            cursor.execute('SELECT clock_timestamp()<%s::timestamptz AS wait_valid',(wait['expires_at'],))
            if wait['state'] not in {'pending', 'controlling'} or not cursor.fetchone()['wait_valid']:
                raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_WAIT_EXPIRED')
        return node, tool, run, wait

    @staticmethod
    def fact(wait):
        fact = wait.get('completion_fact')
        if (not isinstance(fact, dict) or fact.get('version') != 1
                or fact.get('completion_ref') != wait.get('completion_ref')
                or fact.get('assistance_id') != wait['assistance_id']
                or fact.get('wait_id') != wait['runner_wait_id']
                or not isinstance(fact.get('binding'),dict)
                or type(fact.get('step_index')) is not int or fact['step_index']<0
                or type(fact.get('completed_by_human')) is not bool
                or not isinstance(fact.get('continuation'),dict)
                or fact['continuation'].get('phase') not in {'observed','started','completed'}
                or (fact['continuation']['phase']=='completed'
                    and not isinstance(fact['continuation'].get('result'),dict))):
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_FACT_MISSING')
        return fact

    @staticmethod
    def _write_fact(cursor, wait, fact):
        cursor.execute('''UPDATE bs_browser_assistance_requests SET completion_ref=%s,
            completion_fact=%s::jsonb,updated_at=CURRENT_TIMESTAMP WHERE assistance_id=%s''',
            (fact['completion_ref'], json.dumps(fact, ensure_ascii=False), wait['assistance_id']))

    def record_fact(self, binding, assistance_id, *, step_index, completed_by_human):
        if type(step_index) is not int or step_index < 0 or type(completed_by_human) is not bool:
            raise ValueError('BROWSER_COMPLETION_INVALID')
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor, binding['runner_id'])
            lock_original_claim(cursor, row)
            _, tool, run, wait = self.scope(cursor, row, binding, assistance_id)
            if wait.get('completion_ref'):
                fact = self.fact(wait)
                if fact['step_index'] != step_index:
                    raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_STEP_CHANGED')
                return fact
            if (tool.get('phase') not in {'dispatching', 'waiting'}
                    or wait['state'] not in {'pending', 'controlling'} or row['cancel_requested']):
                raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_WAIT_CLOSED')
            cursor.execute('SELECT clock_timestamp() AS database_now')
            fact = dict(version=1, completion_ref='bcf_' + uuid.uuid4().hex,
                assistance_id=assistance_id, wait_id=wait['runner_wait_id'], binding=copy.deepcopy(binding),
                step_index=step_index, completed_by_human=completed_by_human,
                observed_at=cursor.fetchone()['database_now'].isoformat(), continuation={'phase': 'observed'})
            self._write_fact(cursor, wait, fact)
            self.scope(cursor,row,binding,assistance_id)
            from .application_public_projection import sync_public_display
            sync_public_display(cursor,row)
            EventRepository.notify_in_tx(cursor,row)
            connection.commit()
            return fact

    def assert_completion_in_tx(self, cursor, row, request):
        cursor.execute('''SELECT * FROM bs_browser_assistance_requests WHERE runner_id=%s
            AND completion_ref=%s''', (row['runner_id'], request.completion_ref))
        initial = cursor.fetchone()
        if initial is None:
            raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_FACT_MISSING')
        fact = self.fact(initial)
        node, _, _, wait = self.scope(cursor, row, fact['binding'], initial['assistance_id'])
        if (request.target_execution_id != node['execution_id'] or request.wait_id != wait['runner_wait_id']
                or fact['continuation']['phase'] != 'observed'):
            raise BrowserRecoveryUnavailable()
        return wait, fact

    def bridge_parked(self, binding, assistance_id):
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor, binding['runner_id'])
            if (row['cancel_requested'] or row.get('pause_requested')
                    or row['status'] not in {'paused','waiting','interrupted'} or row['lease_valid']):
                return None
            lock_original_claim(cursor, row)
            _, _, _, wait = self.scope(cursor, row, binding, assistance_id)
            if not wait.get('completion_ref'):
                return None
            fact = self.fact(wait)
            if row.get('resume_control_id'):
                # Another legitimate accepted control has priority. Keep this
                # original observation for the next actual park and finite scan.
                return fact.get('control_id') if row['resume_control_id']==fact.get('control_id') else None
            client_key='browser-completion:' + fact['completion_ref']
            if fact.get('control_id'):
                cursor.execute('''SELECT * FROM agent_runner_controls
                    WHERE runner_id=%s AND control_id=%s FOR UPDATE''',(row['runner_id'],fact['control_id']))
                previous=cursor.fetchone()
                if not previous:
                    raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_CONTROL_MISSING')
                if previous['status']!='rejected' or previous['consumed_attempt'] is not None:
                    return fact['control_id']
                # A pause may revoke an unconsumed bridge. Only an actual later
                # explicit resume of this attempt, followed by real park, can
                # associate the same observation with another internal control.
                applied=row['checkpoint'].get('applied_control_id')
                cursor.execute('''SELECT * FROM agent_runner_controls WHERE runner_id=%s
                    AND control_id=%s AND action='resume' AND status='consumed'
                    AND consumed_attempt=%s''',(row['runner_id'],applied,row['attempt']))
                resume=cursor.fetchone()
                if (fact['continuation']['phase']!='observed'
                        or previous.get('error_code')!='RESUME_SUPERSEDED_BY_PAUSE'
                        or not resume or not previous.get('consumed_at') or not resume.get('consumed_at')
                        or resume['accepted_at']<=previous['consumed_at']
                        or resume['consumed_at']<=previous['consumed_at']):
                    return None
                client_key += ':resume:' + resume['control_id']
            if fact['continuation']['phase']!='observed':
                return None
            request = RunnerControl(action='browser_complete',
                client_request_id=client_key,
                target_execution_id=binding['runner_execution_id'], wait_id=wait['runner_wait_id'],
                completion_ref=fact['completion_ref'])
            control = ControlRepository(self.connection_factory, browser_completions=self).accept_completion_in_tx(
                cursor, row, request)
            fact['control_id'] = control['control_id']
            self._write_fact(cursor, wait, fact)
            self.scope(cursor,row,binding,assistance_id)
            from .application_public_projection import sync_public_display
            cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s',(row['runner_id'],))
            sync_public_display(cursor,dict(cursor.fetchone()),advance_view=False)
            EventRepository.notify_in_tx(cursor,row)
            connection.commit()
            return fact['control_id']

    def read_fact(self, runner_id, completion_ref):
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute('''SELECT * FROM bs_browser_assistance_requests
                WHERE runner_id=%s AND completion_ref=%s''', (runner_id, completion_ref))
            wait = cursor.fetchone()
            if wait is None:
                raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_FACT_MISSING')
            return self.fact(wait)

    def unbridged(self, run_id, after_id=0):
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute('''SELECT a.id,a.assistance_id FROM bs_browser_assistance_requests a
                LEFT JOIN agent_runner_controls c ON c.control_id=a.completion_fact->>'control_id'
                WHERE a.run_id=%s AND a.id>%s AND a.completion_ref IS NOT NULL
                AND a.completion_fact->'continuation'->>'phase'='observed'
                AND (a.completion_fact->>'control_id' IS NULL OR
                    (c.runner_id=a.runner_id AND c.status='rejected' AND c.consumed_attempt IS NULL))
                ORDER BY a.id LIMIT 16''', (run_id,after_id))
            return [dict(row) for row in cursor.fetchall()]

    def start(self, attempt, revision, binding, assistance_id, completion_ref):
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
            lock_original_claim(cursor, row)
            if row['revision'] != revision or binding['owner_worker_id'] != attempt.worker_id:
                raise LeaseLost('BROWSER_CONTINUATION_ATTEMPT_CHANGED')
            node, _, _, wait = self.scope(cursor, row, binding, assistance_id)
            fact = self.fact(wait)
            if fact['completion_ref'] != completion_ref or fact['continuation']['phase'] != 'observed':
                raise BrowserRecoveryUnavailable()
            cursor.execute('SELECT * FROM agent_runner_controls WHERE control_id=%s', (fact.get('control_id'),))
            control = cursor.fetchone()
            payload=control.get('payload') if control else None
            if (not control or control['runner_id']!=attempt.runner_id
                    or control['action']!='browser_complete' or control['status']!='consumed'
                    or type(control.get('consumed_attempt')) is not int
                    or not 0<control['consumed_attempt']<=attempt.number
                    or not control.get('consumed_at') or not isinstance(payload,dict)
                    or payload.get('target_execution_id')!=binding['runner_execution_id']
                    or payload.get('wait_id')!=fact['wait_id'] or payload.get('completion_ref')!=completion_ref
                    or node['resources'].get('browser_completions',{}).get(binding['runner_tool_call_id'])!=completion_ref):
                raise BrowserRecoveryUnavailable('BROWSER_COMPLETION_NOT_CONSUMED')
            if control['consumed_attempt']!=attempt.number:
                # Consuming a completion is not the physical action watermark.
                # A pause before start can be resumed under a later explicit
                # attempt, while the original wait/control stays consumed once.
                cursor.execute('''SELECT * FROM agent_runner_controls WHERE runner_id=%s
                    AND control_id=%s AND action='resume' AND status='consumed'
                    AND consumed_attempt=%s''',
                    (attempt.runner_id,row['checkpoint'].get('applied_control_id'),attempt.number))
                resume=cursor.fetchone()
                if (not resume or not resume.get('consumed_at')
                        or resume['accepted_at']<control['consumed_at']
                        or resume['consumed_at']<=control['consumed_at']):
                    raise BrowserRecoveryUnavailable('BROWSER_CONTINUATION_NOT_RESUMED')
            fact['continuation'] = dict(phase='started', attempt=attempt.number, worker_id=attempt.worker_id)
            self._write_fact(cursor, wait, fact)
            self.scope(cursor,row,binding,assistance_id)
            lock_runner(cursor, attempt.runner_id, attempt, dispatch=True)
            from .application_public_projection import sync_public_display
            sync_public_display(cursor,row)
            EventRepository.notify_in_tx(cursor,row)
            connection.commit()
            return fact

    def finish(self, attempt, binding, assistance_id, completion_ref, result):
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor, attempt.runner_id, attempt)
            lock_original_claim(cursor, row)
            _, _, _, wait = self.scope(cursor, row, binding, assistance_id, live=False)
            fact = self.fact(wait)
            if (fact['completion_ref'] != completion_ref or fact['continuation'] !=
                    dict(phase='started', attempt=attempt.number, worker_id=attempt.worker_id)):
                raise BrowserRecoveryUnavailable()
            fact['continuation'] = dict(phase='completed', attempt=attempt.number,
                worker_id=attempt.worker_id, result=copy.deepcopy(result))
            self._write_fact(cursor, wait, fact)
            cursor.execute('''UPDATE bs_browser_assistance_requests SET state='resumed',
                resumed_at=CURRENT_TIMESTAMP WHERE assistance_id=%s''', (assistance_id,))
            lock_runner(cursor, attempt.runner_id, attempt)
            from .application_public_projection import sync_public_display
            sync_public_display(cursor,row)
            EventRepository.notify_in_tx(cursor,row)
            connection.commit()
            return fact

    def hold(self, runner_id, revision, control_id, code):
        """Public verification without consuming/rejecting the original control."""
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            row = lock_runner(cursor, runner_id)
            if row['revision'] != revision or row['resume_control_id'] != control_id or row['lease_valid']:
                return False
            if row['cancel_requested'] or row['status'] not in {'paused','waiting','interrupted'}:
                return False
            lock_original_claim(cursor, row)
            snapshot = copy.deepcopy(row.get('public_snapshot') or {})
            snapshot['progress'] = '原浏览器执行尚待核对，暂未继续原操作'
            snapshot['waiting'] = dict(kind='verification', error_code=code,
                question='原浏览器执行尚待核对，暂未继续原操作')
            from .application_public_projection import project_public_snapshot
            snapshot=project_public_snapshot(cursor,row,snapshot=snapshot)
            if snapshot == (row.get('public_snapshot') or {}):
                return False
            cursor.execute('''UPDATE agent_runners SET public_snapshot=%s::jsonb,
                view_revision=view_revision+1,updated_at=clock_timestamp() WHERE runner_id=%s''',
                (capped_snapshot_dumps(snapshot), runner_id))
            EventRepository.notify_in_tx(cursor,row)
            connection.commit()
            return True
