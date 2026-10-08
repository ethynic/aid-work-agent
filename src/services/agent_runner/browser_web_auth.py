"""Private fresh Web authorization for observing an original Browser runtime."""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from src.db.database import get_db_connection
from src.services.auth_service import AuthSubjectError, fresh_web_subject
from .browser_binding import owned_browser_execution
from .browser_endpoint import trusted_browser_endpoint
from .contracts import RunnerError
from .repository import decoded


def _fresh_user(authorization):
    """中性主体校验；同失败语义转 Runner 统一错误，包内不导入 Web 路由。"""
    try:
        return fresh_web_subject(authorization)
    except AuthSubjectError:
        raise RunnerError('USER_UNAUTHORIZED', 401) from None


class BrowserViewAssertion(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    version: StrictInt
    token_row_id: StrictInt
    user_id: StrictStr
    tenant_id: StrictStr
    session_id: StrictStr
    runner_id: StrictStr
    run_id: StrictStr
    runner_execution_id: StrictStr
    runner_tool_call_id: StrictStr
    assistance_id: StrictStr
    runner_wait_id: StrictStr
    owner_worker_id: StrictStr
    owner_boot_id: StrictStr
    browser_epoch: StrictStr
    owner_endpoint: StrictStr


class BrowserWebAuth:
    def __init__(self, config, connection_factory=get_db_connection):
        self.config, self.connection_factory = config, connection_factory

    def is_native(self, run_id):
        # Identify the PG binding before choosing a transport. DB/auth failures
        # never turn an original native run into a legacy Redis-only run.
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute('SELECT runner_id FROM bs_browser_runs WHERE run_id=%s', (run_id,))
            row = cursor.fetchone()
            if row is None:
                raise RunnerError('BROWSER_RUN_NOT_FOUND',404)
            if row['runner_id'] is None:
                cursor.execute('''SELECT 1 FROM bs_browser_assistance_requests
                    WHERE run_id=%s AND runner_id IS NOT NULL LIMIT 1''',(run_id,))
                if cursor.fetchone() is not None:
                    raise RunnerError('BROWSER_RUN_OWNER_MISMATCH',403)
            return row['runner_id'] is not None

    @staticmethod
    def _token_user(cursor, token_row_id, expected_user):
        cursor.execute('''SELECT u.*,t.expires_at AS token_expires_at FROM tokens t
            JOIN users u ON u.user_id=t.user_id WHERE t.id=%s''', (token_row_id,))
        user = cursor.fetchone()
        expiration = user.get('token_expires_at') if user else None
        if isinstance(expiration, str):
            expiration = datetime.fromisoformat(expiration)
        now = datetime.now(timezone.utc) if expiration and expiration.tzinfo else datetime.now()
        if (not user or user['user_id'] != expected_user or user['status'] != 'active'
                or not expiration or expiration <= now):
            raise RunnerError('USER_UNAUTHORIZED', 401)
        return user

    def _binding(self, cursor, run_id, user, *, live=True):
        cursor.execute('SELECT *,clock_timestamp() AS database_now FROM bs_browser_runs WHERE run_id=%s', (run_id,))
        run = cursor.fetchone()
        if (not run or not run['runner_id'] or (live and (run['runtime_state'] != 'live'
                or not run['owner_lease_until'] or run['owner_lease_until'] <= run['database_now']))
                or run['user_id'] != user['user_id']):
            raise RunnerError('BROWSER_RUNTIME_NOT_AVAILABLE', 409)
        trusted_browser_endpoint(run['owner_endpoint'], self.config.allowed_endpoints)
        # Match the existing owned_web_session policy: a platform administrator
        # can own a real-tenant session while its user row has tenant_id=NULL.
        if user['role'] != 'platform_admin' and user['tenant_id'] != run['tenant_id']:
            raise RunnerError('TENANT_FORBIDDEN', 403)
        cursor.execute('''SELECT session_id FROM chat_sessions WHERE session_id=%s
            AND user_id=%s AND tenant_id IS NOT DISTINCT FROM %s''',
            (run['session_id'], user['user_id'], run['tenant_id']))
        if cursor.fetchone() is None:
            raise RunnerError('SESSION_NOT_FOUND', 404)
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s', (run['runner_id'],))
        raw = cursor.fetchone()
        row = decoded(raw) if raw else None
        if (not row or row['session_kind'] != 'web' or row['source'] != 'chat'
                or row['status'] in {'completed','failed','cancelled','iteration_limit'}
                or any(row[key] != run[key] for key in ('tenant_id','user_id','session_id'))):
            raise RunnerError('BROWSER_RUN_OWNER_MISMATCH', 403)
        cursor.execute('''SELECT owner_runner_id FROM agent_runner_session_claims
            WHERE scope_key=%s AND session_kind='web' AND session_id=%s''',
            (row['scope_key'],row['session_id']))
        claim = cursor.fetchone()
        if not claim or claim['owner_runner_id'] != row['runner_id']:
            raise RunnerError('BROWSER_RUN_OWNER_MISMATCH',403)
        node, tool, digest = owned_browser_execution(row, row['checkpoint'],
            run['runner_execution_id'], run['runner_tool_call_id'])
        ref = node.get('resources', {}).get('browser_runs', {}).get(run['runner_tool_call_id']) or {}
        keys = ('runner_id','run_id','runner_execution_id','runner_tool_call_id',
                'owner_worker_id','owner_boot_id','browser_epoch','owner_endpoint')
        if (ref.get('version') != 1 or ref.get('arguments_digest') != digest
                or tool.get('phase') not in {'dispatching','waiting'}
                or any(ref.get(key) != run[key] for key in keys)):
            raise RunnerError('BROWSER_RUN_OWNER_MISMATCH', 403)
        cursor.execute('''SELECT * FROM bs_browser_assistance_requests WHERE run_id=%s
            AND runner_id=%s AND state IN ('pending','controlling')
            AND (%s=FALSE OR expires_at > clock_timestamp())''', (run_id, row['runner_id'],live))
        waits = cursor.fetchall()
        if len(waits) != 1:
            raise RunnerError('BROWSER_WAIT_NOT_AVAILABLE', 409)
        wait = waits[0]
        if (any(wait[key] != run[key] for key in ('runner_id','tenant_id','user_id','run_id','owner_boot_id','browser_epoch'))
                or wait['agent_execution_id'] != run['runner_execution_id']
                or wait['tool_call_id'] != run['runner_tool_call_id']
                or ref.get('waits', {}).get(wait['runner_wait_id']) != wait['assistance_id']):
            raise RunnerError('BROWSER_WAIT_OWNER_MISMATCH', 403)
        return {key: run[key] for key in keys} | dict(
            user_id=run['user_id'], tenant_id=run['tenant_id'], session_id=run['session_id'],
            assistance_id=wait['assistance_id'], runner_wait_id=wait['runner_wait_id'])

    def issue_assertion(self, authorization, run_id, *, target_tenant=None, live=True):
        user = _fresh_user(authorization)
        bearer = authorization[7:] if authorization.startswith('Bearer ') else None
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            cursor.execute('SELECT id FROM tokens WHERE token=%s AND user_id=%s', (bearer,user['user_id']))
            token = cursor.fetchone()
            if not token:
                raise RunnerError('USER_UNAUTHORIZED',401)
            current = self._token_user(cursor, token['id'], user['user_id'])
            binding = self._binding(cursor,run_id,current,live=live)
            if target_tenant is not None and binding['tenant_id'] != target_tenant:
                raise RunnerError('TENANT_FORBIDDEN',403)
            return BrowserViewAssertion(version=1,token_row_id=token['id'], **binding)

    def authorize_view(self, assertion):
        assertion = BrowserViewAssertion.model_validate(assertion)
        if assertion.version != 1 or assertion.token_row_id <= 0:
            raise RunnerError('BROWSER_VIEW_TICKET_INVALID',403)
        with self.connection_factory() as connection:
            cursor = connection.cursor()
            user = self._token_user(cursor, assertion.token_row_id, assertion.user_id)
            expected = self._binding(cursor,assertion.run_id,user)
            if any(getattr(assertion,key) != value for key,value in expected.items()):
                raise RunnerError('BROWSER_VIEW_OWNER_CHANGED',403)
        return assertion

    def authorize_action(self, assertion, *, execute=True, credit=False):
        """Fresh manual identity plus existing persisted execution policy."""
        from src.config.settings import settings
        from .authorization import RunnerAuthorizer
        assertion=BrowserViewAssertion.model_validate(assertion)
        if execute:
            assertion=self.authorize_view(assertion)
        else:
            with self.connection_factory() as connection:
                cursor=connection.cursor()
                user=self._token_user(cursor,assertion.token_row_id,assertion.user_id)
                expected=self._binding(cursor,assertion.run_id,user,live=False)
                if any(getattr(assertion,key)!=value for key,value in expected.items()):
                    raise RunnerError('BROWSER_VIEW_OWNER_CHANGED',403)
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s',(assertion.runner_id,))
            row=decoded(cursor.fetchone())
            node,_,_=owned_browser_execution(row,row['checkpoint'],
                assertion.runner_execution_id,assertion.runner_tool_call_id)
        authorizer=RunnerAuthorizer(settings.agent_runner,self.connection_factory)
        principal=authorizer.authorize_persisted(row,execute=execute)
        if execute:
            if node['profile_id']!=row['profile_id']:
                authorizer.authorize_child_profile(principal,node['profile_id'])
            if credit:
                authorizer.assert_credit(principal)
        return row,principal

    def _completion_read(self, cursor, user, run_id, assistance_id, *, target_tenant=None, assertion=None):
        """Read a committed original wait fact; this grants no view or IO right."""
        from .browser_completion import BrowserCompletionRepository
        cursor.execute('SELECT * FROM bs_browser_runs WHERE run_id=%s',(run_id,))
        run=cursor.fetchone()
        if (not run or not run['runner_id'] or run['user_id']!=user['user_id']
                or (target_tenant is not None and run['tenant_id']!=target_tenant)
                or (user['role']!='platform_admin' and user['tenant_id']!=run['tenant_id'])):
            raise RunnerError('BROWSER_RUN_OWNER_MISMATCH',403)
        cursor.execute('''SELECT 1 FROM chat_sessions WHERE session_id=%s AND user_id=%s
            AND tenant_id IS NOT DISTINCT FROM %s''',(run['session_id'],user['user_id'],run['tenant_id']))
        if cursor.fetchone() is None:
            raise RunnerError('SESSION_NOT_FOUND',404)
        # Root first, then the original run/assistance locks used by scope.
        cursor.execute('SELECT * FROM agent_runners WHERE runner_id=%s FOR UPDATE',(run['runner_id'],))
        raw=cursor.fetchone()
        row=decoded(raw) if raw else None
        if (not row or row['session_kind']!='web' or row['source']!='chat'
                or any(row[key]!=run[key] for key in ('tenant_id','user_id','session_id'))):
            raise RunnerError('BROWSER_RUN_OWNER_MISMATCH',403)
        keys=('runner_id','run_id','runner_execution_id','runner_tool_call_id',
              'owner_worker_id','owner_boot_id','browser_epoch','owner_endpoint')
        binding={key:run[key] for key in keys}
        _,_,_,wait=BrowserCompletionRepository.scope(cursor,row,binding,assistance_id,live=False)
        if assertion is not None:
            expected=binding|{key:run[key] for key in ('user_id','tenant_id','session_id')}|dict(
                assistance_id=assistance_id,runner_wait_id=wait['runner_wait_id'])
            if any(getattr(assertion,key)!=value for key,value in expected.items()):
                raise RunnerError('BROWSER_VIEW_OWNER_CHANGED',403)
        if not wait.get('completion_ref'):
            return None
        fact=BrowserCompletionRepository.fact(wait)
        expected=binding|{key:row[key] for key in ('tenant_id','user_id','session_id')}
        if any(fact['binding'].get(key)!=value for key,value in expected.items()):
            raise RunnerError('BROWSER_COMPLETION_OWNER_MISMATCH',403)
        return row,wait,fact

    def completion_read(self, authorization, run_id, assistance_id, *, target_tenant=None):
        user=_fresh_user(authorization)
        bearer=authorization[7:] if authorization.startswith('Bearer ') else None
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            cursor.execute('SELECT id FROM tokens WHERE token=%s AND user_id=%s',(bearer,user['user_id']))
            token=cursor.fetchone()
            if not token:
                raise RunnerError('USER_UNAUTHORIZED',401)
            current=self._token_user(cursor,token['id'],user['user_id'])
            known=self._completion_read(cursor,current,run_id,assistance_id,target_tenant=target_tenant)
        return self._authorize_completion_read(known)

    def completion_read_assertion(self, assertion):
        assertion=BrowserViewAssertion.model_validate(assertion)
        if assertion.version!=1 or assertion.token_row_id<=0:
            raise RunnerError('BROWSER_VIEW_TICKET_INVALID',403)
        with self.connection_factory() as connection:
            cursor=connection.cursor()
            user=self._token_user(cursor,assertion.token_row_id,assertion.user_id)
            known=self._completion_read(cursor,user,assertion.run_id,assertion.assistance_id,assertion=assertion)
        return self._authorize_completion_read(known)

    def _authorize_completion_read(self, known):
        if known is not None:
            from src.config.settings import settings
            from .authorization import RunnerAuthorizer
            RunnerAuthorizer(settings.agent_runner,self.connection_factory).authorize_persisted(known[0],execute=False)
        return known
