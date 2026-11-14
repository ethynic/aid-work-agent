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


# ==================== 企微应用 target 登记（Phase 3 绑定闭环） ====================


class WecomAppStubAdapter:
    """真库 upsert 语义验证用：wecom_app 协议桩（发送不走网络）。"""

    channel = provider = 'wecom_app'
    capabilities = AdapterCapabilities()

    def available(self):
        return True

    def validate_target(self, configuration):
        return hashlib.sha256(configuration['wecom_userid'].encode()).hexdigest()

    def render(self, message, target):
        return {'ref': message['notification_ref']}

    def send(self, configuration, payload, ttl):
        return SendResult('accepted')


def wecom_registration(user_id, version=None, userid='zhangsan'):
    """服务端绑定后登记载体（与 wecom_bind._register_target 同构）。"""
    from src.notifications.contracts import WecomAppTargetRegistration
    return WecomAppTargetRegistration(
        installation_ref='wecom_bind_' + user_id, expected_binding_version=version,
        configuration={'wecom_userid': userid, 'bound_user_name': '张三', 'tenant_id': ''})


def wecom_bind_register(repo, owner, body):
    """四轮回炉（Web Push 回归）：与 wecom_bind._register_target 同构——绑定
    路径身份已由服务端 member() 核验，repository 传 identity_preverified=True
    才允许对接手/复活非 active 行豁免版本钉扎；web_push 用户 API（上方
    register_target 直调）不带此标记，保持版本校验。"""
    return repo.register_target(owner, body, identity_preverified=True)


def test_wecom_bind_target_registration_queryable_and_rebind_idempotent(boundary_repo, actors):
    """绑定登记闭环：登记后 targets 可查；重复绑定幂等（同 target、版本不变）；
    解绑撤销后重绑复活（版本+1）。"""
    boundary_repo.registry.register(WecomAppStubAdapter())
    owner = subject(actors['a'])
    first = wecom_bind_register(boundary_repo, owner, wecom_registration(actors['a'].user_id))
    listed = [item for item in boundary_repo.targets(owner) if item['channel'] == 'wecom_app']
    assert [item['target_id'] for item in listed] == [first['target_id']]
    assert first['binding_version'] == 1 and first['state'] == 'active'
    # 重复绑定（同配置同版本）幂等：命中更新分支，不抬版本
    again = wecom_bind_register(boundary_repo,
        owner, wecom_registration(actors['a'].user_id, version=first['binding_version']))
    assert again['target_id'] == first['target_id'] and again['binding_version'] == 1
    # 解绑撤销 → 重绑复活（登记时回传撤销后的当前版本；复活是状态变更，
    # update 分支 changed=True 抬版本：撤销后 2 → 复活后 3）
    boundary_repo.revoke_target(owner, first['target_id'], first['binding_version'])
    revived = wecom_bind_register(boundary_repo,
        owner, wecom_registration(actors['a'].user_id, version=first['binding_version'] + 1))
    assert revived['target_id'] == first['target_id']
    assert revived['state'] == 'active' and revived['binding_version'] == 3


def test_wecom_same_member_binds_targets_in_different_tenants(boundary_repo, service_database, actors):
    """评审回炉 P2-4：同一企微成员（同 wecom_userid，digest 相同）可在不同租户
    scope 各自绑定 target（digest 查重按 scope_key 收敛，唯一约束本就含 scope）；
    同租户内他人绑定同一企微身份仍 409。复审 4：归属从库直查断言（public_target
    不透出 scope_key，不再读返回字典）。"""
    boundary_repo.registry.register(WecomAppStubAdapter())
    # 两个租户的用户绑定同一企微成员 zhangsan（installation_ref 按各自 user_id）
    first = wecom_bind_register(boundary_repo, subject(actors['a']), wecom_registration(actors['a'].user_id))
    second = wecom_bind_register(boundary_repo, subject(actors['b']), wecom_registration(actors['b'].user_id))
    assert second['target_id'] != first['target_id']
    # 归属从库直查：b 的 target 落在自己租户 scope、属主为 b
    row = service_database.rows('SELECT scope_key,recipient_user_id FROM notification_targets WHERE target_id=%s',
                                (second['target_id'],))[0]
    assert row['scope_key'] == 'tenant:' + actors['b'].tenant_id
    assert row['recipient_user_id'] == actors['b'].user_id
    # 同租户他人绑定同企微身份：endpoint 摘要命中 a 的 active target → 仍 TARGET_OWNED
    with pytest.raises(NotificationError, match='NOTIFICATION_TARGET_OWNED'):
        wecom_bind_register(boundary_repo, subject(actors['a_other']), wecom_registration(actors['a_other'].user_id))
    # 各自视角只看得到本人 target（scope 断言兜底）
    assert [item['target_id'] for item in boundary_repo.targets(subject(actors['a']))] == [first['target_id']]
    assert [item['target_id'] for item in boundary_repo.targets(subject(actors['b']))] == [second['target_id']]


def test_wecom_target_takeover_after_unbind_in_same_tenant(boundary_repo, service_database, actors):
    """复审 1（同租户接手）：A 绑定后解绑（revoked 行保留 digest），同租户 B 绑定
    同一企微身份接手该行（改属主/安装引用并复活）而非 409；A active 时 B 绑定仍
    409；B 接手后重复绑定幂等；B 自己的 stale revoked 行（其他身份）不阻塞接手
    （installation 冲突仅 active 判定）。"""
    boundary_repo.registry.register(WecomAppStubAdapter())
    a, b = subject(actors['a']), subject(actors['a_other'])
    # B 先绑另一身份再解绑：留下 B 的 stale revoked installation 行
    stale = wecom_bind_register(boundary_repo, b, wecom_registration(actors['a_other'].user_id, userid='lisi'))
    boundary_repo.revoke_target(b, stale['target_id'], stale['binding_version'])
    # A 绑定 zhangsan
    first = wecom_bind_register(boundary_repo, a, wecom_registration(actors['a'].user_id))
    # A active 时同租户 B 绑定 → 409 不变（必须先解绑）
    with pytest.raises(NotificationError, match='NOTIFICATION_TARGET_OWNED'):
        wecom_bind_register(boundary_repo, b, wecom_registration(actors['a_other'].user_id))
    # A 解绑：行保留（state=revoked，digest 不变）
    boundary_repo.revoke_target(a, first['target_id'], first['binding_version'])
    # B 绑定同一身份 → 接手 A 的行（同 target_id，非新增），版本 +1
    taken = wecom_bind_register(boundary_repo, b, wecom_registration(actors['a_other'].user_id))
    assert taken['target_id'] == first['target_id']
    assert taken['state'] == 'active' and taken['binding_version'] == first['binding_version'] + 2
    row = service_database.rows('SELECT scope_key,recipient_user_id,installation_ref FROM notification_targets WHERE target_id=%s',
                                (first['target_id'],))[0]
    assert row['scope_key'] == 'tenant:' + actors['a'].tenant_id
    assert row['recipient_user_id'] == actors['a_other'].user_id
    assert row['installation_ref'] == 'wecom_bind_' + actors['a_other'].user_id
    # 可见性随属主切换：B 可查到 active 恰好为接手的甲行；targets() 返回历史行，
    # B 先绑后解绑的 stale revoked 乙行仍在且保持 revoked（复审 3：不得按全量
    # target_id 列表相等断言，否则历史行必致误报）
    b_rows = boundary_repo.targets(b)
    assert [item['target_id'] for item in b_rows if item['state'] == 'active'] == [first['target_id']]
    stale_row = next(item for item in b_rows if item['target_id'] == stale['target_id'])
    assert stale_row['state'] == 'revoked'
    assert boundary_repo.targets(a) == []
    # 接手后重复绑定幂等：同配置回传当前版本，版本不变
    again = wecom_bind_register(boundary_repo,
        b, wecom_registration(actors['a_other'].user_id, version=taken['binding_version']))
    assert again['target_id'] == first['target_id']
    assert again['binding_version'] == taken['binding_version']


def test_wecom_rebind_after_takeover_unbind_closed_loop(boundary_repo, actors):
    """复审 2 换绑闭环：A 绑甲→B 绑乙→双双解绑→B 接手甲→B 解绑→B 重绑甲。
    重绑时 digest 命中 revoked 甲行，wecom_bind 层 existing 回退按 created_at
    最新取到乙行 stale 版本——repository 更新分支对 revoked 行跳过版本/安装
    引用钉扎直接激活，不得 BINDING_CHANGED 409 死循环；重绑后重复绑定幂等。"""
    boundary_repo.registry.register(WecomAppStubAdapter())
    a, b = subject(actors['a']), subject(actors['a_other'])
    first = wecom_bind_register(boundary_repo, a, wecom_registration(actors['a'].user_id))
    yi = wecom_bind_register(boundary_repo, b, wecom_registration(actors['a_other'].user_id, userid='lisi'))
    boundary_repo.revoke_target(a, first['target_id'], first['binding_version'])
    boundary_repo.revoke_target(b, yi['target_id'], yi['binding_version'])
    taken = wecom_bind_register(boundary_repo, b, wecom_registration(actors['a_other'].user_id))
    assert taken['target_id'] == first['target_id'] and taken['state'] == 'active'
    boundary_repo.revoke_target(b, taken['target_id'], taken['binding_version'])
    # 与 wecom_bind._register_target 同构取 expected：existing 优先 active 行，
    # 无 active 回退 created_at 最近行——此处命中乙行 stale 版本（触发前提）
    rows = [item for item in boundary_repo.targets(b) if item['channel'] == 'wecom_app']
    existing = next((item for item in rows if item['state'] == 'active'), rows[0])
    assert existing['target_id'] == yi['target_id']
    rebound = wecom_bind_register(boundary_repo, b, wecom_registration(
        actors['a_other'].user_id, version=existing['binding_version']))
    assert rebound['target_id'] == first['target_id']
    assert rebound['state'] == 'active'
    assert rebound['binding_version'] == taken['binding_version'] + 2
    # 重绑后幂等：active 行回传当前版本，版本不变
    again = wecom_bind_register(boundary_repo,
        b, wecom_registration(actors['a_other'].user_id, version=rebound['binding_version']))
    assert again['target_id'] == rebound['target_id']
    assert again['binding_version'] == rebound['binding_version']


def test_wecom_bind_profile_active_row_not_shadowed_by_stale_revoked(boundary_repo, actors):
    """复审 1：接手复用旧行（created_at 不变），用户的 stale revoked 新行不得
    遮蔽接手的 active 行——状态查询 active 行优先；无 active 时回退 created_at
    最近行（既有语义）。"""
    boundary_repo.registry.register(WecomAppStubAdapter())
    a, b = subject(actors['a']), subject(actors['a_other'])
    first = wecom_bind_register(boundary_repo, a, wecom_registration(actors['a'].user_id))
    yi = wecom_bind_register(boundary_repo, b, wecom_registration(actors['a_other'].user_id, userid='lisi'))
    boundary_repo.revoke_target(a, first['target_id'], first['binding_version'])
    boundary_repo.revoke_target(b, yi['target_id'], yi['binding_version'])
    # B 接手甲：旧行转 active（created_at 早于 B 的 revoked 乙行）
    taken = wecom_bind_register(boundary_repo, b, wecom_registration(actors['a_other'].user_id))
    assert taken['target_id'] == first['target_id']
    profile = boundary_repo.wecom_bind_profile(b)
    assert profile['target_state'] == 'active'
    assert profile['name'] == '张三'
    # B 解绑后无 active：回退 created_at 最近行（乙行 revoked）
    boundary_repo.revoke_target(b, taken['target_id'], taken['binding_version'])
    assert boundary_repo.wecom_bind_profile(b)['target_state'] == 'revoked'
