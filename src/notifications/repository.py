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
from .contracts import (PLATFORM_RUNNER_INSTALLATION_REF, PLATFORM_RUNNER_POLICY_KEY,
                        RULE_EVENT_POLICY_KEY, NotificationError, Subject)

TABLE_COLUMNS = {
    'notification_messages': 'notification_id notification_ref schema_version producer event_type event_key scope_key tenant_id recipient_user_id resource_ref template_key template_version parameters_encrypted occurred_at accepted_at expires_at category priority policy_key expansion_state route_snapshot read_at created_at',
    'notification_targets': 'target_id scope_key tenant_id recipient_user_id channel provider installation_ref identity_digest target_schema_version configuration_encrypted rule_id binding_version state enabled_since verified_at created_at updated_at',
    'notification_preferences': 'scope_key tenant_id recipient_user_id category channel provider enabled event_types enabled_since updated_at',
    'notification_deliveries': 'delivery_id notification_id target_id binding_version rendered_encrypted state attempts next_attempt_at owner lease_until claim_version error_class provider_acceptance_id accepted_at delivered_at displayed_at read_at created_at updated_at',
    'notification_rules': 'rule_id tenant_id event_type name enabled is_system conditions recipient_mode created_by created_at updated_at',
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
# 2026-11-04 追加 task.manual_rejected（§15 评审 P1：窗口收尾任务手动执行被
# 拒通知）：旧默认五元（DDL DEFAULT 曾为该五元）同样兼容扩展，存量偏好行
# 与已部署库的新建行（DEFAULT 未迁移）都能投递新事件。
LEGACY_TASK_EVENT_TYPES = ('task.completed', 'task.failed')
LEGACY_DEFAULT_TASK_EVENT_TYPES = ('task.completed', 'task.failed', 'task.waiting_input')
LEGACY_DEFAULT_TASK_EVENT_TYPES_V2 = ('task.completed', 'task.failed', 'task.waiting_input',
                                      'task.paused', 'task.needs_action')
TASK_EVENT_TYPES = ('task.completed', 'task.failed', 'task.waiting_input', 'task.paused',
                    'task.needs_action', 'task.manual_rejected')


def _effective_event_types(stored):
    stored = tuple(stored or ())
    if set(stored) in (set(LEGACY_TASK_EVENT_TYPES), set(LEGACY_DEFAULT_TASK_EVENT_TYPES),
                       set(LEGACY_DEFAULT_TASK_EVENT_TYPES_V2)):
        return TASK_EVENT_TYPES
    return stored


# 渠道事件白名单（评审回炉 P2-5，设计决策 3/§4.3）：企微应用通知仅投递任务
# 终态（completed/failed）——waiting_input/paused 等过程事件不推企微卡片，
# 否则 ask_user 中途提问会绕过「≥5 分钟长任务才通知」的终态门禁；expand
# 资格判断对 wecom_app target 在既有偏好/事件集合判定之上 AND 本白名单，
# 其他渠道不受影响。adapter 渲染层同样只留终态映射（fail loud 兜底）。
PROVIDER_ALLOWED_EVENT_TYPES = {'wecom_app': ('task.completed', 'task.failed')}


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


def lock_subject(cursor, subject):
    cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                   ('notification:subject:'+subject.scope_key+':'+subject.user_id,))


# 规则引擎预置默认规则（设计 §4）：与 db_update 预置 INSERT 保持同一定义。
# (event_type, name, conditions, recipient_mode)
PRESET_RULES = (
    ('billing.credit_low', '积分提醒', {}, 'tenant_admins'),
    ('billing.tenant_expiry', '到期提醒', {}, 'tenant_admins'),
    ('abuse.rule_triggered', '滥用告警', {}, 'tenant_admins'),
    ('lead.captured', '留资通知', {'scope': 'channel'}, 'kf_assignee'),
)

# 规则引擎可配置事件白名单（租户管理员 CRUD 边界；task.* 用户任务事件不进
# 事件级规则匹配）。scheduled.task 为规则专用聚合类型：仅进创建白名单，不进
# PRESET_RULES（不预置）、不注册进 EVENTS 发布注册表、不参与 _expand_rule_event
# 常规匹配——由具体任务选用后经参数快照 notify_rule_id 在 expand 用户级路径路由
# （仅被任务选定时生效，不对全租户任务自动生效）。
SCHEDULED_TASK_RULE_TYPE = 'scheduled.task'
RULE_EVENT_TYPES = frozenset({item[0] for item in PRESET_RULES} | {SCHEDULED_TASK_RULE_TYPE})

RULE_RECIPIENT_MODES = frozenset({'tenant_admins', 'kf_assignee', 'task_owner'})

# scheduled.task 规则 conditions.events 值域：4 类任务事件（缺省=全部）
SCHEDULED_TASK_RULE_EVENTS = ('task.completed', 'task.failed', 'task.paused',
                              'task.needs_action')


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
    def guard_platform(cursor, subject):
        if subject.tenant_id is not None:
            raise NotificationError('NOTIFICATION_FORBIDDEN', 403)
        user = assert_subject_in_tx(cursor, subject)
        if user['role'] != 'platform_admin' or user['tenant_id'] is not None:
            raise NotificationError('NOTIFICATION_FORBIDDEN', 403)

    @staticmethod
    def platform_runner_target(row):
        return bool(row and row['scope_key'] == 'global' and row['tenant_id'] is None
                    and row['recipient_user_id'] is None and row['channel'] == 'im_webhook'
                    and row['provider'] == 'feishu_bot'
                    and row['installation_ref'] == PLATFORM_RUNNER_INSTALLATION_REF
                    and row['rule_id'] is None)

    def get_platform_runner_target(self, subject):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_platform(cursor, subject)
            cursor.execute('''SELECT * FROM notification_targets
                WHERE provider='feishu_bot' AND installation_ref=%s AND state='active' ''',
                (PLATFORM_RUNNER_INSTALLATION_REF,))
            row = cursor.fetchone()
            if row and not self.platform_runner_target(row):
                raise NotificationError('NOTIFICATION_TARGET_SCOPE_MISMATCH', 409)
            return public_target(row) if row else None

    def register_platform_runner_target(self, subject, body):
        adapter = self.registry.get('im_webhook', 'feishu_bot')
        if not adapter or not adapter.available():
            raise NotificationError('NOTIFICATION_PROVIDER_UNAVAILABLE', 503)
        configuration = {'webhook_url': body.webhook_url}
        digest = adapter.validate_target(configuration)
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_platform(cursor, subject)
            cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                           ('notification:platform:'+PLATFORM_RUNNER_INSTALLATION_REF,))
            cursor.execute('''SELECT * FROM notification_targets WHERE provider='feishu_bot'
                AND installation_ref=%s ORDER BY (state='active') DESC,updated_at DESC
                LIMIT 1 FOR UPDATE''', (PLATFORM_RUNNER_INSTALLATION_REF,))
            row = cursor.fetchone()
            if row and not self.platform_runner_target(row):
                raise NotificationError('NOTIFICATION_TARGET_SCOPE_MISMATCH', 409)
            if row:
                if (body.expected_binding_version is not None
                        and body.expected_binding_version != row['binding_version']):
                    raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
                changed = (row['state'] != 'active'
                           or unprotect(row['configuration_encrypted']) != configuration)
                cursor.execute('''UPDATE notification_targets SET configuration_encrypted=%s,
                    identity_digest=%s,state='active',binding_version=binding_version+%s,
                    enabled_since=CASE WHEN state='active' THEN enabled_since ELSE clock_timestamp() END,
                    verified_at=clock_timestamp(),updated_at=clock_timestamp()
                    WHERE target_id=%s RETURNING *''',
                    (protected(configuration),digest,int(changed),row['target_id']))
            else:
                if body.expected_binding_version is not None:
                    raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
                cursor.execute('''INSERT INTO notification_targets
                    (target_id,scope_key,tenant_id,recipient_user_id,channel,provider,
                     installation_ref,identity_digest,configuration_encrypted,rule_id)
                    VALUES (%s,'global',NULL,NULL,'im_webhook','feishu_bot',%s,%s,%s,NULL)
                    RETURNING *''', ('target_'+uuid.uuid4().hex,
                    PLATFORM_RUNNER_INSTALLATION_REF,digest,protected(configuration)))
            result = public_target(cursor.fetchone())
            conn.commit()
            return result

    def revoke_platform_runner_target(self, subject, version):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard_platform(cursor, subject)
            cursor.execute('''SELECT * FROM notification_targets WHERE provider='feishu_bot'
                AND installation_ref=%s AND state='active' FOR UPDATE''',
                (PLATFORM_RUNNER_INSTALLATION_REF,))
            row = cursor.fetchone()
            if not row or not self.platform_runner_target(row):
                raise NotificationError('NOTIFICATION_TARGET_NOT_FOUND', 404)
            if row['binding_version'] != version:
                raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
            cursor.execute('''UPDATE notification_targets SET state='revoked',
                binding_version=binding_version+1,updated_at=clock_timestamp()
                WHERE target_id=%s RETURNING *''', (row['target_id'],))
            result = public_target(cursor.fetchone())
            cursor.execute('''UPDATE notification_deliveries SET state='skipped',
                error_class='target_revoked',owner=NULL,lease_until=NULL,
                updated_at=clock_timestamp() WHERE target_id=%s
                AND state IN ('pending','sending')''', (row['target_id'],))
            conn.commit()
            return result

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
                            else 'jsonb' if column in ('resource_ref','route_snapshot','event_types','conditions')
                            else 'int8' if column in ('binding_version','claim_version')
                            else 'int4' if column in ('schema_version','target_schema_version','template_version','priority','attempts')
                            else 'bool' if column in ('enabled','is_system') else 'text')
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
            # digest 查重按 scope 收敛（唯一约束 UNIQUE(provider,identity_digest,
            # scope_key,rule_id) 本就按 scope 隔离）：同一 endpoint（如同一企微
            # 成员）可在不同租户 scope 各自绑定 target，同 scope 内他人绑定仍 409。
            cursor.execute('''SELECT * FROM notification_targets WHERE provider=%s AND identity_digest=%s
                AND scope_key=%s FOR UPDATE''',
                (body.provider, digest, subject.scope_key))
            endpoint = cursor.fetchone()
            # 复审 1（同租户接手）：active 行他人绑定仍 409（必须先解绑）；
            # 非 active（revoked/invalid）行允许同 scope 用户接手——身份已由
            # wecom_bind 服务端 member() 核验，无条件 409 会造成「A 解绑后
            # B 绑同一身份被 A 的废弃行卡死」死循环。
            if endpoint and not owned(endpoint, subject) and endpoint['state'] == 'active':
                raise NotificationError('NOTIFICATION_TARGET_OWNED', 409)
            cursor.execute('''SELECT * FROM notification_targets WHERE provider=%s AND installation_ref=%s
                ORDER BY (state='active') DESC,updated_at DESC LIMIT 1 FOR UPDATE''', (body.provider, body.installation_ref))
            installation = cursor.fetchone()
            if installation and installation['state'] == 'active' and not owned(installation, subject):
                raise NotificationError('NOTIFICATION_INSTALLATION_OWNED', 409)
            # installation 仅 active 时才与 endpoint 行冲突；接手人自己的 stale
            # revoked 行（如曾绑过其他身份后解绑）忽略，继续接手
            if (endpoint and installation and installation['state'] == 'active'
                    and endpoint['target_id'] != installation['target_id']):
                raise NotificationError('NOTIFICATION_BINDING_CONFLICT', 409)
            if endpoint and not owned(endpoint, subject):
                # 接手：改属主/安装引用/配置并复活；identity_digest 不变（同一
                # 身份）。跳过 expected_binding_version 校验——旧版本号属于前任
                # 属主，对绑定发起人无并发意义。
                cursor.execute('''UPDATE notification_targets SET recipient_user_id=%s,
                    installation_ref=%s,configuration_encrypted=%s,state='active',
                    binding_version=binding_version+1,enabled_since=clock_timestamp(),
                    verified_at=clock_timestamp(),updated_at=clock_timestamp()
                    WHERE target_id=%s RETURNING *''',
                    (subject.user_id, body.installation_ref, protected(configuration), endpoint['target_id']))
                result = public_target(cursor.fetchone())
                conn.commit()
                return result
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

    def list_task_rules(self, subject):
        """定时任务可选用通知规则（普通用户只读）：本租户 enabled 的
        scheduled.task 规则 + 规则级渠道目标脱敏摘要。规则的创建与外发
        目标配置在租户管理员规则页；此处仅供任务编辑弹窗选择，仅返回
        rule_id/name/conditions 与目标展示字段，不含凭据原文。"""
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard(cursor, subject)
            if subject.tenant_id is None:
                raise NotificationError('NOTIFICATION_TENANT_REQUIRED', 400)
            cursor.execute('''SELECT rule_id,name,conditions FROM notification_rules
                WHERE tenant_id=%s AND event_type=%s AND enabled
                ORDER BY created_at,rule_id''',
                (subject.tenant_id, SCHEDULED_TASK_RULE_TYPE))
            rules = cursor.fetchall()
            result = []
            for rule in rules:
                cursor.execute('''SELECT * FROM notification_targets
                    WHERE rule_id=%s AND state='active' ORDER BY created_at''',
                    (rule['rule_id'],))
                result.append({'rule_id': rule['rule_id'], 'name': rule['name'],
                               'conditions': rule['conditions'] if isinstance(rule['conditions'], dict) else {},
                               'targets': [public_target(row) for row in cursor.fetchall()]})
            return result

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
        scheduled.task 支持 {events}（4 类任务事件子集，缺省=全部）；其余事件
        无条件（{}）。未知键一律拒绝，防止前端冗余字段静默失效。"""
        if not isinstance(conditions, dict):
            raise NotificationError('NOTIFICATION_RULE_CONDITIONS_INVALID', 400)
        if event_type == SCHEDULED_TASK_RULE_TYPE:
            if set(conditions) - {'events'}:
                raise NotificationError('NOTIFICATION_RULE_CONDITIONS_INVALID', 400)
            events = conditions.get('events')
            if events is None or events == []:
                return {}
            if (not isinstance(events, list)
                    or not all(isinstance(item, str) and item in SCHEDULED_TASK_RULE_EVENTS
                               for item in events)):
                raise NotificationError('NOTIFICATION_RULE_CONDITIONS_INVALID', 400)
            # 去重保序（值域 4 项，去重后自然 1-4 项）
            return {'events': list(dict.fromkeys(events))}
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

    @staticmethod
    def _validate_rule_recipient_mode(event_type, recipient_mode):
        """接收人模式按事件类型校验：scheduled.task 固定 task_owner（仅占位
        枚举——站内信恒发任务属主，规则只决定外发渠道与事件过滤，不参与
        _resolve_rule_recipients 接收人解析）；其余事件沿用租户管理员/
        线索归属员工两枚举，task_owner 不落到事件级规则。"""
        if event_type == SCHEDULED_TASK_RULE_TYPE:
            if recipient_mode != 'task_owner':
                raise NotificationError('NOTIFICATION_RULE_RECIPIENT_INVALID', 400)
        elif recipient_mode not in RULE_RECIPIENT_MODES or recipient_mode == 'task_owner':
            raise NotificationError('NOTIFICATION_RULE_RECIPIENT_INVALID', 400)

    def _validate_rule_kf_configs(self, cursor, tenant_id, conditions):
        """kf_config 层条件逐个校验归属本租户：允许渠道 config_id（渠道层，
        存量语义）或该租户 wecom_kf 渠道 config JSON 内的客服账号 open_kfid
        （账号级）。"""
        ids = conditions.get('kf_config_ids', ())
        cursor.execute("SELECT config_id, config FROM tenant_channel_configs"
                       " WHERE tenant_id=%s AND channel_type='wecom_kf'", (tenant_id,))
        valid_ids = set()
        for row in cursor.fetchall():
            valid_ids.add(row['config_id'])
            try:
                config = json.loads(row['config']) if isinstance(row['config'], str) else (row['config'] or {})
            except (TypeError, ValueError):
                continue
            kf_account = (config or {}).get('kf_account')
            if not isinstance(kf_account, list):
                continue
            for kf in kf_account:
                if isinstance(kf, dict):
                    open_kfid = kf.get('open_kfid')
                    if open_kfid:
                        valid_ids.add(open_kfid)
        for config_id in ids:
            if config_id not in valid_ids:
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
            self._validate_rule_recipient_mode(body.event_type, body.recipient_mode)
            conditions = self._validate_rule_conditions(body.event_type, body.conditions or {})
            if conditions.get('scope') == 'kf_config':
                self._validate_rule_kf_configs(cursor, subject.tenant_id, conditions)
            cursor.execute('''INSERT INTO notification_rules
                (rule_id,tenant_id,event_type,name,enabled,is_system,conditions,recipient_mode,
                 created_by) VALUES (%s,%s,%s,%s,%s,FALSE,%s::jsonb,%s,%s)
                ON CONFLICT (tenant_id,event_type,name) DO NOTHING RETURNING *''',
                ('rule_'+uuid.uuid4().hex,subject.tenant_id,body.event_type,name,
                 bool(body.enabled),json.dumps(conditions),body.recipient_mode,subject.user_id))
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
                self._validate_rule_recipient_mode(rule['event_type'], body.recipient_mode)
                updates.append('recipient_mode=%s')
                params.append(body.recipient_mode)
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
        （允许同 provider 多个 active）。同一地址允许挂到多条规则（多账号
        通知同一群）：行选取优先本规则已有行（幂等/换配置/复活），其次
        接管旧默认渠道行（rule_id IS NULL，概念已移除），否则新插一行；
        不再因其他规则占用同一 digest 而 409。"""
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
            # 行选取：本规则已有行（active 优先）→ 幂等登记/换配置/复活；
            # 否则旧默认渠道行（rule_id IS NULL）接管改绑（设计 §10 承诺）；
            # 否则新插一行（同 digest 多规则各行独立投递）
            cursor.execute('''SELECT * FROM notification_targets WHERE provider=%s AND identity_digest=%s
                AND scope_key=%s AND rule_id=%s
                ORDER BY CASE WHEN state='active' THEN 0 ELSE 1 END,
                updated_at DESC LIMIT 1 FOR UPDATE''',
                (body.provider, digest, subject.scope_key, body.rule_id))
            row = cursor.fetchone()
            if row is None:
                cursor.execute('''SELECT * FROM notification_targets WHERE provider=%s AND identity_digest=%s
                    AND scope_key=%s AND rule_id IS NULL AND state='active'
                    ORDER BY updated_at DESC LIMIT 1 FOR UPDATE''',
                    (body.provider, digest, subject.scope_key))
                row = cursor.fetchone()
            if row:
                active = row['state'] == 'active'
                if body.expected_binding_version is not None and body.expected_binding_version != row['binding_version']:
                    raise NotificationError('NOTIFICATION_BINDING_CHANGED', 409)
                # expected_binding_version 未传时允许接管/幂等登记/复活：
                # 阻断会出现「又删又加必报绑定冲突」的回归。
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

    def wecom_bind_profile(self, subject):
        """设置页绑定状态（Phase 4，设计 §4.5）：当前用户最近的 wecom_app
        target 状态与绑定姓名快照（绑定落库时缓存于 target 配置，设计 §4.1）。
        未登记 target 时两个字段均为 None。"""
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            self.guard(cursor, subject)
            cursor.execute('''SELECT state,configuration_encrypted FROM notification_targets
                WHERE scope_key=%s AND recipient_user_id=%s AND channel='wecom_app'
                ORDER BY created_at DESC LIMIT 1''', (subject.scope_key, subject.user_id))
            row = cursor.fetchone()
            if not row:
                return {'target_state': None, 'name': None}
            configuration = unprotect(row['configuration_encrypted'])
            return {'target_state': row['state'],
                    'name': (configuration or {}).get('bound_user_name') or None}

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
            result = {'notification_ref':ref,'event_type':message['event_type'],'resource':resource}
            # 站内信同款标题/正文（§15 评审 P2：推送点击落地页可展示通知文案，
            # 如手动执行被拒的原因；仅模板渲染文本，不含原始参数对象）
            try:
                from .events import render_inbox_text
                params = unprotect(message['parameters_encrypted'])
                title, body = render_inbox_text(message['event_type'], params)
                result['title'], result['body'] = title, body
            except Exception:
                pass
            return result

    INBOX_EVENT_TYPES = ('billing.credit_low','billing.tenant_expiry','abuse.rule_triggered',
                         'task.completed','task.failed','task.waiting_input','task.paused',
                         'task.needs_action','task.manual_rejected','lead.captured')

    @staticmethod
    def _inbox_link(event_type, params, subject, notification_ref=None, share_key=None):
        """点击站内信的跳转地址；abuse 告警跳防滥用中心并定位到该用户明细；
        提问等待通知复用既有通知解析入口 /notification?ref=——入口完成认证与
        resource 解析后带 _load_session/数字员工路由回原会话（五轮评审 P2-4：
        不自行拼 chat 路径，避免遗漏非默认数字员工路由）。scheduled 的
        task.completed/failed 有结果分享映射时跳只读结果页；回填失败及普通聊天
        终态仍走通知入口。"""
        if event_type in ('task.completed', 'task.failed') and params.get('task_id') and share_key:
            return '/share/task-result/' + quote(share_key, safe='')
        if event_type in ('task.waiting_input', 'task.paused', 'task.needs_action',
                          'task.completed', 'task.failed', 'task.manual_rejected'):
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
                parameters_encrypted,resource_ref FROM notification_messages
                WHERE {scope_sql} AND recipient_user_id=%s AND event_type=ANY(%s)'''
            if unread_only:
                sql += ' AND read_at IS NULL'
            sql += ' ORDER BY created_at DESC LIMIT %s'
            cursor.execute(sql,(*scope_params,subject.user_id,list(self.INBOX_EVENT_TYPES),limit))
            prepared = []
            for row in cursor.fetchall():
                try:
                    params = unprotect(row['parameters_encrypted'])
                    title,body = render_inbox_text(row['event_type'],params)
                except Exception:
                    params = {}
                    title,body = row['event_type'],''
                prepared.append((row, params, title, body))
            runner_ids = [row['resource_ref']['id'] for row, params, _, _ in prepared
                if row['event_type'] in ('task.completed', 'task.failed') and params.get('task_id')
                and isinstance(row.get('resource_ref'), dict)
                and row['resource_ref'].get('type') == 'runner_result'
                and row['resource_ref'].get('id')]
            share_keys = {}
            if runner_ids:
                from src.services.task_result_sharing.service import task_result_links_for_runners_in_tx

                cursor.execute('SAVEPOINT inbox_result_share')
                try:
                    share_keys = task_result_links_for_runners_in_tx(
                        cursor, runner_ids, subject.tenant_id, subject.user_id)
                    cursor.execute('RELEASE SAVEPOINT inbox_result_share')
                    conn.commit()
                except Exception as error:
                    cursor.execute('ROLLBACK TO SAVEPOINT inbox_result_share')
                    cursor.execute('RELEASE SAVEPOINT inbox_result_share')
                    logger.warning('后端日志：站内信结果分享链接回填失败 异常类型={}', type(error).__name__)
            items = []
            for row, params, title, body in prepared:
                resource = row.get('resource_ref') or {}
                items.append({'notification_ref':row['notification_ref'],'event_type':row['event_type'],
                    'category':row['category'],'priority':row['priority'],'title':title,'body':body,
                    'link':self._inbox_link(row['event_type'],params,subject,
                                            notification_ref=row['notification_ref'],
                                            share_key=share_keys.get(resource.get('id'))),
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
                (rule_id,tenant_id,event_type,name,is_system,conditions,recipient_mode)
                VALUES (%s,%s,%s,%s,TRUE,%s::jsonb,%s)
                ON CONFLICT (tenant_id,event_type,name) DO NOTHING''',
                ('rule_preset_' + hashlib.md5((tenant_id + ':' + event).encode()).hexdigest(),
                 tenant_id, event, name, json.dumps(conditions), mode))

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
        """规则命中后的外发目标：仅规则专属 target（同 provider 可多个）。"""
        cursor.execute('''SELECT * FROM notification_targets
            WHERE state='active' AND recipient_user_id IS NULL AND tenant_id=%s
            AND rule_id=%s''', (tenant_id, rule['rule_id']))
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
        cursor.execute('''SELECT rule_id,name,conditions,recipient_mode
            FROM notification_rules WHERE tenant_id=%s AND event_type=%s AND enabled
            ORDER BY created_at,rule_id''', (tenant_id, message['event_type']))
        rules = cursor.fetchall()
        for rule in rules:
            conditions = rule['conditions'] if isinstance(rule['conditions'], dict) else {}
            if conditions.get('scope') == 'kf_config':
                # kf_config 层规则：事件 kf_config_id（渠道级，存量条件）或
                # open_kfid（账号级）命中 ID 列表才展开；渠道层（scope=channel）
                # 与无条件规则全量命中
                ids = conditions.get('kf_config_ids') or []
                if (params.get('kf_config_id') not in ids
                        and params.get('open_kfid') not in ids):
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
                target_rows.append({'target_id':target['target_id'],'channel':target['channel'],
                                    'provider':target['provider'],'binding_version':target['binding_version']})
            route.append({'rule_id':rule['rule_id'],'rule_name':rule['name'],
                          'recipients':recipient_ids,'targets':target_rows})
        return 'expanded', route

    @staticmethod
    def platform_runner_message(message, parameters):
        resource = message.get('resource_ref') or {}
        return bool(message.get('scope_key') == 'global'
                    and message.get('tenant_id') is None
                    and message.get('recipient_user_id') is None
                    and message.get('producer') == 'runner-monitor'
                    and message.get('event_type') == 'runner.long_running'
                    and message.get('policy_key') == PLATFORM_RUNNER_POLICY_KEY
                    and message.get('template_key') == 'runner_long_running'
                    and message.get('schema_version') == 1
                    and resource.get('type') == 'runner_alert'
                    and isinstance(parameters, dict)
                    and resource.get('id') == parameters.get('runner_id'))

    def _expand_platform_runner_alert(self, cursor, message):
        if message['expires_at'] <= message['database_now']:
            return 'expired', []
        try:
            parameters = unprotect(message['parameters_encrypted'])
        except Exception:
            parameters = None
        if not self.platform_runner_message(message, parameters):
            return 'expanded', [{'skipped': 'platform_event_invalid'}]
        message['parameters'] = parameters
        cursor.execute('''SELECT * FROM notification_targets WHERE provider='feishu_bot'
            AND installation_ref=%s AND state='active' ''',
            (PLATFORM_RUNNER_INSTALLATION_REF,))
        target = cursor.fetchone()
        if not target:
            return 'expanded', [{'skipped': 'platform_target_missing'}]
        if not self.platform_runner_target(target):
            return 'expanded', [{'skipped': 'platform_target_invalid'}]
        adapter = self.registry.get('im_webhook', 'feishu_bot')
        if not adapter or not adapter.available():
            return 'expanded', [{'skipped': 'platform_provider_unavailable'}]
        route = []
        self._append_delivery(cursor, message, target, route)
        return 'expanded', route

    def _append_delivery(self, cursor, message, target, route):
        """渲染并落一条外发投递行（payload 上限、ON CONFLICT DO NOTHING、
        route 留痕）；适配器不可用或渲染失败不抛错。偏好路由、任务级目标
        快照与任务级规则快照共用同一渲染与落库语义。"""
        if (target.get('installation_ref') == PLATFORM_RUNNER_INSTALLATION_REF
                and not (self.platform_runner_target(target)
                         and self.platform_runner_message(message, message.get('parameters')))):
            return
        adapter = self.registry.get(target['channel'],target['provider'])
        if not adapter or not adapter.available() or message['schema_version'] not in adapter.capabilities.notification_schema_versions:
            return
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

    def _expand_task_rule_snapshot(self, cursor, message, rule_id, route):
        """任务级规则快照展开（定时任务接入通知规则）：按快照规则取规则级
        targets 投递（替代偏好路由，与 notify_target_id 快照同语义，跳过偏好
        门控）。规则已删除/非本租户、已停用或事件被 conditions.events 过滤时
        仅 route 留痕 skipped——任务已显式选规则，不回退偏好路由。"""
        cursor.execute('''SELECT rule_id,name,enabled,conditions FROM notification_rules
            WHERE rule_id=%s AND tenant_id=%s''', (rule_id, message['tenant_id']))
        rule = cursor.fetchone()
        if not rule:
            route.append({'rule_id': rule_id, 'skipped': 'rule_not_found'})
            return
        if not rule['enabled']:
            route.append({'rule_id': rule_id, 'skipped': 'rule_disabled'})
            return
        conditions = rule['conditions'] if isinstance(rule['conditions'], dict) else {}
        events = conditions.get('events') or []
        if events and message['event_type'] not in events:
            route.append({'rule_id': rule_id, 'skipped': 'rule_event_filtered'})
            return
        for target in self._rule_event_targets(cursor, rule, message['tenant_id']):
            self._append_delivery(cursor, message, target, route)

    def expand(self):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT *,clock_timestamp() AS database_now FROM notification_messages
                WHERE expansion_state='pending' ORDER BY priority DESC,created_at
                LIMIT %s FOR UPDATE SKIP LOCKED''', (self.config.batch_size,))
            messages = cursor.fetchall()
            for row in messages:
                message = dict(row)
                if message['policy_key'] == PLATFORM_RUNNER_POLICY_KEY:
                    state, route = self._expand_platform_runner_alert(cursor, message)
                    cursor.execute('''UPDATE notification_messages SET expansion_state=%s,
                        route_snapshot=%s::jsonb WHERE notification_id=%s''',
                        (state,json.dumps(route),message['notification_id']))
                    continue
                resource = message.get('resource_ref') or {}
                if (message['event_type'] in ('task.completed', 'task.failed')
                        and isinstance(resource, dict) and resource.get('type') == 'runner_result'
                        and message['expires_at'] > message['database_now']):
                    from src.services.task_result_sharing.service import task_result_links_for_runners_in_tx

                    cursor.execute('SAVEPOINT delivery_result_share')
                    try:
                        keys = task_result_links_for_runners_in_tx(
                            cursor, [resource.get('id')], message['tenant_id'],
                            message['recipient_user_id'])
                        message['_task_result_share_key'] = keys.get(resource.get('id'))
                        cursor.execute('RELEASE SAVEPOINT delivery_result_share')
                    except Exception as error:
                        cursor.execute('ROLLBACK TO SAVEPOINT delivery_result_share')
                        cursor.execute('RELEASE SAVEPOINT delivery_result_share')
                        logger.warning('后端日志：外发结果分享链接回填失败 notification_id={} 异常类型={}',
                                       message['notification_id'], type(error).__name__)
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
                        # 租户级事件与测试投递额外路由到租户外发 target（recipient 为空）。
                        include_tenant_targets = message['policy_key'] in ('tenant_admin_event', 'explicit_test_target')
                        # 租户级事件按事件归属租户兜底匹配：租户无 tenant_admin 时消息只有
                        # 平台管理员 global 副本（scope_key='global'），仅按 scope_key 匹配不到
                        # 租户外发 target，webhook 将永不外发；event_key 去重保证同事件只投一次。
                        params_tenant = self._event_tenant(message) \
                            if message['policy_key'] == 'tenant_admin_event' else None
                        # 外发目标走规则级 target（默认渠道概念已移除）：聚合事件归属租户
                        # 该事件全部 enabled 规则的规则级 target；无启用规则即停外发
                        # （站内信不在此路径，不受影响）。惰性补插预置规则保持规则页完整。
                        tenant_rule_ids = []
                        if message['policy_key'] == 'tenant_admin_event' and include_tenant_targets:
                            gate_tenant = params_tenant or message['tenant_id']
                            if gate_tenant:
                                self._ensure_preset_rules_in_tx(cursor, gate_tenant, message['event_type'])
                                cursor.execute('''SELECT rule_id FROM notification_rules
                                    WHERE tenant_id=%s AND event_type=%s AND enabled''',
                                    (gate_tenant, message['event_type']))
                                tenant_rule_ids = [row['rule_id'] for row in cursor.fetchall()]
                                if not tenant_rule_ids:
                                    include_tenant_targets = False
                        # 任务级规则快照（定时任务接入通知规则）：事件参数含
                        # notify_rule_id 时按任务选用的通知规则路由外发——取
                        # 规则级 targets 投递（替代偏好路由，与目标快照同为
                        # 显式选择语义，跳过偏好门控）；规则停用/删除或事件被
                        # conditions.events 过滤时 route 留痕 skipped，不回退
                        # 偏好路由（任务已显式选规则，静默回退会造成通知去向
                        # 与用户预期不符）。站内信恒保留。
                        snapshot_rule_id = (message['parameters'] or {}).get('notify_rule_id') \
                            if isinstance(message['parameters'], dict) else None
                        if snapshot_rule_id:
                            self._expand_task_rule_snapshot(cursor, message, snapshot_rule_id, route)
                        else:
                            # 任务级目标快照（Phase 3，设计 §3.3/§8.2）：事件参数含
                            # notify_target_id 时**替代**默认偏好路由——个人偏好渠道
                            # 不再重复投递（评审修正：此前双路由重复打扰，且显式个人
                            # 目标被 prepare_send 偏好门控拦截）；站内信恒保留。
                            snapshot_target_id = (message['parameters'] or {}).get('notify_target_id') \
                                if isinstance(message['parameters'], dict) else None
                            # 租户级事件外发 target 限定启用规则的规则级 target；
                            # 测试投递不限（具体目标由 resource_ref 资格检查锁定）。
                            if message['policy_key'] == 'tenant_admin_event':
                                tenant_cond, tenant_cond_params = 't.rule_id=ANY(%s)', (tenant_rule_ids,)
                            else:
                                tenant_cond, tenant_cond_params = 'TRUE', ()
                            cursor.execute('''SELECT t.*,p.enabled AS preference_enabled,p.enabled_since AS preference_since,
                                p.event_types FROM notification_targets t LEFT JOIN notification_preferences p
                                ON p.scope_key=t.scope_key AND p.recipient_user_id=t.recipient_user_id
                                AND p.channel=t.channel AND p.provider=t.provider AND p.category=%s
                            WHERE t.state='active' AND ''' + (
                                "t.target_id=%s" if snapshot_target_id else '''(
                                (t.scope_key=%s AND (t.recipient_user_id=%s OR (t.recipient_user_id IS NULL AND t.tenant_id IS NOT NULL AND ''' + tenant_cond + ''')))
                                OR (t.recipient_user_id IS NULL AND t.tenant_id=%s AND ''' + tenant_cond + '''))'''),
                            ((message['category'], snapshot_target_id) if snapshot_target_id
                             else (message['category'], subject.scope_key, subject.user_id,
                                   *tenant_cond_params, params_tenant, *tenant_cond_params)))
                            for target in cursor.fetchall():
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
                                        and message['event_type'] in _effective_event_types(target['event_types'])
                                        and (target['provider'] != 'wecom_app' or
                                             message['event_type'] in PROVIDER_ALLOWED_EVENT_TYPES['wecom_app']))
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
                                self._append_delivery(cursor, message, target, route)
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
                if message['policy_key'] == PLATFORM_RUNNER_POLICY_KEY:
                    try:
                        parameters = unprotect(message['parameters_encrypted'])
                    except Exception:
                        parameters = None
                    if (not self.platform_runner_target(target)
                            or not self.platform_runner_message(message, parameters)):
                        reason = 'resource_revoked'
                elif target['installation_ref'] == PLATFORM_RUNNER_INSTALLATION_REF:
                    # A task snapshot or tenant rule must never redirect to the
                    # platform's dedicated Feishu target.
                    reason = 'resource_revoked'
                elif message['recipient_user_id'] is None:
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
