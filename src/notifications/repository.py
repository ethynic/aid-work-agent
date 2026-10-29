"""Short PostgreSQL transactions for notifications. No transport runs under locks."""
import hashlib
import json
import random
import uuid

from loguru import logger
from datetime import timedelta
from urllib.parse import quote, urlsplit

from src.db.database import get_db_connection
from src.db.encryption import encryption_manager

from .authorization import assert_subject_in_tx, resource_in_tx
from .contracts import RULE_EVENT_POLICY_KEY, NotificationError, Subject

TABLE_COLUMNS = {
    'notification_messages': 'notification_id notification_ref schema_version producer event_type event_key scope_key tenant_id recipient_user_id resource_ref template_key template_version parameters_encrypted occurred_at accepted_at expires_at category priority policy_key expansion_state route_snapshot read_at created_at',
    'notification_targets': 'target_id scope_key tenant_id recipient_user_id channel provider installation_ref identity_digest target_schema_version configuration_encrypted rule_id binding_version state enabled_since verified_at created_at updated_at',
    'notification_preferences': 'scope_key tenant_id recipient_user_id category channel provider enabled event_types enabled_since updated_at',
    'notification_deliveries': 'delivery_id notification_id target_id binding_version rendered_encrypted state attempts next_attempt_at owner lease_until claim_version error_class provider_acceptance_id accepted_at delivered_at displayed_at read_at created_at updated_at',
    'notification_rules': 'rule_id tenant_id event_type name enabled is_system conditions recipient_mode include_default_target created_by created_at updated_at',
}


def protected(value):
    return encryption_manager.encrypt(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def unprotect(value):
    return json.loads(encryption_manager.decrypt(value))


# task.* 用户任务事件的默认/兼容事件集合（五轮评审 P1-2）：存量偏好行的
# event_types 为旧默认（仅 completed/failed）时，读取按等效集合兼容扩展
# task.waiting_input；显式自定义集合不受影响，不回写行、不改开关事实。
# Phase 3 追加 task.paused / task.needs_action（定时任务自动暂停与需要处理，
# 设计 §8.1）：旧默认两元与旧 DDL 默认三元（waiting_input 尚无 paused）均
# 兼容扩展到当前默认集合；显式自定义集合（含显式三元自定义——偏好接口未
# 提供 event_types 编辑，三元只能来自旧 DDL 默认）不受影响。
LEGACY_TASK_EVENT_TYPES = ('task.completed', 'task.failed')
LEGACY_DEFAULT_TASK_EVENT_TYPES = ('task.completed', 'task.failed', 'task.waiting_input')
TASK_EVENT_TYPES = ('task.completed', 'task.failed', 'task.waiting_input', 'task.paused',
                    'task.needs_action')


def _effective_event_types(stored):
    stored = tuple(stored or ())
    if set(stored) in (set(LEGACY_TASK_EVENT_TYPES), set(LEGACY_DEFAULT_TASK_EVENT_TYPES)):
        return TASK_EVENT_TYPES
    return stored


def mask_tenant_configuration(channel, configuration):
    """租户渠道配置回显：webhook 属凭证脱敏（保留 scheme://host + 末4位）；
    邮箱非机密信息，完整回显。"""
    if not isinstance(configuration, dict):
        return None
    if channel == 'im_webhook':
        parts = urlsplit(str(configuration.get('webhook_url') or ''))
        if not parts.scheme or not parts.netloc:
            return '****'
        # netloc 剥离 userinfo，避免回显 URL 内嵌凭证
        host = parts.netloc.rsplit('@', 1)[-1]
        tail = ((parts.path or '') + (('?' + parts.query) if parts.query else ''))[-4:]
        return f'{parts.scheme}://{host}/****{tail}'
    if channel == 'email':
        address = str(configuration.get('email_address') or '')
        return address or None
    return None


def public_target(row):
    target = {key:row[key] for key in ('target_id','installation_ref','binding_version','state','enabled_since',
                                       'channel','provider','rule_id')}
    if row.get('configuration_encrypted'):
        try:
            display = mask_tenant_configuration(row['channel'], unprotect(row['configuration_encrypted']))
        except Exception:
            display = None
        if display is not None:
            target['configuration_display'] = display
    return target


def owned(row, subject):
    return bool(row and row['scope_key'] == subject.scope_key and row['recipient_user_id'] == subject.user_id
                and row['tenant_id'] == subject.tenant_id)


def tenant_owned(row, subject):
    return bool(row and row['scope_key'] == subject.scope_key and row['tenant_id'] == subject.tenant_id
                and row['recipient_user_id'] is None)


def tenant_installation_ref(tenant_id):
    # 租户+provider 维度确定性 installation_ref：配合 idx_notification_active_installation
    # 的 (provider, installation_ref) WHERE state='active' 唯一索引，在 DB 层保证同租户同 provider 至多一个 active target。
    return 'tenant_' + hashlib.sha256(('tenant_target:'+tenant_id).encode()).hexdigest()[:40]


def lock_subject(cursor, subject):
    cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                   ('notification:subject:'+subject.scope_key+':'+subject.user_id,))


# 规则引擎预置默认规则（设计 §4）：与 db_update 预置 INSERT 保持同一定义。
# (event_type, name, conditions, recipient_mode)；预置规则 include_default_target=TRUE
# （留资默认规则含租户级邮件渠道，旧邮件单发收编后能力不丢失，设计 §5.2）。
PRESET_RULES = (
    ('billing.credit_low', '积分提醒', {}, 'tenant_admins'),
    ('billing.tenant_expiry', '到期提醒', {}, 'tenant_admins'),
    ('abuse.rule_triggered', '滥用告警', {}, 'tenant_admins'),
    ('lead.captured', '留资通知', {'scope': 'channel'}, 'kf_assignee'),
)

# 规则引擎可配置事件白名单（租户管理员 CRUD 边界；task.* 用户任务事件不进规则层）。
RULE_EVENT_TYPES = frozenset({item[0] for item in PRESET_RULES})

RULE_RECIPIENT_MODES = frozenset({'tenant_admins', 'kf_assignee'})


class NotificationRepository:
    def __init__(self, config, registry, connection_factory=get_db_connection):
        self.config, self.registry, self.connection_factory = config, registry, connection_factory

    def allowed(self, subject):
        return (subject.tenant_id is None or self.config.allowed_tenants is None
                or subject.tenant_id in self.config.allowed_tenants)

    def guard(self, cursor, subject):
        user = assert_subject_in_tx(cursor, subject)
        if not self.allowed(subject):
            raise NotificationError('NOTIFICATION_TENANT_DISABLED', 403)
        return user

    def guard_admin(self, cursor, subject):
        user = assert_subject_in_tx(cursor, subject)
        if user['role'] not in ('tenant_admin', 'platform_admin'):
            raise NotificationError('NOTIFICATION_FORBIDDEN', 403)
        if not self.allowed(subject):
            raise NotificationError('NOTIFICATION_TENANT_DISABLED', 403)

    @staticmethod
    def assert_schema_in_tx(cursor):
        for table, columns in TABLE_COLUMNS.items():
            cursor.execute('SELECT to_regclass(%s) AS name', (table,))
            if not cursor.fetchone()['name']:
                raise RuntimeError('NOTIFICATION_SCHEMA_REQUIRED')
            cursor.execute('SELECT obj_description(%s::regclass) AS version', (table,))
            if cursor.fetchone()['version'] != 'AID_NOTIFICATION_SCHEMA_V1':
                raise RuntimeError('NOTIFICATION_SCHEMA_VERSION_REQUIRED')
            cursor.execute("""SELECT a.attname,t.typname FROM pg_attribute a
                JOIN pg_type t ON t.oid=a.atttypid
                WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped""", (table,))
            actual = {row['attname']:row['typname'] for row in cursor.fetchall()}
            for column in columns.split():
                expected = ('timestamptz' if column.endswith('_at') or column in ('enabled_since','lease_until')
                            else 'jsonb' if column in ('resource_ref','route_snapshot','event_types')
                            else 'int8' if column in ('binding_version','claim_version')
                            else 'int4' if column in ('schema_version','target_schema_version','template_version','priority','attempts')
                            else 'bool' if column == 'enabled' else 'text')
                if actual.get(column) != expected:
                    raise RuntimeError('NOTIFICATION_SCHEMA_COLUMNS_REQUIRED')
        cursor.execute('''SELECT conname FROM pg_constraint WHERE conrelid IN
            ('notification_messages'::regclass,'notification_targets'::regclass,
             'notification_preferences'::regclass,'notification_deliveries'::regclass,
             'notification_rules'::regclass)''')
        names = {row['conname'] for row in cursor.fetchall()}
        if not {'notification_message_identity','notification_target_identity',
                'notification_delivery_target','notification_preferences_pkey',
                'notification_rules_pkey'}.issubset(names):
            raise RuntimeError('NOTIFICATION_SCHEMA_CONSTRAINTS_REQUIRED')

    def assert_schema(self):
        with self.connection_factory() as conn:
            self.assert_schema_in_tx(conn.cursor())

    def targets(self, subject):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard(cursor, subject)
            cursor.execute('''SELECT * FROM notification_targets WHERE scope_key=%s
                AND recipient_user_id=%s ORDER BY created_at DESC''', (subject.scope_key, subject.user_id))
            return [public_target(row) for row in cursor.fetchall()]

    def register_target(self, subject, body):
        adapter = self.registry.get(body.channel, body.provider)
        if not adapter or not adapter.available():
            raise NotificationError('NOTIFICATION_PROVIDER_UNAVAILABLE', 503)
        configuration = body.configuration.model_dump()
        digest = adapter.validate_target(configuration)  # DNS/validation outside DB transaction.
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard(cursor, subject)
            # Serialize both unique identities without lock ordering inversions.
            for identity in sorted((body.installation_ref, digest)):
                cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                               ('notification:target:'+body.provider+':'+identity,))
            cursor.execute('''SELECT * FROM notification_targets WHERE provider=%s AND identity_digest=%s FOR UPDATE''',
                           (body.provider, digest))
            endpoint = cursor.fetchone()
            if endpoint and not owned(endpoint, subject):
                raise NotificationError('NOTIFICATION_TARGET_OWNED', 409)
            cursor.execute('''SELECT * FROM notification_targets WHERE provider=%s AND installation_ref=%s
                ORDER BY (state='active') DESC,updated_at DESC LIMIT 1 FOR UPDATE''', (body.provider, body.installation_ref))
            installation = cursor.fetchone()
            if installation and installation['state'] == 'active' and not owned(installation, subject):
                raise NotificationError('NOTIFICATION_INSTALLATION_OWNED', 409)
            if endpoint and installation and endpoint['target_id'] != installation['target_id']:
                raise NotificationError('NOTIFICATION_BINDING_CONFLICT', 409)
            row = endpoint or (installation if owned(installation, subject) else None)
            if row:
                if (row['installation_ref'] != body.installation_ref
                        or body.expected_binding_version != row['binding_version']):
                    raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
                old = unprotect(row['configuration_encrypted'])
                changed = old != configuration or row['state'] != 'active'
                cursor.execute('''UPDATE notification_targets SET configuration_encrypted=%s,
                    identity_digest=%s,state='active',binding_version=binding_version+%s,
                    enabled_since=CASE WHEN state='active' THEN enabled_since ELSE clock_timestamp() END,
                    verified_at=clock_timestamp(),updated_at=clock_timestamp()
                    WHERE target_id=%s RETURNING *''', (protected(configuration),digest,int(changed),row['target_id']))
            else:
                if body.expected_binding_version is not None:
                    raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
                cursor.execute('''INSERT INTO notification_targets
                    (target_id,scope_key,tenant_id,recipient_user_id,channel,provider,installation_ref,
                     identity_digest,configuration_encrypted) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                    ('target_'+uuid.uuid4().hex,subject.scope_key,subject.tenant_id,subject.user_id,
                     body.channel,body.provider,body.installation_ref,digest,protected(configuration)))
            result = public_target(cursor.fetchone())
            conn.commit()
            return result

    @staticmethod
    def revoke_in_tx(cursor, subject, target_id, version):
        cursor.execute('SELECT * FROM notification_targets WHERE target_id=%s FOR UPDATE', (target_id,))
        row = cursor.fetchone()
        if not owned(row, subject):
            raise NotificationError('NOTIFICATION_TARGET_NOT_FOUND', 404)
        if row['binding_version'] != version:
            # Idempotent original revocation, never a newer incarnation.
            if row['state'] == 'revoked' and row['binding_version'] == version + 1:
                return public_target(row)
            raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
        cursor.execute('''UPDATE notification_targets SET state='revoked',binding_version=binding_version+1,
            updated_at=clock_timestamp() WHERE target_id=%s RETURNING *''', (target_id,))
        result = public_target(cursor.fetchone())
        cursor.execute('''UPDATE notification_deliveries SET state='skipped',error_class='target_revoked',
            owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
            WHERE target_id=%s AND state IN ('pending','sending')''', (target_id,))
        return result

    def revoke_target(self, subject, target_id, version):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            # Revocation remains possible even when tenant/provider is disabled.
            result = self.revoke_in_tx(cursor, subject, target_id, version)
            conn.commit()
            return result

    def tenant_targets(self, subject):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_admin(cursor, subject)
            cursor.execute('''SELECT * FROM notification_targets WHERE scope_key=%s
                AND recipient_user_id IS NULL ORDER BY created_at DESC''', (subject.scope_key,))
            return [public_target(row) for row in cursor.fetchall()]

    def selectable_targets(self, subject):
        """任务通知目标选项（Phase 3，设计 §3.3/§8.3）：普通用户可读——本人
        个人目标（active）+ 本租户租户级目标（active）；只返回展示字段与
        target_id 引用，不含凭据原文（configuration_display 走脱敏回显）。"""
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard(cursor, subject)
            cursor.execute("""SELECT * FROM notification_targets
                WHERE state='active' AND (
                    (scope_key=%s AND recipient_user_id=%s)
                    OR (recipient_user_id IS NULL AND scope_key=%s))
                ORDER BY recipient_user_id NULLS FIRST, created_at DESC""",
                (subject.scope_key, subject.user_id,
                 'tenant:' + subject.tenant_id if subject.tenant_id else ''))
            return [public_target(row) for row in cursor.fetchall()]

    def task_event_deliveries(self, subject, task_id):
        """任务视图投递状态（Phase 3，设计 §8.4）：该任务全部通知事件的
        投递账（消息事件类型/时间 + 各目标 state/attempts/异常），供任务
        视图展示；资源按任务行与其 runner（terminal/needs_action 事件）
        归属当前用户。"""
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard(cursor, subject)
            cursor.execute("""SELECT session_id FROM scheduled_tasks
                WHERE task_id=%s AND user_id=%s
                AND tenant_id IS NOT DISTINCT FROM %s""",
                (task_id, subject.user_id, subject.tenant_id))
            task = cursor.fetchone()
            if not task:
                raise NotificationError('NOTIFICATION_RESOURCE_UNAVAILABLE', 404)
            cursor.execute("""SELECT runner_id FROM agent_runners
                WHERE session_id=%s AND source='scheduled' AND user_id=%s""",
                (task['session_id'], subject.user_id))
            runner_ids = [row['runner_id'] for row in cursor.fetchall()]
            cursor.execute("""SELECT m.notification_id,m.notification_ref,m.event_type,
                    m.event_key,m.created_at,m.expansion_state,d.target_id,d.state,d.attempts,d.error_class,
                    d.accepted_at,d.delivered_at,t.channel,t.provider
                FROM notification_messages m
                LEFT JOIN notification_deliveries d ON d.notification_id=m.notification_id
                LEFT JOIN notification_targets t ON t.target_id=d.target_id
                WHERE m.recipient_user_id=%s AND m.scope_key=%s
                  AND (m.resource_ref->>'id'=%s OR m.resource_ref->>'id' = ANY(%s))
                ORDER BY m.created_at DESC LIMIT 100""",
                (subject.user_id, subject.scope_key, task_id, runner_ids))
            events, by_ref = [], {}
            for row in cursor.fetchall():
                row = dict(row)
                ref = row['notification_ref']
                if ref not in by_ref:
                    # expansion_state（Phase 5 二轮评审第 5 项）：pending=消息尚待
                    # 异步展开（此时无 deliveries 不代表未配置外发），前端据此
                    # 显示"投递准备中"并纳入 30s 刷新；expanded 且无 deliveries
                    # 才是确认无外发目标。
                    entry = {'notification_ref': ref, 'event_type': row['event_type'],
                             'created_at': row['created_at'],
                             'expansion_state': row['expansion_state'], 'deliveries': []}
                    by_ref[ref] = entry
                    events.append(entry)
                if row['target_id']:
                    by_ref[ref]['deliveries'].append(
                        {key: row[key] for key in ('target_id', 'channel', 'provider',
                         'state', 'attempts', 'error_class', 'accepted_at', 'delivered_at')})
            return events

    def register_tenant_target(self, subject, body):
        from .contracts import TENANT_CHANNEL_PROVIDERS
        if body.provider not in TENANT_CHANNEL_PROVIDERS.get(body.channel, frozenset()):
            raise NotificationError('NOTIFICATION_PROVIDER_UNAVAILABLE', 503)
        # 租户级 target 必须归属具体租户；platform_admin 需经 X-Tenant-Id 代管上下文注册，
        # 无租户上下文的 global "租户级" target 无事件归属，永不外发。
        if subject.tenant_id is None:
            raise NotificationError('NOTIFICATION_TENANT_REQUIRED', 400)
        adapter = self.registry.get(body.channel, body.provider)
        if not adapter or not adapter.available():
            raise NotificationError('NOTIFICATION_PROVIDER_UNAVAILABLE', 503)
        configuration = body.configuration.model_dump()
        digest = adapter.validate_target(configuration)  # DNS/validation outside DB transaction.
        installation_ref = tenant_installation_ref(subject.tenant_id)
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_admin(cursor, subject)
            # Serialize both unique identities without lock ordering inversions.
            for identity in sorted((installation_ref, digest)):
                cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                               ('notification:target:'+body.provider+':'+identity,))
            # 同 scope 内 digest 唯一即可；不同租户允许共用同一群机器人 Webhook
            # （平台管理员搭建一个群接收多租户告警）。
            cursor.execute('''SELECT * FROM notification_targets WHERE provider=%s AND identity_digest=%s
                AND scope_key=%s FOR UPDATE''', (body.provider, digest, subject.scope_key))
            endpoint = cursor.fetchone()
            if endpoint and not tenant_owned(endpoint, subject):
                raise NotificationError('NOTIFICATION_TARGET_OWNED', 409)
            # 规则级 active 行占用同 digest 时拒绝（提示换地址或先撤销），
            # 不允许租户级登记悄悄接管规则的投递目标
            if endpoint and endpoint.get('rule_id') and endpoint['state'] == 'active':
                raise NotificationError('NOTIFICATION_TARGET_OWNED', 409)
            cursor.execute('''SELECT * FROM notification_targets WHERE provider=%s AND installation_ref=%s
                AND state='active' FOR UPDATE''', (body.provider, installation_ref))
            installation = cursor.fetchone()
            if installation and endpoint and installation['target_id'] != endpoint['target_id']:
                raise NotificationError('NOTIFICATION_BINDING_CONFLICT', 409)
            # 复活已撤销的同配置行，否则改绑当前 active 行；都无则新建。
            # 优先 active 行：同 installation_ref 的 active 行存在时改绑它，避免
            # idx_notification_active_installation 唯一索引冲突。
            row = installation or endpoint
            if row:
                active = row['state'] == 'active'
                if body.expected_binding_version is not None and body.expected_binding_version != row['binding_version']:
                    raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
                if active and body.expected_binding_version is None:
                    raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
                old = unprotect(row['configuration_encrypted'])
                changed = old != configuration or not active
                # rule_id 一并清空：曾是规则级的行复活为租户级默认渠道时，
                # 残留 rule_id 会被 delete_rule 级联撤销误伤
                cursor.execute('''UPDATE notification_targets SET configuration_encrypted=%s,
                    identity_digest=%s,state='active',rule_id=NULL,binding_version=binding_version+%s,
                    enabled_since=CASE WHEN state='active' THEN enabled_since ELSE clock_timestamp() END,
                    verified_at=clock_timestamp(),updated_at=clock_timestamp()
                    WHERE target_id=%s RETURNING *''', (protected(configuration),digest,int(changed),row['target_id']))
            else:
                if body.expected_binding_version is not None:
                    raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
                cursor.execute('''INSERT INTO notification_targets
                    (target_id,scope_key,tenant_id,recipient_user_id,channel,provider,installation_ref,
                     identity_digest,configuration_encrypted) VALUES (%s,%s,%s,NULL,%s,%s,%s,%s,%s) RETURNING *''',
                    ('target_'+uuid.uuid4().hex,subject.scope_key,subject.tenant_id,
                     body.channel,body.provider,installation_ref,digest,protected(configuration)))
            result = public_target(cursor.fetchone())
            conn.commit()
            return result

    def revoke_tenant_target(self, subject, target_id, version):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_admin(cursor, subject)
            cursor.execute('SELECT * FROM notification_targets WHERE target_id=%s FOR UPDATE', (target_id,))
            row = cursor.fetchone()
            if not tenant_owned(row, subject):
                raise NotificationError('NOTIFICATION_TARGET_NOT_FOUND', 404)
            if row['binding_version'] != version:
                if row['state'] == 'revoked' and row['binding_version'] == version + 1:
                    conn.commit()
                    return public_target(row)
                raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
            cursor.execute('''UPDATE notification_targets SET state='revoked',binding_version=binding_version+1,
                updated_at=clock_timestamp() WHERE target_id=%s RETURNING *''', (target_id,))
            result = public_target(cursor.fetchone())
            cursor.execute('''UPDATE notification_deliveries SET state='skipped',error_class='target_revoked',
                owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                WHERE target_id=%s AND state IN ('pending','sending')''', (target_id,))
            conn.commit()
            return result

    # ---------- 通知规则（规则引擎，设计 §3/§6/§10） ----------

    @staticmethod
    def _validate_rule_conditions(event_type, conditions):
        """规则条件白名单校验，返回规范化 conditions。

        lead.captured 支持 channel（全渠道）与 kf_config（指定客服账号）两层；
        其余事件无条件（{}）。未知键一律拒绝，防止前端冗余字段静默失效。"""
        if not isinstance(conditions, dict):
            raise NotificationError('NOTIFICATION_RULE_CONDITIONS_INVALID', 400)
        if event_type != 'lead.captured':
            if conditions not in ({}, None):
                raise NotificationError('NOTIFICATION_RULE_CONDITIONS_INVALID', 400)
            return {}
        scope = conditions.get('scope', 'channel')
        if scope == 'channel':
            if set(conditions) - {'scope'}:
                raise NotificationError('NOTIFICATION_RULE_CONDITIONS_INVALID', 400)
            return {'scope': 'channel'}
        if scope != 'kf_config':
            raise NotificationError('NOTIFICATION_RULE_CONDITIONS_INVALID', 400)
        if set(conditions) - {'scope', 'kf_config_ids'}:
            raise NotificationError('NOTIFICATION_RULE_CONDITIONS_INVALID', 400)
        ids = conditions.get('kf_config_ids')
        if (not isinstance(ids, list) or not ids or len(ids) > 100
                or not all(isinstance(item, str) and 1 <= len(item) <= 64 for item in ids)):
            raise NotificationError('NOTIFICATION_RULE_CONDITIONS_INVALID', 400)
        return {'scope': 'kf_config', 'kf_config_ids': list(dict.fromkeys(ids))}

    def _validate_rule_kf_configs(self, cursor, tenant_id, conditions):
        """kf_config 层条件逐个校验客服账号归属本租户（tenant_channel_configs）。"""
        for config_id in conditions.get('kf_config_ids', ()):
            cursor.execute('SELECT 1 FROM tenant_channel_configs WHERE config_id=%s AND tenant_id=%s',
                           (config_id, tenant_id))
            if not cursor.fetchone():
                raise NotificationError('NOTIFICATION_KF_CONFIG_INVALID', 400)

    def _admin_rule(self, cursor, subject, rule_id, for_update=False):
        cursor.execute('SELECT * FROM notification_rules WHERE rule_id=%s' +
                       (' FOR UPDATE' if for_update else ''), (rule_id,))
        rule = cursor.fetchone()
        if not rule or rule['tenant_id'] != subject.tenant_id:
            raise NotificationError('NOTIFICATION_RULE_NOT_FOUND', 404)
        return rule

    def list_rules(self, subject):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_admin(cursor, subject)
            if subject.tenant_id is None:
                raise NotificationError('NOTIFICATION_TENANT_REQUIRED', 400)
            cursor.execute('''SELECT r.*,
                    (SELECT COUNT(*) FROM notification_targets t
                     WHERE t.rule_id=r.rule_id AND t.state='active') AS active_targets
                FROM notification_rules r WHERE r.tenant_id=%s
                ORDER BY r.created_at''', (subject.tenant_id,))
            rows = cursor.fetchall()
            conn.commit()
            return [self._public_rule(row) for row in rows]

    @staticmethod
    def _public_rule(row):
        return {'rule_id': row['rule_id'], 'event_type': row['event_type'], 'name': row['name'],
                'enabled': row['enabled'], 'is_system': row['is_system'],
                'conditions': row['conditions'] if isinstance(row['conditions'], dict) else {},
                'recipient_mode': row['recipient_mode'],
                'include_default_target': row['include_default_target'],
                'created_by': row['created_by'], 'created_at': row['created_at'],
                'updated_at': row['updated_at'],
                'active_targets': row.get('active_targets', 0)}

    def create_rule(self, subject, body):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_admin(cursor, subject)
            if subject.tenant_id is None:
                raise NotificationError('NOTIFICATION_TENANT_REQUIRED', 400)
            if body.event_type not in RULE_EVENT_TYPES:
                raise NotificationError('NOTIFICATION_RULE_EVENT_UNSUPPORTED', 400)
            name = (body.name or '').strip()
            if not 1 <= len(name) <= 64:
                raise NotificationError('NOTIFICATION_RULE_NAME_INVALID', 400)
            if body.recipient_mode not in RULE_RECIPIENT_MODES:
                raise NotificationError('NOTIFICATION_RULE_RECIPIENT_INVALID', 400)
            conditions = self._validate_rule_conditions(body.event_type, body.conditions or {})
            if conditions.get('scope') == 'kf_config':
                self._validate_rule_kf_configs(cursor, subject.tenant_id, conditions)
            cursor.execute('''INSERT INTO notification_rules
                (rule_id,tenant_id,event_type,name,enabled,is_system,conditions,recipient_mode,
                 include_default_target,created_by) VALUES (%s,%s,%s,%s,%s,FALSE,%s::jsonb,%s,%s,%s)
                ON CONFLICT (tenant_id,event_type,name) DO NOTHING RETURNING *''',
                ('rule_'+uuid.uuid4().hex,subject.tenant_id,body.event_type,name,
                 bool(body.enabled),json.dumps(conditions),body.recipient_mode,
                 bool(body.include_default_target),subject.user_id))
            row = cursor.fetchone()
            if not row:
                raise NotificationError('NOTIFICATION_RULE_EXISTS', 409)
            result = self._public_rule(row)
            conn.commit()
            return result

    def update_rule(self, subject, rule_id, body):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_admin(cursor, subject)
            if subject.tenant_id is None:
                raise NotificationError('NOTIFICATION_TENANT_REQUIRED', 400)
            rule = self._admin_rule(cursor, subject, rule_id, for_update=True)
            updates, params = [], []
            if body.name is not None:
                if rule['is_system']:
                    # 预置规则名称承载前端识别语义，不允许改名
                    raise NotificationError('NOTIFICATION_RULE_SYSTEM', 403)
                name = body.name.strip()
                if not 1 <= len(name) <= 64:
                    raise NotificationError('NOTIFICATION_RULE_NAME_INVALID', 400)
                cursor.execute(
                    'SELECT 1 FROM notification_rules WHERE tenant_id=%s AND event_type=%s'
                    ' AND name=%s AND rule_id<>%s',
                    (subject.tenant_id, rule['event_type'], name, rule_id))
                if cursor.fetchone():
                    raise NotificationError('NOTIFICATION_RULE_EXISTS', 409)
                updates.append('name=%s')
                params.append(name)
            if body.enabled is not None:
                updates.append('enabled=%s')
                params.append(bool(body.enabled))
            if body.conditions is not None:
                conditions = self._validate_rule_conditions(rule['event_type'], body.conditions)
                if conditions.get('scope') == 'kf_config':
                    self._validate_rule_kf_configs(cursor, subject.tenant_id, conditions)
                updates.append('conditions=%s::jsonb')
                params.append(json.dumps(conditions))
            if body.recipient_mode is not None:
                if body.recipient_mode not in RULE_RECIPIENT_MODES:
                    raise NotificationError('NOTIFICATION_RULE_RECIPIENT_INVALID', 400)
                updates.append('recipient_mode=%s')
                params.append(body.recipient_mode)
            if body.include_default_target is not None:
                updates.append('include_default_target=%s')
                params.append(bool(body.include_default_target))
            if not updates:
                raise NotificationError('NOTIFICATION_RULE_NOTHING_TO_UPDATE', 400)
            updates.append('updated_at=clock_timestamp()')
            params.append(rule_id)
            cursor.execute('UPDATE notification_rules SET '+','.join(updates)+
                           ' WHERE rule_id=%s RETURNING *', tuple(params))
            result = self._public_rule(cursor.fetchone())
            conn.commit()
            return result

    def delete_rule(self, subject, rule_id):
        """删除自定义规则；is_system 拒绝。级联撤销规则级目标并跳过在途投递
        （与 revoke_tenant_target 同款处理，设计 §6 决策 6）。"""
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_admin(cursor, subject)
            if subject.tenant_id is None:
                raise NotificationError('NOTIFICATION_TENANT_REQUIRED', 400)
            rule = self._admin_rule(cursor, subject, rule_id, for_update=True)
            if rule['is_system']:
                raise NotificationError('NOTIFICATION_RULE_SYSTEM', 403)
            cursor.execute('DELETE FROM notification_rules WHERE rule_id=%s', (rule_id,))
            cursor.execute('''SELECT target_id FROM notification_targets
                WHERE rule_id=%s AND state='active' FOR UPDATE''', (rule_id,))
            target_ids = [row['target_id'] for row in cursor.fetchall()]
            if target_ids:
                cursor.execute('''UPDATE notification_targets SET state='revoked',
                    binding_version=binding_version+1,updated_at=clock_timestamp()
                    WHERE target_id = ANY(%s)''', (target_ids,))
                cursor.execute('''UPDATE notification_deliveries SET state='skipped',
                    error_class='target_revoked',owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                    WHERE target_id = ANY(%s) AND state IN ('pending','sending')''', (target_ids,))
            conn.commit()
            return {'rule_id': rule_id, 'revoked_targets': len(target_ids)}

    def register_rule_target(self, subject, body):
        """规则级外发目标登记（设计 §10）：installation_ref 服务端生成
        （允许同 provider 多个 active）；同 digest 已被租户级或其他规则的
        active 目标占用时 409。"""
        from .contracts import TENANT_CHANNEL_PROVIDERS
        if body.provider not in TENANT_CHANNEL_PROVIDERS.get(body.channel, frozenset()):
            raise NotificationError('NOTIFICATION_PROVIDER_UNAVAILABLE', 503)
        if subject.tenant_id is None:
            raise NotificationError('NOTIFICATION_TENANT_REQUIRED', 400)
        adapter = self.registry.get(body.channel, body.provider)
        if not adapter or not adapter.available():
            raise NotificationError('NOTIFICATION_PROVIDER_UNAVAILABLE', 503)
        configuration = body.configuration.model_dump()
        digest = adapter.validate_target(configuration)
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_admin(cursor, subject)
            cursor.execute('SELECT * FROM notification_rules WHERE rule_id=%s FOR UPDATE', (body.rule_id,))
            rule = cursor.fetchone()
            if not rule or rule['tenant_id'] != subject.tenant_id:
                raise NotificationError('NOTIFICATION_RULE_NOT_FOUND', 404)
            cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                           ('notification:target:'+body.provider+':'+digest,))
            # 同 digest 优先 active 行；非 active（撤销态）允许复活到本规则
            cursor.execute('''SELECT * FROM notification_targets WHERE provider=%s AND identity_digest=%s
                AND scope_key=%s ORDER BY CASE WHEN state='active' THEN 0 ELSE 1 END,
                updated_at DESC FOR UPDATE''', (body.provider, digest, subject.scope_key))
            endpoint = cursor.fetchone()
            if endpoint and endpoint['state'] == 'active' and endpoint['rule_id'] != body.rule_id:
                raise NotificationError('NOTIFICATION_TARGET_OWNED', 409)
            row = endpoint
            if row:
                active = row['state'] == 'active'
                if body.expected_binding_version is not None and body.expected_binding_version != row['binding_version']:
                    raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
                if active and body.expected_binding_version is None:
                    raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
                old = unprotect(row['configuration_encrypted'])
                changed = old != configuration or not active
                cursor.execute('''UPDATE notification_targets SET configuration_encrypted=%s,
                    identity_digest=%s,rule_id=%s,state='active',binding_version=binding_version+%s,
                    enabled_since=CASE WHEN state='active' THEN enabled_since ELSE clock_timestamp() END,
                    verified_at=clock_timestamp(),updated_at=clock_timestamp()
                    WHERE target_id=%s RETURNING *''',
                    (protected(configuration),digest,body.rule_id,int(changed),row['target_id']))
            else:
                if body.expected_binding_version is not None:
                    raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
                cursor.execute('''INSERT INTO notification_targets
                    (target_id,scope_key,tenant_id,recipient_user_id,channel,provider,installation_ref,
                     identity_digest,configuration_encrypted,rule_id)
                    VALUES (%s,%s,%s,NULL,%s,%s,%s,%s,%s,%s) RETURNING *''',
                    ('target_'+uuid.uuid4().hex,subject.scope_key,subject.tenant_id,
                     body.channel,body.provider,'rule_'+body.rule_id+'_'+uuid.uuid4().hex[:8],
                     digest,protected(configuration),body.rule_id))
            result = public_target(cursor.fetchone())
            conn.commit()
            return result

    def tenant_test(self, subject, body):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_admin(cursor, subject)
            cursor.execute('SELECT * FROM notification_targets WHERE target_id=%s', (body.target_id,))
            target = cursor.fetchone()
            if (not tenant_owned(target, subject) or target['state'] != 'active'
                    or target['binding_version'] != body.binding_version):
                raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
            adapter = self.registry.get(target['channel'],target['provider'])
            if not adapter or not adapter.available():
                raise NotificationError('NOTIFICATION_PROVIDER_UNAVAILABLE', 503)
            lock_subject(cursor, subject)
            cursor.execute('''SELECT 1 FROM notification_messages WHERE scope_key=%s AND recipient_user_id=%s
                AND event_type='notification.test' AND created_at>clock_timestamp()-interval '1 minute' LIMIT 1''',
                (subject.scope_key,subject.user_id))
            if cursor.fetchone():
                raise NotificationError('NOTIFICATION_TEST_RATE_LIMITED', 429)
            from .events import publish_in_tx
            cursor.execute('SELECT clock_timestamp() AS now')
            now = cursor.fetchone()['now']
            ref = publish_in_tx(cursor,subject=subject,producer='notification_api',event_type='notification.test',
                event_key='test:'+uuid.uuid4().hex,
                resource_ref={'type':'notification_test','id':target['target_id'],'binding_version':body.binding_version},
                parameters={'session_title':'浏览器通知测试','event_kind':'test'},
                occurred_at=now,accepted_at=now,expires_at=now+timedelta(minutes=5),
                policy_key='explicit_test_target')
            conn.commit()
            return ref

    def preferences(self, subject):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard(cursor, subject)
            cursor.execute('''SELECT category,channel,provider,enabled,enabled_since FROM notification_preferences
                WHERE scope_key=%s AND recipient_user_id=%s''', (subject.scope_key,subject.user_id))
            return [dict(row) for row in cursor.fetchall()]

    def set_preference(self, subject, body):
        adapter = self.registry.get(body.channel, body.provider)
        if body.enabled and (not adapter or not adapter.available()):
            raise NotificationError('NOTIFICATION_PROVIDER_UNAVAILABLE', 503)
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard(cursor, subject)
            lock_subject(cursor, subject)
            cursor.execute('''INSERT INTO notification_preferences
                (scope_key,tenant_id,recipient_user_id,category,channel,provider,enabled,enabled_since)
                VALUES (%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN clock_timestamp() END)
                ON CONFLICT (scope_key,recipient_user_id,category,channel,provider) DO UPDATE SET
                    enabled=EXCLUDED.enabled,updated_at=clock_timestamp(),
                    enabled_since=CASE WHEN EXCLUDED.enabled AND NOT notification_preferences.enabled
                        THEN clock_timestamp() ELSE notification_preferences.enabled_since END
                RETURNING category,channel,provider,enabled,enabled_since''',
                (subject.scope_key,subject.tenant_id,subject.user_id,body.category,body.channel,body.provider,
                 body.enabled,body.enabled))
            result = dict(cursor.fetchone())
            if not body.enabled:
                cursor.execute('''UPDATE notification_deliveries d SET state='skipped',error_class='preference_revoked',
                    owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                    FROM notification_messages m,notification_targets t
                    WHERE d.notification_id=m.notification_id AND d.target_id=t.target_id
                    AND m.scope_key=%s AND m.recipient_user_id=%s AND m.category=%s
                    AND t.channel=%s AND t.provider=%s AND d.state IN ('pending','sending')''',
                    (subject.scope_key,subject.user_id,body.category,body.channel,body.provider))
            conn.commit()
            return result

    def test(self, subject, body):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard(cursor, subject)
            cursor.execute('SELECT * FROM notification_targets WHERE target_id=%s', (body.target_id,))
            target = cursor.fetchone()
            if not owned(target, subject) or target['state'] != 'active' or target['binding_version'] != body.binding_version:
                raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
            adapter = self.registry.get(target['channel'],target['provider'])
            if not adapter or not adapter.available():
                raise NotificationError('NOTIFICATION_PROVIDER_UNAVAILABLE', 503)
            lock_subject(cursor, subject)
            cursor.execute('''SELECT 1 FROM notification_messages WHERE scope_key=%s AND recipient_user_id=%s
                AND event_type='notification.test' AND created_at>clock_timestamp()-interval '1 minute' LIMIT 1''',
                (subject.scope_key,subject.user_id))
            if cursor.fetchone():
                raise NotificationError('NOTIFICATION_TEST_RATE_LIMITED', 429)
            from .events import publish_in_tx
            cursor.execute('SELECT clock_timestamp() AS now')
            now = cursor.fetchone()['now']
            ref = publish_in_tx(cursor,subject=subject,producer='notification_api',event_type='notification.test',
                event_key='test:'+uuid.uuid4().hex,
                resource_ref={'type':'notification_test','id':target['target_id'],'binding_version':body.binding_version},
                parameters={'session_title':'浏览器通知测试','event_kind':'test'},
                occurred_at=now,accepted_at=now,expires_at=now+timedelta(minutes=5),
                policy_key='explicit_test_target')
            conn.commit()
            return ref

    def resolve(self, subject, ref):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            user = self.guard(cursor, subject)
            if user['role'] == 'platform_admin':
                # 与 inbox 同口径：portal 看全部 global，代管租户时 about_tenant 隔离
                scope_sql, scope_params = self._platform_scope_clause(user, subject)
                cursor.execute(f'''SELECT * FROM notification_messages WHERE notification_ref=%s
                    AND recipient_user_id=%s AND {scope_sql}''',
                    (ref,subject.user_id,*scope_params))
            else:
                cursor.execute('''SELECT * FROM notification_messages WHERE notification_ref=%s
                    AND scope_key=%s AND recipient_user_id=%s AND tenant_id IS NOT DISTINCT FROM %s''',
                    (ref,subject.scope_key,subject.user_id,subject.tenant_id))
            message = cursor.fetchone()
            if not message:
                raise NotificationError('NOTIFICATION_NOT_FOUND', 404)
            resource = resource_in_tx(cursor, message)
            return {'notification_ref':ref,'event_type':message['event_type'],'resource':resource}

    INBOX_EVENT_TYPES = ('billing.credit_low','billing.tenant_expiry','abuse.rule_triggered',
                         'task.waiting_input','task.paused','task.needs_action','lead.captured')

    @staticmethod
    def _inbox_link(event_type, params, subject, notification_ref=None):
        """点击站内信的跳转地址；abuse 告警跳防滥用中心并定位到该用户明细；
        提问等待通知复用既有通知解析入口 /notification?ref=——入口完成认证与
        resource 解析后带 _load_session/数字员工路由回原会话（五轮评审 P2-4：
        不自行拼 chat 路径，避免遗漏非默认数字员工路由）。"""
        if event_type in ('task.waiting_input', 'task.paused', 'task.needs_action'):
            # 提问等待与定时任务暂停通知复用既有通知解析入口 /notification?ref=
            # ——入口完成认证与 resource 解析（task.paused 解析到任务行，
            # Phase 5 前端据此路由任务视图）；入口（NotificationEntry）以
            # tenant 查询参数选择 saas_token_{tenant}：租户站内信必须携带
            # tenant，否则租户用户落在 portal 登录页（六轮评审 P2-1）；
            # tenant 为空（portal 用户）时不带参数。
            ref = f'/notification?ref={notification_ref}' if notification_ref else None
            if not ref:
                return None
            return (f'{ref}&tenant={quote(str(subject.tenant_id), safe="")}'
                    if subject.tenant_id else ref)
        if event_type != 'abuse.rule_triggered':
            return None
        tenant_id = params.get('tenant_id') or subject.tenant_id
        user_id = params.get('user_id')
        if not tenant_id or not user_id:
            return None
        window = params.get('period') if params.get('period') in ('hour', 'day') else 'day'
        return (f"/t/{quote(str(tenant_id), safe='')}/abuse-center"
                f"?focus_user={quote(str(user_id), safe='')}&window={window}")

    @staticmethod
    def _event_tenant(message):
        """租户级事件副本的归属租户：resource_ref.about_tenant（平台兜底副本打标）
        或 parameters.tenant_id（abuse 参数模型自带）。取不到返回 None。"""
        tenant = (message.get('resource_ref') or {}).get('about_tenant')
        if tenant:
            return tenant
        try:
            params = unprotect(message.get('parameters_encrypted'))
        except Exception:
            return None
        return params.get('tenant_id') if isinstance(params, dict) else None

    @staticmethod
    def _platform_scope_clause(user, subject):
        """平台管理员 global 消息可见范围：portal（无租户上下文）看全部 global 副本；
        代管租户时按 resource_ref.about_tenant 隔离，仅本租户 scope + 本租户相关 global
        副本，防止跨租户告警互见（about_tenant 由生产者对无租户 scope 的副本打标）。"""
        if user['role'] != 'platform_admin':
            return 'scope_key=%s', (subject.scope_key,)
        if subject.tenant_id is None:
            return "scope_key='global'", ()
        return ("(scope_key=%s OR (scope_key='global' AND resource_ref->>'about_tenant'=%s))",
                (subject.scope_key, subject.tenant_id))

    def inbox(self, subject, unread_only=False, limit=20):
        from .events import render_inbox_text
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            user = self.guard(cursor, subject)
            scope_sql, scope_params = self._platform_scope_clause(user, subject)
            cursor.execute(f'''SELECT count(*) AS unread FROM notification_messages
                WHERE {scope_sql} AND recipient_user_id=%s AND event_type=ANY(%s) AND read_at IS NULL''',
                (*scope_params,subject.user_id,list(self.INBOX_EVENT_TYPES)))
            unread = cursor.fetchone()['unread']
            sql = f'''SELECT notification_ref,event_type,category,priority,read_at,created_at,
                parameters_encrypted FROM notification_messages
                WHERE {scope_sql} AND recipient_user_id=%s AND event_type=ANY(%s)'''
            if unread_only:
                sql += ' AND read_at IS NULL'
            sql += ' ORDER BY created_at DESC LIMIT %s'
            cursor.execute(sql,(*scope_params,subject.user_id,list(self.INBOX_EVENT_TYPES),limit))
            items = []
            for row in cursor.fetchall():
                try:
                    params = unprotect(row['parameters_encrypted'])
                    title,body = render_inbox_text(row['event_type'],params)
                except Exception:
                    params = {}
                    title,body = row['event_type'],''
                items.append({'notification_ref':row['notification_ref'],'event_type':row['event_type'],
                    'category':row['category'],'priority':row['priority'],'title':title,'body':body,
                    'link':self._inbox_link(row['event_type'],params,subject,
                                            notification_ref=row['notification_ref']),
                    'read_at':row['read_at'],'created_at':row['created_at']})
            return {'unread_count':unread,'items':items}

    def mark_inbox_read(self, subject, ref):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            user = self.guard(cursor, subject)
            scope_sql, scope_params = self._platform_scope_clause(user, subject)
            cursor.execute(f'''SELECT notification_ref FROM notification_messages
                WHERE notification_ref=%s AND {scope_sql} AND recipient_user_id=%s''',
                (ref,*scope_params,subject.user_id))
            if not cursor.fetchone():
                raise NotificationError('NOTIFICATION_NOT_FOUND', 404)
            cursor.execute('''UPDATE notification_messages SET read_at=clock_timestamp()
                WHERE notification_ref=%s AND read_at IS NULL''', (ref,))
            conn.commit()
            return {'notification_ref':ref,'success':True}

    @staticmethod
    def _ensure_preset_rules_in_tx(cursor, tenant_id, event_type):
        """预置规则惰性补插（设计 §4）：租户首次产生对应事件时按预置清单补齐，
        幂等。该 (tenant, event_type) 已存在任意规则行（含停用）则不插——停用
        事实不被覆盖。"""
        cursor.execute('SELECT 1 FROM notification_rules WHERE tenant_id=%s AND event_type=%s LIMIT 1',
                       (tenant_id, event_type))
        if cursor.fetchone():
            return
        for event, name, conditions, mode in PRESET_RULES:
            if event != event_type:
                continue
            cursor.execute('''INSERT INTO notification_rules
                (rule_id,tenant_id,event_type,name,is_system,conditions,recipient_mode,include_default_target)
                VALUES (%s,%s,%s,%s,TRUE,%s::jsonb,%s,TRUE)
                ON CONFLICT (tenant_id,event_type,name) DO NOTHING''',
                ('rule_preset_' + hashlib.md5((tenant_id + ':' + event).encode()).hexdigest(),
                 tenant_id, event, name, json.dumps(conditions), mode))

    def _tenant_rule_enabled(self, cursor, tenant_id, event_type):
        self._ensure_preset_rules_in_tx(cursor, tenant_id, event_type)
        cursor.execute('''SELECT 1 FROM notification_rules
            WHERE tenant_id=%s AND event_type=%s AND enabled LIMIT 1''', (tenant_id, event_type))
        return bool(cursor.fetchone())

    @staticmethod
    def _resolve_rule_recipients(cursor, recipient_mode, tenant_id, params):
        """按 recipient_mode 解析接收人（设计 §6）：tenant_admins=租户全部管理员；
        kf_assignee=事件参数归属员工（必须归属本租户且 active，防参数伪造跨租户通知）。"""
        if recipient_mode == 'kf_assignee':
            user_id = str(params.get('assignee_user_id') or '')
            if not user_id:
                return []
            cursor.execute('''SELECT user_id FROM users
                WHERE user_id=%s AND tenant_id=%s AND status='active' ''', (user_id, tenant_id))
            row = cursor.fetchone()
            return [row['user_id']] if row else []
        cursor.execute('''SELECT user_id FROM users
            WHERE tenant_id=%s AND role='tenant_admin' AND status='active' ''', (tenant_id,))
        return [row['user_id'] for row in cursor.fetchall()]

    @staticmethod
    def _rule_event_targets(cursor, rule, tenant_id):
        """规则命中后的外发目标：规则专属 target（同 provider 可多个）+ 可选
        叠加租户级默认渠道 target（include_default_target）。"""
        default_clause = ' OR rule_id IS NULL' if rule['include_default_target'] else ''
        cursor.execute('''SELECT * FROM notification_targets
            WHERE state='active' AND recipient_user_id IS NULL AND tenant_id=%s
            AND (rule_id=%s''' + default_clause + ')', (tenant_id, rule['rule_id']))
        return cursor.fetchall()

    def _expand_rule_event(self, cursor, message):
        """事件级消息展开（规则引擎，设计 §6）：匹配租户通知规则，每条命中规则
        独立展开接收人（站内信消息）与外发目标（delivery），两层叠加都通知不去重；
        无命中规则零投递、消息不滞留 pending。"""
        route = []
        if message['expires_at'] <= message['database_now']:
            return 'expired', route
        tenant_id = message['tenant_id']
        if not tenant_id:
            # 事件级消息必然归属租户，异常形态按展开完成处理避免反复重扫
            return 'expanded', route
        try:
            message['parameters'] = unprotect(message['parameters_encrypted'])
        except Exception:
            message['parameters'] = {}
        params = message['parameters'] if isinstance(message['parameters'], dict) else {}
        self._ensure_preset_rules_in_tx(cursor, tenant_id, message['event_type'])
        cursor.execute('''SELECT rule_id,name,conditions,recipient_mode,include_default_target
            FROM notification_rules WHERE tenant_id=%s AND event_type=%s AND enabled
            ORDER BY created_at,rule_id''', (tenant_id, message['event_type']))
        rules = cursor.fetchall()
        seen_targets = set()
        for rule in rules:
            conditions = rule['conditions'] if isinstance(rule['conditions'], dict) else {}
            if conditions.get('scope') == 'kf_config':
                # 客服账号层规则：事件 kf_config_id 命中账号列表才展开；渠道层
                # （scope=channel）与无条件规则全量命中
                if params.get('kf_config_id') not in (conditions.get('kf_config_ids') or []):
                    continue
            recipients = self._resolve_rule_recipients(cursor, rule['recipient_mode'], tenant_id, params)
            recipient_ids = []
            for user_id in recipients:
                # 站内信消息直插 expanded（message 即信）：event_key 追加规则维度，
                # 多规则命中同一接收人各留一条（"都通知"口径），唯一约束保证幂等
                cursor.execute('''INSERT INTO notification_messages
                    (notification_id,notification_ref,schema_version,producer,event_type,event_key,
                     scope_key,tenant_id,recipient_user_id,resource_ref,template_key,template_version,
                     parameters_encrypted,occurred_at,accepted_at,expires_at,category,priority,
                     policy_key,expansion_state,route_snapshot)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,1,%s,%s,%s,%s,%s,%s,%s,'expanded','[]'::jsonb)
                    ON CONFLICT(scope_key,recipient_user_id,producer,event_key) DO NOTHING
                    RETURNING notification_ref''',
                    ('notification_'+uuid.uuid4().hex, uuid.uuid4().hex, message['schema_version'],
                     message['producer'], message['event_type'],
                     message['event_key'] + ':' + rule['rule_id'], 'tenant:'+tenant_id, tenant_id,
                     user_id, json.dumps(message['resource_ref']), message['template_key'],
                     message['parameters_encrypted'], message['occurred_at'], message['accepted_at'],
                     message['expires_at'], message['category'], message['priority'], 'rule_recipient'))
                if cursor.fetchone():
                    recipient_ids.append(user_id)
            target_rows = []
            for target in self._rule_event_targets(cursor, rule, tenant_id):
                if target['target_id'] in seen_targets:
                    # 跨规则叠加的租户级默认 target 只投一次（怕丢线索不怕重复
                    # 指多规则各自业务投递；同一 webhook 重复轰炸无业务意义）
                    continue
                adapter = self.registry.get(target['channel'],target['provider'])
                if not adapter or not adapter.available() or message['schema_version'] not in adapter.capabilities.notification_schema_versions:
                    continue
                delivery_state,error = 'pending',None
                try:
                    payload = adapter.render(message, target)
                    if len(json.dumps(payload,ensure_ascii=False).encode())>adapter.capabilities.payload_limit:
                        raise ValueError('NOTIFICATION_PAYLOAD_TOO_LARGE')
                except Exception as e:
                    # 不打异常体：错误文本可能携带消息参数（含联系方式明文）
                    logger.warning('后端日志：通知渲染失败 notification_id={} target_id={} 异常类型={}',
                                   message['notification_id'], target['target_id'], type(e).__name__)
                    payload,delivery_state,error = {},'failed','render_failure'
                cursor.execute('''INSERT INTO notification_deliveries
                    (delivery_id,notification_id,target_id,binding_version,rendered_encrypted,state,error_class)
                    VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(notification_id,target_id) DO NOTHING''',
                    ('delivery_'+uuid.uuid4().hex,message['notification_id'],target['target_id'],
                     target['binding_version'],protected(payload),delivery_state,error))
                seen_targets.add(target['target_id'])
                target_rows.append({'target_id':target['target_id'],'channel':target['channel'],
                                    'provider':target['provider'],'binding_version':target['binding_version']})
            route.append({'rule_id':rule['rule_id'],'rule_name':rule['name'],
                          'recipients':recipient_ids,'targets':target_rows})
        return 'expanded', route

    def expand(self):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT *,clock_timestamp() AS database_now FROM notification_messages
                WHERE expansion_state='pending' ORDER BY priority DESC,created_at
                LIMIT %s FOR UPDATE SKIP LOCKED''', (self.config.batch_size,))
            messages = cursor.fetchall()
            for row in messages:
                message = dict(row)
                if message['policy_key'] == RULE_EVENT_POLICY_KEY:
                    state, route = self._expand_rule_event(cursor, message)
                    cursor.execute('''UPDATE notification_messages SET expansion_state=%s,route_snapshot=%s::jsonb
                        WHERE notification_id=%s''', (state,json.dumps(route),message['notification_id']))
                    continue
                subject = Subject(message['recipient_user_id'],message['tenant_id'])
                route = []
                if message['expires_at'] <= message['database_now']:
                    state = 'expired'
                else:
                    state = 'expanded'
                    try:
                        self.guard(cursor, subject)
                        resource_in_tx(cursor, message)
                    except NotificationError:
                        pass
                    else:
                        lock_subject(cursor, subject)
                        message['parameters'] = unprotect(message['parameters_encrypted'])
                        # 租户级事件与测试投递额外路由到租户级 target（recipient 为空）。
                        include_tenant_targets = message['policy_key'] in ('tenant_admin_event', 'explicit_test_target')
                        # 租户级事件按事件归属租户兜底匹配：租户无 tenant_admin 时消息只有
                        # 平台管理员 global 副本（scope_key='global'），仅按 scope_key 匹配不到
                        # 租户级 IM target，webhook 将永不外发；event_key 去重保证同事件只投一次。
                        params_tenant = self._event_tenant(message) \
                            if message['policy_key'] == 'tenant_admin_event' else None
                        # 规则门控（设计 §5.2）：租户级 target 外发前查该事件在
                        # 事件归属租户是否有 enabled 规则；预置规则被停用即停
                        # 租户级外发（站内信不在此路径，不受影响）。global 平台
                        # 兜底副本解析不出事件租户时保持现状路由。
                        if message['policy_key'] == 'tenant_admin_event' and include_tenant_targets:
                            gate_tenant = params_tenant or message['tenant_id']
                            if gate_tenant and not self._tenant_rule_enabled(
                                    cursor, gate_tenant, message['event_type']):
                                include_tenant_targets = False
                        # 任务级目标快照（Phase 3，设计 §3.3/§8.2）：事件参数含
                        # notify_target_id 时**替代**默认偏好路由——个人偏好渠道
                        # 不再重复投递（评审修正：此前双路由重复打扰，且显式个人
                        # 目标被 prepare_send 偏好门控拦截）；站内信恒保留。
                        snapshot_target_id = (message['parameters'] or {}).get('notify_target_id') \
                            if isinstance(message['parameters'], dict) else None
                        cursor.execute('''SELECT t.*,p.enabled AS preference_enabled,p.enabled_since AS preference_since,
                                p.event_types FROM notification_targets t LEFT JOIN notification_preferences p
                                ON p.scope_key=t.scope_key AND p.recipient_user_id=t.recipient_user_id
                                AND p.channel=t.channel AND p.provider=t.provider AND p.category=%s
                            WHERE t.state='active' AND ''' + (
                                "t.target_id=%s" if snapshot_target_id else '''(
                                (t.scope_key=%s AND (t.recipient_user_id=%s OR (t.recipient_user_id IS NULL AND t.tenant_id IS NOT NULL AND %s)))
                                OR (t.recipient_user_id IS NULL AND t.tenant_id=%s))'''),
                            ((message['category'], snapshot_target_id) if snapshot_target_id
                             else (message['category'], subject.scope_key, subject.user_id,
                                   include_tenant_targets, params_tenant)))
                        for target in cursor.fetchall():
                            adapter = self.registry.get(target['channel'],target['provider'])
                            if not adapter or not adapter.available() or message['schema_version'] not in adapter.capabilities.notification_schema_versions:
                                continue
                            if message['policy_key'] == 'explicit_test_target':
                                ref = message['resource_ref']
                                eligible = target['target_id'] == ref['id'] and target['binding_version'] == ref['binding_version']
                            elif snapshot_target_id is not None:
                                # 任务级快照目标（Phase 3）：显式选择替代偏好路由
                                # （设计 §3.3/§8.2）——不做偏好门控、事件类型集合
                                # 与启用时序检查（受理后才启用的目标在长任务终态
                                # 必须可达；enabled_since<=accepted 是普通聊天默认
                                # 路由的门槛，评审复现误伤显式选择）；归属与当前
                                # 有效性由查询 WHERE（state='active'+target_id）保证。
                                eligible = True
                            elif target['recipient_user_id'] is None:
                                # 租户级 target 无偏好行，不参与偏好门控。
                                eligible = (message['policy_key'] == 'tenant_admin_event'
                                    and target['enabled_since'] <= message['accepted_at'])
                            else:
                                eligible = (target['preference_enabled'] and target['preference_since'] is not None
                                    and target['preference_since'] <= message['accepted_at']
                                    and target['enabled_since'] <= message['accepted_at']
                                    and message['event_type'] in _effective_event_types(target['event_types']))
                            if not eligible:
                                continue
                            if message['policy_key'] == 'tenant_admin_event' and target['recipient_user_id'] is None:
                                # 租户级事件每个管理员各有一条 message，同一租户级 target
                                # 只需投递一次：按 event_key 查已有投递，命中则跳过
                                cursor.execute('''SELECT 1 FROM notification_deliveries d
                                    JOIN notification_messages m ON m.notification_id=d.notification_id
                                    WHERE d.target_id=%s AND m.event_key=%s LIMIT 1''',
                                    (target['target_id'],message['event_key']))
                                if cursor.fetchone():
                                    continue
                            delivery_state,error = 'pending',None
                            try:
                                payload = adapter.render(message, target)
                                if len(json.dumps(payload,ensure_ascii=False).encode())>adapter.capabilities.payload_limit:
                                    raise ValueError('NOTIFICATION_PAYLOAD_TOO_LARGE')
                            except Exception as e:
                                # 不打异常体：错误文本可能携带消息参数（含联系方式明文）
                                logger.warning('后端日志：通知渲染失败 notification_id={} target_id={} 异常类型={}',
                                               message['notification_id'], target['target_id'], type(e).__name__)
                                payload,delivery_state,error = {},'failed','render_failure'
                            cursor.execute('''INSERT INTO notification_deliveries
                                (delivery_id,notification_id,target_id,binding_version,rendered_encrypted,state,error_class)
                                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(notification_id,target_id) DO NOTHING''',
                                ('delivery_'+uuid.uuid4().hex,message['notification_id'],target['target_id'],
                                 target['binding_version'],protected(payload),delivery_state,error))
                            route.append({'target_id':target['target_id'],'channel':target['channel'],
                                          'provider':target['provider'],'binding_version':target['binding_version']})
                        # 快照目标不存在（已硬删/换租户，主查询未命中）时在路由
                        # 快照留痕；存在时主循环已按快照语义完成投递（替代偏好
                        # 路由），此处不再二次展开。
                        if snapshot_target_id is not None and not any(
                                item.get('target_id') == snapshot_target_id for item in route):
                            route.append({'target_id':snapshot_target_id,
                                          'skipped':'target_not_found'})
                cursor.execute('''UPDATE notification_messages SET expansion_state=%s,route_snapshot=%s::jsonb
                    WHERE notification_id=%s''', (state,json.dumps(route),message['notification_id']))
            conn.commit()
            return len(messages)

    def claim(self, owner, limit):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT d.*,m.expires_at,clock_timestamp() AS database_now
                FROM notification_deliveries d JOIN notification_messages m USING(notification_id)
                WHERE (d.state='pending' AND d.next_attempt_at<=clock_timestamp())
                   OR (d.state='sending' AND d.lease_until<clock_timestamp())
                ORDER BY d.next_attempt_at LIMIT %s FOR UPDATE OF d SKIP LOCKED''', (limit,))
            claimed = []
            for row in cursor.fetchall():
                if row['expires_at'] <= row['database_now'] or row['attempts'] >= self.config.max_attempts:
                    cursor.execute('''UPDATE notification_deliveries SET state=%s,error_class=%s,
                        owner=NULL,lease_until=NULL,updated_at=clock_timestamp() WHERE delivery_id=%s''',
                        ('expired' if row['expires_at'] <= row['database_now'] else 'failed',
                         'ttl_expired' if row['expires_at'] <= row['database_now'] else 'attempts_exhausted',row['delivery_id']))
                    continue
                cursor.execute('''UPDATE notification_deliveries SET state='sending',owner=%s,
                    lease_until=clock_timestamp()+%s*interval '1 second',claim_version=claim_version+1,
                    attempts=attempts+1,updated_at=clock_timestamp() WHERE delivery_id=%s RETURNING *''',
                    (owner,self.config.lease_seconds,row['delivery_id']))
                claimed.append(dict(cursor.fetchone()))
            conn.commit()
            return claimed

    def prepare_send(self, claim):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT * FROM notification_deliveries WHERE delivery_id=%s AND state='sending'
                AND owner=%s AND claim_version=%s AND lease_until>clock_timestamp()''',
                (claim['delivery_id'],claim['owner'],claim['claim_version']))
            delivery = cursor.fetchone()
            if not delivery:
                return None
            cursor.execute('SELECT *,clock_timestamp() AS database_now FROM notification_messages WHERE notification_id=%s',
                           (claim['notification_id'],))
            message = cursor.fetchone()
            cursor.execute('SELECT * FROM notification_targets WHERE target_id=%s', (claim['target_id'],))
            target = cursor.fetchone()
            reason = None
            if not message or not target or target['state'] != 'active' or target['binding_version'] != delivery['binding_version']:
                reason = 'target_changed'
            elif message['expires_at'] <= message['database_now']:
                reason = 'ttl_expired'
            else:
                if message['recipient_user_id'] is None:
                    # 事件级消息（规则引擎，recipient NULL）：无个人主体可 guard，
                    # 只校验 target 是租户级且与消息同租户（事件租户归属由 expand
                    # 阶段的路由查询保证）；不符 fail-closed 记 skipped。
                    if (target['recipient_user_id'] is not None
                            or not message['tenant_id']
                            or target['tenant_id'] != message['tenant_id']):
                        reason = 'resource_revoked'
                else:
                    subject = Subject(message['recipient_user_id'],message['tenant_id'])
                    try:
                        self.guard(cursor, subject)
                        resource_in_tx(cursor, message)
                        if target['recipient_user_id'] is None:
                            # 租户级 target：正常路径要求与消息同 scope；平台管理员 global
                            # 兜底副本按事件归属租户放行（与 expand 兜底路由同一边界，
                            # 归属不符或无法判定一律拒绝，fail-closed）。
                            about_tenant = self._event_tenant(message)
                            if subject.tenant_id is None and about_tenant:
                                if target['tenant_id'] != about_tenant:
                                    raise NotificationError('TARGET_SCOPE_MISMATCH')
                            elif not tenant_owned(target, subject):
                                raise NotificationError('TARGET_SCOPE_MISMATCH')
                        elif not owned(target, subject):
                            raise NotificationError('TARGET_SCOPE_MISMATCH')
                    except NotificationError:
                        reason = 'resource_revoked'
                # 偏好门控豁免：任务级快照目标（设计 §3.3/§8.2——事件参数含
                # notify_target_id 即用户显式选择，替代默认偏好路由）与显式测试
                # 投递不参与偏好门控；expand 的 eligible 与此处准入同口径
                # （评审修正：此前个人目标被偏好门控拦截成 skipped/preference_changed）。
                try:
                    event_parameters = unprotect(message['parameters_encrypted'])
                except Exception:
                    event_parameters = None
                snapshot_selected = isinstance(event_parameters, dict) and bool(
                    event_parameters.get('notify_target_id'))
                if (not reason and target['recipient_user_id'] is not None
                        and message['policy_key'] != 'explicit_test_target'
                        and not snapshot_selected):
                    cursor.execute('''SELECT * FROM notification_preferences WHERE scope_key=%s
                        AND recipient_user_id=%s AND category=%s AND channel=%s AND provider=%s''',
                        (subject.scope_key,subject.user_id,message['category'],target['channel'],target['provider']))
                    pref = cursor.fetchone()
                    if (not pref or not pref['enabled'] or not pref['enabled_since']
                            or pref['enabled_since'] > message['accepted_at']
                            or target['enabled_since'] > message['accepted_at']
                            or message['event_type'] not in _effective_event_types(pref['event_types'])):
                        reason = 'preference_changed'
                adapter = self.registry.get(target['channel'], target['provider'])
                if not reason and (not adapter or not adapter.available()):
                    reason = 'provider_disabled'
            if reason:
                cursor.execute('''UPDATE notification_deliveries SET state=%s,error_class=%s,
                    owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                    WHERE delivery_id=%s AND owner=%s AND claim_version=%s AND state='sending' ''',
                    ('expired' if reason == 'ttl_expired' else 'skipped',reason,claim['delivery_id'],claim['owner'],claim['claim_version']))
                conn.commit()
                return None
            ttl = max(1,int((message['expires_at'] - message['database_now']).total_seconds()))
            return adapter,unprotect(target['configuration_encrypted']),unprotect(delivery['rendered_encrypted']),ttl

    def finish(self, claim, result):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            # Lock target first to match revocation lock order, then fence result.
            cursor.execute('SELECT * FROM notification_targets WHERE target_id=%s FOR UPDATE', (claim['target_id'],))
            target = cursor.fetchone()
            cursor.execute('''SELECT d.*,m.expires_at,clock_timestamp() AS database_now
                FROM notification_deliveries d JOIN notification_messages m USING(notification_id)
                WHERE d.delivery_id=%s AND d.state='sending' AND d.owner=%s AND d.claim_version=%s
                AND d.lease_until>clock_timestamp() FOR UPDATE OF d''',
                (claim['delivery_id'],claim['owner'],claim['claim_version']))
            row = cursor.fetchone()
            if not row:
                return False
            state, error = 'failed',result.error_class
            next_attempt = row['database_now']
            accepted = result.outcome == 'accepted'
            if not target or target['state'] != 'active' or target['binding_version'] != claim['binding_version']:
                state, error = 'skipped','target_changed'
            elif accepted:
                state = 'accepted'
            elif row['expires_at'] <= row['database_now']:
                state, error = 'expired','ttl_expired'
            elif result.outcome == 'retryable' and row['attempts'] < self.config.max_attempts:
                delay = max(result.retry_after or 0,min(3600,15*2**(row['attempts']-1))+random.randint(0,15))
                next_attempt = row['database_now']+timedelta(seconds=delay)
                state = 'pending' if next_attempt < row['expires_at'] else 'expired'
            elif result.outcome == 'target_invalid':
                cursor.execute('''UPDATE notification_targets SET state='invalid',binding_version=binding_version+1,
                    updated_at=clock_timestamp() WHERE target_id=%s AND binding_version=%s''',
                    (claim['target_id'],claim['binding_version']))
            cursor.execute('''UPDATE notification_deliveries SET state=%s,error_class=%s,next_attempt_at=%s,
                accepted_at=CASE WHEN %s THEN clock_timestamp() ELSE accepted_at END,
                provider_acceptance_id=%s,owner=NULL,lease_until=NULL,updated_at=clock_timestamp()
                WHERE delivery_id=%s''',
                (state,error,next_attempt,state=='accepted',result.provider_id,claim['delivery_id']))
            conn.commit()
            return True

    def cleanup(self):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT notification_id FROM notification_messages
                WHERE expires_at<clock_timestamp()-%s*interval '1 day'
                ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED''', (self.config.retention_days,self.config.batch_size))
            ids = [row['notification_id'] for row in cursor.fetchall()]
            if ids:
                cursor.execute('DELETE FROM notification_deliveries WHERE notification_id=ANY(%s)', (ids,))
                cursor.execute('DELETE FROM notification_messages WHERE notification_id=ANY(%s)', (ids,))
            conn.commit()
            return len(ids)
