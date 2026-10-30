"""Independent notification boundary tests on an isolated real PostgreSQL DB.

Only the external push provider is replaced; guards, SQL and auth are real.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import threading
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.config.settings import NotificationsConfig, settings
from src.notifications.contracts import (
    AdapterCapabilities, AdapterRegistry, NotificationError, PreferenceRequest,
    RegisterTarget, SendResult, Subject, TestRequest as NotificationTestRequest,
)
from src.notifications.repository import NotificationRepository
from .test_notifications import terminal

pytestmark = pytest.mark.integration


class BoundaryWebAdapter:
    channel = provider = 'web_push'
    capabilities = AdapterCapabilities()
    enabled = True

    def available(self):
        return self.enabled

    def validate_target(self, configuration):
        return hashlib.sha256(configuration['endpoint'].encode()).hexdigest()

    def render(self, message, target):
        return {'ref': message['notification_ref']}


@pytest.fixture
def boundary_repo(service_database, actors, monkeypatch):
    config = NotificationsConfig()
    adapter = BoundaryWebAdapter()
    repo = NotificationRepository(config, AdapterRegistry([adapter]), service_database.connect)
    monkeypatch.setattr(settings, 'notifications', config)
    repo.assert_schema()
    yield repo
    ids = [actor.user_id for actor in actors.values()]
    service_database.rows('DELETE FROM notification_deliveries WHERE notification_id IN '
                          '(SELECT notification_id FROM notification_messages WHERE recipient_user_id=ANY(%s))', (ids,))
    for table in ('notification_messages', 'notification_preferences', 'notification_targets'):
        service_database.rows('DELETE FROM ' + table + ' WHERE recipient_user_id=ANY(%s)', (ids,))


def subject(actor):
    return Subject(actor.user_id, actor.tenant_id)


def registration(*, installation=None, endpoint=None, version=None):
    return RegisterTarget(channel='web_push', provider='web_push',
                          installation_ref=installation or uuid.uuid4().hex,
                          expected_binding_version=version,
                          configuration={'endpoint': endpoint or 'https://provider.invalid/' + uuid.uuid4().hex,
                                         'keys': {'p256dh': 'fixture-public', 'auth': 'fixture-auth'},
                                         'key_id': 'v1'})


def preference(enabled=True):
    return PreferenceRequest(category='task', channel='web_push', provider='web_push', enabled=enabled)


def ready_claim(repo, database, actor):
    target = repo.register_target(subject(actor), registration())
    repo.set_preference(subject(actor), preference())
    terminal(database, actor)
    repo.expand()
    claim = repo.claim('independent-owner', 1)[0]
    return target, claim


def test_registration_refresh_preserves_enabled_boundary_and_cas_rejects_stale_tab(boundary_repo, actors):
    owner = subject(actors['a'])
    request = registration()
    first = boundary_repo.register_target(owner, request)
    refreshed = boundary_repo.register_target(owner, request.model_copy(update={'expected_binding_version': 1}))
    assert refreshed == first
    changed = request.model_copy(deep=True, update={'expected_binding_version': 1})
    changed.configuration.keys.auth = 'rotated-auth'
    second = boundary_repo.register_target(owner, changed)
    assert second['binding_version'] == 2 and second['enabled_since'] == first['enabled_since']
    with pytest.raises(NotificationError, match='NOTIFICATION_BINDING_CHANGED'):
        boundary_repo.register_target(owner, request.model_copy(update={'expected_binding_version': 1}))
    with pytest.raises(NotificationError, match='NOTIFICATION_BINDING_CHANGED'):
        boundary_repo.revoke_target(owner, first['target_id'], 1)


def test_target_revocation_reenable_resets_boundary_and_cannot_backfill(boundary_repo, service_database, actors):
    actor = actors['a']; owner = subject(actor); request = registration()
    first = boundary_repo.register_target(owner, request)
    boundary_repo.set_preference(owner, preference())
    terminal(service_database, actor)
    revoked = boundary_repo.revoke_target(owner, first['target_id'], 1)
    assert boundary_repo.revoke_target(owner, first['target_id'], 1) == revoked
    fresh = boundary_repo.register_target(owner, request.model_copy(update={'expected_binding_version': 2}))
    assert fresh['binding_version'] == 3 and fresh['enabled_since'] > first['enabled_since']
    boundary_repo.expand()
    assert boundary_repo.claim('independent-owner', 10) == []


def test_preference_reenable_resets_boundary_but_idempotent_enable_does_not(boundary_repo, service_database, actors):
    actor = actors['a']; owner = subject(actor)
    boundary_repo.register_target(owner, registration())
    first = boundary_repo.set_preference(owner, preference())
    unchanged = boundary_repo.set_preference(owner, preference())
    assert unchanged['enabled_since'] == first['enabled_since']
    terminal(service_database, actor)
    boundary_repo.set_preference(owner, preference(False))
    second = boundary_repo.set_preference(owner, preference())
    assert second['enabled_since'] > first['enabled_since']
    boundary_repo.expand()
    assert boundary_repo.claim('independent-owner', 10) == []


def test_account_switch_needs_revocation_and_new_endpoint(boundary_repo, actors):
    first_request = registration()
    first = boundary_repo.register_target(subject(actors['a']), first_request)
    with pytest.raises(NotificationError, match='NOTIFICATION_INSTALLATION_OWNED'):
        boundary_repo.register_target(subject(actors['a_other']), registration(installation=first_request.installation_ref))
    with pytest.raises(NotificationError, match='NOTIFICATION_TARGET_OWNED'):
        boundary_repo.register_target(subject(actors['a_other']), registration(endpoint=first_request.configuration.endpoint))
    boundary_repo.revoke_target(subject(actors['a']), first['target_id'], 1)
    switched = boundary_repo.register_target(subject(actors['a_other']), registration(installation=first_request.installation_ref))
    assert switched['target_id'] != first['target_id']
    with pytest.raises(NotificationError, match='NOTIFICATION_INSTALLATION_OWNED'):
        boundary_repo.register_target(subject(actors['a']), first_request.model_copy(update={'expected_binding_version': 2}))


def test_concurrent_duplicate_registration_creates_one_target(boundary_repo, actors, service_database):
    owner = subject(actors['a']); request = registration(); barrier = threading.Barrier(2)
    def enroll(_):
        barrier.wait(timeout=5)
        try:
            return boundary_repo.register_target(owner, request)
        except NotificationError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(enroll, range(2)))
    assert sum(isinstance(value, dict) for value in results) == 1
    assert 'NOTIFICATION_BINDING_CHANGED' in results
    assert len(service_database.rows('SELECT 1 FROM notification_targets WHERE recipient_user_id=%s', (owner.user_id,))) == 1


def test_concurrent_test_requests_share_subject_rate_limit_across_targets(boundary_repo, actors, service_database):
    owner = subject(actors['a'])
    targets = [boundary_repo.register_target(owner, registration()) for _ in range(2)]
    barrier = threading.Barrier(2)
    def request(target):
        barrier.wait(timeout=5)
        try:
            return boundary_repo.test(owner, NotificationTestRequest(target_id=target['target_id'], binding_version=1))
        except NotificationError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(request, targets))
    assert sum(len(result) == 32 for result in results) == 1
    assert 'NOTIFICATION_TEST_RATE_LIMITED' in results
    boundary_repo.expand()
    claims = boundary_repo.claim('independent-owner', 10)
    assert len(claims) == 1  # Test explicitly chooses one target, regardless of fanout or preference.
    assert boundary_repo.prepare_send(claims[0]) is not None


def test_test_limit_expires_after_one_minute(boundary_repo, actors, service_database):
    owner = subject(actors['a']); target = boundary_repo.register_target(owner, registration())
    request = NotificationTestRequest(target_id=target['target_id'], binding_version=1)
    first = boundary_repo.test(owner, request)
    service_database.rows("UPDATE notification_messages SET created_at=clock_timestamp()-interval '61 seconds' WHERE notification_ref=%s", (first,))
    assert boundary_repo.test(owner, request) != first


@pytest.mark.parametrize('kind', ['provider_disabled', 'max_attempts', 'expired', 'target_invalid'])
def test_delivery_termination_boundaries(boundary_repo, service_database, actors, kind):
    target, claim = ready_claim(boundary_repo, service_database, actors['a'])
    if kind == 'provider_disabled':
        boundary_repo.registry.get('web_push', 'web_push').enabled = False
        assert boundary_repo.prepare_send(claim) is None
        expected = 'skipped'
    elif kind == 'max_attempts':
        service_database.rows('UPDATE notification_deliveries SET attempts=%s WHERE delivery_id=%s', (boundary_repo.config.max_attempts, claim['delivery_id']))
        assert boundary_repo.finish(claim, SendResult('retryable', 'temporary'))
        expected = 'failed'
    elif kind == 'expired':
        service_database.rows("UPDATE notification_messages SET expires_at=clock_timestamp()-interval '1 second' WHERE notification_id=%s", (claim['notification_id'],))
        assert boundary_repo.prepare_send(claim) is None
        expected = 'expired'
    else:
        assert boundary_repo.finish(claim, SendResult('target_invalid', 'subscription_gone'))
        row = service_database.rows('SELECT state,binding_version FROM notification_targets WHERE target_id=%s', (target['target_id'],))[0]
        assert row['state'] == 'invalid' and row['binding_version'] == 2
        expected = 'failed'
    row = service_database.rows('SELECT state,owner,lease_until FROM notification_deliveries WHERE delivery_id=%s', (claim['delivery_id'],))[0]
    assert row == {'state': expected, 'owner': None, 'lease_until': None}


def test_retention_removes_only_notification_ledger(boundary_repo, service_database, actors):
    actor = actors['a']; target, claim = ready_claim(boundary_repo, service_database, actor)
    boundary_repo.finish(claim, SendResult('accepted'))
    service_database.rows("UPDATE notification_messages SET expires_at=clock_timestamp()-interval '90 days' WHERE notification_id=%s", (claim['notification_id'],))
    assert boundary_repo.cleanup() == 1
    assert service_database.rows('SELECT 1 FROM notification_deliveries WHERE delivery_id=%s', (claim['delivery_id'],)) == []
    assert service_database.rows('SELECT 1 FROM notification_targets WHERE target_id=%s', (target['target_id'],))
    assert service_database.rows("SELECT 1 FROM agent_runners WHERE user_id=%s AND status='completed'", (actor.user_id,))
    assert service_database.rows('SELECT 1 FROM chat_sessions WHERE session_id=%s', (actor.session_id,))


def test_resource_read_ignores_credit_but_respects_fresh_tenant_state(boundary_repo, service_database, actors):
    actor = actors['a']; terminal(service_database, actor)
    ref = service_database.rows('SELECT notification_ref FROM notification_messages WHERE recipient_user_id=%s', (actor.user_id,))[0]['notification_ref']
    service_database.rows('UPDATE tenants SET credit_balance=0 WHERE tenant_id=%s', (actor.tenant_id,))
    assert boundary_repo.resolve(subject(actor), ref)['resource']['session_id'] == actor.session_id
    service_database.rows("UPDATE tenants SET status='suspended' WHERE tenant_id=%s", (actor.tenant_id,))
    with pytest.raises(NotificationError, match='NOTIFICATION_TENANT_UNAVAILABLE'):
        boundary_repo.resolve(subject(actor), ref)


def test_api_uses_real_auth_and_rechecks_token_user_scope(boundary_repo, service_database, actors):
    from src.notifications.api import create_router
    app = FastAPI(); app.include_router(create_router(boundary_repo.config, boundary_repo))
    client = TestClient(app); actor = actors['a']
    auth = {'Authorization': 'Bearer ' + actor.token}
    assert client.get('/api/notifications/targets').status_code == 401
    assert client.get('/api/notifications/targets', headers=auth).status_code == 200
    assert client.get('/api/notifications/targets', headers={**auth, 'X-Tenant-Id': actors['b'].tenant_id}).status_code == 403
    malformed = client.post('/api/notifications/test', headers=auth, json={'target_id': 'dummy', 'binding_version': 1, 'endpoint': 'never echo'})
    assert malformed.status_code == 422 and 'never echo' not in malformed.text
    service_database.rows("UPDATE tokens SET expires_at=clock_timestamp()-interval '1 second' WHERE user_id=%s", (actor.user_id,))
    assert client.get('/api/notifications/targets', headers=auth).status_code == 401
    null_user = actors['null_user']
    assert client.get('/api/notifications/targets', headers={'Authorization': 'Bearer ' + null_user.token}).status_code == 403


def test_server_logout_only_revokes_current_browser_and_stale_version_is_safe(boundary_repo, actors, service_database):
    from src.notifications.logout import revoke_browser_logout_in_tx
    actor = actors['a']; owner = subject(actor)
    targets = [boundary_repo.register_target(owner, registration()) for _ in range(2)]
    def logout(target, version, user_id=actor.user_id):
        with service_database.connect() as connection, connection.cursor() as cursor:
            revoke_browser_logout_in_tx(cursor, user_id, {'X-Notification-Target-Id': target['target_id'],
                                         'X-Notification-Binding-Version': str(version)}, boundary_repo.config)
    logout(targets[0], 1, actors['a_other'].user_id)
    assert all(row['state'] == 'active' for row in boundary_repo.targets(owner))
    logout(targets[0], 9)
    assert all(row['state'] == 'active' for row in boundary_repo.targets(owner))
    logout(targets[0], 1)
    rows = {row['target_id']: row for row in boundary_repo.targets(owner)}
    assert rows[targets[0]['target_id']]['state'] == 'revoked'
    assert rows[targets[1]['target_id']]['state'] == 'active'


def test_concurrent_event_writer_is_unique_and_rollback_is_atomic(boundary_repo, actors, service_database):
    from src.notifications.events import publish_in_tx
    owner = subject(actors['a']); now = datetime.now(timezone.utc)
    event_key = 'independent:' + uuid.uuid4().hex
    def write(cursor, key):
        return publish_in_tx(cursor, subject=owner, producer='independent_fixture', event_type='task.completed',
                             event_key=key, resource_ref={'type': 'runner_result', 'id': 'fixture'},
                             parameters={'session_title': 'Independent test', 'event_kind': 'completed'},
                             occurred_at=now, accepted_at=now, expires_at=now + timedelta(minutes=10))
    barrier = threading.Barrier(2)
    def transaction(_):
        barrier.wait(timeout=5)
        with service_database.connect() as connection, connection.cursor() as cursor:
            return write(cursor, event_key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        refs = list(pool.map(transaction, range(2)))
    assert refs[0] == refs[1]
    assert len(service_database.rows('SELECT 1 FROM notification_messages WHERE recipient_user_id=%s', (owner.user_id,))) == 1
    with pytest.raises(RuntimeError, match='force rollback'):
        with service_database.connect() as connection, connection.cursor() as cursor:
            write(cursor, event_key + ':rolled-back')
            raise RuntimeError('force rollback')
    assert len(service_database.rows('SELECT 1 FROM notification_messages WHERE recipient_user_id=%s', (owner.user_id,))) == 1


@pytest.mark.parametrize('fault', ['version', 'type', 'constraint'])
def test_runtime_schema_gate_rejects_incompatible_tables(boundary_repo, service_database, fault):
    with service_database.connect() as connection, connection.cursor() as cursor:
        if fault == 'version':
            cursor.execute("COMMENT ON TABLE notification_targets IS 'AID_NOTIFICATION_SCHEMA_V999'")
            expected = 'NOTIFICATION_SCHEMA_VERSION_REQUIRED'
        elif fault == 'type':
            cursor.execute('ALTER TABLE notification_targets ALTER COLUMN binding_version TYPE integer')
            expected = 'NOTIFICATION_SCHEMA_COLUMNS_REQUIRED'
        else:
            cursor.execute('ALTER TABLE notification_deliveries DROP CONSTRAINT notification_delivery_target')
            expected = 'NOTIFICATION_SCHEMA_CONSTRAINTS_REQUIRED'
        with pytest.raises(RuntimeError, match=expected):
            boundary_repo.assert_schema_in_tx(cursor)
        connection.rollback()
    boundary_repo.assert_schema()


def test_actual_token_deletion_and_browser_revocation_commit_together(boundary_repo, actors, service_database):
    from src.api.auth import delete_token
    actor = actors['a']; owner = subject(actor)
    targets = [boundary_repo.register_target(owner, registration()) for _ in range(2)]
    assert delete_token(actor.token, notification_user_id=actor.user_id,
                        notification_headers={'X-Notification-Target-Id': targets[0]['target_id'],
                                              'X-Notification-Binding-Version': '1'})
    assert service_database.rows('SELECT 1 FROM tokens WHERE user_id=%s', (actor.user_id,)) == []
    rows = {row['target_id']: row for row in boundary_repo.targets(owner)}
    assert rows[targets[0]['target_id']]['state'] == 'revoked'
    assert rows[targets[1]['target_id']]['state'] == 'active'


def test_logout_database_failure_does_not_delete_token(boundary_repo, actors, service_database):
    from src.api.auth import delete_token
    import psycopg2
    actor = actors['a']; owner = subject(actor)
    target = boundary_repo.register_target(owner, registration())
    service_database.rows("ALTER TABLE notification_targets ADD CONSTRAINT independent_reject_logout CHECK(state<>'revoked') NOT VALID")
    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            delete_token(actor.token, notification_user_id=actor.user_id,
                         notification_headers={'X-Notification-Target-Id': target['target_id'],
                                               'X-Notification-Binding-Version': '1'})
        assert service_database.rows('SELECT 1 FROM tokens WHERE user_id=%s', (actor.user_id,))
        assert boundary_repo.targets(owner)[0]['state'] == 'active'
    finally:
        service_database.rows('ALTER TABLE notification_targets DROP CONSTRAINT independent_reject_logout')


class TenantWebhookAdapter:
    channel = 'im_webhook'
    provider = 'feishu_bot'
    capabilities = AdapterCapabilities()
    enabled = True

    def available(self):
        return True

    def validate_target(self, configuration):
        return hashlib.sha256(configuration['webhook_url'].encode()).hexdigest()

    def render(self, message, target):
        return {'ref': message['notification_ref']}


def _insert_rule(service_database, rule_id, tenant_id):
    """规则级目标登记依赖 notification_rules 行（register_rule_target 校验归属）。"""
    service_database.rows(
        '''INSERT INTO notification_rules (rule_id,tenant_id,event_type,name,recipient_mode)
           VALUES (%s,%s,'billing.credit_low','测试规则','tenant_admins')''',
        (rule_id, tenant_id))


def test_tenant_targets_share_webhook_across_tenants(boundary_repo, actors, service_database):
    """不同租户可用同一群机器人 Webhook（各自规则级目标，installation_ref 独立）。"""
    from src.notifications.contracts import TenantRegisterTarget
    boundary_repo.registry.register(TenantWebhookAdapter())
    admin_a, admin_b = actors['a'], actors['b']
    service_database.rows("UPDATE users SET role='tenant_admin' WHERE user_id=ANY(%s)",
                          ([admin_a.user_id, admin_b.user_id],))
    _insert_rule(service_database, 'rule_test_a', admin_a.tenant_id)
    _insert_rule(service_database, 'rule_test_b', admin_b.tenant_id)
    body_a = TenantRegisterTarget(channel='im_webhook', provider='feishu_bot', rule_id='rule_test_a',
                                  configuration={'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/shared-token'})
    body_b = TenantRegisterTarget(channel='im_webhook', provider='feishu_bot', rule_id='rule_test_b',
                                  configuration={'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/shared-token'})
    try:
        first = boundary_repo.register_rule_target(Subject(admin_a.user_id, admin_a.tenant_id), body_a)
        second = boundary_repo.register_rule_target(Subject(admin_b.user_id, admin_b.tenant_id), body_b)
        assert first['target_id'] != second['target_id']
        rows = service_database.rows(
            'SELECT target_id,tenant_id FROM notification_targets WHERE target_id=ANY(%s)',
            ([first['target_id'], second['target_id']],))
        tenants = {row['target_id']: row['tenant_id'] for row in rows}
        assert tenants[first['target_id']] == admin_a.tenant_id
        assert tenants[second['target_id']] == admin_b.tenant_id
    finally:
        service_database.rows('DELETE FROM notification_targets WHERE scope_key=ANY(%s)',
                              (['tenant:' + admin_a.tenant_id, 'tenant:' + admin_b.tenant_id],))
        service_database.rows('DELETE FROM notification_rules WHERE rule_id=ANY(%s)',
                              (['rule_test_a', 'rule_test_b'],))


def test_tenant_target_same_tenant_rebinds_active_row(boundary_repo, actors, service_database):
    """同租户同规则重复注册同一 Webhook 走改绑更新，不产生第二行；
    携带过期 expected_binding_version 的并发登记被 CAS 拒绝。"""
    from src.notifications.contracts import TenantRegisterTarget
    boundary_repo.registry.register(TenantWebhookAdapter())
    admin = actors['a']
    service_database.rows("UPDATE users SET role='tenant_admin' WHERE user_id=%s", (admin.user_id,))
    _insert_rule(service_database, 'rule_test_a', admin.tenant_id)
    body = TenantRegisterTarget(channel='im_webhook', provider='feishu_bot', rule_id='rule_test_a',
                                configuration={'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/tenant-a-token'})
    try:
        first = boundary_repo.register_rule_target(Subject(admin.user_id, admin.tenant_id), body)
        second = boundary_repo.register_rule_target(Subject(admin.user_id, admin.tenant_id), body)
        assert second['target_id'] == first['target_id']
        assert second['binding_version'] == first['binding_version']
        stale = body.model_copy(update={
            'expected_binding_version': first['binding_version'] + 1})
        with pytest.raises(NotificationError, match='NOTIFICATION_BINDING_CHANGED'):
            boundary_repo.register_rule_target(Subject(admin.user_id, admin.tenant_id), stale)
    finally:
        service_database.rows('DELETE FROM notification_targets WHERE scope_key=%s',
                              ('tenant:' + admin.tenant_id,))
        service_database.rows('DELETE FROM notification_rules WHERE rule_id=%s', ('rule_test_a',))


def test_tenant_target_legacy_default_row_takeover_without_binding_version(boundary_repo, actors, service_database):
    """前端登记恒不传 expected_binding_version：active 的旧默认渠道行（rule_id IS NULL）
    必须能被规则登记接管改绑，不得误报 NOTIFICATION_BINDING_CHANGED。"""
    from src.notifications.contracts import TenantRegisterTarget
    from src.notifications.repository import protected
    boundary_repo.registry.register(TenantWebhookAdapter())
    admin = actors['a']
    service_database.rows("UPDATE users SET role='tenant_admin' WHERE user_id=%s", (admin.user_id,))
    _insert_rule(service_database, 'rule_test_a', admin.tenant_id)
    webhook = 'https://open.feishu.cn/open-apis/bot/v2/hook/legacy-default-hook'
    digest = hashlib.sha256(webhook.encode()).hexdigest()
    service_database.rows(
        '''INSERT INTO notification_targets
           (target_id,scope_key,tenant_id,channel,provider,installation_ref,identity_digest,
            configuration_encrypted,rule_id,state)
           VALUES (%s,%s,%s,'im_webhook','feishu_bot','legacy_install',%s,%s,NULL,'active')''',
        ('target_legacy', 'tenant:' + admin.tenant_id, admin.tenant_id, digest,
         protected({'webhook_url': webhook})))
    body = TenantRegisterTarget(channel='im_webhook', provider='feishu_bot', rule_id='rule_test_a',
                                configuration={'webhook_url': webhook})
    try:
        result = boundary_repo.register_rule_target(Subject(admin.user_id, admin.tenant_id), body)
        assert result['target_id'] == 'target_legacy'
        row = service_database.rows(
            'SELECT rule_id,state FROM notification_targets WHERE target_id=%s', ('target_legacy',))[0]
        assert row['rule_id'] == 'rule_test_a' and row['state'] == 'active'
    finally:
        service_database.rows('DELETE FROM notification_targets WHERE scope_key=%s',
                              ('tenant:' + admin.tenant_id,))
        service_database.rows('DELETE FROM notification_rules WHERE rule_id=%s', ('rule_test_a',))


def test_tenant_target_same_rule_readd_without_binding_version_idempotent(boundary_repo, actors, service_database):
    """同规则重复添加同一 Webhook（不传 expected_binding_version）幂等成功，不报绑定冲突。"""
    from src.notifications.contracts import TenantRegisterTarget
    boundary_repo.registry.register(TenantWebhookAdapter())
    admin = actors['a']
    service_database.rows("UPDATE users SET role='tenant_admin' WHERE user_id=%s", (admin.user_id,))
    _insert_rule(service_database, 'rule_test_a', admin.tenant_id)
    body = TenantRegisterTarget(channel='im_webhook', provider='feishu_bot', rule_id='rule_test_a',
                                configuration={'webhook_url': 'https://open.feishu.cn/open-apis/bot/v2/hook/readd-hook'})
    try:
        first = boundary_repo.register_rule_target(Subject(admin.user_id, admin.tenant_id), body)
        second = boundary_repo.register_rule_target(Subject(admin.user_id, admin.tenant_id), body)
        assert second['target_id'] == first['target_id']
        assert second['binding_version'] == first['binding_version']
    finally:
        service_database.rows('DELETE FROM notification_targets WHERE scope_key=%s',
                              ('tenant:' + admin.tenant_id,))
        service_database.rows('DELETE FROM notification_rules WHERE rule_id=%s', ('rule_test_a',))
