"""Original assistance actions. PostgreSQL owns state, expiry and one extension."""

from .browser_completion import BrowserCompletionRepository
from .contracts import RunnerError
from .ownership import lock_runner
from .recovery_repository import lock_original_claim
from src.db.database import get_db_connection
from .event_repository import EventRepository


class BrowserHumanRepository:
    def __init__(self, connection_factory=get_db_connection):
        self.connection_factory = connection_factory

    @staticmethod
    def _expiry(cursor,wait):
        cursor.execute('SELECT expires_at::timestamptz AS expiry_instant FROM bs_browser_assistance_requests WHERE assistance_id=%s',
            (wait['assistance_id'],))
        return dict(wait,expiry_instant=cursor.fetchone()['expiry_instant'])

    @staticmethod
    def _scope(cursor, binding, assistance_id, *, controlling=False):
        row = lock_runner(cursor, binding['runner_id'])
        lock_original_claim(cursor, row)
        node, tool, run, wait = BrowserCompletionRepository.scope(cursor,row,binding,assistance_id)
        if row['cancel_requested'] or row.get('pause_requested'):
            raise RunnerError('HUMAN_ACTION_EXECUTION_STOPPED',409)
        if tool.get('phase') not in {'dispatching','waiting'}:
            raise RunnerError('HUMAN_ACTION_WAIT_CLOSED',409)
        if wait.get('completion_ref'):
            raise RunnerError('RESUME_ALREADY_CONSUMED',409)
        if controlling and wait['state'] != 'controlling':
            raise RunnerError('HUMAN_CONTROL_REQUIRED',409)
        return row,node,run,wait

    def check(self,binding,assistance_id,*,controlling=False):
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            return self._scope(cursor,binding,assistance_id,controlling=controlling)

    def completion(self,binding,assistance_id):
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            row=lock_runner(cursor,binding['runner_id'])
            lock_original_claim(cursor,row)
            _,_,_,wait=BrowserCompletionRepository.scope(cursor,row,binding,assistance_id,live=False)
            return BrowserCompletionRepository.fact(wait) if wait.get('completion_ref') else None

    def projection(self,binding,assistance_id):
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            row=lock_runner(cursor,binding['runner_id'])
            lock_original_claim(cursor,row)
            _,_,_,wait=BrowserCompletionRepository.scope(cursor,row,binding,assistance_id,live=False)
            return self._expiry(cursor,wait)

    def assert_cancel_owner(self,attempt,binding):
        from .browser_binding import owned_browser_execution,_same_binding,_live_browser_lease
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            row=lock_runner(cursor,attempt.runner_id,attempt)
            lock_original_claim(cursor,row)
            if not row['cancel_requested'] or attempt.worker_id!=binding['owner_worker_id']:
                raise RunnerError('BROWSER_CANCEL_OWNER_MISMATCH',409)
            node,_,_=owned_browser_execution(row,row['checkpoint'],binding['runner_execution_id'],binding['runner_tool_call_id'])
            ref=node.get('resources',{}).get('browser_runs',{}).get(binding['runner_tool_call_id']) or {}
            if any(ref.get(key)!=binding[key] for key in ('run_id','owner_worker_id','owner_boot_id','browser_epoch','owner_endpoint')):
                raise RunnerError('BROWSER_CANCEL_OWNER_MISMATCH',409)
            cursor.execute('SELECT * FROM bs_browser_runs WHERE run_id=%s FOR UPDATE',(binding['run_id'],))
            run=cursor.fetchone()
            _same_binding(run,binding)
            if run['runtime_state']=='closed':
                return False
            _live_browser_lease(cursor,run)
            lock_runner(cursor,attempt.runner_id,attempt)
            return True

    def take(self,binding,assistance_id):
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            row,_,_,wait=self._scope(cursor,binding,assistance_id)
            if wait['state'] not in {'pending','controlling'}:
                raise RunnerError('CONTROL_ALREADY_TAKEN',409)
            cursor.execute("""UPDATE bs_browser_assistance_requests SET state='controlling',
                updated_at=clock_timestamp() WHERE assistance_id=%s RETURNING *""",(assistance_id,))
            updated=cursor.fetchone()
            self._scope(cursor,binding,assistance_id,controlling=True)
            updated=self._expiry(cursor,updated)
            from .application_public_projection import sync_public_display
            sync_public_display(cursor,row)
            EventRepository.notify_in_tx(cursor,row)
            connection.commit()
            return updated

    def extend(self,binding,assistance_id,seconds):
        if type(seconds) is not int or seconds<=0:
            raise ValueError('HUMAN_EXTENSION_INVALID')
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            row,_,_,wait=self._scope(cursor,binding,assistance_id)
            if wait['extended_at'] is not None:
                # Reprojection after a Redis failure uses this exact deadline,
                # and does not grant another extension.
                return self._expiry(cursor,wait)
            cursor.execute("""UPDATE bs_browser_assistance_requests
                SET extended_at=clock_timestamp(),expires_at=clock_timestamp()+%s*interval '1 second',
                updated_at=clock_timestamp() WHERE assistance_id=%s AND extended_at IS NULL RETURNING *""",
                (seconds,assistance_id))
            updated=cursor.fetchone()
            self._scope(cursor,binding,assistance_id)
            updated=self._expiry(cursor,updated)
            from .application_public_projection import sync_public_display
            sync_public_display(cursor,row)
            EventRepository.notify_in_tx(cursor,row)
            connection.commit()
            return updated
