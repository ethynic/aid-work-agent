"""管理员门控角色口径与租户级 target 展开（多管理员 event_key 去重）验证。"""
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.config.settings import NotificationsConfig
from src.notifications.authorization import NotificationError
from src.notifications.contracts import AdapterCapabilities, Subject
from src.notifications.repository import (
    NotificationRepository,
    protected,
    tenant_installation_ref,
)


def make_repo():
    repo = NotificationRepository.__new__(NotificationRepository)
    repo.config = NotificationsConfig()
    return repo


def make_cursor(user_row):
    cursor = MagicMock()

    def execute_dispatch(sql, params=None):
        if 'FROM users' in sql:
            cursor.fetchone = lambda: user_row
        elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
            cursor.fetchone = lambda: {'ok': 1}

    cursor.execute.side_effect = execute_dispatch
    return cursor


class TestGuardAdminRoles:
    @pytest.mark.parametrize('role', ['tenant_admin', 'platform_admin'])
    def test_admin_roles_accepted(self, role):
        cursor = make_cursor({'user_id': 'u1', 'tenant_id': 't1', 'role': role, 'status': 'active'})
        NotificationRepository.guard_admin(make_repo(), cursor, Subject('u1', 't1'))

    @pytest.mark.parametrize('role', ['tenant_user', 'admin', None])
    def test_non_admin_roles_rejected(self, role):
        cursor = make_cursor({'user_id': 'u1', 'tenant_id': 't1', 'role': role, 'status': 'active'})
        with pytest.raises(NotificationError, match='NOTIFICATION_FORBIDDEN'):
            NotificationRepository.guard_admin(make_repo(), cursor, Subject('u1', 't1'))


class StubAdapter:
    channel = 'im_webhook'
    capabilities = AdapterCapabilities()

    def available(self):
        return True

    def render(self, message, target):
        return {'text': 'body'}


def tenant_target():
    return {'target_id': 'target_1', 'scope_key': 'tenant:t1', 'tenant_id': 't1',
            'recipient_user_id': None, 'state': 'active', 'binding_version': 1,
            'channel': 'im_webhook', 'provider': 'feishu_bot',
            'installation_ref': tenant_installation_ref('t1'),
            'enabled_since': datetime(2026, 1, 1), 'preference_enabled': None,
            'preference_since': None, 'event_types': None}


def billing_message(admin_id, event_key):
    return {'notification_id': 'notification_' + admin_id, 'notification_ref': admin_id,
            'recipient_user_id': admin_id, 'tenant_id': 't1', 'scope_key': 'tenant:t1',
            'schema_version': 1, 'policy_key': 'tenant_admin_event', 'category': 'billing',
            'event_type': 'billing.credit_low', 'event_key': event_key,
            'parameters_encrypted': protected({'tenant_name': '宏陶', 'daily_avg_credits': 5.0,
                                               'estimated_days': 2, 'remaining_credits': 10.0}),
            'resource_ref': {'type': 'tenant_credit', 'id': 't1'},
            'accepted_at': datetime(2026, 10, 10), 'created_at': datetime(2026, 10, 10),
            'expires_at': datetime(2026, 10, 11), 'priority': 0,
            'database_now': datetime(2026, 10, 10, 12, 0)}


def global_abuse_message(admin_id, event_key):
    """平台管理员 global 副本（tenant_id=None）：租户无 tenant_admin 时的唯一消息形态。"""
    return {'notification_id': 'notification_' + admin_id, 'notification_ref': admin_id,
            'recipient_user_id': admin_id, 'tenant_id': None, 'scope_key': 'global',
            'schema_version': 1, 'policy_key': 'tenant_admin_event', 'category': 'abuse',
            'event_type': 'abuse.rule_triggered', 'event_key': event_key,
            'parameters_encrypted': protected({'tenant_id': 't1', 'tenant_name': '宏陶',
                                               'rule_name': 'credit_budget', 'subject_label': '用户A',
                                               'user_id': 'user_a', 'period': 'day',
                                               'window_start': '2026-10-13 00:00',
                                               'window_end': '2026-10-13 01:00',
                                               'window_credits': 10.0, 'threshold_credits': 5.0,
                                               'observe_mode': True, 'show_tenant': True}),
            'resource_ref': {'type': 'abuse_event', 'id': '3', 'about_tenant': 't1'},
            'accepted_at': datetime(2026, 10, 10), 'created_at': datetime(2026, 10, 10),
            'expires_at': datetime(2026, 10, 11), 'priority': 0,
            'database_now': datetime(2026, 10, 10, 12, 0)}


def global_billing_message(admin_id, event_key):
    """billing 平台兜底副本：参数模型无 tenant_id，归属租户在 resource_ref.about_tenant。"""
    return {'notification_id': 'notification_' + admin_id, 'notification_ref': admin_id,
            'recipient_user_id': admin_id, 'tenant_id': None, 'scope_key': 'global',
            'schema_version': 1, 'policy_key': 'tenant_admin_event', 'category': 'billing',
            'event_type': 'billing.credit_low', 'event_key': event_key,
            'parameters_encrypted': protected({'tenant_name': '宏陶', 'daily_avg_credits': 5.0,
                                               'estimated_days': 2, 'remaining_credits': 10.0}),
            'resource_ref': {'type': 'tenant_credit', 'id': 't1', 'about_tenant': 't1'},
            'accepted_at': datetime(2026, 10, 10), 'created_at': datetime(2026, 10, 10),
            'expires_at': datetime(2026, 10, 11), 'priority': 0,
            'database_now': datetime(2026, 10, 10, 12, 0)}


class TestExpandTenantDedupe:
    def _run_expand(self, messages, dedupe_hits, user_row=None):
        """dedupe_hits: 去重查询依次返回的假值列表（None=无已有投递）。"""
        repo = NotificationRepository.__new__(NotificationRepository)
        repo.config = NotificationsConfig(batch_size=10)
        registry = MagicMock()
        registry.get.return_value = StubAdapter()
        repo.registry = registry

        cursor = MagicMock()
        state = {'messages': iter([messages]), 'dedupe': iter(dedupe_hits), 'inserted': 0,
                 'targets_sql': None, 'targets_params': None}

        def execute_dispatch(sql, params=None):
            if "expansion_state='pending'" in sql:
                cursor.fetchall = lambda: next(state['messages'])
            elif 'FROM users' in sql:
                cursor.fetchone = lambda: user_row or {'user_id': 'x', 'tenant_id': 't1',
                                                       'role': 'tenant_admin', 'status': 'active'}
            elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
                cursor.fetchone = lambda: {'ok': 1}
            elif 'FROM notification_targets' in sql:
                state['targets_sql'] = sql
                state['targets_params'] = params
                cursor.fetchall = lambda: [tenant_target()]
            elif 'JOIN notification_messages m' in sql:
                hit = next(state['dedupe'])
                cursor.fetchone = lambda: hit
            elif 'INSERT INTO notification_deliveries' in sql:
                state['inserted'] += 1

        cursor.execute.side_effect = execute_dispatch
        conn = MagicMock()
        conn.cursor.return_value = cursor

        @contextmanager
        def factory():
            yield conn

        repo.connection_factory = factory
        count = repo.expand()
        return count, state

    def test_second_admin_message_skips_duplicate_tenant_delivery(self):
        key = 'credit_low:t1:2026-10'
        messages = [billing_message('admin1', key), billing_message('admin2', key)]
        count, state = self._run_expand(messages, dedupe_hits=[None, {'x': 1}])
        assert count == 2
        # 同一租户级 target 只插入一条投递，第二个管理员的消息命中去重跳过
        assert state['inserted'] == 1

    def test_single_admin_unaffected_by_dedupe(self):
        key = 'credit_low:t1:2026-10'
        count, state = self._run_expand([billing_message('admin1', key)], dedupe_hits=[None])
        assert count == 1
        assert state['inserted'] == 1

    def test_platform_global_copy_routes_tenant_target(self):
        """租户无 tenant_admin 时，平台管理员 global 副本按事件参数 tenant_id 路由租户级 target。"""
        key = 'abuse:t1:user_a:credit_budget:2026101316'
        count, state = self._run_expand(
            [global_abuse_message('p1', key)], dedupe_hits=[None],
            user_row={'user_id': 'p1', 'tenant_id': None, 'role': 'platform_admin', 'status': 'active'})
        assert count == 1
        assert state['inserted'] == 1
        assert 't.tenant_id=%s' in state['targets_sql']
        assert 't1' in state['targets_params']

    def test_billing_platform_copy_routes_by_about_tenant(self):
        """billing 平台兜底副本参数无 tenant_id，按 resource_ref.about_tenant 路由租户级 target。"""
        key = 'credit_low:t1:2026-10'
        count, state = self._run_expand(
            [global_billing_message('p1', key)], dedupe_hits=[None],
            user_row={'user_id': 'p1', 'tenant_id': None, 'role': 'platform_admin', 'status': 'active'})
        assert count == 1
        assert state['inserted'] == 1
        assert 't.tenant_id=%s' in state['targets_sql']

    def test_tenant_scope_message_still_routes_by_scope(self):
        key = 'abuse:t1:user_a:credit_budget:2026101316'
        count, state = self._run_expand([billing_message('admin1', key)], dedupe_hits=[None])
        assert state['inserted'] == 1
        assert 't.scope_key=%s' in state['targets_sql']

    def test_global_scope_tenant_level_target_excluded(self):
        """无租户归属的 global 租户级 target（历史脏数据）不参与租户级匹配，不产生死投递。"""
        key = 'credit_low:t1:2026-10'
        count, state = self._run_expand(
            [global_billing_message('p1', key)], dedupe_hits=[None],
            user_row={'user_id': 'p1', 'tenant_id': None, 'role': 'platform_admin',
                      'status': 'active'})
        assert count == 1
        assert 't.tenant_id IS NOT NULL' in state['targets_sql']


class TestRegisterTenantTarget:
    def test_platform_admin_without_tenant_rejected(self):
        from src.notifications.contracts import NotificationError
        repo = NotificationRepository.__new__(NotificationRepository)
        repo.config = NotificationsConfig()
        registry = MagicMock()
        registry.get.return_value = StubAdapter()
        repo.registry = registry

        class Body:
            channel = 'im_webhook'
            provider = 'feishu_bot'

            class configuration:
                @staticmethod
                def model_dump():
                    return {}

        with pytest.raises(NotificationError, match='NOTIFICATION_TENANT_REQUIRED'):
            repo.register_tenant_target(Subject('p1', None), Body())


class TestPrepareSendTenantTarget:
    """prepare_send 租户级 target scope 校验：global 兜底副本按事件归属租户放行（fail-closed）。"""

    def _run_prepare_send(self, message, target, user_row, target_tenant_id='t1'):
        repo = NotificationRepository.__new__(NotificationRepository)
        repo.config = NotificationsConfig()
        registry = MagicMock()
        registry.get.return_value = StubAdapter()
        repo.registry = registry

        delivery = {'delivery_id': 'd1', 'binding_version': 1,
                    'rendered_encrypted': protected({'text': 'body'})}
        target = dict(target)
        target['configuration_encrypted'] = protected({'url': 'https://example/hook'})

        cursor = MagicMock()
        state = {'skipped': 0}

        def execute_dispatch(sql, params=None):
            if 'FROM notification_deliveries WHERE delivery_id' in sql:
                cursor.fetchone = lambda: delivery
            elif 'FROM notification_messages' in sql:
                cursor.fetchone = lambda: message
            elif 'FROM notification_targets WHERE target_id' in sql:
                cursor.fetchone = lambda: target
            elif 'FROM users' in sql:
                cursor.fetchone = lambda: user_row
            elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
                cursor.fetchone = lambda: {'ok': 1}
            elif 'UPDATE notification_deliveries' in sql:
                state['skipped'] += 1

        cursor.execute.side_effect = execute_dispatch
        conn = MagicMock()
        conn.cursor.return_value = cursor

        @contextmanager
        def factory():
            yield conn

        repo.connection_factory = factory
        claim = {'delivery_id': 'd1', 'owner': 'w1', 'claim_version': 1,
                 'notification_id': message['notification_id'], 'target_id': 'target_1'}
        return repo.prepare_send(claim), state

    platform_admin_row = {'user_id': 'p1', 'tenant_id': None,
                          'role': 'platform_admin', 'status': 'active'}

    def test_global_copy_tenant_target_matching_about_tenant_passes(self):
        key = 'credit_low:t1:2026-10'
        result, state = self._run_prepare_send(
            global_billing_message('p1', key), tenant_target(), self.platform_admin_row)
        assert result is not None
        assert state['skipped'] == 0

    def test_global_copy_tenant_target_wrong_tenant_rejected(self):
        message = global_billing_message('p1', 'credit_low:t2:2026-10')
        message['resource_ref'] = {'type': 'tenant_credit', 'id': 't2', 'about_tenant': 't2'}
        result, state = self._run_prepare_send(
            message, tenant_target(), self.platform_admin_row)
        assert result is None
        assert state['skipped'] == 1

    def test_global_copy_without_tenant_attribution_rejected(self):
        """无 about_tenant 且参数无 tenant_id 的 global 消息：无法判定归属，拒绝（fail-closed）。"""
        message = global_billing_message('p1', 'credit_low:t1:2026-10')
        message['resource_ref'] = {'type': 'tenant_credit', 'id': 't1'}
        result, state = self._run_prepare_send(
            message, tenant_target(), self.platform_admin_row)
        assert result is None
        assert state['skipped'] == 1

    def test_tenant_scope_message_tenant_target_still_owned_check(self):
        key = 'credit_low:t1:2026-10'
        admin_row = {'user_id': 'a1', 'tenant_id': 't1', 'role': 'tenant_admin',
                     'status': 'active'}
        result, state = self._run_prepare_send(
            billing_message('a1', key), tenant_target(), admin_row)
        assert result is not None
        assert state['skipped'] == 0

    def test_tenant_scope_message_wrong_tenant_rejected(self):
        key = 'credit_low:t1:2026-10'
        admin_row = {'user_id': 'a1', 'tenant_id': 't2', 'role': 'tenant_admin',
                     'status': 'active'}
        result, state = self._run_prepare_send(
            billing_message('a1', key), tenant_target(), admin_row)
        assert result is None
        assert state['skipped'] == 1


def inbox_repo(user_row):
    """构造 guard 走真 DB mock、SQL 可捕获的 repository"""
    repo = NotificationRepository.__new__(NotificationRepository)
    repo.config = NotificationsConfig()

    cursor = MagicMock()
    captured = {}
    state = {'unread': [{'unread': 0}], 'items': []}

    def execute_dispatch(sql, params=None):
        if 'FROM users' in sql:
            cursor.fetchone = lambda: user_row
        elif "FROM tenants WHERE tenant_id=%s AND status='active'" in sql:
            cursor.fetchone = lambda: {'ok': 1}
        elif 'count(*) AS unread' in sql:
            captured['count_sql'] = sql
            captured['count_params'] = params
            cursor.fetchone = lambda: next(iter(state['unread']))
        elif 'ORDER BY created_at DESC' in sql:
            captured['list_sql'] = sql
            captured['list_params'] = params
            cursor.fetchall = lambda: state['items']

    cursor.execute.side_effect = execute_dispatch
    conn = MagicMock()
    conn.cursor.return_value = cursor

    @contextmanager
    def factory():
        yield conn

    repo.connection_factory = factory
    return repo, captured


class TestInboxPlatformScope:
    def test_platform_admin_inbox_includes_global_scope(self):
        repo, captured = inbox_repo({'user_id': 'p1', 'tenant_id': None,
                                     'role': 'platform_admin', 'status': 'active'})
        result = repo.inbox(Subject('p1', None))
        assert "scope_key='global'" in captured['count_sql']
        assert "scope_key='global'" in captured['list_sql']
        assert result['unread_count'] == 0

    def test_platform_admin_tenant_context_isolated_by_about_tenant(self):
        """代管租户时只见本租户 scope + about_tenant=本租户的 global 副本，不见其他租户告警。"""
        repo, captured = inbox_repo({'user_id': 'p1', 'tenant_id': None,
                                     'role': 'platform_admin', 'status': 'active'})
        repo.inbox(Subject('p1', 't1'))
        assert "resource_ref->>'about_tenant'=%s" in captured['count_sql']
        assert "resource_ref->>'about_tenant'=%s" in captured['list_sql']
        # 参数顺序：scope 条件参数 + recipient + 事件类型
        assert captured['count_params'][:2] == ('tenant:t1', 't1')

    def test_tenant_user_inbox_strict_scope(self):
        repo, captured = inbox_repo({'user_id': 'u1', 'tenant_id': 't1',
                                     'role': 'tenant_admin', 'status': 'active'})
        repo.inbox(Subject('u1', 't1'))
        assert "OR scope_key='global'" not in captured['count_sql']
        assert "OR scope_key='global'" not in captured['list_sql']
        assert "about_tenant" not in captured['list_sql']
