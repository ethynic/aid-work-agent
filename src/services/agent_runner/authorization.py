"""Validate internal callers and the current database principal/session binding."""

from datetime import datetime, timezone

from src.core.agent_engine.contracts import Identity
from src.db.database import get_db_connection
from src.db.models import verify_password
from src.saas.models.enums import TenantStatus, UserRole, UserStatus
from .contracts import Principal, RunnerError, canonical_json


class RunnerAuthorizer:
    def __init__(self, config, connection_factory=get_db_connection, token_verifier=None, source_port=None):
        self.config, self.connection_factory = config, connection_factory
        if token_verifier is None:
            # 中性认证原语：认证服务不反向依赖 src.api，Runner 侧零 Web 路由导入。
            from src.services.auth_service import verify_token
            token_verifier = verify_token
        self.token_verifier = token_verifier
        self.source_port = source_port

    def verify_service(self, service_id, service_token, source):
        peer = self.config.peers.get(service_id or "")
        if not peer or not service_token or not peer.token_hash or not verify_password(service_token, peer.token_hash):
            raise RunnerError("SERVICE_UNAUTHORIZED", 401)
        if source is not None and source not in peer.sources:
            raise RunnerError("SERVICE_SOURCE_FORBIDDEN", 403)
        return service_id

    def authorize(self, request, service_id, service_token, user_token=None, target_tenant=None,
                  actor_user=None, actor_chat=None, actor_source=None, *, execute=True):
        self.verify_service(service_id, service_token, request.source)
        if request.session.kind == "web":
            if request.source != "chat" or request.channel_user_id is not None or request.channel_chat_id is not None:
                raise RunnerError("SESSION_SOURCE_MISMATCH", 400)
            user_id = self.token_verifier(user_token, auto_refresh=False) if user_token else None
            if not user_id:
                raise RunnerError("USER_UNAUTHORIZED", 401)
            # Redis can retain a previously valid token after an administrative
            # revocation or expiry shortening. Critical runner requests also check
            # the authoritative row; this raw token is never persisted elsewhere.
            with self.connection_factory() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id,expires_at FROM tokens WHERE token=%s", (user_token,))
                token = cursor.fetchone()
                if not token or token["user_id"] != user_id or self._expired(token["expires_at"]):
                    raise RunnerError("USER_UNAUTHORIZED", 401)
            return self._web(request, service_id, user_id, target_tenant, execute=execute)
        if request.source == "chat":
            raise RunnerError("SESSION_SOURCE_MISMATCH", 400)
        if actor_source is not None and actor_source != request.source:
            raise RunnerError("CHANNEL_ACTOR_FORBIDDEN", 403)
        if actor_user is not None and (actor_user != request.channel_user_id or actor_chat != request.channel_chat_id):
            raise RunnerError("CHANNEL_ACTOR_FORBIDDEN", 403)
        return self._channel(request, service_id, execute=execute)

    def authorize_persisted(self, row, *, execute=True, prepared_source=None):
        """No original bearer is needed or stored after durable acceptance."""
        from .contracts import RunnerSubmit
        request = RunnerSubmit(client_request_id=row["client_request_id"], **row["input"])
        request = request.model_copy(update={'profile_id':row['profile_id']})
        peer = self.config.peers.get(row["service_id"])
        if not peer or request.source not in peer.sources:
            raise RunnerError("SERVICE_SOURCE_FORBIDDEN", 403)
        if row["session_kind"] == "web":
            principal = self._web(request, row["service_id"], row["user_id"], row["tenant_id"],execute=execute)
        else:
            with self.connection_factory() as conn:
                cursor=conn.cursor()
                if self.source_port:
                    self.source_port.authorize_row_in_tx(cursor,row,execute=execute,prepared=prepared_source)
                principal=self._channel_in_tx(cursor,request,row['service_id'],execute=execute)
        from .repository import RunnerRepository
        RunnerRepository.assert_owner(principal, row)
        return principal

    def assert_credit(self, principal):
        tenant_id = principal.identity.tenant_id
        if tenant_id is None:
            return
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT credit_balance FROM tenants WHERE tenant_id=%s", (tenant_id,))
            row = cursor.fetchone()
            if not row or row["credit_balance"] <= 0:
                raise RunnerError("CREDIT_BLOCKED", 402)

    def authorize_read_in_tx(self, cursor, row, credentials, *, verified_user_id=None):
        """Service/token verification precedes this short consistent read transaction."""
        from .contracts import RunnerSubmit
        from .repository import RunnerRepository
        service_id = credentials['service_id']
        peer = self.config.peers.get(service_id)
        if not peer or row['source'] not in peer.sources:
            raise RunnerError('SERVICE_SOURCE_FORBIDDEN',403)
        intent = dict(row['input'])
        if row['session_kind'] == 'channel':
            if credentials.get('actor_source') != row['source'] or not credentials.get('actor_user'):
                raise RunnerError('CHANNEL_ACTOR_REQUIRED',403)
            intent['channel_user_id'] = credentials['actor_user']
            intent['channel_chat_id'] = credentials.get('actor_chat')
        request = RunnerSubmit(client_request_id=row['client_request_id'], **intent)
        if row['session_kind'] == 'web':
            token = credentials.get('user_token')
            cursor.execute('''SELECT user_id,expires_at>clock_timestamp() AS valid
                FROM tokens WHERE token=%s''',(token,))
            original = cursor.fetchone()
            if not verified_user_id or not original or original['user_id'] != verified_user_id or not original['valid']:
                raise RunnerError('USER_UNAUTHORIZED',401)
            principal = self._web_in_tx(cursor,request,service_id,verified_user_id,
                                        credentials.get('target_tenant'),execute=False)
        else:
            if self.source_port:
                self.source_port.authorize_row_in_tx(cursor,row,credentials,execute=False)
            principal = self._channel_in_tx(cursor,request,service_id,execute=False)
        RunnerRepository.assert_owner(principal,row)
        return principal

    def authorize_row(self, row, credentials):
        self.verify_service(credentials['service_id'],credentials['service_token'],row['source'])
        with self.connection_factory() as conn:
            return self.authorize_read_in_tx(conn.cursor(),row,credentials)


    def authorize_child_profile(self, principal, profile_id):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            user = None
            if principal.identity.user_id is not None:
                cursor.execute('SELECT * FROM users WHERE user_id=%s',(principal.identity.user_id,))
                row = cursor.fetchone()
                if not row:
                    raise RunnerError('USER_UNAUTHORIZED',401)
                user = dict(row)
            self._profile(cursor,profile_id,user,principal.identity.tenant_id)

    def _web(self, request, service_id, user_id, target_tenant, *, execute=True):
        with self.connection_factory() as conn:
            return self._web_in_tx(conn.cursor(),request,service_id,user_id,target_tenant,execute=execute)

    def _web_in_tx(self, cursor, request, service_id, user_id, target_tenant, *, execute=True):
        cursor.execute("SELECT user_id,tenant_id,role,status FROM users WHERE user_id=%s", (user_id,))
        user = cursor.fetchone()
        if not user or user["status"] != UserStatus.ACTIVE.value:
            raise RunnerError("USER_UNAUTHORIZED", 401)
        is_platform = user["role"] == UserRole.PLATFORM_ADMIN.value
        tenant_id = target_tenant if is_platform else user["tenant_id"]
        if not is_platform and target_tenant is not None and target_tenant != tenant_id:
            raise RunnerError("TENANT_FORBIDDEN", 403)
        if tenant_id is None and not is_platform:
            raise RunnerError("TENANT_FORBIDDEN", 403)
        cursor.execute("""SELECT * FROM chat_sessions WHERE session_id=%s
            AND tenant_id IS NOT DISTINCT FROM %s AND user_id=%s""",
            (request.session.session_id, tenant_id, user_id))
        session = cursor.fetchone()
        if not session:
            raise RunnerError("SESSION_NOT_FOUND", 404)
        if execute:
            if request.instance_id and session.get('instance_id') and request.instance_id != session['instance_id']:
                raise RunnerError('INSTANCE_SCOPE_MISMATCH', 403)
            self._tenant(cursor, tenant_id)
            self._profile(cursor, request.profile_id, dict(user), tenant_id)
        return Principal(Identity(tenant_id, user_id, request.session.session_id, "chat", "web"),
                     "user", user_id, service_id)

    def _channel(self, request, service_id, *, execute=True):
        with self.connection_factory() as conn:
            return self._channel_in_tx(conn.cursor(),request,service_id,execute=execute)

    def _channel_in_tx(self, cursor, request, service_id, *, execute=True):
        if not request.channel_user_id:
            raise RunnerError("CHANNEL_ACTOR_REQUIRED", 400)
        cursor.execute("SELECT * FROM channel_sessions WHERE session_id=%s", (request.session.session_id,))
        session = cursor.fetchone()
        if not session or not session["tenant_id"] or session["channel_type"] != request.source:
            raise RunnerError("SESSION_NOT_FOUND", 404)
        if session["channel_user_id"] != request.channel_user_id or (session.get("channel_chat_id") or None) != request.channel_chat_id:
            raise RunnerError("CHANNEL_ACTOR_FORBIDDEN", 403)
        if execute and (session.get("subagent_id") or "main") != request.profile_id:
            raise RunnerError("CHANNEL_PROFILE_MISMATCH", 403)
        if execute:
            self._tenant(cursor, session["tenant_id"])
        user_id = session.get("user_id")
        if user_id:
            # 绑定用户仅做存在性/有效性检查：渠道会话绑定的常是外部客户映射账号
            # （普通角色，无用户级智能体授权概念），用户级权限对渠道路径不适用。
            cursor.execute("SELECT user_id,tenant_id,role,status FROM users WHERE user_id=%s AND tenant_id=%s",
                           (user_id, session["tenant_id"]))
            user = cursor.fetchone()
            if not user or user["status"] != UserStatus.ACTIVE.value:
                raise RunnerError("CHANNEL_USER_FORBIDDEN", 403)
        if execute:
            # 渠道执行授权只看租户订阅，与产品授权模型一致（绑定用户不构成为
            # web 用户，平台 actor 分支同样不查用户级权限）。
            self._profile(cursor, request.profile_id, None, session["tenant_id"])
        if execute and request.source == 'wecom_kf':
            from .channel_context import kf_binding
            kf_binding(cursor, request, session)
        actor_id = canonical_json([request.channel_user_id, request.channel_chat_id])
        return Principal(Identity(session["tenant_id"], user_id, request.session.session_id, request.source, "channel"),
                     "channel", actor_id, service_id)

    @staticmethod
    def _expired(expiration):
        if not expiration:
            return True
        if isinstance(expiration, str):
            expiration = datetime.fromisoformat(expiration)
        current = datetime.now(timezone.utc) if expiration.tzinfo else datetime.now()
        return expiration <= current

    @staticmethod
    def _tenant(cursor, tenant_id):
        if tenant_id is None:
            return
        cursor.execute("SELECT status,expire_at FROM tenants WHERE tenant_id=%s", (tenant_id,))
        tenant = cursor.fetchone()
        if not tenant or tenant["status"] != TenantStatus.ACTIVE.value:
            raise RunnerError("TENANT_UNAVAILABLE", 403)
        expiration = tenant.get("expire_at")
        if expiration:
            if RunnerAuthorizer._expired(expiration):
                raise RunnerError("TENANT_EXPIRED", 403)

    @staticmethod
    def _profile(cursor, profile_id, user, tenant_id):
        # Existing Web's default master route has no selected subscription. This
        # is an execution policy for main, never a substitute for session ownership.
        if profile_id == "main":
            return
        if tenant_id is None and user and user["role"] == UserRole.PLATFORM_ADMIN.value:
            return
        cursor.execute("""SELECT 1 FROM subscriptions WHERE tenant_id=%s AND subagent_type=%s
            AND status='active' AND starts_at<=CURRENT_TIMESTAMP
            AND (expires_at IS NULL OR expires_at>CURRENT_TIMESTAMP) LIMIT 1""", (tenant_id, profile_id))
        if not cursor.fetchone():
            raise RunnerError("PROFILE_FORBIDDEN", 403)
        if user and user["role"] not in (UserRole.PLATFORM_ADMIN.value, UserRole.TENANT_ADMIN.value):
            cursor.execute("""SELECT 1 FROM user_agent_permissions WHERE user_id=%s
                AND agent_id=%s AND tenant_id IS NOT DISTINCT FROM %s LIMIT 1""",
                (user["user_id"], profile_id, tenant_id))
            if not cursor.fetchone():
                raise RunnerError("PROFILE_FORBIDDEN", 403)
