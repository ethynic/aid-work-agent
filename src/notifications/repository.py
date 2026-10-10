"""Short PostgreSQL transactions for notifications. No transport runs under locks."""
import json
import random
import uuid
from datetime import timedelta

from src.db.database import get_db_connection
from src.db.encryption import encryption_manager

from .authorization import assert_subject_in_tx, resource_in_tx
from .contracts import NotificationError, Subject

TABLE_COLUMNS = {
    'notification_messages': 'notification_id notification_ref schema_version producer event_type event_key scope_key tenant_id recipient_user_id resource_ref template_key template_version parameters_encrypted occurred_at accepted_at expires_at category priority policy_key expansion_state route_snapshot created_at',
    'notification_targets': 'target_id scope_key tenant_id recipient_user_id channel provider installation_ref identity_digest target_schema_version configuration_encrypted binding_version state enabled_since verified_at created_at updated_at',
    'notification_preferences': 'scope_key tenant_id recipient_user_id category channel provider enabled event_types enabled_since updated_at',
    'notification_deliveries': 'delivery_id notification_id target_id binding_version rendered_encrypted state attempts next_attempt_at owner lease_until claim_version error_class provider_acceptance_id accepted_at delivered_at displayed_at read_at created_at updated_at',
}


def protected(value):
    return encryption_manager.encrypt(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def unprotect(value):
    return json.loads(encryption_manager.decrypt(value))


def public_target(row):
    return {key:row[key] for key in ('target_id','installation_ref','binding_version','state','enabled_since')}


def owned(row, subject):
    return bool(row and row['scope_key'] == subject.scope_key and row['recipient_user_id'] == subject.user_id
                and row['tenant_id'] == subject.tenant_id)


def lock_subject(cursor, subject):
    cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                   ('notification:subject:'+subject.scope_key+':'+subject.user_id,))


class NotificationRepository:
    def __init__(self, config, registry, connection_factory=get_db_connection):
        self.config, self.registry, self.connection_factory = config, registry, connection_factory

    def allowed(self, subject):
        return (subject.tenant_id is None or self.config.allowed_tenants is None
                or subject.tenant_id in self.config.allowed_tenants)

    def guard(self, cursor, subject):
        assert_subject_in_tx(cursor, subject)
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
             'notification_preferences'::regclass,'notification_deliveries'::regclass)''')
        names = {row['conname'] for row in cursor.fetchall()}
        if not {'notification_message_identity','notification_target_identity',
                'notification_delivery_target','notification_preferences_pkey'}.issubset(names):
            raise RuntimeError('NOTIFICATION_SCHEMA_CONSTRAINTS_REQUIRED')

    def assert_schema(self):
        if self.config.enabled:
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
            cursor.execute('''SELECT * FROM notification_messages WHERE notification_ref=%s
                AND scope_key=%s AND recipient_user_id=%s AND tenant_id IS NOT DISTINCT FROM %s''',
                (ref,subject.scope_key,subject.user_id,subject.tenant_id))
            message = cursor.fetchone()
            if not message:
                raise NotificationError('NOTIFICATION_NOT_FOUND', 404)
            resource = resource_in_tx(cursor, message)
            return {'notification_ref':ref,'event_type':message['event_type'],'resource':resource}

    def expand(self):
        with self.connection_factory() as conn:
            cursor = conn.cursor()
            cursor.execute('''SELECT *,clock_timestamp() AS database_now FROM notification_messages
                WHERE expansion_state='pending' ORDER BY priority DESC,created_at
                LIMIT %s FOR UPDATE SKIP LOCKED''', (self.config.batch_size,))
            messages = cursor.fetchall()
            for row in messages:
                message = dict(row)
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
                        cursor.execute('''SELECT t.*,p.enabled AS preference_enabled,p.enabled_since AS preference_since,
                                p.event_types FROM notification_targets t LEFT JOIN notification_preferences p
                                ON p.scope_key=t.scope_key AND p.recipient_user_id=t.recipient_user_id
                                AND p.channel=t.channel AND p.provider=t.provider AND p.category=%s
                            WHERE t.scope_key=%s AND t.recipient_user_id=%s AND t.state='active' ''',
                            (message['category'],subject.scope_key,subject.user_id))
                        for target in cursor.fetchall():
                            adapter = self.registry.get(target['channel'],target['provider'])
                            if not adapter or not adapter.available() or message['schema_version'] not in adapter.capabilities.notification_schema_versions:
                                continue
                            if message['policy_key'] == 'explicit_test_target':
                                ref = message['resource_ref']
                                eligible = target['target_id'] == ref['id'] and target['binding_version'] == ref['binding_version']
                            else:
                                eligible = (target['preference_enabled'] and target['preference_since'] is not None
                                    and target['preference_since'] <= message['accepted_at']
                                    and target['enabled_since'] <= message['accepted_at']
                                    and message['event_type'] in target['event_types'])
                            if not eligible:
                                continue
                            delivery_state,error = 'pending',None
                            try:
                                payload = adapter.render(message, target)
                                if len(json.dumps(payload,ensure_ascii=False).encode())>adapter.capabilities.payload_limit:
                                    raise ValueError('NOTIFICATION_PAYLOAD_TOO_LARGE')
                            except Exception:
                                payload,delivery_state,error = {},'failed','render_failure'
                            cursor.execute('''INSERT INTO notification_deliveries
                                (delivery_id,notification_id,target_id,binding_version,rendered_encrypted,state,error_class)
                                VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(notification_id,target_id) DO NOTHING''',
                                ('delivery_'+uuid.uuid4().hex,message['notification_id'],target['target_id'],
                                 target['binding_version'],protected(payload),delivery_state,error))
                            route.append({'target_id':target['target_id'],'channel':target['channel'],
                                          'provider':target['provider'],'binding_version':target['binding_version']})
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
                subject = Subject(message['recipient_user_id'],message['tenant_id'])
                try:
                    self.guard(cursor, subject)
                    resource_in_tx(cursor, message)
                    if not owned(target, subject):
                        raise NotificationError('TARGET_SCOPE_MISMATCH')
                except NotificationError:
                    reason = 'resource_revoked'
                if not reason and message['policy_key'] != 'explicit_test_target':
                    cursor.execute('''SELECT * FROM notification_preferences WHERE scope_key=%s
                        AND recipient_user_id=%s AND category=%s AND channel=%s AND provider=%s''',
                        (subject.scope_key,subject.user_id,message['category'],target['channel'],target['provider']))
                    pref = cursor.fetchone()
                    if (not pref or not pref['enabled'] or not pref['enabled_since']
                            or pref['enabled_since'] > message['accepted_at']
                            or target['enabled_since'] > message['accepted_at']
                            or message['event_type'] not in pref['event_types']):
                        reason = 'preference_changed'
                adapter = self.registry.get(target['channel'], target['provider'])
                if not reason and (not adapter or not adapter.available() or not self.config.enabled):
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
