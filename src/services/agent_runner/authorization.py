"""Validate internal callers and the current database principal/session binding."""

import hmac
import json
from datetime import datetime, timezone

from src.core.agent_engine.contracts import Identity
from src.db.database import get_db_connection
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
        # 明文凭据唯一验证源：主 API 与 runner-api 共用同一 .env 注入的
        # AGENT_RUNNER_WEB_SERVICE_TOKEN，恒等比较即验证（无 config.yaml 回退）。
        peer = self.config.peers.get(service_id or "")
        if (not peer or not service_token or not self.config.web_service_token
                or not hmac.compare_digest(service_token, self.config.web_service_token)):
            raise RunnerError("SERVICE_UNAUTHORIZED", 401)
        if source is not None and source not in peer.sources:
            raise RunnerError("SERVICE_SOURCE_FORBIDDEN", 403)
        return service_id

    def authorize(self, request, service_id, service_token, user_token=None, target_tenant=None,
                  actor_user=None, actor_chat=None, actor_source=None, *, execute=True):
        self.verify_service(service_id, service_token, request.source)
        if request.session.kind == "web":
            if (request.source not in ("chat", "scheduled")
                    or request.channel_user_id is not None or request.channel_chat_id is not None):
                raise RunnerError("SESSION_SOURCE_MISMATCH", 400)
            if request.source == "scheduled":
                # scheduled 来源：任务专属会话由 scheduler 服务身份提交；
                # 用户 Bearer token 仅只读（设计 §5.2 三语义）。
                return self._scheduled(request, service_id, user_token, target_tenant, execute=execute)
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
            if request.source == "scheduled":
                # 已受理重校验（设计 §5.2）：任务行存在 + 租户/用户 active +
                # profile 校验，不要求任务 active（已受理执行在任务暂停后继续完成）。
                with self.connection_factory() as conn:
                    principal = self._scheduled_in_tx(conn.cursor(), request, row["service_id"],
                        execute=execute, persisted={"tenant_id": row["tenant_id"], "user_id": row["user_id"]})
            else:
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
        # 私有云部署跳过（客户自购 key，无积分概念，余额恒 0 不得 402 阻断）
        from src.config.settings import settings
        if settings.deployment.private_cloud:
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
            if request.source == "scheduled":
                # scheduled 行读取（任务视图事件流等）：用户 token 只读（属主
                # 匹配）或 scheduler 服务重查；web 网关必须携带用户 token。
                # verified_user_id 为事务外已验证结果，事务内不重复认证。
                principal = self._scheduled_in_tx(cursor, request, service_id,
                    user_token=credentials.get('user_token'),
                    target_tenant=credentials.get('target_tenant'), execute=False,
                    task_id=row['client_request_id'].partition(':')[0],
                    verified_user_id=verified_user_id)
            else:
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

    def _scheduled(self, request, service_id, user_token, target_tenant, *, execute=True,
                   verified_user_id=None):
        # RunnerSubmit（提交/重查）携带幂等键 '{task_id}:{occurrence_key}'（设计
        # §5.1）；SessionQuery（list_session，任务视图执行列表）无幂等键——
        # 任务会话 1:1 唯一引用任务行（scheduled_tasks.session_id UNIQUE），
        # 按会话定位。
        client_request_id = getattr(request, "client_request_id", "")
        task_id, _, occurrence_key = client_request_id.partition(":")
        if client_request_id and (not task_id or not occurrence_key):
            # 幂等键缺段即定义错误
            raise RunnerError("SCHEDULED_REQUEST_INVALID", 422)
        with self.connection_factory() as conn:
            return self._scheduled_in_tx(conn.cursor(), request, service_id, user_token=user_token,
                target_tenant=target_tenant, execute=execute, task_id=task_id or None,
                verified_user_id=verified_user_id)

    def _scheduled_in_tx(self, cursor, request, service_id, *, user_token=None, target_tenant=None,
                         execute=True, task_id=None, persisted=None, verified_user_id=None):
        """scheduled 来源三语义鉴权（设计 §5.2）。

        - 提交（scheduler 服务身份，execute=True）：会话归属（任务行按
          session_id 唯一引用）+ 租户/用户 active + profile 订阅授权；
          active+pending_occurrence_key 的复核在提交事务内的接单守卫完成
          （repository.submit_in_tx 持任务行锁），此处不加非锁预检；
        - 幂等重查（服务身份，execute=False）：任务行存在 + 会话/属主匹配
          即可，不要求任务 active（暂停后同键重试必须找回原 runner）；
        - 用户 Bearer token 只读：token 用户必须等于任务属主，仅
          execute=False 放行；提交拒绝 SCHEDULED_SUBMIT_FORBIDDEN。返回行
          属主的 system Principal，使既有 assert_owner 行级所有权自然通过；
        - persisted（worker 执行前）：任务行存在 + 归属与已受理行一致 +
          用户 active；execute=True 时加租户 active 与 profile 校验，同样
          不要求任务 active。
        - verified_user_id：事件读取等"外部认证在事务外"的调用方已验证的
          token 用户——事务内只复核 tokens 权威行，不重复调用认证依赖。
        """
        if task_id is None or persisted is not None:
            # 会话唯一定位：persisted 重校验与 list_session（无幂等键）按
            # session_id 唯一行取任务（scheduled_tasks.session_id UNIQUE）。
            cursor.execute("""SELECT task_id,tenant_id,user_id,status FROM scheduled_tasks
                WHERE session_id=%s""", (request.session.session_id,))
        else:
            cursor.execute("""SELECT task_id,tenant_id,user_id,status FROM scheduled_tasks
                WHERE session_id=%s AND task_id=%s""", (request.session.session_id, task_id))
        task = cursor.fetchone()
        if not task:
            raise RunnerError("SCHEDULED_TASK_NOT_FOUND", 404)
        if persisted is not None and (task["tenant_id"] != persisted["tenant_id"]
                or task["user_id"] != persisted["user_id"]):
            raise RunnerError("SCHEDULED_TASK_NOT_FOUND", 404)
        tenant_id, owner_id = task["tenant_id"], task["user_id"]
        if target_tenant is not None and target_tenant != tenant_id:
            raise RunnerError("TENANT_FORBIDDEN", 403)
        if not user_token and service_id == self.config.web_service_id:
            # web 网关服务身份不得代表任何用户：scheduled 行的服务身份操作仅
            # 限专用调度 peer（scheduler）；网关必须携带用户 Bearer token。
            raise RunnerError("USER_UNAUTHORIZED", 401)
        cursor.execute("SELECT user_id,tenant_id,role,status FROM users WHERE user_id=%s", (owner_id,))
        user = cursor.fetchone()
        if not user or user["status"] != UserStatus.ACTIVE.value:
            raise RunnerError("USER_UNAUTHORIZED", 401)
        # 归属双重核对（提交与重校验同规则）：①任务属主的当前租户必须仍属
        # 任务租户（platform_admin 沿用既有跨租户规则）——用户转租户后不得
        # 继续以旧租户身份执行/计费；②真实会话行必须存在且 tenant/user 与
        # 任务行一致——不依赖历史写入/终态模块事后兜底。
        is_platform = user["role"] == UserRole.PLATFORM_ADMIN.value
        if not is_platform and user["tenant_id"] != tenant_id:
            raise RunnerError("TENANT_FORBIDDEN", 403)
        cursor.execute("""SELECT tenant_id,user_id FROM chat_sessions WHERE session_id=%s""",
                       (request.session.session_id,))
        session = cursor.fetchone()
        # 会话租户与任务租户严格一致（任务 tenant_id NOT NULL，NULL 不作通配）：
        # 受理/执行与终态 write_history 的归属规则保持同一口径，不留下
        # "受理通过、收尾 FINALIZE_SESSION_OWNER_CHANGED"的行
        if not session or session["user_id"] != owner_id or session["tenant_id"] != tenant_id:
            raise RunnerError("SESSION_NOT_FOUND", 404)
        if user_token:
            token_user = verified_user_id or self.token_verifier(user_token, auto_refresh=False)
            if not token_user:
                raise RunnerError("USER_UNAUTHORIZED", 401)
            # 与 _web 相同的双验：tokens 表权威行核对（撤销/缩短过期后的兜底）
            cursor.execute("SELECT user_id,expires_at FROM tokens WHERE token=%s", (user_token,))
            token = cursor.fetchone()
            if not token or token["user_id"] != token_user or self._expired(token["expires_at"]):
                raise RunnerError("USER_UNAUTHORIZED", 401)
            if token_user != owner_id:
                # 非属主 fail-closed：不泄露任务行存在性
                raise RunnerError("RUNNER_NOT_FOUND", 404)
            if execute:
                raise RunnerError("SCHEDULED_SUBMIT_FORBIDDEN", 403)
        if execute:
            self._tenant(cursor, tenant_id)
            self._profile(cursor, request.profile_id, dict(user), tenant_id)
        return Principal(Identity(tenant_id, owner_id, request.session.session_id, "scheduled", "web"),
                         "system", "scheduler", service_id)

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
        if execute and request.source == "chat":
            # 任务专属会话（metadata.scheduled_task 标记）不接收自由聊天：
            # 任务操作走任务接口/任务视图（设计 §5.2）。metadata 为 JSONB，
            # psycopg2 返回 dict 或 None。
            metadata = session.get("metadata")
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except ValueError:
                    metadata = None
            if isinstance(metadata, dict) and metadata.get("scheduled_task"):
                raise RunnerError("TASK_SESSION_CHAT_FORBIDDEN", 400)
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
        # 租户数字员工副本实例（agent_id != base_agent_id）：订阅仍挂基础员工；
        # 实例归属已按 tenant_id+agent_id 限定，archived 行也解析（与会话历史
        # 可用口径一致，新会话在实例 API 层拒绝）。用户级授权按实例 ID 或基础
        # 员工 ID 任一命中放行（授权界面按实例 ID 写入 user_agent_permissions，
        # 历史数据两种形态并存），仅限本次请求的 profile，不引入通配。
        requested_profile_id = profile_id
        cursor.execute("""SELECT base_agent_id FROM tenant_agent_instances
            WHERE tenant_id=%s AND agent_id=%s LIMIT 1""", (tenant_id, profile_id))
        instance_row = cursor.fetchone()
        if instance_row and instance_row["base_agent_id"]:
            profile_id = instance_row["base_agent_id"]
        cursor.execute("""SELECT 1 FROM subscriptions WHERE tenant_id=%s AND subagent_type=%s
            AND status='active' AND starts_at<=CURRENT_TIMESTAMP
            AND (expires_at IS NULL OR expires_at>CURRENT_TIMESTAMP) LIMIT 1""", (tenant_id, profile_id))
        if not cursor.fetchone():
            raise RunnerError("PROFILE_FORBIDDEN", 403)
        if user and user["role"] not in (UserRole.PLATFORM_ADMIN.value, UserRole.TENANT_ADMIN.value):
            cursor.execute("""SELECT 1 FROM user_agent_permissions WHERE user_id=%s
                AND agent_id IN (%s, %s) AND tenant_id IS NOT DISTINCT FROM %s LIMIT 1""",
                (user["user_id"], requested_profile_id, profile_id, tenant_id))
            if not cursor.fetchone():
                raise RunnerError("PROFILE_FORBIDDEN", 403)
